#!/usr/bin/env bash
#
# Build every P1 payload variant from the kernel tree, in one go.
#
# Why a script: the runbook names four images, and they differ only in the two
# properties that separate our kernels from the one this phone actually boots
# (see the table in docs/07) — compression, and whether the arm64 image header
# is in its EFI/PE form. Rebuilding them by hand is where a wrong `gzip` or a
# forgotten `--text-offset` turns a device cycle into a mystery, because the
# images look identical in `ls -la`.
#
#   kernel          raw or gzip        the phone's own kernel region is raw
#   arm64 header    EFI stub or not    the phone's is a plain branch, res5=0
#   text_offset     0 or 0x80000       the phone's declares 0x80000
#
# `raw-noefi` is the only one that matches the phone on all three and is the
# first thing to try on the device.
#
# The device tree is built here too, not taken from the tree: the ramoops
# carveout in it (0xbff00000, the vendor's own address and record geometry) is
# what makes the log readable from Android afterwards, so it is part of the
# payload, not an input.
#
# The no-EFI kernel needs a second full build (`./scripts/config --disable EFI`
# changes code generation, so it cannot be patched into an existing Image). That
# build is the slow part and its result does not change when only the DTB or the
# cmdline does, so by default the saved work/out/Image-noefi is reused. Pass
# --rebuild-noefi after touching kernel source or config.
#
# Usage:  tools/build-p1-payloads.sh [--rebuild-noefi]
#
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINUX="$ROOT/work/linux"
OUT="$ROOT/work/out"
DTB_NAME=sm7225-xiaomi-gauguin
DTB="$OUT/$DTB_NAME.dtb"
RAMDISK="$OUT/initramfs.cpio.gz"
MK="$ROOT/tools/make_boot_image.py"

# The command line is a file so the same string is used everywhere, including
# in docs/07. It is what gives a payload with no UART and no screen driver a
# place to leave its log (pstore at the phone's own region, out of its base tree).
CMDLINE="$(cat "$ROOT/docs/p1-cmdline.txt")"

log() { printf '\033[1m%s\033[0m\n' "$*"; }

REBUILD_NOEFI=0
[ "${1:-}" = "--rebuild-noefi" ] && REBUILD_NOEFI=1

[ -f "$RAMDISK" ] || { echo "missing $RAMDISK" >&2; exit 1; }
[ -f "$ROOT/docs/p1-cmdline.txt" ] || { echo "missing docs/p1-cmdline.txt" >&2; exit 1; }

cd "$LINUX"

log "== device tree ($DTB_NAME.dtb)"
# The board DTS is tracked in dts/ and copied into the kernel tree, because that
# tree is not versioned here. Refresh it, so this build cannot run against a
# stale copy of the file the ramoops check below is about - the two drifting
# apart is exactly how the log ends up somewhere Android does not read.
cp "$ROOT/dts/$DTB_NAME.dts" "arch/arm64/boot/dts/qcom/$DTB_NAME.dts"
# And make a missing Makefile entry an error rather than a stale .dtb.
rm -f "arch/arm64/boot/dts/qcom/$DTB_NAME.dtb"
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)" dtbs
cp "arch/arm64/boot/dts/qcom/$DTB_NAME.dtb" "$DTB"
# A tree that still carries the inherited 0xffc00000 ramoops, or a second
# ramoops node, would silently move the log somewhere Android does not read.
n=$(dtc -I dtb -O dts -o - "$DTB" 2>/dev/null | grep -c 'ramoops@')
[ "$n" = 1 ] || { echo "expected exactly 1 ramoops node in $DTB, found $n" >&2; exit 1; }
dtc -I dtb -O dts -o - "$DTB" 2>/dev/null | grep -q 'ramoops@bff00000' \
    || { echo "$DTB has no ramoops@bff00000 - the log would go somewhere Android" \
              "cannot read" >&2; exit 1; }

# --- 1. the EFI-stub kernel, as configured -----------------------------------
log "== building Image (CONFIG_EFI as configured)"
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)" Image
gzip -9 -c arch/arm64/boot/Image > "$OUT/Image-pstore.gz"

log "== raw variants from the EFI-stub kernel"
python3 "$MK" --kernel arch/arm64/boot/Image --ramdisk "$RAMDISK" --dtb "$DTB" \
    --profile stock --cmdline "$CMDLINE" \
    -o "$OUT/boot-pstore-raw.img"
python3 "$MK" --kernel arch/arm64/boot/Image --ramdisk "$RAMDISK" --dtb "$DTB" \
    --profile stock --cmdline "$CMDLINE" --text-offset 0x80000 \
    -o "$OUT/boot-pstore-raw-txt.img"

log "== compressed variants, kept for the comparison"
python3 "$MK" --kernel "$OUT/Image-pstore.gz" --ramdisk "$RAMDISK" --dtb "$DTB" \
    --profile stock --cmdline "$CMDLINE" \
    -o "$OUT/boot-pstore.img"

# ABL has a check whose text is "Decompress kernel size is smaller than image
# header size", and BootShim's image is written so that it passes (0x300000
# declared, 0x300070 decompressed). A Linux Image.gz fails it, because
# image_size covers BSS and so is larger than anything the gzip stream
# contains. This variant lowers image_size to the real decompressed length,
# which is the only cheap way to test whether that check is the one refusing us.
#
# Printed as hex, because --image-size takes hex: the first version of this
# script passed the decimal length and produced image_size 0x46891520, which is
# the decimal digits read as hex. It passed every structural check, which is
# exactly why tools/check-payload.py prints the value rather than trusting it.
kimg=$(python3 -c "
import gzip
d=gzip.decompress(open('$OUT/Image-pstore.gz','rb').read())
print(hex(len(d)))")
log "== compressed variant with image_size lowered to the real size ($kimg)"
python3 "$MK" --kernel "$OUT/Image-pstore.gz" --ramdisk "$RAMDISK" --dtb "$DTB" \
    --profile stock --cmdline "$CMDLINE" --image-size "$kimg" \
    -o "$OUT/boot-pstore-gz-fixedsz.img"

# --- 2. the EFI-stub-free kernel, which is a second full build ---------------
if [ "$REBUILD_NOEFI" = 0 ] && [ -f "$OUT/Image-noefi" ]; then
    log "== reusing the saved no-EFI kernel ($OUT/Image-noefi, --rebuild-noefi to redo)"
else
    log "== building Image with CONFIG_EFI=n (full rebuild)"
    cp .config /tmp/p1-config-with-efi
    # Restore on every exit path: leaving EFI off would silently change every
    # later build in this tree.
    trap 'cp /tmp/p1-config-with-efi "$LINUX/.config"' EXIT
    ./scripts/config --disable EFI
    make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
    make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)" Image
    cp arch/arm64/boot/Image "$OUT/Image-noefi"
    cp /tmp/p1-config-with-efi .config
    trap - EXIT
fi

# text_offset 0x80000 because that is what the phone's own kernel declares; it
# is bootloader metadata the kernel never reads back, so setting it is a way to
# remove a difference rather than a way to change behaviour.
log "== raw-noefi variant, the closest match to stock"
python3 "$MK" --kernel "$OUT/Image-noefi" --ramdisk "$RAMDISK" --dtb "$DTB" \
    --profile stock --cmdline "$CMDLINE" --text-offset 0x80000 \
    -o "$OUT/boot-pstore-raw-noefi.img"

log "== done"
ls -la "$OUT"/boot-pstore*.img
