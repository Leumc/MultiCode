#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
(( EUID == 0 )) || fail "this script must be run as root"
require_root
umask 077

SOURCE_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
DOMAIN=""
VERSION=""
UUIDS=()

usage() {
    printf 'Usage: %s --domain code.example.com --version VERSION --instance UUID [--instance UUID ...] [--source DIR]\n' "$0" >&2
    exit 2
}

while (( $# )); do
    case $1 in
        --domain) [[ $# -ge 2 ]] || usage; DOMAIN=$2; shift 2 ;;
        --version) [[ $# -ge 2 ]] || usage; VERSION=$2; shift 2 ;;
        --instance) [[ $# -ge 2 ]] || usage; UUIDS+=("$2"); shift 2 ;;
        --source) [[ $# -ge 2 ]] || usage; SOURCE_DIR=$(cd -- "$2" && pwd -P); shift 2 ;;
        *) usage ;;
    esac
done

validate_domain "$DOMAIN"
validate_version "$VERSION"
(( ${#UUIDS[@]} > 0 )) || fail "at least one --instance UUID is required"
(( ${#UUIDS[@]} <= 3 )) || fail "at most three --instance UUIDs are allowed"
declare -A seen_uuids=()
for uuid in "${UUIDS[@]}"; do
    validate_uuid "$uuid"
    [[ ! ${seen_uuids[$uuid]+present} ]] || fail "duplicate instance UUID"
    seen_uuids[$uuid]=1
done
[[ -f $SOURCE_DIR/pyproject.toml && -d $SOURCE_DIR/src && -d $SOURCE_DIR/deploy ]] || fail "source is not a release tree"
require_commands install cp ln mv readlink uv gcc find chmod

# Immutable releases live below /opt/remote-dev/releases.
install -d -o root -g root -m 0755 "$RELEASES_DIR"
FINAL="$RELEASES_DIR/$VERSION"
STAGE="$RELEASES_DIR/.${VERSION}.incomplete.$$"
[[ ! -e $FINAL && ! -L $FINAL ]] || fail "release already exists: $VERSION"
[[ ! -e $CURRENT_LINK || -L $CURRENT_LINK ]] || fail "$CURRENT_LINK exists and is not a symlink"
cleanup() { rm -rf -- "$STAGE"; }
trap cleanup EXIT
install -d -o root -g root -m 0755 "$STAGE/app" "$STAGE/libexec" "$STAGE/extensions"
cp -a -- "$SOURCE_DIR/src" "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/README.md" "$STAGE/app/"
cp -a -- "$SOURCE_DIR/uv.lock" "$STAGE/app/uv.lock"
cp -a -- "$SOURCE_DIR/deploy/systemd" "$SOURCE_DIR/deploy/apparmor" "$SOURCE_DIR/deploy/code-server" "$STAGE/"
cp -a -- "$SOURCE_DIR/extension/remote-dev-submit-0.1.0.vsix" "$STAGE/extensions/"
uv sync --project "$STAGE/app" --frozen --no-dev --no-cache
gcc -O2 -Wall -Wextra -Werror \
    "$SOURCE_DIR/sandbox/sandbox_exec.c" -lseccomp -o "$STAGE/libexec/sandbox-exec"
chmod 0755 "$STAGE/libexec/sandbox-exec"
printf 'domain=%s\nversion=%s\n' "$DOMAIN" "$VERSION" >"$STAGE/release.conf"
printf '%s\n' "${UUIDS[@]}" >"$STAGE/instances"
chown -R root:root "$STAGE"
find "$STAGE" -type f -perm /111 -exec chmod 0755 {} +
find "$STAGE" -type f ! -perm /111 -exec chmod 0644 {} +
find "$STAGE" -type d -exec chmod 0755 {} +
mv -- "$STAGE" "$FINAL"

if [[ -L $CURRENT_LINK ]]; then
    old_target=$(readlink -f -- "$CURRENT_LINK")
    if [[ $old_target == "$RELEASES_DIR/"* && -d $old_target ]]; then
        ln -s -- "$old_target" "/opt/remote-dev/.previous.next.$$"
        mv -Tf -- "/opt/remote-dev/.previous.next.$$" "/opt/remote-dev/previous"
    fi
fi
ln -s -- "$FINAL" "/opt/remote-dev/.current.next.$$"
mv -Tf -- "/opt/remote-dev/.current.next.$$" "$CURRENT_LINK"
trap - EXIT
printf 'Installed release %s; no service was started, enabled, stopped, or restarted.\n' "$VERSION"
