#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 SimuZ
"""The device identity record: what makes a drive look "already set up".

Setting the password is only half of initializing an IronKey Locker+ 50 G2.
The other half is a small record the vendor's own application keeps on the
drive. When that record is missing, the official Windows and macOS software
does not recognise the drive as initialized: it offers its setup wizard and
would overwrite the password. Writing the record is what makes a drive
initialized on Linux behave identically on every operating system.

The record lives in two protected pages (0x0D and 0x0E, 512 bytes each). It
is encrypted with AES-128-ECB under a fixed key held inside the vendor's
binary, and the pages are opened with the literal password "rick", carried
in the command block itself. Layout, recovered by capturing an official
initialization over USB and confirmed by reading a drive back:

    0x000  "IKVP"        magic; without it, the drive looks uninitialized
    0x004  01            version
    0x007  device mode   0 = not initialized, 1 = initialized
    0x00B  01            a password is set
    0x00D  01
    0x00F  password hint         96-byte slot
    0x06F  (unused slot)
    0x0CF  owner name    0x12F company        0x18F details

One thing worth knowing: the protected-page commands only exist inside an
open secure session. Asked for outside one, the firmware answers "invalid
command operation code" — it hides them rather than refusing them, which is
easy to mistake for a device that does not implement them at all.
"""
import argparse
import fcntl
import os
import sys

# The unlocker may sit next to this file, inside the installed application,
# or in the user's home. Under pkexec the home directory is root's, so "~"
# alone is not enough.
_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    _HERE,
    os.environ.get("IRONKEY_UNLOCKER", ""),
    os.path.join(_HERE, "ironkey-unlocker"),
    os.path.join(os.path.dirname(_HERE), "ironkey-unlocker"),
    os.path.expanduser("~/ironkey-unlocker"),
    "/usr/share/ironkey-lockerplus",
    "/usr/share/ironkey-unlocker",
]
for _cand in _CANDIDATES:
    if _cand and os.path.isfile(os.path.join(_cand, "ironkey_unlock.py")):
        if _cand not in sys.path:
            sys.path.insert(0, _cand)
        break

import ironkey_unlock as ik  # noqa: E402

try:
    from Crypto.Cipher import AES
except ImportError:  # Debian and Ubuntu package pycryptodome as Cryptodome
    from Cryptodome.Cipher import AES

VID_KINGSTON = "0951"

# Fixed key used by the vendor application for this record. It is the same on
# every drive: it depends neither on the device nor on the password.
RECORD_KEY = bytes.fromhex("50444d47fe873130b961884502c67820")
PAGE_PASSWORD = b"rick"
PAGES = (0x0D, 0x0E)
PAGE_SIZE = 512
MAGIC = b"IKVP"
SLOT_SIZE = 96
SLOTS = {"hint": 0x00F, "unused": 0x06F, "name": 0x0CF,
         "company": 0x12F, "details": 0x18F}


def _unmount_ironkey_only():
    """Release the drive without touching the machine's optical drive.

    The unlocker's own version ejects /dev/sr0 unconditionally, which on a
    machine with a real optical drive opens that tray and leaves the IronKey
    alone. Only nodes that really belong to a Kingston device are ejected.
    """
    import subprocess

    nodes = []
    for name in sorted(os.listdir("/sys/block")):
        if not (name.startswith("sr") or name.startswith("sd")):
            continue
        try:
            if ik.block_device_usb_vid(name) == VID_KINGSTON:
                nodes.append("/dev/" + name)
        except Exception:
            pass
    try:
        res = subprocess.run(["findmnt", "-lno", "SOURCE,TARGET"],
                             capture_output=True, text=True, timeout=10)
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and any(parts[0].startswith(n) for n in nodes):
                subprocess.run(["umount", parts[1]], timeout=10,
                               capture_output=True)
    except Exception:
        pass
    for node in nodes:
        try:
            subprocess.run(["eject", node], timeout=10, capture_output=True)
        except Exception:
            pass


def install_safe_unmount():
    """Use the safe release above instead of the unlocker's own version.

    Anything that calls trigger_pid_switch() should call this first.
    """
    ik.unmount_volumes = _unmount_ironkey_only


install_safe_unmount()


def page_cdb(page):
    """Command bytes 3..10 for reading or writing one protected page."""
    return bytes([page, 0x00]) + PAGE_PASSWORD + b"\x00\x00"


def read_page(fd, page):
    return ik.send_hid(fd, 0x63, 0x00, "read", data_len=PAGE_SIZE,
                       cdb3_10=page_cdb(page))


def write_page(fd, page, data):
    if len(data) != PAGE_SIZE:
        raise ValueError("a protected page is exactly %d bytes" % PAGE_SIZE)
    return ik.send_hid(fd, 0x62, 0x00, "write", data=data,
                       data_len=PAGE_SIZE, cdb3_10=page_cdb(page))


