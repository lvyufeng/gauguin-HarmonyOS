#!/usr/bin/env python3
"""Count what the reference tables do with the ADC block, reproducibly.

`tools/acpi/gauguin.asl` is about to declare `ADC1` - the device `QCOM0A11`
names, which `qcadc7280.inf` claims - and the node's comment states the corpus's
behaviour as numbers: how many tables declare an ADC, what the four members of
the single-ADC tables are, what the twelve bytes of `VUSR` and `VBTM` contain
and how they divide between the tables that carry one ADC and the tables that
carry three, where the block sits in the table, and how far the `_CRS` shape is
unanimous.

Those numbers were first read with a throwaway script in `/tmp`, and the first
reading was wrong in a way worth keeping: iasl writes a twelve-byte `Name` as
`Name (VUSR, Buffer (0x0C)` and closes the *outer* parenthesis only after the
buffer's closing brace, so a pattern that expects `Buffer (0x0C))` matches
nothing at all - not a few rows fewer, none. The second attempt, written as a
line-oriented scan, produced numbers; this tool is what makes them a
measurement, which is the standard `acpi-hid-census.disassemble`'s docstring
already sets.

What it reads is the twelve bytes. The corpus writes them as a `Buffer (0x0C)`
with two comment-stripped rows of six, so the reading is by byte and not by
field name: bytes 0-6 are the same seven in all 70, and bytes 7 and 8 are the
two that move. Which of those two travels with what is not assumed here - it is
what the variation answers. Byte 7 moves with the pin pair and not with the
instance's ordinal, which the two-ADC tables say by skipping the middle pin pair
*and* the middle value: their second instance carries 0x04 where the three-ADC
tables' second carries 0x02. Byte 8 moves with the `_HID` and with nothing else
in the 20 tables. What the two bytes *mean* is not established here and is not
guessed at; `gauguin.asl`'s `ADC1` comment has the one correspondence the corpus
and this board's own device tree offer.

One of the tables in the corpus can be *this* table: the corpus is a
Mu-Silicium checkout's `Silicium-ACPI` submodule and
`tools/sync-uefi-platform.sh` installs ours into it. A file counting itself is
not corroboration, so it is left out by default - the same rule and the same
default as `tools/acpi-order-votes.py` and `tools/acpi-usb-pair-census.py` -
and `--keep-self` prints the other framing.

Usage:
    tools/acpi-adc-blob-census.py                 # our own copy excluded
    tools/acpi-adc-blob-census.py --keep-self     # count it as a voter
    tools/acpi-adc-blob-census.py --tree DIR --cache DIR
    tools/acpi-adc-blob-census.py --list          # one row per ADC declaration
"""

import argparse
import collections
import importlib.util
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ASL = os.path.join(REPO, "tools/acpi/gauguin.asl")

# The blob is a Name of a twelve-byte buffer, and iasl closes the outer
# parenthesis after the brace rather than before it. `.*?` against `\}` is what
# stays honest about that; `Buffer \(0x0C\)\)` is what matched nothing.
BLOB = re.compile(r"Name \((V\w+), Buffer \(0x0C\)\s*\{(.*?)\}\)", re.S)
BYTE = re.compile(r"0x([0-9A-Fa-f]{2})")

# A pin list is a four-digit literal in its own braces. The two-digit literals
# the descriptor is full of cannot be mistaken for one, which is why the width
# is the whole of the pattern.
PIN = re.compile(r"\{\s*(0x[0-9A-Fa-f]{4})\s*\}")

NAM = re.compile(r'Name \(NAM, Buffer \(0x0A\)\s*\{\s*"([^"]*)"')
HID = re.compile(r'Name \(_HID, "([^"]+)"\)')
UID = re.compile(r"Name \(_UID, (Zero|One|0x[0-9A-Fa-f]+)\)")
ADR = re.compile(r"Name \(_ADR,")
CONC = re.compile(r"Concatenate \((\w+, \w+, \w+)\)")
# `Alias` is not one of the declarations the brace-stack walk collects - it has
# no body, so it never opens a brace - and it is in the ADC's member list on
# most tables. Its *spelling* is the thing worth counting here: `\_SB.PSUB` and
# `^PSUB` decode to the same object and are written by different files.
SUB = re.compile(r"Alias \((\S+?), _SUB\)")
ADC = re.compile(r"ADC[0-9]")

