#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077

VERSION=""
usage() { printf 'Usage: %s --to-version VERSION\n' "$0" >&2; exit 2; }
while (( $# )); do
    case $1 in
        --to-version) [[ $# -ge 2 ]] || usage; VERSION=$2; shift 2 ;;
        *) usage ;;
    esac
done
validate_version "$VERSION"
# Select only an immutable target below /opt/remote-dev/releases.
TARGET="$RELEASES_DIR/$VERSION"
[[ -d $TARGET && ! -L $TARGET ]] || fail "requested release does not exist"
[[ -L $CURRENT_LINK ]] || fail "current release symlink is missing"
old_target=$(readlink -f -- "$CURRENT_LINK")
[[ $old_target == "$RELEASES_DIR/"* && -d $old_target ]] || fail "current release is invalid"
[[ $old_target != "$TARGET" ]] || fail "requested release is already current"
ln -s -- "$old_target" "/opt/remote-dev/.previous.next.$$"
mv -Tf -- "/opt/remote-dev/.previous.next.$$" "/opt/remote-dev/previous"
ln -s -- "$TARGET" "/opt/remote-dev/.current.next.$$"
mv -Tf -- "/opt/remote-dev/.current.next.$$" "$CURRENT_LINK"
printf 'Current release changed to %s; no service was started, stopped, or restarted.\n' "$VERSION"
