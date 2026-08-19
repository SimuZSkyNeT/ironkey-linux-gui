#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Command-line interface — part of IronKey Locker+ for Linux
# Copyright (C) 2026 SimuZSkyNeT
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
"""
IronKey Locker+ from the terminal.

Everything the graphical application does to the drive is available here —
without needing a desktop session, so it works over SSH and in scripts.
The graphical extras (file browser, saved-password vault, appearance) are
not, by nature.

    ironkey status                 what the drive is doing
    ironkey info                   hardware and filesystem details
    ironkey unlock                 unlock and mount
    ironkey mount / umount / lock
    ironkey format [--fs exfat] [--label NAME]
    ironkey init                   set the first password (new drive)
    ironkey fsck [--repair]
    ironkey diagnostics            low-level firmware query
    ironkey filesystems            what this machine can format
    ironkey update                 check GitHub for a newer version
    ironkey version

Passwords are read from a terminal prompt, never from the command line.
Use --password-stdin to pipe one in from a script.

Add --json to any command for machine-readable output.
"""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ironkey_about import APP_NAME, GITHUB_URL, VERSION  # noqa: E402

BACKEND = os.path.join(HERE, "ironkey_backend.py")

# Commands the helper can run without root.
UNPRIVILEGED = {"status", "info", "fstypes"}
# Commands that need a password on stdin.
NEEDS_PASSWORD = {"unlock", "init"}
# Two passwords on one channel, current first: never in a command line.
NEEDS_TWO_PASSWORDS = {"passwd"}


def is_root():
    return os.geteuid() == 0


def elevate_prefix():
    """How to become root, preferring the non-graphical route in a terminal."""
    if is_root():
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    if shutil.which("pkexec"):
        return ["pkexec"]
    return []


def call_backend(command, args=(), password=None):
    argv = []
    if command not in UNPRIVILEGED and not is_root():
        prefix = elevate_prefix()
        if not prefix:
            return {"ok": False,
                    "message": "This needs root, but neither sudo nor "
                               "pkexec is available."}
        argv += prefix
        if prefix[0] == "sudo":
            print("Your COMPUTER password may be requested by sudo below.",
                  file=sys.stderr)
    argv += [sys.executable, BACKEND, command] + [str(a) for a in args]

    try:
        proc = subprocess.run(
            argv,
            input=(password + "\n") if password is not None else None,
            capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Timed out."}
    except Exception as e:
        return {"ok": False, "message": f"Could not run the helper: {e}"}

    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"ok": False,
            "message": (proc.stderr or "").strip() or "No reply from helper."}


def ask_password(prompt, confirm=False):
    if not sys.stdin.isatty():
        print("No terminal available for a password prompt. "
              "Use --password-stdin.", file=sys.stderr)
        sys.exit(2)
    pw = getpass.getpass(prompt)
    if confirm and pw != getpass.getpass("Repeat: "):
        print("The two passwords do not match.", file=sys.stderr)
        sys.exit(1)
    return pw


def human(n):
    if not isinstance(n, (int, float)) or n <= 0:
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PiB"


# ------------------------------------------------------------ presenters
def show_status(res):
    state = res.get("state", "unknown")
    labels = {"absent": "Not connected", "locked": "Locked",
              "unlocked": "Unlocked"}
    print(f"State      : {labels.get(state, state)}")
    if state == "unlocked":
        print(f"Device     : {res.get('device','—')}")
        print(f"Capacity   : {res.get('size_gib','—')} GiB")
        print(f"Filesystem : {res.get('fstype') or 'none (not formatted)'}")
        print(f"Mounted at : {res.get('mountpoint') or 'not mounted'}")


