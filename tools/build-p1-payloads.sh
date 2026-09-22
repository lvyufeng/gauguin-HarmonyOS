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
# Usage:  tools/build-p1-payloads.sh [--skip-noefi]
#
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINUX="$ROOT/work/linux"
OUT="$ROOT/work/out"
DTB="$OUT/sm7225-xiaomi-gauguin.dtb"
RAMDISK="$OUT/initramfs.cpio.gz"
MK="$ROOT/tools/make_boot_image.py"

# The command line is a file so the same string is used everywhere, including
# in docs/07. It is what gives a payload with no UART and no screen driver a
# place to leave its log (pstore at the phone's own region, out of its base tree).
CMDLINE="$(cat "$ROOT/docs/p1-cmdline.txt")"

log() { printf '\033[1m%s\033[0m\n' "$*"; }

for f in "$DTB" "$RAMDISK" "$ROOT/docs/p1-cmdline.txt"; do
    [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }
done

cd "$LINUX"

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
if [ "${1:-}" = "--skip-noefi" ]; then
    log "== skipping the no-EFI build (--skip-noefi)"
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

    # text_offset 0x80000 because that is what the phone's own kernel declares;
    # it is bootloader metadata the kernel never reads back, so setting it is a
    # way to remove a difference rather than a way to change behaviour.
    log "== raw-noefi variant, the closest match to stock"
    python3 "$MK" --kernel "$OUT/Image-noefi" --ramdisk "$RAMDISK" --dtb "$DTB" \
        --profile stock --cmdline "$CMDLINE" --text-offset 0x80000 \
        -o "$OUT/boot-pstore-raw-noefi.img"
fi

log "== done"
ls -la "$OUT"/boot-pstore*.img