CORPUS_MODULE = None      # acpi-hid-census.py: owns the disassembly and cache
WALK_MODULE = None        # acpi-usb-pair-census.py: owns the declaration walk

strip = walk = children = span = None


def load(name, filename):
    path = os.path.join(REPO, "tools", filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def census(tables, cache):
    """The counts, as a dict of dicts, over the given tables."""
    R = dict(
        tables=0, decls=0, by_name=collections.Counter(),
        hid=collections.Counter(), uid=collections.Counter(), adr=0,
        members=collections.Counter(), members_of={},
        blobs=collections.Counter(), blob_name=collections.Counter(),
        head=collections.Counter(), b7=collections.Counter(),
        b8=collections.Counter(), b8_first=collections.Counter(),
        pins=collections.Counter(), pins_first=collections.Counter(),
        nam=collections.Counter(), conc=collections.Counter(),
        sub=collections.Counter(),
        last_device=0, last_decl=0, rows=[], paths={},
    )
    for aml in tables:
        dsl = CORPUS_MODULE.disassemble(aml, cache)
        if dsl is None:
            continue
        stem = os.path.basename(dsl)[:-4]
        text = strip(open(dsl, errors="replace").read())
        items = walk(text)
        top = [i for i in items if not i[2]]
        adcs = [i for i in items if i[0] == "Device" and ADC.fullmatch(i[1])]
        if not adcs:
            continue
        R["tables"] += 1
        R["paths"][stem] = [i[1] for i in adcs]
        R["decls"] += len(adcs)
        for i in adcs:
            R["by_name"][i[1]] += 1
        # The block's place in the table: the corpus writes the ADC as the last
        # device it declares, and in the one-ADC tables as the last declaration
        # of any kind. Both are read off the same pass, by index into `top`.
        devs = [j for j, k in enumerate(top) if k[0] == "Device"]
        if devs and top[devs[-1]][1] in R["paths"][stem]:
            R["last_device"] += 1
        if top and top[-1][1] in R["paths"][stem] and top[-1][0] == "Device":
            R["last_decl"] += 1
        for i in adcs:
            body = span(text, i[3]) or ""
            kids = tuple(k[1] for k in children(items, i))
            R["members"][kids] += 1
            R["members_of"][(stem, i[1])] = kids
            R["hid"][HID.search(body).group(1) if HID.search(body) else "-"] += 1
            R["uid"][UID.search(body).group(1) if UID.search(body) else "-"] += 1
            if ADR.search(body):
                R["adr"] += 1
            pins = tuple(PIN.findall(body))
            R["pins"][pins] += 1
            R["conc"][tuple(CONC.findall(body))] += 1
            n = NAM.search(body)
            R["nam"][n.group(1) if n else "-"] += 1
            R["sub"][SUB.search(body).group(1) if SUB.search(body) else "-"] += 1
            blobs = {}
            for nm, hexs in BLOB.findall(body):
                b = BYTE.findall(hexs)
                val = " ".join(b)
                blobs[nm] = val
                R["blobs"][val] += 1
                R["blob_name"][(nm, val)] += 1
                if len(b) == 12:
                    R["head"][" ".join(b[:7])] += 1
                    R["b7"][b[7]] += 1
                    R["b8"][b[8]] += 1
                    if i[1] == "ADC1":
                        R["b8_first"][(nm, b[7], b[8])] += 1
            if i[1] == "ADC1":
                R["pins_first"][pins] += 1
            R["rows"].append(dict(
                stem=stem, name=i[1],
                hid=HID.search(body).group(1) if HID.search(body) else "-",
                uid=UID.search(body).group(1) if UID.search(body) else "-",
                members=kids, pins=pins,
                vusr=blobs.get("VUSR", "-"), vbtm=blobs.get("VBTM", "-"),
                nam=n.group(1) if n else "-",
                sub=SUB.search(body).group(1) if SUB.search(body) else "-",
                conc=len(CONC.findall(body)),
            ))
    return R


def report(R, label, list_rows=False):
    print(f"--- {label}: {R['tables']} tables")
    if not R["tables"]:
        return
    print(f"    ADC declarations              : {R['decls']} ("
          + ", ".join(f"{k} {v}" for k, v in sorted(R["by_name"].items())) + ")")
    print("    _HID                          : "
          + ", ".join(f"{k}={v}" for k, v in sorted(R["hid"].items())))
    print("    _UID                          : "
          + ", ".join(f"{k}={v}" for k, v in sorted(R["uid"].items()))
          + f", _ADR in {R['adr']}")
    print("    member lists                  : "
          + "; ".join(f"{v}x {'/'.join(k)}" for k, v in
                      R["members"].most_common()))
    print(f"    twelve-byte blobs             : {sum(R['blobs'].values())}, "
          f"{len(R['blobs'])} distinct")
    print("    blob head (bytes 0-6)         : "
          + ", ".join(f"{k} {v}" for k, v in R["head"].most_common()))
    print("    byte 7                        : "
          + ", ".join(f"0x{k} {v}" for k, v in sorted(R["b7"].items())))
    print("    byte 8, all instances         : "
          + ", ".join(f"0x{k} {v}" for k, v in sorted(R["b8"].items())))
    print("    byte 8 on the first instance  : "
          + ", ".join(f"{k[0]} 0x{k[2]} {v}" for k, v in sorted(R["b8_first"].items())))
    print("    pins on the first instance    : "
          + "; ".join(f"{'/'.join(k) or '(none)'} {v}" for k, v in
                      R["pins_first"].most_common()))
    print("    NAM                           : "
          + ", ".join(f'"{k}" {v}' for k, v in R["nam"].most_common()))
    print("    _SUB alias                    : "
          + ", ".join(f"{k} {v}" for k, v in R["sub"].most_common()))
    print("    concatenation sequences       : "
          + "; ".join(f"{v}x {' then '.join(k)}" for k, v in R["conc"].most_common()))
    print(f"    the block is the last device  : {R['last_device']} of {R['tables']}"
          f"; the last declaration of the table: {R['last_decl']}")
    if list_rows:
        for r in R["rows"]:
            print(f"      {r['stem'][:40]:40s} {r['name']} {r['hid']:9s} "
                  f"{r['uid']:5s} vbtm={r['vbtm'] or '-'} pins={r['pins']} "
                  f"nam={r['nam']} sub={r['sub']} conc={r['conc']}")


def main():
    global CORPUS_MODULE, WALK_MODULE
    global strip, walk, children, span
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", default=None, help="the Silicium-ACPI checkout")
    ap.add_argument("--cache", default=None, help="the disassembly cache")
    ap.add_argument("--asl", default=DEFAULT_ASL,
                    help="the table that is *ours*, dropped unless --keep-self")
    ap.add_argument("--keep-self", action="store_true",
                    help="count our own installed copy as a voter")
    ap.add_argument("--list", action="store_true", help="one row per declaration")
    args = ap.parse_args()

    CORPUS_MODULE = load("acpi_hid_census", "acpi-hid-census.py")
    WALK_MODULE = load("acpi_usb_pair_census", "acpi-usb-pair-census.py")
    strip = WALK_MODULE.strip
    walk = WALK_MODULE.walk
    children = WALK_MODULE.children
    span = WALK_MODULE.span
    tree = args.tree or CORPUS_MODULE.DEFAULT_TREE
    cache = args.cache or CORPUS_MODULE.DEFAULT_CACHE
    if not os.path.isdir(tree):
        print(f"no such tree: {tree}", file=sys.stderr)
        return 1
    tables = CORPUS_MODULE.table_files(tree)
    drop = None if args.keep_self else os.path.splitext(os.path.basename(args.asl))[0]

    keep, drop_t = [], []
    for aml in tables:
        rel = os.path.relpath(aml, tree)
        if drop and re.search(r"(^|/)" + re.escape(drop) + r"(/|$)",
                              os.path.dirname(rel)):
            drop_t.append(aml)
        else:
            keep.append(aml)

    print(f"corpus: {len(tables)} tables in {os.path.relpath(tree, REPO)}")
    if drop_t:
        print(f"        {len(drop_t)} left out as our own: "
              + ", ".join(os.path.relpath(p, tree) for p in drop_t))
        print()
        print("THE COUNTS BELOW EXCLUDE THE FILE BEING WRITTEN.")
        print()
    report(census(keep, cache),
           ("corpus including our own table" if args.keep_self
            else "corpus less our own table") + f" ({len(keep)} tables)", args.list)
    if drop_t:
        print()
        report(census(keep + drop_t, cache),
               f"corpus including it ({len(keep) + len(drop_t)} tables)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
