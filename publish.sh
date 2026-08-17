#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Publish IronKey Locker+ for Linux to GitHub.
#
# Handles the whole path: checks the working tree, verifies no vendor
# material would leak, builds the .deb, commits, creates the remote
# repository if needed, pushes, tags and drafts a release.
#
#   ./publish.sh                 # first publish, or push new changes
#   ./publish.sh --release       # also tag and create a GitHub release
#   ./publish.sh --dry-run       # show what would happen, change nothing
#
# Authentication: uses the GitHub CLI (gh) when available — it can create
# the repository for you. Otherwise falls back to plain git, and you must
# create the repository on github.com yourself first.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

GH_USER="SimuZSkyNeT"
REPO="ironkey-linux-gui"
BRANCH="main"
DEFAULT_EMAIL="318048242+SimuZSkyNeT@users.noreply.github.com"

DRY=0
DO_RELEASE=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --release) DO_RELEASE=1 ;;
        -h|--help) sed -n '3,18p' "$0"; exit 0 ;;
        *) echo "Unknown option: $a" >&2; exit 1 ;;
    esac
done

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY" = 1 ]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

VERSION="$(python3 -c "
import sys; sys.path.insert(0, '$ROOT/src')
from ironkey_about import VERSION; print(VERSION)")"
TAG="v$VERSION"

say "IronKey Locker+ for Linux — publish $TAG"
[ "$DRY" = 1 ] && warn "dry run: nothing will be changed or uploaded"

# ---------------------------------------------------------------- checks
say "1. Sanity checks"

command -v git >/dev/null || die "git is not installed"
ok "git present"

# Every module must at least compile before anything is published.
for f in src/*.py; do
    python3 -m py_compile "$f" 2>/dev/null || die "$f does not compile"
done
ok "all modules compile"

# The one mistake that must never happen: shipping Kingston's binaries.
LEAK="$(git ls-files --cached --others --exclude-standard 2>/dev/null \
        | grep -iE '\.iso$|\.exe$|\.dylib$|IronKey\.app|^iso/|^refs/' || true)"
[ -z "$LEAK" ] || die "vendor material would be published:
$LEAK
Fix .gitignore before continuing."
ok "no vendor material in the tree"

grep -q "GPL-2.0-only" src/ironkey_gui.py || warn "licence header missing in ironkey_gui.py"
[ -f LICENSE ] || die "LICENSE file is missing"
ok "licence present"

# ------------------------------------------------------------- identity
say "2. Git identity"
if ! git config user.name >/dev/null 2>&1 && \
   ! git config --global user.name >/dev/null 2>&1; then
    run git config user.name "$GH_USER"
    ok "set user.name = $GH_USER (local to this repository)"
else
    ok "user.name = $(git config user.name || git config --global user.name)"
fi
if ! git config user.email >/dev/null 2>&1 && \
   ! git config --global user.email >/dev/null 2>&1; then
    run git config user.email "$DEFAULT_EMAIL"
    ok "set user.email = $DEFAULT_EMAIL (local to this repository)"
else
    ok "user.email = $(git config user.email || git config --global user.email)"
fi

# ---------------------------------------------------------------- build
say "3. Build the Debian package"
if command -v dpkg-deb >/dev/null && command -v fakeroot >/dev/null; then
    if [ "$DRY" = 1 ]; then
        printf '  [dry-run] ./packaging/build-deb.sh\n'
    else
        ./packaging/build-deb.sh >/dev/null && \
            ok "dist/ironkey-lockerplus_${VERSION}_all.deb"
    fi
else
    warn "dpkg-deb/fakeroot missing — skipping the .deb"
fi

# ----------------------------------------------------------------- repo
say "4. Local repository"
if [ ! -d .git ]; then
    run git init -q
    ok "initialised"
fi
run git checkout -q -B "$BRANCH"
ok "branch $BRANCH"

run git add -A
if [ "$DRY" = 1 ]; then
    printf '  [dry-run] commit\n'
elif git diff --cached --quiet; then
    ok "nothing new to commit"
else
    git commit -q -m "IronKey Locker+ for Linux $VERSION

Graphical application to initialise, unlock, mount and manage Kingston
IronKey Locker+ drives on Linux, including first-time setup."
    ok "committed"
fi

# --------------------------------------------------------------- remote
say "5. GitHub"
REMOTE_URL="https://github.com/$GH_USER/$REPO.git"

if command -v gh >/dev/null; then
    if gh auth status >/dev/null 2>&1; then
        ok "gh authenticated"
        if ! gh repo view "$GH_USER/$REPO" >/dev/null 2>&1; then
            say "   creating the repository"
            run gh repo create "$GH_USER/$REPO" --public \
                --description "Set up and use Kingston IronKey Locker+ encrypted drives on Linux, including first-time initialisation" \
                --source . --remote origin --push
            ok "repository created and pushed"
        else
            ok "repository already exists"
            git remote get-url origin >/dev/null 2>&1 || \
                run git remote add origin "$REMOTE_URL"
            run git push -u origin "$BRANCH"
            ok "pushed"
        fi
    else
        warn "gh is installed but not logged in. Run:  gh auth login"
        warn "then run this script again."
        exit 1
    fi
else
    warn "GitHub CLI (gh) not installed."
    echo "     Either install it:"
    echo "       sudo apt install gh    # then: gh auth login"
    echo "     or create the repository by hand at:"
    echo "       https://github.com/new   (name it '$REPO', public, empty)"
    echo
    git remote get-url origin >/dev/null 2>&1 || \
        run git remote add origin "$REMOTE_URL"
    ok "remote set to $REMOTE_URL"
    echo "     Then push with:"
    echo "       git push -u origin $BRANCH"
    [ "$DO_RELEASE" = 1 ] && warn "a release needs gh; skipping"
    exit 0
fi

# -------------------------------------------------------------- release
if [ "$DO_RELEASE" = 1 ]; then
    say "6. Release $TAG"
    if gh release view "$TAG" >/dev/null 2>&1; then
        ok "release $TAG already exists"
    else
        NOTES="$(python3 - <<'PY'
import sys
sys.path.insert(0, "src")
from ironkey_about import CHANGELOG
version, date, items = CHANGELOG[0]
print(f"## {version} — {date}\n")
for i in items:
    print(f"- {i}")
print("""
### Install

**Any distribution**
```
git clone https://github.com/SimuZSkyNeT/ironkey-linux-gui.git
cd ironkey-linux-gui && ./install.sh
```

**Debian, Ubuntu, Mint, elementary** — download the .deb below, then:
```
sudo apt install ./ironkey-lockerplus_*.deb
```

Ten consecutive wrong drive passwords erase the data permanently. This is a
hardware feature of the drive.""")
PY
)"
        DEB="$(ls -1 dist/ironkey-lockerplus_${VERSION}_all.deb 2>/dev/null || true)"
        if [ -n "$DEB" ]; then
            run gh release create "$TAG" "$DEB" \
                --title "IronKey Locker+ for Linux $VERSION" \
                --notes "$NOTES"
        else
            run gh release create "$TAG" \
                --title "IronKey Locker+ for Linux $VERSION" \
                --notes "$NOTES"
        fi
        ok "release published"
    fi
fi

say "Done"
echo "  https://github.com/$GH_USER/$REPO"
[ "$DO_RELEASE" = 1 ] && echo "  https://github.com/$GH_USER/$REPO/releases/tag/$TAG"
echo
