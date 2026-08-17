# Troubleshooting

Start by opening **Activity log** at the bottom of the main window: every
command and its result is recorded there.

## The application will not start

Run it from a terminal so you can see the error:

```bash
python3 /usr/share/ironkey-lockerplus/ironkey_gui.py     # package install
python3 ~/ironkey-linux-gui/src/ironkey_gui.py           # from a clone
```

**`ModuleNotFoundError: No module named 'gi'`** — PyGObject is missing:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0     # Debian/Ubuntu/elementary
sudo dnf install python3-gobject gtk3          # Fedora
sudo pacman -S python-gobject gtk3             # Arch
```

**Nothing happens at all** — the app is single-instance. A window is
probably already open, possibly on another workspace. A second launch
raises the existing window rather than opening a new one. Check with:

```bash
pgrep -af ironkey_gui.py
```

## The drive is not detected

```bash
lsusb | grep 0951
```

- **No output** — the drive is not connected, or the cable/port is faulty.
  Try another port, preferably directly on the machine rather than a hub.
- **`0951:159d`** — connected and **locked**. Normal; press Unlock.
- **A different product ID** — connected and already unlocked.

If `lsusb` sees it but the app does not, check that the helper can reach
the device: it needs root, which arrives through `pkexec`.

## "Neither pkexec nor sudo is available"

Install polkit:

```bash
sudo apt install policykit-1     # Debian/Ubuntu
sudo dnf install polkit          # Fedora
sudo pacman -S polkit            # Arch
```

## Unlock fails

**"Unlock failed"** — almost always a mistyped password.

> Every failed attempt consumes one of the ten. After ten consecutive
> failures the drive erases itself. If you are unsure of the password,
> stop and think rather than guessing repeatedly.

Watch for a keyboard layout mismatch — symbols move between layouts, and a
password typed on a different layout will not match. Use **Show password**
in the dialog to check what you actually typed.

## It says unlocked, but the file manager shows nothing

Check where it is actually mounted:

```bash
findmnt /dev/sda
```

- **No output** — unlocked but not mounted. Press **Mount**.
- **A path** — mounted. Open exactly that path; an older window may be
  pointing at a stale one. The mount point follows the volume label, so it
  changes when you rename the volume.

## Mounted, but I cannot write to it

This was a bug in versions before 1.2.0: exFAT has no POSIX ownership, so
permissions must be passed as mount options. Without them the mount ends up
owned by root. Fixed by mounting through udisks. If you still see it:

```bash
mount | grep sda        # look for uid= and gid=
```

Unmount and mount again from the app. If udisks is missing, install it —
`sudo apt install udisks2` — since it also removes the password prompt for
mounting.

## Format fails

- **"Device is locked"** — unlock first.
- **"… is not available"** — the mkfs tool for that filesystem is not
  installed; the message names the package.
- **"Device or resource busy"** — something still has the drive open. Close
  file manager windows and terminals sitting in that directory, then:

```bash
lsof +f -- /dev/sda      # shows what is holding it
```

## Lock does not work

**"the drive is still in use"** — close anything using the drive first.

**"Lock command sent but the data area is still visible"** — the command
was not honoured. Unplugging the drive always locks it; that is the
guaranteed fallback.

## Firmware diagnostics show nothing

The diagnostics need the drive connected and the helper authenticated. If
the session fails, the report says so instead of showing empty values.
These are read-only queries and never touch the attempt counter.

## Reporting a problem

Use **☰ → Save report**, which collects device state and configuration into
one file. Check it before sending: it contains your drive's serial number,
though never any password.

Also useful:

```bash
lsusb | grep 0951
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
dmesg | tail -30
```
