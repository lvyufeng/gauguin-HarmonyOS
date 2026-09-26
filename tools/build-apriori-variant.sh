#!/usr/bin/env bash
#
# Build one variant payload and prove that what came out is what was asked for.
# Two experiments are defined below: `arch-first` reorders the a-priori batch,
# `xhci-host` adds the USB host stack.
#
# Why arch-first exists. The nine architectural protocols DXE never installs
# (Security, Bds, Watchdog, Variable, Variable Write, Capsule, Monotonic, Reset,
# RTC - docs/08 step 4.95) are all providers that sit late in APRIORI.inc, and the
# batch runs in APRIORI.inc order, not in firmware-volume order. So there are two
# readings of the same evidence and the static pass cannot separate them:
#
#   * these drivers fail wherever they are put, or
#   * something between the providers that do install and these eight fails them.
#
# Running the eight early separates them, and it is the only experiment that does
# so without changing anything else: no driver is added, removed or rebuilt, the
# volume keeps every file at every offset it had, and the only bytes that differ
# between this image and the baseline are the Apriori GUID array. What the panel
# then shows is the answer to both halves at once - whether the eight install,
# and which driver in the block they were moved ahead of is the first to fail
# (`P2 SEQ`, one character per entry, in the order they ran).
#
# Why xhci-host exists. The firmware has no USB host controller driver, so it
# cannot see a USB stick, and a Windows installer has to arrive on one (P3). The
# blob cannot be extracted from this phone - its XBL carries no host driver, only
# the device-mode one - so the three files come from the SM7225 sibling instead
# and the platform gains a build switch, USE_XHCI_HOST_DRIVER, that is off until
# this script turns it on. What is being checked here is that EDK2 accepts the
# three INFs at all: bitra's copies are used verbatim, which means a DXE_DEPEX
# section, a module type per file and QC's own binding, none of which the other
# 55 blobs in this image carry. A payload that builds, packs and passes the
# gates is the whole result - nothing here says the controller comes up on the
# device, and only the panel can say that.
#
# Unlike `arch-first` this one does *not* reorder anything. bitra lists
# XhciPciEmulationDxe and XhciDxe in its own APRIORI.inc as well as its DXE.inc,
# and that half is deliberately not copied: a driver in the a-priori batch is
# promoted by `Dispatcher.c:2111` setting `DriverEntry->Dependent = FALSE`, so
# its depex - here a conjunction of thirteen architectural protocols - is read
# and then ignored, and it would start before the protocols it names exist. See
# XHCI_HOST_DRIVERS in tools/make_uefi_platform.py. The consequence for this
# experiment is a useful one: APRIORI.inc is byte-identical to the default
# generation's, the a-priori array stays at 70 entries, and the P2 instrument
# keeps reading the same shape it reads on the payload now in `boot`.
#
# Usage:  tools/build-apriori-variant.sh [EXPERIMENT]
#
#   arch-first   the eight arch providers run before the Qualcomm block.
#                The default, and the definition of the experiment is the
#                ARCH_FIRST_NAMES list below rather than this script's argument
#                handling.
#   xhci-host    the USB host stack is added, and nothing is reordered. The
#                blobs are staged from Binaries/bitra/ into
#                uefi/Binaries/gauguin/, which is a directory this repository
#                ignores, so the tracked tree only ever differs by the DSC
#                switch - and restore() puts even that back.
#
# Environment:
#   DISPLAY=simple|qcom   which display driver the platform is regenerated with.
#                         Default simple, because that is what the baseline
#                         payloads are; changing it here would make this two
#                         variables instead of one.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MU=${MU:-$ROOT/work/uefi/Mu-Silicium}
OUT="$ROOT/work/out"
P2="$OUT/p2-variants"
GEN="$ROOT/tools/make_uefi_platform.py"
DISPLAY=${DISPLAY:-simple}

