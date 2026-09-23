#!/usr/bin/env bash
#
# Get the bootloader's log off the phone, by whichever route answers.
#
# Why this is separate from tools/fastboot-capture.sh: that script needs ABL's
# fastboot to be answering, and the state this device keeps ending up in is one
# where it is not. The log does not need ABL. It is written to the `logfs`
# partition, which is a FAT12 volume with five 32 KiB text files in it
# (tools/read-logfs.py has the format), so anything that can read a block device
# can get it - TWRP, or Android with root - and the P0 backup already has a copy
# from before any payload was ever flashed.
#
# Three routes, tried in order, each cheap:
#
#   fastboot  `oem uefilog` - the only route that reads ABL's *live* log buffer
#             rather than the partition, so it is the only one that can show a
#             boot which never reached the shutdown that writes the file. It
#             needs ABL answering, so it is tried first and abandoned fast.
#   adb       `dd` the logfs partition and pull it. Works from TWRP (which does
#             not go through ABL at all) and from Android with `su`. `su` is
#             tried, then plain adb, so a recovery build without root still has
#             a chance.
#   backup    ~/backup/gauguin/images/part-logfs.img, dumped in P0. Not the boot
#             you are chasing, but it is the baseline a failed boot is read
#             against, and it is the only route that always works.
#
# Every fastboot command redirects to a file rather than a pipe: a killed client
# mid-response strands ABL's fastboot until a physical reset, which is how this
# device got into its current state in the first place.
#
# Usage:  tools/pull-bootloader-log.sh [outdir]
#         FASTBOOT_TIMEOUT=5 tools/pull-bootloader-log.sh
#
set -u

OUT="${1:-work/bllog-$(date +%Y%m%d-%H%M%S)}"
FASTBOOT_TIMEOUT="${FASTBOOT_TIMEOUT:-12}"
BACKUP="${BACKUP:-$HOME/backup/gauguin/images/part-logfs.img}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$OUT"

log()  { printf '\033[1m%s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

GOT=""

# --- 1. fastboot: ABL's live log buffer --------------------------------------
if have fastboot; then
    log "== fastboot: probing ABL (getvar product, ${FASTBOOT_TIMEOUT}s)"
    if timeout "$FASTBOOT_TIMEOUT" fastboot getvar product \
            >"$OUT/00-probe.txt" 2>&1; then
        log "   answering - reading the live log"
        # `oem uefilog` dumps the logfs files. No pipe, no timeout: a client
        # killed mid-response is what strands ABL.
        if fastboot oem uefilog >"$OUT/uefilog.txt" 2>&1; then
            bytes=$(stat -c%s "$OUT/uefilog.txt")
            if [ "$bytes" -gt 512 ]; then
                log "   ok  $OUT/uefilog.txt ($bytes bytes)"
                GOT="$OUT/uefilog.txt"
            else
                log "   answered but returned $bytes bytes"
                sed 's/^/     /' "$OUT/uefilog.txt"
            fi
        else
            log "   'oem uefilog' failed (rc=$?) - see $OUT/uefilog.txt"
        fi
    else
        log "   ABL did not answer; the logfs partition does not need it."
        log "   Run tools/unwedge-fastboot.py to classify the silence."
    fi
fi

# --- 2. adb: read the partition ----------------------------------------------
# TWRP comes up as `recovery` and is root already; Android comes up as `device`
# and needs `su`. Both end up at the same dd. Both states have to be accepted
# here: TWRP is the route this script exists for - it is the one that works when
# ABL's fastboot is wedged - and a check that only accepted `device` would skip
# it and fall through to the backup while reporting success at having tried.
if [ -z "$GOT" ] && have adb && adb devices 2>/dev/null | grep -qE '(device|recovery)$'; then
    log "== adb: reading /dev/block/by-name/logfs"
    shell() {
        # root if the device offers it, plain otherwise - a recovery build that
        # is already root prints "not found" for `su` and that is not an error.
        if adb shell 'command -v su >/dev/null 2>&1' 2>/dev/null; then
            adb shell "su -c '$1'" 2>/dev/null || adb shell "$1"
        else
            adb shell "$1"
        fi
    }
    if shell 'dd if=/dev/block/by-name/logfs of=/tmp/logfs.img bs=4096' \
            >/dev/null 2>&1; then
        if adb pull /tmp/logfs.img "$OUT/logfs.img" >/dev/null 2>&1; then
            shell 'rm -f /tmp/logfs.img' >/dev/null 2>&1
            log "   ok  $OUT/logfs.img ($(stat -c%s "$OUT/logfs.img") bytes)"
            GOT="$OUT/logfs.img"
        else
            log "   pull failed; the device may not expose /tmp"
        fi
    else
        log "   dd failed - no root, or no such partition"
    fi
fi

# --- 3. the P0 backup --------------------------------------------------------
if [ -z "$GOT" ] && [ -f "$BACKUP" ]; then
    log "== backup: $BACKUP"
    log "   This is the P0 dump, so it predates any payload. Useful as the"
    log "   baseline a failed boot is read against, not as the failed boot."
    cp "$BACKUP" "$OUT/logfs.img"
    GOT="$OUT/logfs.img"
fi

if [ -z "$GOT" ]; then
    # Quoted delimiter: nothing in this prose should ever be run as a command.
    cat >&2 <<'EOF'
No route to the log.

  fastboot: not answering (it may still be *listed* by lsusb - that proves
            nothing; the descriptor is served while ABL's thread is stuck)
  adb:      no device
  backup:   the P0 logfs dump is not where this script looked for it
EOF
    echo "            (it looked at $BACKUP)" >&2
    cat >&2 <<'EOF'

Hold Power ~20s to reset, or Power + Volume Up for TWRP, and re-run.
EOF
    exit 2
fi

# --- read it -----------------------------------------------------------------
log "== reading $GOT"
python3 "$ROOT/tools/read-logfs.py" "$GOT" -o "$OUT/slots" 2>&1 | sed 's/^/   /'
log "== done  ($OUT)"
