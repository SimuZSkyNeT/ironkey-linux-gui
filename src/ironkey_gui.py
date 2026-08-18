#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Graphical front-end — part of IronKey Locker+ for Linux
# Copyright (C) 2026 Simuz
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, see <https://www.gnu.org/licenses/>.
"""
IronKey Locker+ — graphical front-end for Linux
===============================================
Set up, unlock, mount and format a Kingston IronKey Locker+ 50 G2
without touching a terminal.

Portable: works with GTK4 or GTK3, on any distribution. Runs
unprivileged and asks for privileges only when needed, through the
system's own authentication dialog (pkexec).

The password is never passed as a command-line argument: it travels on
the backend's stdin, so it never shows up in `ps`.

Usage:
    python3 ironkey_gui.py
"""

import json
import os
import shutil
import subprocess
import sys
import threading

import gi

try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    GTK4 = True
except (ValueError, ImportError):
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    GTK4 = False

from gi.repository import GLib, Gdk  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "ironkey_backend.py")
sys.path.insert(0, HERE)

from ironkey_about import (  # noqa: E402
    APP_NAME, VERSION, about_text, changelog_text)

try:
    import ironkey_vault as vault
    VAULT_OK = True
except ImportError:
    VAULT_OK = False

try:
    from ironkey_files import open_browser
    FILES_OK = True
except ImportError:
    FILES_OK = False

try:
    import ironkey_update as updater
    UPDATE_OK = True
except ImportError:
    UPDATE_OK = False

try:
    import ironkey_deploy as deployer
    DEPLOY_OK = True
except ImportError:
    DEPLOY_OK = False

APP_ID = "org.ironkey.LockerPlus"
APP_TITLE = APP_NAME

SETTINGS_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "ironkey", "settings.json")


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), mode=0o700, exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def apply_theme(mode):
    """mode: 'system' | 'light' | 'dark'."""
    settings = Gtk.Settings.get_default()
    if settings is None:
        return
    if mode == "dark":
        settings.set_property("gtk-application-prefer-dark-theme", True)
    elif mode == "light":
        settings.set_property("gtk-application-prefer-dark-theme", False)
    else:
        # Follow the desktop: read the portal/GNOME preference if present.
        prefer_dark = False
        try:
            r = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface",
                 "color-scheme"],
                capture_output=True, text=True, timeout=3)
            prefer_dark = "prefer-dark" in r.stdout
        except Exception:
            pass
        settings.set_property("gtk-application-prefer-dark-theme", prefer_dark)

# Device states, in the order a first-time user meets them.
ABSENT, LOCKED, RAW, READY, MOUNTED = (
    "absent", "locked", "raw", "ready", "mounted")

CSS = b"""
.status-title   { font-size: 20px; font-weight: 700; }
.status-detail  { opacity: 0.7; }
.dim            { opacity: 0.6; font-size: 12px; }
.card           { background: alpha(currentColor, 0.05);
                  border-radius: 12px; padding: 18px; }
.ok    { color: #26a269; }
.warn  { color: #e5a50a; }
.err   { color: #c01c28; }
.mono  { font-family: monospace; font-size: 12px; }
"""


# --------------------------------------------------------------------------
# GTK3 / GTK4 compatibility helpers
# --------------------------------------------------------------------------
def add(container, widget, expand=False):
    if GTK4:
        if isinstance(container, Gtk.Box):
            widget.set_vexpand(expand)
            container.append(widget)
        else:
            container.append(widget)
    else:
        container.pack_start(widget, expand, expand, 0)


def set_child(parent, child):
    if GTK4:
        parent.set_child(child)
    else:
        parent.add(child)


def style(widget, *classes):
    ctx = widget.get_style_context()
    for c in classes:
        ctx.add_class(c)
    return widget


def wrap_label(label):
    if hasattr(label, "set_wrap"):
        label.set_wrap(True)
    else:
        label.set_line_wrap(True)
    return label


def show(window):
    if GTK4:
        window.present()
    else:
        window.show_all()


# --------------------------------------------------------------------------
# Backend plumbing
# --------------------------------------------------------------------------
def find_python():
    """An interpreter that can import pycryptodome (the backend needs it).

    The GUI itself needs PyGObject, and the two may live in different
    interpreters — typical when the unlocker sits in a virtualenv.
    """
    candidates = []
    if os.environ.get("IRONKEY_PYTHON"):
        candidates.append(os.environ["IRONKEY_PYTHON"])
    for base in (HERE, os.path.dirname(HERE), os.path.expanduser("~")):
        for name in ("ironkey-unlocker/.venv/bin/python",
                     ".venv/bin/python", "venv/bin/python"):
            candidates.append(os.path.join(base, name))
    candidates += [sys.executable, shutil.which("python3") or "python3"]

    for c in candidates:
        if not c or not os.path.exists(c):
            continue
        for mod in ("Cryptodome", "Crypto"):
            try:
                if subprocess.run([c, "-c", f"import {mod}"],
                                  capture_output=True,
                                  timeout=10).returncode == 0:
                    return c
            except Exception:
                pass
    return sys.executable


PYTHON = find_python()


