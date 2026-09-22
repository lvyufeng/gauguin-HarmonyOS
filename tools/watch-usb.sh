#!/usr/bin/env bash
#
# Watch the host USB stack for a phone appearing, and say what it is when one
# does.
#
# Written because "the phone does not connect" has three completely different
# causes that all look the same from `adb devices`:
#
#   1. nothing reached the host at all      -> cable, port, or the phone is off
#   2. the phone enumerated and went away   -> host controller dropping it
#   3. the phone enumerated and stayed, but
#      adb/fastboot cannot see it           -> udev permissions, or the phone
#                                               is in the wrong mode
#
# Only the kernel log distinguishes them, so this prints the kernel's view live
# and labels each event. Run it, then plug the phone in.
#
#   tools/watch-usb.sh [seconds]      (default: run until Ctrl-C)

set -uo pipefail

DURATION=${1:-0}
start=$(date +%s)

# VIDs worth naming. A phone in fastboot is 18d1:d00d; in adb it is 18d1:4ee7
# (or the vendor's own VID); a Qualcomm SoC in EDL is 05c6:9008.
declare -A VENDOR=(
    [18d1]="Google/Android (adb or fastboot)"
    [2717]="Xiaomi"
    [05c6]="Qualcomm (EDL/diag, or a phone in a raw bootloader state)"
    [0bb4]="HTC"
    [22b8]="Motorola"
    [04e8]="Samsung"
    [1004]="LG"
    [12d1]="Huawei"
    [2a70]="OnePlus"
    [0e8d]="MediaTek (BROM/preloader)"
    [2e04]="Nothing"
    [1d45]="Qualcomm (secondary)"
)

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
bad()  { printf '\033[31m%s\033[0m\n' "$*"; }

say "== host USB state at $(date '+%H:%M:%S')"
for d in /sys/bus/pci/devices/*/; do
    [ "$(cat "$d/class" 2>/dev/null)" = "0x0c0330" ] || continue
    printf '   %s  control=%s  d3cold=%s  status=%s\n' \
        "$(basename "$d")" "$(cat "$d/power/control" 2>/dev/null)" \
        "$(cat "$d/d3cold_allowed" 2>/dev/null)" \
        "$(cat "$d/power/runtime_status" 2>/dev/null)"
done
printf '   usbcore.autosuspend = %s\n' \
    "$(cat /sys/module/usbcore/parameters/autosuspend 2>/dev/null)"

say "== currently attached"
found=0
for d in /sys/bus/usb/devices/*/; do
    n=$(basename "$d")
    [ -f "$d/idVendor" ] || continue
    case "$n" in usb*) continue ;; esac
    vid=$(cat "$d/idVendor")
    found=1
    printf '   %-6s %s:%s  %s\n' "$n" "$vid" "$(cat "$d/idProduct")" \
        "$(cat "$d/product" 2>/dev/null)"
done
[ "$found" = 1 ] || echo "   (nothing but root hubs)"

say "== watching the kernel log; plug the phone in now"
say "   (Ctrl-C to stop)"
echo

sudo journalctl -f -n 0 --no-pager 2>/dev/null |
while IFS= read -r line; do
    t=$(date '+%H:%M:%S')

    if [[ $line =~ New\ USB\ device\ found,\ idVendor=([0-9a-f]{4}),\ idProduct=([0-9a-f]{4}) ]]; then
        vid=${BASH_REMATCH[1]}; pid=${BASH_REMATCH[2]}
        name=${VENDOR[$vid]:-unknown vendor}
        ok "  $t  ENUMERATED  $vid:$pid  <- $name"
        if [ "$vid" = "05c6" ] && [ "$pid" = "9008" ]; then
            bad "        That is EDL mode. The phone is in Qualcomm's emergency"
            bad "        download mode and will not answer adb or fastboot."
        fi
    elif [[ $line =~ USB\ disconnect ]]; then
        warn "  $t  DISCONNECTED"
    elif [[ $line =~ New\ USB\ device\ strings ]]; then
        :
    elif [[ $line =~ (usb\ [0-9]+-[0-9.]+:.*(error|fail|reset|device\ descriptor|unable)) ]]; then
        bad  "  $t  USB ERROR  ${BASH_REMATCH[1]}"
    elif [[ $line =~ (xhci_hcd.*(reset failed|Host not accessible|deregistered|not responding)) ]]; then
        bad  "  $t  CONTROLLER  ${BASH_REMATCH[1]}"
    elif [[ $line =~ new\ (high|full|low|SuperSpeed|SuperSpeed\ Plus)[-]speed\ USB\ device ]]; then
        printf '  %s  %s\n' "$t" "${line#*kernel: }"
    fi

    if [ "$DURATION" -gt 0 ] && [ $(( $(date +%s) - start )) -ge "$DURATION" ]; then
        break
    fi
done

echo
say "== adb / fastboot"
adb devices -l 2>&1 | sed 's/^/   /'
fastboot devices 2>&1 | sed 's/^/   /'
