# NAS 部署与回滚

本文使用占位路径和占位域名。真实录像路径、域名、密码和企业微信凭据只能放在 NAS 私有运行目录，不得写入 Git、发布包、日志或聊天。

## 前置条件

- x86-64 NAS，Docker Engine 与 Docker Compose。
- 已有下载器输出目录；以只读方式挂载。
- 应用数据、模型和 secrets 分别使用持久目录。
- 公网入口复用现有共享网关；应用本身只监听 `127.0.0.1`。
- 输入文件可为 NAS 下载器常见的 `root:root 0640`；镜像内专用 UID 10001 具有只读所需的 `root` 补充组，不需要修改宿主目录 ACL。

## 初始化

```bash
cp .env.example .env
./scripts/bootstrap.sh
```

初始化会：

- 创建 `runtime/`、`models/` 和 `secrets/`。
- 生成内部 API 密钥、安全 pepper 和 Argon2id 管理密码哈希。
- 把首次随机密码只写入未挂载进容器的 `initial_web_password.txt`，终端不回显密码。
- 下载锁定的 InsightFace 模型包并验证包与两个 ONNX 文件的 SHA-256。

首次登录并妥善保存密码后，应从私有运行目录移除明文首次密码文件；密码哈希保留。该操作不影响登录，但必须确认密码已保存后再执行。

## 配置

复制 `.env.example` 后至少修改：

```dotenv
DOORLOCK_PUBLIC_BASE_URL=https://doorlock.example.invalid
DOORLOCK_TRUSTED_HOSTS=doorlock.example.invalid,doorlock-sentinel,localhost,127.0.0.1
DOORLOCK_DATA_DIR=/absolute/private/application-data
DOORLOCK_INBOX_DIR=/absolute/read-only/video-directory
DOORLOCK_MODELS_DIR=/absolute/private/model-directory
DOORLOCK_SECRETS_DIR=/absolute/private/secret-directory
DOORLOCK_DETECTOR_SHA256=<models.lock.json 中的值>
DOORLOCK_RECOGNIZER_SHA256=<models.lock.json 中的值>
```

V0.0.4 的 N100 工作值延续 V0.0.3：

```dotenv
DOORLOCK_ORT_INTRA_THREADS=2
DOORLOCK_ORT_INTER_THREADS=1
WECOM_ENABLED=false
```

Compose 同时把容器限制为最多 2 个 CPU、2 GiB 内存和 128 个进程/线程 ID；目标实测如
触发内存限制，只能在保存资源证据后调整，不能直接移除上限影响其他 NAS 业务。

训练期先保持企微关闭。配置真实账号时，只在 NAS 私有 `.env` 写 Bot ID 和目标 UserID，把 Secret 写入 `secrets/wecom_bot_secret`；不得通过聊天传递。

