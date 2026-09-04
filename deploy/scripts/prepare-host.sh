#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077
require_commands install getent groupadd useradd id mktemp rm grep readlink sed

EXPECTED_CODE_SERVER_VERSION="4.135.0"
EXPECTED_CODE_SERVER_COMMIT="de89acbcdce9d9b870008a270c9f6466993d91f4"
[[ -L $CURRENT_LINK ]] || fail "current release symlink is missing"
RELEASE=$(readlink -f -- "$CURRENT_LINK")
[[ $RELEASE == "$RELEASES_DIR/"* && -d $RELEASE ]] || fail "current release is invalid"
validate_domain "$(sed -n 's/^domain=//p' "$RELEASE/release.conf")"
mapfile -t UUIDS <"$RELEASE/instances"
(( ${#UUIDS[@]} >= 1 && ${#UUIDS[@]} <= 3 )) || fail "release must declare one to three instances"
for uuid in "${UUIDS[@]}"; do validate_uuid "$uuid"; done

[[ -x /opt/code-server/lib/node ]] || fail "bundled code-server node is missing"
[[ -f /opt/code-server/out/node/entry.js ]] || fail "code-server entry is missing"
probe_home=$(mktemp -d)
cleanup() { rm -rf -- "$probe_home"; }
trap cleanup EXIT
mapfile -t version_lines < <(
    HOME="$probe_home" XDG_CONFIG_HOME="$probe_home/config" \
        /opt/code-server/lib/node /opt/code-server --version
)
[[ ${version_lines[0]:-} == "$EXPECTED_CODE_SERVER_VERSION" ]] || fail "unexpected code-server version"
[[ ${version_lines[2]:-} == "$EXPECTED_CODE_SERVER_COMMIT" ]] || fail "unexpected code-server commit"

getent group remote-dev-runner >/dev/null || groupadd --system remote-dev-runner
getent passwd remote-dev >/dev/null || \
    useradd --system --gid remote-dev-runner --home-dir /var/lib/remote-dev \
        --shell /usr/sbin/nologin remote-dev
getent passwd remote-dev-runner >/dev/null || \
    useradd --system --gid remote-dev-runner --home-dir /nonexistent \
        --shell /usr/sbin/nologin remote-dev-runner
getent group remote-dev-gateway >/dev/null || groupadd --system remote-dev-gateway
getent passwd remote-dev-gateway >/dev/null || \
    useradd --system --gid remote-dev-gateway --home-dir /nonexistent \
        --shell /usr/sbin/nologin remote-dev-gateway

install -d -o remote-dev -g remote-dev-runner -m 0750 /var/lib/remote-dev
install -d -o remote-dev -g remote-dev-runner -m 0750 /var/lib/remote-dev/workspaces
install -d -o remote-dev-runner -g remote-dev-runner -m 0700 /var/lib/remote-dev/runner
install -d -o root -g root -m 0755 /opt/remote-dev/extensions
install -d -o root -g root -m 0755 /etc/remote-dev/code-server

for unit in code-server@.service remote-dev-control.service remote-dev-worker.service remote-dev-gateway.service; do
    install -o root -g root -m 0644 "$RELEASE/systemd/$unit" "/etc/systemd/system/$unit"
done
install -o root -g root -m 0644 "$RELEASE/apparmor/remote-dev-code-server" \
    /etc/apparmor.d/remote-dev-code-server
install -o root -g root -m 0644 "$RELEASE/code-server/locked-settings.json" \
    /etc/remote-dev/code-server/locked-settings.json
install -o root -g root -m 0644 "$RELEASE/code-server/locked-keybindings.json" \
    /etc/remote-dev/code-server/locked-keybindings.json
printf 'REMOTE_DEV_WORKER_UID=%s\n' "$(id -u remote-dev-runner)" \
    > /etc/remote-dev/control.env
chmod 0600 /etc/remote-dev/control.env

port=9101
gateway_args=()
for uuid in "${UUIDS[@]}"; do
    printf 'CODE_SERVER_PORT=%s\n' "$port" >"/etc/remote-dev/code-server/$uuid.env"
    chmod 0600 "/etc/remote-dev/code-server/$uuid.env"
    if [[ ! -e "/var/lib/remote-dev/workspaces/$uuid.img" ]]; then
        "$RELEASE/app/.venv/bin/python" -c \
            'import sys; from remote_dev.workspace_image import create_workspace_image; create_workspace_image(sys.argv[1], sys.argv[2], size_mib=1024)' \
            /var/lib/remote-dev/workspaces "$uuid"
    fi
    gateway_args+=(--user "$uuid:$port")
    ((port += 1))
done
"$RELEASE/app/.venv/bin/remote-dev" render-gateway "${gateway_args[@]}" \
    --output /etc/remote-dev/Caddyfile
chmod 0644 /etc/remote-dev/Caddyfile

extension_home=$(mktemp -d)
HOME="$extension_home" XDG_CONFIG_HOME="$extension_home/config" \
    /opt/code-server/lib/node /opt/code-server \
    --user-data-dir "$extension_home/user-data" \
    --extensions-dir /opt/remote-dev/extensions \
    --install-extension "$RELEASE/extensions/remote-dev-submit-0.1.0.vsix" --force
rm -rf -- "$extension_home"
trap - EXIT
rm -rf -- "$probe_home"
printf '%s\n' \
    "Host files prepared; no service was started, stopped, enabled, or restarted." \
    "Review unit files, load AppArmor, run daemon-reload, initialize the admin, then start only in an approved change window."
