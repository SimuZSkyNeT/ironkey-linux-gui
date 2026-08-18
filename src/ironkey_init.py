#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 SimuZ
"""Setting the first password on a factory-fresh drive.

The vendor's software calls this "initialization". On this model it is one
command: the private area is already allocated at the factory, so nothing is
resized or created — a key is configured for it (FF A2), which is what turns
the password into the key that protects the data.

The transport and the secure session are the ones ironkey_unlock.py already
implements; the only difference from unlocking is that FF A2 is sent instead
of the FF A4 login, carrying the encrypted password plus a small plaintext
header read back from the drive.

    RSA handshake (FF 81-88)   ->  session key
    FF 8B  start secure access
    FF 8F  open session
    FF A2  configure private area key   <- the initialization
    FF 62  device identity record       <- what makes other systems agree
    FF 89  commit

The identity record matters as much as the password: without it the vendor's
Windows and macOS application does not consider the drive initialized, offers
its setup wizard, and would overwrite the password just set. See
ironkey_metadata.py for the record itself.

Risk: on a drive with nothing on it the worst case is a return to factory
state, which is where such a drive already is. Initialization does not touch
the ten-attempt counter — only a failed login does.

    sudo python3 ironkey_init.py                      # dry run
    sudo python3 ironkey_init.py --commit --hint "…"  # really do it
"""
import argparse
import fcntl
import getpass
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the record module first: it works out where the unlocker lives and
# installs the safe device release in place of the one that ejects the
# machine's optical drive.
import ironkey_metadata as meta  # noqa: E402
import ironkey_unlock  # noqa: E402
from ironkey_unlock import (  # noqa: E402
    PVC0_KEY,
    IronKeyError,
    aes128_ecb_encrypt,
    encrypt_pvc_key,
    find_ironkey_hidraw,
    find_ironkey_locked,
    log,
    rsa_handshake,
    send_hid,
    trigger_pid_switch,
)

meta.install_safe_unmount()

DOMAIN = 0


def read_private_area(fd, domain=DOMAIN):
    """Read the private area descriptor. Read-only, nothing is changed."""
    cdb3_10 = bytes([domain]) + bytes(7)
    data = send_hid(fd, 0xA0, 0x00, "read", data_len=0x10, cdb3_10=cdb3_10)
    if not data or len(data) < 16:
        raise IronKeyError("FF A0: reply too short")
    d0, d1, d2, d3 = struct.unpack_from("<4I", data, 0)
    return {"raw": data[:16], "dword0": d0, "dword1": d1,
            "dword2": d2, "dword3": d3}


# Dieci tentativi falliti di fila cancellano la chiave. Il conto dei tentativi
# gia' spesi sta nel quarto dword della struttura dell'area privata, e si
# azzera al primo accesso riuscito.
MAX_ATTEMPTS = 10


def attempts_from_area(area):
    """(spesi, rimasti) a partire dalla struttura letta con FF A0."""
    used = area["dword3"] & 0xFFFF
    if used > MAX_ATTEMPTS:          # valore inatteso: meglio non allarmare
        return None, None
    return used, MAX_ATTEMPTS - used


def build_a2_payload(header_dword, encrypted_pw):
    """Four plaintext header bytes followed by the encrypted password."""
    return struct.pack("<I", header_dword) + encrypted_pw


