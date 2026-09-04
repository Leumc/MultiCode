# code-server 锁定层

此目录内的 settings/keybindings **只负责界面与误操作防护，不是最终安全边界**。

生产边界必须同时具备：

1. 尽量移除普通用户的终端、Task/Debug/Notebook/Git/端口转发 UI 入口；不能据此声称核心命令 ID 已消失；
2. 通过 `VSCODE_SKIP_BUILTIN_EXTENSIONS` 与 server `disable-extension` 参数跳过敏感内置扩展；
3. 即使核心命令、`command:` URI 或扩展 API 仍能触发，Node process/network hook 也必须拒绝非白名单进程与 TCP 目标；
4. settings/keybindings 以只读 bind mount 覆盖，用户不能恢复常见入口；
5. 扩展目录由管理员写入，code-server 用户只读；浏览器上传关闭，已知可写候选扩展目录不可访问；
6. Marketplace 公网、VSIX 上传、CLI 安装和自动更新均由网络、上传与文件边界组合禁用；
7. code-server 服务仅监听 loopback，并由 Caddy `forward_auth` 验证工作区归属；
8. 每个逻辑用户以不可变 UUID 启动独立实例；`DynamicUser=` 提供运行期独立 UID，
   `StateDirectory=`/`CacheDirectory=`/`RuntimeDirectory=` 提供隔离目录并防止 UID 复用越权；
9. systemd 文件边界阻止访问 `/home`、其他工作区和宿主敏感路径；
10. 用户代码即使绕过界面也不能在 code-server OS 上下文执行；部署前必须加载 AppArmor 并做真实绕过测试。

`locked-settings.json` 中的 workspace trust 保持启用，是为了阻止非信任工作区自动触发潜在功能；不能将其关闭后误认为更安全。

所有实例共享 root 管理的 `/opt/code-server` 安装和 `/opt/remote-dev/extensions` 扩展目录；
后者以及 bind mount 到各实例 user-data 的 settings/keybindings 都必须保持只读。
