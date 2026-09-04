#!/usr/bin/env bash
set -euo pipefail

DOMAIN_RE='^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$'
RELEASES_DIR="/opt/remote-dev/releases"
CURRENT_LINK="/opt/remote-dev/current"
BACKUP_ROOT="/var/backups/remote-dev"
CONTROL_DB="/var/lib/remote-dev/control.db"
WORKSPACES_DIR="/var/lib/remote-dev/workspaces"
STATE_DIR="/var/lib/private/remote-dev/code-server"
CONFIG_DIR="/etc/remote-dev"
BROKER_SOCKET="/run/remote-dev/control-worker.sock"
UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

require_root() {
    (( EUID == 0 )) || fail "this script must be run as root"
}

validate_domain() {
    local value=${1-}
    (( ${#value} <= 253 )) || fail "invalid domain"
    [[ $value =~ $DOMAIN_RE ]] || fail "invalid domain"
}

validate_uuid() {
    [[ ${1-} =~ $UUID_RE ]] || fail "invalid UUID"
}

validate_version() {
    local value=${1-}
    [[ $value =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || fail "invalid release version"
    [[ $value != *..* ]] || fail "invalid release version"
}

require_commands() {
    local command_name
    for command_name in "$@"; do
        command -v "$command_name" >/dev/null 2>&1 || fail "required command is unavailable: $command_name"
    done
}

publish_root_file() {
    local source=$1 destination=$2 mode=$3
    [[ -f $source && ! -L $source ]] || fail "unsafe staged configuration file"
    chown root:root "$source"
    chmod "$mode" "$source"
    mv -Tf -- "$source" "$destination"
}

validate_uuid_entries() {
    local directory=$1 kind=$2 path name
    [[ -d $directory ]] || return 0
    while IFS= read -r -d '' path; do
        name=${path##*/}
        if [[ $kind == image ]]; then
            [[ -f $path && ! -L $path && $name == *.img ]] || fail "invalid workspace image entry"
            name=${name%.img}
        elif [[ $kind == state ]]; then
            [[ -d $path && ! -L $path ]] || fail "invalid state entry"
        else
            fail "invalid UUID entry kind"
        fi
        validate_uuid "$name"
    done < <(find "$directory" -mindepth 1 -maxdepth 1 -print0)
}

assert_managed_services_inactive() {
    local unit
    for unit in remote-dev-control.service remote-dev-worker.service remote-dev-gateway.service 'code-server@*.service'; do
        if systemctl is-active --quiet "$unit" 2>/dev/null; then
            fail "managed service is active ($unit); stop it explicitly before restore"
        fi
    done
}

secure_control_database_files() {
    local path
    for path in "$CONTROL_DB" "$CONTROL_DB-wal" "$CONTROL_DB-shm"; do
        [[ -e $path ]] || continue
        [[ -f $path && ! -L $path ]] || fail "unsafe control database file: $path"
        chown remote-dev:remote-dev-runner "$path"
        chmod 0600 "$path"
    done
}

verify_control_database_files() {
    local path expected owner
    expected="$(id -u remote-dev):$(id -g remote-dev-runner):600"
    for path in "$CONTROL_DB" "$CONTROL_DB-wal" "$CONTROL_DB-shm"; do
        if [[ $path == "$CONTROL_DB" ]]; then
            [[ -e $path ]] || fail "control database is missing"
        elif [[ ! -e $path ]]; then
            continue
        fi
        [[ -f $path && ! -L $path ]] || fail "unsafe control database file: $path"
        owner=$(stat -c '%u:%g:%a' "$path")
        [[ $owner == "$expected" ]] || fail "unsafe control database ownership or mode: $path"
    done
}

assert_workspace_services_inactive() {
    if systemctl is-active --quiet 'code-server@*.service' 2>/dev/null; then
        fail "a code-server instance is active; stop instances explicitly before mirroring workspace images"
    fi
}
