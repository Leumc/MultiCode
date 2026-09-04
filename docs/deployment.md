# Remote Dev Platform 全新部署、备份与恢复手册

> 本手册是唯一有效的生产部署流程，取代旧的“永久 Linux 开发用户”流程。示例使用保留域名 `code.example.com`，部署时必须替换为实际域名。

## 1. 安全边界

- **禁止创建永久 Linux 开发用户**。每个 code-server 实例由 systemd `DynamicUser=yes` 提供临时身份；Web 用户与 Linux UID 不对应。
- 不操作 Docker、containerd、现有 nginx、博客服务及端口 `80/443/8765`。
- 不由这些脚本登录 Cloudflare、创建 DNS、安装 Tunnel 服务或读取 Cloudflare credential。
- 所有脚本必须由用户审计后以 root 手工执行；仓库开发和测试阶段不得执行它们。
- 密码、Token、Cookie、证书、私钥和 Tunnel credential 不得作为命令参数，不得写入仓库或日志。脚本不启用 shell trace。
- 脚本不会启动、停止、启用或重启服务。服务切换必须作为独立、人工批准的变更窗口操作。
- code-server 的部分 Terminal/Task/Debug/Ports 核心命令仍可能出现在 Command Palette；不能把“命令不可见”当作安全边界。锁定快捷键统一转到 `remoteDev.blocked`，systemd `InaccessiblePaths=` 与 AppArmor 同时禁止加载 `node-pty` 的 `pty.node`，AppArmor 另行拒绝 shell/编译器执行。直接选择 Terminal 命令可能留下失败面板，但不得产生 shell 子进程。
- `EXTENSIONS_GALLERY={}` 在产品层禁用公开扩展市场；管理员预装的只读 VSIX 不受影响。

## 2. 固定生产布局

```text
/opt/remote-dev/releases/<VERSION>/              不可变应用 release
/opt/remote-dev/current -> releases/<VERSION>    当前版本原子 symlink
/opt/remote-dev/previous -> releases/<VERSION>   上次版本
/opt/code-server/                                root 管理的共享 code-server
/etc/remote-dev/code-server/<UUID>.env           仅非秘密实例配置，root:root 0600
/var/lib/remote-dev/control.db                   控制面 SQLite
/var/lib/remote-dev/workspaces/<UUID>.img        工作区文件系统镜像
/var/lib/private/remote-dev/code-server/<UUID>   DynamicUser state 的宿主后端
/run/remote-dev/control-worker.sock              control/worker broker
/var/backups/remote-dev/<BACKUP_UUID>/           原子、root-only 备份
```

UUID 必须为带连字符的 RFC 4122 UUID（版本 1–5、variant 1）。任何其他名称都会被拒绝。state 也可能由 systemd 通过 `StateDirectory=` 映射为 `/var/lib/remote-dev/code-server/<UUID>`；备份针对其真实 private 后端。

## 3. 部署前检查（只读、非 root 可做部分）

在仓库目录运行：

```bash
uv run pytest tests/test_deployment_scripts.py -q
uv run pytest -q
node --test tests/lockdown.test.mjs
command -v shellcheck >/dev/null && shellcheck deploy/scripts/*.sh
```

人工确认：

1. `/opt/code-server` 已通过单独、可信且校验 hash 的流程安装；本部署脚本不会下载它。
2. release 版本名仅含字母、数字、点、下划线和连字符，且不含 `..`。
3. 所有实例 UUID 已从控制面取得，不能使用用户名替代。
4. `/etc/remote-dev/code-server/<UUID>.env` 仅含端口等非秘密配置；凭据必须存于仓库和此备份流程之外的 root-only secret store。
5. 记录现有 `80/443/8765` 的监听者，部署后应完全不变。

## 4. 安装固定 code-server 与构建 release

先将已经下载的官方 tarball 交给本地校验安装器。脚本不联网，SHA-256 不匹配或 `/opt/code-server` 已存在时拒绝执行：

```bash
sudo deploy/scripts/install-code-server.sh \
  --archive /absolute/path/code-server-4.135.0-linux-amd64.tar.gz
```

然后构建不可变 release。`install.sh` 会复制源码和部署资产，按 `uv.lock` 建立 `--frozen --no-dev` venv，以 `-Werror -lseccomp` 编译 launcher，纳入已打包 VSIX，再原子切换 `current`；它不安装 unit、不操作服务或网络。

```bash
sudo deploy/scripts/install.sh \
  --domain code.example.com \
  --version 2026.09.04-1 \
  --instance 11111111-1111-4111-8111-111111111111 \
  --instance 22222222-2222-4222-8222-222222222222
```

安装结果必须是新目录 `/opt/remote-dev/releases/<VERSION>`，其中包含生产 venv、`libexec/sandbox-exec`、部署策略和提交扩展。同名 release 不可覆盖。原 `current`（若合法）记录到 `previous`，然后借助临时 symlink 和 `mv -T` 原子切换。

## 5. 准备宿主文件（不启动服务）

`prepare-host.sh` 校验 code-server 版本/commit，创建固定 control/runner/gateway 服务账户，布置 unit、AppArmor、锁定设置、Caddyfile、提交扩展和每实例 1 GiB ext4 镜像。开发者仍只有应用 UUID，不创建 Linux 开发账户。脚本不会加载策略、daemon-reload 或启动/启用服务：

