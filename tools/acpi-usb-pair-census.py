#!/usr/bin/env python3
"""Count what the reference tables do with the USB hub/port pair, reproducibly.

`tools/acpi/gauguin.asl` writes `RHUB` and `PRT1` under both of `URS0`'s
children, and the comment above `USB0`'s copy states the corpus's behaviour as
numbers: how many tables carry the pair, how many carry neither, what the two
bodies contain, what `_UPC` says under each controller, and which slot in the
parent's member list the node occupies. Those numbers were measured with a
throwaway script in `/tmp`, and this repository's own standard - stated in
`acpi-hid-census.disassemble`'s docstring - is that a number a reader cannot
reproduce from the repository is not a measurement. Step 4.91 is what came of
running the same questions again from here: seven of the numbers moved, and one
of those is not an arithmetic slip but a field the earlier reading could not
see at all, which is the argument for having the tool rather than the number.

The unit is the **declaration**, and each is attributed to the device that
encloses it by a single brace-stack pass over the disassembly, so `USB0`'s
`RHUB` and `UFN0`'s `RHUB` are two occurrences with different parents rather
than two matches of one pattern. Counting `grep -c RHUB` over the corpus gives
the same totals here and would give the wrong answer the moment a table nested
one inside something else - which is exactly what `nabu` does.

Two readings of the disassembly are needed and they do not agree: a field can
be named in the text or it can be bytes in a buffer. `PLD_GroupPosition` is
named in a `ToPLD (...)` and is a bit field at a fixed offset in a
`Buffer (0x14)`; iasl renders the second form for a `_PLD` built as a
`VarPackage`, which pipa's four are, so a name-only reading reports four
values absent that the table in fact states - two of them not zero. `_UPC` is
named in every port in this corpus, so its reading is by name alone and its
one `absent` is a port that has no `_UPC`.

Three controller bands are reported rather than two, because the pair is not
only under `URS0` and `URS1`: one occurrence in the corpus sits under a
controller whose name is neither, and a two-band reading has to file it
somewhere wrong. `A` is `USB0`/`UFN0`, `B` is `USB1`/`UFN1`, `X` is anything
else, and `X` is one row here - which is a fact about the corpus, not a
rounding.

One of the tables in the corpus can be *this* table: the corpus is a
Mu-Silicium checkout's `Silicium-ACPI` submodule and
`tools/sync-uefi-platform.sh` installs ours into it, so ours is one of the
sixty-six. A file counting itself is not corroboration, so it is left out by
default - the same rule and the same default as `tools/acpi-order-votes.py` -
and `--keep-self` prints the other framing. Both framings are printed side by
side, because the difference *is* the finding: it is exactly the contribution
of the file being written.

Usage:
    tools/acpi-usb-pair-census.py                 # our own copy excluded
    tools/acpi-usb-pair-census.py --keep-self     # count it as a voter
    tools/acpi-usb-pair-census.py --tree DIR --cache DIR
    tools/acpi-usb-pair-census.py --list          # which tables, and their paths
"""

import argparse
import collections
import importlib.util
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ASL = os.path.join(REPO, "tools/acpi/gauguin.asl")

# The declarations a device body can hold. `If`, `While`, `Buffer` and `Package`
# open braces too, and they are deliberately absent: a brace they open is a
# scope, not a device, and the walk pushes an empty frame for it.
DECL = re.compile(
    r"(Device|ThermalZone|Name|Method|Scope|PowerResource|External)"
    r"\s*\(\s*([A-Z0-9_]{4})")

BAND_A = ("USB0", "UFN0")   # \_SB.URS0's two controllers
BAND_B = ("USB1", "UFN1")   # \_SB.URS1's, which this table does not have


def strip(text):
    """The disassembly with its comments removed, strings left alone.

    Both comment forms have to go: `iasl -d` writes `// _HID: Hardware ID`
    after a declaration and `/* \\_SB_.TZ0_.TTSP */` after the object a method
    returns, and the second form is where a brace inside a comment would
    otherwise close a device. Strings are kept because the brace inside
    `ToUUID ("...")` is not a brace either.
    """
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def walk(text):
    """[(kind, name, path, offset)] for every declaration with a body.

    `path` is the tuple of enclosing *device* names, outermost first, and it is
    what makes an occurrence attributable: the `RHUB` under `USB0` and the one
    under `UFN0` differ in it and in nothing else.

    One pass, one stack. A declaration whose closing parenthesis is followed by
    `{` pushes its own name; a brace opened by anything else - `If`, `While`,
    `Buffer`, `Package`, `ResourceTemplate` - pushes `None`, so the pop that
    closes it cannot unbalance the device chain.
    """
    out = []
    stack = []
    pending = None
    i = 0
    n = len(text)
    while i < n:
        m = DECL.match(text, i)
        if m and not (i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_")):
            kind, name = m.group(1), m.group(2)
            out.append((kind, name, tuple(x for x in stack if x), m.start()))
            j = m.end()
            depth = 1
            while j < n and depth > 0:
                c = text[j]
                if c == '"':
                    j += 1
                    while j < n and text[j] != '"':
                        j += 2 if text[j] == "\\" else 1
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                j += 1
            k = j
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] == "{":
                pending = name
            i = j
            continue
        c = text[i]
        if c == "{":
            stack.append(pending)
            pending = None
        elif c == "}":
            if stack:
                stack.pop()
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        i += 1
    return out


