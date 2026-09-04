#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root

DOMAIN=""
UUIDS=()
usage() {
    printf 'Usage: %s --domain code.example.com --instance UUID [--instance UUID ...]\n' "$0" >&2
    exit 2
}
while (( $# )); do
    case $1 in
        --domain) [[ $# -ge 2 ]] || usage; DOMAIN=$2; shift 2 ;;
        --instance) [[ $# -ge 2 ]] || usage; UUIDS+=("$2"); shift 2 ;;
        *) usage ;;
    esac
done
validate_domain "$DOMAIN"
(( ${#UUIDS[@]} > 0 )) || fail "at least one --instance UUID is required"
for uuid in "${UUIDS[@]}"; do validate_uuid "$uuid"; done
require_commands readlink stat sqlite3 ss

[[ -L $CURRENT_LINK ]] || fail "current release symlink is missing"
target=$(readlink -f -- "$CURRENT_LINK")
[[ $target == "$RELEASES_DIR/"* && -d $target ]] || fail "current release is outside versioned releases"
[[ -d /opt/code-server && ! -L /opt/code-server ]] || fail "shared /opt/code-server installation is missing or is a symlink"
[[ -f $CONTROL_DB ]] || fail "control database is missing"
[[ $(sqlite3 "$CONTROL_DB" 'PRAGMA integrity_check;') == ok ]] || fail "control database integrity check failed"
validate_uuid_entries "$WORKSPACES_DIR" image
validate_uuid_entries "$STATE_DIR" state

for uuid in "${UUIDS[@]}"; do
    [[ -f "$WORKSPACES_DIR/$uuid.img" ]] || fail "workspace image missing for UUID"
    [[ -d "$STATE_DIR/$uuid" || -d "/var/lib/remote-dev/code-server/$uuid" ]] || fail "code-server state missing for UUID"
    [[ -f "$CONFIG_DIR/code-server/$uuid.env" ]] || fail "instance config missing for UUID"
done
[[ ! -S $BROKER_SOCKET || $(stat -c '%a' "$BROKER_SOCKET") == 660 ]] || fail "broker socket mode must be 0660"

while read -r address; do
    case $address in
        0.0.0.0:*|'[::]':*|'*':*) fail "wildcard TCP listener found: $address" ;;
    esac
done < <(ss -H -lnt | tr -s ' ' | cut -d' ' -f4 | grep -E ':(9000|9080|91[0-9][0-9])$' || true)
printf 'Verification passed for release %s and %d instance(s).\n' "${target##*/}" "${#UUIDS[@]}"
