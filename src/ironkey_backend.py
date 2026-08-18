#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Privileged helper — part of IronKey Locker+ for Linux
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
Privileged backend for the IronKey GUI.

Runs as root via pkexec. The GUI runs unprivileged and invokes this only
for operations that actually need root.

The password is never passed as a command-line argument (it would show up
in `ps`): it arrives on the FIRST LINE of stdin.

Commands:
    status            device state (JSON on stdout)
    unlock            unlock the data area (password on stdin)
    mount             mount the data area
    umount            unmount
    lock              unmount and re-encrypt the drive
    info              detailed hardware and filesystem information
    diagnostics       low-level firmware query (read-only)
    fsck [repair]     check (or repair) the filesystem
    format            create an exFAT filesystem (DESTRUCTIVE)
    init              set the very first password (password on stdin)

Typical invocation from the GUI:
    pkexec /path/venv/bin/python ironkey_backend.py unlock   <<< "password"
"""

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Portability: ironkey_unlock.py may sit next to us, in a sibling folder,
# or be installed system-wide. No hardcoded paths.
_CANDIDATES = [
    os.environ.get("IRONKEY_UNLOCKER", ""),
    HERE,
    os.path.join(HERE, "ironkey-unlocker"),
    os.path.join(os.path.dirname(HERE), "ironkey-unlocker"),
    os.path.expanduser("~/ironkey-unlocker"),
    "/usr/share/ironkey-unlocker",
    "/usr/local/share/ironkey-unlocker",
    "/opt/ironkey-unlocker",
]
for _p in _CANDIDATES:
    if _p and os.path.isfile(os.path.join(_p, "ironkey_unlock.py")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break
if HERE not in sys.path:
    sys.path.insert(0, HERE)

VID_KINGSTON = 0x0951
LABEL = "IRONKEY"


def emit(ok, message, **extra):
    """Every reply is one JSON line, so the GUI never parses free text."""
    out = {"ok": bool(ok), "message": message}
    out.update(extra)
    print(json.dumps(out), flush=True)
    return 0 if ok else 1


def invoking_user():
    """The user who launched pkexec (used for mountpoint ownership)."""
    uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
    if uid:
        try:
            import pwd
            return pwd.getpwuid(int(uid)).pw_name
        except (KeyError, ValueError):
            pass
    return os.environ.get("SUDO_USER") or "root"


def mount_base(user):
    """Mountpoint base following the distro convention.

    Fedora/Arch/openSUSE use /run/media/<user>, Debian/Ubuntu /media/<user>.
    Falls back to /mnt when neither parent exists.
    """
    for base in (f"/run/media/{user}", f"/media/{user}"):
        if os.path.isdir(os.path.dirname(base)):
            return base
    return "/mnt"


# Filesystems offered at format time.
#   tool      : the mkfs binary that must exist
#   argv      : how to invoke it, with {label} and {dev} placeholders
#   label_max : maximum label length the filesystem allows
#   package   : hint shown when the tool is missing
FILESYSTEMS = {
    "exfat": {
        "name": "exFAT",
        "tool": "mkfs.exfat",
        "argv": ["mkfs.exfat", "-n", "{label}", "{dev}"],
        "label_max": 15,
        "package": "exfatprogs",
        "summary": "Recommended. Works on Linux, Windows and macOS, "
                   "no file-size limit.",
    },
    "fat32": {
        "name": "FAT32",
        "tool": "mkfs.vfat",
        "argv": ["mkfs.vfat", "-F", "32", "-n", "{label}", "{dev}"],
        "label_max": 11,
        "package": "dosfstools",
        "summary": "Universal, reads everywhere, but no single file "
                   "larger than 4 GB.",
    },
    "ntfs": {
        "name": "NTFS",
        "tool": "mkfs.ntfs",
        "argv": ["mkfs.ntfs", "-f", "-L", "{label}", "{dev}"],
        "label_max": 32,
        "package": "ntfs-3g",
        "summary": "Best for Windows. Linux reads and writes it; "
                   "macOS only reads it.",
    },
    "ext4": {
        "name": "ext4",
        "tool": "mkfs.ext4",
        "argv": ["mkfs.ext4", "-F", "-L", "{label}", "{dev}"],
        "label_max": 16,
        "package": "e2fsprogs",
        "summary": "Linux only. Permissions and journalling; not readable "
                   "on Windows or macOS.",
    },
    "btrfs": {
        "name": "Btrfs",
        "tool": "mkfs.btrfs",
        "argv": ["mkfs.btrfs", "-f", "-L", "{label}", "{dev}"],
        "label_max": 255,
        "package": "btrfs-progs",
        "summary": "Linux only. Checksums, snapshots and compression.",
    },
}

DEFAULT_FS = "exfat"


def fs_available(key):
    return shutil.which(FILESYSTEMS[key]["tool"]) is not None


def cmd_fstypes():
    """Which filesystems this machine can actually create."""
    out = []
    for key, fs in FILESYSTEMS.items():
        out.append({
            "key": key,
            "name": fs["name"],
            "summary": fs["summary"],
            "available": fs_available(key),
            "package": fs["package"],
        })
    return emit(True, "Filesystem list", filesystems=out, default=DEFAULT_FS)


def block_usb_vid(name):
    path = os.path.realpath(f"/sys/block/{name}/device")
    while path != "/":
        f = os.path.join(path, "idVendor")
        if os.path.isfile(f):
            try:
                with open(f) as fh:
                    return int(fh.read().strip(), 16)
            except (ValueError, OSError):
                return None
        path = os.path.dirname(path)
    return None


def find_data_device():
    """The unlocked Kingston data disk (size > 1 GB)."""
    try:
        r = subprocess.run(["lsblk", "-dbno", "NAME,SIZE,TYPE"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None, 0
    for line in r.stdout.splitlines():
        p = line.split()
        if len(p) >= 3 and p[2] == "disk" and p[0].startswith("sd"):
            try:
                size = int(p[1])
            except ValueError:
                continue
            if size > 1_000_000_000 and block_usb_vid(p[0]) == VID_KINGSTON:
                return f"/dev/{p[0]}", size
    return None, 0


def target_of(dev):
    return f"{dev}1" if os.path.exists(f"{dev}1") else dev


def fs_of(target):
    try:
        r = subprocess.run(["lsblk", "-no", "FSTYPE", target],
                           capture_output=True, text=True, timeout=5)
        out = r.stdout.strip()
        return out.splitlines()[0].strip() if out else ""
    except Exception:
        return ""


def mountpoint_of(target):
    try:
        r = subprocess.run(["findmnt", "-no", "TARGET", target],
                           capture_output=True, text=True, timeout=5)
        out = r.stdout.strip()
        return out.splitlines()[0] if out else ""
    except Exception:
        return ""


def usb_present():
    try:
        r = subprocess.run(["lsusb"], capture_output=True, text=True,
                           timeout=5)
        return "0951:" in r.stdout
    except Exception:
        return False


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def usb_node_for(name):
    """Walk up sysfs from a block device to its USB device node."""
    path = os.path.realpath(f"/sys/block/{name}/device")
    while path and path != "/":
        if os.path.isfile(os.path.join(path, "idVendor")):
            return path
        path = os.path.dirname(path)
    return None


def usb_speed_label(speed):
    """Map the raw sysfs speed to something a person can read."""
    table = {
        "1.5": ("1.5 Mbps", "USB 1.0 low speed"),
        "12": ("12 Mbps", "USB 1.1 full speed"),
        "480": ("480 Mbps", "USB 2.0 high speed"),
        "5000": ("5 Gbps", "USB 3.0 SuperSpeed"),
        "10000": ("10 Gbps", "USB 3.1 SuperSpeed+"),
        "20000": ("20 Gbps", "USB 3.2"),
    }
    return table.get(speed, (f"{speed} Mbps", "unknown"))


def disk_usage(mountpoint):
    if not mountpoint:
        return None
    try:
        st = os.statvfs(mountpoint)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - (st.f_bfree * st.f_frsize)
    return {
        "total": total, "used": used, "free": free,
        "percent": round(used / total * 100, 1) if total else 0,
    }


def cmd_info():
    """Everything worth knowing about the drive. Needs no privileges."""
    dev, size = find_data_device()
    info = {"unlocked": bool(dev)}

    # The USB node exists whether or not the data area is unlocked: when
    # locked, look for the CD-ROM node instead.
    name = os.path.basename(dev) if dev else None
    if not name:
        for cand in sorted(os.listdir("/sys/block")):
            if (cand.startswith("sr") or cand.startswith("sd")) and \
                    block_usb_vid(cand) == VID_KINGSTON:
                name = cand
                break

    if name:
        scsi = os.path.realpath(f"/sys/block/{name}/device")
        info["scsi"] = {
            "vendor": _read(f"{scsi}/vendor"),
            "model": _read(f"{scsi}/model"),
            "revision": _read(f"{scsi}/rev"),
        }
        info["block"] = {
            "name": f"/dev/{name}",
            "read_only": _read(f"/sys/block/{name}/ro") == "1",
            "removable": _read(f"/sys/block/{name}/removable") == "1",
            "logical_block_size": _read(
                f"/sys/block/{name}/queue/logical_block_size"),
        }
        usb = usb_node_for(name)
        if usb:
            speed = _read(f"{usb}/speed")
            rate, gen = usb_speed_label(speed)
            info["usb"] = {
                "manufacturer": _read(f"{usb}/manufacturer"),
                "product": _read(f"{usb}/product"),
                "serial": _read(f"{usb}/serial"),
                "vid": _read(f"{usb}/idVendor"),
                "pid": _read(f"{usb}/idProduct"),
                "bcd_device": _read(f"{usb}/bcdDevice"),
                "usb_version": _read(f"{usb}/version").strip(),
                "speed": rate,
                "speed_class": gen,
                "max_power": _read(f"{usb}/bMaxPower"),
            }

    if dev:
        tgt = target_of(dev)
        mp = mountpoint_of(tgt)
        info["capacity"] = {
            "bytes": size,
            "gib": round(size / (1 << 30), 2),
            "gb": round(size / 1_000_000_000, 2),
            "sectors": size // 512,
        }
        info["filesystem"] = {
            "type": fs_of(tgt),
            "label": _lsblk_field(tgt, "LABEL"),
            "uuid": _lsblk_field(tgt, "UUID"),
            "mountpoint": mp,
            "options": _mount_options(tgt),
        }
        usage = disk_usage(mp)
        if usage:
            info["usage"] = usage

    return emit(True, "Device information", info=info)


def _lsblk_field(target, field):
    try:
        r = subprocess.run(["lsblk", "-no", field, target],
                           capture_output=True, text=True, timeout=5)
        out = r.stdout.strip()
        return out.splitlines()[0].strip() if out else ""
    except Exception:
        return ""


def _mount_options(target):
    try:
        r = subprocess.run(["findmnt", "-no", "OPTIONS", target],
                           capture_output=True, text=True, timeout=5)
        out = r.stdout.strip()
        return out.splitlines()[0] if out else ""
    except Exception:
        return ""


# Vendor query services worth showing. Numbers and field offsets come from
# the SDK inside the vendor's macOS app (see INIT_PROTOCOL.md).
QUERY_SERVICES = {
    3: "Device identity",
    4: "Domain layout",
    6: "Capability flags",
    8: "Protected area and attempt limits",
}


def cmd_diagnostics():
    """Low-level information straight from the drive's firmware.

    Read-only: only query and read commands are sent. No password is
    transmitted, so the attempt counter is untouched.
    """
    fd, iu = open_hid()
    if fd is None:
        return emit(False, iu)

    result = {"services": {}, "raw": {}}
    try:
        try:
            shared = iu.rsa_handshake(fd)
            result["session"] = shared.hex()
        except iu.IronKeyError as e:
            result["session_error"] = str(e)

        import struct as _s

        def query(sub, cdb3_10, length):
            try:
                iu.drain_interrupts(fd, 0.1)
                return iu.send_hid(fd, sub, 0x00, "read",
                                   data_len=length, cdb3_10=cdb3_10)
            except (iu.IronKeyError, OSError):
                return b""

        # FF 00 — NTU_Query_Service, one entry per service.
        for svc, title in QUERY_SERVICES.items():
            cdb = bytearray(16)
            cdb[3] = svc & 0xFF
            cdb[4] = (svc >> 8) & 0xFF
            cdb[6] = 2
            _s.pack_into("<H", cdb, 8, 0x200)
            cdb[10] = 0xA2
            data = query(0x00, bytes(cdb[3:11]), 0x200)
            entry = {"title": title, "bytes": data[:32].hex(" ")}
            if len(data) >= 8:
                entry["length_field"] = _s.unpack_from("<H", data, 4)[0]
                if svc == 8:
                    # Offset 6 = protected-area size, per
                    # DevGetProtectHiddenAreaSize.
                    entry["protected_area_pages"] = _s.unpack_from(
                        "<H", data, 6)[0]
                if svc == 3:
                    txt = bytes(b for b in data[:64]
                                if 32 <= b < 127).decode("ascii", "ignore")
                    entry["ascii"] = txt
            result["services"][str(svc)] = entry

        # FF 21 — device configuration: 8-byte entries from offset 5.
        cfg = query(0x21, bytes(8), 0x200)
        if cfg:
            domains = []
            for i in range(4):
                off = 5 + i * 8
                if off + 4 <= len(cfg):
                    sectors = _s.unpack_from("<I", cfg, off)[0]
                    if sectors:
                        domains.append({
                            "index": i,
                            "sectors": sectors,
                            "bytes": sectors * 512,
                            "human": f"{sectors * 512 / (1 << 30):.2f} GiB",
                        })
            result["device_configuration"] = {
                "raw": cfg[:24].hex(" "),
                "domain_count": cfg[0] if cfg else 0,
                "domains": domains,
            }

        # FF A0 — private area parameters for domain 0.
        area = query(0xA0, bytes([0]) + bytes(7), 0x10)
        if len(area) >= 16:
            d = _s.unpack_from("<4I", area, 0)
            result["private_area"] = {
                "raw": area[:16].hex(" "),
                "total_sectors": d[0],
                "allocated_sectors": d[1],
                "field2": d[2],
                "field3": d[3],
                "human": f"{d[1] * 512 / (1 << 30):.2f} GiB allocated",
                "initialized": bool(d[0] and d[1]),
            }
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    return emit(True, "Diagnostics complete", diagnostics=result)


FSCK_TOOLS = {
    "exfat": "fsck.exfat",
    "vfat": "fsck.vfat",
    "ntfs": "ntfsfix",
    "ext4": "e2fsck",
    "ext3": "e2fsck",
    "ext2": "e2fsck",
    "btrfs": "btrfs",
}


def cmd_fsck(repair="no"):
    """Check the filesystem. Must be unmounted; repair only when asked."""
    dev, _ = find_data_device()
    if not dev:
        return emit(False, "Device is locked.")
    tgt = target_of(dev)
    if mountpoint_of(tgt):
        return emit(False, "Unmount the drive before checking it.")
    fstype = fs_of(tgt)
    tool = FSCK_TOOLS.get(fstype)
    if not tool:
        return emit(False, f"No check tool known for '{fstype or 'unknown'}'.")
    if not shutil.which(tool):
        return emit(False, f"{tool} is not installed.")

    do_repair = str(repair).lower() in ("yes", "true", "1", "repair")
    if tool == "fsck.exfat":
        argv = [tool, "-y", tgt] if do_repair else [tool, "-n", tgt]
    elif tool == "fsck.vfat":
        argv = [tool, "-a", tgt] if do_repair else [tool, "-n", tgt]
    elif tool == "e2fsck":
        argv = [tool, "-p", tgt] if do_repair else [tool, "-n", tgt]
    elif tool == "ntfsfix":
        argv = [tool, tgt] if do_repair else [tool, "-n", tgt]
    else:
        argv = [tool, "check", tgt]

    r = subprocess.run(argv, capture_output=True, text=True)
    output = (r.stdout + r.stderr).strip()
    # fsck exit codes: 0 clean, 1 errors fixed, anything else is trouble.
    ok = r.returncode in (0, 1)
    return emit(ok,
                ("Filesystem is clean." if r.returncode == 0 else
                 "Errors were found and corrected." if r.returncode == 1 else
                 f"Check reported problems (exit {r.returncode})."),
                output=output, exit_code=r.returncode, repaired=do_repair)


def cmd_status():
    dev, size = find_data_device()
    if not dev:
        present = usb_present()
        return emit(True, "Locked" if present else "Not connected",
                    state="locked" if present else "absent")
    tgt = target_of(dev)
    return emit(True, "Unlocked", state="unlocked", device=dev,
                target=tgt, size=size,
                size_gib=round(size / (1 << 30), 2),
                fstype=fs_of(tgt), mountpoint=mountpoint_of(tgt))


def read_password():
    return sys.stdin.readline().rstrip("\n").rstrip("\r")


def _logs_to_stderr():
    """Keep the device modules' running commentary off stdout.

    Replies to the front-end are one JSON line on stdout, and in helper mode
    exactly one line is read back. Anything the device modules print would be
    read in its place, so their log goes to stderr, where it stays available
    for debugging.
    """
    def to_stderr(msg=""):
        print(msg, file=sys.stderr, flush=True)

    for name in ("ironkey_unlock", "ironkey_init", "ironkey_metadata"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "log"):
            mod.log = to_stderr


def open_hid():
    """Find the HID interface, switching the device into HID mode if needed.

    Returns (fd, module) or (None, error_message).
    """
    import fcntl

    import ironkey_unlock as iu
    try:
        # Replaces the unlocker's own release, which ejects /dev/sr0 whatever
        # that happens to be: on a machine with an optical drive it opens that
        # tray and leaves the IronKey alone.
        import ironkey_metadata
        ironkey_metadata.install_safe_unmount()
    except ImportError:
        try:
            from ironkey_status import unmount_ironkey_volumes
            iu.unmount_volumes = unmount_ironkey_volumes
        except ImportError:
            pass
    _logs_to_stderr()

    hidraw, _ = iu.find_ironkey_hidraw()
    if not hidraw:
        d = iu.find_ironkey_locked()
        if not d:
            return None, "No IronKey device found."
        iu.trigger_pid_switch(d)
        hidraw, _ = iu.find_ironkey_hidraw()
        if not hidraw:
            return None, ("Could not switch the device into HID mode. "
                          "Unplug and plug it back in.")

    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl & ~os.O_NONBLOCK)
    return fd, iu


def cmd_unlock():
    dev, _ = find_data_device()
    if dev:
        return emit(True, "Already unlocked.", state="unlocked")

    password = read_password()
    if not password:
        return emit(False, "No password provided.")

    fd, iu = open_hid()
    if fd is None:
        return emit(False, iu)

    try:
        shared = iu.rsa_handshake(fd)
        iu.send_unlock(fd, shared, password)
    except iu.IronKeyError as e:
        return emit(False, f"Unlock failed: {e}. Warning: a wrong password "
                           f"uses up one of the 10 attempts.")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    for _ in range(10):
        time.sleep(1)
        dev, size = find_data_device()
        if dev:
            msg = f"Unlocked: {dev} ({size / (1 << 30):.1f} GiB)"
            # Deliberately NOT mounted here. The caller mounts through
            # udisks, which needs no authentication and — unlike a
            # privileged mount — registers the volume with the desktop so
            # the file manager actually shows it.
            return emit(True, msg, state="unlocked", device=dev,
                        mountpoint="")
    return emit(False, "Commands sent but the data area did not appear.")


def do_mount():
    """Mount the data area. Returns (ok, message, mountpoint).

    Kept separate from cmd_mount so unlock and format can chain into it
    within the SAME privileged invocation — otherwise pkexec would ask
    for authentication once per step.
    """
    dev, _ = find_data_device()
    if not dev:
        return False, "Device is locked.", ""
    tgt = target_of(dev)
    mp = mountpoint_of(tgt)
    if mp:
        return True, f"Already mounted at {mp}", mp
    if not fs_of(tgt):
        return False, "No filesystem yet: format it on first use.", ""
    user = invoking_user()
    mp = os.path.join(mount_base(user), "IronKey")
    os.makedirs(mp, exist_ok=True)

    # exFAT/FAT/NTFS have no POSIX ownership: permissions come from mount
    # options. Without uid/gid the mount ends up owned by root and the
    # user cannot write to it. Filesystems that DO have real ownership
    # (ext4, btrfs) reject these options, so only pass them when needed.
    fstype = fs_of(tgt)
    opts = []
    if fstype in ("exfat", "vfat", "msdos", "ntfs", "ntfs3", "fuseblk"):
        try:
            import pwd
            pw = pwd.getpwnam(user)
            opts = ["-o", f"uid={pw.pw_uid},gid={pw.pw_gid},"
                          f"fmask=0077,dmask=0077"]
        except KeyError:
            opts = []

    r = subprocess.run(["mount"] + opts + [tgt, mp],
                       capture_output=True, text=True)
    if r.returncode != 0 and opts:
        # Some drivers refuse these options: retry without them.
        r = subprocess.run(["mount", tgt, mp], capture_output=True, text=True)
    if r.returncode != 0:
        return False, f"Mount failed: {r.stderr.strip()}", ""

    if fstype in ("ext4", "btrfs", "xfs", "ext3", "ext2"):
        # Real ownership: hand the whole tree to the user.
        subprocess.run(["chown", "-R", f"{user}:{user}", mp],
                       capture_output=True)
    return True, f"Mounted at {mp}", mp


def cmd_mount():
    ok, msg, mp = do_mount()
    return emit(ok, msg, mountpoint=mp)


def cmd_umount():
    dev, _ = find_data_device()
    if not dev:
        return emit(False, "Device is locked.")
    tgt = target_of(dev)
    mp = mountpoint_of(tgt)
    if not mp:
        return emit(True, "Not mounted.")
    subprocess.run(["sync"], capture_output=True)
    r = subprocess.run(["umount", mp], capture_output=True, text=True)
    if r.returncode != 0:
        return emit(False, f"Unmount failed: {r.stderr.strip()}")
    return emit(True, f"Unmounted from {mp}")


def cmd_format(fstype=None, label=None):
    key = (fstype or DEFAULT_FS).lower()
    if key not in FILESYSTEMS:
        return emit(False, f"Unknown filesystem '{key}'. "
                           f"Choose one of: {', '.join(FILESYSTEMS)}")
    fs = FILESYSTEMS[key]
    if not fs_available(key):
        return emit(False, f"{fs['name']} is not available: {fs['tool']} is "
                           f"missing. Install the '{fs['package']}' package.")

    dev, _ = find_data_device()
    if not dev:
        return emit(False, "Device is locked.")
    tgt = target_of(dev)
    mp = mountpoint_of(tgt)
    if mp:
        subprocess.run(["umount", mp], capture_output=True)

    lbl = (label or LABEL)[:fs["label_max"]]
    argv = [a.format(label=lbl, dev=tgt) for a in fs["argv"]]
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        return emit(False, f"Format failed: {(r.stderr or r.stdout).strip()}")

    # Let the kernel notice the new filesystem, then leave mounting to the
    # caller: udisks does it without a password and registers it with the
    # desktop.
    subprocess.run(["udevadm", "settle"], capture_output=True)
    time.sleep(1)
    return emit(True, f"{fs['name']} filesystem created (label \"{lbl}\").",
                fstype=key, mountpoint="")


def cmd_lock():
    """Lock the drive again without unplugging it.

    Mirrors UDV_Logout: unmount, then FF A5 (NTU_Close) inside a PVC0
    session — the twin of the FF A4 login command.
    """
    dev, _ = find_data_device()
    if not dev:
        return emit(True, "Already locked.", state="locked")

    tgt = target_of(dev)
    mp = mountpoint_of(tgt)
    if mp:
        subprocess.run(["sync"], capture_output=True)
        r = subprocess.run(["umount", mp], capture_output=True, text=True)
        if r.returncode != 0:
            return emit(False, f"Cannot lock: the drive is still in use "
                               f"({r.stderr.strip()}). Close any open file "
                               f"or folder and try again.")

    fd, iu = open_hid()
    if fd is None:
        return emit(False, iu)

    try:
        shared = iu.rsa_handshake(fd)
        # Same secure session the unlock path opens.
        iu.send_hid(fd, 0x8B, 0x00, "none",
                    cdb3_10=bytes([0x00, 0x03, 0, 0, 0, 0, 0, 0]))
        iu.send_hid(fd, 0x8F, 0x00, "write",
                    data=iu.encrypt_pvc_key(iu.PVC0_KEY, shared),
                    cdb3_10=bytes([0x02, 0, 0, 0, 0, 0, 0, 0]))
        # FF A5 = NTU_Close, cdb[3] = domain
        iu.send_hid(fd, 0xA5, 0x00, "none",
                    cdb3_10=bytes([0x00, 0, 0, 0, 0, 0, 0, 0]))
        iu.send_hid(fd, 0x89, 0x00, "none")
    except iu.IronKeyError as e:
        return emit(False, f"Lock failed: {e}")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    for _ in range(8):
        time.sleep(1)
        if not find_data_device()[0]:
            return emit(True, "Locked.", state="locked")
    return emit(False, "Lock command sent but the data area is still "
                       "visible. Unplug the drive to be sure.")


def cmd_init(hint="", name="", company="", details=""):
    """Set the first password, and record the identity that other systems read.

    The identity record is written in the same session as the password. It is
    what makes the vendor's own application on Windows and macOS treat the
    drive as initialized; without it, that application offers its setup wizard
    and would overwrite the password just set.
    """
    password = read_password()
    if not password:
        return emit(False, "No password provided.")
    if len(password.encode("utf-8")) > 16:
        return emit(False, "Password too long: 16 bytes maximum.")

    fd, iu = open_hid()
    if fd is None:
        return emit(False, iu)

    from ironkey_init import read_private_area, send_init
    _logs_to_stderr()

    identity = {"hint": hint, "name": name,
                "company": company, "details": details}
    cross_platform = True
    try:
        shared = iu.rsa_handshake(fd)
        area = read_private_area(fd)
        if area["dword0"] == 0 or area["dword1"] == 0:
            return emit(False, "Private area is not allocated as expected; "
                               "stopping to be safe.")
        if area["dword2"] != 0:
            return emit(False, "Unexpected state (dword2 != 0); stopping.")
        try:
            send_init(fd, shared, password, area, dry_run=False,
                      identity=identity)
        except iu.IronKeyError as e:
            # The password is the part that must not be left half-done. If the
            # drive refuses the identity record, say so plainly rather than
            # reporting a failure that did not happen.
            if "0x62" not in str(e):
                raise
            cross_platform = False
    except iu.IronKeyError as e:
        return emit(False, f"Initialization failed: {e}")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if not cross_platform:
        return emit(True, "Password set, but the identity record could not be "
                          "written: this drive will work here, while the "
                          "vendor's application would offer to set it up "
                          "again. The data area now needs formatting.")
    return emit(True, "Password set. The data area now needs formatting.")


def cmd_attempts():
    """How many password attempts are left before the drive erases itself.

    Ten consecutive failures destroy the key, and no Linux tool has ever
    shown this. The count of failures already spent is part of the private
    area structure; a successful unlock puts it back to zero.
    """
    fd, iu = open_hid()
    if fd is None:
        return emit(False, iu)

    from ironkey_init import read_private_area, attempts_from_area, MAX_ATTEMPTS
    _logs_to_stderr()
    try:
        area = read_private_area(fd)
        used, left = attempts_from_area(area)
    except iu.IronKeyError as e:
        return emit(False, f"Could not read the attempt counter: {e}")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if used is None:
        return emit(False, "The attempt counter reads an unexpected value.")
    if left == 0:
        message = ("No attempts left: the next wrong password erases the "
                   "data permanently.")
    elif left <= 3:
        message = (f"Only {left} attempts left before the data is erased "
                   f"permanently.")
    else:
        message = f"{left} of {MAX_ATTEMPTS} attempts left."
    return emit(True, message, attempts_left=left, attempts_used=used,
                attempts_max=MAX_ATTEMPTS)


def cmd_identity():
    """Read the identity record: hint and owner details, as other systems see them."""
    fd, iu = open_hid()
    if fd is None:
        return emit(False, iu)

    import ironkey_metadata as meta
    _logs_to_stderr()
    try:
        meta.open_session(fd)
        info = meta.read_record(fd)
    except iu.IronKeyError as e:
        return emit(False, f"Could not read the identity record: {e}")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if info is None:
        return emit(True, "This drive carries no identity record: the "
                          "vendor's application would treat it as never "
                          "initialized.", identity=None)
    return emit(True, "Identity record read.", identity=info)



def cmd_serve():
    """Stay alive and execute commands from stdin, one JSON request per line.

    This is what lets the application authenticate once instead of at every
    operation. It is the same shape as a system daemon such as udisks: a
    privileged process that only ever performs a fixed set of operations,
    driven by an unprivileged front-end.

    The safety properties that matter:
      - only names in COMMANDS are accepted; anything else is refused
      - no shell is ever involved, arguments are passed as a list
      - the process exits as soon as stdin closes, i.e. when the GUI quits
      - a password arrives in its request line and is never logged

    Request : {"command": "unlock", "args": [], "password": "..."}
    Reply   : one JSON line, exactly as the one-shot commands produce.
    """
    print(json.dumps({"ok": True, "message": "helper ready",
                      "serving": True}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            emit(False, "Malformed request.")
            continue

        name = req.get("command", "")
        if name == "quit":
            emit(True, "helper stopping")
            return 0
        if name not in COMMANDS or name == "serve":
            emit(False, f"Unknown command: {name}")
            continue

        args = [str(a) for a in (req.get("args") or [])]
        password = req.get("password")

        # The one-shot commands read a password from stdin; here it comes
        # in the request, so feed it through a temporary stand-in.
        if password is not None:
            import io
            real_stdin, sys.stdin = sys.stdin, io.StringIO(password + "\n")
            try:
                COMMANDS[name](*args)
            except Exception as e:
                emit(False, f"{type(e).__name__}: {e}")
            finally:
                sys.stdin = real_stdin
        else:
            try:
                COMMANDS[name](*args)
            except Exception as e:
                emit(False, f"{type(e).__name__}: {e}")
    return 0


COMMANDS = {
    "status": cmd_status,
    "info": cmd_info,
    "diagnostics": cmd_diagnostics,
    "fsck": cmd_fsck,
    "fstypes": cmd_fstypes,
    "unlock": cmd_unlock,
    "mount": cmd_mount,
    "umount": cmd_umount,
    "lock": cmd_lock,
    "format": cmd_format,
    "init": cmd_init,
    "identity": cmd_identity,
    "attempts": cmd_attempts,
    "serve": cmd_serve,
}

# Commands that work fine without root, so the GUI never asks needlessly.
UNPRIVILEGED = {"status", "info", "fstypes"}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        return emit(False, f"Unknown command. Use: {', '.join(COMMANDS)}")
    cmd = sys.argv[1]
    if os.geteuid() != 0 and cmd not in UNPRIVILEGED:
        return emit(False, "Root privileges required.")
    try:
        return COMMANDS[cmd](*sys.argv[2:])
    except TypeError as e:
        return emit(False, f"Bad arguments for '{cmd}': {e}")
    except Exception as e:  # the GUI must always get valid JSON back
        return emit(False, f"Unexpected error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
