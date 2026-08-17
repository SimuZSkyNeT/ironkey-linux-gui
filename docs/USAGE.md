# User guide

Everything the application does, in the order you are likely to need it.

## The two passwords

This trips up everyone once, so it comes first.

| You see | You type |
|---|---|
| A window from **this app**, titled *Unlock IronKey* or *First password* | The **drive** password — the one stored on the IronKey itself |
| The **grey system dialog** saying *Authentication is required* | Your **computer** login password, the one you use with `sudo` |

They are unrelated. When you unlock a drive you will usually see both, one
after the other: first the app asking for the drive password, then the
system asking permission to talk to the hardware.

> **Ten consecutive wrong drive passwords destroy the data permanently.**
> The drive erases its own encryption key. This is built into the hardware
> and no software can undo it. Wrong *computer* passwords are harmless.

## Window at a glance

The main window always shows one of five states, and the big button always
does the sensible next thing:

| State | Meaning | Button does |
|---|---|---|
| **Not connected** | no IronKey plugged in | — |
| **Locked** | connected, contents encrypted | Unlock |
| **Unlocked — not formatted** | no filesystem yet (normal after setup) | Format |
| **Unlocked** | ready, but not mounted | Mount |
| **Ready** | mounted and usable | Open folder |

The window refreshes itself every few seconds, so plugging or removing the
drive updates it without you doing anything.

## First-time setup of a new drive

A drive fresh from the shop has no password. Setting one is a one-off.

1. Plug it in. The app shows **Locked**.
2. Open **Advanced → Set first password (initialize)…**
3. Confirm the warning, then choose your password:
   - 6 to 16 characters
   - at least three of: uppercase, lowercase, digits, symbols
   - **write it down somewhere safe** — there is no recovery
4. Authenticate with your computer password when the system asks.
5. The state becomes **Unlocked — not formatted**. Press **Format now…**,
   pick a filesystem (exFAT is preselected and recommended), give the
   volume a name, and confirm.
6. Done. The drive mounts and is ready to use.

> Initialization is only for a drive that has never been set up. On a drive
> that already holds data, it makes that data permanently unreadable.

## Everyday use

**Unlock** — press the big button, type the drive password. It mounts by
itself.

**Open folder** — opens the drive in your file manager.

**Browse files** — opens the app's own file browser. You can import files
from your computer, export a selection out, create folders and delete
things, without leaving the app.

**Unmount** — always do this before pulling the drive out. It flushes
pending writes; skipping it risks corrupt files.

**Lock** — unmounts *and* re-encrypts the drive without unplugging it. The
next use needs the password again. Handy when you step away from the desk.

## Choosing a filesystem

| Filesystem | Use it when |
|---|---|
| **exFAT** | you want Linux, Windows and macOS. No file-size limit. **The default.** |
| **FAT32** | maximum compatibility with old devices. No file over 4 GB. |
| **NTFS** | mainly Windows. Linux reads and writes; macOS only reads. |
| **ext4** | Linux only, and you want real permissions and journalling. |
| **Btrfs** | Linux only, and you want checksums and snapshots. |

Options whose tools are not installed appear greyed out, with the package
name you would need.

## Tools

Reached from the **☰ menu**.

**Drive details** — capacity and free space, filesystem and mount options,
serial number, firmware revision, USB link speed, write-protect state.

**Speed test** — writes 128 MB, reads it back, reports real throughput. It
calls `fsync`, so the result reflects the drive rather than your RAM cache.
Needs the drive mounted.

**Verify integrity** — writes 32 MB of random data, reads it back and
compares SHA-256. Confirms the drive returns exactly what it stored. Worth
running on a drive you have doubts about.

**Check filesystem** — runs the appropriate `fsck`. The drive must be
unmounted. Read-only unless you ask for repair.

**Firmware diagnostics** — queries the drive controller directly: the
private-area layout, the domain map and the vendor query services. This is
read-only; no password is sent, so the attempt counter is untouched.

**Save report** — writes everything to a text file, useful when reporting a
problem.

## Remembering the drive password

Optional, off by default. **☰ → Saved password…**

Set a username and an application password, and the next successful unlock
offers to remember the drive password. From then on, one application
password gets you in.

**What this does and does not do.** It protects the *stored copy* of your
drive password, encrypted with scrypt and AES-256-GCM. It does **not** add
any protection to the drive: anyone at your computer can still unlock it
with the command-line tool or Kingston's own software. The app is not a
gatekeeper and does not pretend to be.

Forgetting the saved password costs you nothing but convenience — the drive
still opens by typing the device password.

## Appearance

**☰ → Appearance**: follow the system, or force light or dark. Remembered
between sessions.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `F5` or `Ctrl+R` | Refresh |
| `Ctrl+U` | Main action (unlock / mount / open) |
| `Ctrl+L` | Lock the drive |
| `Ctrl+Q` | Quit |
