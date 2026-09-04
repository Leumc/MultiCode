#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077

SOURCE_ROOT=$BACKUP_ROOT
BACKUP_ID=""
MODE=""
usage() { printf 'Usage: %s --backup-id UUID (--empty-target|--backup-current) [--source ABSOLUTE_DIRECTORY]\n' "$0" >&2; exit 2; }
while (( $# )); do
    case $1 in
        --backup-id) [[ $# -ge 2 ]] || usage; BACKUP_ID=$2; shift 2 ;;
        --empty-target|--backup-current) [[ -z $MODE ]] || usage; MODE=$1; shift ;;
        --source) [[ $# -ge 2 ]] || usage; SOURCE_ROOT=$2; shift 2 ;;
        *) usage ;;
    esac
done
validate_uuid "$BACKUP_ID"
[[ $MODE == --empty-target || $MODE == --backup-current ]] || fail "explicitly choose --empty-target or --backup-current"
[[ $SOURCE_ROOT == /* && $SOURCE_ROOT != / ]] || fail "backup source must be an absolute, non-root directory"
require_commands sqlite3 sha256sum install cp rsync mv systemctl cmp find sort chown chmod
BACKUP="$SOURCE_ROOT/$BACKUP_ID"
[[ -d $BACKUP && ! -L $BACKUP ]] || fail "backup not found"
(cd -- "$BACKUP" && sha256sum --check --strict manifest.sha256 >/dev/null) || fail "backup manifest verification failed"
(cd -- "$BACKUP" && cmp -s state-config.inventory.nul <(find state config -printf '%y\t%m\t%U\t%G\t%s\t%p\t%l\0' | sort -z)) || fail "state/config inventory verification failed"
[[ $(sqlite3 "$BACKUP/database/control.db" 'PRAGMA integrity_check;') == ok ]] || fail "backup database integrity check failed"
validate_uuid_entries "$BACKUP/workspaces" image
validate_uuid_entries "$BACKUP/state" state
assert_managed_services_inactive

if [[ $MODE == --backup-current ]]; then
    "$SCRIPT_DIR/backup.sh" --destination "$SOURCE_ROOT" >/dev/null
else
    [[ ! -e $CONTROL_DB ]] || fail "--empty-target requires no control database"
    for directory in "$WORKSPACES_DIR" "$STATE_DIR" "$CONFIG_DIR/code-server"; do
        [[ ! -d $directory ]] || [[ -z $(find "$directory" -mindepth 1 -print -quit) ]] || fail "--empty-target requires empty target directories"
    done
fi

# Prepare all replacement trees before moving current data aside.
RESTORE_ROOT="/var/lib/remote-dev/.restore-${BACKUP_ID}.incomplete"
cleanup() { rm -rf -- "$RESTORE_ROOT"; }
trap cleanup EXIT
install -d -m 0700 "$RESTORE_ROOT/workspaces" "$RESTORE_ROOT/state" "$RESTORE_ROOT/config"
cp --reflink=auto --sparse=always --preserve=mode,ownership,timestamps -- "$BACKUP/workspaces/"*.img "$RESTORE_ROOT/workspaces/" 2>/dev/null || true
rsync -aHAX --numeric-ids -- "$BACKUP/state/" "$RESTORE_ROOT/state/"
rsync -a -- "$BACKUP/config/" "$RESTORE_ROOT/config/"
sqlite3 "$BACKUP/database/control.db" ".backup '$RESTORE_ROOT/control.db'"
[[ $(sqlite3 "$RESTORE_ROOT/control.db" 'PRAGMA integrity_check;') == ok ]] || fail "restored database integrity check failed"

# Targets are inactive and either empty or protected by a completed backup; replace without deleting the safety copy.
install -d -m 0700 "$(dirname -- "$CONTROL_DB")" "$(dirname -- "$STATE_DIR")" "$CONFIG_DIR"
rm -rf -- "$WORKSPACES_DIR" "$STATE_DIR" "$CONFIG_DIR/code-server"
mv -- "$RESTORE_ROOT/workspaces" "$WORKSPACES_DIR"
mv -- "$RESTORE_ROOT/state" "$STATE_DIR"
mv -- "$RESTORE_ROOT/config/code-server" "$CONFIG_DIR/code-server"
install -m 0600 -o root -g root "$RESTORE_ROOT/control.db" "$CONTROL_DB.new"
mv -f -- "$CONTROL_DB.new" "$CONTROL_DB"
secure_control_database_files
trap - EXIT
rm -rf -- "$RESTORE_ROOT"
printf 'Restore completed from backup %s; no service was started or restarted.\n' "$BACKUP_ID"