# These go to stderr rather than stdout, which is not a style choice: this script
# is normally run with its output redirected to a log, and there stdout is
# block-buffered while the child processes (the generator, the sync script, the
# build) write straight to the fd. The script's own notes then arrive late and
# out of order against the children's, so a captured log misplaces the step it
# was in - which is exactly how the first run of this file read as if it had
# stopped during the firmware build with no reason given. stderr is unbuffered.
log() { printf '\033[1m== %s\033[0m\n' "$*" >&2; }
note() { printf '   %s\n' "$*" >&2; }
die() { echo "error: $*" >&2; exit 1; }

# The anchor is a driver that is already early and whose own result is known
# good: ArmTimerDxe installs the Timer architectural protocol, and four of the
# eight moved drivers need exactly that. The names are INF paths as they appear
# in APRIORI.inc; make_uefi_platform.py refuses any that does not match exactly
# one line, so a typo here is an error and not an experiment that did nothing.
ANCHOR="ArmPkg/Drivers/TimerDxe/TimerDxe.inf"
ARCH_FIRST_NAMES=(
    "Universal/Variable/RuntimeDxe/VariableRuntimeDxe.inf"
    "Universal/ResetSystemRuntimeDxe/ResetSystemRuntimeDxe.inf"
    "Universal/WatchdogTimerDxe/WatchdogTimer.inf"
    "Universal/SecurityStubDxe/SecurityStubDxe.inf"
    "EmbeddedPkg/EmbeddedMonotonicCounter/EmbeddedMonotonicCounter.inf"
    "EmbeddedPkg/RealTimeClockRuntimeDxe/RealTimeClockRuntimeDxe.inf"
    "Universal/CapsuleRuntimeDxe/CapsuleRuntimeDxe.inf"
    "Universal/BdsDxe/BdsDxe.inf"
)

EXP=${1:-arch-first}
# The experiment, as arguments: GEN_ARGS goes to the platform generator and
# ORDER_ARGS goes to apriori-order.py, which has to be told the same thing about
# every conditional it will find in the regenerated APRIORI.inc - it refuses to
# guess one, so a flag added here and not there is a failed gate, not a wrong
# answer. Both start from a non-empty list so that `"${a[@]}"` is never the
# empty-array expansion, which older bash treats as an unbound variable under -u.
GEN_ARGS=()
ORDER_ARGS=(--display "$DISPLAY")
OUTDIR="$P2"
STAGED_BLOBS=""
REORDERS=0
case "$EXP" in
    arch-first)
        GEN_ARGS=(--apriori-move "$ANCHOR:$(IFS=,; echo "${ARCH_FIRST_NAMES[*]}")")
        REORDERS=1
        ;;
    xhci-host)
        GEN_ARGS=(--xhci-host)
        # ORDER_ARGS is left alone: this experiment adds no `!if` to APRIORI.inc,
        # because nothing it adds goes into that file. If that ever changes, the
        # gate below refuses to run rather than guessing the branch.
        # Its own output directory, because the payload in p2-variants/ is a P2
        # experiment on the a-priori order and this is not one. Both are read by
        # the same gates; only the directory they land in differs, so a reader
        # listing p2-variants/ still sees exactly the a-priori runs.
        OUTDIR="$OUT/usb-host"
        # The blob paths come from the generator's own table rather than being
        # spelled out again here, so a fourth one added there cannot leave this
        # cleanup quietly behind.
        STAGED_BLOBS=$(python3 -c "
import sys; sys.path.insert(0, '$ROOT/tools')
from make_xbl_binaries import SIBLING_BLOBS
print(' '.join(SIBLING_BLOBS.values()))")
        [ -n "$STAGED_BLOBS" ] || die "SIBLING_BLOBS is empty - nothing would be staged"
        ;;
    *) die "unknown experiment '$EXP' (known: arch-first, xhci-host)" ;;
esac

