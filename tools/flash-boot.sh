#!/usr/bin/env bash
#
# Write a P1 payload to `boot`, by whichever route answers.
#
# Why this exists separately from restore-stock-boot.sh: that script is the A/B
# control and it is deliberately pinned to one known-good file, which is what
# makes it a safety net. This one writes whatever it is pointed at, which is the
# opposite requirement, but it keeps the part that makes the restore trustworthy
# - the write is verified by reading the partition back, not by the absence of an
# error message.
#
# The route matters more than it looks. `docs/08` step 1b records that the P2
# image in `boot` appears to wedge ABL's fastboot, and that resetting the phone
# may simply reproduce the wedge. If that is what happens, TWRP is not a fallback
# - it is the only way onto the device, because it does not go through ABL's
# fastboot at all. A runbook whose step 4 says `fastboot flash boot` would then
# have no way to run, which is the gap this closes.
#
# It also inverts the usual advice about transfer size. `fastboot flash` has no
# cancel: killing the client mid-download strands the bootloader until a physical
# reset, and the wedge that started all of this was exactly that. A dropped link
# during `adb push` fails the push and nothing else, and the push can be retried.
# On a phone whose USB port drops its link about once a minute, that asymmetry
# argues for TWRP on large files rather than against it.
#
# Usage:  tools/flash-boot.sh work/out/boot-pstore-raw-noefi.img
#         tools/flash-boot.sh --twrp  <image>     # force the TWRP route
#         tools/flash-boot.sh --fastboot <image>  # force the fastboot route
#
set -u

ROUTE=auto
while [ $# -gt 0 ]; do
    case "$1" in
        --twrp)     ROUTE=twrp ;;
        --fastboot) ROUTE=fastboot ;;
        --auto)     ROUTE=auto ;;
        -h|--help)  sed -n '2,29p' "$0"; exit 0 ;;
        -*)         echo "unknown option: $1" >&2; exit 2 ;;
        *)          break ;;
    esac
    shift
done

IMG="${1:-}"
log() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ -n "$IMG" ] || die "usage: $0 [--twrp|--fastboot] <boot-image>"
[ -f "$IMG" ] || die "no such file: $IMG"

# --- pre-flight 1: is this a boot image at all, before it reaches the phone ---
#
# Cheap on purpose. The deep structural check is tools/check-payload.py, which
# compares the image against the phone's own and is required by the runbook; this
# only catches the case where the wrong kind of file - a .dtb, a raw Image, a log
# - would otherwise be pushed 46 MB to a phone in recovery and written to a
# partition. And this *must* run before the push: discovering it after the
# transfer costs the session its scarce resource.
if ! python3 - "$IMG" <<'PY'
import struct, sys, os
p = sys.argv[1]
d = open(p, 'rb').read(4096)
if d[:8] != b'ANDROID!':
    sys.exit(f"{p}: not an Android boot image (magic is {d[:8]!r}, want b'ANDROID!')")
hdr_ver, page = struct.unpack('<I', d[40:44])[0], struct.unpack('<I', d[36:40])[0]
def u32(o): return struct.unpack('<I', d[o:o+4])[0]
ks, rs, ss = u32(8), u32(16), u32(24)
ds = u32(1648) if hdr_ver >= 2 else u32(1632)
size = os.path.getsize(p)
def pages(n): return (n + page - 1) // page
need = (1 + pages(ks) + pages(rs) + pages(ss) + pages(ds)) * page
if need > size:
    sys.exit(f"{p}: header declares {need} bytes but the file is {size} - truncated")
print(f"   {os.path.basename(p)}")
print(f"   header v{hdr_ver}  page 0x{page:x}  kernel {ks:,}  ramdisk {rs:,}  dtb {ds:,}")
print(f"   {size:,} bytes, data ends at {need:,}")
PY
then
    exit 1
fi

SRC_SHA=$(sha256sum "$IMG" | awk '{print $1}')
SRC_SIZE=$(stat -c%s "$IMG")
log "   sha256 $SRC_SHA"

