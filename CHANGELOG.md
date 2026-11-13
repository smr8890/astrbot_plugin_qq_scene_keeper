# 更新日志

## v0.1.0

### 新增

- 定期快照 QQ 官方适配器（`qq_official`、`qq_official_webhook`）的会话场景映射，并写入 AstrBot KV 存储。
- 在插件初始化、平台加载/重载和 AstrBot 启动完成后自动回放场景。
- 支持 `scene_overrides` 手动映射，优先级高于快照和定时任务推断。
- 支持从定时任务目标会话推断场景，仅补齐缺失项。
- 支持场景归一化：`guild` 归一化为 `channel`，`c2c` 归一化为 `friend`。
- 按平台实例隔离快照，多机器人实例之间互不覆盖。

### 说明

- 平台不存在或场景为空时，不覆盖已有快照。
- 适配器缺少 `remember_session_scene` 或 `_session_scene` 时跳过回放并记录警告。
- 不持久化 `msg_id`，不修改 AstrBot 核心或适配器实现。