## 构建与启动

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
```

若且仅若固定外部基础镜像注册表暂时不可达、当前已验证前代镜像仍在本机、且本版本没有
依赖或基础镜像变化，可使用版本专用的离线升级构建。调用方必须同时传入实际前代镜像 ID
和最终源码提交，脚本在构建前后复核二者，并在断网只读容器中比对包元数据与运行版本：

```bash
DOORLOCK_EXPECTED_PREDECESSOR_IMAGE_ID=sha256:<verified-id> \
DOORLOCK_EXPECTED_SOURCE_COMMIT=<final-commit> \
sh ./scripts/build_upgrade_image.sh
```

`Dockerfile.upgrade` 当前只用于 V0.0.4 → V0.0.5 的代码层升级；它不替代规范 Dockerfile，
不得用于依赖、基础镜像、系统包或 Node 运行代码发生变化的版本。正式记录必须注明使用了
哪条构建路径，并保留前代镜像直到新版本消费者验收完成。

启动入口先校验持久数据目录所有者；首次启动按“子目录在前、父目录在后”的顺序交给 UID 10001，后续启动由 UID 10001 自己补齐子目录，避免依赖 `DAC_OVERRIDE`。随后复制只读 secret 到容器临时文件系统、执行 `alembic upgrade head`，最后启动 Python 和 Node。容器根文件系统只读，删除所有 capabilities 后只加回降权、初始化与优雅停止所需的 `CHOWN`、`SETGID`、`SETUID`、`KILL`；`KILL` 仅供容器内 Supervisor 结束 UID 10001 子进程，容器不共享宿主 PID 命名空间。Python/Node 仍以 UID 10001 运行；容器内 `root` 补充组只用于读取 `0640` 的只读输入挂载。

## 本机健康验收

```bash
curl --fail http://127.0.0.1:18125/health/live
curl --fail http://127.0.0.1:18125/health/ready
docker inspect --format '{{json .State.Health}}' doorlock-sentinel
```

`live` 只证明 Web 进程存活；`ready` 同时要求数据库和固定模型可用。企微启用后，容器健康检查还要求 WebSocket 心跳已认证且未过期。

还需验证：

- 容器重启后仍为健康。
- 优雅重启日志不得出现 Supervisor `PermissionError` 或无法结束子进程。
- 输入挂载为只读，容器内无法修改源录像。
- 数据库、派生证据和导出清单位于持久目录。
- 日志不含密码、令牌、UserID、原始 SDK 消息、私有路径或家庭录像文件名。
- 实际 N100 处理真实录像时仍只有一个推理任务，CPU、内存和处理时长有记录。

## 共享公网入口

应用只发布回环端口。新增主机名必须在共享网关中旁路生成候选版本：先保留当前运行版本，再用备用端口启动候选，逐一验证所有既有主机名及未知主机 404，最后才切换隧道路由。任何既有站点异常都立即回滚；不得为本项目启动第二个长期 cloudflared。

公网验收至少覆盖：TLS、错误密码、正确密码、12 小时会话、CSRF、退出、撤销全部会话、安全响应头、移动端布局和既有站点无回归。

## 更新前备份

每个正式版本前必须同时保留：

- 当前运行镜像的不可变摘要与镜像归档。
- 当前版本源码包及 SHA-256 清单。
- 使用 SQLite 在线备份 API 生成并通过 `PRAGMA integrity_check` 的数据库快照。
- 当前私有 `.env`、secrets 和网关路由的恢复位置；备份中不得把 secret 加入源码包。
- 上一个可运行版本，直到新版本经真实消费者验证稳定。

V0.0.4 还必须生成一份只保存在私有运行目录的媒体迁移清单：聚合记录旧/新录像数、
旧/新人脸图数、旧/新场景图数、总字节和内容摘要集合；不得把真实文件名、时间或摘要
提交到 Git。先停止 V0.0.3，完成下载器 MP4 改名，再使用候选镜像执行
`doorlockctl migrate-media-names` dry-run。只有 dry-run、数据库快照和下载器复读全部通过，
才运行 `--apply`。迁移后先旁路启动候选、验证健康和数据库，再切换生产容器；不得让旧
摄取进程在 MP4 改名与数据库迁移之间扫描目录。

## 回滚

1. 停止新容器，保留日志和数据库副本。
2. 将共享网关的该主机名恢复到上一版本端口，并验证既有主机名。
3. 使用上一镜像和上一 Compose 配置启动。
4. 若数据库迁移不兼容，先隔离当前数据库，再从已验证快照恢复；绝不覆盖唯一副本。
5. 验证健康端点、登录、事件读取、源目录只读和数据完整性。

若 V0.0.4 媒体迁移后需要回滚，先停止所有识别进程，用执行前 SQLite 在线快照恢复数据库，
再按私有迁移清单把派生图片恢复旧名；录像旧名由下载器 V0.0.8 的回滚清单负责。三个
对象集合完成数量、总字节和内容摘要复核前不得启动 V0.0.3。115 不参与本地名称回滚。

模型、企业微信和 115 的真实外部验收分别记录，不能用本地健康检查代替。
