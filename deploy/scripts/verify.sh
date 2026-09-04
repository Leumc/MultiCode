#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077
(( $# == 0 )) || fail "verify.sh accepts no arguments; the current release is authoritative"
require_commands readlink stat sqlite3 ss sed setpriv id grep tr cut

[[ -L $CURRENT_LINK ]] || fail "current release symlink is missing"
target=$(readlink -f -- "$CURRENT_LINK")
[[ $target == "$RELEASES_DIR/"* && -d $target ]] || fail "current release is outside versioned releases"
validate_domain "$(sed -n 's/^domain=//p' "$target/release.conf")"
mapfile -t release_instances <"$target/instances"
(( ${#release_instances[@]} >= 1 && ${#release_instances[@]} <= 3 )) || fail "invalid release instance count"
declare -A seen_instances=()
for instance in "${release_instances[@]}"; do
    validate_uuid "$instance"
    [[ ! ${seen_instances[$instance]+present} ]] || fail "duplicate release instance UUID"
    seen_instances[$instance]=1
done
[[ -d /opt/code-server && ! -L /opt/code-server ]] || fail "shared /opt/code-server installation is missing or is a symlink"
verify_control_database_files
setpriv --reuid=remote-dev --regid=remote-dev-runner --init-groups \
    sqlite3 "$CONTROL_DB" 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null
secure_control_database_files
[[ $(sqlite3 "file:$CONTROL_DB?mode=ro&immutable=1" 'PRAGMA integrity_check;') == ok ]] || fail "control database integrity check failed"

"$target/app/.venv/bin/python" -m remote_dev.verify_bootstrap \
    --database "$CONTROL_DB" \
    --release "$target" \
    --workspace-root "$WORKSPACES_DIR" \
    --env-dir "$CONFIG_DIR/code-server" \
    --credential-dir "$CONFIG_DIR/credentials" \
    --caddyfile "$CONFIG_DIR/Caddyfile"

[[ ! -S $BROKER_SOCKET || $(stat -c '%a' "$BROKER_SOCKET") == 660 ]] || fail "broker socket mode must be 0660"
while read -r address; do
    case $address in
        0.0.0.0:*|'[::]':*|'*':*) fail "wildcard TCP listener found: $address" ;;
    esac
done < <(ss -H -lnt | tr -s ' ' | cut -d' ' -f4 | grep -E ':(9000|9080|91[0-9][0-9])$' || true)
printf 'Verification passed for release %s.\n' "${target##*/}"
