#!/usr/bin/env python3
"""Count what the reference tables put in a `_DEP`, reproducibly.

`tools/acpi/gauguin.asl` writes seven `_DEP`s and its comments state the
corpus's behaviour as numbers: how many engine nodes of each protocol exist,
how many write `{PEP0}` and nothing else, how many name a GPI DMA controller,
how many name an SMMU; and, for the two nodes whose entry was a split, which
`_HID`s fall on which side. Those numbers were first measured with a throwaway
script in `/tmp`, and a throwaway script is how a number stops being
reproducible - the rule this repository states in `acpi-hid-census.disassemble`'s
docstring is that a number a reader cannot ask again from the repository is not
a measurement. Step 4.94 is what came of running the same questions again from
here: the first reading had counted this table as part of the family it was
measuring, so every one of its totals moved by this table's own contribution,
and the two statements that disagreed inside the same comment - "43 of 53" in
the table and "52 of the 53" in the prose above it - were the same mistake seen
twice.

The unit is the **node**, attributed by a single brace-stack pass over the
disassembly, and a node's `_DEP` is the declaration the walk finds directly
inside it rather than the first `Name (_DEP` anywhere after its header. That
distinction matters here for the same reason it does in
`tools/acpi-usb-pair-census.py`: a table that nests one device inside another
would otherwise attribute the inner node's dependency to the outer one, and the
count would be right by accident on this corpus and wrong on the next.

The corpus can contain *this* table - it is a Mu-Silicium checkout's
`Silicium-ACPI` submodule and `tools/sync-uefi-platform.sh` installs ours into
it - and a census of the family that includes the file being written is not a
census. It is therefore left out by default, the same rule and default as
`tools/acpi-order-votes.py` and `tools/acpi-usb-pair-census.py`, and
`--keep-self` prints the other framing. Both are printed when they differ,
because the difference is this table's own contribution.

Usage:
    tools/acpi-dep-census.py                    # the protocol table
    tools/acpi-dep-census.py --split MMU0       # per-_HID split for a node name
    tools/acpi-dep-census.py --empty SPMI BAM1  # nodes that never carry one
    tools/acpi-dep-census.py --carrier ABD SCM0 # tables, against a PEP0 device
    tools/acpi-dep-census.py --keep-self
    tools/acpi-dep-census.py --tree DIR --cache DIR
"""

import argparse
import collections
import importlib.util
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ASL = os.path.join(REPO, "tools/acpi/gauguin.asl")

# The name families the protocol table is grouped by. The three are the three
# engine protocols this board's QUP block speaks, and the spellings are the
# corpus's two for each: a QUP block labels its engines `I2C<n>`/`IC<n>`,
# `SPI<n>`/`SP<n>` and `UAR<n>`/`UR<n>`, and the two forms are the same engine
# at the same address in different generations rather than different nodes.
ORTHO = re.compile(r"^(I2C[0-9A-F]|IC[0-9A-F]{2}|SPI[0-9A-F]|SP[0-9A-F]{2}"
                   r"|UAR[0-9A-F]|UR[0-9A-F]{2})$")

PROTOCOL = (
    ("I2C / IC", re.compile(r"^(I2C[0-9A-F]|IC[0-9A-F]{2})$")),
    ("SPI / SP", re.compile(r"^(SPI[0-9A-F]|SP[0-9A-F]{2})$")),
    ("UART",     re.compile(r"^(UAR[0-9A-F]|UR[0-9A-F]{2})$")),
)

# How a `_DEP` reads once the braces are off: what `shape` sorts a node's
# package into. The declaration's *kind* - `Name` or `Method` - is carried in
# the row and is deliberately not one of the four, because the two kinds do not
# produce different packages here: all 451 `Method (_DEP)` bodies in the 65
# reference tables are a `Return (Package (0x01) { ... })` and nothing else, so
# for the question these columns ask - which name does this node depend on -
# `Method` and `Name` are the same answer written twice.
#
# This comment said the opposite until Step 4.94: that a `Method` form "is not a
# package at all" and is "reported as its own shape". It is a package, it is
# reported as one of these four, and the claim had never been checked against
# the corpus it is about. That is the same fault the step was written to fix, so
# it is recorded here rather than quietly reworded.
HID = re.compile(r'Name \(_HID, (?:EisaId \()?"([^"]+)"')
ENTRY = re.compile(r"[A-Za-z_0-9]{4}$")
PKG = re.compile(r"Package\s*\([^)]*\)\s*\{")


