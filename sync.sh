#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Keep everything in step: the working copy, the installed package and
# GitHub.
#
#   ./sync.sh              build and install locally (what you use daily)
#   ./sync.sh --publish    also commit, push and cut a GitHub release
#   ./sync.sh --status     just show what is aligned and what is not
#
# There are two copies of this application on a development machine: the
# source you edit, and the package you actually run. They drift apart the
# moment you change a file. This puts them back in step.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PKG=ironkey-lockerplus

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

SRC_VERSION="$(python3 -c "
import sys; sys.path.insert(0, '$ROOT/src')
from ironkey_about import VERSION; print(VERSION)")"

installed_version() {
    dpkg-query -W -f='${Version}' "$PKG" 2>/dev/null || echo "none"
}

published_version() {
    command -v gh >/dev/null || { echo "unknown"; return; }
    gh release view --repo SimuZSkyNeT/ironkey-linux-gui \
        --json tagName --jq '.tagName' 2>/dev/null | sed 's/^v//' \
        || echo "none"
}

show_status() {
    local inst pub dirty
    inst="$(installed_version)"
    pub="$(published_version)"
    dirty="$(git status --porcelain 2>/dev/null | wc -l)"

    say "Versions"
    printf '  source      : %s\n' "$SRC_VERSION"
    printf '  installed   : %s\n' "$inst"
    printf '  published   : %s\n' "$pub"
    printf '  uncommitted : %s file(s)\n' "$dirty"

    say "Alignment"
    [ "$inst" = "$SRC_VERSION" ] && ok "installed matches the source" \
        || warn "installed ($inst) differs from the source ($SRC_VERSION)"
    [ "$pub" = "$SRC_VERSION" ] && ok "published matches the source" \
        || warn "published ($pub) differs from the source ($SRC_VERSION)"
    [ "$dirty" = "0" ] && ok "nothing uncommitted" \
        || warn "$dirty file(s) not committed"
}

case "${1:-}" in
--status)
    show_status
    exit 0
    ;;
--publish) DO_PUBLISH=1 ;;
"")        DO_PUBLISH=0 ;;
*)         echo "Unknown option: $1" >&2; exit 1 ;;
esac

show_status

say "1. Checks"
for f in src/*.py; do
    python3 -m py_compile "$f" 2>/dev/null || {
        echo "  $f does not compile" >&2; exit 1; }
done
ok "all modules compile"

say "2. Building the package"
./packaging/build-deb.sh >/dev/null
DEB="dist/${PKG}_${SRC_VERSION}_all.deb"
[ -f "$DEB" ] || { echo "  build produced no .deb" >&2; exit 1; }
ok "$DEB"

say "3. Installing it"
if [ "$(installed_version)" = "$SRC_VERSION" ]; then
    warn "version $SRC_VERSION is already installed"
    warn "apt compares version numbers, not contents — to install a"
    warn "rebuild of the same version, bump VERSION in src/ironkey_about.py"
    warn "or force it with: sudo dpkg -i --force-all $DEB"
else
    # A world-readable copy, so apt's unprivileged sandbox can read it.
    TMP="/tmp/$(basename "$DEB")"
    cp "$DEB" "$TMP"
    chmod 644 "$TMP"
    sudo apt install -y "$TMP"
    rm -f "$TMP"
    ok "installed $(installed_version)"
fi

if [ "${DO_PUBLISH:-0}" = "1" ]; then
    say "4. Publishing"
    ./publish.sh --release
fi

say "Done"
show_status
