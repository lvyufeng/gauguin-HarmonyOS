#!/usr/bin/env bash
#
# One-shot capture of everything ABL can be made to tell us, run the moment the
# phone is back in fastboot.
#
# Why this exists: diagnosing a silent boot failure on a phone with no UART
# costs one physical reset per attempt, because a stranded fastboot needs the
# power button and nobody is standing next to the device. So the expensive
# resource is *device time in a responsive state*, and this script spends it in
# one go rather than one question at a time.
#
# Two rules it follows, both learned the hard way:
#
#   1. Every `fastboot` command redirects to a file. Never a pipe. `getvar all
#      | head -45` kills the client mid-response and strands ABL's fastboot
#      until a power-button reset; the same thing happened with an aborted
#      128 MB transfer. If a later command stops answering, the earlier ones are
#      still on disk.
#
#   2. It checks that ABL is answering before spending time on commands that
#      need it. `fastboot devices` is NOT that check - it reads the USB
#      descriptor and keeps working while ABL is wedged, which makes it a
#      convincing lie.
#
# Usage:  tools/fastboot-capture.sh [outdir]
#
set -u

OUT="${1:-work/fb-capture-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
PROBE_TIMEOUT=20

log() { printf '\033[1m%s\033[0m\n' "$*"; }

# --- 1. Is ABL actually talking? ------------------------------------------
# `getvar product` is the cheapest command that needs a reply from ABL. A
# timeout here means the device needs a physical reset and nothing below will
# work, so say that rather than dumping six timeouts.
log "== probing ABL (getvar product, ${PROBE_TIMEOUT}s)"
if ! timeout "$PROBE_TIMEOUT" fastboot getvar product >"$OUT/00-probe.txt" 2>&1; then
    echo "ABL did NOT answer."
    echo
    echo "  The USB device may well still be listed - that proves nothing:"
    echo "    $(lsusb 2>/dev/null | grep -i 18d1 || echo '(not listed either)')"
    echo
    echo "  Hold the power button ~20s until the phone restarts. It needs a"
    echo "  physical reset; a host-side port reset does not clear it."
    exit 2
fi
log "ABL is answering:"
sed 's/^/    /' "$OUT/00-probe.txt"

# --- 2. Which fastboot are we in? ----------------------------------------
# is-userspace:no means ABL's own fastboot, which is where the boot decision is
# made. If it ever says yes we are in fastbootd and none of this applies.
run() {  # run <filename> <fastboot args...>
    local f="$OUT/$1"; shift
    timeout 30 fastboot "$@" >"$f" 2>&1
    local rc=$?
    printf '  %-28s rc=%-3s %s\n' "$1" "$rc" "$(wc -c <"$f") bytes"
    return 0
}

log "== ABL's own logs (the part that matters)"
# These are the three windows ABL gives into what it did. On a phone with no
# UART they are the only feedback channel there is, and they were added to
# fastboot by Qualcomm precisely for boards like this one.
run 10-uefilog.txt  oem uefilog
run 11-lkmsg.txt    oem lkmsg
run 12-lpmsg.txt    oem lpmsg

log "== device state"
run 20-device-info.txt oem device-info
run 21-getvar-all.txt  getvar all

# --- 3. Summarise anything that looks like a decision --------------------
log "== boot-path lines found"
PATTERN='bootstats|kernel load|image header|magic|dtb|decompress|avb|verified|authen|unbootable|bootable|slot|boot reason|fastboot mode|cmdline|load address|kernel size'
if grep -rhiE "$PATTERN" "$OUT"/1*.txt 2>/dev/null; then
    :
else
    echo "  (nothing - see $OUT, and consider that the log may be empty)"
fi

log "== wrote $OUT"
ls -la "$OUT"
cat <<'EOF'

How to read the result:

  * "BootStats: ID-n: Kernel Load Start" with no matching "Kernel Load Done"
    means ABL began loading `boot` and stopped - the failure is in the image,
    and the next string in the log says which check it failed.

  * No BootStats at all means ABL never reached its boot path. That is a
    different problem: something before it (a BCB, a key, a slot decision)
    routed to fastboot, and the payload is not implicated at all.
EOF