def package(text, offset):
    """The names inside the first `Package (...) {...}` at or after `offset`.

    `_DEP` does not have one body shape. `Name (_DEP, Package (0x01) { ... })`
    puts the brace *inside* the declaration's own parentheses, so a reader that
    looks for a brace after them - which is what `span` does, and what every
    other reading in these tools wants - finds none and reports the node as
    carrying an empty package. The package is read by its own `Package (` here
    instead, and a `Method (_DEP, ...)` that returns one lands on the same
    reading on purpose: for the shape a node is, `Return (Package (0x01)
    { PEP0 })` names PEP0 in exactly the sense `Name` does.
    """
    m = PKG.search(text, offset)
    if not m:
        return None
    i = text.index("{", m.start())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    body = text[i + 1:j]
    # An entry is usually an absolute reference - `\_SB.PEP0` - and the scope
    # prefix is the same on every one of them, so the leaf is what is kept. The
    # filter is what tells a name from the punctuation and comments the
    # disassembler leaves between the entries.
    out = []
    for e in re.split(r"[,\s]+", body.strip()):
        e = e.split(".")[-1].strip()
        if ENTRY.match(e):
            out.append(e)
    return out


def load(name):
    """Import another tool in this directory, which owns part of the reading."""
    path = os.path.join(REPO, "tools", name)
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").removesuffix(".py"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def nodes(pair_census, hid_census, files, cache):
    """[(table, name, hid, kind, entries)] for every Device in the corpus.

    `kind` is the declaration the `_DEP` is written as - `Name` or `Method` -
    and `entries` is the list of names inside its package, `None` when the node
    carries no `_DEP` at all. The body is read through `children`, so an inner
    device's `_DEP` is never credited to the outer one.
    """
    out = []
    for aml in files:
        dsl = hid_census.disassemble(aml, cache)
        if dsl is None:
            continue
        stem = os.path.basename(dsl)[:-4]
        text = pair_census.strip(open(dsl, errors="replace").read())
        items = pair_census.walk(text)
        for it in items:
            if it[0] != "Device":
                continue
            body = pair_census.span(text, it[3]) or ""
            m = HID.search(body)
            dep = [k for k in pair_census.children(items, it) if k[1] == "_DEP"]
            if len(dep) > 1:
                raise SystemExit(f"{stem}: {it[1]} carries {len(dep)} _DEPs")
            if not dep:
                out.append((stem, it[1], m.group(1) if m else None, None, None))
                continue
            kind = dep[0][0]
            entries = package(text, dep[0][3])
            out.append((stem, it[1], m.group(1) if m else None, kind, entries))
    return out


def shape(entries):
    """What a `_DEP` names, as the comment's four columns read it."""
    if entries is None:
        return "none"
    if not entries:
        return "empty"
    rest = [e for e in entries if "PEP0" not in e]
    if not rest:
        return "pep0"
    return "other"


def protocol_table(rows):
    """{protocol: Counter} over the engine nodes, by the four shapes."""
    R = collections.defaultdict(collections.Counter)
    for _, name, _, _, entries in rows:
        for label, pat in PROTOCOL:
            if pat.match(name):
                c = R[label]
                c["nodes"] += 1
                c[shape(entries)] += 1
                if entries:
                    if any("QGP" in e for e in entries):
                        c["qgp"] += 1
                    if any("MMU0" in e for e in entries):
                        c["mmu0"] += 1
                break
    return R


def split(rows, name):
    """{_HID: Counter} for one node name - which ids fall on which side."""
    R = collections.defaultdict(collections.Counter)
    for _, n, hid, _, entries in rows:
        if n != name:
            continue
        R[hid or "(none)"][shape(entries)] += 1
        if entries:
            R[hid or "(none)"]["/".join(entries)] += 1
    return R


def emptiness(rows, names):
    """{name: (nodes, with a _DEP)} - the families the corpus writes bare."""
    R = collections.defaultdict(lambda: [0, 0])
    for _, n, _, _, entries in rows:
        if n in names:
            R[n][0] += 1
            R[n][1] += entries is not None
    return R


def stem(hid_census, aml):
    """The name `nodes` files a table under - the same key `disassemble` uses."""
    rel = os.path.relpath(aml, hid_census.DEFAULT_TREE)
    return os.path.splitext(rel.replace("/", "-"))[0]


def carriers(rows, names, other, files, hid_census):
    """{name: (tables holding it, of those holding `other`, the rest)}.

    The question this answers is the one the ABD comment got wrong: it says
    Waipio is "the only table in the corpus with no PEP0 anywhere in it", and
    over the corpus 45 tables of 65 write no PEP0 device at all. The sentence is
    true of the family being discussed and false of the corpus, and only the
    second column - how many of the tables holding *this* device also hold PEP0
    - tells the two apart.

    `other` is a device name, and a table "holds" it when it declares a device
    of that name, which is the reading that matters to a `_DEP` entry: an entry
    is a path, and `\\_SB.PEP0` resolves in exactly the tables that declare the
    device. A table that declares no device at all is counted apart from one
    that declares devices and this one is not among them, because the two are
    different facts about a table and the comment that was wrong confused them.
    """
    per = collections.defaultdict(set)
    for s, n, _, _, _ in rows:
        per[s].add(n)
    stems = [stem(hid_census, f) for f in files]
    R = {}
    for name in names:
        have = [s for s in stems if name in per[s]]
        also = [s for s in have if other in per[s]]
        R[name] = (len(have), len(also), [s for s in have if s not in also])
    none = [s for s in stems if other not in per[s]]
    return R, len(stems), none, [s for s in stems if not per[s]]