def send_init(fd, shared, password, area, user_id_count=1, dry_run=True,
              identity=None):
    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) > 16:
        raise IronKeyError(
            "Password too long: 16 bytes maximum in Complex mode. "
            "Passphrase mode is not supported here.")
    plaintext = pw_bytes.ljust(16, b"\x00")
    encrypted_pw = aes128_ecb_encrypt(shared, plaintext)

    header = area["dword0"]
    payload = build_a2_payload(header, encrypted_pw)

    # cdb[3] = domain, cdb[6] = 0x41 (change key), cdb[7] = 0xA2
    cdb3_10 = bytes([DOMAIN, 0x00, 0x00, 0x41, 0xA2, 0x00, 0x00, 0x00])

    log()
    log("=== FF A2 — configure private area key ===")
    log(f"  domain          : {DOMAIN}")
    log(f"  user id count   : {user_id_count}")
    log(f"  header (plain)  : 0x{header:08x}")
    log(f"  encrypted pw    : {encrypted_pw.hex(' ')}")
    log(f"  payload         : {len(payload)} bytes")
    log()

    if dry_run:
        log("Dry run: nothing was sent. Add --commit to really do it.")
        return False

    log("--- opening the secure session (FF 8B / FF 8F) ---")
    send_hid(fd, 0x8B, 0x00, "none",
             cdb3_10=bytes([0x00, 0x03, 0, 0, 0, 0, 0, 0]))
    send_hid(fd, 0x8F, 0x00, "write", data=encrypt_pvc_key(PVC0_KEY, shared),
             cdb3_10=bytes([0x02, 0, 0, 0, 0, 0, 0, 0]))

    log("--- sending FF A2 ---")
    send_hid(fd, 0xA2, 0x00, "write", data=payload, data_len=len(payload),
             cdb3_10=cdb3_10)

    if identity is not None:
        # Written in the same session, before the commit: this is what makes
        # the vendor's own application on Windows and macOS see the drive as
        # already initialized instead of offering to set it up again.
        log("--- device identity record (FF 62, pages 0x0D and 0x0E) ---")
        meta.write_record(fd, **identity)

    log("--- commit (FF 89) ---")
    send_hid(fd, 0x89, 0x00, "none")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Set the first password on a factory-fresh IronKey "
                    "Locker+ 50 G2")
    parser.add_argument("-p", "--password", help="password (asked otherwise)")
    parser.add_argument("--commit", action="store_true",
                        help="really send the commands (default: dry run)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt")
    parser.add_argument("--hint", default="",
                        help="password reminder, shown at login by this "
                             "application and by the vendor's own")
    parser.add_argument("--name", default="", help="owner name")
    parser.add_argument("--company", default="", help="company")
    parser.add_argument("--details", default="", help="contact details")
    parser.add_argument("--no-identity", action="store_true",
                        help="skip the identity record: the drive will work "
                             "on Linux, but the vendor's application will "
                             "treat it as never initialized")
    args = parser.parse_args()

    log("IronKey Locker+ 50 G2 — initialization")
    log("=" * 40)
    if not args.commit:
        log("DRY RUN: nothing will be written. Add --commit to proceed.")
    log()

    if os.geteuid() != 0:
        log("Root privileges are required.")
        return 1

    hidraw_path, _ = find_ironkey_hidraw()
    if not hidraw_path:
        dev = find_ironkey_locked()
        if not dev:
            log("No IronKey device found.")
            return 1
        log(f"Found {dev['product']}, switching to HID mode...")
        trigger_pid_switch(dev)
        hidraw_path, _ = find_ironkey_hidraw()
        if not hidraw_path:
            log("Switched, but no HID interface appeared. Replug the drive.")
            return 1
    log(f"HID interface: {hidraw_path}")

    password = args.password or getpass.getpass("New IronKey password: ")
    if not password:
        log("No password given.")
        return 1

    fd = os.open(hidraw_path, os.O_RDWR | os.O_NONBLOCK)
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

    identity = None if args.no_identity else {
        "hint": args.hint, "name": args.name,
        "company": args.company, "details": args.details,
    }

    try:
        log("RSA handshake (no password is sent at this stage)...")
        shared = rsa_handshake(fd)

        area = read_private_area(fd)
        log(f"private area: dword0=0x{area['dword0']:08x} "
            f"dword1=0x{area['dword1']:08x} dword2=0x{area['dword2']:08x}")
        if area["dword0"] == 0 or area["dword1"] == 0:
            log("The private area is not allocated as expected; stopping.")
            return 1
        if area["dword2"] != 0:
            log("Unexpected state (dword2 is not zero); stopping.")
            return 1

        if args.commit and not args.yes:
            log()
            log("This will set the first password on the drive.")
            answer = input("Type INITIALIZE to proceed: ")
            if answer.strip() != "INITIALIZE":
                log("Cancelled.")
                return 1

        sent = send_init(fd, shared, password, area,
                         dry_run=not args.commit, identity=identity)
    except IronKeyError as e:
        log(f"Failed: {e}")
        return 1
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if sent:
        log()
        log("Done. The drive now accepts this password, and its data area "
            "still needs a filesystem.")
        if identity is not None:
            log("The identity record was written: the vendor's application "
                "on Windows and macOS will ask for this password too, "
                "instead of offering to set the drive up again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
