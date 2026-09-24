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

    python3 tools/acpi-hid-census.py                   # the address join
    python3 tools/acpi-hid-census.py --functions       # the name join
    python3 tools/acpi-hid-census.py --blocks          # gauguin's block list
    python3 tools/acpi-hid-census.py --drivers DIR     # which INFs bind these

Measured 2026-09-25, 66 reference tables. Three defects were found and fixed in
the drafts of this file, and all three made a negative answer look stronger
than the corpus supports:

    TLMM    0x0F100000   11 tables have a device here, 5 distinct _HIDs
                         QCOM1A0C x4   OnePlus/lemonade, Xiaomi/venus,
                                       Xiaomi/vili, Qualcomm/Lahaina/DSDT_MTP
                         QCOM0A0C x2   Samsung/a52sxq, Xiaomi/lisa
                         QCOM090C x2   Xiaomi/renoir, Qualcomm/Cedros/DSDT_IDP
                         QCOM0C0C x2   Qualcomm/Kailua/DSDT_MTP, .../DSDT_QRD
                         QCOM250C x1   Xiaomi/alioth
    SPMI    0x0C440000   no device *at this address*; 22 tables declare a
                         window at 0x0C400000 that contains it
    TSENS0  0x0C263000   no device at this address, no thermal node anywhere
    TSENS1  0x0C265000   same
    SE0..SE7 0x00[89]xxxxx  every window carries a different name per SoC

The 36 came from reading each platform's `DSDT.aml`, which is the trim. The
board variants are where Qualcomm writes the *complete* device list: Lahaina
ships `DSDT_MTP` (152 `Device` nodes) and `DSDT_Minimal` (8) and no plain
`DSDT.aml` at all, so the trim-only glob read none of Lahaina's device list in
any form - and Lahaina is the platform whose `GIO0` sits on gauguin's exact
TLMM window and length. Three of the four new TLMM references are variants.
Counted by name rather than by address, `GIO0` appears in 21 of the 66 tables
and `SPMI` in 22.

The second error is the join key, and the way it failed is worth copying down
because it is not the obvious one. **An address is a weak key, because a
reference's `_CRS` is often much coarser than the block it declares.** The
corpus's `SPMI` node claims `0x0C400000` for `0x02800000` - forty megabytes -
and gauguin's arbiter at `0x0C440000` sits inside that region. So an equality
test says "no device here" about a block that 22 of these 66 tables describe,
and it does so with the same confidence whether the corpus is empty or full.
Containment, not equality, is the test that matches how `_CRS` is written; what
carries across SoCs even better is the device *name* - every reference table
calls the GPIO controller `GIO0` and the arbiter `SPMI` regardless of where
their windows are. `--functions` joins on that.

Measured 2026-09-25, the name join:

    GIO0   02->17  05->0D  08->0D  09->0C  0A->0C  0C->0C  14->0D  1A->0C  25->0C  60->16
    SPMI   02->16  05->0C  08->0C  09->0B  0A->0B  0C->0B  14->0C  1A->0B  25->0B
    MMU0   02->12  05->09  08->09  09->09  0A->09  0C->09  14->09  1A->09  25->09
    QDSS   02->8C  05->5A  08->5A  09->56  0A->56  0C->56  14->5A  1A->56  25->56
    RFS0   02->35  05->17  08->17  09->15  0A->15  0C->15  14->17  1A->15  25->15

So a Qualcomm `_HID` is `QCOM` + two hex pairs, and the pairs are not
independent. The low pair is a block index that a whole *generation* of the
table generator shares: the families {09, 0A, 0C, 1A, 25} put the arbiter at 0B,
the GPIO controller at 0C, the MMU at 09, QDSS at 56 and RFS at 15, while the
older group {05, 08, 14} uses 0C, 0D, 09, 5A and 17 for the same five devices.
SDM850's 02 is a third group of its own - 16, 17, 12, 8C, 35 - which is why this
list used to be wrong: treating {02, 05, 08, 14} as one generation put the
arbiter at the wrong index for one of its four families. `MMU0` is the exception
that shows the axis is the generator and not the silicon: it stayed at 09 across
05 08 09 0A 0C 14 1A 25 and moved only for 02.