def report_protocol(R, label):
    print(f"== engines, by protocol  [{label}]")
    print(f"   {'protocol':12s} {'nodes':>6s} {'{PEP0}':>7s} {'none':>5s} "
          f"{'other':>6s} {'QGP tail':>9s} {'MMU0':>5s}")
    for lab, _ in PROTOCOL:
        c = R[lab]
        print(f"   {lab:12s} {c['nodes']:6d} {c['pep0']:7d} {c['none']:5d} "
              f"{c['other']:6d} {c['qgp']:9d} {c['mmu0']:5d}")


def report_split(R, name, label):
    print(f"== {name}, by _HID  [{label}]")
    for hid in sorted(R):
        c = R[hid]
        sides = {k: v for k, v in c.items()
                 if k in ("pep0", "none", "other", "empty")}
        pkgs = {k: v for k, v in c.items() if "/" in k}
        total = sum(sides.values())
        print(f"   {hid:10s} {total:3d}  " +
              "  ".join(f"{k}={v}" for k, v in sorted(sides.items())) +
              (f"   packages: {pkgs}" if pkgs else ""))


def report_empty(R, label):
    print(f"== nodes that carry no _DEP anywhere  [{label}]")
    for name in sorted(R):
        total, with_dep = R[name]
        print(f"   {name:6s} {total:3d} nodes, {with_dep} carrying one"
              + ("" if with_dep == 0 else "   <-- not empty"))


def report_carriers(R, tables, none, deviceless, other, label):
    print(f"== tables holding a device, against `{other}`  [{label}]")
    print(f"   {tables} tables, {len(none)} of them declaring no {other} device"
          + (f" ({len(deviceless)} of those declare no device at all)"
             if deviceless else ""))
    for name in sorted(R):
        have, also, rest = R[name]
        print(f"   {name:6s} {have:3d} tables carry it, {also} also name {other}"
              + (f", and {len(rest)} do not: {rest}" if rest else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--asl", default=DEFAULT_ASL,
                    help="the table that is *ours*, dropped unless --keep-self")
    ap.add_argument("--tree", help="the Silicium-ACPI corpus root")
    ap.add_argument("--cache", help="the disassembly cache directory")
    ap.add_argument("--keep-self", action="store_true",
                    help="count our own table as part of the corpus")
    ap.add_argument("--split", action="append", default=[],
                    metavar="NAME", help="per-_HID split for a node name")
    ap.add_argument("--empty", nargs="+", default=[],
                    metavar="NAME", help="family names to check for any _DEP")
    ap.add_argument("--carrier", nargs="+", default=[], metavar="NAME",
                    help="tables holding this device, against --other")
    ap.add_argument("--other", default="PEP0",
                    help="the device --carrier counts against (default PEP0)")
    ap.add_argument("--protocol", action="store_true",
                    help="the protocol table (the default when nothing else is)")
    args = ap.parse_args()

    pair = load("acpi-usb-pair-census.py")
    hid = load("acpi-hid-census.py")
    tree = args.tree or hid.DEFAULT_TREE
    if not os.path.isdir(tree):
        print(f"no such tree: {tree}", file=sys.stderr)
        return 1
    cache = args.cache or hid.DEFAULT_CACHE
    # `disassemble` keys the cache by each table's path relative to this, so
    # both have to be the ones in force rather than passed around.
    hid.DEFAULT_TREE = tree
    hid.DEFAULT_CACHE = cache

    all_files = hid.table_files(tree)
    # The platform directory our table is filed under is its own stem, the same
    # rule the other two tools drop by.
    drop = None if args.keep_self else \
        os.path.splitext(os.path.basename(args.asl))[0]
    keep, dropped = [], []
    for aml in all_files:
        rel = os.path.relpath(aml, tree)
        if drop and re.search(r"(^|/)" + re.escape(drop) + r"(/|$)",
                              os.path.dirname(rel)):
            dropped.append(aml)
        else:
            keep.append(aml)

    print(f"corpus: {len(all_files)} tables in {os.path.relpath(tree, REPO)}")
    if dropped:
        print(f"        {len(dropped)} left out as our own: "
              + ", ".join(os.path.relpath(p, tree) for p in dropped))
    print()

    want_protocol = args.protocol or not (args.split or args.empty
                                          or args.carrier)
    for label, files in (("corpus less our own table" if dropped else "corpus",
                          keep), ("corpus including it", keep + dropped)):
        rows = nodes(pair, hid, files, cache)
        print(f"### {label} ({len(files)} tables)\n")
        if want_protocol:
            report_protocol(protocol_table(rows), label)
            print()
        for name in args.split:
            report_split(split(rows, name), name, label)
            print()
        if args.empty:
            report_empty(emptiness(rows, set(args.empty)), label)
            print()
        if args.carrier:
            R, tables, none, deviceless = carriers(rows, args.carrier,
                                                   args.other, files, hid)
            report_carriers(R, tables, none, deviceless, args.other, label)
            print()
        if not dropped:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
