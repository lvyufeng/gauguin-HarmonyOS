#!/usr/bin/env python3
"""Can gauguin's ACPI device names be taken from another Qualcomm DSDT?

`tools/acpi/gauguin.asl` describes exactly five things: UFS, the USB controller
and its two children, and the eight CPUs. P3 item 1 asks for I2C, SPI, GPIO,
buttons and thermal zones on top of that, and none of those nodes is in the
file. The device tree supplies every one of their *addressing* facts - register
windows, interrupt numbers, pin numbers - so the obvious next move is to lift
the nodes out of a platform that already has them and substitute the addresses.

That move is only sound if the names travel. A Qualcomm DSDT block carries a
`_HID` like `QCOM1A0C`, an `_AEI` template, a `_REG` hook and a `_DSM` whose
UUID and return value are not documented anywhere in this tree. If `QCOM1A0C`
means "the TLMM block, wherever it is", it can be copied with the window
changed. If it means "this SoC's GPIO controller", copying it produces a device
node that no driver binds to - and Windows reports nothing when a device node
has an unknown `_HID`. It is simply absent from Device Manager. A silently
unbound node is worse than no node, because no node is visibly missing.

So this measures the question rather than arguing it. It disassembles every
reference DSDT in the Silicium-ACPI tree, joins each device to the block it
describes by the `Memory32Fixed` base address - the one key both the ACPI and
the device-tree worlds carry - and tabulates the `_HID` seen at each of
gauguin's own block addresses.

    python3 tools/acpi-hid-census.py                  # the census
    python3 tools/acpi-hid-census.py --drivers DIR    # which INFs bind these
    python3 tools/acpi-hid-census.py --blocks         # gauguin's block list

Measured 2026-09-25, 36 reference DSDTs:

    TLMM    0x0F100000   7 describe it, 4 different _HIDs
                         QCOM1A0C x3 (lemonade, venus, vili)
                         QCOM0A0C x2 (a52sxq, lisa)
                         QCOM250C x1 (alioth)    QCOM090C x1 (renoir)
    SE0     0x00880000   5 describe it, 4 different _HIDs
                         QCOM0811 x2, QCOM0C10, QCOM0511, QCOM140F
    SE3     0x00984000   QCOM0A10 x2, QCOM2510
    SE5     0x00988000   QCOM250E, QCOM090E
    SE7     0x00990000   QCOM250E, QCOM0A10, QCOM1A10
    SPMI    0x0C440000   0 describe it
    TSENS0  0x0C263000   0 describe it
    TSENS1  0x0C265000   0 describe it

The same window carries a different name on every SoC, and two of the five
blocks have no reference at all - not a wrong form, no form. The `_DSM`
contracts are per-SoC in the same way: the GPIO node returns 0x0140 on vili,
0x0100 on alioth and 0x0180 on lisa, and nothing in the tree says what that
number means.

**The names are a property of the SoC, and the only authoritative source is the
Windows driver set**, whose `.inf` files list the `ACPI\\...` hardware IDs their
drivers bind. Obtaining that set is therefore a precondition for authoring
these tables, not a later step: write them first and every node names a block no
driver answers to. `--drivers DIR` takes such a set and reports which of
gauguin's blocks it covers, which is the check to run the moment one is in hand.
"""
import argparse
import glob
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TREE = os.path.join(
    REPO, "work/uefi/Mu-Silicium/Silicium-ACPI")
DEFAULT_CACHE = "/tmp/acpi-hid-census"

