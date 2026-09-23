#!/usr/bin/env bash
#
# Build one payload with the a-priori batch reordered, and prove the order that
# came out is the order that was asked for.
#
# Why this exists. The eight architectural protocols DXE never installs
# (Security, Bds, Watchdog, Variable, Capsule, Monotonic, Reset, RTC - docs/08
# step 4.9) are all providers that sit late in APRIORI.inc, and the batch runs in
# APRIORI.inc order, not in firmware-volume order. So there are two readings of
# the same evidence and the static pass cannot separate them:
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
# Usage:  tools/build-apriori-variant.sh [EXPERIMENT]
#
#   arch-first   the eight arch providers run before the Qualcomm block.
#                The default, and the definition of the experiment is the
#                MOVE table below rather than this script's argument handling.
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
case "$EXP" in
    arch-first)
        MOVE="$ANCHOR:$(IFS=,; echo "${ARCH_FIRST_NAMES[*]}")"
        ;;
    *) die "unknown experiment '$EXP' (known: arch-first)" ;;
esac

# The tree has to be left the way it was found, including after a failure: this
# script regenerates the tracked platform package, and a checkout that quietly
# keeps the experimental order is a checkout that builds a firmware nobody chose.
restore() {
    log "restoring the default a-priori order"
    python3 "$GEN" --display "$DISPLAY" >/dev/null
    "$ROOT/tools/sync-uefi-platform.sh" >/dev/null
    note "uefi/Platforms/Xiaomi/gauguinPkg is back to the reference order"
}
trap restore EXIT

# ---------------------------------------------------------------------------
log "generating the platform with the a-priori batch reordered"
# ---------------------------------------------------------------------------
python3 "$GEN" --display "$DISPLAY" --apriori-move "$MOVE"
"$ROOT/tools/sync-uefi-platform.sh"

INC="$ROOT/uefi/Platforms/Xiaomi/gauguinPkg/Include/APRIORI.inc"
mkdir -p "$P2"
cp "$INC" "$P2/APRIORI.$EXP.inc"
note "kept a copy at $P2/APRIORI.$EXP.inc"

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
IMG="$P2/Mu-gauguin-$EXP-gzip.img"
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
    python3 "$ROOT/tools/apriori-order.py" "$IMG" --display "$DISPLAY"
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
note "flash it with tools/flash-boot.sh --twrp   (boot partition only)"
note "and read the P2 SEQ line off the panel before anything else"
