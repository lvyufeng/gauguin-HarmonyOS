#!/usr/bin/env python3
"""Give our device tree the `/__symbols__` the phone's board overlay demands.

The problem is not ours - it is a property of the phone, and the phone boots
Android with it. This device's `dtbo` partition validates, which puts ABL on the
*overlay* path rather than the "use the tree in the boot image as it is" one, and
from there:

    1. GetSocDtb()   picks a tree out of the boot image's DTB slot by qcom,msm-id.
                     It only skips the overlay when the tree it picked sets every
                     bit of ALL_BITS_SET, which includes a PMIC model match at all
                     sixteen indices and SOFTSKU_EXACT_MATCH - so it needs
                     qcom,pmic-id and qcom,softsku-id. No tree in this device's
                     boot image declares either. That branch is dead on this
                     hardware, for the stock payload as much as for ours.
    2. GetBoardDtb() ranks the 19 dtbo entries by the *device's* variant id (0x23,
                     from the CDT) and lands on entry 13, "Qualcomm Technologies,
                     Inc. Gauguin" - the only entry carrying it.
    3. ApplyOverlay() hands both to ufdt_overlay_do_fixups(), which resolves the
                     overlay's `/__fixups__` against the *main* tree's
                     `/__symbols__` and returns -1 if either node is missing.

Entry 13 is a dtc `-@` overlay: 79 fragments whose `target = <0xffffffff>` is a
placeholder, and a `__fixups__` node saying which symbol each placeholder stands
for. So step 3 fails on a tree with no `/__symbols__`, ABL prints

    ApplyOverlay: ufdt apply overlay failed

and returns EFI_NOT_FOUND before the kernel's first instruction - a refusal that
looks exactly like a payload that never ran.

Our tree is built without `-@`. scripts/Makefile.dtbs only adds that flag to
trees listed in base-dtb-y, and ours is a local board file, so it carries no
symbols at all - measured: 71,714 bytes, root properties `interrupt-parent` only.
Building with `-@` is necessary but not sufficient: it yields 294 symbols from our
own labels (tlmm, mdss_mdp, soc) of which only 19 match the overlay's 158, because
the overlay is written against the vendor tree's names (BOB, L11A, pm7250b_charger,
cam_sensor_*, ...). What the gate actually needs is narrower than that:

  * `ufdt_do_one_fixup()` reads the phandle of the node each symbol resolves to and
    patches it over the placeholder, and
  * `ufdt_overlay_apply_fragment()` then merges the fragment into whatever node
    that phandle belongs to.

So the *content* of our tree is irrelevant to whether the overlay applies. Only the
existence of the symbols is. This generates one empty node per needed symbol, each
carrying a phandle, plus a `/__symbols__` that names them - so the vendor overlay
applies cleanly and lands entirely inside a dummy subtree. Measured against entry
13 with fdtoverlay: 0 nodes added outside `/__sink__`, 0 lost, and the only
properties changed anywhere are `/__symbols__` entries now pointing into it.

It emits the *union* of every entry's fixups, not just entry 13's: 158 symbols for
the board overlay, 217 across all 19. Absorbing the rest costs a few hundred bytes
and bounds the risk that a dtbo entry we cannot predict offline names something
entry 13 does not.

Usage:  tools/make_dtbo_sinks.py [-o work/out/gauguin-dtbo-sinks.dtsi]
                                [--dtbo ~/backup/gauguin/images/part-dtbo.img]
                                [--base 0x8000]

The output is a dtc fragment meant to be *appended* to the board dts, which is
what tools/build-p1-payloads.sh does - a second `/ { ... };` block at top level
merges into the root node. It is generated rather than tracked because it is a
function of the phone's dtbo dump, which is not in this repository.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fdt      # noqa: E402
import gauguin  # noqa: E402

# Where the sink phandles start. libufdt resolves a fragment's target through a
# phandle table built from the main tree, so a sink phandle that collides with a
# real node's would send the vendor overlay into that node instead. dtc allocates
# phandles sequentially from 1, and this tree has 196 of them topping out at 0xc4,
# so 0x8000 is far above anything it can reach - and tools/abl-boot-check.py
# checks the built tree for a collision rather than trusting that.
DEFAULT_BASE = 0x8000

DEFAULT_DTBO = "~/backup/gauguin/images/part-dtbo.img"


def needed(entries):
    """{symbol: [entry indices that ask for it]} across every valid entry."""
    syms = {}
    for e in entries:
        if not e.get("ok"):
            continue
        for s in e.get("fixups") or {}:
            syms.setdefault(s, []).append(e["index"])
    return syms


def render(syms, base, dtbo):
    """The dtsi: one empty node per symbol, and the `__symbols__` naming them."""
    names = sorted(syms)
    out = [
        "/*",
        f" * Generated by tools/make_dtbo_sinks.py from {dtbo} - do not edit.",
        " *",
        f" * {len(names)} symbols, the union of every dtbo entry's __fixups__.",
        " * The vendor board overlay resolves each of these against /__symbols__",
        " * before any fragment is applied, and refuses the whole boot if one is",
        " * missing. Naming them into empty nodes is enough: only the phandle the",
        " * symbol resolves to is used, and the overlay lands in the sink.",
        " */",
        "/ {",
        "\t__sink__ {",
    ]
    for i, s in enumerate(names):
        who = ", ".join(str(x) for x in syms[s])
        out.append(f"\t\ts{i:03d} {{ phandle = <{base + i:#x}>; }};"
                   f"\t/* {s} (dtbo entry {who}) */")
    out.append("\t};")
    out.append("")
    out.append("\t__symbols__ {")
    for i, s in enumerate(names):
        out.append(f'\t\t{s} = "/__sink__/s{i:03d}";')
    out.append("\t};")
    out.append("};")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default="work/out/gauguin-dtbo-sinks.dtsi")
    ap.add_argument("--dtbo", default=DEFAULT_DTBO)
    ap.add_argument("--base", default=hex(DEFAULT_BASE),
                    help=f"first sink phandle (default {DEFAULT_BASE:#x})")
    a = ap.parse_args()

    path = os.path.expanduser(a.dtbo)
    if not os.path.exists(path):
        print(f"{path}: no dtbo dump - pass --dtbo. Without it there is no way to "
              f"know which symbols the phone's overlay needs.", file=sys.stderr)
        return 2

    ok, fields, reason, entries = fdt.dtbo_table(path)
    if not ok:
        # Not an error: a dtbo that does not validate is the *other* ABL path, and
        # on that path no overlay is applied and no symbols are needed.
        print(f"{path}: does not validate ({reason})", file=sys.stderr)
        print("-> ABL would use the tree in the boot image as it stands; no "
              "symbols are needed", file=sys.stderr)
        open(a.out, "w").write("/* no dtbo overlay on this device - see "
                               "tools/make_dtbo_sinks.py */\n")
        return 0

    d = open(path, "rb").read()
    parsed = fdt.dtbo_entries(d, entries)
    syms = needed(parsed)
    if not syms:
        open(a.out, "w").write("/* no dtbo entry carries __fixups__ - see "
                               "tools/make_dtbo_sinks.py */\n")
        print(f"{path}: {fields['entry_count']} entries, none with __fixups__; "
              f"nothing to absorb")
        return 0

    base = int(a.base, 0)
    open(a.out, "w").write(render(syms, base, path))
    # Entry 13 is the board overlay, and the board overlay is the one that has to
    # apply for the phone to boot at all - see gauguin.GAUGUIN_VARIANT_ID.
    board = [e["index"] for e in parsed
             if e.get("ok") and (e.get("board_id") or (None,))[0]
             == gauguin.GAUGUIN_VARIANT_ID]
    ours = sum(1 for s, who in syms.items() if set(who) & set(board))
    print(f"{path}: {fields['entry_count']} entries, "
          f"{sum(len(e.get('fixups') or {}) for e in parsed if e.get('ok'))} fixups "
          f"-> {len(syms)} distinct symbols")
    print(f"   {ours} of them are the board overlay's "
          f"(entry {board[0] if board else '?'}, the only one carrying "
          f"board-id {gauguin.GAUGUIN_VARIANT_ID:#x}); "
          f"phandles {base:#x}-{base + len(syms) - 1:#x}")
    print(f"   wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
