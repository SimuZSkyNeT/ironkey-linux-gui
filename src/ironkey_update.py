#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Update checking and developer publishing — part of IronKey Locker+ for Linux
# Copyright (C) 2026 SimuZSkyNeT
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
"""
Check GitHub for newer releases, and — when running from a git checkout —
commit and push your own changes back.

On updating automatically
-------------------------
Pulling code from the internet and running it is remote code execution by
design. So nothing here happens on its own: the check is opt-in, and an
update is only ever applied after you confirm it. Installations from a
distribution package are never touched — those belong to the package
manager, and silently overwriting them would be wrong.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request

from ironkey_about import GITHUB_REPO, GITHUB_USER, VERSION

API = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
RELEASES_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases"
TIMEOUT = 10


def parse_version(text):
    """'v1.6.0' or '1.6.0' -> (1, 6, 0). Unparseable pieces become 0."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def newer(remote, local):
    return parse_version(remote) > parse_version(local)


def source_dir():
    return os.path.dirname(os.path.abspath(__file__))


def git_root():
    """The git checkout this code lives in, or None if installed as a package."""
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=source_dir(), capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def install_kind():
    """How this copy was installed: 'git', 'package' or 'unknown'."""
    if git_root():
        return "git"
    if source_dir().startswith(("/usr/share/", "/usr/local/share/", "/opt/")):
        return "package"
    return "unknown"


# ----------------------------------------------------------------- check
def check_latest():
    """Ask GitHub for the newest release.

    Returns a dict: available, version, url, notes, error.
    Never raises — the caller is a GUI and must always get an answer.
    """
    req = urllib.request.Request(
        f"{API}/releases/latest",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"{GITHUB_REPO}/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"available": False, "version": None,
                    "error": "No releases have been published yet."}
        return {"available": False, "version": None,
                "error": f"GitHub returned HTTP {e.code}."}
    except Exception as e:
        return {"available": False, "version": None,
                "error": f"Could not reach GitHub: {e}"}

    tag = data.get("tag_name") or ""
    return {
        "available": newer(tag, VERSION),
        "version": tag.lstrip("v"),
        "url": data.get("html_url") or RELEASES_URL,
        "notes": (data.get("body") or "").strip(),
        "published": (data.get("published_at") or "")[:10],
        "error": None,
    }


# ---------------------------------------------------------------- update
def update_from_git():
    """Pull the newest code into this checkout. Returns (ok, message)."""
    root = git_root()
    if not root:
        return False, ("This copy was not installed from git, so it cannot "
                       "update itself. Use your package manager, or "
                       "download the new release.")

    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                           capture_output=True, text=True)
    if dirty.stdout.strip():
        return False, ("You have uncommitted changes. Commit or stash them "
                       "before updating, so nothing of yours is lost.")

    r = subprocess.run(["git", "pull", "--ff-only"], cwd=root,
                       capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return False, f"Update failed:\n{out}"
    if "Already up to date" in out:
        return True, "Already up to date."
    return True, f"Updated. Restart the application to use the new version.\n\n{out}"


# --------------------------------------------------------------- publish
def repo_status():
    """What is uncommitted here, for the developer view."""
    root = git_root()
    if not root:
        return None
    r = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                       capture_output=True, text=True)
    changes = [ln for ln in r.stdout.splitlines() if ln.strip()]
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=root, capture_output=True, text=True
                            ).stdout.strip()
    ahead = subprocess.run(
        ["git", "rev-list", "--count", "@{upstream}..HEAD"], cwd=root,
        capture_output=True, text=True).stdout.strip() or "0"
    return {"root": root, "branch": branch, "changes": changes,
            "unpushed": ahead}


def publish(message):
    """Commit everything and push. Returns (ok, output)."""
    root = git_root()
    if not root:
        return False, "Not a git checkout: nothing to publish."
    if not message.strip():
        return False, "A commit message is required."

    steps = [
        (["git", "add", "-A"], "staging"),
        (["git", "commit", "-m", message], "committing"),
        (["git", "push"], "pushing"),
    ]
    log = []
    for argv, what in steps:
        r = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                           timeout=180)
        out = (r.stdout + r.stderr).strip()
        log.append(f"$ {' '.join(argv)}\n{out}")
        if r.returncode != 0:
            if what == "committing" and "nothing to commit" in out:
                continue          # nothing staged is not an error
            return False, "\n\n".join(log)
    return True, "\n\n".join(log)


if __name__ == "__main__":
    print("installation :", install_kind())
    print("git root     :", git_root() or "—")
    print("local version:", VERSION)
    res = check_latest()
    if res.get("error"):
        print("check        :", res["error"])
    else:
        print("latest       :", res["version"],
              "(newer)" if res["available"] else "(up to date)")
    st = repo_status()
    if st:
        print("branch       :", st["branch"],
              f"| {len(st['changes'])} changed, {st['unpushed']} unpushed")