def children(items, item):
    """The declarations directly inside `item`: depth one deeper, same prefix."""
    return [k for k in items if k[2] == item[2] + (item[1],)]


def parent(item):
    return item[2][-1] if item[2] else None


def band(item):
    chain = set(item[2]) | {item[1]}
    if chain & set(BAND_A):
        return "A"
    if chain & set(BAND_B):
        return "B"
    return "X"


UPC = r"_UPC,\s*Package \(0x04\)\s*\{([^}]*)\}"
GROUP = r"PLD_GroupPosition\s*=\s*(0x[0-9A-Fa-f]+|\d+)"
RAWPLD = r"Buffer \(0x14\)\s*\{([^}]*)\}"
HEXBYTE = re.compile(r"0x([0-9A-Fa-f]{2})")


def group_of(body):
    """(value, how) for a `_PLD`'s GroupPosition, or (None, None).

    Read by name when the disassembler rendered a `ToPLD`, and otherwise out
    of the twenty bytes themselves, because the two are not the same set.
    iasl prints an unexpanded `Buffer (0x14)` for a `_PLD` built as a
    `VarPackage` even when the bytes are exactly the ones `ToPLD` would have
    emitted - pipa's four ports are the case in this corpus - so a reader that
    only knows the field names reports four values as missing that are in the
    table. The field is seven bits at bit 7 of bytes 10-11, little endian,
    measured by compiling `ToPLD` at 0, 1, 2, 3, 4, 0x0F, 0x1F and 0x7F and
    reading the bytes back.
    """
    m = re.search(GROUP, body)
    if m:
        return m.group(1), "name"
    m = re.search(RAWPLD, body)
    if not m:
        return None, None
    b = [int(x, 16) for x in HEXBYTE.findall(m.group(1))]
    if len(b) != 0x14:
        return None, None
    return f"0x{(b[10] | (b[11] << 8)) >> 7:X}", "raw"


def census(tables, cache):
    """The counts, as a dict of dicts, over the given tables."""
    R = dict(
        tables=0, pair=0, neither=0, hub_only=[], name_only=[],
        hub=0, hub_bare=0, hub_bare_odd=[],
        port=0, port_three=0, port_odd=[],
        upc=collections.Counter(), group=collections.Counter(),
        group_raw=collections.Counter(), group_odd=[],
        slot=collections.Counter(), usb0=0, ufn0=0, paths={},
    )
    for aml in tables:
        dsl = disassemble(aml, cache)
        if dsl is None:
            continue
        stem = os.path.basename(dsl)[:-4]
        text = strip(open(dsl, errors="replace").read())
        items = walk(text)
        hubs = [i for i in items if i[0] == "Device" and i[1] == "RHUB"]
        ports = [i for i in items if i[0] == "Device" and i[1] == "PRT1"]
        named = [i for i in items if i[1] == "PRT1" and i[0] != "Device"]
        R["tables"] += 1
        R["paths"][stem] = [i[2] + (i[1],) for i in hubs]
        if hubs:
            R["hub"] += len(hubs)
        if ports:
            R["port"] += len(ports)
        parents = {parent(i) for i in hubs}
        if hubs and ports and {"USB0", "UFN0"} <= parents:
            R["pair"] += 1
        if not hubs and not ports:
            R["neither"] += 1
            if named:
                R["name_only"].append(stem)
        if hubs and not ports:
            R["hub_only"].append(stem)
        for i in hubs:
            kids = [(k[0], k[1]) for k in children(items, i)]
            # "bare" is the ADR plus the port and nothing else: the hub's own
            # body is one member, and the second is the device it holds.
            if kids == [("Name", "_ADR"), ("Device", "PRT1")]:
                R["hub_bare"] += 1
            else:
                R["hub_bare_odd"].append((stem, tuple(kids)))
        for i in ports:
            kids = [(k[0], k[1]) for k in children(items, i)]
            if kids == [("Name", "_ADR"), ("Name", "_UPC"), ("Name", "_PLD")]:
                R["port_three"] += 1
            else:
                R["port_odd"].append((stem, "/".join(i[2] + (i[1],)), tuple(kids)))
            # the body, not a window from the header: a `_UPC` search anchored
            # at a device header would otherwise read the next device's, which
            # is what the one PRT1 without a `_UPC` in the corpus shows.
            body = span(text, i[3]) or ""
            m = re.search(UPC, body)
            R["upc"][(band(i), re.sub(r"\s+", " ", m.group(1)).strip()
                      if m else "absent")] += 1
            val, how = group_of(body)
            R["group"][(band(i), val if val else "absent")] += 1
            if how == "raw":
                R["group_raw"][(band(i), val)] += 1
            elif how is None:
                R["group_odd"].append((stem, "/".join(i[2] + (i[1],))))
        for i in hubs:
            par = parent(i)
            if par not in BAND_A:
                continue
            if par == "USB0":
                R["usb0"] += 1
            else:
                R["ufn0"] += 1
            host = [k for k in items
                    if k[0] == "Device" and k[1] == par and k[2] == i[2][:-1]]
            if not host:
                continue
            members = [k[1] for k in children(items, host[0])]
            if "RHUB" in members:
                R["slot"][(par, len(members), members.index("RHUB"))] += 1
    return R


