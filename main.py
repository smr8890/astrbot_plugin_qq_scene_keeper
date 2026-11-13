from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import AstrBotConfig
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register

SUPPORTED_PLATFORMS = frozenset({"qq_official", "qq_official_webhook"})
KV_VERSION = 1
SCENE_ALIASES = {
    "group": "group",
    "channel": "channel",
    "friend": "friend",
    "guild": "channel",
    "c2c": "friend",
}


def _normalize_scene(scene: Any) -> str | None:
    """Convert an adapter scene value into the canonical scene name.

    Args:
        scene: Raw scene value stored by the QQ official adapter.

    Returns:
        One of ``group``, ``channel`` or ``friend``, or None when invalid.
    """
    if not isinstance(scene, str):
        return None
    return SCENE_ALIASES.get(scene.strip().lower())


@register(
    "astrbot_plugin_qq_scene_keeper",
    "smr8890",
    "在 AstrBot 重启或平台重载后恢复 QQ 官方适配器的会话场景，保证主动消息正常发送。",
    "0.1.0",
)
class QQSceneKeeper(Star):
    """Persist and replay QQ official adapter session scenes."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        interval = config.get("snapshot_interval_secs", 60)
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            self.logger.warning(
                "[qq_scene_keeper] Invalid snapshot_interval_secs %r, using 60.",
                interval,
            )
            interval = 60
        if interval < 1:
            self.logger.warning(
                "[qq_scene_keeper] snapshot_interval_secs must be at least 1, using 1."
            )
            interval = 1
        self._snapshot_interval = interval

        infer_from_cron = config.get("infer_from_cron", True)
        if not isinstance(infer_from_cron, bool):
            self.logger.warning(
                "[qq_scene_keeper] Invalid infer_from_cron %r, using true.",
                infer_from_cron,
            )
            infer_from_cron = True
        self._infer_from_cron = infer_from_cron

        # platform_id -> {session_id: scene}
        self._scene_overrides: dict[str, dict[str, str]] = {}
        raw_overrides = config.get("scene_overrides", [])
        if not isinstance(raw_overrides, list):
            self.logger.warning(
                "[qq_scene_keeper] Invalid scene_overrides %r, expected a list.",
                raw_overrides,
            )
            raw_overrides = []
        for item in raw_overrides:
            parts = item.split(":") if isinstance(item, str) else []
            if len(parts) != 3:
                self.logger.warning(
                    "[qq_scene_keeper] Ignoring invalid scene override %r, "
                    "expected platform_id:session_id:scene.",
                    item,
                )
                continue
            platform_id, session_id, raw_scene = (part.strip() for part in parts)
            scene = _normalize_scene(raw_scene)
            if not platform_id or not session_id or scene is None:
                self.logger.warning(
                    "[qq_scene_keeper] Ignoring invalid scene override %r, "
                    "expected platform_id:session_id:scene.",
                    item,
                )
                continue
            self._scene_overrides.setdefault(platform_id, {})[session_id] = scene

        self._snapshot_cache: dict[str, dict[str, str]] = {}
        self._warned_platforms: set[str] = set()
        self._snapshot_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Replay scenes for loaded adapters and start the snapshot loop."""
        try:
            await self._replay_all()
        except Exception:
            self.logger.error(
                "[qq_scene_keeper] Initial scene replay failed.", exc_info=True
            )
        self._snapshot_task = asyncio.create_task(
            self._snapshot_loop(), name="qq_scene_keeper_snapshot"
        )

    async def terminate(self) -> None:
        """Stop the snapshot loop and take a final snapshot."""
        task = self._snapshot_task
        self._snapshot_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                self.logger.error(
                    "[qq_scene_keeper] Snapshot loop stopped with an error.",
                    exc_info=True,
                )
        try:
            await self._snapshot_once()
        except Exception:
            self.logger.error(
                "[qq_scene_keeper] Final scene snapshot failed.", exc_info=True
            )

    @filter.on_platform_loaded()
    async def _on_platform_loaded(self) -> None:
        """Replay scenes whenever a platform adapter is loaded or reloaded."""
        try:
            await self._replay_all()
        except Exception:
            self.logger.error(
                "[qq_scene_keeper] Scene replay after platform load failed.",
                exc_info=True,
            )

    @filter.on_astrbot_loaded()
    async def _on_astrbot_loaded(self) -> None:
        """Run one fallback replay after AstrBot finishes loading."""
        try:
            await self._replay_all()
        except Exception:
            self.logger.error(
                "[qq_scene_keeper] Fallback scene replay failed.", exc_info=True
            )

    async def _snapshot_loop(self) -> None:
        """Periodically snapshot scenes until the plugin is terminated."""
        while True:
            await asyncio.sleep(self._snapshot_interval)
            try:
                await self._snapshot_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.error(
                    "[qq_scene_keeper] Scene snapshot failed.", exc_info=True
                )

    async def _snapshot_once(self) -> None:
        """Persist changed scene mappings of all loaded QQ official adapters."""
        manager = getattr(self.context, "platform_manager", None)
        insts = list(getattr(manager, "platform_insts", []) or []) if manager else []
        for inst in insts:
            try:
                meta = inst.meta()
            except Exception:
                self.logger.warning(
                    "[qq_scene_keeper] Failed to read platform metadata.",
                    exc_info=True,
                )
                continue
            if getattr(meta, "name", None) not in SUPPORTED_PLATFORMS:
                continue
            platform_id = getattr(meta, "id", None)
            if not platform_id:
                continue

            session_scene = getattr(inst, "_session_scene", None)
            if not isinstance(session_scene, dict):
                continue

            scenes: dict[str, str] = {}
            for session_id, scene in session_scene.items():
                if not isinstance(session_id, str) or not session_id:
                    continue
                normalized = _normalize_scene(scene)
                if normalized is not None:
                    scenes[session_id] = normalized

            # Never overwrite an existing snapshot with an empty mapping.
            if not scenes or self._snapshot_cache.get(platform_id) == scenes:
                continue

            key = f"scenes:{platform_id}"
            try:
                await self.put_kv_data(key, {"version": KV_VERSION, "scenes": scenes})
            except Exception:
                self.logger.error(
                    "[qq_scene_keeper] Failed to save scene snapshot to %s.",
                    key,
                    exc_info=True,
                )
                continue
            self._snapshot_cache[platform_id] = scenes

    async def _replay_all(self) -> None:
        """Merge stored and inferred scenes, then replay them into adapters."""
        manager = getattr(self.context, "platform_manager", None)
        insts = list(getattr(manager, "platform_insts", []) or []) if manager else []

        # platform_id -> {session_id: scene} inferred from cron job targets.
        cron_scenes: dict[str, dict[str, str]] = {}
        if self._infer_from_cron:
            cron_manager = getattr(self.context, "cron_manager", None)
            list_jobs = getattr(cron_manager, "list_jobs", None)
            if callable(list_jobs):
                try:
                    jobs = await list_jobs()
                except Exception:
                    self.logger.warning(
                        "[qq_scene_keeper] Failed to list cron jobs for scene "
                        "inference.",
                        exc_info=True,
                    )
                    jobs = []
                for job in jobs:
                    payload = getattr(job, "payload", None)
                    if not isinstance(payload, dict):
                        continue
                    session_str = str(payload.get("session") or "").strip()
                    parts = session_str.split(":", 2)
                    if len(parts) != 3 or not all(parts):
                        continue
                    platform_id, message_type, session_id = parts
                    if message_type == "GroupMessage":
                        scene = "channel" if session_id.isdigit() else "group"
                    elif message_type == "FriendMessage":
                        scene = "friend"
                    else:
                        continue
                    cron_scenes.setdefault(platform_id, {})[session_id] = scene

        for inst in insts:
            try:
                meta = inst.meta()
            except Exception:
                self.logger.warning(
                    "[qq_scene_keeper] Failed to read platform metadata.",
                    exc_info=True,
                )
                continue
            if getattr(meta, "name", None) not in SUPPORTED_PLATFORMS:
                continue
            platform_id = getattr(meta, "id", None)
            if not platform_id:
                continue

            key = f"scenes:{platform_id}"
            stored: dict[str, str] = {}
            try:
                raw = await self.get_kv_data(key, {})
            except Exception:
                self.logger.warning(
                    "[qq_scene_keeper] Failed to read scene snapshot from %s.",
                    key,
                    exc_info=True,
                )
                raw = {}
            if isinstance(raw, dict):
                raw_scenes = raw.get("scenes")
                if isinstance(raw_scenes, dict):
                    for session_id, scene in raw_scenes.items():
                        if not isinstance(session_id, str) or not session_id:
                            continue
                        normalized = _normalize_scene(scene)
                        if normalized is not None:
                            stored[session_id] = normalized
                elif raw:
                    self.logger.warning(
                        "[qq_scene_keeper] Ignoring malformed scene snapshot at %s.",
                        key,
                    )
            elif raw:
                self.logger.warning(
                    "[qq_scene_keeper] Ignoring malformed scene snapshot at %s.", key
                )

            # Priority: manual overrides > stored snapshot > cron inference.
            merged = dict(cron_scenes.get(platform_id, {}))
            merged.update(stored)
            merged.update(self._scene_overrides.get(platform_id, {}))
            if not merged:
                continue

            remember = getattr(inst, "remember_session_scene", None)
            session_scene = getattr(inst, "_session_scene", None)
            restored = 0
            if callable(remember):
                for session_id, scene in merged.items():
                    try:
                        remember(session_id, scene)
                        restored += 1
                    except Exception:
                        self.logger.warning(
                            "[qq_scene_keeper] Failed to restore scene for session "
                            "%s on platform %s.",
                            session_id,
                            platform_id,
                            exc_info=True,
                        )
            elif isinstance(session_scene, dict):
                session_scene.update(merged)
                restored = len(merged)
            else:
                if platform_id not in self._warned_platforms:
                    self._warned_platforms.add(platform_id)
                    self.logger.warning(
                        "[qq_scene_keeper] Platform %s exposes neither "
                        "remember_session_scene nor _session_scene; skipping replay.",
                        platform_id,
                    )
                continue

            if restored:
                self.logger.info(
                    "[qq_scene_keeper] Restored %s scene(s) for platform %s.",
                    restored,
                    platform_id,
                )
