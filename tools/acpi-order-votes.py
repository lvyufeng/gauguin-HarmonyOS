#!/usr/bin/env python3
"""Score gauguin's device declaration order against the corpus, and relocate it.

`tools/acpi/gauguin.asl` was not written in one sitting; it was assembled node by
node, each one argued against the reference tables in Silicium-ACPI, and each one
appended where it was easiest to write. Nothing ever compared the *order* of that
assembly to the order the corpus uses — and the corpus does state an order:
twenty-one tables declare `ABD`, and about every pair of gauguin's node names
they can agree on which comes first. Nine of them state `SPMI` after `SCM0`,
twenty of them state `UCS0` last, and so on.

A pair the corpus agrees on is a *relation*, and a relation gauguin's file
reverses is a defect in a file whose method is "the corpus is the specification".
Step 4.84 counted those defects (128 broken relations over seven placements) and
recorded them as a debt; this tool is how the debt is measured and how the order
that pays it is chosen.

The unit of measurement is the **table-vote**. A table that declares both nodes
of a pair votes on that pair's direction; a pair's weight is the number of tables
voting on it. Two framings are printed, because the earlier steps used the first
and the reorder was chosen under the second:

  unanimity   a pair is a relation only if no table contradicts it. The file
              either satisfies such a relation or breaks it, and "broken" is a
              defect that cannot be argued away. This is the 128 of Step 4.84.

  majority    every pair counts. A table that dissents alone does not delete a
              pair from the accounting; it lowers what any order can reach. The
              ceiling is the *pairwise upper bound* - for each pair, the larger
              of its two counts, summed - and an order that attains it cannot be
              improved by any permutation. This framing is what makes an order
              provably optimal rather than merely good, and it is why a lone
              dissenting table shows up as an unavoidable cost instead of as a
              hole in the data.

`--moves` turns a target order into the minimal set of relocations from the
file's order, by longest common subsequence: the nodes not in the LCS are the
ones that move, and each one is re-inserted at the index the target gives it.
`--fixed-point` asks the complementary question of the *result*: take each node
out in turn and re-insert it at every index, and report whether the slot it
occupies is among the ones that maximise the score. An order that is a fixed
point of that operation is one where no single node is misplaced, which is a
different claim from being the global optimum - and where a node has no relation
at all, the two disagree, which is worth seeing rather than hiding.

One of the tables the corpus offers can be *this* table: the corpus is a
Mu-Silicium checkout's `Silicium-ACPI` submodule and the sync script installs
ours into it. A file scoring itself is not a measurement, so that copy is left
out by default; `--keep-self` puts it back, and `corpus_nodes`'s last paragraph
has what the difference was measured to be.

Usage:
    tools/acpi-order-votes.py                        # current order, every subset
    tools/acpi-order-votes.py --subset ABD           # only the 21 ABD tables
    tools/acpi-order-votes.py --order "UFS0 DEV0 ..."
    tools/acpi-order-votes.py --order "$(python3 -c ...)" --moves
    tools/acpi-order-votes.py --fixed-point
    tools/acpi-order-votes.py --prove                # the ceiling and who dissents
    tools/acpi-order-votes.py --asl /tmp/candidate.asl
    tools/acpi-order-votes.py --keep-self            # count our own copy as a voter

The corpus itself is read from the cached disassembly `tools/acpi-hid-census.py`
writes (36 reference trees, ~90 s of `iasl -d`); that tool owns the disassembly
and the table list, and this one imports both rather than repeating them.
"""

import argparse
import collections
import importlib.util
import itertools
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEVICE = re.compile(r"^(\s*)Device \(([A-Z0-9_]+)\)", re.M)

# Subsets that the earlier steps measured on. A subset is defined by a node
# every table in it must declare, so the subsets are not nested: the 20 tables
# carrying PRTC are not a subset of the 21 carrying ABD.
SUBSETS = {
    "all": None,
    "ABD": "ABD",
    "SPMI": "SPMI",
    "PRTC": "PRTC",
    "QGP0": "QGP0",
}