# The stock image is a full-partition dump and restoring it is a different
# operation with a different guarantee - it is pinned to one known-good file and
# verified against a hash read off the device. Handing it to this script would
# work and would quietly lose that guarantee, so it is redirected instead.
STOCK_SHA=50ef59beb17e75de1e749b7d261eb41a18d9cca025befb3357e43e239de78ef3
if [ "$SRC_SHA" = "$STOCK_SHA" ]; then
    die "this is the stock \`boot\` backup, not a payload.
     Use tools/restore-stock-boot.sh (or --twrp), which pins that file to this
     exact hash and is the A/B control the P2 gate depends on."
fi

# The read-back compares exactly as many bytes as were written. The partition is
# 128 MB (the stock image is a full-partition dump) and a payload is smaller, so
# the rest of the partition keeps whatever the previous image left there. That is
# harmless - ABL is driven by the header, and the stock image's own tail is
# 86 MB of zeros - but it does mean only the written prefix can be verified, so
# that is what is verified.
#
# A payload is page-aligned to the size its own header declares, and that is 2048
# for the `silicon` profile - which leaves it a multiple of 2048 and not of 4096.
# The block device is 4096, so an unaligned write leaves the last block half old
# and half new, and the read-back can then only be compared over a partial block:
# `dd bs=4096 count=$((SIZE/4096))` reads *less* than was written and hashes a
# prefix, which passes whatever the last half-block contains. Previous payloads
# happened to be 4096-aligned and hid this; the instrumented build is 1,140,736
# bytes = 278.5 blocks and it does not.
#
# So the file is padded up to a block with zeros and *that* is what is pushed,
# written and read back. It is a stronger check than truncating the comparison
# would be: the whole written region is verified, the half-block that used to be
# unverifiable is now zeros on both sides, and the tail of the previous image
# stops at the padding instead of surviving underneath this one.
FLASH_SRC=$IMG
if (( SRC_SIZE % 4096 != 0 )); then
    PAD=$(( (SRC_SIZE / 4096 + 1) * 4096 ))
    PAD_BYTES=$(( PAD - SRC_SIZE ))
    tmp=$(mktemp) || die "mktemp failed"
    trap 'rm -f "$tmp"' EXIT
    cp "$IMG" "$tmp"
    dd if=/dev/zero bs=1 count="$PAD_BYTES" >> "$tmp" 2>/dev/null
    FLASH_SRC=$tmp
    SRC_SIZE=$PAD
    log "   padded to $PAD bytes with $PAD_BYTES zero bytes (4096-block)"
fi
FLASH_SHA=$(sha256sum "$FLASH_SRC" | awk '{print $1}')
BLOCKS=$(( SRC_SIZE / 4096 ))

# --- pre-flight 2: pick a route, by talking to the thing we intend to use ------
#
# `fastboot devices` is deliberately not the probe. It reads the USB descriptor
# and keeps succeeding while ABL's fastboot is wedged, which is exactly the
# misleading signal that cost earlier sessions; only `getvar` proves ABL answers.
if [ "$ROUTE" = auto ]; then
    if timeout 15 fastboot getvar product >/dev/null 2>&1; then
        ROUTE=fastboot
    elif adb devices 2>/dev/null | grep -q 'recovery$'; then
        ROUTE=twrp
    else
        cat >&2 <<'EOF'
No route to the phone.

  fastboot: not answering `getvar product`. Note that `fastboot devices` listing
            the phone proves nothing - it only reads the USB descriptor and keeps
            working while ABL's fastboot is wedged.
  TWRP:     no `recovery` device on adb.

The TWRP route needs the phone in recovery: hold Power + Volume Up until TWRP
appears. That path does not go through ABL's fastboot, which is why it is the one
that works when fastboot has stranded itself.
EOF
        exit 2
    fi
fi
log "== route: $ROUTE"

