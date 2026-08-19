# Command-line interface

Everything the graphical application does to the drive, without a desktop
session — so it works over SSH, in scripts and on headless machines.

```
ironkey --help
```

## Commands

| Command | What it does | Root |
|---|---|---|
| `status` | what the drive is doing | no |
| `info` | hardware and filesystem details | no |
| `filesystems` | what this machine can format | no |
| `unlock` | unlock and mount | yes |
| `mount` / `umount` | mount or unmount | yes |
| `lock` | unmount and re-encrypt | yes |
| `format` | create a filesystem (**erases everything**) | yes |
| `init` | set the first password on a new drive | yes |
| `fsck` | check the filesystem | yes |
| `diagnostics` | low-level firmware query (read-only) | yes |
| `update` | check GitHub for a newer version | no |
| `version` | version and project URL | no |

Commands needing root re-run themselves through `sudo` (or `pkexec`), so
you do not have to remember which is which.

## Examples

```bash
ironkey status                       # is it there, is it unlocked
ironkey unlock                       # prompts for the drive password
ironkey info                         # serial, firmware, USB speed, space
ironkey lock                         # re-encrypt without unplugging

ironkey format --fs exfat --label WORK
ironkey format --fs ext4 --label BACKUP --yes    # no confirmation

ironkey passwd                       # change the password, keep the data
ironkey attempts                     # tries left before the data is erased
ironkey identity                     # what Windows and macOS read from it
ironkey init --hint "office laptop"  # first password, plus the identity record

ironkey fsck                         # report only
ironkey fsck --repair                # fix what it finds
```

## Scripting

Add `--json` to any command for machine-readable output:

```bash
ironkey status --json | jq -r .state
```

Exit status is `0` on success and `1` on failure, so the usual shell
patterns work:

```bash
if ironkey status --json | jq -e '.state == "unlocked"' >/dev/null; then
    echo "drive is open"
fi
```

To supply the password without a prompt — in a script, a cron job or a
pipeline:

```bash
echo "$DRIVE_PASSWORD" | ironkey unlock --password-stdin
```

The password is read from standard input and never appears in the command
line, so it stays out of `ps` and your shell history. Prefer reading it
from a file with restrictive permissions or a secret manager rather than
embedding it in the script.

> Ten consecutive wrong passwords erase the data permanently. Be careful
> with retry loops in automation.

## Not available here

The file browser, the saved-password vault, the appearance settings and
"copy app to drive" are graphical by nature and exist only in the GUI.

## Where authentication comes from

Commands needing root re-run themselves through `sudo` when you are in a
terminal, or `pkexec` when a desktop session is available. On a system-wide
install a polkit action means the authentication is remembered for a few
minutes, so a sequence of commands asks once.
