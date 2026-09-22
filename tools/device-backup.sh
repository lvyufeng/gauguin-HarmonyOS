#!/usr/bin/env bash
# Back up every partition of the gauguin test phone to ./images/
# The Smartisan R2 port on this device has no public image, so this is the
# only copy that will ever exist. Run with the phone connected and rooted.
set -uo pipefail

OUT="$(cd "$(dirname "$0")" && pwd)/images"
mkdir -p "$OUT"
LOG="$OUT/../backup.log"
: > "$LOG"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

adb_wait() {
  adb wait-for-device
  local t=0
  until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
    sleep 2; t=$((t+2)); [ $t -gt 120 ] && break
  done
}

# Whole-LUN dump: captures the partition table as well as the payload.
dump_lun() {
  local lun="$1" size_kb="$2"
  local f="$OUT/LUN-${lun}.img"
  [ -s "$f" ] && [ "$(stat -c%s "$f")" -eq $((size_kb*1024)) ] && { log "skip LUN $lun (done)"; return; }
  log "LUN $lun -> $(basename "$f") ($((size_kb/1024)) MB)"
  adb exec-out "su -c 'dd if=/dev/block/$lun bs=1048576 2>/dev/null'" > "$f" || true
  log "  got $(stat -c%s "$f") bytes"
}

# Named-partition dump, used for the big sda LUN where we skip userdata.
dump_part() {
  local name="$1"
  local f="$OUT/part-${name}.img"
  [ -s "$f" ] && { log "skip $name (done, $(stat -c%s "$f") B)"; return; }
  log "part $name"
  adb exec-out "su -c 'dd if=/dev/block/by-name/$name bs=1048576 2>/dev/null'" > "$f" || true
  log "  got $(stat -c%s "$f") bytes"
}

# --- GPT headers for the big LUN so the table is restorable ---
dump_gpt() {
  local lun="$1"
  local f="$OUT/GPT-${lun}.bin"
  [ -s "$f" ] && return
  log "GPT of $lun"
  adb exec-out "su -c 'dd if=/dev/block/$lun bs=512 count=34 2>/dev/null'" > "$f" || true
}

log "=== gauguin partition backup starting ==="
adb_wait
log "device: $(adb shell getprop ro.product.board | tr -d '\r')  root: $(adb shell 'su -c id' 2>/dev/null | tr -d '\r')"

# Small firmware LUNs: whole-LUN dumps (b=xbl, c=xblbak, d=cdt/ddr, f=fsg/modemst)
dump_lun sdb 16384
dump_lun sdc 16384
dump_lun sdd 32768
dump_lun sdf 28672
# sde is the 512 MB firmware LUN: abl, boot, dtbo, modem, dsp, tz, vbmeta*
dump_lun sde 1048576

# sda holds the OS. Everything except userdata (sda35) is small and precious.
dump_gpt sda
while read -r dev name; do
  case "$name" in
    userdata) continue ;;   # 107 GB, backed up separately
  esac
  case "$dev" in
    /dev/block/sda*) dump_part "$name" ;;
  esac
done < <(adb shell "su -c 'ls -l /dev/block/by-name/'" 2>/dev/null | tr -d '\r' | awk '{print $NF, $(NF-2)}')

log "=== phase 1 complete: $(du -sh "$OUT" | cut -f1) in $OUT ==="
ls -la "$OUT" | tail -5
