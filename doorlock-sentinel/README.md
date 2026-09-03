# Doorlock Sentinel

Doorlock Sentinel 是一个 NAS 原生的小米门锁录像识别与人工复核服务。它只读取已经下载完成的事件录像，不接触小米、米家或 Home Assistant 账号凭据，也绝不执行开锁。

`V0.0.6` 已发布并部署，在既有关系分类中加入“我”和“朋友”，继续采用一个镜像、一个容器、两个受监督进程：

```text
doorlock-sentinel
├── recognition（Python / FastAPI）
│   ├── 只读录像发现、稳定性检查、SHA-256 去重和失败重试
│   ├── SCRFD 2.5G + ArcFace R50 + ONNX Runtime CPU
│   ├── 多人物跟踪、同框不可合并、未知人物聚类和自然学习
│   ├── SQLite / SQLAlchemy / Alembic 单写者
│   ├── 密码登录管理页面、审计、备份清单和回执
│   └── 持久化 Outbox
└── wecom-bot（TypeScript / Node.js）
    ├── 企业微信官方智能机器人 WebSocket SDK
    ├── 只向一个已配置 UserID 发送消息
    └── 通过回环内部 API 领取、确认或延迟 Outbox
```

## 主要能力

- 自动发现 MP4/MOV/MKV/AVI/M4V，等待写入稳定后分析。
- 录像内容 SHA-256 去重；租约恢复和 `5/20/60` 分钟分析重试。
- 单次录像串行推理，默认 ONNX Runtime `2` 个 intra-op、`1` 个 inter-op 线程。
- 每个人按轨迹聚合多帧；多人同框独立识别。
- 同帧共现永久 `cannot-link`，人物或未知簇合并时强制拒绝冲突。
- 不要求预录人脸：未知人物经自然事件积累，由用户人工确认；名称可留空并按关系自动编号。
- 高质量、具有差异性的代表样本永久保存且数量不封顶；模糊、过暗、过亮、小脸和重复样本跳过并记录原因。
- 人物命名、关系修改、人物合并、待确认人物直接并入已确认人物、未知簇合并/拆分、误检标记与撤销；关系分类包含“我”“家人”“朋友”等选项。
- 管理页面查看事件、录像、最佳人脸、身份依据、失败任务、下载状态、备份回执、存储和中文审计记录；待确认人物簇可切换大图并播放对应录像。
- 管理页面所有事件与运行时间固定按北京时间显示，不受访问设备的本地时区影响；页面标题
  和品牌区同时显示当前版本号。
- 训练阶段身份/风险通知默认关闭，运行故障通知不可关闭。
- 下载器状态通过只读 JSONL 日志同步；记录只含固定错误码和不可逆摘要。
- 新录像的人脸裁剪图使用 `<录像名>-a001.jpg`，带人脸框场景图使用
  `<录像名>-b001.jpg`；多人轨迹依次使用 `002`、`003`。
- 显式迁移命令可在下载器先完成 MP4 改名后，同步更新历史数据库路径与派生图片名；
  它先 dry-run，拒绝任何备份回执、路径漂移、摘要不符或覆盖目标。
- 向 115 专项任务导出制品清单，并接收幂等、可审计的备份回执；本项目不直接登录网盘。

## 目录与模块

```text
src/doorlock_sentinel/
├── ingest.py             # 只读发现、租约、重试、去重
├── pipeline.py           # 视频到事件的事务编排
├── face_backend.py       # SCRFD / ArcFace ONNX 推理
├── tracking.py           # 多人物轨迹和共现关系
├── recognition.py        # 身份匹配、未知聚类、原型准入
├── people.py             # 人工标记、合并、拆分和撤销
├── artifacts.py          # 制品登记、保留类别、清单导出
├── media_names.py        # 北京时间录像名与 a/b 派生图片名
├── media_migration.py    # 历史数据库与图片的可回滚改名
├── download_status.py    # 下载器状态日志桥接
├── security.py           # Argon2id、会话、限速、CSRF、审计
├── web_*.py              # 管理页面 API
├── internal_api.py       # 企微与备份专项任务的回环接口
└── models.py             # 单一持久化模型
```

模块以数据所有权和故障边界划分；Python 是数据库唯一写者，Node 不访问 SQLite，人脸后端不负责业务状态，下载器和 115 任务也不共享账号凭据。

## 快速验证

Windows 本地开发环境：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
Set-Location services\wecom-bot
npm ci
npm test
npm run build
```

Linux/NAS 构建：

```bash
cp .env.example .env
./scripts/bootstrap.sh
docker compose config --quiet
docker compose build
docker compose up -d
```

`bootstrap.sh` 会生成不回显的随机密码和内部密钥，并下载经过 SHA-256 固定的模型。首次密码只写入被 Git 忽略的本地文件；不要把它复制到聊天、日志或仓库。

完整操作见：

- [架构](docs/architecture.md)
- [部署与回滚](docs/deployment.md)
- [下载器契约](docs/downloader-contract.md)
- [自然学习](docs/natural-learning.md)
- [运维](docs/operations.md)
- [安全与隐私](docs/security.md)
- [模型与 SDK 许可](docs/model-and-sdk-notes.md)

## 明确不包含

- 不下载小米云录像；由 `xiaomi-lock-cloud-video` 负责。
- 不读取或复用 Home Assistant、小米或米家令牌。
- 不进行自动开锁或门锁控制。
- 不直接上传 115；由备份专项任务负责。
- 不在 Git、镜像或发布包中包含录像、人脸、向量、数据库、模型权重、密码、私有域名或真实家庭时间线。

## 许可

项目代码采用 MIT。InsightFace 代码与预训练模型权重不是同一个授权边界；当前锁定权重仅用于家庭非商业研究评估，商业用途必须重新确认授权，详见 [模型与 SDK 说明](docs/model-and-sdk-notes.md)。