# The tree has to be left the way it was found, including after a failure: this
# script regenerates the tracked platform package, and a checkout that quietly
# keeps the experimental order is a checkout that builds a firmware nobody chose.
restore() {
    log "restoring the default platform"
    python3 "$GEN" --display "$DISPLAY" >/dev/null
    "$ROOT/tools/sync-uefi-platform.sh" >/dev/null
    note "uefi/Platforms/Xiaomi/gauguinPkg is back to the reference contents"
    if [ -n "$STAGED_BLOBS" ]; then
        # The sibling blobs are the one thing this run puts under uefi/ that the
        # default generation does not. They land in a directory this repository
        # ignores, so git would never show them - but "left the way it was
        # found" is what this trap promises, and a stale copy of another
        # device's driver is exactly what a later run must not silently reuse.
        #
        # Both trees, because sync-uefi-platform.sh copies rather than mirrors
        # and would otherwise leave the trio in the checkout as well. Neither
        # copy can reach a default build on its own - the generator reads its
        # driver set from device/dxe and stages a sibling only when asked, and
        # the DSC line that names them is 0 unless this script wrote a 1 - so
        # this is housekeeping rather than a correctness fix.
        for rel in $STAGED_BLOBS; do
            rm -rf "$ROOT/uefi/Binaries/gauguin/$rel" "$MU/Binaries/gauguin/$rel"
        done
        note "the $(echo "$STAGED_BLOBS" | wc -w) staged USB host blobs are gone from uefi/Binaries/gauguin and from $MU/Binaries/gauguin"
    fi
}
trap restore EXIT

# ---------------------------------------------------------------------------
log "generating the platform: $EXP"
# ---------------------------------------------------------------------------
python3 "$GEN" --display "$DISPLAY" "${GEN_ARGS[@]}"
"$ROOT/tools/sync-uefi-platform.sh"

INC="$ROOT/uefi/Platforms/Xiaomi/gauguinPkg/Include/APRIORI.inc"
mkdir -p "$OUTDIR"
cp "$INC" "$OUTDIR/APRIORI.$EXP.inc"
note "kept a copy at $OUTDIR/APRIORI.$EXP.inc"

# ---------------------------------------------------------------------------
log "building the firmware"
# ---------------------------------------------------------------------------
FD="$MU/Build/gauguinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd"
BOOTSHIM="$MU/BootShim/BootShim.bin"
BUILDLOG="$OUT/build-apriori-$EXP.log"
before=$(stat -c %Y "$FD" 2>/dev/null || echo 0)

cd "$MU"
# `set +u` around this, and it is load-bearing. setup_env.sh is Mu-Silicium's
# one-time package installer and it is not -u-clean: line 40 reads `$CI_BUILD`
# unguarded, so with this script's `set -u` inherited it aborts the whole shell
# on "CI_BUILD: unbound variable" before the build is even reached. The abort is
# fatal to the *sourcing* script rather than something `||` can catch, and its
# one message goes into this line's redirect, so the symptom is a script that
# stops after "building the firmware" having printed no reason at all - which is
# what the first two runs of this file did. Both of them did run their restore
# trap afterwards (the tracked APRIORI.inc was rewritten 48 ms after the copy
# above was taken), so the tree was never left in the experimental order; but
# nothing in the log said why the run stopped.
# shellcheck disable=SC1091
set +u
source ./setup_env.sh -p apt >/dev/null 2>&1 || { set -u; die "setup_env.sh failed"; }
set -u
# -c because the FFS layout is unchanged but the volume's own Apriori file is
# not, and an incremental build is the one case where that difference could be
# missed. A full build is minutes; a wrong answer is a hardware session.
if ! python3 build_uefi.py -d gauguin -r DEBUG -c >"$BUILDLOG" 2>&1; then
    # The build is known to end non-zero on this tree with
    # `ValueError: DTB image must not be empty.` from mkbootimg, on the *sync-dtb*
    # path, after the FD and every FV have been written. That is not a build
    # failure and it has been treated as one before, so what is checked here is
    # the artifact rather than the exit status - see docs/08 step 4.6.
    grep -q "DTB image must not be empty" "$BUILDLOG" ||
        { tail -30 "$BUILDLOG" | sed 's/^/   /'; die "build failed (full log: $BUILDLOG)"; }
    note "build ended non-zero on the known mkbootimg DTB nag; checking the artifact instead"
fi
[ -f "$FD" ] || die "no $FD after the build"
[ "$(stat -c %Y "$FD")" -gt "$before" ] ||
    die "$FD was not rewritten - the build did not produce a new volume"
