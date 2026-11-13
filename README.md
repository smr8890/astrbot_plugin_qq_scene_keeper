# astrbot_plugin_qq_scene_keeper

在 AstrBot 重启或平台适配器重载后，恢复 QQ 官方适配器内存中的会话场景（scene），让依赖 `send_by_session` 的主动消息继续按正确的 QQ 接口发送。

## 背景

QQ 官方适配器使用 `_session_scene` 区分同一会话是 QQ 群、频道子频道还是好友单聊。该字典只存在于适配器实例内存中：

- 消息事件到达时写入；
- 进程重启、平台重载、平台禁用再启用后清空；
- 没有 TTL，也没有持久化。

scene 为空时，QQ 群主动推送会在调用 QQ 接口前被跳过，导致主动消息发送失败。本插件通过“快照 + 回放”维护 scene。

## 功能

- 定期快照 QQ 官方适配器的 scene 映射到 AstrBot KV 存储；
- 平台加载、重载或 AstrBot 启动后自动回放；
- 支持手动 scene 覆盖；
- 可选地从定时任务目标会话推断 scene；
- 按平台实例隔离，多机器人实例互不影响；
- 所有私有 API 访问均做特性检测，失败时降级并记录日志。

## 支持平台

- `qq_official`
- `qq_official_webhook`

需要 AstrBot `>=4.24.0,<5`。

## 配置

| 配置 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `snapshot_interval_secs` | int | `60` | 快照间隔，最小 1 秒 |
| `infer_from_cron` | bool | `true` | 启动时从定时任务 session 推断 scene |
| `scene_overrides` | list[string] | `[]` | 手动映射，格式 `平台ID:会话ID:scene` |

示例：

```json
{
  "snapshot_interval_secs": 60,
  "infer_from_cron": true,
  "scene_overrides": [
    "qq_official_default:B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5:group",
    "qq_official_default:123456789012345678:channel"
  ]
}
```

scene 取值：`group` / `channel` / `friend`。适配器内部的 `guild` 会归一化为 `channel`，`c2c` 会归一化为 `friend`。

## 合并优先级

1. `scene_overrides`（最高）
2. KV 快照
3. 定时任务推断（仅补齐缺失项）

## 存储

使用 AstrBot 的 `PluginKVStoreMixin`（底层为 `shared_preferences` SQLite），按平台分键：

- key：`scenes:<platform_id>`
- value：

```json
{
  "version": 1,
  "scenes": {
    "B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5": "group",
    "123456789012345678": "channel",
    "E4F4AEA33253A2797FB897C50B81D7ED": "friend"
  }
}
```

只保存 scene，不保存 `msg_id`、时间戳或消息内容。平台不存在或 scene 为空时不会覆盖已有快照。

## 已知限制

- 快照间隔内发生硬崩溃，可能丢失最近一个间隔内的 scene 更新；
- 纯数字 `GroupMessage` 会话 ID 推断为频道属于启发式规则；
- 频道子频道仍然需要 `msg_id`，本插件不解决；
- 频道私信仍然走 C2C 接口，本插件不解决；
- 适配器私有属性 `_session_scene` 和 `remember_session_scene` 可能随 AstrBot 版本变化。

## 许可证

AGPL-3.0，详见 [LICENSE](LICENSE)。
