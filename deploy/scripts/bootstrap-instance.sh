#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077
require_commands flock readlink install chown chmod systemctl

usage() {
    printf 'Usage: %s --instance UUID --username USERNAME\n' "$0" >&2
    exit 2
}

INSTANCE=""
USERNAME=""
while (( $# )); do
    case $1 in
        --instance) [[ $# -ge 2 ]] || usage; INSTANCE=$2; shift 2 ;;
        --username) [[ $# -ge 2 ]] || usage; USERNAME=$2; shift 2 ;;
        *) usage ;;
    esac
done
validate_uuid "$INSTANCE"
[[ $USERNAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || fail "invalid username"
exec 9>/run/lock/remote-dev-deploy.lock
flock -n 9 || fail "another remote-dev deployment operation is active"
assert_managed_services_inactive
[[ -L $CURRENT_LINK ]] || fail "current release symlink is missing"
RELEASE=$(readlink -f -- "$CURRENT_LINK")
[[ $RELEASE == "$RELEASES_DIR/"* && -d $RELEASE ]] || fail "current release is invalid"
install -d -o root -g root -m 0700 "$CONFIG_DIR/credentials"
repair_database_permissions() {
    local status=$?
    set +e
    secure_control_database_files
    trap - EXIT
    exit "$status"
}
trap repair_database_permissions EXIT
"$RELEASE/app/.venv/bin/remote-dev" bootstrap-instance \
    --instance "$INSTANCE" --username "$USERNAME" --release "$RELEASE"
secure_control_database_files
trap - EXIT
printf 'Bootstrapped instance %s; token was written to the systemd credential store.\n' "$INSTANCE"