def udisks_mount(device):
    """Mount through udisks, as the current user.

    Preferred over a privileged mount: it needs no authentication, sets
    ownership correctly by itself, and — crucially — registers the mount
    with the desktop, so the file manager actually shows the drive.
    Returns (ok, message).
    """
    if not shutil.which("udisksctl") or not device:
        return False, "udisksctl not available"
    try:
        r = subprocess.run(["udisksctl", "mount", "--no-user-interaction",
                            "-b", device],
                           capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return False, "udisks did not answer in time."
    except Exception as e:
        return False, str(e)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0 or "already mounted" in out.lower():
        return True, out
    return False, out


def udisks_unmount(device):
    """Unmount through udisks.

    udisks refuses to unmount a filesystem it did not mount itself without
    an extra authorisation, and will sit waiting for it. A short timeout
    keeps that from hanging the application: the caller falls back to the
    privileged helper, which has no such restriction.
    """
    if not shutil.which("udisksctl") or not device:
        return False, "udisksctl not available"
    try:
        r = subprocess.run(["udisksctl", "unmount", "--no-user-interaction",
                            "-b", device],
                           capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False, ("udisks did not answer in time — it was probably "
                       "waiting for an authorisation to unmount a volume "
                       "it did not mount.")
    except Exception as e:
        return False, str(e)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0 or "not mounted" in out.lower():
        return True, out
    return False, out


# Fixed-path helper authorised by our polkit policy. When present, pkexec
# shows OUR wording and can remember the authentication for a few minutes
# (auth_admin_keep), so a whole unlock/use/lock cycle asks only once.
POLKIT_HELPER = "/usr/libexec/ironkey-lockerplus/ironkey-helper"
POLKIT_POLICY = ("/usr/share/polkit-1/actions/"
                 "org.ironkey.lockerplus.policy")


def has_polkit_action():
    return os.path.isfile(POLKIT_HELPER) and os.path.isfile(POLKIT_POLICY)


def privileged_prefix():
    if shutil.which("pkexec") and os.environ.get("DISPLAY"):
        return ["pkexec"]
    if shutil.which("sudo"):
        return ["sudo", "-S"]
    return []


def run_backend(command, password=None, privileged=True, args=()):
    # Privileged work goes through the long-lived helper, so the user is
    # asked to authenticate once per session rather than per operation.
    if privileged:
        return HELPER.send(command, args, password)
    argv = []
    if privileged:
        prefix = privileged_prefix()
        if not prefix:
            return {"ok": False,
                    "message": "Neither pkexec nor sudo is available."}
        argv += prefix
        if prefix[0] == "pkexec" and has_polkit_action():
            # One authentication for several operations, and a dialog that
            # explains which password is wanted.
            argv += [POLKIT_HELPER, command] + [str(a) for a in args]
            return _spawn(argv, password)
    argv += [PYTHON, BACKEND, command] + [str(a) for a in args]
    return _spawn(argv, password)


def _spawn(argv, password):
    try:
        proc = subprocess.run(
            argv, input=(password + "\n") if password is not None else "",
            capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Operation timed out."}
    except Exception as e:
        return {"ok": False, "message": f"Could not start backend: {e}"}

    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    if proc.returncode == 126:
        return {"ok": False, "message": "Authorization denied."}
    return {"ok": False,
            "message": (proc.stderr or "").strip() or "No reply from backend."}



# --------------------------------------------------------------------------
# Persistent privileged helper
# --------------------------------------------------------------------------
class Helper:
    """One authenticated helper process for the whole session.

    Without this, every privileged operation goes through pkexec and asks
    again. Here the helper is started once, authenticated once, and then
    fed requests over a pipe until the application quits — the same shape
    as a system daemon like udisks.

    It is deliberately not a general-purpose root shell: the helper only
    accepts a fixed list of command names, never involves a shell, and
    exits the moment its stdin closes.
    """

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        """Spawn and authenticate. Returns (ok, message)."""
        if self.alive():
            return True, "already running"
        prefix = privileged_prefix()
        if not prefix:
            return False, "Neither pkexec nor sudo is available."
        argv = list(prefix)
        if prefix[0] == "pkexec" and has_polkit_action():
            argv += [POLKIT_HELPER, "serve"]
        else:
            argv += [PYTHON, BACKEND, "serve"]
        try:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1)
        except Exception as e:
            self.proc = None
            return False, f"Could not start the helper: {e}"

        line = self.proc.stdout.readline()
        if not line:
            code = self.proc.poll()
            err = ""
            try:
                err = (self.proc.stderr.read() or "").strip()
            except Exception:
                pass
            self.proc = None
            if code == 126:
                return False, "Authorization denied."
            return False, err or "The helper did not start."
        try:
            first = json.loads(line.strip())
        except ValueError:
            self.proc = None
            return False, "Unexpected reply from the helper."
        if not first.get("serving"):
            # It answered, but refused to serve — report its own reason.
            self.proc = None
            return False, first.get("message", "The helper refused to start.")
        return True, first.get("message", "")

    def send(self, command, args=(), password=None):
        """Run one command through the live helper."""
        with self.lock:
            if not self.alive():
                ok, msg = self.start()
                if not ok:
                    return {"ok": False, "message": msg}
            req = {"command": command, "args": [str(a) for a in args]}
            if password is not None:
                req["password"] = password
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except Exception as e:
                self.proc = None
                return {"ok": False, "message": f"Helper connection lost: {e}"}
            if not line:
                self.proc = None
                return {"ok": False, "message": "The helper stopped."}
            try:
                return json.loads(line.strip())
            except ValueError:
                return {"ok": False, "message": "Unreadable reply."}

    def stop(self):
        if self.alive():
            try:
                self.proc.stdin.write('{"command": "quit"}\n')
                self.proc.stdin.flush()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
        self.proc = None


HELPER = Helper()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class IronKeyWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_TITLE)
        self.set_default_size(520, 620)
        self.state = ABSENT
        self.info = {}
        self.busy = False          # an operation is running
        self._refreshing = False   # a status poll is in flight
        self._browser = None       # the file browser window, once opened
        self.settings = load_settings()
        self.vault_key = None      # set once the vault is unlocked
        apply_theme(self.settings.get("theme", "system"))

        header = Gtk.HeaderBar()
        if GTK4:
            header.set_show_title_buttons(True)
            self.set_titlebar(header)
        else:
            header.set_show_close_button(True)
            header.set_title(APP_TITLE)
            self.set_titlebar(header)

        self.btn_refresh = Gtk.Button()
        self.btn_refresh.set_tooltip_text("Refresh")
        icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic") if GTK4 \
            else Gtk.Image.new_from_icon_name("view-refresh-symbolic",
                                              Gtk.IconSize.BUTTON)
        self.btn_refresh.set_child(icon) if GTK4 else self.btn_refresh.add(icon)
        self.btn_refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.btn_refresh)

        self.btn_menu = Gtk.MenuButton()
        self.btn_menu.set_tooltip_text("Menu")
        micon = Gtk.Image.new_from_icon_name("open-menu-symbolic") if GTK4 \
            else Gtk.Image.new_from_icon_name("open-menu-symbolic",
                                              Gtk.IconSize.BUTTON)
        self.btn_menu.set_child(micon) if GTK4 else self.btn_menu.add(micon)
        self.btn_menu.set_popover(self.build_menu())
        header.pack_end(self.btn_menu)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(outer, m)(20)
        set_child(self, outer)

        # ---- status card -------------------------------------------------
        card = style(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6),
                     "card")
        self.lbl_state = style(Gtk.Label(), "status-title")
        self.lbl_state.set_xalign(0)
        self.lbl_detail = wrap_label(style(Gtk.Label(), "status-detail"))
        self.lbl_detail.set_xalign(0)
        self.lbl_detail.set_selectable(True)
        add(card, self.lbl_state)
        add(card, self.lbl_detail)
        add(outer, card)

        # ---- guidance ----------------------------------------------------
        self.lbl_hint = wrap_label(style(Gtk.Label(), "dim"))
        self.lbl_hint.set_xalign(0)
        add(outer, self.lbl_hint)

        # ---- primary action ----------------------------------------------
        self.btn_primary = Gtk.Button()
        self.btn_primary.set_size_request(-1, 46)
        style(self.btn_primary, "suggested-action")
        self.btn_primary.connect("clicked", self.on_primary)
        add(outer, self.btn_primary)

        # ---- secondary actions -------------------------------------------
        self.row_secondary = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.row_secondary.set_homogeneous(True)
        self.btn_eject = Gtk.Button(label="Unmount")
        self.btn_eject.connect("clicked", lambda *_: self.umount_action())
        self.btn_files = Gtk.Button(label="Browse files")
        self.btn_files.set_tooltip_text(
            "Open the drive's contents inside this app")
        self.btn_files.connect("clicked", self.on_files)
        add(self.row_secondary, self.btn_files, True)
        self.btn_lock = Gtk.Button(label="Lock")
        self.btn_lock.set_tooltip_text(
            "Unmount and re-encrypt the drive without unplugging it")
        self.btn_lock.connect("clicked", self.on_lock)
        add(self.row_secondary, self.btn_eject, True)
        add(self.row_secondary, self.btn_lock, True)
        add(outer, self.row_secondary)

        # ---- spinner ------------------------------------------------------
        self.spinner = Gtk.Spinner()
        add(outer, self.spinner)

        # GTK3's show_all() would force-show everything; opt these two out so
        # the state logic stays in charge of their visibility.
        if not GTK4:
            self.spinner.set_no_show_all(True)

        # ---- advanced ------------------------------------------------------
        adv = Gtk.Expander(label="Advanced")
        advbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        advbox.set_margin_top(10)
        self.btn_init = Gtk.Button(label="Set first password (initialize)…")
        self.btn_init.connect("clicked", self.on_init)
        self.btn_format = Gtk.Button(label="Format data area…")
        self.btn_format.connect("clicked", self.on_format)
        warn = wrap_label(style(Gtk.Label(
            label="Initialization is for a brand-new device only. "
                  "Formatting erases every file on the drive."), "dim"))
        warn.set_xalign(0)
        add(advbox, self.btn_init)
        add(advbox, self.btn_format)
        add(advbox, warn)
        set_child(adv, advbox)
        add(outer, adv)

        # ---- log -----------------------------------------------------------
        logexp = Gtk.Expander(label="Activity log")
        self.buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.buffer)
        view.set_editable(False)
        style(view, "mono")
        wrap = Gtk.WrapMode.WORD_CHAR
        view.set_wrap_mode(wrap)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(140)
        scroll.set_vexpand(True)
        set_child(scroll, view)
        set_child(logexp, scroll)
        add(outer, logexp, True)

        self.connect("destroy", lambda *_: HELPER.stop())
        self.log(f"{APP_NAME} {VERSION}")
        self.log(f"Backend interpreter: {PYTHON}")
        self.refresh()

        # Poll the device so plugging or removing it updates the window
        # on its own. Cheap: status needs no privileges.
        GLib.timeout_add_seconds(4, self._tick)

        # Keyboard shortcuts.
        if not GTK4:
            self.connect("key-press-event", self._on_key)

    # ------------------------------------------------------------------
    def log(self, text):
        self.buffer.insert(self.buffer.get_end_iter(), text + "\n")

    def set_busy(self, busy):
        for b in (self.btn_primary, self.btn_eject, self.btn_lock,
                  self.btn_files,
                  self.btn_init, self.btn_format, self.btn_refresh):
            b.set_sensitive(not busy)
        if busy:
            self.spinner.start()
            self.spinner.show() if not GTK4 else self.spinner.set_visible(True)
        else:
            self.spinner.stop()
            self.spinner.set_visible(False)

    def run(self, command, password=None, then=None, args=()):
        self.set_busy(True)
        self.log("→ " + " ".join([command] + [str(a) for a in args]))

        def worker():
            res = run_backend(command, password, args=args)
            GLib.idle_add(done, res)

        def done(res):
            self.set_busy(False)
            self.log(("  ✓ " if res.get("ok") else "  ✗ ")
                     + str(res.get("message", "")))
            if not res.get("ok"):
                self.notify_error(str(res.get("message", "")))
            if then:
                then(res)
            self.refresh()
            return False

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    def _tick(self):
        if not self.busy:
            self.refresh()
        return True      # keep the timer alive

    def _on_key(self, _w, event):
        from gi.repository import Gdk as _Gdk
        key = event.keyval
        ctrl = event.state & _Gdk.ModifierType.CONTROL_MASK
        if key == _Gdk.KEY_F5:
            self.refresh()
            return True
        if ctrl and key in (_Gdk.KEY_r, _Gdk.KEY_R):
            self.refresh()
            return True
        if ctrl and key in (_Gdk.KEY_u, _Gdk.KEY_U):
            if self.btn_primary.get_sensitive():
                self.on_primary(None)
            return True
        if ctrl and key in (_Gdk.KEY_l, _Gdk.KEY_L):
            if self.btn_lock.get_sensitive():
                self.on_lock(None)
            return True
        if ctrl and key in (_Gdk.KEY_q, _Gdk.KEY_Q):
            self.close()
            return True
        return False

    # ---------------- drive details ----------------
    @staticmethod
    def _human(n):
        if not isinstance(n, (int, float)) or n <= 0:
            return "—"
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if n < 1024:
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
            n /= 1024
        return f"{n:.1f} PiB"

    def on_details(self, _btn):
        self.set_busy(True)

        def worker():
            res = run_backend("info", privileged=False)
            GLib.idle_add(present, res)

        def present(res):
            self.set_busy(False)
            if not res.get("ok"):
                self.notify_error(str(res.get("message", "")))
                return False
            self.details_window(res.get("info", {}))
            return False

        threading.Thread(target=worker, daemon=True).start()

    def details_window(self, info):
        win = Gtk.Window(title="Drive details", transient_for=self)
        win.set_default_size(520, 620)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(box, m)(20)

        def section(title):
            l = Gtk.Label()
            l.set_markup(f"<b>{title}</b>")
            l.set_xalign(0)
            l.set_margin_top(6)
            add(box, l)
            g = Gtk.Grid()
            g.set_column_spacing(16)
            g.set_row_spacing(4)
            add(box, g)
            return g

        def row(grid, r, key, value):
            k = style(Gtk.Label(label=key), "dim")
            k.set_xalign(0)
            v = Gtk.Label(label=str(value) if value not in (None, "") else "—")
            v.set_xalign(0)
            v.set_selectable(True)
            wrap_label(v)
            grid.attach(k, 0, r, 1, 1)
            grid.attach(v, 1, r, 1, 1)

        # --- usage bar first: it is what people look for ---
        usage = info.get("usage")
        cap = info.get("capacity", {})
        if usage:
            l = Gtk.Label()
            l.set_markup("<b>Space</b>")
            l.set_xalign(0)
            add(box, l)
            bar = Gtk.ProgressBar()
            bar.set_fraction(min(usage["percent"] / 100.0, 1.0))
            bar.set_show_text(True)
            bar.set_text(f"{usage['percent']}% used")
            add(box, bar)
            g = Gtk.Grid()
            g.set_column_spacing(16)
            g.set_row_spacing(4)
            add(box, g)
            row(g, 0, "Used", self._human(usage["used"]))
            row(g, 1, "Free", self._human(usage["free"]))
            row(g, 2, "Total", self._human(usage["total"]))
        elif cap:
            g = section("Capacity")
            row(g, 0, "Total", self._human(cap.get("bytes")))
            row(g, 1, "Not mounted", "space usage unavailable")

        # --- state ---
        g = section("State")
        row(g, 0, "Encryption", "Unlocked" if info.get("unlocked")
            else "Locked (data encrypted)")
        blk = info.get("block", {})
        row(g, 1, "Device node", blk.get("name"))
        row(g, 2, "Write protected", "yes" if blk.get("read_only") else "no")
        row(g, 3, "Removable", "yes" if blk.get("removable") else "no")

        # --- filesystem ---
        fsy = info.get("filesystem")
        if fsy:
            g = section("Filesystem")
            row(g, 0, "Type", fsy.get("type") or "none (not formatted)")
            row(g, 1, "Label", fsy.get("label"))
            row(g, 2, "UUID", fsy.get("uuid"))
            row(g, 3, "Mounted at", fsy.get("mountpoint") or "not mounted")
            row(g, 4, "Options", fsy.get("options"))

        # --- hardware ---
        usb = info.get("usb", {})
        scsi = info.get("scsi", {})
        g = section("Hardware")
        row(g, 0, "Manufacturer", usb.get("manufacturer")
            or scsi.get("vendor"))
        row(g, 1, "Model", usb.get("product") or scsi.get("model"))
        row(g, 2, "Serial number", usb.get("serial"))
        row(g, 3, "Firmware revision", scsi.get("revision"))
        row(g, 4, "USB ID", f"{usb.get('vid','')}:{usb.get('pid','')}"
            if usb.get("vid") else "")
        row(g, 5, "Connection", f"{usb.get('speed','')} — "
            f"{usb.get('speed_class','')}" if usb.get("speed") else "")
        row(g, 6, "USB version", usb.get("usb_version"))
        row(g, 7, "Max power draw", usb.get("max_power"))
        row(g, 8, "Sector size", (blk.get("logical_block_size") or "") +
            (" bytes" if blk.get("logical_block_size") else ""))
        if cap.get("sectors"):
            row(g, 9, "Sectors", f"{cap['sectors']:,}")

        sc = Gtk.ScrolledWindow()
        sc.set_vexpand(True)
        set_child(sc, box)
        set_child(win, sc)
        show(win)

    def on_files(self, _btn):
        """Open the built-in browser for the drive's contents."""
        mp = self.info.get("mountpoint")
        if not mp:
            self.notify_error("Mount the drive first.")
            return
        if not FILES_OK:
            self.notify_error("The file browser module is missing.")
            return
        self._browser = open_browser(self, mp)
        self.log("  opened file browser")

    # ---------------- internal tools ----------------
    def _mounted_path(self):
        mp = self.info.get("mountpoint")
        if not mp:
            self.notify_error("Mount the drive first.")
            return None
        return mp

    def on_benchmark(self, _btn):
        """Measure real read and write speed with a temporary file."""
        mp = self._mounted_path()
        if not mp:
            return
        self.confirm(
            "Run a speed test?",
            "A 128 MB temporary file is written, read back and deleted. "
            "It measures the drive, not the page cache.",
            "Run test", lambda: self._run_benchmark(mp))

    def _run_benchmark(self, mp):
        self.set_busy(True)
        self.log("→ speed test")

        def worker():
            import time as _t
            path = os.path.join(mp, ".ironkey-benchmark.tmp")
            size = 128 * 1024 * 1024
            chunk = os.urandom(1024 * 1024)
            res = {}
            try:
                t0 = _t.time()
                with open(path, "wb") as f:
                    for _ in range(size // len(chunk)):
                        f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())       # real write, not cache
                res["write"] = size / (_t.time() - t0) / (1 << 20)

                # Drop what we can so the read is not served from RAM.
                try:
                    subprocess.run(["sync"], capture_output=True, timeout=30)
                except Exception:
                    pass
                t0 = _t.time()
                with open(path, "rb") as f:
                    while f.read(1024 * 1024):
                        pass
                res["read"] = size / (_t.time() - t0) / (1 << 20)
            except Exception as e:
                res["error"] = str(e)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            GLib.idle_add(done, res)

        def done(res):
            self.set_busy(False)
            if "error" in res:
                self.notify_error(f"Speed test failed: {res['error']}")
                return False
            msg = (f"Write: {res['write']:.1f} MB/s\n"
                   f"Read:  {res['read']:.1f} MB/s")
            self.log("  " + msg.replace("\n", "   "))
            self.notify_info("Speed test\n\n" + msg)
            return False

        threading.Thread(target=worker, daemon=True).start()

    def on_verify(self, _btn):
        """Write a known pattern, read it back, compare byte for byte."""
        mp = self._mounted_path()
        if not mp:
            return
        self.confirm(
            "Verify data integrity?",
            "Writes 32 MB of random data, reads it back and compares it. "
            "Confirms the drive stores exactly what it is given.",
            "Verify", lambda: self._run_verify(mp))

    def _run_verify(self, mp):
        self.set_busy(True)
        self.log("→ integrity check")

        def worker():
            import hashlib as _h
            path = os.path.join(mp, ".ironkey-verify.tmp")
            res = {}
            try:
                data = os.urandom(32 * 1024 * 1024)
                digest = _h.sha256(data).hexdigest()
                with open(path, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                with open(path, "rb") as f:
                    back = f.read()
                res["match"] = _h.sha256(back).hexdigest() == digest
                res["digest"] = digest
                res["size"] = len(data)
            except Exception as e:
                res["error"] = str(e)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            GLib.idle_add(done, res)

        def done(res):
            self.set_busy(False)
            if "error" in res:
                self.notify_error(f"Verification failed: {res['error']}")
            elif res.get("match"):
                self.log("  \u2713 integrity OK")
                self.notify_info(
                    "Integrity check passed.\n\n32 MB written and read "
                    "back identically (SHA-256 match).")
            else:
                self.notify_error(
                    "INTEGRITY CHECK FAILED: what was read back does not "
                    "match what was written. Do not trust this drive with "
                    "important data until you investigate.")
            return False

        threading.Thread(target=worker, daemon=True).start()

    def on_fsck(self, _btn):
        if self.state == MOUNTED:
            self.notify_error(
                "Unmount the drive first: a filesystem cannot be checked "
                "while it is in use.")
            return
        if self.state not in (READY, RAW):
            self.notify_error("Unlock the drive first.")
            return
        self.confirm(
            "Check the filesystem?",
            "Runs a read-only consistency check. Nothing is modified "
            "unless you choose to repair afterwards.",
            "Check", lambda: self.run("fsck", args=("no",),
                                      then=self._show_fsck))

    def _show_fsck(self, res):
        out = res.get("output", "")
        body = str(res.get("message", "")) + ("\n\n" + out if out else "")
        self.text_window("Filesystem check", body)

    def on_diagnostics(self, _btn):
        self.confirm(
            "Read firmware diagnostics?",
            "Queries the drive controller directly. Read-only: no password "
            "is sent and the attempt counter is not touched.",
            "Read", lambda: self.run("diagnostics", then=self._show_diag))

    def _show_diag(self, res):
        d = res.get("diagnostics")
        if not d:
            return
        lines = ["Firmware diagnostics", ""]
        pa = d.get("private_area")
        if pa:
            lines += ["Private area",
                      f"  Total sectors     : {pa['total_sectors']:,}",
                      f"  Allocated sectors : {pa['allocated_sectors']:,}",
                      f"  Size              : {pa['human']}",
                      f"  Initialized       : "
                      f"{'yes' if pa['initialized'] else 'no'}",
                      f"  Raw               : {pa['raw']}", ""]
        dc = d.get("device_configuration")
        if dc:
            lines.append("Domains")
            for dom in dc.get("domains", []):
                lines.append(f"  #{dom['index']}: {dom['human']} "
                             f"({dom['sectors']:,} sectors)")
            lines += [f"  Raw: {dc['raw']}", ""]
        lines.append("Query services")
        for svc, e in sorted(d.get("services", {}).items()):
            lines.append(f"  [{svc}] {e.get('title','')}")
            if "protected_area_pages" in e:
                pages = e["protected_area_pages"]
                lines.append(f"      protected area: {pages} pages "
                             f"({pages * 512 // 1024} KiB)")
            if e.get("ascii"):
                lines.append(f"      text: {e['ascii'][:60]}")
            lines.append(f"      raw : {e.get('bytes','')}")
        self.text_window("Firmware diagnostics", "\n".join(lines))

    def on_report(self, _btn):
        """Collect everything into one text file the user can keep or send."""
        self.set_busy(True)
        self.log("→ building report")

        def worker():
            info = run_backend("info", privileged=False)
            status = run_backend("status", privileged=False)
            GLib.idle_add(save, info, status)

        def save(info, status):
            self.set_busy(False)
            import datetime
            lines = [
                f"{APP_NAME} {VERSION} — drive report",
                f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}",
                "",
                "STATUS", json.dumps(status, indent=2),
                "", "INFORMATION", json.dumps(info.get("info", {}), indent=2),
            ]
            body = "\n".join(lines)
            dlg = Gtk.FileChooserDialog(
                title="Save report", transient_for=self,
                action=Gtk.FileChooserAction.SAVE)
            dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dlg.add_button("Save", Gtk.ResponseType.OK)
            try:
                dlg.set_current_name("ironkey-report.txt")
            except Exception:
                pass

            def resp(d, r):
                path = None
                if r == Gtk.ResponseType.OK:
                    try:
                        path = d.get_file().get_path() if GTK4 \
                            else d.get_filename()
                    except Exception:
                        path = None
                d.destroy()
                if not path:
                    return
                try:
                    with open(path, "w") as f:
                        f.write(body)
                    self.log(f"  \u2713 report saved to {path}")
                except OSError as e:
                    self.notify_error(f"Could not save: {e}")
            dlg.connect("response", resp)
            show(dlg)
            return False

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- carry the app on the drive ----------------
    def on_deploy(self, _btn):
        """Copy this application onto the drive, with a checksum manifest."""
        if not DEPLOY_OK:
            self.notify_error("The deploy module is missing.")
            return
        mp = self.info.get("mountpoint")
        if not mp:
            self.notify_error("Mount the drive first.")
            return

        self.set_busy(True)
        self.log("→ verifying this installation before copying")

        def worker():
            state, detail = deployer.verify_source()
            GLib.idle_add(decide, state, detail)

        def decide(state, detail):
            self.set_busy(False)
            self.log(f"  source: {state}")

            if state == "verified":
                self.confirm(
                    "Copy the application to the drive?",
                    f"{detail}\n\n"
                    f"A folder \u201c{deployer.FOLDER}\u201d will be created "
                    f"on the drive containing the application, install "
                    f"instructions, and a SHA-256 manifest so the copy can "
                    f"be checked later.\n\n"
                    "It will not start on its own: the drive's firmware does "
                    "not allow writing to the CD-ROM partition, so the app "
                    "still has to be installed on the computer using it.",
                    "Copy", lambda: self._do_deploy(mp))
            elif state == "modified":
                self.confirm(
                    "This installation has been modified",
                    f"{detail}\n\n"
                    "Copying it would put files on the drive that do not "
                    "match the published release. Anyone verifying the copy "
                    "later would see a mismatch.\n\n"
                    "Continue only if you changed these files yourself and "
                    "know what they contain.",
                    "Copy anyway", lambda: self._do_deploy(mp))
            else:
                self.confirm(
                    "Cannot verify this installation",
                    f"{detail}\n\n"
                    "The copy will still carry a SHA-256 manifest, but there "
                    "is nothing to check the source against right now.",
                    "Copy anyway", lambda: self._do_deploy(mp))
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _do_deploy(self, mountpoint):
        self.set_busy(True)
        self.log("→ copying the application to the drive")

        def worker():
            ok, msg, folder = deployer.deploy(mountpoint)
            ok2, msg2 = (deployer.verify_copy(mountpoint) if ok
                         else (False, ""))
            GLib.idle_add(done, ok, msg, folder, ok2, msg2)

        def done(ok, msg, folder, ok2, msg2):
            self.set_busy(False)
            self.log(("  \u2713 " if ok else "  \u2717 ") + msg)
            if not ok:
                self.notify_error(msg)
                return False
            self.log("  " + ("\u2713 " if ok2 else "\u2717 ") + msg2)
            self.notify_info(
                f"{msg}\n\nVerification right after writing:\n{msg2}\n\n"
                f"Folder: {folder}")
            self.refresh()
            return False

        threading.Thread(target=worker, daemon=True).start()

    def on_verify_copy(self, _btn):
        """Re-check a copy already sitting on the drive."""
        if not DEPLOY_OK:
            self.notify_error("The deploy module is missing.")
            return
        mp = self.info.get("mountpoint")
        if not mp:
            self.notify_error("Mount the drive first.")
            return
        self.set_busy(True)
        self.log("→ verifying the copy on the drive")

        def worker():
            ok, msg = deployer.verify_copy(mp)
            GLib.idle_add(done, ok, msg)

        def done(ok, msg):
            self.set_busy(False)
            self.log(("  \u2713 " if ok else "  \u2717 ") + msg.splitlines()[0])
            if ok:
                self.notify_info("Copy on the drive\n\n" + msg)
            else:
                self.notify_error("Copy on the drive\n\n" + msg)
            return False

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- updates and publishing ----------------
    def on_check_update(self, _btn):
        if not UPDATE_OK:
            self.notify_error("The update module is missing.")
            return
        self.set_busy(True)
        self.log("→ checking for updates")

        def worker():
            res = updater.check_latest()
            GLib.idle_add(present, res)

        def present(res):
            self.set_busy(False)
            if res.get("error"):
                self.notify_info(f"Update check\n\n{res['error']}")
                return False
            if not res.get("available"):
                self.notify_info(
                    f"You are up to date.\n\nInstalled: {VERSION}\n"
                    f"Latest published: {res.get('version')}")
                return False
            kind = updater.install_kind()
            notes = (res.get("notes") or "")[:700]
            body = (f"Version {res['version']} is available "
                    f"(you have {VERSION}).\n\n{notes}")
            if kind == "git":
                self.confirm(
                    f"Update to {res['version']}?",
                    body + "\n\nThis pulls the new code from GitHub into "
                           "your working copy.",
                    "Update", self._do_update)
            else:
                self.notify_info(
                    body + "\n\nThis copy was installed as a package, so "
                           "update it through your package manager, or "
                           f"download it from:\n{res.get('url','')}")
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _do_update(self):
        self.set_busy(True)

        def worker():
            ok, msg = updater.update_from_git()
            GLib.idle_add(done, ok, msg)

        def done(ok, msg):
            self.set_busy(False)
            self.log(("  \u2713 " if ok else "  \u2717 ") + msg.splitlines()[0])
            (self.notify_info if ok else self.notify_error)(msg)
            return False

        threading.Thread(target=worker, daemon=True).start()

    def on_publish(self, _btn):
        """Developer helper: commit and push this working copy to GitHub."""
        if not UPDATE_OK:
            self.notify_error("The update module is missing.")
            return
        st = updater.repo_status()
        if not st:
            self.notify_error(
                "This copy is not a git checkout, so there is nothing to "
                "publish. Clone the repository to work on the code.")
            return
        if not st["changes"] and st["unpushed"] == "0":
            self.notify_info("Nothing to publish: no changes, nothing "
                             "waiting to be pushed.")
            return

        preview = "\n".join(st["changes"][:12])
        if len(st["changes"]) > 12:
            preview += f"\n… and {len(st['changes']) - 12} more"

        dlg = Gtk.Dialog(title="Publish changes", transient_for=self,
                         modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Commit and push", Gtk.ResponseType.OK)
        style(ok, "suggested-action")
        c = dlg.get_content_area()
        c.set_spacing(10)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(c, m)(16)

        head = wrap_label(Gtk.Label(label=
            f"Branch {st['branch']} — {len(st['changes'])} changed file(s), "
            f"{st['unpushed']} commit(s) not yet pushed."))
        head.set_xalign(0)
        add(c, head)

        buf = Gtk.TextBuffer()
        buf.set_text(preview or "(no uncommitted changes)")
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        style(tv, "mono")
        sc = Gtk.ScrolledWindow()
        sc.set_min_content_height(140)
        set_child(sc, tv)
        add(c, sc)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Commit message")
        entry.set_activates_default(True)
        add(c, entry)
        dlg.set_default_response(Gtk.ResponseType.OK)

        def resp(d, r):
            msg = entry.get_text().strip()
            d.destroy()
            if r != Gtk.ResponseType.OK:
                return
            if not msg:
                self.notify_error("A commit message is required.")
                return
            self.set_busy(True)
            self.log("→ publishing to GitHub")

            def worker():
                ok2, out = updater.publish(msg)
                GLib.idle_add(after, ok2, out)

            def after(ok2, out):
                self.set_busy(False)
                self.log("  " + ("\u2713 pushed" if ok2 else "\u2717 failed"))
                self.text_window(
                    "Publish " + ("succeeded" if ok2 else "failed"), out)
                return False

            threading.Thread(target=worker, daemon=True).start()

        dlg.connect("response", resp)
        show(dlg)

    def build_menu(self):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(box, m)(8)

        lbl = Gtk.Label(label="Appearance")
        lbl.set_xalign(0)
        style(lbl, "dim")
        add(box, lbl)

        current = self.settings.get("theme", "system")
        group = None
        for key, text in (("system", "Follow system"),
                          ("light", "Light"), ("dark", "Dark")):
            if GTK4:
                r = Gtk.CheckButton(label=text)
                if group is None:
                    group = r
                else:
                    r.set_group(group)
            else:
                r = Gtk.RadioButton.new_with_label_from_widget(group, text)
                if group is None:
                    group = r
            r.set_active(key == current)
            r.connect("toggled", self.on_theme, key)
            add(box, r)

        add(box, Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        for text, cb in (("Browse files…", self.on_files),
                         ("Drive details…", self.on_details),
                         ("Speed test…", self.on_benchmark),
                         ("Verify integrity…", self.on_verify),
                         ("Check filesystem…", self.on_fsck),
                         ("Firmware diagnostics…", self.on_diagnostics),
                         ("Copy app to drive…", self.on_deploy),
                         ("Verify copy on drive", self.on_verify_copy),
                         ("Save report…", self.on_report),
                         ("Check for updates…", self.on_check_update),
                         ("Publish my changes…", self.on_publish),
                         ("Saved password…", self.on_vault),
                         ("What's new", self.on_changelog),
                         ("About", self.on_about)):
            b = Gtk.Button(label=text)
            b.set_relief(Gtk.ReliefStyle.NONE) if not GTK4 else None
            b.connect("clicked", cb)
            add(box, b)

        set_child(pop, box)
        if not GTK4:
            box.show_all()
        return pop

    def on_theme(self, btn, key):
        if not btn.get_active():
            return
        self.settings["theme"] = key
        save_settings(self.settings)
        apply_theme(key)
        self.log(f"  appearance: {key}")

    def text_window(self, title, body):
        win = Gtk.Window(title=title, transient_for=self)
        win.set_default_size(560, 460)
        buf = Gtk.TextBuffer()
        buf.set_text(body)
        view = Gtk.TextView(buffer=buf)
        view.set_editable(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for m in ("set_left_margin", "set_right_margin",
                  "set_top_margin", "set_bottom_margin"):
            getattr(view, m)(16)
        sc = Gtk.ScrolledWindow()
        sc.set_vexpand(True)
        set_child(sc, view)
        set_child(win, sc)
        show(win)

    def on_changelog(self, _btn):
        self.text_window(f"What's new — {APP_NAME}", changelog_text())

    def on_about(self, _btn):
        self.text_window(f"About {APP_NAME}", about_text())

    # ---------------- saved password (optional vault) ----------------
    def on_vault(self, _btn):
        """Manage the optional encrypted store for the drive password."""
        if not VAULT_OK:
            self.notify_error(
                "The vault needs the 'cryptography' module.\n"
                "Install it with: pip install cryptography")
            return
        meta = vault.read_meta()
        if not meta["exists"]:
            self.vault_setup_dialog()
        else:
            self.vault_manage_dialog(meta)

    def vault_setup_dialog(self):
        dlg = Gtk.Dialog(title="Set up saved password", transient_for=self,
                         modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Create", Gtk.ResponseType.OK)
        style(ok, "suggested-action")
        c = dlg.get_content_area()
        c.set_spacing(10)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(c, m)(16)

        intro = wrap_label(Gtk.Label(label=
            "This lets the app remember your drive password so you do not "
            "have to type it every time."))
        intro.set_xalign(0)
        add(c, intro)

        honest = wrap_label(style(Gtk.Label(label=
            "Note: this does not add protection to the drive itself — the "
            "drive password always does that. What it protects is the "
            "remembered copy, encrypted with scrypt and AES-256-GCM."),
            "dim"))
        honest.set_xalign(0)
        add(c, honest)

        u = Gtk.Entry()
        u.set_placeholder_text("Username")
        add(c, u)
        p1 = Gtk.Entry()
        p1.set_visibility(False)
        p1.set_placeholder_text("Application password (min 8 characters)")
        add(c, p1)
        p2 = Gtk.Entry()
        p2.set_visibility(False)
        p2.set_placeholder_text("Repeat application password")
        add(c, p2)

        def resp(d, r):
            un, a, b = u.get_text().strip(), p1.get_text(), p2.get_text()
            d.destroy()
            if r != Gtk.ResponseType.OK:
                return
            if not un:
                self.notify_error("Enter a username.")
            elif a != b:
                self.notify_error("The two passwords do not match.")
            else:
                try:
                    vault.create(un, a)
                    self.log("  \u2713 vault created")
                    self.notify_info(
                        "Vault created. Next time you unlock the drive you "
                        "can tick \u201cRemember this password\u201d.")
                except ValueError as e:
                    self.notify_error(str(e))
        dlg.connect("response", resp)
        show(dlg)

    def vault_manage_dialog(self, meta):
        body = (f"Username: {meta['username']}\n"
                f"Drive password stored: "
                f"{'yes' if meta['has_secret'] else 'no'}\n\n"
                "Forgetting the saved password does not affect the drive: "
                "you can still open it by typing the device password.")
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text="Saved password")
        dlg.format_secondary_text(body)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.add_button("Forget saved password", Gtk.ResponseType.REJECT)

        def resp(d, r):
            d.destroy()
            if r == Gtk.ResponseType.REJECT:
                if vault.destroy():
                    self.vault_key = None
                    self.log("  \u2713 vault removed")
                else:
                    self.notify_error("Could not remove the vault file.")
        dlg.connect("response", resp)
        show(dlg)

    def try_saved_password(self, on_ready):
        """If a vault exists, offer to use the stored drive password."""
        if not VAULT_OK or not vault.exists():
            on_ready(None)
            return
        meta = vault.read_meta()
        if not meta["has_secret"]:
            on_ready(None)
            return

        dlg = Gtk.Dialog(title="Use saved password", transient_for=self,
                         modal=True)
        dlg.add_button("Type it instead", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Unlock", Gtk.ResponseType.OK)
        style(ok, "suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        c = dlg.get_content_area()
        c.set_spacing(10)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(c, m)(16)
        lab = wrap_label(Gtk.Label(label=
            f"Enter the application password for \u201c{meta['username']}\u201d "
            "to use the saved drive password."))
        lab.set_xalign(0)
        add(c, lab)
        pw = Gtk.Entry()
        pw.set_visibility(False)
        pw.set_placeholder_text("Application password")
        pw.set_activates_default(True)
        add(c, pw)

        def resp(d, r):
            entered = pw.get_text()
            d.destroy()
            if r != Gtk.ResponseType.OK:
                on_ready(None)
                return
            ok2, key, msg = vault.unlock(meta["username"], entered)
            if not ok2:
                self.notify_error(msg)
                on_ready(None)
                return
            self.vault_key = key
            try:
                on_ready(vault.get_secret(key))
            except vault.VaultTampered as e:
                self.notify_error(str(e))
                on_ready(None)
        dlg.connect("response", resp)
        show(dlg)

    def offer_to_save(self, drive_password):
        """After a successful unlock, offer to remember the password.

        If the vault is set up but still locked in this session, ask for
        the application password first — otherwise the offer could never
        be accepted.
        """
        if not VAULT_OK or not vault.exists():
            return
        meta = vault.read_meta()
        if meta["has_secret"]:
            return

        def store():
            if self.vault_key is not None:
                self._save_secret(drive_password)
                return
            # Vault never unlocked this session: ask for its password.
            dlg = Gtk.Dialog(title="Application password",
                             transient_for=self, modal=True)
            dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
            ok = dlg.add_button("Save", Gtk.ResponseType.OK)
            style(ok, "suggested-action")
            dlg.set_default_response(Gtk.ResponseType.OK)
            c = dlg.get_content_area()
            c.set_spacing(10)
            for m in ("set_margin_top", "set_margin_bottom",
                      "set_margin_start", "set_margin_end"):
                getattr(c, m)(16)
            lab = wrap_label(Gtk.Label(label=
                f"Enter the application password for "
                f"\u201c{meta['username']}\u201d to store the drive "
                f"password."))
            lab.set_xalign(0)
            add(c, lab)
            e = Gtk.Entry()
            e.set_visibility(False)
            e.set_placeholder_text("Application password")
            e.set_activates_default(True)
            add(c, e)

            def resp(d, r):
                entered = e.get_text()
                d.destroy()
                if r != Gtk.ResponseType.OK:
                    return
                ok2, key, msg = vault.unlock(meta["username"], entered)
                if not ok2:
                    self.notify_error(msg)
                    return
                self.vault_key = key
                self._save_secret(drive_password)
            dlg.connect("response", resp)
            show(dlg)

        self.confirm(
            "Remember this drive password?",
            "It will be stored encrypted, unlocked by your application "
            "password.",
            "Remember", store)

    def _save_secret(self, drive_password):
        try:
            vault.set_secret(self.vault_key, drive_password)
            self.log("  \u2713 drive password saved")
        except Exception as e:
            self.notify_error(f"Could not save the password: {e}")

    def notify_info(self, text):
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.OK, text=text)
        d.connect("response", lambda x, *_: x.destroy())
        show(d)

    def mount_action(self):
        """Mount without asking for a password when udisks can do it."""
        dev = self.info.get("device")
        self.set_busy(True)
        self.log("→ mount (udisks)")

        def worker():
            ok, msg = udisks_mount(dev)
            GLib.idle_add(done, ok, msg)

        def done(ok, msg):
            self.set_busy(False)
            if ok:
                self.log("  \u2713 " + (msg or "mounted"))
                self.refresh()
            else:
                self.log("  \u2026 udisks could not do it: " + str(msg))
                self.run("mount")      # fallback: privileged mount
            return False

        threading.Thread(target=worker, daemon=True).start()

    def umount_action(self):
        dev = self.info.get("device")
        self.set_busy(True)
        self.log("→ unmount (udisks)")

        def worker():
            ok, msg = udisks_unmount(dev)
            GLib.idle_add(done, ok, msg)

        def done(ok, msg):
            self.set_busy(False)
            if ok:
                self.log("  \u2713 " + (msg or "unmounted"))
                self.refresh()
            else:
                self.log("  \u2026 udisks could not do it: " + str(msg))
                self.run("umount")
            return False

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        # Guard against overlapping polls: the 4s timer must never stack
        # threads on top of a slow backend call.
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True

        def worker():
            try:
                res = run_backend("status", privileged=False)
            except Exception as e:            # never leave the flag stuck
                res = {"ok": False, "message": str(e), "state": "absent"}
            GLib.idle_add(apply_state, res)

        def apply_state(res):
            self._refreshing = False
            self.info = res
            raw = res.get("state", "absent")
            if raw == "unlocked":
                if res.get("mountpoint"):
                    self.state = MOUNTED
                elif not res.get("fstype"):
                    self.state = RAW
                else:
                    self.state = READY
            elif raw == "locked":
                self.state = LOCKED
            else:
                self.state = ABSENT
            self.render()
            return False

        threading.Thread(target=worker, daemon=True).start()

    def render(self):
        """One place decides what the window shows for each state."""
        s, i = self.state, self.info
        ctx = self.lbl_state.get_style_context()
        for c in ("ok", "warn", "err"):
            ctx.remove_class(c)

        if s == ABSENT:
            style(self.lbl_state, "err")
            self.lbl_state.set_text("Not connected")
            self.lbl_detail.set_text("Plug in your IronKey Locker+ drive.")
            self.lbl_hint.set_text("")
            self.btn_primary.set_label("Waiting for device")
            self.btn_primary.set_sensitive(False)

        elif s == LOCKED:
            style(self.lbl_state, "warn")
            self.lbl_state.set_text("Locked")
            self.lbl_detail.set_text("The drive is connected and encrypted.")
            self.lbl_hint.set_text(
                "Ten consecutive wrong passwords erase the data permanently.")
            self.btn_primary.set_label("Unlock")
            self.btn_primary.set_sensitive(True)

        elif s == RAW:
            style(self.lbl_state, "warn")
            self.lbl_state.set_text("Unlocked — not formatted")
            self.lbl_detail.set_text(
                f"{i.get('device','?')} · {i.get('size_gib','?')} GiB")
            self.lbl_hint.set_text(
                "The data area has no filesystem yet. This is normal right "
                "after initialization.")
            self.btn_primary.set_label("Format now…")
            self.btn_primary.set_sensitive(True)

        elif s == READY:
            style(self.lbl_state, "ok")
            self.lbl_state.set_text("Unlocked")
            self.lbl_detail.set_text(
                f"{i.get('device','?')} · {i.get('size_gib','?')} GiB · "
                f"{i.get('fstype','')}")
            self.lbl_hint.set_text("")
            self.btn_primary.set_label("Mount")
            self.btn_primary.set_sensitive(True)

        elif s == MOUNTED:
            style(self.lbl_state, "ok")
            self.lbl_state.set_text("Ready")
            self.lbl_detail.set_text(
                f"{i.get('device','?')} · {i.get('size_gib','?')} GiB · "
                f"{i.get('fstype','')}\nMounted at {i.get('mountpoint','')}")
            self.lbl_hint.set_text(
                "Always unmount before unplugging the drive.")
            self.btn_primary.set_label("Open folder")
            self.btn_primary.set_sensitive(True)

        self.btn_eject.set_sensitive(s == MOUNTED)
        self.btn_lock.set_sensitive(s in (RAW, READY, MOUNTED))
        self.btn_files.set_sensitive(s == MOUNTED and FILES_OK)
        self.btn_init.set_sensitive(s in (LOCKED, RAW, READY, MOUNTED))
        self.btn_format.set_sensitive(s in (RAW, READY, MOUNTED))

    # ------------------------------------------------------------------
    def on_lock(self, _btn):
        """Re-lock the drive: unmount, then close the secure session."""
        self.confirm(
            "Lock the drive?",
            "The drive will be unmounted and encrypted again. "
            "You will need the password to use it next time.",
            "Lock",
            lambda: self.run("lock"))

    def on_umount(self, _btn):
        self.umount_action()

    def on_primary(self, _btn):
        if self.state == LOCKED:
            def proceed(saved):
                if saved:
                    self.log("  using saved drive password")
                    self.run("unlock", saved)
                    return
                self.ask_password(
                    "Unlock IronKey",
                    "Enter the DRIVE password — the one you chose for this "
                    "IronKey, not your computer password.",
                    "Ten consecutive wrong passwords erase all data "
                    "permanently.",
                    confirm=False,
                    on_ok=lambda pw: self.run(
                        "unlock", pw,
                        then=lambda r: (self.offer_to_save(pw),
                                        self.mount_action())
                        if r.get("ok") else None))

            self.try_saved_password(proceed)
        elif self.state == RAW:
            self.on_format(None)
        elif self.state == READY:
            self.mount_action()
        elif self.state == MOUNTED:
            self.on_open(None)

    def on_open(self, _btn):
        mp = self.info.get("mountpoint")
        if not mp:
            return
        for opener in ("xdg-open", "gio"):
            if shutil.which(opener):
                argv = [opener, mp] if opener == "xdg-open" \
                    else [opener, "open", mp]
                try:
                    subprocess.Popen(argv)
                    self.log(f"  opened {mp}")
                    return
                except Exception:
                    continue
        self.notify_error("No file manager found to open the folder.")

    def on_format(self, _btn):
        """Ask which filesystem to use, then confirm, then format."""
        def worker():
            res = run_backend("fstypes", privileged=False)
            GLib.idle_add(present, res)

        def present(res):
            if not res.get("ok"):
                self.notify_error(str(res.get("message", "")))
                return False
            self.format_dialog(res.get("filesystems", []),
                               res.get("default", "exfat"))
            return False

        threading.Thread(target=worker, daemon=True).start()

    def format_dialog(self, filesystems, default_key):
        dlg = Gtk.Dialog(title="Format data area", transient_for=self,
                         modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        go = dlg.add_button("Format", Gtk.ResponseType.OK)
        style(go, "destructive-action")
        dlg.set_default_response(Gtk.ResponseType.CANCEL)

        content = dlg.get_content_area()
        content.set_spacing(12)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(content, m)(16)

        warn = wrap_label(Gtk.Label(
            label="Every file on the drive will be erased."))
        warn.set_xalign(0)
        add(content, warn)

        lbl = Gtk.Label(label="Filesystem")
        lbl.set_xalign(0)
        style(lbl, "dim")
        add(content, lbl)

        radios = {}
        group = None
        for fs in filesystems:
            key = fs["key"]
            title = fs["name"]
            if key == default_key:
                title += "  (recommended)"
            if GTK4:
                r = Gtk.CheckButton(label=title)
                if group is None:
                    group = r
                else:
                    r.set_group(group)
            else:
                r = Gtk.RadioButton.new_with_label_from_widget(group, title)
                if group is None:
                    group = r
            desc = fs["summary"]
            if not fs["available"]:
                r.set_sensitive(False)
                desc += f"  —  not installed (package: {fs['package']})"
            r.set_tooltip_text(desc)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            add(box, r)
            d = wrap_label(style(Gtk.Label(label=desc), "dim"))
            d.set_xalign(0)
            d.set_margin_start(26)
            add(box, d)
            add(content, box)

            radios[key] = r
            if key == default_key and fs["available"]:
                r.set_active(True)

        # If the default is unavailable, select the first one that is.
        if not any(r.get_active() for r in radios.values()):
            for fs in filesystems:
                if fs["available"]:
                    radios[fs["key"]].set_active(True)
                    break

        name_lbl = Gtk.Label(label="Volume name")
        name_lbl.set_xalign(0)
        style(name_lbl, "dim")
        name_lbl.set_margin_top(6)
        add(content, name_lbl)

        entry = Gtk.Entry()
        entry.set_text("IRONKEY")
        entry.set_max_length(15)
        add(content, entry)

        def on_response(d, resp):
            chosen = next((k for k, r in radios.items() if r.get_active()),
                          None)
            name = entry.get_text().strip() or "IRONKEY"
            d.destroy()
            if resp != Gtk.ResponseType.OK or not chosen:
                return
            fsname = next(f["name"] for f in filesystems
                          if f["key"] == chosen)
            self.confirm(
                f"Format as {fsname}?",
                f"The drive will be erased and a new {fsname} filesystem "
                f"named \"{name}\" will be created.\n\n"
                "This cannot be undone.",
                "Erase and format",
                lambda: self.run(
                    "format", args=(chosen, name),
                    then=lambda r: self.mount_action()
                    if r.get("ok") else None))

        dlg.connect("response", on_response)
        show(dlg)

    def on_init(self, _btn):
        self.confirm(
            "Set the first password?",
            "Do this only on a drive that has never been set up.\n\n"
            "If the drive already holds data, that data becomes permanently "
            "unreadable.",
            "Continue",
            lambda: self.ask_password(
                "First password",
                "Choose the DRIVE password. This is stored on the IronKey itself, and is not your computer password.",
                "6 to 16 characters, using at least three of: uppercase, "
                "lowercase, digits, symbols.\n"
                "Write it down — without it the data cannot be recovered.",
                confirm=True,
                on_ok=lambda pw: self.run("init", pw)))

    # ------------------------------------------------------------------
    def ask_password(self, title, subtitle, hint, confirm, on_ok):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = dlg.add_button("OK", Gtk.ResponseType.OK)
        style(ok_btn, "suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)

        content = dlg.get_content_area()
        content.set_spacing(10)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(content, m)(16)

        lbl = wrap_label(Gtk.Label(label=subtitle))
        lbl.set_xalign(0)
        add(content, lbl)

        e1 = Gtk.Entry()
        e1.set_visibility(False)
        e1.set_placeholder_text("Password")
        e1.set_activates_default(True)
        add(content, e1)

        e2 = None
        if confirm:
            e2 = Gtk.Entry()
            e2.set_visibility(False)
            e2.set_placeholder_text("Repeat password")
            e2.set_activates_default(True)
            add(content, e2)

        chk = Gtk.CheckButton(label="Show password")

        def toggled(b):
            v = b.get_active()
            e1.set_visibility(v)
            if e2:
                e2.set_visibility(v)
        chk.connect("toggled", toggled)
        add(content, chk)

        hl = wrap_label(style(Gtk.Label(label=hint), "dim"))
        hl.set_xalign(0)
        add(content, hl)

        def on_response(d, resp):
            pw = e1.get_text()
            pw2 = e2.get_text() if e2 else pw
            d.destroy()
            if resp != Gtk.ResponseType.OK:
                return
            if not pw:
                self.notify_error("No password entered.")
            elif pw != pw2:
                self.notify_error("The two passwords do not match.")
            elif len(pw.encode("utf-8")) > 16:
                self.notify_error(
                    "Password too long: the device accepts 16 bytes at most.")
            else:
                on_ok(pw)

        dlg.connect("response", on_response)
        show(dlg)

    def confirm(self, text, detail, ok_label, on_ok):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.NONE, text=text)
        dlg.format_secondary_text(detail)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        btn = dlg.add_button(ok_label, Gtk.ResponseType.OK)
        style(btn, "destructive-action")

        def resp(d, r):
            d.destroy()
            if r == Gtk.ResponseType.OK:
                on_ok()
        dlg.connect("response", resp)
        show(dlg)

    def notify_error(self, text):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.OK, text=text)
        dlg.connect("response", lambda d, *_: d.destroy())
        show(dlg)


class IronKeyApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        # Single instance: a second launch must raise the existing window
        # rather than silently doing nothing.
        existing = self.get_active_window()
        if existing is not None:
            existing.present()
            return

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        display = Gdk.Display.get_default()
        if GTK4:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        else:
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = IronKeyWindow(self)
        show(win)


def main():
    if not os.path.isfile(BACKEND):
        print(f"Backend not found: {BACKEND}", file=sys.stderr)
        return 1
    return IronKeyApp().run(None)


if __name__ == "__main__":
    sys.exit(main())
