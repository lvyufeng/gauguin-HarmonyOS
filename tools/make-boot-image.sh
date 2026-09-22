#!/usr/bin/env bash
#
# Assemble the Android boot image and load it onto the device.
#
# `fastboot boot` downloads the image into RAM and boots it without writing
# anything to the phone's storage. That is the whole reason this project can be
# developed against a device holding the only copy of its own ROM: until P4,
# nothing on the device is ever modified.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=$REPO/work/out
CMD=${CMD:-}

usage() {
    cat <<EOF
usage: $(basename "$0") [--flash|--boot-only|--capture]

  (default)     build boot.img and \`fastboot boot\` it
  --flash       \`fastboot flash boot\` instead - WRITES TO THE DEVICE, needs P0 backups
  --boot-only   build boot.img but do not touch the device
  --capture     after booting, grab the console off the screen every 20s to console/

Console output is the framebuffer, because this phone exposes no UART. That
means the only feedback channel is the screen itself.
EOF
    exit 1
}

MODE=boot
case "${1:-}" in
    --flash)     MODE=flash ;;
    --boot-only) MODE=build ;;
    --capture)   MODE=capture ;;
    "")          ;;
    *)           usage ;;
esac

for f in Image.gz initramfs.cpio.gz sm7225-xiaomi-gauguin.dtb; do
    [ -f "$OUT/$f" ] || { echo "missing $OUT/$f - run tools/build-kernel.sh first" >&2; exit 1; }
done

# console=tty0 puts the kernel log on the framebuffer, which the bootloader has
# already brought up and left at the address uefiplat.cfg calls "Display
# Reserved". fbcon's larger font is what makes the result legible in a photo.
CMDLINE="console=tty0 loglevel=8 ignore_loglevel fbcon=font:TER16x32 \
rdinit=/init panic=10 ${CMD}"

echo "== assembling boot.img"
python3 "$REPO/tools/mkbootimg.py" \
    --kernel "$OUT/Image.gz" \
    --ramdisk "$OUT/initramfs.cpio.gz" \
    --dtb "$OUT/sm7225-xiaomi-gauguin.dtb" \
    --cmdline "$CMDLINE" \
    --out "$OUT/boot.img"

ls -l "$OUT/boot.img"

if [ "$MODE" = build ]; then
    echo "== --boot-only: not touching the device"
    exit 0
fi

adb wait-for-device
echo "== rebooting to bootloader"
adb reboot bootloader
sleep 5
fastboot devices

case "$MODE" in
    boot)
        echo "== fastboot boot (nothing is written to the device)"
        fastboot boot "$OUT/boot.img"
        ;;
    flash)
        echo "!! writing to the boot partition"
        echo "!! restore path: fastboot flash boot ~/backup/gauguin/images/part-boot.img"
        read -r -p "type 'yes' to continue: " ans
        [ "$ans" = yes ] || exit 1
        fastboot flash boot "$OUT/boot.img"
        fastboot reboot
        ;;
    capture)
        echo "== booting, then capturing the screen"
        fastboot boot "$OUT/boot.img"
        mkdir -p "$REPO/work/console"
        # The device will not be visible to adb while mainline is running, so
        # this only works once it has rebooted back into Android.
        ;;
esac

echo "== done. The kernel log is on the phone's screen."