The high pair is a chipset-family token, and it does not follow the SoC's
marketing number: pipa and alioth both declare `SDM8250` and carry 05 and 25
respectively. Across the eleven tables where a GIO0 and an OEM table id can both
be read, the pairs are SDM850 02, SDM8150 05, SDM8250 05, SDM8250 25, SDM7180 08,
SDM7350 09, SDM7280 0A, SDM8550 0C, SDM7150 14, SDM8350 1A, SDM636 60.

**What that leaves is one byte, not a name.** The node shapes are all here -
`GIO0` with its `_CRS`, its interrupts and its pin tables; `SPMI`; `I2C<n>`,
`SPI<n>` and `UR<n>` for the GENI SE blocks, named by protocol; `BTNS` as a
standard `ACPI0011` Generic Buttons Device with Microsoft's `_DSD` UUID, whose
own `_HID` needs no vendor INF - though its `_CRS` names `\\_SB.PM01` as the
controller its `GpioInt` resources belong to, so it cannot be added on its own
either: a PMIC node has to exist for it, and a PMIC node carries this same
family byte. What cannot be read off the corpus is which family
SM7225 carries, and therefore whether its GPIO controller is `QCOM??0C` or
`QCOM??0D` - let alone what `??` is. But the search for it is finite and
mechanical: ten of gauguin's twelve blocks have a known index in each of the
measured generations, so a driver set either answers to `QCOM<byte><index>` for
all ten or it does not. No SM7225 table exists here: the only one that declares
the name is `Platforms/Xiaomi/gauguin/DSDT.aml`, and that is the file this tree
generated. Searching the corpus for `SM7225` returns our own work.

**And the reason is structural, not an accident of this corpus.** ACPI's `_HID`
is not a hardware fact; it is a string this port gets to choose, and the only
thing that constrains the choice is that a driver has to claim it. That the same
block appears under five different names in five reference DSDTs is evidence
about the choice being free, not about a name being lost.

So the direction of the work is unchanged but much narrower than "the tables
cannot be written": **adopt a driver set first, then name every block after it.**
The DSDT is written *to a driver*, not *to the SoC*, and an `.inf` lists exactly
the ids its driver answers to - so the driver set is not a later step, it is
where the missing two digits come from. A node described with an `_HID` no
available driver claims is hardware that cannot be driven, and it fails
silently: no error, no warning, just a device absent from Device Manager.

`--drivers DIR` takes a set and reports which of gauguin's blocks it covers,
which is the check to run the moment one is in hand, and it also answers the
question in the other direction - whose driver set this is, read off which names
it answers to. The same mechanism is what P5 means by "re-bind the WoA driver
INF" for the GPU.

Three ways this file fooled itself, recorded because all three agreed with the
conclusion being reached at the time. The `isinstance(True, int)` sentinel in
`devices_in`, which reported that *no* reference describes *any* of gauguin's
blocks; reading `*/DSDT.aml` alone, which reported that two of them have no
reference *at all*; and an equality test on the address, where containment is
what a `_CRS` actually means. Each one produced the same answer - nothing here
describes this - with perfect confidence and for a reason unconnected to the
corpus. A negative result is easiest to believe when it matches what you were
about to say, and the third one is the hardest to notice, because a miss read as
an absence looks exactly like a miss.
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


