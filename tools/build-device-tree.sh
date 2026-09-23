#!/usr/bin/env bash
#
# Build the one device tree every payload carries, and refuse to hand over a
# tree that would change what the payload does.
#
# Why this is a script of its own rather than a block inside the P1 builder:
# the tree has two consumers now — the Linux payloads and the UEFI ones — and
# when it had one, the second consumer was built by hand against whatever
# `arch/arm64/boot/dts/qcom/*.dtb` happened to be lying around. It was stale by
# two changes (the ramoops moved to 0xd0000000, and the `/__symbols__` fragment
# that ABL's overlay needs was added) and nothing noticed, because the only
# checker that reads the tree inside a UEFI image is `abl-boot-check.py` and it
# was not being run on those images. A tree built in one place and consumed in
# two cannot drift; a tree built twice can, and did.
#
# The four properties below are the ones a payload cannot survive losing, and
# each has cost a device cycle or would have:
#
#   /__symbols__        >= ~200 entries   - without it ABL's overlay is refused
#                                           and BootLinux never reaches a kernel
#   ramoops nodes       exactly one       - two and which one wins is node order
#   ramoops address     0xd0000000        - anything else is not in DRAM, or is
#                                           inside a no-map carveout
#   /chosen/framebuffer all four props    - the only log channel that needs no
#                                           round trip
#
# Usage:  tools/build-device-tree.sh [-o OUTPUT] [--reuse]
#         DTBO=/path/to/part-dtbo.img to override the dtbo dump.
#
#   --reuse   keep an existing, valid output instead of rebuilding. Used by the
#             P2 builder, which does not touch the kernel tree and so has no
#             reason to make the kernel rebuild the dtbs target.
#
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINUX="$ROOT/work/linux"
OUT="$ROOT/work/out"
DTB_NAME=sm7225-xiaomi-gauguin
DTB="$OUT/$DTB_NAME.dtb"
REUSE=0

while [ $# -gt 0 ]; do
    case "$1" in
        -o|--output) DTB="$2"; shift 2 ;;
        --reuse)     REUSE=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

# The phone's own dtbo partition, dumped by tools/device-dump-helper.sh. It is an
# input to the build, not a reference: the symbols the tree has to declare, and
# therefore whether ABL accepts the payload at all, are read out of it.
DTBO_IMG="${DTBO:-$HOME/backup/gauguin/images/part-dtbo.img}"

log() { printf '\033[1m%s\033[0m\n' "$*"; }

