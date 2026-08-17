# Architecture

How the pieces fit together, for anyone reading or changing the code.

## Two processes, one privilege boundary

```
┌──────────────────────────────────────────┐
│  ironkey_gui.py          runs as YOU     │
│  GTK window, state machine, dialogs      │
│  ironkey_files.py   file browser         │
│  ironkey_vault.py   encrypted vault      │
│  ironkey_about.py   version, changelog   │
└───────────────┬──────────────────────────┘
                │  pkexec, one JSON line per reply
                │  password on stdin, never in argv
┌───────────────▼──────────────────────────┐
│  ironkey_backend.py      runs as ROOT    │
│  device discovery, mount, format, fsck   │
│  unlock / initialize / lock              │
└───────────────┬──────────────────────────┘
                │
┌───────────────▼──────────────────────────┐
│  ironkey_unlock.py    THIRD PARTY, GPL-2 │
│  RSA-512 handshake, HID transport,       │
│  PID switch, unlock sequence             │
└──────────────────────────────────────────┘
```

The GUI never runs as root. Only the helper does, and only for the
operations that genuinely need it.

## Why the split

Running a graphical application as root exposes the whole GTK stack — and
every library it loads — with full privileges. Keeping the window
unprivileged means a bug in the interface cannot become a system
compromise.

The cost is that privileged work needs a round trip through `pkexec`, which
is why the helper batches related steps: `unlock` mounts in the same call,
`format` mounts afterwards in the same call. One authentication per user
action, not one per internal step.

## Passing secrets

The drive password goes to the helper on **stdin**, never as an argument.
Arguments are visible to every user on the machine through `ps`, and end up
in shell history. Reading one line from stdin costs nothing and closes that
hole.

## The reply contract

Every helper command prints exactly one JSON object:

```json
{"ok": true, "message": "Unlocked: /dev/sda (31.8 GiB)", "state": "unlocked"}
```

The GUI reads the last line starting with `{`. No screen-scraping of free
text, so wording can change without breaking the interface.

Unprivileged commands (`status`, `info`, `fstypes`) skip `pkexec`
entirely — that is what lets the window poll device state every few seconds
without ever prompting.

## The state machine

One function, `render()`, decides everything the window shows. States:

```
ABSENT ──plug──> LOCKED ──unlock──> RAW ──format──> READY ──mount──> MOUNTED
                    ▲                                                    │
                    └────────────────── lock ────────────────────────────┘
```

`RAW` means unlocked with no filesystem — normal right after
initialization, which is why the app explains it rather than showing an
error.

## Mounting via udisks

Mounting goes through `udisksctl`, run as the user, for three reasons: it
needs no authentication for a local session, it sets ownership correctly on
filesystems without POSIX permissions, and it registers the mount with the
desktop so the file manager actually shows the drive. A privileged mount in
the helper remains as a fallback for systems without udisks.

## The vault

`ironkey_vault.py` is standalone — it imports only the standard library and
`cryptography`, and knows nothing about IronKey drives.

```
key  = scrypt(app_password, salt, n=2^16, r=8, p=1)   → 32 bytes
blob = AES-256-GCM(key, nonce, drive_password)
```

No password hash is stored. The GCM tag doubles as the check: a wrong
password simply fails to decrypt. A canary blob lets the app verify the
password even before any drive password has been saved.

scrypt at n=2^16 costs about 64 MB and half a second per attempt —
unnoticeable once, ruinous for a brute-force run.

## Portability

No hardcoded paths. The helper looks for `ironkey_unlock.py` beside itself,
in sibling directories, in `~`, and in the usual system locations. The GUI
finds an interpreter that can import pycryptodome, which may differ from
the one running the GUI itself when a virtualenv is involved.

The mount point follows each distribution's convention:
`/run/media/<user>` on Fedora, Arch and openSUSE; `/media/<user>` on Debian
and Ubuntu; `/mnt` as a last resort.

GTK 4 is used when available, GTK 3 otherwise. A handful of helpers
(`add`, `set_child`, `show`) absorb the API differences so the rest of the
code does not branch.

## Files

| File | Role |
|---|---|
| `ironkey_gui.py` | window, state machine, dialogs, tools |
| `ironkey_backend.py` | privileged operations, JSON replies |
| `ironkey_files.py` | built-in file browser, confined to the drive |
| `ironkey_vault.py` | encrypted store for the drive password |
| `ironkey_about.py` | version and changelog as data |
| `ironkey_unlock.py` | third-party unlock protocol (GPL-2) |

## Adding a command

1. Write `cmd_yourthing()` in the helper, ending in `emit(ok, message, …)`.
2. Register it in `COMMANDS`; add it to `UNPRIVILEGED` if it needs no root.
3. Call it from the GUI with `self.run("yourthing")`.

The GUI stays unaware of how the work is done, which is the point.
