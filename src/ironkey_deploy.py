#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Deploy a verifiable copy of the application onto the drive
# Copyright (C) 2026 SimuZSkyNeT
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
"""
Put a copy of this application on the drive itself, so it travels with it.

The drive's CD-ROM partition cannot be written — the firmware does not
implement the commands, which is a deliberate barrier: a writable
"official software" partition would be an ideal place to hide malware. So
the copy goes on the data partition instead.

That same reasoning applies to us, which is why nothing here is copied
blindly:

  BEFORE copying, the source is verified.
      Installed from a .deb : checked against the package manager's own
                              recorded checksums (dpkg -V).
      Running from git      : checked against the repository (git status).
      Neither               : we say so plainly rather than pretend.

  AFTER copying, a manifest is written.
      MANIFEST.sha256 lists a SHA-256 for every file, plus the version and
      the upstream URL, so the copy can be re-checked at any time — and
      compared against the published release by anyone who cares to.

  verify.sh accompanies the copy so it can be checked without this app.
"""

import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ironkey_about import APP_NAME, GITHUB_URL, VERSION  # noqa: E402

FOLDER = "IronKey-Linux-App"
MANIFEST = "MANIFEST.sha256"
PACKAGE = "ironkey-lockerplus"

# What gets copied. Only our own source; nothing generated, nothing local.
WANTED = (
    "ironkey_gui.py", "ironkey_backend.py", "ironkey_files.py",
    "ironkey_vault.py", "ironkey_about.py", "ironkey_update.py",
    "ironkey_cli.py", "ironkey_deploy.py", "ironkey_unlock.py",
)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------- source
def source_files():
    out = []
    for name in WANTED:
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            out.append(p)
    return out


def verify_source():
    """Is the copy we are about to distribute the one that was installed?

    Returns (state, detail) where state is 'verified', 'modified',
    'unverifiable'.
    """
    # Installed as a distribution package: the package manager holds
    # checksums recorded at build time. dpkg -V prints one line per file
    # that no longer matches, and nothing at all when everything is intact.
    if HERE.startswith(("/usr/share/", "/usr/local/share/")):
        if shutil.which("dpkg"):
            try:
                r = subprocess.run(["dpkg", "-V", PACKAGE],
                                   capture_output=True, text=True, timeout=60)
                if r.returncode == 0 and not r.stdout.strip():
                    return "verified", (
                        f"All files match the checksums recorded by the "
                        f"'{PACKAGE}' package.")
                if r.stdout.strip():
                    return "modified", (
                        "These installed files no longer match the "
                        "package:\n" + r.stdout.strip())
                return "unverifiable", (
                    "dpkg could not verify the package "
                    f"(exit {r.returncode}).")
            except Exception as e:
                return "unverifiable", f"dpkg check failed: {e}"
        if shutil.which("rpm"):
            try:
                r = subprocess.run(["rpm", "-V", PACKAGE],
                                   capture_output=True, text=True, timeout=60)
                if not r.stdout.strip():
                    return "verified", (
                        f"All files match the checksums recorded by the "
                        f"'{PACKAGE}' package.")
                return "modified", ("These installed files no longer match "
                                    "the package:\n" + r.stdout.strip())
            except Exception as e:
                return "unverifiable", f"rpm check failed: {e}"
        return "unverifiable", "No package manager available to check against."

    # Running from a git checkout: the repository is the reference.
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--", "."],
                           cwd=HERE, capture_output=True, text=True,
                           timeout=30)
        if r.returncode == 0:
            changes = [ln for ln in r.stdout.splitlines() if ln.strip()]
            if not changes:
                rev = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                    capture_output=True, text=True).stdout.strip()
                return "verified", (
                    f"Working copy matches the repository at commit {rev}.")
            return "modified", ("Uncommitted local changes:\n"
                                + "\n".join(changes[:10]))
    except Exception:
        pass

    return "unverifiable", (
        "This copy was not installed from a package and is not a git "
        "checkout, so there is no reference to compare it against.")


# ---------------------------------------------------------------- write
def build_manifest(files, base):
    lines = [
        f"# {APP_NAME} {VERSION}",
        f"# {GITHUB_URL}",
        "#",
        "# SHA-256 of every file in this folder. To check them:",
        "#     sha256sum -c MANIFEST.sha256",
        "# or run ./verify.sh",
        "#",
        "# To confirm this really is the published release, compare these",
        "# hashes against the source of the matching tag on GitHub.",
        "",
    ]
    for path in sorted(files):
        lines.append(f"{sha256(path)}  {os.path.relpath(path, base)}")
    return "\n".join(lines) + "\n"