def load_census():
    """Import tools/acpi-hid-census.py, which owns the disassembly and the cache."""
    path = os.path.join(ROOT, "tools/acpi-hid-census.py")
    spec = importlib.util.spec_from_file_location("acpi_hid_census", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_devices(path):
    """[(name, indent, line)] in declaration order, nested devices included.

    Nested devices are declared inside their parent (`UFS0`'s `DEV0`, `URS0`'s
    `USB0` and `UFN0`) and cannot be moved independently of it, which is why the
    line list carries the indent and `units()` below collapses them.
    """
    out = []
    with open(path, errors="replace") as fh:
        for n, ln in enumerate(fh, 1):
            m = DEVICE.match(ln)
            if m:
                out.append((m.group(2), len(m.group(1)), n))
    return out


def units(devices):
    """The movable units: the shallowest indent, in declaration order.

    A unit is a top-level device together with everything declared inside it, so
    relocating a unit relocates its children with it. gauguin has exactly three
    nested devices, all one level deep.
    """
    if not devices:
        return []
    top = min(d for _, d, _ in devices)
    return [n for n, d, _ in devices if d == top]


def scan(lines):
    """([sanitized line], [bool has_code]) with comments blanked out.

    The node headers in this file are written both ways - `//` runs and `/* */`
    blocks - and both belong to the node below them, so the structural walk
    needs to know which lines are pure comment rather than which ones start with
    `//`. It also needs braces and quotes out of the way: the prose quotes ASL
    fragments, and a `}` inside a comment must not close a device. Both lists
    are returned: the sanitized lines (comment characters replaced by spaces,
    same length) for structure, and the mask for "does this line carry code or
    a string".
    """
    sanit, mask, in_block, in_str = [], [], False, False
    for ln in lines:
        out = list(ln)
        code = False
        i = 0
        while i < len(ln):
            c = ln[i]
            if in_str:
                if c == "\\":
                    out[i] = " "
                    if i + 1 < len(out):
                        out[i + 1] = " "
                    i += 2
                    continue
                out[i] = " "
                if c == '"':
                    in_str = False
            elif in_block:
                out[i] = " "
                if c == "*" and ln[i:i + 2] == "*/":
                    out[i + 1] = " "
                    in_block = False
                    i += 2
                    continue
            elif c == '"':
                in_str = True
                code = True
            elif c == "/" and ln[i:i + 2] == "//":
                for k in range(i, len(out)):
                    out[k] = " "
                break
            elif c == "/" and ln[i:i + 2] == "/*":
                out[i] = out[i + 1] = " "
                in_block = True
                i += 2
                continue
            elif not c.isspace():
                code = True
            i += 1
        sanit.append("".join(out))
        mask.append(code)
    return sanit, mask


def split_units(path):
    """(prefix, [(unit, block_text)], suffix) for the file's top-level devices.

    A unit is a top-level `Device (X)` together with the comment and blank lines
    immediately above it - those comments are the node's argument and they
    travel with it, which is how the corpus's own tables read. Nested devices
    are not units: they live inside their parent's text and move with it.

    A unit's block ends where the next unit's does - so text that belongs to
    `\\_SB` rather than to any device (this file has a run of `Name (DPP0 ..)`
    buffers between `UFS0` and `UCS0`) travels with the device it follows,
    which keeps every byte of the file in exactly one block. The last unit ends
    at its own closing brace, leaving the `Scope` and `DefinitionBlock` closers
    as the suffix. The pieces are contiguous slices, so re-joining them in the
    file's own order reproduces it byte for byte, and `apply_order` checks
    exactly that before writing anything.
    """
    with open(path, errors="replace") as fh:
        text = fh.read()
    lines = text.split("\n")
    sanit, mask = scan(lines)
    heads = []
    for i, ln in enumerate(sanit):
        m = DEVICE.match(ln)
        if m:
            heads.append((i, m.group(2), len(m.group(1))))
    if not heads:
        return None, [], []
    top = min(d for _, _, d in heads)
    tops = [(i, n) for i, n, d in heads if d == top]

    def close(i):
        depth, seen = 0, False
        for j in range(i, len(sanit)):
            for ch in sanit[j]:
                if ch == "{":
                    depth += 1
                    seen = True
                elif ch == "}":
                    depth -= 1
            if seen and depth == 0:
                return j
        raise ValueError(f"unterminated Device at line {i + 1}")

    starts = []
    for k, (i, n) in enumerate(tops):
        s = i
        while s - 1 >= 0 and mask[s - 1] is False:
            s -= 1
        starts.append(s)
    spans = []
    for k, (i, n) in enumerate(tops):
        e = close(i)
        if k + 1 < len(tops):
            e = max(e, starts[k + 1] - 1)  # the block runs up to the next unit
        spans.append((n, starts[k], e))
    blocks = [(n, "\n".join(lines[s:e + 1])) for n, s, e in spans]
    prefix = lines[:spans[0][1]]
    suffix = lines[spans[-1][2] + 1:]
    return prefix, blocks, suffix


def apply_order(path, order, out_path):
    """Write `path` with its top-level units in `order`. Returns the new order.

    Text conservation is the whole proof that this is a reordering and not an
    edit: the round trip in the file's own order must reproduce the input byte
    for byte before the permutation is written.
    """
    prefix, blocks, suffix = split_units(path)
    if not blocks:
        raise SystemExit(f"no `Device (..)` declarations in {path}")
    have = [n for n, _ in blocks]
    if sorted(have) != sorted(order):
        raise SystemExit(f"order is not a permutation of {have}")
    with open(path, errors="replace") as fh:
        original = fh.read()
    identity = "\n".join(prefix + [b for _, b in blocks] + suffix)
    if identity != original:
        raise SystemExit("block extraction does not reproduce the input; refusing")
    text = dict(blocks)
    body = "\n".join(prefix + [text[n] for n in order] + suffix)
    if len(body) != len(original):
        raise SystemExit(f"length changed: {len(original)} -> {len(body)}")
    with open(out_path, "w") as fh:
        fh.write(body)
    return [n for n, _ in split_units(out_path)[1]]


def corpus_nodes(census, asl_nodes, drop=None):
    """Tables that declare at least two of gauguin's nodes, as name sequences.

    Keyed by the *disassembled* file's name, not the AML's: `disassemble()`
    flattens the path into the cache name (`Platforms-Xiaomi-lisa-DSDT.dsl`),
    and every AML in the tree is called `DSDT.aml` or `SSDT.aml` or one of the
    board variants, so keying by the AML collapses sixty-odd tables into five
    and silently merges their orders. It was measured that way once - the merged
    set reports 465 pairs over 41 tables' worth of nodes with the CPU block
    appearing to break twenty-one relations each - and the merge is what that
    artifact is.

    `drop` is the platform directory name of the file being scored, and a corpus
    table whose flattened path carries it is left out. The corpus is the
    Mu-Silicium checkout's own `Silicium-ACPI` submodule and the sync script
    installs *this* table into it, so without `drop` the file under test is one
    of the tables voting on its own order - and the vote it casts is whichever
    revision of itself that checkout happens to carry. Measured while writing
    Step 4.88: the corpus's cached copy had been built from a 1,520-byte AML,
    4,830 bytes behind the file it was a copy of, because
    `acpi-hid-census.disassemble` keyed its cache on the path and the one table
    in this tree that ever changes is ours. Both halves are fixed - the cache
    now rebuilds a disassembly older than its `.aml`, and this parameter keeps
    the copy out of the count - but the exclusion is by *name*, which is weaker
    than the content identity the rest of this repository insists on, so the
    tool prints both framings (`--keep-self`) instead of one. On 4.88 they agree
    on the verdict - broken 0 and score equal to the ceiling, 9,768/9,768 with
    the copy out and 10,368/10,368 with it in - and differ by the 37 pairs and
    600 votes the copy contributes.

    Those 37 pairs are the pairs **no other table in the corpus declares**, and
    they are worth knowing separately from the framing question: 34 of them are
    `UAR2`'s and three are `I2C8`'s against `I2C9`, `IC11` and `IPCC`. With the
    copy dropped, `UAR2` is left with no pair at all - which is why
    `--fixed-point` finds it tied with every slot - while the other engines keep
    30 to 33. The stale 1,520-byte copy was rebuilt and scored to see what the
    fault cost (`broken 0`, so no verdict changed): its ten nodes declare no QUP
    engine, so it contributes none of these 37 pairs and only adds votes to pairs
    that already existed, moving the score and the ceiling together.
    """
    tables = {}
    for aml in census.table_files(census.DEFAULT_TREE):
        dsl = census.disassemble(aml, census.DEFAULT_CACHE)
        if not dsl:
            print(f"  !! iasl could not read {aml}", file=sys.stderr)
            continue
        if drop and f"-{drop}-" in os.path.basename(dsl):
            print(f"  -- the corpus carries {os.path.basename(dsl)}, "
                  f"which is this table; not counted as a voter", file=sys.stderr)
            continue
        seq = []
        for n, _, _ in read_devices(dsl):
            if n in asl_nodes and n not in seq:
                seq.append(n)
        if len(seq) >= 2:
            tables[os.path.basename(dsl)] = seq
    return tables


def pairs_of(tables):
    """{(a, b) canonical, with a < b in the sort} -> Counter(ab=n, ba=m)."""
    votes = collections.defaultdict(lambda: collections.Counter())
    for seq in tables.values():
        for a, b in itertools.combinations(seq, 2):
            key = tuple(sorted((a, b)))
            votes[key]["ab" if (a, b) == key else "ba"] += 1
    return votes


def score(order, votes):
    """Table-votes satisfied: for each pair, the majority side's count if agreed."""
    pos = {n: i for i, n in enumerate(order)}
    total = 0
    for key, c in votes.items():
        a, b = key
        ab, ba = c["ab"], c["ba"]
        if ab == ba:
            continue
        majority = ab if ab > ba else ba
        agree = (pos[a] < pos[b]) if ab > ba else (pos[b] < pos[a])
        total += majority if agree else min(ab, ba)
    return total


def ceiling(votes):
    """The pairwise upper bound: no order can exceed this, and one attains it."""
    return sum(max(c["ab"], c["ba"]) for c in votes.values() if c["ab"] != c["ba"])


def relations(votes, pos):
    """(unanimous, broken, broken_votes, by_misplaced_node).

    The framing Step 4.84 used: a pair with a dissenting table is not a relation
    at all, and a relation the order reverses is broken. A pair is stored under
    its alphabetical key, so the corpus's *direction* has to be read out of the
    counts before comparing - the unanimous side is the side with the votes, and
    a pair is broken when the order puts the corpus's second node first.
    `by_misplaced_node` attributes each broken relation to the node the corpus
    puts *first*, since that is the node the file has written too late.
    """
    unan = {k: c for k, c in votes.items() if min(c["ab"], c["ba"]) == 0}
    broken = []
    votes_lost = 0
    per = collections.Counter()
    for (a, b), c in unan.items():
        first, second = (a, b) if c["ab"] else (b, a)
        if pos[first] > pos[second]:
            w = c["ab"] + c["ba"]
            broken.append((first, second))
            votes_lost += w
            per[first] += 1
    return unan, broken, votes_lost, per


def lcs_keep(a, b):
    """The longest subsequence of `a` that also appears in `b`, in order."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(m):
            dp[i + 1][j + 1] = (dp[i][j] + 1 if a[i] == b[j]
                                else max(dp[i][j + 1], dp[i + 1][j]))
    keep, i, j = [], n, m
    while i and j:
        if a[i - 1] == b[j - 1]:
            keep.append(a[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return keep[::-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--asl", default=os.path.join(ROOT, "tools/acpi/gauguin.asl"),
                    help="the table to score (default tools/acpi/gauguin.asl)")
    ap.add_argument("--order", metavar="NAMES",
                    help="score this whitespace-separated order instead of the file's")
    ap.add_argument("--subset", choices=sorted(SUBSETS), default=None,
                    help="restrict to the tables carrying this node")
    ap.add_argument("--all-subsets", action="store_true",
                    help="score every subset in turn")
    ap.add_argument("--moves", action="store_true",
                    help="print the relocations from the file's order to --order")
    ap.add_argument("--apply", metavar="OUT",
                    help="write the --order reordering of --asl to OUT")
    ap.add_argument("--fixed-point", action="store_true",
                    help="per node: is its slot one of the score-maximising ones?")
    ap.add_argument("--prove", action="store_true",
                    help="print the ceiling, the unavoidable dissent and the dissenters")
    ap.add_argument("--quiet", action="store_true", help="scores only")
    ap.add_argument("--keep-self", action="store_true",
                    help="count the corpus's copy of this table as a voter, "
                         "instead of leaving it out")
    args = ap.parse_args()

    census = load_census()
    file_units = units(read_devices(args.asl))
    if not file_units:
        print(f"no `Device (..)` declarations in {args.asl}", file=sys.stderr)
        return 1
    asl_nodes = set(file_units)
    # The platform directory the corpus files this table under is its own stem:
    # `tools/acpi/gauguin.asl` is `Platforms/Xiaomi/gauguin/DSDT.aml` there.
    drop = None if args.keep_self else os.path.splitext(os.path.basename(args.asl))[0]
    tables = corpus_nodes(census, asl_nodes, drop)
    if not tables:
        print("no corpus table declares two of this table's nodes", file=sys.stderr)
        return 1

    target = args.order.split() if args.order else None
    if target:
        unknown = [n for n in target if n not in asl_nodes]
        if unknown:
            print(f"--order names nodes this table does not declare: {unknown}",
                  file=sys.stderr)
            return 1
        if sorted(target) != sorted(file_units):
            print("--order must be a permutation of this table's top-level devices",
                  file=sys.stderr)
            print(f"  missing: {sorted(set(file_units) - set(target))}", file=sys.stderr)
            print(f"  extra:   {sorted(set(target) - set(file_units))}", file=sys.stderr)
            return 1

    want = [args.subset] if args.subset else (sorted(SUBSETS) if args.all_subsets else [None])
    for name in want:
        need = SUBSETS[name] if name else None
        sub = {k: v for k, v in tables.items() if need is None or need in v}
        votes = pairs_of(sub)
        base = score(file_units, votes)
        cap = ceiling(votes)
        unan, broken, lost, per = relations(votes, {n: i for i, n in enumerate(file_units)})
        label = name or "all"
        line = (f"{label:6s} tables={len(sub):3d} pairs={len(votes):4d} "
                f"votes={ceiling(votes) + sum(min(c['ab'], c['ba']) for c in votes.values()):5d} "
                f"relation-relations={len(unan):4d} broken={len(broken):3d}/{lost:5d} "
                f"score={base:5d} ceiling={cap:5d}")
        print(line)
        if not args.quiet and broken:
            print("        broken, by the node the corpus puts first:")
            for node, k in per.most_common():
                print(f"          {node:6s} {k:3d} relations")
        if target:
            tscore = score(target, votes)
            print(f"        target score {tscore}  (ceiling {cap}, "
                  f"{'optimal' if tscore == cap else f'{cap - tscore} short'})")
        if args.prove:
            unavoidable = sum(min(c["ab"], c["ba"]) for c in votes.values())
            print(f"        unavoidable dissent {unavoidable} votes "
                  f"in {sum(1 for c in votes.values() if min(c['ab'], c['ba']))} pairs")
            for (a, b), c in sorted(votes.items()):
                lo = min(c["ab"], c["ba"])
                if not lo:
                    continue
                maj = "ab" if c["ab"] > c["ba"] else "ba"
                who = sorted(t for t, v in sub.items()
                             if a in v and b in v
                             and (("ab" if v.index(a) < v.index(b) else "ba") != maj))
                print(f"          {a} vs {b}: {c['ab']} to {c['ba']}, "
                      f"dissenting alone: {', '.join(who)}")

    if target and args.moves:
        keep = lcs_keep(file_units, target)
        moved = [n for n in target if n not in keep]
        print()
        print(f"relocations: {len(file_units) - len(keep)} of {len(file_units)} units; "
              f"kept {len(keep)}")
        for n in moved:
            print(f"  {n:6s} from index {file_units.index(n):2d} to {target.index(n):2d}")

    if args.apply:
        if not target:
            print("--apply needs an --order", file=sys.stderr)
            return 1
        written = apply_order(args.asl, target, args.apply)
        print()
        print(f"wrote {args.apply}: {len(written)} units in the target order")

    if args.fixed_point:
        need = SUBSETS[args.subset] if args.subset else None
        sub = {k: v for k, v in tables.items() if need is None or need in v}
        votes = pairs_of(sub)
        order = target or file_units
        base = score(order, votes)
        print()
        print(f"fixed point of {len(order)} nodes, order score {base}:")
        free = []
        for node in order:
            i = order.index(node)
            rest = order[:i] + order[i + 1:]
            res = [(score(rest[:j] + [node] + rest[j:], votes), j)
                   for j in range(len(rest) + 1)]
            best = max(s for s, _ in res)
            argmax = [j for s, j in res if s == best]
            if i not in argmax:
                print(f"  MISPLACED {node:6s} at {i:2d}: best {best}, "
                      f"wants {argmax}")
            elif len(argmax) > 1:
                free.append((node, i, argmax))
        for node, i, argmax in free:
            print(f"  free      {node:6s} at {i:2d}: ties with {argmax}")
        if not free:
            print("  every node's slot is unique (no ties)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
