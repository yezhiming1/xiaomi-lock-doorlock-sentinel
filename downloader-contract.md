# 下载器契约入口

权威契约位于 [`doorlock-sentinel/docs/downloader-contract.md`](doorlock-sentinel/docs/downloader-contract.md)。

当前集成目标是 `xiaomi-lock-cloud-video V0.0.4`：下载器独占小米云认证，识别服务只读录像与无敏感信息的 JSONL 状态日志；下载器自动删除设为 `0`，录像删除由 115 备份专项任务在远端校验通过后负责。
