# 架构决策记录

## ADR-001：每用户独立 code-server

所有实例共享一份 root 管理的 `/opt/code-server` 安装和只读扩展。每个开发账户使用不可变
UUID 作为 systemd 实例名，获得独立 code-server 进程、loopback 端口、workspace、user-data、
cache 和 runtime 目录。实例采用 `DynamicUser=yes`，不创建永久 `rdp-*` 账户；systemd 的
目录指令同时提供持久目录所有权和动态 UID 复用隔离。

## ADR-002：控制面与执行器分权

FastAPI 控制面不以 root 运行，也不直接拼接或执行用户命令。生产 runner 作为独立受限服务，只接受结构化作业描述和固定编译器 ID。

## ADR-003：执行资源采用预约与硬限制双层控制

调度器保证同一用户已运行作业声明内存之和不超过有效额度；cgroup 父层再次限制用户运行总内存。单作业子 cgroup 施加自己的内存和墙钟限制。

## ADR-004：单翻译单元

提交仅包含一个 `.cpp` 文件。工作区其他文件、相对头文件、构建脚本和自定义参数不会进入编译沙箱。管理员管理直接 include 白名单；沙箱镜像仅包含批准工具链与标准库。

## ADR-005：Cloudflare Tunnel

最终入口由部署管理员配置。cloudflared 只连接 loopback 网关，不修改现有 Docker nginx、不开放新的公网源站端口。

## ADR-006：可信用户威胁模型

首期防止陌生登录、误操作、普通目录越权和失控程序。systemd、bubblewrap、cgroup v2、seccomp 与禁网提供纵深防御，但不承诺达到公开恶意 OJ 的虚拟机隔离等级。
