# 小米门锁云录像下载器契约

## 所有权

`xiaomi-lock-cloud-video` 是小米云访问的唯一所有者。Doorlock Sentinel 只读其输出目录，不读取 Home Assistant 数据库、配置项、Cookie、访问令牌或米家账号凭据。

目标下载器版本为 `V0.0.4`。下载器应关闭自身录像删除策略，由 115 备份专项任务在远端校验成功后统一决定删除。

## 录像文件

下载器先写临时文件，校验完成后在同一文件系统原子重命名。Doorlock Sentinel 只接受普通文件、拒绝符号链接和越界路径，并额外等待稳定时间及执行视频探测。

推荐命名：

```text
xiaomi_lock_20260829T184213123Z_<opaque-digest>.mp4
```

事件时间按以下顺序确定：

1. 合法旁车文件中的带时区时间。
2. 下载器文件名中的 UTC 时间。
3. 文件修改时间兜底。

管理页面同时保存 `downloaded_at` 和 `time_source`，不会把识别程序扫描时间冒充门锁事件时间。文件内容 SHA-256 是去重依据，文件名不是唯一键。

## 可选事件旁车

可使用 `video.mp4.json` 或 `video.json`：

```json
{
  "occurred_at": "2026-01-01T08:00:00+08:00",
  "source": "xiaomi_lock",
  "event_type": "doorbell",
  "unlock_method": "face",
  "operation_user": "opaque-user-number",
  "doorbell": true,
  "dwell_seconds": 12.5
}
```

旁车不是强制项。无可靠来源的门铃、停留、经过、开锁方式或用户编号必须保持未知，不能由文件名猜测。

## 下载状态日志

下载器在录像目录追加：

```text
.xiaomi_lock_backup_status.jsonl
```

每行最大 2 KiB，文件最大 16 MiB；Doorlock Sentinel 以 `O_NOFOLLOW` 方式只读，拒绝链接、非普通文件、超长行和不合规字段。单行协议：

```json
{
  "schema_version": 1,
  "source": "xiaomi_lock_cloud_backup",
  "report_key": "64位小写十六进制不可逆摘要",
  "recorded_at": "2026-01-01T00:00:00+00:00",
  "state": "retrying",
  "attempts": 1,
  "error_code": "SEGMENT_FETCH_FAILED"
}
```

约束：

- `report_key` 只能是下载器生成的不可逆摘要，不得写云事件 ID、录像 ID、设备 ID、账号或文件名。
- `error_code` 只能是固定 ASCII 代码，不得写异常文本、URL、响应体或凭据。
- `recorded_at` 是下载器记录状态的时间；管理页面明确显示为“下载器记录时间”。
- `discovered` / `retrying` / `downloaded` / `failed` 是唯一允许状态。
- 前两次连续基础设施失败记录为 `retrying`，第三次仍失败才记录 `failed` 并触发运行故障通知；后续成功改为 `downloaded`。
- 非法日志只生成固定 `DOWNLOAD_STATUS_INVALID` 故障，不回显原内容。

如果连输出存储本身都不可写，下载器无法留下状态日志；该存储故障仍应由 Home Assistant 固定错误日志及 NAS 存储监控覆盖，不能把状态文件视为唯一监控通道。

## 删除责任

下载器的保留天数设为 `0` 表示禁用自动删除。Doorlock Sentinel 为普通录像和普通派生证据登记 35 天保留期，但不直接越权上传或删除；115 专项任务必须先比对大小和 SHA-256、回写 `verified` 回执，再依据保留规则操作。永久身份数据、模型、数据库快照和高质量代表人脸没有到期时间。
