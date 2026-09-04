# 用户部署入口（已替换）

旧版要求创建或准备永久 Linux 开发用户的流程已经废止，不得继续使用，也不得执行其中的账户创建命令。

当前唯一部署、验证、备份、恢复和回滚手册：[`docs/deployment.md`](deployment.md)。新架构使用 systemd `DynamicUser=`、不可变实例 UUID 和 workspace 镜像；Cloudflare/DNS 仍须走独立的用户授权流程，不属于仓库部署脚本。
