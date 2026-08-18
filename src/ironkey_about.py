#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Version and changelog — part of IronKey Locker+ for Linux
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
Version, changelog and credits for the IronKey GUI.

Kept in its own module so the changelog is data, not something buried in
UI code — and so a release only needs one edit here.
"""

VERSION = "1.9.0"
APP_NAME = "IronKey Locker+"
TAGLINE = "Set up and use a Kingston IronKey Locker+ drive on Linux"

AUTHOR = "SimuZSkyNeT"
GITHUB_USER = "SimuZSkyNeT"
GITHUB_REPO = "ironkey-linux-gui"
GITHUB_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"

# Donations. EVM address — works on Ethereum mainnet and every EVM chain
# (Arbitrum, Optimism, Base, Polygon, BSC, Avalanche C-Chain, and so on).
# Checksum verified against EIP-55.
DONATION_ADDRESS = "0x74E71BB8849FF0e17FA73Fc61DA107032D117dF6"
DONATION_NOTE = ("ETH or any token on any EVM-compatible chain. "
                 "Entirely optional — the software is free either way.")

# Newest first. Each entry: (version, date, [changes])
CHANGELOG = [
    ("1.9.0", "2026-08-18", [
        "Copy app to drive: keeps a copy of this application on the drive "
        "itself, so it travels with it.",
        "The installation is verified before anything is copied — against "
        "the package manager's own checksums on a packaged install, or "
        "against the repository on a git checkout. If it cannot be "
        "verified, the app says so instead of pretending.",
        "Every copy carries a SHA-256 manifest and a verify.sh, so it can "
        "be checked at any time and compared against the published "
        "release. \"Verify copy on drive\" re-checks it from the menu.",
    ]),
    ("1.8.1", "2026-08-18", [
        "The polkit dialog is English-only, as the rest of the "
        "application already was.",
        "The privileged helper now looks for the Cryptodome module first, "
        "which is how Debian and Ubuntu package pycryptodome.",
    ]),
    ("1.8.0", "2026-08-18", [
        "One authentication now covers a whole session: a polkit action "
        "with auth_admin_keep means unlock, use and lock no longer ask "
        "for the computer password each time (system-wide installs).",
        "The system dialog now says which password it wants, instead of "
        "showing a generic message naming a Python path.",
        "Clearer wording in the terminal too.",
    ]),
    ("1.7.0", "2026-08-18", [
        "Command-line interface: everything the app does to the drive is "
        "now available as `ironkey status|info|unlock|mount|lock|format|"
        "init|fsck|diagnostics`, so it works over SSH and in scripts. "
        "Add --json to any command for machine-readable output.",
        "Update checking against GitHub releases; a git checkout can "
        "update itself, a packaged install is left to the package manager.",
        "Publish changes: commit and push your own work straight from the "
        "app when running from a git checkout.",
        "Author and donation details in the About window.",
    ]),
    ("1.6.0", "2026-08-18", [
        "Built-in file browser: navigate the drive, import files onto it, "
        "export a selection out, create folders and delete items — all "
        "without leaving the app and without any password prompt.",
        "Navigation is confined to the drive: it cannot walk out into the "
        "rest of the filesystem.",
    ]),
    ("1.5.0", "2026-08-18", [
        "Speed test: real write and read throughput, measured with fsync "
        "so the page cache cannot flatter the result.",
        "Integrity check: writes 32 MB, reads it back, compares SHA-256.",
        "Filesystem check, with optional repair, for exFAT, FAT, NTFS, "
        "ext and Btrfs.",
        "Firmware diagnostics: private-area layout, domain map and vendor "
        "query services, read straight from the controller.",
        "Save report: one text file with everything, for keeping or "
        "sending on.",
    ]),
    ("1.4.0", "2026-08-18", [
        "Drive details window: space used, capacity, filesystem, serial "
        "number, USB link speed, firmware revision and write-protect "
        "state.",
        "Fixed: the offer to remember the drive password could never be "
        "accepted when the vault was set up but not yet unlocked.",
        "Fixed: a tampered vault now says so, instead of quietly acting "
        "as though nothing had been saved.",
        "Fixed: background refreshes could stack up on a slow backend.",
    ]),
    ("1.3.0", "2026-08-18", [
        "Optional encrypted vault: remember the drive password behind an "
        "application password (scrypt + AES-256-GCM).",
        "Light / dark / follow-system appearance.",
        "This changelog, plus an About dialog.",
        "Automatic refresh when the drive is plugged in or removed.",
        "Keyboard shortcuts and clearer wording on password prompts.",
    ]),
    ("1.2.0", "2026-08-18", [
        "Lock button: re-encrypt the drive without unplugging it "
        "(FF A5 / NTU_Close, recovered from the vendor binary).",
        "Mounting now goes through udisks: no password needed, and the "
        "file manager finally sees the drive.",
        "Fixed mount permissions on exFAT — the drive mounted read-only "
        "for the user because uid/gid were not passed.",
    ]),
    ("1.1.0", "2026-08-18", [
        "Choice of filesystem when formatting: exFAT, FAT32, NTFS, ext4, "
        "Btrfs, with availability detection and a custom volume label.",
        "Unlock and format now mount in the same privileged step, so the "
        "system password is asked once instead of twice.",
        "Unmount button no longer disappears between states.",
    ]),
    ("1.0.0", "2026-08-17", [
        "First release: status, unlock, mount, unmount, format.",
        "First-password initialization — the part no public tool could do, "
        "reconstructed from the vendor's macOS binary.",
        "Unprivileged GUI with a privileged helper; the drive password "
        "travels on stdin and never appears in the process list.",
    ]),
]

CREDITS = {
    "Made by": [
        f"{AUTHOR} — {GITHUB_URL}",
        "Initialization protocol, application, packaging and documentation.",
    ],
    "Protocol": [
        "Unlock protocol and transport: wltechblog/ironkey-unlocker (GPL-2)",
        "Cross-checked against DavidCarliez/ironkey-vp50 and "
        "meikster/ironkey-linux",
        "Initialization protocol: reconstructed from the vendor's own "
        "macOS application (symbol table intact)",
    ],
    "Warning": [
        "Not affiliated with or endorsed by Kingston Technology.",
        "Ten consecutive wrong drive passwords erase the data permanently.",
    ],
}


def changelog_text():
    out = []
    for version, date, items in CHANGELOG:
        out.append(f"Version {version} — {date}")
        for it in items:
            out.append(f"  • {it}")
        out.append("")
    return "\n".join(out).rstrip()


def about_text():
    lines = [f"{APP_NAME} {VERSION}", TAGLINE, ""]
    for section, items in CREDITS.items():
        lines.append(section)
        for it in items:
            lines.append(f"  • {it}")
        lines.append("")
    lines += [
        "Support the project",
        f"  {DONATION_ADDRESS}",
        f"  {DONATION_NOTE}",
        "",
        "Licence",
        "  GPL-2.0-only. Source and issues: " + GITHUB_URL,
    ]
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    print(about_text())
    print()
    print(changelog_text())
