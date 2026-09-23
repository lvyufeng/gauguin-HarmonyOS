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
# A fourth cause was added after it cost a whole session, and it is why this now
# opens with the port registers rather than with the device list:
#
#   0. nothing is electrically on any port at all, and the watch below would sit
#      silent forever without saying so
#
# The registers answer that one directly - see the note above them - and the same
# section reports a controller being torn down at the PCIe layer, which produces
# no USB event and is therefore invisible to the rest of this script.
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

# Is anything electrically there at all? This has to be read from the xHCI port
# registers rather than from the USB device list, because the two answers are
# different questions and only one of them is about the phone.
#
# A port reading `Connected` means the device pulled up D+ and the host can see
# it. `Not-connected Link:RxDetect` means the host is looking for a receiver and
# finding none - which is what an empty port looks like AND what a phone looks
# like when its USB device controller is not running: powered off, or sitting in
# a boot loop whose payload has no USB stack in it. So "the phone is plugged in
# and nothing sees it" and "the port is empty" are the same reading here, and
# that is worth knowing before swapping cables for an hour.
#
# The other thing this catches is a controller that is flapping at the PCIe
# layer rather than the USB layer. `pciehp: Slot(N): Link Down` / `Card not
# present` produces no USB device event at all, so the event watch below would
# sit silent while the port underneath it was being torn down and rebuilt. On
# this machine that is 0000:6c:00.0 behind root port 00:1c.4 - the Type-C port.
# See docs/06-host-usb.md.
say "== port registers: what is electrically present"
# `-d` is not enough here: /sys/kernel/debug is root-only (drwx------), so the
# test fails for the user even when debugfs is mounted. Ask as root instead.
if ! sudo ls /sys/kernel/debug/usb/xhci >/dev/null 2>&1; then
    sudo mount -t debugfs none /sys/kernel/debug 2>/dev/null
fi
if ! sudo ls /sys/kernel/debug/usb/xhci >/dev/null 2>&1; then
    warn "   (debugfs not mounted; run: sudo mount -t debugfs none /sys/kernel/debug)"
else
    for c in $(sudo ls /sys/kernel/debug/usb/xhci/ 2>/dev/null | sort); do
        ports=$(sudo ls /sys/kernel/debug/usb/xhci/"$c"/ports/ 2>/dev/null | wc -l)
        conn=0
        for p in $(sudo ls /sys/kernel/debug/usb/xhci/"$c"/ports/ 2>/dev/null | sort); do
            line=$(sudo head -1 /sys/kernel/debug/usb/xhci/"$c"/ports/"$p"/portsc 2>/dev/null)
            case "$line" in
                *Not-connected*) ;;
                *) conn=$((conn + 1))
                   printf '   %s %s  %s\n' "$c" "$p" "$line" ;;
            esac
        done
        if [ "$conn" = 0 ]; then
            warn "   $c  $ports ports, none connected  <- nothing is presenting"
        else
            ok   "   $c  $conn of $ports ports connected"
        fi
    done
fi

# The PCIe-layer flap. The window matters and a cumulative count would be worse
# than useless here: this box has been up for a day and a half, and a total over
# that span says nothing about whether the port is flapping *now*. Measured over
# the last five minutes, the answer is stable and reproducible - nine or ten
# events per minute, every minute, one every 6.5 seconds. `journalctl -k` is the
# source rather than `dmesg`, because the kernel ring buffer drops messages and a
# one-minute `dmesg | grep -c` sample here has read `1` while the port was
# dropping its link nine times in that same minute.
flaps=$(sudo journalctl -k --since "5 min ago" --no-pager 2>/dev/null |
        grep -c 'pciehp: Slot.*Link Down')
if [ -z "$flaps" ]; then
    flaps=$(sudo dmesg 2>/dev/null | grep -c 'pciehp: Slot.*Link Down')
fi
if [ "${flaps:-0}" -gt 0 ]; then
    last=$(sudo journalctl -k --since "5 min ago" --no-pager 2>/dev/null |
           grep 'pciehp: Slot.*Link Down' | tail -1)
    bad "   PCIe link flapping: $flaps Link Down in the last 5 min"
    bad "   last: ${last#*kernel: }"
    bad "   that port cannot be used - move the cable to a chipset USB-A port"
elif [ "${flaps:-0}" = 0 ] && sudo journalctl -k --since "5 min ago" --no-pager 2>/dev/null | grep -q 'pciehp'; then
    warn "   no PCIe link-down in the last 5 min, but pciehp is still talking;"
    warn "   treat that port as suspect - see docs/06-host-usb.md"
else
    ok "   no PCIe link-down in the last 5 min"
fi

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
        if [ "$vid" = "1d6b" ]; then
            # 1d6b is the Linux Foundation, i.e. the host's own root hub coming
            # back after its controller was reset. Printing this as ENUMERATED
            # would be the worst possible false positive here: it is the failing
            # controller announcing itself, and it reads exactly like a phone
            # arriving.
            warn "  $t  ROOT HUB BACK  $vid:$pid  <- the host's own hub, not a device"
        else
            ok "  $t  ENUMERATED  $vid:$pid  <- $name"
        fi
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
