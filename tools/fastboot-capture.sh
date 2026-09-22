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
    # Which wedge it is decides whether the reset is actually needed, and this
    # is the only cheap way to ask. See tools/unwedge-fastboot.py for why EP0
    # answering while the bulk endpoints are unarmed means the fastboot thread
    # is stuck rather than merely blocked on us.
    timeout 60 python3 "$(dirname "$0")/unwedge-fastboot.py" \
        --log "$OUT/01-wedge.txt" 2>&1 | sed 's/^/  /'
    echo
    echo "  Hold the power button ~20s until the phone restarts. It needs a"
    echo "  physical reset; a host-side port reset does not clear it."
    exit 2
fi
log "ABL is answering:"
sed 's/^/    /' "$OUT/00-probe.txt"

# helper: run a fastboot command, capture to a file, report which file it wrote.
# The label is the output file name, not the subcommand: with `oem uefilog` and
# friends the subcommand is just "oem" three times in a row, which makes the
# progress output useless for telling which capture is which.
run() {  # run <filename> <fastboot args...>
    local f="$OUT/$1"; shift
    timeout 30 fastboot "$@" >"$f" 2>&1
    local rc=$?
    printf '  %-28s rc=%-3s %s\n' "$(basename "$f")" "$rc" "$(wc -c <"$f") bytes"
    return 0
}

# --- 2. WHY is it in fastboot? This is the one that answers the question ----
# `oem fbreason` is an ABL command that reports why the device entered fastboot.
# ABL's string table shows the complete set of answers it can give, in a block
# of five strings plus a raw code, and they discriminate exactly the cases that
# have been indistinguishable so far:
#
# The handler indexes a pointer table at VA 0xc01b0 with a small integer from a
# global at VA 0xc3000, whose default is 4 - so the full answer set is:
#
#   code 0   Reason:Down Key Press          (see the caveat in the EOF notes)
#   code 1   Reason:Reboot Bootloader
#   code 2   Reason:LoadImageAndAuth Fail   ABL TRIED to load `boot`
#   code 3   Reason:BootLinux Fail          loaded, then failed
#   code >3  Reason:Unknown                 the default, i.e. nothing set it
#
# Code 2 or 3 would confirm the payload was reached, which is the thing that has
# been unknown since the first attempt. Asking this first costs one round trip.
log "== why did it enter fastboot? (oem fbreason)"
run 05-fbreason.txt oem fbreason
sed 's/^/    /' "$OUT/05-fbreason.txt"

log "== ABL's own logs (the part that matters)"
# These are the three windows ABL gives into what it did. On a phone with no
# UART they are the only feedback channel there is, and they were added to
# fastboot by Qualcomm precisely for boards like this one.
run 10-uefilog.txt  oem uefilog
run 11-lkmsg.txt    oem lkmsg
run 12-lpmsg.txt    oem lpmsg

log "== device state"
# is-userspace:no in the getvar dump means this is ABL's own fastboot, which is
# where the boot decision is made. If it ever says yes we are in fastbootd and
# none of the boot-path reading applies.
run 20-device-info.txt oem device-info
run 21-getvar-all.txt  getvar all

# --- 3. Is the boot slot marked unbootable? --------------------------------
# ABL contains "Non Multi-slot: Unbootable entering fastboot mode" and this
# device reports no current-slot, so it IS the non-multi-slot case: if ABL has
# judged the boot slot unbootable it goes to fastboot, and a failed boot can be
# what sets that. These two names come from ABL's own getvar handler, so asking
# for them is not a guess.
log "== boot slot state (ABL has a non-multislot 'unbootable -> fastboot' path)"
for v in slot-unbootable slot-retry-count slot-count current-slot; do
    timeout 20 fastboot getvar "$v" >"$OUT/30-$v.txt" 2>&1
    printf '  %-20s %s\n' "$v" "$(tr '\n' ' ' <"$OUT/30-$v.txt")"
done

log "== boot-path lines found"
PATTERN='bootstats|kernel load|image header|magic|dtb|decompress|avb|verified|authen|unbootable|bootable|slot|boot reason|fastboot mode|cmdline|load address|kernel size'
if grep -rhiE "$PATTERN" "$OUT"/1*.txt "$OUT"/2*.txt "$OUT"/30-*.txt 2>/dev/null; then
    :
else
    echo "  (nothing - see $OUT, and consider that the log may be empty)"
fi

log "== wrote $OUT"
ls -la "$OUT"
cat <<'EOF'

How to read the result:

  * `oem fbreason` (step 2, the first thing asked) is the closest thing to a
    direct answer. Read it against ABL's own five possible replies:

      Reason:Down Key Press           a button was held - BUT see the caveat
      Reason:Reboot Bootloader        something asked for fastboot.
      Reason:LoadImageAndAuth Fail    ABL TRIED to load `boot` and could not.
      Reason:BootLinux Fail           it loaded, and failed after that.
      Reason:Unknown                  nothing set it (it is the default).

    "LoadImageAndAuth Fail" or "BootLinux Fail" both mean the payload was
    reached, which is the thing that has been unknown since the first attempt.

    Caveat on "Down Key Press": the only writer of that variable found in ABL
    stores 0 when the POWER-ON reason is 2 or 8, so this value can be produced
    by how the phone was restarted rather than by anything our firmware did.
    Power on with no keys held before trusting it.

    "Unknown" is the default value (4), not a failure to determine anything -
    it means no fastboot-reason path was taken.

  * "BootStats: ID-n: Kernel Load Start" with no matching "Kernel Load Done"
    means ABL began loading `boot` and stopped - the failure is in the image,
    and the next string in the log says which check it failed.

  * No BootStats at all means ABL never reached its boot path. That is a
    different problem: something before it (a BCB, a key, a slot decision)
    routed to fastboot, and the payload is not implicated at all.
EOF

# A third reading, added after the ABL payload was disassembled properly:
#
#   slot-unbootable:yes (or "Non Multi-slot: Unbootable" in a log) means ABL
#   has marked the boot slot unbootable and is going to fastboot on purpose.
#   This device has no A/B slots, so it is the non-multislot case, and a failed
#   boot can be what sets the flag. In that situation the payload may well have
#   been attempted - the fastboot is a consequence, not a refusal.
#
#   To check whether ABL wrote that flag, read `misc` from TWRP (Power + Volume
#   Up) and compare with the all-zeros it held before the write:
#       adb shell dd if=/dev/block/by-name/misc bs=4096 count=1 | od -c | head