def read_win_text(path):
    """A Windows text file as `str`, whatever it was encoded in.

    Measured the hard way: every `.inf` in a Windows Update driver package is
    UTF-16 with a BOM, and reading one as UTF-8 does not fail - it succeeds and
    returns text whose every other byte is NUL, so `ACPI\\QCOM0A0C` arrives as
    `A\\x00C\\x00P\\x00I\\x00...` and *no* regex matches it. That produced the
    worst kind of wrong answer: `--drivers` reported 0 ids for a set that lists
    157 of them, and then concluded the set was "for some other SoC". A reader
    that silently sees nothing is not a reader, so the BOM is checked first and
    the NULs are used as a second signal for the files that lack one.
    """
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:4096]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


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
    """[(device_name, hid, cid, base_address, length)] in file order.

    The walk keeps a stack of enclosing devices. A resource is attributed to
    the innermost enclosing device that has a `_HID`, which is what a driver
    binds to; a `_CRS` inside a child that has only an `_ADR` (the UFS `DEV0`
    case) belongs to the parent.

    The length is carried because a reference's window is sometimes much
    coarser than the block it declares: Lahaina's `SPMI` claims
    `0x0C400000` for `0x02800000`, forty megabytes, which contains gauguin's
    arbiter at `0x0C440000`. An exact-equality census cannot see a block
    inside a window like that, and that - not a window that moves between
    SoCs - is why this tool reported SPMI as having no reference at all.
    """
    out = []
    stack = []

    def attribute(base, length):
        """Give a window to the innermost enclosing device that has a `_HID`."""
        for fr in reversed(stack):
            if fr[1]:
                out.append((fr[0], fr[1], fr[2], base, length))
                return

    # `NEXT` is a string and not True on purpose. `isinstance(True, int)` is
    # true in Python, so a boolean "the address is on the next line" sentinel
    # passes an `isinstance(pending, int)` test, records base 1, and clears
    # itself - which is how the first draft of this tool reported that *no*
    # reference DSDT describes any of gauguin's blocks.
    NEXT = "next"
    pending = None        # base address, an int, or NEXT while it is unread
    want_len = False      # base is known; its length is on the next line
    for ln in open(dsl, errors="replace"):
        m = DEVICE.match(ln)
        if m:
            stack.append([m.group(2), None, None])
            pending = None
            want_len = False
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
        # The disassembly puts a window over three lines - the call, the base
        # with an `// Address Base` comment, then the length - and a source
        # file may put all three on one. Both are accepted; a window whose
        # length never arrives is still recorded, with the length `None`.
        if want_len:
            n = HEX.search(ln)
            if n:
                attribute(pending, int(n.group(0), 16))
                pending = None
            elif ")" in ln:
                attribute(pending, None)
                pending = None
            want_len = False
            continue
        mm = MEMFIX_CALL.search(ln)
        if mm:
            found = HEX.findall(mm.group(1))
            if not found:
                pending = NEXT
            elif len(found) > 1:
                attribute(int(found[0], 16), int(found[1], 16))
            else:
                pending = int(found[0], 16)
                want_len = True
            continue
        if pending is NEXT:
            n = HEX.search(ln)
            if n:
                pending = int(n.group(0), 16)
                want_len = True
    return out


def table_files(tree):
    """Every ACPI table in the reference tree, not just each platform's DSDT.

    The first draft of this tool read `*/DSDT.aml` alone and concluded that two
    of gauguin's blocks had no reference anywhere. That was wrong, and wrong in
    the direction that flatters a negative result: the tree also carries the
    board variants (`DSDT_MTP`, `DSDT_QRD`, `DSDT_IDP`) and the SSDTs, and the
    variants are where Qualcomm writes the *complete* device list. Lahaina's
    `DSDT.aml` has 24 devices; Lahaina's `DSDT_MTP` has 152, including the
    `GIO0` node on gauguin's exact TLMM window.
    """
    pats = ["Platforms/*/*/", "Silicon/Qualcomm/*/"]
    return sorted(set(
        p for pre in pats
        for pat in ("DSDT*.aml", "SSDT*.aml")
        for p in glob.glob(tree + "/" + pre + pat)))


def census(tree, cache):
    """({base: [(platform, device, hid, cid, base, len)]}, tables) over the corpus.

    Every window in the corpus is kept, not just the ones at gauguin's own
    addresses, because the second question a caller asks is which references
    *contain* an address. A reference's `_CRS` is often much coarser than the
    block it declares, and equality alone reports that as a miss.
    """
    amls = table_files(tree)
    os.makedirs(cache, exist_ok=True)
    found = {}
    for aml in amls:
        dsl = disassemble(aml, cache)
        if not dsl:
            print(f"  !! iasl could not read {aml}", file=sys.stderr)
            continue
        # Keep the table name: `Xiaomi/lahaina/DSDT` and
        # `Xiaomi/lahaina/DSDT_MTP` are different readings of one platform and
        # collapsing them to `Xiaomi/lahaina` is how the variants went unread.
        plat = os.path.relpath(aml, tree)[:-4]
        plat = re.sub(r"^(Platforms|Silicon)/", "", plat)
        for dev, hid, cid, base, length in devices_in(dsl):
            if base is None:
                continue
            found.setdefault(base, []).append((plat, dev, hid, cid, base, length))
    return found, len(amls)


