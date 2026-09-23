#!/usr/bin/env bash
#
# Build the two UEFI payload variants — one per surviving candidate, uncompressed
# and gzip kernel — from the UEFI volume, in one pass, and refuse to hand over
# either one unless ABL's own checks pass on it.
#
# Why a script: these two were built by hand once, and by the time anyone looked
# again the device tree inside them was two changes stale — it still carried the
# inherited `ramoops@ffc00000` and it carried no `/__symbols__` at all. Both are
# fatal *before* any of our firmware runs, both are invisible to
# `tools/check-payload.py` (which checks structure, and the structure was fine),
# and both present on the phone as the same thing: a phone that does nothing.
# They were found by running `tools/abl-boot-check.py` over the images, which
# nothing was doing. This script runs it, so the two cannot drift again.
#
# The payload of record is **not** `Mu-Silicium/Mu-gauguin.img`. That is what
# Mu-Silicium's own builder produces, and it cannot be made bootable here: the
# builder never passes `--pagesize` (so every image it makes is page 2048 while
# this device's ABL, and its own stock `boot`, use 0x1000) and it has no way to
# put the DTB in a declared region (it only ever glues the DTB onto the end of
# the kernel blob, and ABL's `DTBImgCheckAndAppendDT` reads no DTB at all when
# `header_version` is 1 — `BootLinux.c:454`). `tools/make_boot_image.py
# --profile stock` is what builds the images here.
#
# What comes from the UEFI build is the volume and the shim, which is the part
# that took an EDK2 toolchain. Rebuilding those is `./build_uefi.py -d gauguin
# -r DEBUG -c` inside work/uefi/Mu-Silicium; this script only consumes them.
#
# Usage:  tools/build-p2-payloads.sh
#         FD=... BOOTSHIM=... DTBO=... to override the inputs.
#
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/work/out"
P2="$OUT/p2-variants"
MK="$ROOT/tools/make_boot_image.py"

MU="${MU:-$ROOT/work/uefi/Mu-Silicium}"
FD="${FD:-$MU/Build/gauguinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd}"
BOOTSHIM="${BOOTSHIM:-$MU/BootShim/BootShim.bin}"
DTB="${DTB:-$OUT/sm7225-xiaomi-gauguin.dtb}"
DTBO_IMG="${DTBO:-$HOME/backup/gauguin/images/part-dtbo.img}"

log() { printf '\033[1m%s\033[0m\n' "$*"; }

for f in "$FD" "$BOOTSHIM"; do
    [ -f "$f" ] || { echo "missing $f." \
        "The volume and the shim come from the UEFI build; run" \
        "\`./build_uefi.py -d gauguin -r DEBUG -c\` in $MU first, or point" \
        "FD=... and BOOTSHIM=... at another build." >&2; exit 1; }
done

# The one device tree, from the one place that builds it. `--reuse` because this
# script does not touch the kernel tree: if the tree is already there and valid
# it is used as-is, and if it is not, build-device-tree.sh builds it rather than
# failing. Both paths run the same four checks, so honouring a pre-built tree
# does not lower the bar.
log "== device tree"
"$ROOT/tools/build-device-tree.sh" --reuse -o "$DTB"

mkdir -p "$P2"

# The pair is the experiment, not either image: the two differ in exactly one
# property - whether the UEFI volume is a gzip stream - so a difference between
# what the phone does with them is attributable to that property and nothing
# else. See step 4b/4c in docs/08-device-session.md.
for c in none gzip; do
    log "== Mu-gauguin-stock-$c.img"
    python3 "$MK" --fd "$FD" --bootshim "$BOOTSHIM" --dtb "$DTB" \
        --compression "$c" --profile stock \
        -o "$P2/Mu-gauguin-stock-$c.img"
done

log "== done"
ls -la "$P2"/Mu-gauguin-stock-*.img

# Both gates, and both are gates rather than printouts: each exits nonzero if
# any image fails. check-payload.py reads the wrapper's structure; abl-boot-check.py
# replays ABL's own decision path over it - the header check, the msm-id selection,
# the overlay's fixups against the tree's /__symbols__, and where the ramoops
# region and the framebuffer land in the phone's memory map. An image that fails
# the second one is refused by ABL before any of our code runs, so trying it costs
# a physical reset and tells you nothing.
log "== structure"
python3 "$ROOT/tools/check-payload.py" "$P2"/Mu-gauguin-stock-*.img

log "== ABL's checks, replayed offline"
python3 "$ROOT/tools/abl-boot-check.py" --dtbo "$DTBO_IMG" "$P2"/Mu-gauguin-stock-*.img
