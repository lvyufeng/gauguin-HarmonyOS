#!/usr/bin/env bash
#
# Build every P1 payload variant from the kernel tree, in one go.
#
# Why a script: the runbook names six images, and they differ only in the two
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
# carveout in it (0xd0000000, our address - this phone declares none, see
# docs/07) and the /chosen simple-framebuffer are the payload's two log
# channels, so they are part of the payload rather than an input to it.
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

# The phone's own dtbo partition, dumped by tools/device-dump-helper.sh. It is an
# input to the build, not a reference: the symbols the tree has to declare, and
# therefore whether ABL accepts the payload at all, are read out of it. Override
# with DTBO=... .
DTBO_IMG="${DTBO:-$HOME/backup/gauguin/images/part-dtbo.img}"

# The command line is a file so the same string is used everywhere, including
# in docs/07. It is what gives a payload with no UART and no working display
# driver a place to leave its log: the ramoops region it names, and the console
# that goes to the panel.
CMDLINE="$(cat "$ROOT/docs/p1-cmdline.txt")"

log() { printf '\033[1m%s\033[0m\n' "$*"; }

REBUILD_NOEFI=0
[ "${1:-}" = "--rebuild-noefi" ] && REBUILD_NOEFI=1

[ -f "$RAMDISK" ] || { echo "missing $RAMDISK" >&2; exit 1; }
[ -f "$ROOT/docs/p1-cmdline.txt" ] || { echo "missing docs/p1-cmdline.txt" >&2; exit 1; }
[ -f "$DTBO_IMG" ] || { echo "missing the dtbo dump at $DTBO_IMG (set DTBO=...)." \
    "The device tree needs /__symbols__ for exactly the symbols that partition" \
    "names, so a payload built without it is one ABL refuses with" \
    "\"ApplyOverlay: ufdt apply overlay failed\" and no output." >&2; exit 1; }

# The command line asks fbcon for a font by name; fbcon looks that name up with
# find_font() and, when it is not there, falls back to the default **without
# printing anything**. That silence is the whole problem. A payload whose kernel
# lacks the font draws 8x16 text on a 1080-wide panel - 135 columns of glyphs too
# small to read in a photograph - and the only trace that the request was made at
# all is the command line, which says what was intended rather than what is in
# the kernel. Checking the name against the kernel that is about to be packaged
# closes the loop between `CONFIG_FONT_TER16x32=y` (which build-kernel.sh verifies
# survived olddefconfig) and `fbcon=font:TER16x32` (which is what the kernel
# actually looks up). The two are joined only by the font_desc's `.name` field,
# and nothing else checks that they still agree.
FONTNAME=$(printf '%s\n' "$CMDLINE" | tr ' ' '\n' | sed -n 's/^fbcon=font://p')
check_font() {
    # The `|| true` is load-bearing, and its absence would have been the same bug
    # one layer up: `grep -c` exits 1 when the count is zero, and under `set -e`
    # that kills the script at the assignment - so a missing font would abort
    # silently, with the error message below never reaching anyone. A guard whose
    # failure prints nothing is not a guard.
    [ -n "$FONTNAME" ] || { echo "   note: no fbcon=font: on the command line, so" \
                                  "there is no font to check" >&2; return 0; }
    case "$1" in
        *.gz) found=$(gzip -dc "$1" | strings -a | grep -cx "$FONTNAME" || true) ;;
        *)    found=$(strings -a "$1" | grep -cx "$FONTNAME" || true) ;;
    esac
    [ "${found:-0}" -ge 1 ] \
        || { echo "$1 does not contain the font '$FONTNAME' the command line asks" \
                  "fbcon for - find_font() would fall back to 8x16 silently, and the" \
                  "panel would show 135 unreadable columns" >&2; exit 1; }
    printf '   font %-10s present in %s\n' "$FONTNAME" "$(basename "$1")"
}

cd "$LINUX"