def show_info(res):
    i = res.get("info", {})
    usage, cap = i.get("usage"), i.get("capacity", {})
    if usage:
        print("Space")
        print(f"  Used  : {human(usage['used'])} ({usage['percent']}%)")
        print(f"  Free  : {human(usage['free'])}")
        print(f"  Total : {human(usage['total'])}")
    elif cap:
        print(f"Capacity : {human(cap.get('bytes'))}")
    print()
    print("State")
    print(f"  Encryption : "
          f"{'Unlocked' if i.get('unlocked') else 'Locked'}")
    blk = i.get("block", {})
    print(f"  Device     : {blk.get('name','—')}")
    print(f"  Read-only  : {'yes' if blk.get('read_only') else 'no'}")
    fsy = i.get("filesystem")
    if fsy:
        print()
        print("Filesystem")
        print(f"  Type       : {fsy.get('type') or 'none'}")
        print(f"  Label      : {fsy.get('label') or '—'}")
        print(f"  UUID       : {fsy.get('uuid') or '—'}")
        print(f"  Mounted at : {fsy.get('mountpoint') or 'not mounted'}")
    usb, scsi = i.get("usb", {}), i.get("scsi", {})
    print()
    print("Hardware")
    print(f"  Model      : {usb.get('product') or scsi.get('model','—')}")
    print(f"  Serial     : {usb.get('serial','—')}")
    print(f"  Firmware   : {scsi.get('revision','—')}")
    print(f"  USB ID     : {usb.get('vid','')}:{usb.get('pid','')}")
    print(f"  Link speed : {usb.get('speed','—')} "
          f"({usb.get('speed_class','')})")


def show_filesystems(res):
    print(f"{'KEY':<8} {'NAME':<8} {'AVAILABLE':<10} DESCRIPTION")
    for fs in res.get("filesystems", []):
        avail = "yes" if fs["available"] else f"no ({fs['package']})"
        print(f"{fs['key']:<8} {fs['name']:<8} {avail:<10} {fs['summary']}")


def show_diagnostics(res):
    d = res.get("diagnostics", {})
    pa = d.get("private_area")
    if pa:
        print("Private area")
        print(f"  Total sectors     : {pa['total_sectors']:,}")
        print(f"  Allocated sectors : {pa['allocated_sectors']:,}")
        print(f"  Size              : {pa['human']}")
        print(f"  Initialized       : "
              f"{'yes' if pa['initialized'] else 'no'}")
        print()
    dc = d.get("device_configuration")
    if dc:
        print("Domains")
        for dom in dc.get("domains", []):
            print(f"  #{dom['index']}: {dom['human']} "
                  f"({dom['sectors']:,} sectors)")
        print()
    print("Query services")
    for svc, e in sorted(d.get("services", {}).items()):
        print(f"  [{svc}] {e.get('title','')}")
        if "protected_area_pages" in e:
            print(f"       protected area: {e['protected_area_pages']} pages")
        if e.get("ascii"):
            print(f"       text: {e['ascii'][:60]}")


# ----------------------------------------------------------------- main
def show_attempts(res):
    left = res.get("attempts_left")
    total = res.get("attempts_max", 10)
    print(f"Attempts left : {left} of {total}")
    if left is not None and left <= 3:
        print("When they run out the drive erases its key. There is no "
              "recovery afterwards.")


def show_identity(res):
    """The record that tells other operating systems the drive is set up."""
    ident = res.get("identity")
    if not ident:
        print("No identity record on this drive.")
        print("It works here, but the vendor's application on Windows and "
              "macOS would treat it as never set up.")
        return
    print("Recognised as initialized : "
          f"{'yes' if ident.get('initialized') else 'no'}")
    print(f"Password hint             : {ident.get('hint') or '-'}")
    print(f"Owner                     : {ident.get('name') or '-'}")
    print(f"Company                   : {ident.get('company') or '-'}")
    print(f"Details                   : {ident.get('details') or '-'}")


