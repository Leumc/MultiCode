# 运行时目录约定

```text
/opt/code-server/                root 管理的共享 code-server 安装
/opt/remote-dev/                 只读应用
/opt/remote-dev/extensions/      root 管理、所有实例共享的只读扩展
/etc/remote-dev/                 root 管理的配置
/etc/remote-dev/code-server/<UUID>.env
                                 每实例 root-only loopback 端口配置（0600）
/var/lib/remote-dev/control.db   控制数据库
/var/lib/remote-dev/runner/      短期作业目录
/var/lib/private/remote-dev/workspaces/<UUID>/
                                 DynamicUser 持久工作区
/var/lib/private/remote-dev/code-server/<UUID>/
                                 DynamicUser 持久 user-data
/var/cache/private/remote-dev/code-server/<UUID>/
                                 DynamicUser 持久缓存
/run/remote-dev/code-server/<UUID>/
                                 DynamicUser 实例运行时目录
```

控制面、内部网关和各 code-server 只监听 `127.0.0.1`。Cloudflare Tunnel 是唯一公网入口。
code-server 不创建永久 Linux 账户；unit 实例名必须使用控制面分配的不可变 UUID。systemd 的
`DynamicUser=` 与目录指令负责临时 UID、目录所有权及 UID 复用隔离。