def decrypt(buf):
    return AES.new(RECORD_KEY, AES.MODE_ECB).decrypt(buf[:len(buf) // 16 * 16])


def encrypt(buf):
    return AES.new(RECORD_KEY, AES.MODE_ECB).encrypt(buf)


def open_session(fd):
    """Handshake and open a secure session; the pages need one to exist."""
    shared = ik.rsa_handshake(fd)
    ik.send_hid(fd, 0x8B, 0x00, "none",
                cdb3_10=bytes([0x00, 0x03, 0, 0, 0, 0, 0, 0]))
    ik.send_hid(fd, 0x8F, 0x00, "write",
                data=ik.encrypt_pvc_key(ik.PVC0_KEY, shared),
                cdb3_10=bytes([0x02, 0, 0, 0, 0, 0, 0, 0]))
    return shared


def build_record(hint="", name="", company="", details=""):
    """Build a clean record: the fields that matter, everything else zero.

    The vendor application writes whatever happens to be in its own memory
    into the unused space — fragments of its interface, stray pointers. This
    one does not.
    """
    rec = bytearray(PAGE_SIZE * len(PAGES))
    rec[0:4] = MAGIC
    rec[4] = 0x01       # version
    rec[7] = 0x01       # device mode: initialized
    rec[0x0B] = 0x01    # a password is set
    rec[0x0D] = 0x01
    for key, value in (("hint", hint), ("name", name),
                       ("company", company), ("details", details)):
        text = (value or "").encode("latin-1", "replace")[:SLOT_SIZE - 1]
        off = SLOTS[key]
        rec[off:off + len(text)] = text
    return bytes(rec)


def parse_record(rec):
    out = {"valid": rec[:4] == MAGIC, "version": rec[4] if len(rec) > 4 else 0,
           "initialized": bool(rec[7]) if len(rec) > 7 else False}
    for name, off in SLOTS.items():
        raw = rec[off:off + SLOT_SIZE]
        out[name] = raw.split(b"\x00")[0].decode("latin-1", "replace")
    return out


def read_record(fd):
    """Read and decode the record. Returns None when there is none."""
    raw = b"".join(read_page(fd, page) for page in PAGES)
    rec = decrypt(raw)
    if rec[:4] != MAGIC:
        return None
    return parse_record(rec)


def write_record(fd, hint="", name="", company="", details=""):
    encrypted = encrypt(build_record(hint, name, company, details))
    for i, page in enumerate(PAGES):
        write_page(fd, page, encrypted[i * PAGE_SIZE:(i + 1) * PAGE_SIZE])


# --------------------------------------------------------------------------
# Command line, for working on the device without the application
# --------------------------------------------------------------------------
def _open_device():
    if os.geteuid() != 0:
        sys.exit("Root privileges are required (access to /dev/hidraw*).")
    path, _ = ik.find_ironkey_hidraw()
    if not path:
        dev = ik.find_ironkey_locked()
        if not dev:
            sys.exit("No IronKey device found.")
        ik.trigger_pid_switch(dev)
        path, _ = ik.find_ironkey_hidraw()
        if not path:
            sys.exit("Switched to HID mode but no interface appeared.")
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    return fd, path


def _cmd_read(args):
    fd, path = _open_device()
    print("HID interface:", path)
    open_session(fd)
    info = read_record(fd)
    if info is None:
        print("\nNo valid record: the vendor application would treat this "
              "drive as not initialized.")
        return 1
    print("\nDevice identity record")
    print(f"  version     : {info['version']}")
    print(f"  initialized : {'yes' if info['initialized'] else 'no'}")
    print(f"  hint        : {info['hint']!r}")
    print(f"  owner       : {info['name']!r}")
    print(f"  company     : {info['company']!r}")
    print(f"  details     : {info['details']!r}")
    return 0


def _cmd_write(args):
    record = build_record(args.hint, args.name, args.company, args.details)
    print("record to write (first 32 bytes, before encryption):")
    print(" ", record[:32].hex(" "))
    if not args.commit:
        print("\n(dry run: add --commit to write it)")
        return 0
    fd, _ = _open_device()
    open_session(fd)
    write_record(fd, args.hint, args.name, args.company, args.details)
    for page in PAGES:
        print(f"page 0x{page:02X} written")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Read or write the IronKey device identity record")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="read and decode the record")
    r.set_defaults(func=_cmd_read)
    w = sub.add_parser("write", help="write the record (dry run by default)")
    w.add_argument("--hint", default="")
    w.add_argument("--name", default="")
    w.add_argument("--company", default="")
    w.add_argument("--details", default="")
    w.add_argument("--commit", action="store_true")
    w.set_defaults(func=_cmd_write)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
