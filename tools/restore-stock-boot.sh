#!/usr/bin/env bash
#
# Put the stock `boot` image back. This is the A/B control for the P2 gate.
#
# The logic: our firmware is in `boot` and the phone comes up in fastboot
# instead of running it. That is consistent with two very different worlds —
#   (a) our image is refused or crashes, the phone falls through to fastboot; or
#   (b) something else about the device changed, and *any* `boot` would fail.
# Flashing the stock image back separates them. Stock boots Android => (a), the
# problem is ours. Stock also fails => (b), and nothing about the payload is
# implicated. Either way it is one command instead of an argument, and it also
# happens to leave the phone in a good state.
#
# It refuses to write anything until it has verified the backup against the
# hash recorded when the backup was taken, because the whole value of the
# control is that the file written is known-good.
#
# Two routes, because they fail independently:
#   fastboot   - normal, but needs ABL's fastboot to be answering
#   twrp       - Power + Volume Up, then this script; does not go through ABL
#
# Usage:  tools/restore-stock-boot.sh            # auto-detects the route
#         tools/restore-stock-boot.sh --twrp     # force the TWRP route
#         tools/restore-stock-boot.sh --check    # verify the backup only
#
# An unrecognised argument is refused rather than ignored. This script's whole
# value is that running it is safe, and the way that stops being true is a typo:
# `--dryrun` or `--chek` used to fall through to the auto route and write the
# partition. Nothing here writes until the backup has been verified, and the
# file written is the right one either way, but "restore stock" is not something
# to enter by accident.
set -u

BACKUP="${BACKUP:-$HOME/backup/gauguin/images/part-boot.img}"
# sha256 read off the device immediately before the first write, 2026-09-22.
EXPECT="${EXPECT:-50ef59beb17e75de1e749b7d261eb41a18d9cca025befb3357e43e239de78ef3}"
ROUTE=auto
while [ $# -gt 0 ]; do
    case "$1" in
        --twrp)    ROUTE=twrp ;;
        --check)   ROUTE=check ;;
        --auto|"") ;;
        -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
        *)         echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

log() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# --- the backup must be the known-good one, or the control is meaningless ---
log "== verifying the backup"
[ -f "$BACKUP" ] || die "no backup at $BACKUP"
got=$(sha256sum "$BACKUP" | awk '{print $1}')
if [ "$got" != "$EXPECT" ]; then
    die "backup hash mismatch
     file:   $got
     wanted: $EXPECT
   Do not write this file to the phone."
fi
log "   ok  $BACKUP  ($(stat -c%s "$BACKUP") bytes)"
[ "$ROUTE" = check ] && exit 0

# --- pick a route -----------------------------------------------------------
if [ "$ROUTE" = auto ]; then
    if timeout 15 fastboot getvar product >/dev/null 2>&1; then
        ROUTE=fastboot
    elif adb devices 2>/dev/null | grep -q 'recovery$'; then
        ROUTE=twrp
    else
        cat >&2 <<'EOF'
No route to the phone.

  fastboot: not answering. Note that the device being *listed* by `lsusb` or
            `fastboot devices` proves nothing - both read the USB descriptor
            and keep working while ABL's fastboot is wedged.
  TWRP:     not reachable over adb.

For the TWRP route: hold Power + Volume Up until it appears, then re-run this
script. For the fastboot route: hold Power ~20s to reset, let it come back to
fastboot, then re-run.
EOF
        exit 2
    fi
fi
log "== route: $ROUTE"

# --- write ------------------------------------------------------------------
case "$ROUTE" in
fastboot)
    # `fastboot flash` is given the whole 128 MB partition image; ABL handles
    # the size. Nothing here is piped, and no timeout is allowed to kill it
    # mid-transfer - an aborted fastboot transfer strands ABL until a reset.
    log "== flashing $BACKUP -> boot"
    fastboot flash boot "$BACKUP" || die "flash failed"
    ;;
twrp)
    log "== pushing to /tmp on the device"
    adb push "$BACKUP" /tmp/part-boot.img || die "push failed"
    log "== writing with dd"
    adb shell 'dd if=/tmp/part-boot.img of=/dev/block/by-name/boot bs=4096 conv=notrunc' \
        || die "dd failed"
    adb shell 'rm -f /tmp/part-boot.img'

    # Read back with dd, not `head -c`. `head -c` is not reliably present in
    # the toybox that ships in a recovery image, and this is the one place in
    # the project where a missing coreutil would be discovered at the worst
    # possible moment - phone in recovery, partition just overwritten. dd is
    # in every recovery build. The block count is derived from the file size
    # rather than hardcoded, so a re-cut backup still verifies.
    size=$(stat -c%s "$BACKUP")
    [ $((size % 4096)) -eq 0 ] || die "backup size $size is not a multiple of 4096"
    blocks=$((size / 4096))
    log "== reading back ($blocks x 4096 bytes)"
    dev=$(adb shell "dd if=/dev/block/by-name/boot bs=4096 count=$blocks 2>/dev/null | sha256sum" \
          | awk '{print $1}')
    [ "$dev" = "$EXPECT" ] || die "read-back mismatch:
     device: $dev
     wanted: $EXPECT
   The write did not land. Do NOT reboot into the firmware - re-run this."
    log "   ok  device boot partition matches the backup"
    ;;
esac

# Quoted delimiter: the backticks in this block are markdown, not substitution.
# Unquoted, this line runs tools/fastboot-capture.sh as a command the moment the
# block renders - at the end of a restore, unasked, while the phone is rebooting.
cat <<'EOF'

Done. Now reboot and watch: the phone should boot Android normally.

  * Android boots  -> our firmware is the problem. The P2 work continues on the
                      payload, and this also confirms the device is healthy.
  * Android does not boot -> something other than our image changed state
                      (misc BCB, unlock state, userdata). The payload is not
                      implicated and the search moves there.

Either answer is worth having. `tools/fastboot-capture.sh` and
`docs/07-uefi-platform.md` are the next things to read.
EOF