def containing(found, addr):
    """[(platform, device, hid, base, length)] whose window holds `addr`.

    Strictly bigger than a point: a window that starts at `addr` is the exact
    match the caller already has, and counting it twice would make a 40 MB
    region look like a second reference.
    """
    out = []
    for _base, rows in found.items():
        for plat, dev, hid, _cid, base, length in rows:
            if base != addr and length and base < addr < base + length:
                out.append((plat, dev, hid, base, length))
    return out


def cmd_blocks():
    print("gauguin's blocks, as the device tree gives them:\n")
    print(f"  {'block':<7} {'base':<12} {'len':<9} node")
    for label, base, length, node, why in BLOCKS:
        print(f"  {label:<7} 0x{base:08X}  0x{length:06X}  {node}")
        print(f"  {'':<7} {why}")
    print("\n  Every one of these needs an ACPI node with a `_HID` a driver")
    print("  binds to. The addresses are the part the device tree supplies.")


QCOM_ID = re.compile(r"^QCOM([0-9A-F]{2})([0-9A-F]{2})$")


def collect_by_name(tree, cache):
    """{device name: {hid: [platform/table]}} over every reference table.

    The address join in `census` cannot see SPMI at all: the reference `SPMI`
    node declares `0x0C400000` for `0x02800000` - forty megabytes - while
    gauguin's arbiter sits at `0x0C440000` inside that region, so a reader
    testing for equality reports "no reference device at this address" about
    a block 22 of the 66 tables describe. Qualcomm's reference tables name
    their devices by function, and *that* is the key that carries.
    """
    byname = {}
    for aml in table_files(tree):
        dsl = disassemble(aml, cache)
        if not dsl:
            continue
        plat = re.sub(r"^(Platforms|Silicon)/", "",
                      os.path.relpath(aml, tree)[:-4])
        for dev, hid, _cid, _base, _len in devices_in(dsl):
            if hid:
                byname.setdefault(dev, {}).setdefault(hid, []).append(plat)
    return byname


# The reference device names for the blocks gauguin's destination table has to
# describe. The reference DSDTs use these names on every SoC, which is what
# makes them usable as a join key where an address is not.
REF_NAMES = {
    "GIO0": "TLMM", "SPMI": "SPMI", "PMIC": "SPMI (PMIC child)",
    "IC": "SE* I2C", "SPI": "SE* SPI", "UR": "SE* UART",
    "TSEN": "TSENS", "TSSC": "TSENS", "QTSC": "TSENS",
    "BTNS": "buttons (ACPI0011)",
}

# The measured index tables. The low pair of `QCOM<family><index>` is fixed per
# generation of the table generator - `--functions` prints the evidence, which is
# that the families arrive in groups rather than scattered. There are three of
# those groups in the corpus, not two: this list used to merge 02 in with 05 08
# 14, and family 02 does not share their indices (`SPMI` is 16 under 02 and 0C
# under 05 08 14). The SDM850 table is incomplete because the corpus gives no SE
# indices for it; a `None` there means unmeasured, not absent.
#
# The PMIC nodes are deliberately not in here even though their index is measured
# and splits the same way - `PM01` is `QCOM<family>2D` under 09 0A 0C 1A 25,
# `QCOM<family>30` under 05 08 14 and `QCOM0269` for 02. The index is a fact; which
# of gauguin's four SPMI PMICs becomes `PM01` is a choice this port makes, and a
# count that mixed the two would be reporting the choice as a measurement.
GENERATIONS = [
    ("modern  (index table read off 09 0A 0C 1A 25)", {
        "SPMI": "0B", "TLMM": "0C", "MMU": "09", "QDSS": "56", "RFS": "15",
        "GPU": "36", "I2C": "10", "SPI": "0E", "UART": "16"}),
    ("legacy  (index table read off 05 08 14)", {
        "SPMI": "0C", "TLMM": "0D", "MMU": "09", "QDSS": "5A", "RFS": "17",
        "GPU": "3A", "I2C": "11", "SPI": "0F", "UART": "18"}),
    ("sdm850  (family 02 only, SE indices unmeasured)", {
        "SPMI": "16", "TLMM": "17", "MMU": "12", "QDSS": "8C", "RFS": "35",
        "GPU": "7E", "I2C": None, "SPI": None, "UART": None}),
]

