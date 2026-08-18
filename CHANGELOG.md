# Changelog

All notable changes to IronKey Locker+ for Linux.

The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow [Semantic Versioning](https://semver.org/).

## [1.10.0] — 2026-08-18

- udisks calls no longer hang: they run with a timeout and without interactive prompts, so when udisks refuses to unmount a volume it did not mount, the app falls back instead of freezing.
- One authentication per session: a single privileged helper is started and authenticated once, then driven over a pipe — the same shape as a system daemon. It only accepts a fixed list of commands, never uses a shell, and exits when the app quits.
- Fixed a regression: unlocking mounted the drive from the privileged helper, which bypassed udisks and left the volume invisible to the file manager. Mounting is back on udisks, where it needs no password and the desktop can see it.
- Documentation brought up to date, plus a development guide.

## [1.9.0] — 2026-08-18

- Copy app to drive: keeps a copy of this application on the drive itself, so it travels with it.
- The installation is verified before anything is copied — against the package manager's own checksums on a packaged install, or against the repository on a git checkout. If it cannot be verified, the app says so instead of pretending.
- Every copy carries a SHA-256 manifest and a verify.sh, so it can be checked at any time and compared against the published release. "Verify copy on drive" re-checks it from the menu.

## [1.8.1] — 2026-08-18

- The polkit dialog is English-only, as the rest of the application already was.
- The privileged helper now looks for the Cryptodome module first, which is how Debian and Ubuntu package pycryptodome.

## [1.8.0] — 2026-08-18

- One authentication now covers a whole session: a polkit action with auth_admin_keep means unlock, use and lock no longer ask for the computer password each time (system-wide installs).
- The system dialog now says which password it wants, instead of showing a generic message naming a Python path.
- Clearer wording in the terminal too.

## [1.7.0] — 2026-08-18

- Command-line interface: everything the app does to the drive is now available as `ironkey status|info|unlock|mount|lock|format|init|fsck|diagnostics`, so it works over SSH and in scripts. Add --json to any command for machine-readable output.
- Update checking against GitHub releases; a git checkout can update itself, a packaged install is left to the package manager.
- Publish changes: commit and push your own work straight from the app when running from a git checkout.
- Author and donation details in the About window.

## [1.6.0] — 2026-08-18

- Built-in file browser: navigate the drive, import files onto it, export a selection out, create folders and delete items — all without leaving the app and without any password prompt.
- Navigation is confined to the drive: it cannot walk out into the rest of the filesystem.

## [1.5.0] — 2026-08-18

- Speed test: real write and read throughput, measured with fsync so the page cache cannot flatter the result.
- Integrity check: writes 32 MB, reads it back, compares SHA-256.
- Filesystem check, with optional repair, for exFAT, FAT, NTFS, ext and Btrfs.
- Firmware diagnostics: private-area layout, domain map and vendor query services, read straight from the controller.
- Save report: one text file with everything, for keeping or sending on.

## [1.4.0] — 2026-08-18

- Drive details window: space used, capacity, filesystem, serial number, USB link speed, firmware revision and write-protect state.
- Fixed: the offer to remember the drive password could never be accepted when the vault was set up but not yet unlocked.
- Fixed: a tampered vault now says so, instead of quietly acting as though nothing had been saved.
- Fixed: background refreshes could stack up on a slow backend.

## [1.3.0] — 2026-08-18

- Optional encrypted vault: remember the drive password behind an application password (scrypt + AES-256-GCM).
- Light / dark / follow-system appearance.
- This changelog, plus an About dialog.
- Automatic refresh when the drive is plugged in or removed.
- Keyboard shortcuts and clearer wording on password prompts.

## [1.2.0] — 2026-08-18

- Lock button: re-encrypt the drive without unplugging it (FF A5 / NTU_Close, recovered from the vendor binary).
- Mounting now goes through udisks: no password needed, and the file manager finally sees the drive.
- Fixed mount permissions on exFAT — the drive mounted read-only for the user because uid/gid were not passed.

## [1.1.0] — 2026-08-18

- Choice of filesystem when formatting: exFAT, FAT32, NTFS, ext4, Btrfs, with availability detection and a custom volume label.
- Unlock and format now mount in the same privileged step, so the system password is asked once instead of twice.
- Unmount button no longer disappears between states.

## [1.0.0] — 2026-08-17

- First release: status, unlock, mount, unmount, format.
- First-password initialization — the part no public tool could do, reconstructed from the vendor's macOS binary.
- Unprivileged GUI with a privileged helper; the drive password travels on stdin and never appears in the process list.