if grep -q "Images Verified" "$BUILDLOG"; then
    note "$(grep -o '[0-9]* Images Verified' "$BUILDLOG" | tail -1)"
else
    note "the log does not say 'Images Verified' - the artifact check above"
    note "is what the build is judged on (full log: $BUILDLOG)"
fi

# ---------------------------------------------------------------------------
log "building the payload"
# ---------------------------------------------------------------------------
IMG="$OUTDIR/Mu-gauguin-$EXP-gzip.img"
# The same shape as the payload of record - silicon header, gzip - so that the
# only difference from the baseline image is the ordering inside the volume.
#
# These two produce the payload and are therefore *not* piped into anything: the
# image has to exist and the tree has to be the checked one, so both are allowed
# to fail the script on their own status.
"$ROOT/tools/build-device-tree.sh" --reuse -o "$OUT/sm7225-xiaomi-gauguin.dtb"
python3 "$ROOT/tools/make_boot_image.py" --fd "$FD" --bootshim "$BOOTSHIM" \
    --dtb "$OUT/sm7225-xiaomi-gauguin.dtb" --compression gzip --profile silicon \
    -o "$IMG"

# ---------------------------------------------------------------------------
log "the order that came out, read from the image and not from the file"
# ---------------------------------------------------------------------------
# The point of the exercise: APRIORI.inc is what was asked for, this is what the
# firmware carries. They are checked against each other because a reorder that
# silently did not reach the volume would look exactly like a reorder that made
# no difference on the device.
#
# Every check below is read from the exit status of the command itself and not
# from a pipe: `cmd | tail` hands `set -e` the status of `tail`, so a failing
# gate would print its complaint and let the build continue. That is a mistake
# this repository has already made once (tools/build-p2-payloads.sh says so at
# the same place), and the log line is printed from the file afterwards instead.
gate() {                       # gate <log> <name> <command...>
    local lg=$1 name=$2; shift 2
    if ! "$@" >"$lg" 2>&1; then
        sed 's/^/   /' "$lg" >&2
        die "$name failed"
    fi
}

gate "$OUT/apriori-order-$EXP.log" "apriori-order.py" \
    python3 "$ROOT/tools/apriori-order.py" "$IMG" "${ORDER_ARGS[@]}"
tail -n 1 "$OUT/apriori-order-$EXP.log" | sed 's/^/   /' >&2

log "structure and ABL's checks"
gate "$OUT/check-payload-$EXP.log" "check-payload.py" \
    python3 "$ROOT/tools/check-payload.py" "$IMG"
tail -n 1 "$OUT/check-payload-$EXP.log" | sed 's/^/   /' >&2
gate "$OUT/abl-boot-check-$EXP.log" "abl-boot-check.py" \
    python3 "$ROOT/tools/abl-boot-check.py" \
    --dtbo "$HOME/backup/gauguin/images/part-dtbo.img" "$IMG"
tail -n 2 "$OUT/abl-boot-check-$EXP.log" | sed 's/^/   /' >&2

MAP="$(dirname "$FD")/FVMAIN.Fv.txt"
if [ -f "$MAP" ]; then
    log "the volume's contents, against GenFv's map"
    gate "$OUT/fvmap-$EXP.log" "fv-inventory.py --against" \
        python3 "$ROOT/tools/fv-inventory.py" "$IMG" --against "$MAP"
    tail -n 1 "$OUT/fvmap-$EXP.log" | sed 's/^/   /' >&2
fi

# ---------------------------------------------------------------------------
log "ready: $IMG"
if [ "$REORDERS" = 1 ]; then
    note "flash it with tools/flash-boot.sh --twrp   (boot partition only)"
    note "and read the P2 SEQ line off the panel before anything else"
else
    note "built with USE_XHCI_HOST_DRIVER=1. Nothing in it is known to come up on"
    note "the device, and the panel has nothing to say about it, so it is not the"
    note "payload that answers the open P2 question and must not take the place of"
    note "the one in boot until that reading has been taken."
fi