# What each of gauguin's twelve blocks is in the index tables' vocabulary. The
# two TSENS windows have no kind because the corpus has no thermal-sensor device
# at all - 66 tables, no `TSEN`, no `_HID` ending in a thermal index, and no
# device at either of gauguin's windows. Windows on these platforms gets its
# thermal zones from `ThermalZone` objects named `QCOM<family><zone>`, whose
# zone indices are their own per-generation table and which read the PMIC
# rather than a TSENS block. So these two ids cannot be guessed even once the
# family byte is known.
BLOCK_KIND = {
    "TLMM": "TLMM", "SPMI": "SPMI",
    "TSENS0": None, "TSENS1": None,
    "SE0": "SPI", "SE0u": "UART", "SE1": "I2C", "SE2": "I2C",
    "SE3": "I2C", "SE5": "I2C", "SE6": "SPI", "SE7": "I2C",
}


def cmd_functions(args):
    """Is the low pair of a QCOM id the block's function, and the high pair the SoC?

    This is the question `--drivers` and the address census both leave open.
    Every SoC-scoped id in the corpus is `QCOM` + four hex digits. If the last
    two are what the block *is* - the same on every SoC - then the function is
    a fact about the hardware, readable off the corpus; and if the first two
    are the SoC family, then naming gauguin's twelve blocks collapses to
    finding one two-digit number for SM7225. If instead both pairs move
    together, the id is opaque and only a driver set can supply it.
    """
    byname = collect_by_name(args.tree, args.cache)
    tables = len(table_files(args.tree))
    print(f"{tables} reference tables under "
          f"{os.path.relpath(args.tree, REPO)}\n")

    rows = []
    for dev, hids in byname.items():
        forms = {h: p for h, p in hids.items() if QCOM_ID.match(h)}
        if len(forms) < 2:
            continue
        suffixes = {QCOM_ID.match(h).group(2) for h in forms}
        prefixes = {QCOM_ID.match(h).group(1) for h in forms}
        rows.append((dev, forms, prefixes, suffixes))

    # A device whose low pair is constant across SoCs is the evidence for the
    # decomposition; one whose low pair moves is evidence against it, and both
    # belong in the table.
    stable = [r for r in rows if len(r[3]) == 1 and len(r[2]) > 1]
    unstable = [r for r in rows if len(r[3]) > 1]
    stable.sort(key=lambda r: (-len(r[1]), r[0]))

    print(f"  {len(stable)} device names keep one low pair across different "
          f"SoC families:\n")
    print(f"  {'device':<7} {'n':>3}  {'low':<4} {'families (high pair)':<34} ids")
    for dev, forms, prefixes, suffixes in stable[:40]:
        low = next(iter(suffixes))
        fams = " ".join(sorted(prefixes))
        ids = " ".join(sorted(forms))
        note = f"  <- {REF_NAMES[dev]}" if dev in REF_NAMES else ""
        print(f"  {dev:<7} {len(forms):>3}  {low:<4} {fams:<34} {ids}{note}")

    if unstable:
        # The groups are the finding. A device whose low pair takes one value
        # on the five modern families and another on the three older ones is
        # not a counterexample to the decomposition - it is the decomposition
        # with the generation axis still in it, and the axis is the point.
        print(f"\n  {len(unstable)} device names move, and the way they move is\n"
              f"  the generation - the families arrive grouped, not scattered:\n")
        print(f"  {'device':<7} {'n':>3}  low pair <- families")
        for dev, forms, _p, _s in sorted(unstable)[:14]:
            groups = {}
            for h in forms:
                m = QCOM_ID.match(h)
                groups.setdefault(m.group(2), []).append(m.group(1))
            ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            cell = "   ".join(f"{low} <- {' '.join(sorted(f))}"
                              for low, f in ordered[:2])
            if len(ordered) > 2:
                cell += f"   (+{len(ordered) - 2} more)"
            print(f"  {dev:<7} {len(forms):>3}  {cell}")

    print()
    print("  Read the first table as: for those ten the low pair is the same")
    print("  on every family in the corpus, so on its own it reads as a")
    print("  property of the device kind.")
    print()
    print("  Read the second as: for the rest the low pair is fixed per")
    print("  generation of the table generator, and the families arrive")
    print("  grouped rather than scattered. Five of them, groups spelled out:")
    print()
    print("    device   modern (09 0A 0C 1A 25)   older (05 08 14)  02 (SDM850)")
    print("    SPMI     0B                        0C                16")
    print("    GIO0     0C                        0D                17")
    print("    QDSS     56                        5A                8C")
    print("    RFS0     15                        17                35")
    print("    GPU0     36                        3A                7E")
    print()
    print("  which is why gauguin's TLMM window, at gauguin's exact length,")
    print("  is spelled QCOM1A0C in Lahaina's DSDT_MTP. MMU is the exception")
    print("  that fixes what the axis is: it stayed 09 across 05 08 09 0A 0C")
    print("  14 1A 25 and only moved to 12 for 02, so the axis is the")
    print("  generator that emitted the table and not the silicon.")
    print()
    print("  The family sets in that table are the ones each index was")
    print("  measured on, not a closed membership - `GPU0` lands on its")
    print("  modern index 36 under 0E as well, and GIO0 carries a fourth")
    print("  index under family 60. Which is also why `--drivers` tries")
    print("  every byte rather than these:")
    print()
    print("  And the high pair is not the SoC either - pipa and alioth both")
    print("  declare SDM8250 and carry 05 and 25. So the id is two facts in")
    print("  one string: a block index readable off this corpus once the")
    print("  generation is known, and a family token that has to come from a")
    print("  driver set or from firmware that declares it. `--drivers`")
    print("  searches every family byte against each measured index table.")


