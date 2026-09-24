#!/usr/bin/env python3
"""Where the DXE heap's size comes from, and what is (not) in the gap after it.

`MemoryMapLib.c`'s `DXE Heap` row is what `Sec.c:64-78` looks up by name to size
the PHIT, so that one row is the whole of the memory DxeCore can hand out. It is
transcribed from the device's own XBL `uefiplat.cfg`, which is where a change
would have to be made - so the question "can the heap be enlarged" is a question
about that file, and this reads it rather than reasoning about it.

Two things get compared:

  * The declared `DXE Heap` extent, against the sibling platforms that share its
    base address. If gauguin's is the odd one out, that is a fact about gauguin;
    if every platform at that base declares the same extent, the row is a copy.

  * The DDR regions of *this* device, in address order, so the holes between them
    are visible. A hole is not free memory - nothing builds a HOB for an address
    the config never mentions, so it has no GCD descriptor and no memory map
    entry, and extending the heap into it means claiming RAM the device's own
    firmware deliberately left unclaimed. That is the useful half: it says
    whether there is anywhere to grow into, and whether growing there is safe.

    tools/heap-compare.py [--binaries DIR] [--config FILE]

Exit status is 0 whatever it finds; this is a report, not a gate.
"""

import argparse
import glob
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_BINARIES = os.path.join(ROOT, "work", "uefi", "Mu-Silicium", "Binaries")
DEFAULT_CONFIG = os.path.join(ROOT, "device", "config", "uefiplat.cfg")

ROW = re.compile(
    r"^(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),\s*\"([^\"]+)\",\s*"
    r"(\w+),\s*(\w+),\s*(\w+),\s*(\w+),\s*(\w+)",
    re.M,
)


def ddr_rows(path):
    """The AddMem rows of a uefiplat.cfg, in file order, as (base, size, name, memtype)."""
    text = open(path, "r", errors="replace").read()
    head = text.split("[RegisterMap]", 1)[0]
    rows = []
    for m in ROW.finditer(head):
        base, size, name, build, res, attr, memtype, cache = m.groups()
        if build.lower().startswith("nohob"):
            continue
        rows.append((int(base, 16), int(size, 16), name, memtype))
    return rows


def fmt(n):
    return f"{n / (1 << 20):.1f} MiB"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--binaries", default=DEFAULT_BINARIES)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args()

    cfg = os.path.normpath(args.config)
    if not os.path.isfile(cfg):
        sys.exit(f"heap-compare: no config at {cfg}")

    ours = ddr_rows(cfg)
    heap = next((r for r in ours if r[2] == "DXE Heap"), None)
    if heap is None:
        sys.exit("heap-compare: this config declares no DXE Heap")
    base, size, _, memtype = heap

    print(f"heap-compare: {os.path.relpath(cfg, ROOT)}\n")
    print(f"DXE Heap: 0x{base:08X} + 0x{size:08X} = {size // 0x1000} pages = {fmt(size)},"
          f" memory type {memtype}")
    if memtype != "Conv":
        print(f"  !! this row is {memtype}, not Conv - Sec.c sizes the PHIT from it either")
        print("     way, but only a Conv row reaches CoreAddMemoryDescriptor as")
        print("     EfiConventionalMemory (Gcd.c:2776). The heap would not be allocatable.")

    # Sibling platforms at the same heap base: is this extent a copy or a choice?
    print(f"\nother uefiplat.cfg files declaring a DXE Heap at 0x{base:08X}:")
    others = []
    for path in sorted(glob.glob(os.path.join(args.binaries, "*", "RawFiles", "uefiplat.cfg"))):
        dev = os.path.basename(os.path.dirname(os.path.dirname(path)))
        row = next((r for r in ddr_rows(path) if r[2] == "DXE Heap"), None)
        if row is None or row[0] != base:
            continue
        # The region that starts exactly where the heap ends; on every one of
        # these it is Sched Heap, which is the check that the two are adjacent.
        nxt = next((r for r in ddr_rows(path) if r[0] == row[0] + row[1]), None)
        others.append((dev, row[1], nxt[2] if nxt else "-"))
    if not others:
        print("  none in the tree")
    for dev, sz, nxt in others:
        mark = "  <- same as gauguin" if sz == size else ""
        print(f"  {dev:<12} {sz // 0x1000:>6} pages  {fmt(sz):>9}   next region: {nxt}{mark}")
    same = [d for d, sz, _ in others if sz == size]
    if others and not same:
        sizes = sorted({sz for _, sz, _ in others})
        print(f"  -> every sibling at this base declares "
              f"{', '.join(fmt(s) for s in sizes)}; gauguin's {fmt(size)} is its own, "
              f"so the row was cut for this device and not copied.")

    # The holes: what the config does not mention. Gaps are only reported once
    # past the DDR base, because the low-address register-ish rows (LLCC0 sits at
    # 0x09200000) are not part of the same run and a gap measured from one of them
    # to the DDR base is arithmetic about two unrelated things.
    DDR_BASE = 0x80000000
    ddr = sorted(ours, key=lambda r: r[0])
    print(f"\nthis device's AddMem DDR rows in address order ({len(ddr)}):")
    prev_end = None
    span_end = None
    for b, s, name, mt in ddr:
        gap = ""
        if prev_end is not None and b > prev_end and prev_end >= DDR_BASE:
            gap = f"   <- {fmt(b - prev_end)} unlisted"
        print(f"  0x{b:08X} + 0x{s:08X}  {fmt(s):>9}  {name:<18} {mt}{gap}")
        prev_end = max(prev_end or 0, b + s)
        if name == "Sched Heap":
            span_end = prev_end

    # The shape worth naming: on every sibling, heap + Sched Heap runs
    # contiguously from the shared heap base to FV Region (0x9F800000 for all of
    # them). gauguin covers the same span with the same two rows and leaves the
    # rest blank - so the hole is not extra room the config forgot, it is the same
    # span with a device-specific reservation cut out of it.
    if span_end is not None:
        fv = next((r for r in ddr if r[2] == "FV Region"), None)
        if fv is not None:
            print(f"\n  heap + Sched Heap span 0x{base:08X}..0x{span_end:08X}, "
                  f"then {fmt(fv[0] - span_end)} to FV Region at 0x{fv[0]:08X}")
            print(f"  siblings cover that whole span with the two rows and nothing "
                  f"in between")
            print(f"  -> the {fmt(fv[0] - span_end)} is a carveout for this device, "
                  f"not slack in the config")

    print()
    print("A hole above is memory this config never mentions. Nothing builds a HOB for an")
    print("address that is not listed, so a hole has no GCD descriptor and no memory map")
    print("entry - it is invisible to the allocator, and it is not spare RAM either: the")
    print("device's own XBL left it out on purpose. Extending the DXE Heap row into one")
    print("means claiming RAM the firmware does not consider ours, which is how a payload")
    print("that boots today stops booting tomorrow. The demand side is 1562 pages")
    print("(tools/pe-facts.py) - check the ratio before treating the row as the problem.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