case "$ROUTE" in
fastboot)
    # --- pre-flight 3a: confirm ABL is really answering before a 46 MB write ---
    log "== probing ABL"
    timeout 20 fastboot getvar product || die "ABL stopped answering between the
     probe and the flash. Do not retry immediately - a fastboot transfer that is
     interrupted strands the bootloader. Reset the phone (Power ~20s) first."
    log "== flashing $IMG -> boot"
    fastboot flash boot "$IMG" || die "flash failed"
    # No read-back here on purpose: `fastboot flash` verifies its own transfer,
    # and the way to read the partition back is TWRP, which is the other route.
    ;;

twrp)
    # --- pre-flight 3b: the partition path must exist before the push ---------
    #
    # The stock-restore script pushes 128 MB and then finds out. Doing that to a
    # 46 MB payload over a port that drops its link once a minute is the wrong
    # order: if /dev/block/by-name/boot is not there, the answer is available for
    # free before the transfer.
    log "== checking the partition path in TWRP"
    byname=$(adb shell 'ls -l /dev/block/by-name/boot' 2>&1 | tr -d '\r')
    case "$byname" in
        *"by-name/boot"*) printf '   %s\n' "$byname" ;;
        *) die "TWRP cannot see /dev/block/by-name/boot:
     $byname
   Do not guess a raw block device. Check that TWRP mounted /dev/block and that
   this is the recovery you expect." ;;
    esac

    log "== pushing $IMG to /tmp on the device"
    adb push "$FLASH_SRC" /tmp/payload.img || die "push failed (retryable - nothing on the
     device was written; a dropped USB link fails the push and only the push)"
    log "== writing with dd ($BLOCKS x 4096 bytes)"
    adb shell "dd if=/tmp/payload.img of=/dev/block/by-name/boot bs=4096 conv=notrunc" \
        || die "dd failed"
    adb shell 'rm -f /tmp/payload.img'

    # Read back with dd rather than `head -c`: the toybox in a recovery image does
    # not reliably have `head -c`, and this is the one moment where a missing
    # coreutil would be discovered with the partition already overwritten.
    log "== reading back"
    dev=$(adb shell "dd if=/dev/block/by-name/boot bs=4096 count=$BLOCKS 2>/dev/null | sha256sum" \
          | awk '{print $1}')
    if [ "$dev" != "$FLASH_SHA" ]; then
        die "read-back mismatch - the write did not land:
     device: $dev
     source: $FLASH_SHA
   Do NOT reboot into this image. Re-run this script; if it fails again, restore
   the stock image with tools/restore-stock-boot.sh --twrp."
    fi
    log "   ok  the first $SRC_SIZE bytes of \`boot\` match ${FLASH_SRC#$PWD/}"
    ;;
esac

# Quoted delimiter on purpose. Unquoted, the backticks below are command
# substitution: this block would run `docs/08` and `console-ramoops-0` as
# commands, print "No such file or directory" twice on stderr, and silently drop
# both quoted names from the text - so the one paragraph that says where to read
# the log would be the one that lost the filename. The block it prints is prose,
# not a template, so nothing in it should be expanded.
cat <<'EOF'

Done. Now reboot and watch the screen - `docs/08` step 4a has the graded signal
(the logo never changes / text appears / text keeps changing), and step 4.5 says
how to read the log back afterwards.

One thing to know before the reboot, because it is the part that is easy to get
wrong: **do not flash the next payload until the log has been read.** The ring
holds one boot's worth of console output. What this payload writes stays in DRAM
until something reuses that region, and what reuses it is the next boot's ramoops
init - which is also the only thing that exposes it, by snapshotting it into
`console-ramoops-0`. So a boot that dies *before* that init neither destroys the
log nor makes it readable, and a boot that gets past it both reads the old one and
begins overwriting it. Either way there is exactly one chance to read it, and the
order that gets it is: reboot, read, then flash. `docs/08` step 4.5.
EOF