def cmd_census(args):
    found, total = census(args.tree, args.cache)
    print(f"{total} reference tables under {os.path.relpath(args.tree, REPO)}\n")
    verdicts = []
    for label, base, _len, node, _why in BLOCKS:
        rows = found.get(base, [])
        seen = {}
        for plat, dev, hid, cid, _b, _l in rows:
            seen.setdefault((hid, cid), []).append(f"{plat}/{dev}")
        covers = containing(found, base)
        if not rows:
            print(f"  {label:<7} 0x{base:08X}  {node}")
            print(f"          no reference device sits at this address")
            if covers:
                # The reference describes the block, just as part of a bigger
                # region. That distinction is the difference between "cannot be
                # named from this corpus" and "was read with the wrong test".
                bywin = {}
                for plat, dev, hid, cbase, clen in covers:
                    bywin.setdefault((hid, cbase, clen), []).append(f"{plat}/{dev}")
                for (hid, cbase, clen), where in sorted(
                        bywin.items(), key=lambda kv: -len(kv[1])):
                    who = ", ".join(sorted(where)[:3])
                    more = f" +{len(where) - 3}" if len(where) > 3 else ""
                    print(f"          but {len(where)}x {hid} claim(s) "
                          f"0x{cbase:08X} for 0x{clen:X}, which contains it:")
                    print(f"            {who}{more}")
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
        print(f"  no reference device at this address: {', '.join(unnamable)}")
    if ambiguous:
        print(f"  more than one name in the corpus: {', '.join(ambiguous)}")
    print()
    print("  Careful with the first line: an address is a weak join key. A")
    print("  reference `_CRS` is often coarser than the block it declares -")
    print("  `SPMI` claims 0x0C400000 for 0x02800000, forty megabytes, with")
    print("  gauguin's arbiter at 0x0C440000 inside it - so an equality test")
    print("  reports a miss where a containment test finds 22 tables. Any")
    print("  block that line names is worth re-reading above before it is")
    print("  believed. `--functions` joins on the device name, which is the")
    print("  key that carries.")
    if unnamable or ambiguous:
        print()
        print("  Read this as: the name is not a hardware fact. ACPI's _HID is a")
        print("  string this port chooses, and the reference corpus choosing")
        print("  differently on every SoC is evidence the choice is free - not")
        print("  evidence that a correct name exists somewhere and is missing.")
        print("  What constrains it is the driver: a device binds to the name its")
        print("  .inf lists and to nothing else, and a node whose name no driver")
        print("  claims is absent from Device Manager with no error at all.")
        print()
        print("  So adopt a driver set first and name every block after it. The")
        print("  DSDT is written to a driver, not to the SoC. Run --drivers over")
        print("  a set to see which of these blocks it covers, and whose set it is.")
        return 0
    print("  every block has exactly one name in the corpus - safe to carry.")
    return 0