# The tree is built by a script of its own, because it now has two consumers:
# the payloads here and the UEFI ones (tools/build-p2-payloads.sh). It was built
# by hand for the second consumer and went stale, which is a failure this
# arrangement cannot have - see the header of tools/build-device-tree.sh.
"$ROOT/tools/build-device-tree.sh" -o "$DTB"

# --- 1. the EFI-stub kernel, as configured -----------------------------------
log "== building Image (CONFIG_EFI as configured)"
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)" Image
check_font arch/arm64/boot/Image
gzip -9 -c arch/arm64/boot/Image > "$OUT/Image-pstore.gz"
check_font "$OUT/Image-pstore.gz"

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

# ABL compares a size out of the decompressed kernel, and this is the field it
# reads: after inflating, GZipPkgCheck tests
#
#     Kptr->ImageSize > (DeviceTreeLoadAddr - KernelLoadAddr)
#
# and refuses with "DTB header can get corrupted due to runtime kernel size". It
# is a memory check, not an image one - a Linux Image.gz declares an image_size
# that covers BSS (0x2d90000, 47 MB) against 125 MB of headroom between where the
# kernel is copied and where the tree goes, so it passes without being asked.
# Lowering it to the real decompressed length removes the difference anyway,
# which costs nothing and leaves the variant comparable.
#
# (The other decompress check is a red herring that this script used to build
# this variant for: `OutLen <= sizeof(struct kernel64_hdr *)` compares the
# *decompressed length* against the size of a pointer. A 46 MB kernel cannot
# fail it. docs/07.)
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
# Checked even when the saved kernel is reused: a stale Image-noefi is exactly
# how variant 1 - the first thing the runbook flashes - ends up without the font.
check_font "$OUT/Image-noefi"

# text_offset 0x80000 because that is what the phone's own kernel declares; it
# is bootloader metadata the kernel never reads back, so setting it is a way to
# remove a difference rather than a way to change behaviour.
log "== raw-noefi variant, the closest match to stock"
python3 "$MK" --kernel "$OUT/Image-noefi" --ramdisk "$RAMDISK" --dtb "$DTB" \
    --profile stock --cmdline "$CMDLINE" --text-offset 0x80000 \
    -o "$OUT/boot-pstore-raw-noefi.img"

# The same kernel compressed, and it is the variant that makes the compression
# question answerable. The pair below differs in exactly one property - whether
# the kernel is a gzip stream - because everything else is held at the stock
# value on both sides. Comparing `raw-noefi` against `gz-fixedsz` instead, which
# is what the earlier set did, changes three things at once (compression,
# text_offset and image_size), and a difference then has three explanations. Both
# of those other two are now known to be dead fields (docs/07), so the
# three-way comparison was never going to be readable anyway - but a pair that
# isolates the one live variable is strictly better than a pair that does not.
log "== gz-noefi variant, the other half of the compression pair"
gzip -9 -c "$OUT/Image-noefi" > "$OUT/Image-noefi.gz"
check_font "$OUT/Image-noefi.gz"
python3 "$MK" --kernel "$OUT/Image-noefi.gz" --ramdisk "$RAMDISK" --dtb "$DTB" \
    --profile stock --cmdline "$CMDLINE" --text-offset 0x80000 \
    -o "$OUT/boot-pstore-gz-noefi.img"

log "== done"
ls -la "$OUT"/boot-pstore*.img

# Every payload gets replayed against ABL's own decision path before anyone is
# asked to flash one. A failing image is not worth a device cycle - the phone
# needs a physical reset to recover from a bad one, and ABL refuses the image
# before any of our code runs, so the screen shows nothing either way. The
# checker exits nonzero if any image fails, which is what makes this a gate
# rather than a printout.
log "== ABL's checks, replayed offline"
python3 "$ROOT/tools/abl-boot-check.py" --dtbo "$DTBO_IMG" "$OUT"/boot-pstore*.img
