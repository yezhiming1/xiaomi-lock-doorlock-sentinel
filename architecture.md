# 架构入口

Doorlock Sentinel `V0.0.2` 的权威架构说明位于：

- [`doorlock-sentinel/docs/architecture.md`](doorlock-sentinel/docs/architecture.md)
- [`doorlock-sentinel/docs/security.md`](doorlock-sentinel/docs/security.md)
- [`doorlock-sentinel/compose.yaml`](doorlock-sentinel/compose.yaml)

根目录不维护第二份架构副本，避免旧方案与实现漂移。不可变边界仍为：NAS 单容器双进程、Python 单一数据库写者、Node 只走认证回环 API、只读录像输入、SCRFD/ArcFace、同框不可合并、识别禁止开锁。