VERIFY_SH = """#!/bin/sh
# Check this copy against its manifest.
# Any line reporting FAILED means the file changed after it was written.
cd "$(dirname "$0")" || exit 1
if ! command -v sha256sum >/dev/null 2>&1; then
    echo "sha256sum is not available on this system." >&2
    exit 1
fi
grep -v '^#' MANIFEST.sha256 | grep -v '^$' | sha256sum -c -
"""

README_TXT = """{app} {version}
{url}

WHAT THIS IS
A copy of the Linux application for this encrypted drive, kept on the
drive itself so it travels with it.

It does not start on its own. The drive's firmware does not allow writing
to its CD-ROM partition — the only area that could auto-run — so the
application has to be installed on the computer that will use it.

INSTALL
    ./install.sh              (from a clone of the repository)
or on Debian, Ubuntu, Mint, elementary:
    sudo apt install ./ironkey-lockerplus_*.deb

Then run: ironkey-gui        (graphical)
          ironkey --help     (terminal)

CHECK THIS COPY IS INTACT
    ./verify.sh

That compares every file against MANIFEST.sha256. To be sure the files are
genuinely the published release and not a substitute, compare the hashes
in MANIFEST.sha256 against the matching tag at:
    {url}

WARNING
Ten consecutive wrong drive passwords erase the data permanently. That is
a hardware feature of the drive; no software can undo it.
"""


def deploy(mountpoint):
    """Copy the application to the drive. Returns (ok, message, folder)."""
    if not mountpoint or not os.path.isdir(mountpoint):
        return False, "The drive is not mounted.", None

    files = source_files()
    if not files:
        return False, "Could not find the application files to copy.", None

    dest = os.path.join(mountpoint, FOLDER)
    try:
        os.makedirs(dest, exist_ok=True)
        copied = []
        for src in files:
            target = os.path.join(dest, os.path.basename(src))
            shutil.copy2(src, target)
            copied.append(target)

        # The installer and desktop entry, when we can find them.
        for extra in ("install.sh", "ironkey.desktop", "README.md",
                      "LICENSE"):
            for base in (os.path.dirname(HERE), HERE,
                         "/usr/share/doc/" + PACKAGE):
                cand = os.path.join(base, extra)
                if os.path.isfile(cand):
                    target = os.path.join(dest, extra)
                    shutil.copy2(cand, target)
                    copied.append(target)
                    break

        with open(os.path.join(dest, "README.txt"), "w") as f:
            f.write(README_TXT.format(app=APP_NAME, version=VERSION,
                                      url=GITHUB_URL))
        copied.append(os.path.join(dest, "README.txt"))

        vs = os.path.join(dest, "verify.sh")
        with open(vs, "w") as f:
            f.write(VERIFY_SH)
        os.chmod(vs, 0o755)
        copied.append(vs)

        with open(os.path.join(dest, MANIFEST), "w") as f:
            f.write(build_manifest(copied, dest))

        os.sync() if hasattr(os, "sync") else None
        return True, (f"{len(copied)} files copied to {FOLDER}, with a "
                      f"SHA-256 manifest."), dest
    except OSError as e:
        return False, f"Copy failed: {e}", None


def verify_copy(mountpoint):
    """Re-check a copy already on the drive. Returns (ok, message)."""
    dest = os.path.join(mountpoint or "", FOLDER)
    manifest = os.path.join(dest, MANIFEST)
    if not os.path.isfile(manifest):
        return False, f"No {MANIFEST} found in {FOLDER} on the drive."

    good, bad, missing = 0, [], []
    with open(manifest) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                digest, name = line.split("  ", 1)
            except ValueError:
                continue
            path = os.path.join(dest, name)
            if not os.path.isfile(path):
                missing.append(name)
            elif sha256(path) == digest:
                good += 1
            else:
                bad.append(name)

    if not bad and not missing:
        return True, f"All {good} files match the manifest."
    parts = [f"{good} files match."]
    if bad:
        parts.append("CHANGED since the manifest was written:\n  "
                     + "\n  ".join(bad[:10]))
    if missing:
        parts.append("MISSING:\n  " + "\n  ".join(missing[:10]))
    return False, "\n".join(parts)


if __name__ == "__main__":
    state, detail = verify_source()
    print(f"source: {state}\n{detail}")
    if len(sys.argv) > 1:
        ok, msg, folder = deploy(sys.argv[1])
        print(f"\ndeploy: {'ok' if ok else 'failed'}\n{msg}")
        if ok:
            ok2, msg2 = verify_copy(sys.argv[1])
            print(f"\nverify: {'ok' if ok2 else 'failed'}\n{msg2}")
