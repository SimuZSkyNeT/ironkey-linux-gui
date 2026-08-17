#!/usr/bin/env bash
#
# Portable installer for the IronKey GUI.
#
#   ./install.sh            install for the current user (no root needed)
#   ./install.sh --system   install system-wide (requires root)
#   ./install.sh --uninstall
#
# Nothing is copied: it creates a launcher pointing at this folder, so
# updating the code needs no reinstall.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="user"
ACTION="install"

for arg in "$@"; do
    case "$arg" in
        --system) MODE="system" ;;
        --uninstall) ACTION="uninstall" ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

if [ "$MODE" = "system" ]; then
    APPDIR=/usr/share/applications
    BINDIR=/usr/local/bin
    [ "$(id -u)" -eq 0 ] || { echo "--system requires root (use sudo)." >&2; exit 1; }
else
    APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    BINDIR="$HOME/.local/bin"
fi

DESKTOP="$APPDIR/ironkey.desktop"
LAUNCHER="$BINDIR/ironkey-gui"

if [ "$ACTION" = "uninstall" ]; then
    rm -f "$DESKTOP" "$LAUNCHER" "$BINDIR/ironkey"
    if [ "$MODE" = "system" ]; then
        rm -f /usr/share/polkit-1/actions/org.ironkey.lockerplus.policy
        rm -rf /usr/libexec/ironkey-lockerplus
    fi
    command -v update-desktop-database >/dev/null && \
        update-desktop-database "$APPDIR" 2>/dev/null || true
    echo "Uninstalled."
    exit 0
fi

# The GUI needs a python with PyGObject.
if ! python3 -c "import gi" 2>/dev/null; then
    echo "PyGObject not found. Install it with one of:" >&2
    echo "  Debian/Ubuntu : sudo apt install python3-gi gir1.2-gtk-3.0" >&2
    echo "  Fedora        : sudo dnf install python3-gobject gtk3" >&2
    echo "  Arch          : sudo pacman -S python-gobject gtk3" >&2
    echo "  openSUSE      : sudo zypper install python3-gobject gtk3" >&2
    exit 1
fi

mkdir -p "$APPDIR" "$BINDIR"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec python3 "$SRC/src/ironkey_gui.py" "\$@"
EOF
chmod +x "$LAUNCHER"

# Command-line interface alongside the GUI.
cat > "$BINDIR/ironkey" <<EOF
#!/usr/bin/env bash
exec python3 "$SRC/src/ironkey_cli.py" "\$@"
EOF
chmod +x "$BINDIR/ironkey"

sed "s|^Exec=.*||" "$SRC/ironkey.desktop" > "$DESKTOP"
echo "Exec=$LAUNCHER" >> "$DESKTOP"
chmod 644 "$DESKTOP"

# System-wide only: a polkit action so authentication is remembered for a
# few minutes and the dialog explains which password is wanted.
if [ "$MODE" = "system" ]; then
    install -Dm755 "$SRC/packaging/ironkey-helper" \
        /usr/libexec/ironkey-lockerplus/ironkey-helper
    install -dm755 /usr/share/ironkey-lockerplus
    install -m644 "$SRC"/src/*.py /usr/share/ironkey-lockerplus/
    install -Dm644 "$SRC/packaging/org.ironkey.lockerplus.policy" \
        /usr/share/polkit-1/actions/org.ironkey.lockerplus.policy
    echo "  polkit    : one authentication covers several operations"
fi

command -v update-desktop-database >/dev/null && \
    update-desktop-database "$APPDIR" 2>/dev/null || true

echo "Installed."
echo "  GUI       : $LAUNCHER"
echo "  CLI       : $BINDIR/ironkey"
echo "  menu entry: $DESKTOP"
if [ "$MODE" = "user" ] && ! echo "$PATH" | tr ':' '\n' | grep -qx "$BINDIR"; then
    echo
    echo "Note: $BINDIR is not in PATH. Add to ~/.bashrc:"
    echo "  export PATH=\"\$PATH:$BINDIR\""
fi
echo
echo "GUI: applications menu (\"IronKey Locker+\") or  ironkey-gui"
echo "CLI: ironkey --help"