def span(text, offset):
    """The `{...}` body of the declaration starting at `offset`, or None.

    The declaration's own argument list is skipped first, so `Device (PRT1)` is
    read as its body and `External (PRT1, DeviceObj)` - which has none - comes
    back None rather than as whatever brace follows it in the table.
    """
    i = offset
    n = len(text)
    while i < n and text[i] != "(":
        if text[i] == "{":
            break
        i += 1
    if i < n and text[i] == "(":
        depth = 1
        i += 1
        while i < n and depth:
            c = text[i]
            if c == '"':
                i += 1
                while i < n and text[i] != '"':
                    i += 2 if text[i] == "\\" else 1
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n or text[i] != "{":
        return None
    depth = 0
    j = i
    while j < n:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return None


def load_census_module():
    """Import tools/acpi-hid-census.py: it owns the tree walk and the cache."""
    path = os.path.join(REPO, "tools/acpi-hid-census.py")
    spec = importlib.util.spec_from_file_location("acpi_hid_census", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


disassemble = None


def report(R, label, list_paths=False):
    print(f"--- {label}: {R['tables']} tables")
    print(f"    pair under both USB0 and UFN0 : {R['pair']}")
    print(f"    declare neither device        : {R['neither']}"
          + (f"  (of which {len(R['name_only'])} name a PRT1 without declaring one)"
             if R["name_only"] else ""))
    print(f"    hub and no port               : {R['hub_only']}")
    print(f"    RHUB occurrences              : {R['hub']}, bare (_ADR + PRT1) {R['hub_bare']}"
          f", other {R['hub_bare_odd']}")
    print(f"    PRT1 occurrences              : {R['port']}, _ADR+_UPC+_PLD {R['port_three']}"
          f", other {R['port_odd']}")
    print("    _UPC by band                  : "
          + ", ".join(f"{k[0]}:{k[1]}={v}" for k, v in sorted(R["upc"].items())))
    print("    PLD_GroupPosition by band     : "
          + ", ".join(f"{k[0]}:{k[1]}={v}" for k, v in sorted(R["group"].items()))
          + (f"  [{', '.join(f'{k[0]}:{k[1]}={v} read from the raw buffer' for k, v in sorted(R['group_raw'].items()))}]"
             if R["group_raw"] else ""))
    if R["group_odd"]:
        print(f"    ports whose _PLD states no GroupPosition at all: {R['group_odd']}")
    print(f"    hubs under USB0 {R['usb0']}, under UFN0 {R['ufn0']}")
    for par in ("USB0", "UFN0"):
        idx = collections.Counter()
        for (p, nmem, i), v in R["slot"].items():
            if p == par:
                idx[i] += v
        tot = sum(idx.values())
        win = max(idx.items(), key=lambda kv: kv[1])[0] if idx else None
        ordi = {0: "first", 1: "second", 2: "third", 3: "fourth", 4: "fifth"}
        print(f"    {par} slot: " + ", ".join(f"{ordi.get(i, i)}={v}" for i, v in sorted(idx.items()))
              + f"  -> {ordi.get(win, win)} in {idx[win]} of {tot}")
    if list_paths:
        for stem, paths in sorted(R["paths"].items()):
            if paths:
                print(f"      {stem}: " + "; ".join("/".join(p) for p in paths))


def main():
    global disassemble
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", default=None, help="the Silicium-ACPI checkout")
    ap.add_argument("--cache", default=None, help="the disassembly cache")
    ap.add_argument("--asl", default=DEFAULT_ASL,
                    help="the table that is *ours*, dropped unless --keep-self")
    ap.add_argument("--keep-self", action="store_true",
                    help="count our own installed copy as a voter")
    ap.add_argument("--list", action="store_true", help="the per-table paths")
    args = ap.parse_args()

    mod = load_census_module()
    disassemble = mod.disassemble
    tree = args.tree or mod.DEFAULT_TREE
    cache = args.cache or mod.DEFAULT_CACHE
    if not os.path.isdir(tree):
        print(f"no such tree: {tree}", file=sys.stderr)
        return 1
    tables = mod.table_files(tree)
    # The platform directory the corpus files this table under is its own stem,
    # the same rule tools/acpi-order-votes.py drops by.
    drop = None if args.keep_self else os.path.splitext(os.path.basename(args.asl))[0]

    keep, drop_t = [], []
    for aml in tables:
        stem = os.path.basename(aml)
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
           f"corpus less our own table ({len(keep)} tables)", args.list)
    if drop_t:
        print()
        report(census(keep + drop_t, cache),
               f"corpus including it ({len(keep) + len(drop_t)} tables)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