# gauguin's blocks, every address from work/out/gauguin.dts (the generated tree
# from the tracked dts) or, where marked, the merged tree Step 4.54 read - the
# overlay that carries the second SPI controller is one of the ones our
# generated tree replaced with empty sinks, so that node is not in it.
#
# (label, base, length, node in the device tree, what it is for)
BLOCKS = [
    ("TLMM",   0x0F100000, 0x300000, "pinctrl@f100000",
     "GPIO controller: the touchscreen IRQ, the fingerprint IRQ and the volume "
     "keys are all GPIO interrupts, so nothing interrupts without it"),
    ("SPMI",   0x0C440000, 0x000000, "spmi@c440000",
     "PMIC arbiter: the power key and both volume keys are PMIC GPIOs"),
    ("TSENS0", 0x0C263000, 0x0001FF, "thermal-sensor@c263000",
     "temperature sensing, low and critical trip interrupts"),
    ("TSENS1", 0x0C265000, 0x0001FF, "thermal-sensor@c265000",
     "the second sensing block, four more zones"),
    ("SE0",    0x00880000, 0x004000, "spi@880000 (merged tree, Step 4.54)",
     "the touchscreen bus - touch_spi@0 with a Novatek NVT part at 10 MHz"),
    ("SE0u",   0x00884000, 0x004000, "serial@884000", "the Bluetooth UART"),
    ("SE1",    0x00888000, 0x004000, "i2c@888000", "disabled in the tree"),
    ("SE2",    0x00980000, 0x004000, "i2c@980000", "disabled in the tree"),
    ("SE3",    0x00984000, 0x004000, "i2c@984000",
     "the two cs35l41 audio amplifiers (overlay 13)"),
    ("SE5",    0x00988000, 0x004000, "i2c@988000", "the nq NFC controller"),
    ("SE6",    0x0098C000, 0x004000, "serial@98c000 -> spi@98c000 (overlay 13)",
     "the IR blaster"),
    ("SE7",    0x00990000, 0x004000, "i2c@990000",
     "fsa4480, aw8624 haptics, bq25970 charger, pm8008"),
]

# The census joins on the base address alone. A device whose window is a
# sub-block of one of these (the PMIC arbiter's five windows, the second TSENS
# window at 0xC222000) would not match, and does not need to: it is the same
# ACPI device.
MEMFIX_CALL = re.compile(r"Memory32Fixed \(\s*ReadWrite\s*,\s*(.*)$")
HEX = re.compile(r"0x[0-9A-Fa-f]+")
DEVICE = re.compile(r"^( *)Device \(([A-Z0-9_]{4})\)")
NAME_HID = re.compile(r'Name \(_HID, "(?:EisaId \(")?([^"]+)"')
NAME_CID = re.compile(r'Name \(_CID, "(?:EisaId \(")?([^"]+)"')
# An .inf names the hardware it binds as ACPI\<HID>, with the HID either four
# to eight alphanumerics (QCOM1A0C) or a PNP id (PNP0C0E). Both are matched:
# the question is coverage, and a block covered by a PNP device is covered.
INF_ACPI = re.compile(r"ACPI\\\s*([A-Za-z0-9_]{3,16})", re.I)


