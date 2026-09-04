#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077
require_commands sha256sum tar install mv rm mktemp grep find chmod

EXPECTED_VERSION="4.135.0"
EXPECTED_SHA256="300ef4e37e469e6368a4673c6a623e1c9ba8a34f42b394fb49c431a8900bc7d1"
EXPECTED_VERSION_LINE="4.135.0 de89acbcdce9d9b870008a270c9f6466993d91f4 with Code 1.135.0"
ARCHIVE=""
usage() {
    printf 'Usage: %s --archive /absolute/path/code-server-4.135.0-linux-amd64.tar.gz\n' "$0" >&2
    exit 2
}
while (( $# )); do
    case $1 in
        --archive) [[ $# -ge 2 ]] || usage; ARCHIVE=$2; shift 2 ;;
        *) usage ;;
    esac
done
[[ $ARCHIVE == /* && -f $ARCHIVE && ! -L $ARCHIVE ]] || fail "archive must be an absolute regular file"
actual=$(sha256sum -- "$ARCHIVE")
actual=${actual%% *}
[[ $actual == "$EXPECTED_SHA256" ]] || fail "code-server archive checksum mismatch"
[[ ! -e /opt/code-server && ! -L /opt/code-server ]] || fail "/opt/code-server already exists"

STAGE=$(mktemp -d /opt/.code-server.incomplete.XXXXXX)
cleanup() { rm -rf -- "$STAGE"; }
trap cleanup EXIT
tar -xzf "$ARCHIVE" --strip-components=1 -C "$STAGE" \
    --no-same-owner --no-same-permissions
[[ -x $STAGE/lib/node && -f $STAGE/out/node/entry.js ]] || fail "archive layout is invalid"
version_output=$(
    HOME="$STAGE/.probe-home" XDG_CONFIG_HOME="$STAGE/.probe-config" \
        "$STAGE/lib/node" "$STAGE" --version
)
rm -rf -- "$STAGE/.probe-home" "$STAGE/.probe-config"
[[ $(grep -Fxc -- "$EXPECTED_VERSION_LINE" <<<"$version_output") == 1 ]] || \
    fail "archive version mismatch"
chown -R root:root "$STAGE"
find "$STAGE" -type f -perm /111 -exec chmod 0755 {} +
find "$STAGE" -type f ! -perm /111 -exec chmod 0644 {} +
find "$STAGE" -type d -exec chmod 0755 {} +
mv -- "$STAGE" /opt/code-server
trap - EXIT
printf 'Installed verified code-server %s; no service state was changed.\n' "$EXPECTED_VERSION"
