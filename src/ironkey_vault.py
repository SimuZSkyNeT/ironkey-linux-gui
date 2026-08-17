#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Encrypted credential vault — part of IronKey Locker+ for Linux
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
Encrypted credential vault for the IronKey GUI.

WHAT THIS IS FOR — read this before trusting it
-----------------------------------------------
An application password does NOT protect the drive. Anyone with access to
your computer can unlock the drive with the command-line tool or with the
vendor's own app; this GUI is not a gatekeeper, and pretending otherwise
would be security theatre.

What it genuinely protects is the *stored drive password*. If you ask the
app to remember it (so you don't retype it every time), that secret has to
live somewhere — and here it lives encrypted, unlocked only by your
application password. That is real, and that is the only claim made.

Design
------
    key  = scrypt(app_password, salt, n=2^16, r=8, p=1) -> 32 bytes
    blob = AES-256-GCM(key, nonce, secret)      authenticated encryption
    auth = the GCM tag itself proves the password: a wrong password fails
           to decrypt, so no separate password hash is stored.

scrypt is memory-hard, which is what makes brute-forcing the vault file
expensive. AES-GCM is authenticated, so tampering is detected rather than
silently producing garbage.

The vault lives in ~/.config/ironkey/vault.json with mode 0600 and holds
no plaintext. Losing the application password means losing only the
convenience copy — the drive itself is unaffected and still opens with the
device password.
"""

import base64
import hashlib
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULT_VERSION = 1

# scrypt cost. n=2^16 with r=8 needs ~64 MB per attempt: comfortable for a
# single interactive login, punishing for an attacker trying millions.
SCRYPT_N = 1 << 16
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 32          # AES-256
SALT_LEN = 16
NONCE_LEN = 12        # GCM standard


def vault_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ironkey", "vault.json")


def _derive(password, salt):
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_LEN,
        maxmem=256 * 1024 * 1024)


def _b64(b):
    return base64.b64encode(b).decode("ascii")


def _unb64(s):
    return base64.b64decode(s.encode("ascii"))


def exists():
    return os.path.isfile(vault_path())


def read_meta():
    """Non-secret metadata: username and whether a drive password is stored."""
    try:
        with open(vault_path()) as f:
            data = json.load(f)
        return {
            "exists": True,
            "username": data.get("username", ""),
            "has_secret": bool(data.get("secret")),
            "version": data.get("version", 0),
        }
    except (OSError, ValueError):
        return {"exists": False, "username": "", "has_secret": False,
                "version": 0}


def create(username, password, secret=None):
    """Create (or replace) the vault. `secret` is the drive password."""
    if not username or not password:
        raise ValueError("username and password are required")
    if len(password) < 8:
        raise ValueError("the application password must be at least "
                         "8 characters")

    salt = secrets.token_bytes(SALT_LEN)
    key = _derive(password, salt)

    payload = {
        "version": VAULT_VERSION,
        "username": username,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R,
                "p": SCRYPT_P, "salt": _b64(salt)},
        "cipher": "AES-256-GCM",
        "secret": None,
    }

    # A canary lets us verify the password even when no drive password is
    # stored yet: it is a known plaintext, encrypted under the same key.
    nonce = secrets.token_bytes(NONCE_LEN)
    canary = AESGCM(key).encrypt(nonce, b"ironkey-vault-v1", None)
    payload["canary"] = {"nonce": _b64(nonce), "data": _b64(canary)}

    if secret:
        payload["secret"] = _encrypt_secret(key, secret)

    _write(payload)
    return True


def _encrypt_secret(key, secret):
    nonce = secrets.token_bytes(NONCE_LEN)
    data = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), None)
    return {"nonce": _b64(nonce), "data": _b64(data)}


def _write(payload):
    path = vault_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    # Create with 0600 from the start: never briefly world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def unlock(username, password):
    """Verify credentials. Returns (ok, key_or_None, message)."""
    if not exists():
        return False, None, "No vault has been set up yet."
    try:
        with open(vault_path()) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return False, None, f"Vault unreadable: {e}"

    if username != data.get("username"):
        # Same message as a wrong password: never reveal which one was wrong.
        return False, None, "Wrong username or password."

    kdf = data.get("kdf", {})
    try:
        salt = _unb64(kdf["salt"])
        key = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(kdf.get("n", SCRYPT_N)), r=int(kdf.get("r", SCRYPT_R)),
            p=int(kdf.get("p", SCRYPT_P)), dklen=KEY_LEN,
            maxmem=256 * 1024 * 1024)
    except (KeyError, ValueError) as e:
        return False, None, f"Malformed vault: {e}"

    canary = data.get("canary")
    if canary:
        try:
            AESGCM(key).decrypt(_unb64(canary["nonce"]),
                                _unb64(canary["data"]), None)
        except Exception:
            return False, None, "Wrong username or password."
    return True, key, "Unlocked."


class VaultTampered(Exception):
    """The stored secret failed its authentication tag."""


def get_secret(key):
    """Decrypt the stored drive password.

    Returns None when no password is stored. Raises VaultTampered when a
    secret IS present but fails its GCM tag — that means the file was
    altered or corrupted, and the caller must say so rather than silently
    behaving as if nothing had been saved.
    """
    try:
        with open(vault_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    sec = data.get("secret")
    if not sec:
        return None
    try:
        raw = AESGCM(key).decrypt(_unb64(sec["nonce"]), _unb64(sec["data"]),
                                  None)
        return raw.decode("utf-8")
    except Exception as e:
        raise VaultTampered(
            "The saved drive password failed its integrity check: the "
            "vault file has been altered or corrupted. Nothing was "
            "recovered. Your drive is unaffected — unlock it by typing "
            "the device password.") from e


def set_secret(key, secret):
    """Store (or clear, with secret=None) the drive password."""
    with open(vault_path()) as f:
        data = json.load(f)
    data["secret"] = _encrypt_secret(key, secret) if secret else None
    _write(data)
    return True


def change_password(username, old_password, new_password):
    ok, key, msg = unlock(username, old_password)
    if not ok:
        return False, msg
    try:
        secret = get_secret(key)
    except VaultTampered as e:
        return False, str(e)
    create(username, new_password, secret)
    return True, "Application password changed."


def destroy():
    """Remove the vault entirely."""
    try:
        os.remove(vault_path())
        return True
    except OSError:
        return False


if __name__ == "__main__":
    # Self-test: no hardware needed.
    import tempfile
    tmpdir = tempfile.mkdtemp()
    os.environ["XDG_CONFIG_HOME"] = tmpdir
    print("vault:", vault_path())
    create("simuz", "correct horse battery", secret="DriveP4ss!")
    print("meta:", read_meta())
    ok, key, msg = unlock("simuz", "wrong one")
    print("wrong password ->", ok, msg)
    ok, key, msg = unlock("simuz", "correct horse battery")
    print("right password ->", ok, msg)
    print("recovered secret:", get_secret(key))
    print("mode:", oct(os.stat(vault_path()).st_mode & 0o777))
    with open(vault_path()) as f:
        blob = f.read()
    print("plaintext leaked?",
          "YES" if "DriveP4ss!" in blob else "no")
