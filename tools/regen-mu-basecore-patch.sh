#!/usr/bin/env bash
#
# Regenerate uefi/patches/mu-basecore-local.patch from the working checkout.
#
# The patch is how the local edits to Mu_Basecore reach this repository at all:
# Mu_Basecore is a nested checkout of upstream microsoft/mu_basecore living under
# `work/`, which the outer repository ignores and whose `origin` is not ours to
# push to. So the edits are carried as a patch.
#
# The patch has a hand-written provenance header that is NOT part of the
# checkout's diff, which is why a bare `git diff > patch` destroys it. It lives
# in uefi/patches/mu-basecore-local.header; this script concatenates the two and
# then verifies the result the way tools/sync-uefi-platform.sh needs it to be -
# applicable to a pristine upstream HEAD, and reverse-applicable to the working
# tree that produced it.
#
# Usage:  regen-mu-basecore-patch.sh [--mu DIR] [--check]

set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
MU=${MU:-$REPO/work/uefi/Mu-Silicium}
HEADER=$REPO/uefi/patches/mu-basecore-local.header
PATCH=$REPO/uefi/patches/mu-basecore-local.patch
CHECK=0

while [ $# -gt 0 ]; do
    case "$1" in
        --mu)    MU=$2; shift 2 ;;
        --check) CHECK=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

die() { echo "error: $*" >&2; exit 1; }

BASECORE=$MU/Mu_Basecore
# `.git` is a file here, not a directory: Mu_Basecore is a submodule of the
# Mu-Silicium checkout, so its git dir lives in ../.git/modules/Mu_Basecore.
# Ask git rather than testing for a directory.
git -C "$BASECORE" rev-parse --git-dir >/dev/null 2>&1 || die "$BASECORE is not a git checkout"
[ -f "$HEADER" ] || die "missing $HEADER - it carries the patch's provenance notes"

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

cat "$HEADER" > "$TMP"
git -C "$BASECORE" diff >> "$TMP"

# Guard against the one failure that matters: a diff that came out empty means
# the checkout has lost the edits, and writing that would delete the only copy.
[ "$(git -C "$BASECORE" diff --stat | wc -l)" -gt 0 ] || die "the checkout has no local edits - refusing to write an empty patch"

# The edits have to sit on top of a clean upstream commit, or the patch cannot be
# applied to a fresh checkout. Untracked files are fine (the build leaves several).
[ -z "$(git -C "$BASECORE" diff --cached --name-only)" ] || die "the checkout has staged changes - commit or unstage them first"

if [ "$CHECK" = 1 ]; then
    git -C "$BASECORE" apply --reverse --check "$TMP" \
        || die "the regenerated patch does not reverse-apply to the working tree"
    echo "ok: the regenerated patch matches the working tree"
    exit 0
fi

cat "$TMP" > "$PATCH"
echo "wrote $PATCH ($(git -C "$BASECORE" diff --stat | tail -1))"
