# Agent 部署入口（已替换）

旧版面向永久 Linux 开发用户及零散 root 命令的实施说明已经废止。

请只使用 [`docs/deployment.md`](deployment.md) 中经过静态测试覆盖的 release、验证、备份、恢复和回滚流程。不得由 Agent 执行 root 部署、Cloudflare 或 DNS 操作；不得修改 Docker/containerd、nginx、现有服务或 `80/443/8765`。
