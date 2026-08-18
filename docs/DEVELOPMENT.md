# Development

## Layout

```
src/           the application; everything ships from here
packaging/     .deb builder, PKGBUILD, RPM spec, polkit policy, helper
docs/          this documentation
install.sh     install from a working copy (user or system-wide)
sync.sh        keep source, installed package and GitHub in step
publish.sh     commit, push, tag and cut a GitHub release
```

## Two copies, and why they drift

On a development machine the application exists twice: the **source** you
edit, and the **package** you actually run. Editing a file changes only the
first. `sync.sh` puts them back in step.

```bash
./sync.sh --status     # what matches what
./sync.sh              # build the .deb and install it
./sync.sh --publish    # …and commit, push and cut a release
```

## Always bump the version

`apt` decides by **version number, not by content**. Rebuilding the same
version with different code installs nothing — the package manager sees a
version already present and stops. Before rebuilding:

1. Raise `VERSION` in `src/ironkey_about.py`
2. Add an entry at the top of `CHANGELOG` in the same file
3. Run `./sync.sh`

`CHANGELOG.md` is generated from that list, so it never has to be edited by
hand. `sync.sh` warns when the version has not moved.

## Building packages by hand

```bash
./packaging/build-deb.sh          # -> dist/ironkey-lockerplus_<ver>_all.deb
```

Arch and RPM recipes live in `packaging/PKGBUILD` and
`packaging/ironkey-lockerplus.spec`. They are written but not built here —
test them on their own distributions before claiming they work.

## Publishing

```bash
./publish.sh --dry-run    # show what would happen, change nothing
./publish.sh --release    # push, tag and publish with the .deb attached
```

It refuses to publish if any vendor material (`.iso`, `.exe`, `.dylib`)
would be included. That check exists because a file pushed to a public
repository stays in its history even after deletion.

## Checks before a release

```bash
python3 -m pyflakes src/*.py      # ironkey_unlock.py is third-party; leave it
for f in src/*.py; do python3 -m py_compile "$f"; done
```

Two habits worth keeping, both learned from real mistakes here:

**An empty log is not proof of success.** Launching the GUI in the
background and reading its log a second later shows nothing whether it
worked or crashed. Wait long enough for a traceback, and confirm the window
actually exists.

**Verify handlers and attributes exist.** Patching a class in pieces can
silently drop a method that a button still connects to, leaving a window
that builds halfway and renders black. A quick scan catches it:

```bash
python3 - <<'PY'
import re
s = open('src/ironkey_gui.py').read()
connected = set(re.findall(r'connect\("[\w-]+",\s*self\.(\w+)', s))
defined = set(re.findall(r'    def (\w+)\(self', s))
print("missing handlers:", sorted(connected - defined) or "none")
PY
```

## Adding a feature

The helper owns everything privileged; the GUI only asks. To add an
operation, write `cmd_yourthing()` in `src/ironkey_backend.py` ending in
`emit(ok, message, …)`, register it in `COMMANDS` (and in `UNPRIVILEGED` if
it needs no root), then call it from the GUI with `self.run("yourthing")`.
Adding it to `src/ironkey_cli.py` is usually a one-line entry.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together.