# --- the checks, so --reuse and a fresh build are held to the same bar -------
# Run against $1. Every one of them reads the built blob, never the source: a
# guard that inspects the input cannot see a build that dropped what the input
# said.
validate() {
    local dtb="$1" nsym n prop v

    # The sinks are only a fix if they survived into the built blob; a tree that
    # silently lost them is one ABL refuses, and it looks exactly like the ones
    # that were refused before the fix. Checked as a symbol count rather than
    # trusted, because "the fragment is in the .dts" and "the fragment is in the
    # .dtb" are different claims and only the second one matters.
    nsym=$(fdtget -p "$dtb" /__symbols__ 2>/dev/null | wc -l) || true
    [ "${nsym:-0}" -ge 200 ] \
        || { echo "$dtb carries ${nsym:-0} symbols in /__symbols__, expected the ~217" \
                  "tools/make_dtbo_sinks.py generates - ABL would refuse the overlay" >&2
             return 1; }
    printf '   /__symbols__ %s entries\n' "$nsym"

    # A tree that still carries the inherited 0xffc00000 ramoops, or a second
    # ramoops node, would silently move the log - ramoops holds "only a single
    # ramoops area allowed at a time" and fails extra probes, so which of two
    # nodes wins comes down to node order.
    # The `|| true` is load-bearing, and its absence is the same bug this file
    # already documents one layer up: `grep -c` exits 1 when the count is zero,
    # and under `set -e` that kills the shell at the assignment - so the tree
    # with *no* ramoops node, which is the one that most needs the message
    # below, would abort with nothing printed. A guard whose failure prints
    # nothing is not a guard.
    n=$(dtc -I dtb -O dts -o - "$dtb" 2>/dev/null | grep -c 'ramoops@' || true)
    [ "$n" = 1 ] || { echo "expected exactly 1 ramoops node in $dtb, found $n" >&2; return 1; }
    dtc -I dtb -O dts -o - "$dtb" 2>/dev/null | grep -q 'ramoops@d0000000' \
        || { echo "$dtb has no ramoops@d0000000 - the address is derived from"
                  "docs/p1-cmdline.txt and the board dts, and the two have to agree" >&2
             return 1; }

    # The other channel, and the one that needs no reboot to read: /chosen's
    # simple-framebuffer is what simpledrm binds to and fbcon draws on, so the
    # kernel's own printk lands on the panel the bootloader just used for the
    # logo. Without this node the boot is undiagnosable from the screen - a
    # kernel that works and a kernel that dies in early setup both look like a
    # dead phone. width/height/stride/format are checked too, because the console
    # geometry is computed from them and a wrong stride draws diagonal text.
    #
    # All four are read out of the DTB rather than grepped as text, so a node that
    # exists but is missing a property cannot pass. compatible/format are strings
    # and width/height/stride are single cells.
    local fbnode=/chosen/framebuffer@a0000000 spec propname type
    command -v fdtget >/dev/null || { echo "fdtget not found (package: device-tree-compiler)" >&2; return 1; }
    for spec in compatible:s width:i height:i stride:i format:s; do
        propname=${spec%:*} type=${spec#*:}
        v=$(fdtget -t "$type" "$dtb" "$fbnode" "$propname" 2>/dev/null) \
            || { echo "$dtb: $fbnode has no '$propname' - the panel would stay dark" \
                      "and there is no other channel that needs no round trip" >&2
                 return 1; }
        printf '   framebuffer %-10s %s\n' "$propname" "$v"
    done
}

if [ "$REUSE" = 1 ] && [ -f "$DTB" ]; then
    log "== device tree ($DTB_NAME.dtb), reusing the existing build"
    validate "$DTB"
    printf '   %s\n' "$DTB"
    exit 0
fi

[ -f "$DTBO_IMG" ] || { echo "missing the dtbo dump at $DTBO_IMG (set DTBO=...)." \
    "The device tree needs /__symbols__ for exactly the symbols that partition" \
    "names, so a payload built without it is one ABL refuses with" \
    "\"ApplyOverlay: ufdt apply overlay failed\" and no output." >&2; exit 1; }

cd "$LINUX"

log "== device tree ($DTB_NAME.dtb)"
# The board DTS is tracked in dts/ and copied into the kernel tree, because that
# tree is not versioned here. Refresh it, so this build cannot run against a
# stale copy of the file the ramoops check below is about - the two drifting
# apart is exactly how the log ends up somewhere Android does not read.
#
# What is copied is the tracked file *plus* a generated fragment, and the
# fragment is the difference between a boot and ABL's refusal. The tree ABL hands
# the kernel is not the one we write: the phone's dtbo validates, so ABL takes the
# overlay path, picks the Gauguin overlay out of it, and hands both to libufdt -
# which resolves the overlay's 158 `__fixups__` against our tree's `/__symbols__`
# and returns -1 if one is missing, printing "ApplyOverlay: ufdt apply overlay
# failed" and returning EFI_NOT_FOUND before the kernel's first instruction. Ours
# is not built with `-@` (scripts/Makefile.dtbs only adds it to base-dtb-y), so it
# carries no symbols at all and every payload built without the fragment is
# refused. tools/make_dtbo_sinks.py generates one empty node per symbol the dtbo
# asks for and a `/__symbols__` naming them, so the overlay applies cleanly and
# lands inside a subtree nothing binds to. docs/07 has the measurements.
SINKS="$OUT/gauguin-dtbo-sinks.dtsi"
python3 "$ROOT/tools/make_dtbo_sinks.py" --dtbo "$DTBO_IMG" -o "$SINKS"
cat "$ROOT/dts/$DTB_NAME.dts" "$SINKS" > "arch/arm64/boot/dts/qcom/$DTB_NAME.dts"
# And make a missing Makefile entry an error rather than a stale .dtb.
rm -f "arch/arm64/boot/dts/qcom/$DTB_NAME.dtb"
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)" dtbs
cp "arch/arm64/boot/dts/qcom/$DTB_NAME.dtb" "$DTB"

validate "$DTB"
printf '   %s\n' "$DTB"