def disassemble(aml, cache):
    """(dsl_path or None) - iasl -d, cached, because 36 trees take ~90 s."""
    rel = os.path.relpath(aml, DEFAULT_TREE)
    name = rel.replace("/", "-").replace(".aml", "")
    dsl = os.path.join(cache, name + ".dsl")
    if not os.path.exists(dsl):
        subprocess.run(["iasl", "-d", "-p", os.path.join(cache, name), aml],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dsl if os.path.exists(dsl) else None


def devices_in(dsl):
    """[(device_name, hid, cid, base_address or None)] in file order.

    The walk keeps a stack of enclosing devices. A resource is attributed to
    the innermost enclosing device that has a `_HID`, which is what a driver
    binds to; a `_CRS` inside a child that has only an `_ADR` (the UFS `DEV0`
    case) belongs to the parent.
    """
    out = []
    stack = []
    # `NEXT` is a string and not True on purpose. `isinstance(True, int)` is
    # true in Python, so a boolean "the address is on the next line" sentinel
    # passes an `isinstance(pending, int)` test, records base 1, and clears
    # itself - which is how the first draft of this tool reported that *no*
    # reference DSDT describes any of gauguin's blocks.
    NEXT = "next"
    pending = None
    for ln in open(dsl, errors="replace"):
        m = DEVICE.match(ln)
        if m:
            stack.append([m.group(2), None, None, None])
            pending = None
            continue
        h = NAME_HID.search(ln)
        if h and stack:
            for fr in reversed(stack):
                if fr[1] is None:
                    fr[1] = h.group(1)
                    break
            continue
        c = NAME_CID.search(ln)
        if c and stack:
            for fr in reversed(stack):
                if fr[2] is None:
                    fr[2] = c.group(1)
                    break
            continue
        mm = MEMFIX_CALL.search(ln)
        if mm:
            tail = HEX.search(mm.group(1))
            pending = int(tail.group(0), 16) if tail else NEXT
        elif pending is NEXT:
            n = HEX.search(ln)
            if n:
                pending = int(n.group(0), 16)
        if isinstance(pending, int) and not isinstance(pending, bool) and stack:
            for fr in reversed(stack):
                if fr[1]:
                    fr[3] = pending
                    out.append(tuple(fr[:4]))
                    break
            pending = None
    return out


def census(tree, cache, only=None):
    """{base: [(platform, device, hid, cid)]} over every reference DSDT."""
    amls = sorted(glob.glob(tree + "/Platforms/*/*/DSDT.aml") +
                  glob.glob(tree + "/Silicon/Qualcomm/*/DSDT.aml"))
    os.makedirs(cache, exist_ok=True)
    found = {}
    for aml in amls:
        dsl = disassemble(aml, cache)
        if not dsl:
            print(f"  !! iasl could not read {aml}", file=sys.stderr)
            continue
        plat = os.path.relpath(aml, tree).replace("/DSDT.aml", "")
        for dev, hid, cid, base in devices_in(dsl):
            if base is None or (only and base not in only):
                continue
            found.setdefault(base, []).append((plat, dev, hid, cid))
    return found, len(amls)


def cmd_blocks():
    print("gauguin's blocks, as the device tree gives them:\n")
    print(f"  {'block':<7} {'base':<12} {'len':<9} node")
    for label, base, length, node, why in BLOCKS:
        print(f"  {label:<7} 0x{base:08X}  0x{length:06X}  {node}")
        print(f"  {'':<7} {why}")
    print("\n  Every one of these needs an ACPI node with a `_HID` a driver")
    print("  binds to. The addresses are the part the device tree supplies.")


def cmd_census(args):
    blocks = {b[1]: b for b in BLOCKS}
    found, total = census(args.tree, args.cache, only=set(blocks))
    print(f"{total} reference DSDTs under {os.path.relpath(args.tree, REPO)}\n")
    verdicts = []
    for label, base, _len, node, _why in BLOCKS:
        rows = found.get(base, [])
        seen = {}
        for plat, dev, hid, cid in rows:
            seen.setdefault((hid, cid), []).append(f"{plat}/{dev}")
        if not rows:
            print(f"  {label:<7} 0x{base:08X}  {node}")
            print(f"          NO REFERENCE describes this window")
            verdicts.append((label, 0, 0))
            print()
            continue
        print(f"  {label:<7} 0x{base:08X}  {node}")
        print(f"          {len(rows)} describe it, {len(seen)} distinct name(s)")
        for (hid, cid), where in sorted(seen.items(), key=lambda kv: -len(kv[1])):
            cid_s = f"  _CID {cid}" if cid else ""
            who = ", ".join(sorted(where)[:4])
            more = f" +{len(where) - 4}" if len(where) > 4 else ""
            print(f"            {str(hid):<12}{cid_s:<22} {len(where):>2}x  {who}{more}")
        verdicts.append((label, len(rows), len(seen)))
        print()

    print("  " + "-" * 70)
    unnamable = [l for l, rows, names in verdicts if rows == 0]
    ambiguous = [l for l, rows, names in verdicts if names > 1]
    if unnamable:
        print(f"  no reference at all: {', '.join(unnamable)}")
    if ambiguous:
        print(f"  more than one name in the corpus: {', '.join(ambiguous)}")
    if unnamable or ambiguous:
        print()
        print("  A name that differs between SoCs at the same address is a name of")
        print("  the SoC, not of the block, and cannot be carried across. A block no")
        print("  reference describes has no form here to adapt - not a wrong one, no")
        print("  one. Either way the node cannot be written from this host, and a")
        print("  guessed `_HID` fails silently: the device simply never appears.")
        print("  The authoritative source is the Windows driver set's .inf files.")
        print("  Run --drivers over one when it is in hand.")
        return 0
    print("  every block has exactly one name in the corpus - safe to carry.")
    return 0


def cmd_drivers(args):
    """Which of gauguin's blocks an available Windows driver set covers.

    This is the other half of the census: the tables cannot be authored until
    the names are known, and the names are whatever the drivers answer to. So
    the question to ask of a driver set is not "does it have a GPIO driver" but
    "which of these twelve blocks does it name an ACPI id for".
    """
    inffiles = []
    for root, _dirs, names in os.walk(args.drivers):
        inffiles += [os.path.join(root, n) for n in names if n.lower().endswith(".inf")]
    if not inffiles:
        print(f"no .inf files under {args.drivers}")
        return 1
    hids = {}      # ACPI id -> [inf paths]
    for inf in inffiles:
        try:
            text = open(inf, errors="replace").read()
        except OSError:
            continue
        for m in INF_ACPI.finditer(text):
            hids.setdefault(m.group(1).upper(), []).append(os.path.relpath(inf, args.drivers))
    print(f"{len(inffiles)} .inf files, {len(hids)} distinct ACPI hardware ids\n")

    # A driver for a block does not have to carry the block's own id - it can
    # be a child device (the touchscreen) that names the parent's. So this
    # reports the ids present and the ids each block's reference DSDTs used,
    # and lets the reader see which of them the set answers to.
    refs = {}
    if os.path.isdir(args.tree):
        blocks = {b[1]: b for b in BLOCKS}
        found, _total = census(args.tree, args.cache, only=set(blocks))
        for base, rows in found.items():
            for _plat, _dev, hid, _cid in rows:
                if hid:
                    refs.setdefault(base, set()).add(hid.upper())

    print(f"  {'block':<7} {'base':<12} referenced as            in the driver set")
    covered = 0
    for label, base, _len, node, _why in BLOCKS:
        want = sorted(refs.get(base, []))
        if not want:
            print(f"  {label:<7} 0x{base:08X} (no reference name)      -")
            continue
        hits = [h for h in want if h in hids]
        mark = "YES" if hits else "no"
        note = ", ".join(hits) if hits else "none of " + "/".join(want)
        if hits:
            covered += 1
        print(f"  {label:<7} 0x{base:08X} {note:<25} {mark}")
    print()
    print(f"  {covered} of {len(BLOCKS)} blocks have a reference name the driver set")
    print("  answers to. For the rest, either the set is the wrong one or the")
    print("  block is not one Windows drives on this device; the .inf that binds")
    print("  it names the id to use, and that id is what the node must carry.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Is a Qualcomm ACPI block's _HID a property of the block, "
                    "or of the SoC?")
    ap.add_argument("--tree", default=DEFAULT_TREE,
                    help="Silicium-ACPI tree holding the reference DSDTs")
    ap.add_argument("--cache", default=DEFAULT_CACHE,
                    help="where to keep the disassembled .dsl files")
    ap.add_argument("--blocks", action="store_true",
                    help="list gauguin's blocks and why each one is needed")
    ap.add_argument("--drivers", metavar="DIR",
                    help="a Windows driver set to check coverage against")
    args = ap.parse_args()
    if args.blocks:
        cmd_blocks()
        return 0
    if args.drivers:
        return cmd_drivers(args)
    return cmd_census(args)


if __name__ == "__main__":
    sys.exit(main())
