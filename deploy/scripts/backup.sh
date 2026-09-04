#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077

DESTINATION=$BACKUP_ROOT
usage() { printf 'Usage: %s [--destination ABSOLUTE_DIRECTORY]\n' "$0" >&2; exit 2; }
while (( $# )); do
    case $1 in
        --destination) [[ $# -ge 2 ]] || usage; DESTINATION=$2; shift 2 ;;
        *) usage ;;
    esac
done
[[ $DESTINATION == /* && $DESTINATION != / ]] || fail "backup destination must be an absolute, non-root directory"
require_commands sqlite3 install cp rsync sha256sum find sort mv systemctl
[[ -f $CONTROL_DB ]] || fail "control database is missing"
assert_workspace_services_inactive
# Audited production inputs: /var/lib/remote-dev/workspaces UUID images and
# /var/lib/private/remote-dev/code-server UUID state directories.
validate_uuid_entries "$WORKSPACES_DIR" image
validate_uuid_entries "$STATE_DIR" state

BACKUP_ID=$(tr 'A-F' 'a-f' </proc/sys/kernel/random/uuid)
validate_uuid "$BACKUP_ID"
install -d -o root -g root -m 0700 "$DESTINATION"
TMP="$DESTINATION/.${BACKUP_ID}.incomplete"
FINAL="$DESTINATION/$BACKUP_ID"
[[ ! -e $TMP && ! -e $FINAL ]] || fail "backup ID collision"
cleanup() { rm -rf -- "$TMP"; }
trap cleanup EXIT
install -d -m 0700 "$TMP/database" "$TMP/workspaces" "$TMP/state" "$TMP/config/code-server"

# SQLite's online backup API produces a transactionally consistent database while services remain online.
sqlite3 "$CONTROL_DB" ".timeout 30000" ".backup '$TMP/database/control.db'"
[[ $(sqlite3 "$TMP/database/control.db" 'PRAGMA integrity_check;') == ok ]] || fail "backup database integrity check failed"

if [[ -d $WORKSPACES_DIR ]]; then
    while IFS= read -r -d '' image; do
        cp --reflink=auto --sparse=always --preserve=mode,ownership,timestamps -- "$image" "$TMP/workspaces/"
    done < <(find "$WORKSPACES_DIR" -mindepth 1 -maxdepth 1 -type f -name '*.img' -print0)
fi
[[ ! -d $STATE_DIR ]] || rsync -aHAX --numeric-ids -- "$STATE_DIR/" "$TMP/state/"

# Only known non-secret files below /etc/remote-dev are copied; external
# provider configuration and credential stores are excluded.
if [[ -d $CONFIG_DIR/code-server ]]; then
    while IFS= read -r -d '' entry; do
        name=${entry##*/}
        [[ -f $entry && ! -L $entry ]] || fail "unsupported config entry"
        case $name in
            locked-settings.json|locked-keybindings.json) ;;
            *.env) validate_uuid "${name%.env}" ;;
            *) fail "unsupported config entry" ;;
        esac
    done < <(find "$CONFIG_DIR/code-server" -mindepth 1 -maxdepth 1 -print0)
    while IFS= read -r -d '' config; do
        name=${config##*/}; uuid=${name%.env}; validate_uuid "$uuid"
        if grep -Eiq '(password|token|cookie|secret|credential|private[_-]?key)' "$config"; then
            fail "credential-like field found in instance config; refusing to copy it"
        fi
        cp -a -- "$config" "$TMP/config/code-server/"
    done < <(find "$CONFIG_DIR/code-server" -maxdepth 1 -type f -name '*.env' -print0)
fi
for config in locked-settings.json locked-keybindings.json; do
    [[ ! -f $CONFIG_DIR/code-server/$config ]] || cp -a -- "$CONFIG_DIR/code-server/$config" "$TMP/config/code-server/"
done

printf 'backup_id=%s\ncreated_utc=%s\n' "$BACKUP_ID" "$(date -u +%FT%TZ)" >"$TMP/backup.conf"
(
    cd -- "$TMP"
    find state config -printf '%y\t%m\t%U\t%G\t%s\t%p\t%l\0' | sort -z >state-config.inventory.nul
    {
        find database workspaces state config -type f -print0
        printf '%s\0' state-config.inventory.nul
    } | sort -z | xargs -0 sha256sum >manifest.sha256
)
chmod -R go-rwx "$TMP"
mv -- "$TMP" "$FINAL"
trap - EXIT
printf 'Backup completed: %s\n' "$BACKUP_ID"