def cmd_drivers(args):
    """Which family byte turns a driver set into a name for every block.

    This is the other half of the census, and the generation search is the
    point of it. The block *index* - the low pair of `QCOM<family><index>` - is
    readable off the corpus; the *family* byte is not, and it is not derivable
    from the SoC's marketing number either: pipa and alioth both declare
    `SDM8250` and carry 05 and 25.

    What an `.inf` does carry is the exact ids its driver binds to. So a driver
    set is a four-hex-digit oracle, and this uses it as one: for each of the 256
    family bytes and each of the measured index tables, build
    `QCOM<family><index>` for every block gauguin has, and count how many of
    them the set answers to. A set that covers all of them names the family, and
    names every block in the same breath.
    """
    inffiles = []
    for root, _dirs, names in os.walk(args.drivers):
        inffiles += [os.path.join(root, n) for n in names if n.lower().endswith(".inf")]
    if not inffiles:
        print(f"no .inf files under {args.drivers}")
        return 1
    hids = {}      # ACPI id -> [inf paths]
    encodings = {}  # what each .inf turned out to be, for the summary line
    for inf in inffiles:
        try:
            raw = open(inf, "rb").read()
        except OSError:
            continue
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            enc = "utf-16 (BOM)"
        elif b"\x00" in raw[:4096]:
            enc = "utf-16 (no BOM)"
        else:
            enc = "utf-8"
        encodings[enc] = encodings.get(enc, 0) + 1
        text = read_win_text(inf)
        for m in INF_ACPI.finditer(text):
            hids.setdefault(m.group(1).upper(), []).append(os.path.relpath(inf, args.drivers))
    print(f"{len(inffiles)} .inf files, {len(hids)} distinct ACPI hardware ids")
    print(f"  encodings: {', '.join(f'{v} {k}' for k, v in sorted(encodings.items()))}\n")

    qcom = sorted(h for h in hids if QCOM_ID.match(h))
    print(f"  {len(qcom)} of them are QCOM ids: "
          f"{' '.join(qcom[:12])}{' ...' if len(qcom) > 12 else ''}\n")

    kinds = {label: kind for label, kind in BLOCK_KIND.items()}
    nokind = sorted(l for l, k in kinds.items() if not k)
    best = []
    for gen, table in GENERATIONS:
        for byte in range(0x100):
            fam = f"{byte:02X}"
            # `None` in a generation's table means that generation's index for
            # that kind was never measured, so no id can be built from it. The
            # denominator shrinks with it rather than being faked.
            want = {label: f"QCOM{fam}{table[kind]}"
                    for label, kind in kinds.items()
                    if kind and table.get(kind)}
            hits = {label: h for label, h in want.items() if h in hids}
            if hits:
                best.append((len(hits), len(want), gen, fam, want, hits))
    best.sort(key=lambda r: -r[0])

    if nokind:
        print(f"  Not covered by this search at all: {', '.join(nokind)} - the")
        print("  corpus has no device of that kind, so there is no index to fill in.")
        print()

    if not best:
        print("  No family byte under any measured index table makes this set")
        print(f"  answer a name for any of gauguin's {len(BLOCKS)} blocks. So this")
        print("  set is not the one for this device - it is a set for some other")
        print("  SoC, and its ids say which one, above.")
        return 0

    print(f"  {'coverage':<10} {'generation':<36} family  blocks, and the id each gets")
    for n, total, gen, fam, want, hits in best[:6]:
        print(f"  {n}/{total:<8} {gen:<36} {fam}")
        for label in sorted(want):
            mark = "yes" if label in hits else " - "
            print(f"  {'':<10} {'':<36} {'':<7}  {mark} {label:<6} {want[label]}")
        print()
    top = best[0]
    if top[0] == top[1]:
        print(f"  A complete match: family byte {top[3]} under {top[2].split()[0]},")
        print("  and the ids above are the ones the nodes must carry. Check them")
        print("  against BLOCKS before using them - a family byte that happens to")
        print("  collide on a partial set is not the same as a full one.")
    else:
        print(f"  Best is {top[0]} of {top[1]} under family {top[3]}. A partial match")
        print("  is not a family: it can happen by collision, so look at the ids")
        print("  the set lists above before believing any byte.")
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
    ap.add_argument("--functions", action="store_true",
                    help="decompose the QCOM ids by device name (SoC vs block)")
    ap.add_argument("--drivers", metavar="DIR",
                    help="a Windows driver set to check coverage against")
    args = ap.parse_args()
    if args.blocks:
        cmd_blocks()
        return 0
    if args.functions:
        return cmd_functions(args)
    if args.drivers:
        return cmd_drivers(args)
    return cmd_census(args)


if __name__ == "__main__":
    sys.exit(main())
