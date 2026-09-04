# code-server 4.135.0 协议层锁定与边界

本文只描述 `deploy/code-server` 与 `code-server@.service` 当前实现能证明的性质。settings 与 keybindings 是只读的误操作防护，不是命令授权系统。

## 本地产物调查结论

调查对象：code-server 4.135.0（Code 1.135.0，commit `de89acbcdce9d9b870008a270c9f6466993d91f4`）。

- code-server wrapper 的公开 CLI 没有禁用 Terminal、Tasks、Debug、Notebook、Git、扩展或端口转发的开关。
- wrapper 会拒绝直接传入 `--disable-extensions`；实际测试退出码为 1，输出 `Unknown option --disable-extensions`。
- `--vscode-option disable-extension=<id>` 会传给 VS Code server；编译产物的 environment service 将它解析为 `disableExtensions`。
- 同一 environment service 会读取 `VSCODE_SKIP_BUILTIN_EXTENSIONS`。服务使用该 root 管理环境变量在扫描后、扩展宿主激活前跳过敏感内置扩展，并重复传入 `disable-extension` 做纵深防御。
- Terminal、Tasks、Debug、Notebook 与 Ports 的主要命令和服务也存在于 workbench 核心；禁用内置扩展不能删除这些核心命令。

## 实施的拒绝层

| 能力 | 实施层 | 可证明的结果 |
|---|---|---|
| Git / GitHub auth | `VSCODE_SKIP_BUILTIN_EXTENSIONS` + `disable-extension` + process hook | 内置 Git/Auth 扩展不应激活；即使核心或其他允许扩展尝试启动 Git，非白名单进程创建同步返回 `EACCES`。 |
| Debug / Notebook | 跳过 JS debug、debug helpers、IPYNB/renderers + process hook | 对应内置扩展不应激活；debug adapter、kernel、解释器等非白名单进程不能由 Node 服务/扩展宿主创建。 |
| Terminal / Tasks | 跳过 terminal-suggest + process hook | workbench 核心命令仍可能可见，但 shell、task binary 和用户 Node 脚本创建被同步拒绝。 |
| Extension Development Host | process hook 参数检查 | 带 `extensionDevelopment*` / `extensionTests*` 参数的 Node 子进程，以及 `--require`、`--import`、loader/eval 绕过被拒绝。 |
| Marketplace | systemd `IPAddressDeny=any`，仅允许 localhost；自动更新关闭 | 服务上下文不能连接公网 Gallery。缓存 UI 或元数据可能仍出现，不等于可以下载。 |
| VSIX / 扩展安装 | `--disable-file-uploads`；`--extensions-dir /opt/remote-dev/extensions`；只读 `/opt/remote-dev/extensions`；隐藏 user-data/workspace 候选扩展目录 | code-server 浏览器上传路由关闭；扩展管理服务不能写管理员扩展目录，也不能回退到两个已知可写候选目录。管理员只能在服务外部署已审核扩展。 |
| Tunnel / Port Forward | 跳过 `vscode.tunnel-forwarding`；`--disable-proxy`；Node `net.connect/createConnection` 精确 allowlist | relay 扩展不应激活，code-server proxy 路由关闭；Node 服务与扩展宿主只能 TCP 连接 `127.0.0.1:9000` 或 `[::1]:9000`，其他本地/远端转发目标同步返回 `EACCES`。Unix socket 保留供 VS Code 内部 IPC。 |
| Simple Browser | 跳过 `vscode.simple-browser` + TCP allowlist | 内置 Simple Browser 不应激活；Node 后端不能替它连接任意 URL。 |
| command URI | 上述扩展/进程/网络后端拒绝 | 没有声称全局解析并拒绝所有 `command:` URI。敏感 URI 即使触发核心命令，后端创建进程或 TCP 连接仍受 hook 限制。 |

`REMOTE_DEV_ALLOWED_CONNECTS` 当前固定为提交 API 的 `127.0.0.1:9000,[::1]:9000`。若 `REMOTE_DEV_API_BASE` 改端口或主机，必须同步修改 allowlist 并增加测试；不要用 `localhost` 模糊匹配。

## 验收

无需 root 的静态/单元验收：

```bash
node --test tests/lockdown.test.mjs
uv run pytest tests/test_code_server_systemd.py -q
systemd-analyze verify deploy/systemd/code-server@.service
```

开发实例验收必须使用临时 HOME、user-data、extensions 和 workspace，只监听 `127.0.0.1`；结束后终止进程并删除临时目录。至少验证：

1. bundled Node + `NODE_OPTIONS=--require=.../lockdown-spawn.cjs` 能启动 4.135.0；
2. HTTP `/` 为 302 到 workspace URL，随后 200，并成功启动 Agent Host；
3. 非 allowlist `net.connect` 与 shell/用户 Node/loader 参数均返回 `EACCES`；
4. 进程退出后没有开发监听端口或临时目录残留。

完整浏览器 WebSocket 命令验收仍应在预生产镜像中执行：从 Command Palette、键绑定、`command:` URI 和扩展 API 分别尝试 Terminal、Task、Debug、Notebook kernel、Git、Install VSIX、Extension Dev Host、Ports 与 Simple Browser，并记录 UI 错误及服务日志。HTTP 成功不能替代该验收。

## 未彻底封锁与升级风险

- workbench 核心的 Terminal/Tasks/Debug/Notebook/Ports 命令 ID 仍被打包，部分入口可能仍可见。当前保证是敏感后端操作失败，不是命令从注册表消失。
- `lockdown-spawn.cjs` 是进程内 Node hook，不是内核安全边界。可信代码中的 native addon、`process.binding`、已捕获的原始函数引用或未来 Node/VS Code 实现变化可能绕过它。生产仍必须加载并回归测试 AppArmor/systemd 边界。
- AppArmor 的通用 `network inet stream` 规则不能按目标端口区分 connect；当前 TCP 目标限制来自 Node hook。若威胁模型包含被攻陷的 code-server/允许扩展，应增加 cgroup/eBPF/nftables 等内核层 connect 策略，而不是扩大本文声明。
- `/usr/bin/clangd-19` 是唯一额外进程白名单。核心 Task 命令若仍可达，理论上可尝试以异常参数驱动 clangd；必须把 clangd 当作受信任组件并固定版本。
- 管理员放入 `/opt/remote-dev/extensions` 的任何扩展都在信任边界内，必须单独审核。只读目录阻止用户安装，不会使已安装扩展安全。
- browser worker 或将来的非 Node agent host 不一定经过此 hook。每次 code-server/VS Code 升级都必须重新确认参数名、内置扩展 ID、Agent Host 架构和网络调用路径。
- 本地无 root 验收不会加载正式 AppArmor profile、DynamicUser mount namespace 或正式 reverse proxy；这些只能在预生产部署后验证。
