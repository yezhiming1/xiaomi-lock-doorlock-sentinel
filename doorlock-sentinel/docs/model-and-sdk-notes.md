# 模型、运行时与 SDK 许可

## 人脸模型

V0.0.1 固定使用 InsightFace `buffalo_m` 包中的：

- 人脸检测：SCRFD 2.5G，文件 `det_2.5g.onnx`。
- 人脸特征：ArcFace R50，文件 `w600k_r50.onnx`。
- 推理运行时：ONNX Runtime CPU。

`models.lock.json` 固定官方发布地址、模型包 SHA-256、两个 ONNX 文件 SHA-256、文件大小和模型 ID。应用启动时再次核对哈希；相同 `model_id` 绝不允许指向不同权重。模型权重不进入 Git、镜像或公开发布包。

InsightFace 项目代码采用 MIT，但官方预训练模型的训练数据和权重另有非商业研究限制。当前家庭内部评估属于非商业研究用途；任何商业化、对外服务或再分发必须重新确认授权，不能只凭代码许可证判断。

上游参考：

- [InsightFace 发布页](https://github.com/deepinsight/insightface/releases)
- [Python 包说明](https://github.com/deepinsight/insightface/blob/master/python-package/README.md)
- [模型库说明](https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md)

## 模型升级

轨迹、原型、人物索引和未知簇都带 `model_id`。不同模型版本不直接比较向量。升级时保留旧模型、旧向量和代表人脸，旁路重嵌入并验证后再切换；不得覆盖旧向量后冒充同一模型。

## 企业微信 SDK

Node 服务锁定企业微信官方智能机器人 Node SDK `1.0.7`，只使用 WebSocket 长连接。SDK 细节封装在 `transport.ts`；卡片生成和 Outbox 协议不依赖 SDK 内部对象。

生产日志不记录 SDK 原始消息、Bot Secret、Bot ID、目标 UserID、内部令牌或完整错误对象。运行故障只发送固定错误码和管理页面链接。

上游参考：[WeCom 智能机器人 Node SDK](https://github.com/WecomTeam/aibot-node-sdk)

## 依赖锁

- Python 容器使用 `requirements.lock` 的精确运行依赖，开发依赖不进入镜像。
- Node 使用 `package-lock.json` 和 `npm ci`，构建后只保留生产依赖。
- Docker 基础镜像以内容摘要固定。
- 每次正式构建执行 `pip check`、Node 生产依赖漏洞审计和镜像内版本检查。