def main():
    ap = argparse.ArgumentParser(
        prog="ironkey",
        description=f"{APP_NAME} {VERSION} — command line interface",
        epilog=f"Documentation and issues: {GITHUB_URL}",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # Shared options, attached to every subcommand as well as the top
    # level, so both "ironkey --json status" and the far more natural
    # "ironkey status --json" work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="print the raw JSON reply")
    common.add_argument("--password-stdin", action="store_true",
                        help="read the drive password from standard input "
                             "instead of prompting")
    ap.add_argument("--json", action="store_true",
                    help=argparse.SUPPRESS)
    ap.add_argument("--password-stdin", action="store_true",
                    help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")

    simple = {
        "status": "what the drive is doing",
        "info": "hardware and filesystem details",
        "mount": "mount the data area",
        "umount": "unmount the data area",
        "lock": "unmount and re-encrypt the drive",
        "diagnostics": "low-level firmware query (read-only)",
        "attempts": "how many password tries are left before erasure",
        "filesystems": "what this machine can format",
        "version": "show the version",
    }
    for name, helptext in simple.items():
        sub.add_parser(name, help=helptext, parents=[common])

    sub.add_parser("unlock", help="unlock the drive and mount it",
                   parents=[common])
    p_init = sub.add_parser("init",
                            help="set the first password on a NEW drive",
                            parents=[common])
    p_init.add_argument("--hint", default="",
                        help="password reminder written onto the drive and "
                             "shown at login, here and on Windows and macOS")
    p_init.add_argument("--owner", default="", help="owner name")
    p_init.add_argument("--company", default="", help="company")
    p_init.add_argument("--details", default="", help="contact details")

    sub.add_parser("passwd", help="change the drive password, keeping data",
                   parents=[common])

    sub.add_parser("identity",
                   help="show the identity other systems read from the drive",
                   parents=[common])
    sub.add_parser("update", help="check GitHub for a newer version",
                   parents=[common])

    p_fmt = sub.add_parser("format", help="create a filesystem (DESTRUCTIVE)",
                           parents=[common])
    p_fmt.add_argument("--fs", default="exfat",
                       help="exfat (default), fat32, ntfs, ext4, btrfs")
    p_fmt.add_argument("--label", default="IRONKEY", help="volume name")
    p_fmt.add_argument("--yes", action="store_true",
                       help="skip the confirmation prompt")

    p_fsck = sub.add_parser("fsck", help="check the filesystem",
                            parents=[common])
    p_fsck.add_argument("--repair", action="store_true",
                        help="fix problems instead of only reporting them")

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        return 0

    if args.command == "version":
        print(f"{APP_NAME} {VERSION}")
        print(GITHUB_URL)
        return 0

    if args.command == "update":
        from ironkey_update import check_latest, install_kind
        res = check_latest()
        if args.json:
            print(json.dumps(res, indent=2))
            return 0
        print(f"Installed : {VERSION}  ({install_kind()})")
        if res.get("error"):
            print(f"Check     : {res['error']}")
            return 1
        print(f"Latest    : {res['version']}")
        if res["available"]:
            print(f"\nA newer version is available: {res['url']}")
            print("From a git checkout, update with:  git pull")
        else:
            print("\nYou are up to date.")
        return 0

    # Map CLI names to helper commands.
    command = {"filesystems": "fstypes"}.get(args.command, args.command)
    extra, password = [], None

    if args.command == "format":
        if not args.yes:
            print(f"This ERASES everything on the drive and creates a new "
                  f"{args.fs} filesystem.")
            if input("Type ERASE to continue: ").strip() != "ERASE":
                print("Cancelled.")
                return 1
        extra = [args.fs, args.label]

    if args.command == "fsck":
        extra = ["repair" if args.repair else "no"]

    if args.command == "init":
        extra = [args.hint, args.owner, args.company, args.details]

    if args.command in NEEDS_TWO_PASSWORDS:
        command = "changepw"
        if args.password_stdin:
            current = sys.stdin.readline().rstrip("\n")
            replacement = sys.stdin.readline().rstrip("\n")
        else:
            print("Changing the drive password. The files on the drive are "
                  "not touched.")
            print("A wrong current password counts towards the ten that "
                  "erase the drive.\n")
            current = ask_password("Current drive password: ")
            replacement = ask_password("New password: ", confirm=True)
        password = current + "\n" + replacement

    if args.command in NEEDS_PASSWORD:
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        elif args.command == "init":
            print("Setting the FIRST password on a new drive.")
            print("6-16 characters, at least three of: uppercase, "
                  "lowercase, digits, symbols.")
            print("Write it down: there is no recovery.\n")
            password = ask_password("New drive password: ", confirm=True)
        else:
            print("Enter the DRIVE password (the one stored on the IronKey),")
            print("not your computer password.")
            print("Warning: ten consecutive wrong attempts erase the data.")
            password = ask_password("Drive password: ")

    res = call_backend(command, extra, password)

    if args.json:
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    if not res.get("ok"):
        print(f"Error: {res.get('message','unknown error')}", file=sys.stderr)
        return 1

    presenters = {
        "status": show_status,
        "info": show_info,
        "filesystems": show_filesystems,
        "diagnostics": show_diagnostics,
        "identity": show_identity,
        "attempts": show_attempts,
    }
    if args.command in presenters:
        presenters[args.command](res)
    else:
        print(res.get("message", "Done."))
        if res.get("output"):
            print(res["output"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