```bash
sudo deploy/scripts/prepare-host.sh
```

完成后，先人工审查 `/etc/systemd/system/remote-dev-*`、`code-server@.service`、`/etc/apparmor.d/remote-dev-code-server` 和 `/etc/remote-dev/`。加载 AppArmor、执行 daemon-reload、初始化管理员及启动平台服务属于独立批准的变更窗口；Cloudflare/DNS 始终是另一独立步骤。

## 6. 验证

生产数据和配置准备完成后运行：

```bash
sudo deploy/scripts/verify.sh \
  --domain code.example.com \
  --instance 11111111-1111-4111-8111-111111111111
```

验证内容包括：release symlink 未逃逸、共享 code-server 存在、数据库 `PRAGMA integrity_check`、UUID 工作区/state/config 对应关系、broker socket 权限，以及平台端口没有 wildcard TCP 监听。它不会探测公网、Cloudflare 或 DNS。正式激活前还必须在已加载 AppArmor 和 systemd namespace 的真实实例中，从 Command Palette 选择 Terminal，并核对：`pty.node` 不可加载、无 shell 子进程、AppArmor 拒绝日志符合预期；仅检查 UI 隐藏或 Node `child_process` hook 不足以验收该边界。

仅当静态测试、数据库完整性、systemd/AppArmor/沙箱的独立验收均通过后，才可在另一个经批准的变更步骤中安装 unit 并启动服务。本仓库脚本不执行该步骤。

## 7. 原子备份

先在独立、经批准的操作中停止 code-server 实例，使工作区镜像卸载且 state 静止；不要停止或复制 SQLite WAL/SHM。脚本会只读检查所有 code-server 实例 inactive，自己不会停止服务。控制面可继续运行，数据库由 SQLite online backup API 一致地备份。

```bash
sudo deploy/scripts/backup.sh
# 或写入独立挂载点
sudo deploy/scripts/backup.sh --destination /mnt/secure/remote-dev-backups
```

流程：

1. 在目标目录创建隐藏的 `<UUID>.incomplete`，权限 `0700`；
2. 使用 SQLite online backup API 备份运行中的 `control.db`；
3. 对副本执行 `PRAGMA integrity_check`，不是直接复制 WAL/SHM；
4. 以 reflink（可用时）及 sparse-aware copy 镜像所有 `<UUID>.img`；
5. 保存 DynamicUser code-server state；
6. 只保存已知非秘密的实例/锁定配置，发现 credential-like 字段立即失败；明确排除 Cloudflare 配置和凭据；
7. 生成覆盖 database、workspace、state、config 的 `manifest.sha256`，以及包含类型、权限、UID/GID、大小、路径和 symlink target 的 NUL 分隔 `state-config.inventory.nul`；恢复前同时核对内容 hash 与清单；
8. 最后一次 `mv` 发布为 `/var/backups/remote-dev/<BACKUP_UUID>`。

失败只会留下随后由 trap 清除的 `.incomplete`，绝不会把半份备份发布为完整备份。备份目录本身可能含用户源码，应置于加密、root-only 存储，并按数据保留政策处理。

## 8. 恢复

恢复会替换数据库、workspace、state 和已备份的非秘密配置，因此要求相关平台服务**事先由操作者明确停止**；脚本只检查 inactive，不代替操作者停止服务。

必须且只能选择一种防误覆盖模式：

```bash
# 全新空目标
sudo deploy/scripts/restore.sh \
  --backup-id aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa \
  --empty-target

# 非空目标：先自动完成一份新的现状备份，成功后才替换
sudo deploy/scripts/restore.sh \
  --backup-id aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa \
  --backup-current
```

自定义备份根使用 `--source /absolute/path`。恢复前校验 `manifest.sha256` 和 SQLite 完整性；恢复到 staging 后再次检查数据库。`--empty-target` 遇到数据库或非空目标目录即拒绝；`--backup-current` 的现状备份失败则不进入替换阶段。脚本恢复完仍不会启动服务，应先再次运行 `verify.sh`，再由批准的服务变更流程启动。

## 9. 代码版本回滚

数据恢复与代码回滚是两个独立动作。代码回滚只原子切换版本化 release symlink：

```bash
sudo deploy/scripts/rollback.sh --to-version 2026.09.03-2
```

目标必须是 `/opt/remote-dev/releases` 下已存在的非 symlink release。当前 release 被记入 `previous`。脚本不改数据库、不恢复 workspace、不触碰任何服务；需重启时必须另行批准。回滚后运行 `verify.sh`。

## 10. 审计与事故处理

- 每次保留：测试输出、release 版本、实例 UUID 列表、备份 UUID、manifest 校验结果；不要记录 secret 内容。
- 若发现凭据进入仓库或日志：停止部署、轮换凭据、清理历史，不能只删除工作区文件。
- 若 SQLite integrity、manifest、UUID、域名、release 边界或监听边界失败：不得绕过检查。
- 若恢复中断：保持服务停止；使用刚生成的现状备份或原备份重新执行，不手工拼接 WAL/SHM。
- Cloudflare/DNS 的授权和变更属于单独流程，不得追加到这些脚本。
