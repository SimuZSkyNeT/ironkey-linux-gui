#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Build a .deb for Debian, Ubuntu, Mint and elementary OS.
# Needs only dpkg-deb and fakeroot — no root privileges.
#
#   ./packaging/build-deb.sh          -> dist/ironkey-lockerplus_<ver>_all.deb

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG=ironkey-lockerplus
VERSION="$(python3 -c "
import sys; sys.path.insert(0, '$ROOT/src')
from ironkey_about import VERSION; print(VERSION)")"
ARCH=all
BUILD="$ROOT/build/deb"
DIST="$ROOT/dist"

for tool in dpkg-deb fakeroot; do
    command -v "$tool" >/dev/null || {
        echo "Missing $tool. Install with: sudo apt install dpkg-dev fakeroot" >&2
        exit 1
    }
done

rm -rf "$BUILD"
mkdir -p "$BUILD/DEBIAN" \
         "$BUILD/usr/share/$PKG" \
         "$BUILD/usr/bin" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/doc/$PKG"

install -m 644 "$ROOT"/src/*.py "$BUILD/usr/share/$PKG/"
chmod 755 "$BUILD/usr/share/$PKG/ironkey_gui.py" \
          "$BUILD/usr/share/$PKG/ironkey_backend.py"

cat > "$BUILD/usr/bin/ironkey-gui" <<'LAUNCHER'
#!/usr/bin/env bash
exec python3 /usr/share/ironkey-lockerplus/ironkey_gui.py "$@"
LAUNCHER
chmod 755 "$BUILD/usr/bin/ironkey-gui"

cat > "$BUILD/usr/bin/ironkey" <<'CLI'
#!/usr/bin/env bash
exec python3 /usr/share/ironkey-lockerplus/ironkey_cli.py "$@"
CLI
chmod 755 "$BUILD/usr/bin/ironkey"

sed 's|^Exec=.*||' "$ROOT/ironkey.desktop" > "$BUILD/usr/share/applications/$PKG.desktop"
echo "Exec=/usr/bin/ironkey-gui" >> "$BUILD/usr/share/applications/$PKG.desktop"

install -Dm755 "$ROOT/packaging/ironkey-helper" \
    "$BUILD/usr/libexec/ironkey-lockerplus/ironkey-helper"
install -Dm644 "$ROOT/packaging/org.ironkey.lockerplus.policy" \
    "$BUILD/usr/share/polkit-1/actions/org.ironkey.lockerplus.policy"

install -m 644 "$ROOT/LICENSE" "$BUILD/usr/share/doc/$PKG/copyright"
install -m 644 "$ROOT/README.md" "$BUILD/usr/share/doc/$PKG/README.md"

INSTALLED_KB="$(du -ks "$BUILD" | cut -f1)"

cat > "$BUILD/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0,
 python3-pycryptodome | python3-crypto, policykit-1 | polkit
Recommends: udisks2, exfatprogs, python3-cryptography
Suggests: dosfstools, ntfs-3g, e2fsprogs
Installed-Size: $INSTALLED_KB
Maintainer: Simuz <318048242+SimuZSkyNeT@users.noreply.github.com>
Homepage: https://github.com/SimuZSkyNeT/ironkey-linux-gui
Description: Set up and use Kingston IronKey Locker+ drives on Linux
 A graphical application for Kingston IronKey Locker+ 50 G2 encrypted USB
 drives, including first-time initialization — which no other Linux tool
 can do, since Kingston ships setup software only for Windows and macOS.
 .
 Unlock, mount, lock, format with a choice of filesystems, browse the
 drive's contents, run speed and integrity tests, and read firmware
 diagnostics. The interface runs unprivileged and asks for authentication
 only when an operation genuinely needs it.
EOF

# Refresh the desktop database after install/remove.
cat > "$BUILD/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
EOF
cp "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/postrm"
chmod 755 "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/postrm"

mkdir -p "$DIST"
OUT="$DIST/${PKG}_${VERSION}_${ARCH}.deb"
fakeroot dpkg-deb --build "$BUILD" "$OUT" >/dev/null

echo "Built: $OUT"
dpkg-deb --info "$OUT" | sed -n '1,12p'
echo
echo "Install with:  sudo apt install $OUT"
