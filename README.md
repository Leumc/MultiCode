# Remote Dev Platform

受控的多用户 code-server 开发平台。首期面向 3 个可信开发账户与 1 个独立管理员账户，提供 C++17 单文件编译运行、持久工作区、资源配额和审计。

## 安全边界

- 现有 Docker/containerd 与博客服务不属于本项目，禁止接触。
- 每个开发用户运行独立 code-server 实例和独立持久工作区。
- 普通用户不能使用终端、Task、Debug、Git、端口转发或安装扩展。
- 用户代码只能提交到 runner；runner 在生产环境通过 systemd、bubblewrap、cgroup v2 和 seccomp 隔离。
- 控制面永远不直接执行用户提供的命令或编译参数。
- Cloudflare Tunnel 只发布管理员配置的域名，源站服务仅监听 loopback。

## 首期资源契约

- 每用户最多 5 个 in-flight 作业。
- 全局 1 个编译槽、2 个运行槽。
- 每用户全部运行作业内存预约之和与父 cgroup 上限均为 256 MiB。
- 单组墙钟上限 60 秒；一次提交最多 20 组、总墙钟上限 180 秒。
- 源码与单组 stdin 最大 1 MiB；stdout 1 MiB；stderr 512 KiB。
- 编译内存 512 MiB、墙钟 30 秒。
