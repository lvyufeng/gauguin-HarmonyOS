#!/usr/bin/env python3
"""Name the drivers behind a `P2 SEQ` / `P2 WHY` letter string, with no GUID typed.

`P2 SEQ` and `P2 WHY` are the two most informative rows the bring-up build prints
and the two a person is least likely to bring back intact. Each is one unbroken
string with **one character per promoted Apriori entry**, in dispatch order
(`Dispatcher.c:2254-2261`):

    P2 SEQ [ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL]
    P2 WHY [ssssssssssssssssssRRRsRRRRRRRRRRRRRRRRRRRRRRRR]
             ^                                                       ^
             the first entry promoted                 the last one promoted

`SEQ` is the phase (`s` started, `L` CoreLoadImage failed, `S` the entry point
returned an error, `?` never reached) and `WHY` is P2WhyLetter's class at the same
position. The digest's own comment records the standing situation: *"SEQ has been
read off this panel three times and WHY has never been read once."* - and the
reason is that neither line means anything without knowing which driver each
position is, which until now required transcribing a 36-character GUID off a
neighbouring `K` row.

This tool removes that step. It prints, for every position, the Apriori file
index, the GUID and the driver's own name, so a letter string read off the panel
is decoded here rather than by eye.

**The position mapping, and why it is trustworthy.** Promoted position `i` is
Apriori file index `i + 1`: the array's entry 0 is `DxeCore`, which is a
`DXE_CORE` file and is never added to the discovered driver list, and every other
entry in this array names a `DRIVER` file the volume contains. So position is file
order minus the non-matches (`Dispatcher.c:2050-2060` fills `mP2AprioriGuid[i]` in
file order with `mP2Apriori++` inside the match branch), and on this volume the
only non-match is entry 0.

That is also what `docs/08`'s decode table means when it writes `ap1..ap46
matched`, and what `fv-census.py` means by its 1-based "Apriori N": **position `i`
is Apriori `i + 1`.** This tool asserts two of those as a live self-test, both
quoted from measurements that did not come from the string being decoded:

  * *"**74** (ShmBridgeDxe, Apriori 22) succeeded"* - so position 21 is
    `ShmBridgeDxe`, and `P2 SEQ` independently shows `s` at slot 21 and `L` either
    side, the run's only `s` past the first eighteen.
  * *"**5** (SecurityStubDxe, Apriori 37) failed"* - so position 36 is
    `SecurityStubDxe`.

If a later rebuild moves the Apriori file, those assertions fail loudly, which is
the point: a stale mapping mislabels every letter, and a mislabelled reading looks
exactly like a right one.

**`--skip J`** is for the documented case where the mapping shifts. `docs/08`'s
decode table has a row for `miss=j`: the absent entries are interleaved rather than
a suffix, `miss` names the first one, and the name tables are shifted from
`SEQ[j-1]` on. Passing the file index that matched nothing reproduces that shift,
so the labels stay right under either reading instead of silently assuming the
suffix.

Note what `--skip` does **not** decide. The two open readings of this run's
`P2 APRI` - the array read whole (`entries=70`, then `miss=47`) and the array read
368 bytes short (`entries=47`, `unhit=1`) - are *indistinguishable on the letter
strings*: both promote `ap1..ap46`, so both print a 46-character `SEQ`. The
distinguishing row is `P2 APRI` itself, whose `sum=` names its own length
(`b4ba9d75` is 47 entries, `a998b263` is all 70), and `KEY`'s `miss=`/`unhit=`.

`--why` decodes a letter string; `--seq` is the same thing with the phase meanings
spelled out, and the two can be given together to read one line against the other.
The tool prints the run of non-`s` characters as its own summary, because that run
is what the phase turns on: it names the first position that failed, which is what
`KEY`'s `at=` should say, and it counts the failure classes, which is what `KEY`'s
`err=` gives in words for the first one only.

**`--sizes` is a convenience, and `tools/pe-facts.py` is the tool for that
question.** The boundary question - does any size separate the loads from the
refusals - was answered there first, field by field over 80 drivers, and its
verdict is *"no field separates the 19 s from the 27 L by a single value or a
threshold"*; it also reports the cumulative demand in the pages the allocator
actually hands out, which this tool does not. What is here is the same walk and
the same column (`SizeOfImage`, the field `CoreLoadPeImage` acts on) plus the one
number `pe-facts.py` leaves qualitative: how wrong the best candidate cut is. If
the question is *why* a particular driver was refused rather than *whether* size
explains the run, neither tool is the one - `P2 WHY` is.

Usage:
    tools/apriori-index.py IMG                                   # the whole table
    tools/apriori-index.py IMG --at 21                           # one position
    tools/apriori-index.py IMG --seq ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL
    tools/apriori-index.py IMG --why ssssssssssssssssssRRRsRRRRRRRRRRRRRRRRRRRRRRRR
    tools/apriori-index.py IMG --skip 47 --why STRING            # interleaved reading
    tools/apriori-index.py IMG --sizes --seq STRING              # was it about room?
"""

import argparse
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISPATCHER = os.path.join(
    ROOT, "work", "uefi", "Mu-Silicium", "Mu_Basecore",
    "MdeModulePkg", "Core", "Dxe", "Dispatcher", "Dispatcher.c")

# The anchors the docstring cites, asserted against every run. They are not
# configuration: if the firmware stops satisfying them, the mapping this tool
# labels letters with is no longer the mapping the panel uses, and it has to say
# so rather than decode 46 letters against the wrong drivers. Both are quoted
# from `fv-census.py`'s measured sentence about physical file 5 and physical file
# 74, which is a reading of the volume and not of the letter string.
ANCHORS = [(21, "ShmBridgeDxe"), (36, "SecurityStubDxe")]

# The letter meanings, resolved out of P2WhyLetter() at run time. Hand-typing this
# table is the mistake `tools/probe-fingerprint.py` already paid for once (it typed
# `err=%a` where the source says `err=%r` and then declared an instrument absent
# from an image that had it); a decoder with a stale table mislabels a reading,
# which is the same failure with a worse consequence.
WHY_PATTERNS = [
    (r"if \(Status == EFI_SUCCESS\)\s*\{\s*return '(\w)';",
     "EFI_SUCCESS", "ran and returned success"),
    (r"if \(Status == EFI_OUT_OF_RESOURCES\)\s*\{\s*return '(\w)';",
     "EFI_OUT_OF_RESOURCES", "the allocator refused - room, not permission and not format"),
    (r"if \(Status == EFI_NOT_FOUND\)\s*\{\s*return '(\w)';",
     "EFI_NOT_FOUND", "a section or a dependency the load needed was absent"),
    (r"if \(Status == EFI_SECURITY_VIOLATION\)\s*\{\s*return '(\w)';",
     "EFI_SECURITY_VIOLATION", "the security protocol refused it"),
    (r"if \(Status == EFI_DEVICE_ERROR\)\s*\{\s*return '(\w)';",
     "EFI_DEVICE_ERROR", "the section stream reported an I/O error"),
    (r"if \(Status == EFI_LOAD_ERROR\)\s*\{\s*return '(\w)';",
     "EFI_LOAD_ERROR", "the image itself would not load"),
    (r"if \(Status == EFI_INVALID_PARAMETER\)\s*\{\s*return '(\w)';",
     "EFI_INVALID_PARAMETER", "malformed input to the loader"),
    (r"if \(Status == EFI_UNSUPPORTED\)\s*\{\s*return '(\w)';",
     "EFI_UNSUPPORTED", "a machine type or a compression this platform cannot run"),
]

# The letters that are not statuses. `?` is what the promotion loop stamps into
# mP2AprioriRes before anything runs (`Dispatcher.c:2057`), and `L`/`S` are the
# phase letters P2Record and P2MarkSeq stamp from the two call sites (`:1051`,
# `:1096`, `:1098`).
EXTRA_LETTERS = {
    "?": ("never reached", "the batch stopped before this position"),
    "L": ("CoreLoadImage failed", "phase letter - this driver's code never ran"),
    "S": ("EntryPoint failed", "phase letter - the code ran and returned an error"),
}

# `P2 SEQ` has an alphabet of four and `P2 WHY` has ten, and they are different
# questions - so they get different tables rather than one shared one. SEQ's `s`
# and WHY's `s` do happen to mean the same thing (both are stamped from
# EFI_SUCCESS: `P2MarkSeq(..., 's', EFI_SUCCESS)` at `Dispatcher.c:1098`, and
# P2WhyLetter(EFI_SUCCESS) is `'s'` at `:501`), and SEQ's `S` likewise coincides
# with WHY's `S`. Relying on that coincidence is how a decoder starts mislabelling
# a reading it otherwise got right, so it is spelled out here instead.
SEQ_LETTERS = {
    "?": ("promoted, nothing recorded", "the drain never got there, or got there and left no trace"),
    "s": ("EntryPoint returned EFI_SUCCESS", "CoreStartImage succeeded - driver code ran"),
    "S": ("EntryPoint returned an error", "CoreStartImage failed - driver code ran and failed"),
    "L": ("CoreLoadImage failed", "the EntryPoint was never called; the status is in P2 WHY here"),
}


def load_sibling(name, filename):
    path = os.path.join(ROOT, "tools", filename)
    if not os.path.isfile(path):
        sys.exit(f"apriori-index: {filename} is not beside this tool")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def why_table():
    """{'s': (name, meaning), ...} parsed from P2WhyLetter(), plus the two phases."""
    try:
        text = open(DISPATCHER, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        sys.exit(f"apriori-index: cannot read the Dispatcher: {exc}")
    m = re.search(r"\nP2WhyLetter \(\s*\n.*?\n\}\n", text, re.S)
    if not m:
        sys.exit("apriori-index: P2WhyLetter() is no longer in the Dispatcher -"
                 " the letter table this decodes with has moved, and a stale table"
                 " mislabels a reading")
    body = m.group(0)
    table = {}
    for pat, name, meaning in WHY_PATTERNS:
        hit = re.search(pat, body)
        if not hit:
            sys.exit(f"apriori-index: P2WhyLetter() no longer returns a letter for"
                     f" {name}; the decoder's table is stale")
        table[hit.group(1)] = (name, meaning)
    table["O"] = ("another status", "not one of the eight above")
    table.update(EXTRA_LETTERS)
    return table


def build(image):
    """([entry dict], file count, ctx) - one entry per Apriori array entry.

    Imported rather than reimplemented: `apriori-order.py` owns reading the
    Apriori array out of the volume, and `fv-inventory.py` owns the walk that gets
    inside the LZMA section. Both have documented histories of being wrong in ways
    that still produced plausible output, so there is exactly one copy of each.
    """
    fvi = load_sibling("fvi", "fv-inventory.py")
    ao = load_sibling("ao", "apriori-order.py")
    guids, nfiles = ao.apriori_array(fvi, image)
    files, _len, offsets, inner = fvi.unpack(image)
    rows = fvi.roster(files, offsets, inner)

    by_guid = {}
    for (g, t, s, nm), o in zip(rows, offsets):
        by_guid.setdefault(g.upper(), (t, nm or "", s, o))
    out = []
    for i, g in enumerate(guids):
        t, nm, size, o = by_guid.get(g.upper(), (None, "", 0, None))
        out.append({"file": i, "guid": g, "type": t, "name": nm, "size": size,
                    "off": o})
    return out, nfiles, {"inner": inner, "fvi": fvi}


def section_stream(raw):
    """The section stream of an FFS file - after its header, which is 32 bytes
    when the large-file prefix is present and 24 otherwise."""
    if raw[:3] == b"\xff\xff\xff":
        return raw[32:]
    return raw[24:]


def pe_image(blob, fvi, depth=0):
    """The `EFI_SECTION_PE32` body inside an FFS file's section stream, or None.

    A driver's PE image can sit behind one wrapped section (compression,
    `EFI_SECTION_COMPRESSION`) or a GUIDed one, so this descends rather than
    taking the first section. Bounded at depth 3 because a section stream that
    nests deeper than that on this platform is not a thing that exists, and an
    unbounded walk on malformed input is a hang rather than an error.
    """
    if depth > 3 or not blob:
        return None
    for st, body in fvi.sections(blob):
        if st == 0x10:                                    # EFI_SECTION_PE32
            return body
        if st == 0x01:                                    # EFI_SECTION_COMPRESSION
            # 4B uncompressed length, 1B compression type, then the stream.
            for st2, b2 in fvi.sections(body[5:]):
                if st2 == 0x10:
                    return b2
            got = pe_image(body[5:], fvi, depth + 1)
            if got:
                return got
        if st == 0x02:                                    # EFI_SECTION_GUID_DEFINED
            dec = fvi.decompress_guided(body)
            if dec:
                got = pe_image(dec, fvi, depth + 1)
                if got:
                    return got
    return None


def pe_facts(entry, ctx):
    """(ffs size, PE32 bytes, SizeOfImage, machine) for one Apriori entry.

    `SizeOfImage` and not the FFS file size, because the file size is padded to
    4 bytes and carries a UI section holding the driver's printable name, while
    `SizeOfImage` is what `CoreLoadPeImage` actually allocates pages for. The
    distinction is the whole point of the measurement in docs/08 step 4.37: the
    two disagree by a factor that varies per driver (a 9,216-byte PE can sit in a
    20,534-byte file), so a claim about room made from file sizes is not a claim
    about room.
    """
    if entry["off"] is None:
        return None
    fvi, inner = ctx["fvi"], ctx["inner"]
    raw = inner[entry["off"]:entry["off"] + entry["size"]]
    pe = pe_image(section_stream(raw), fvi)
    if not pe or pe[:2] != b"MZ":
        return (entry["size"], None, None, None)
    off = int.from_bytes(pe[0x3c:0x40], "little")
    if off + 0x3c > len(pe) or pe[off:off + 4] != b"PE\0\0":
        return (entry["size"], len(pe), None, None)
    machine = int.from_bytes(pe[off + 4:off + 6], "little")
    soi = int.from_bytes(pe[off + 24 + 56:off + 24 + 60], "little")
    return (entry["size"], len(pe), soi, machine)


def positions(entries, skips):
    """file index -> promoted position, with `skips` naming entries that matched nothing.

    A dict and not a list, because the two readings in `docs/08`'s decode table
    disagree about how many positions exist and the honest output is the mapping
    under the assumption the reader named, not one number pretending to be it.
    """
    pos, n = {}, 0
    for e in entries:
        if e["file"] in skips:
            continue
        pos[e["file"]] = n
        n += 1
    return pos


def entry_at(entries, pos, i):
    """promoted position -> entry: the inverse of `positions`, and None if unmapped.

    Named separately because a position is not a file index and the two are easy to
    confuse once a `skips` set is in play - `positions` skips entries, so position i
    is `entries[i]` only when nothing was skipped before it.
    """
    for e in entries:
        if pos.get(e["file"]) == i:
            return e
    return None


def anchors_hold(entries, pos):
    """[] or a list of the anchors this image disagrees with."""
    bad = []
    for p, name in ANCHORS:
        e = next((x for x in entries if pos.get(x["file"]) == p), None)
        if e is None or e["name"] != name:
            bad.append((p, name, e["name"] if e else "nothing"))
    return bad


def show(entries, pos, at=None, only_file=None):
    if at is not None or only_file is not None:
        e = None
        for x in entries:
            if only_file is not None and x["file"] == only_file:
                e = x
            elif at is not None and pos.get(x["file"]) == at:
                e = x
        if e is None:
            print(f"nothing at {'file index' if only_file is not None else 'position'}"
                  f" {only_file if only_file is not None else at}")
            return 1
        p = pos.get(e["file"])
        print(f"  Apriori file index {e['file']}, promoted position "
              f"{p if p is not None else '(matched nothing)'}")
        print(f"  {e['guid']}  {e['name'] or '(no UI name)'}")
        print(f"  {'a DRIVER file in this volume' if e['type'] == 0x07 else 'file type ' + hex(e['type'] or 0)}"
              f", {e['size']:,} bytes")
        return 0

    print(f"  file  pos       size  guid                                  name")
    for e in entries:
        p = pos.get(e["file"])
        note = "" if p is not None else "  <- matched nothing"
        if e["type"] is None:
            note = "  <- no such file in this volume"
        elif e["type"] != 0x07:
            note = f"  <- type {hex(e['type'])}, not a DRIVER"
        print(f"  {e['file']:4d}  {p if p is not None else '-':>3}  "
              f"{e['size']:>9,}  {e['guid']}  {e['name'] or '(no UI name)'}{note}")
    return 0


def decode(letters, entries, pos, table, kind):
    s = letters.strip().strip("[]").replace(" ", "")
    letters_table = SEQ_LETTERS if kind == "SEQ" else table
    print(f"\n{kind} [{s}]  {len(s)} letters")
    usable = [e for e in entries if pos.get(e["file"]) is not None]
    if len(s) != len(usable):
        print(f"  this image offers {len(usable)} promoted positions and the string"
              f" has {len(s)} - decoding the {min(len(s), len(usable))} that line up")
        print(f"  (a length disagreement is itself a reading: `docs/08`'s decode table"
              f" has a row for each way the array can be short)")
    bad = 0
    for i, ch in enumerate(s):
        e = next((x for x in entries if pos.get(x["file"]) == i), None)
        label, meaning = letters_table.get(ch, (f"unknown letter {ch!r}", ""))
        if e is None:
            print(f"  {i:3d}  {ch}  {label}  (past the end of the array)")
            if ch not in letters_table:
                bad = 1
            continue
        print(f"  ap{e['file']:<3d} pos {i:2d}  {ch}  {e['name'] or '(no UI name)':28s}"
              f" {label}")
        if ch not in letters_table:
            bad = 1

    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    print(f"\n  classes:")
    for ch, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        label, meaning = letters_table.get(ch, (f"unknown letter {ch!r}", ""))
        print(f"    {n:3d}  {ch}  {label:32s} {meaning}")

    nons = [(i, ch) for i, ch in enumerate(s) if ch != "s"]
    if nons:
        i0 = nons[0][0]
        e0 = next((x for x in entries if pos.get(x["file"]) == i0), None)
        print(f"\n  first position that is not 's': {i0}  "
              f"(ap{e0['file'] if e0 else '?'}, {e0['name'] if e0 else '?'})")
        if kind == "WHY":
            print(f"  -> KEY's `at=` should be {i0} and `err=` should read"
                  f" {table.get(nons[0][1], ('?',))[0]} if this string and that"
                  f" line are from the same run")
        else:
            print(f"  -> the phase letter alone does not give a status; the WHY"
                  f" string at the same positions does")

    # The two letters that are decisions rather than descriptions, and the reason
    # this paragraph exists: `?` anywhere means the drain did not reach that
    # entry, and `S` anywhere means a driver's code ran and returned an error.
    # Both are single facts the rest of the phase turns on, and both are readable
    # off one line without knowing any driver name - so they are stated rather
    # than left for the eye to count.
    if kind == "SEQ":
        nq, ns = s.count("?"), s.count("S")
        print(f"\n  shape:")
        if nq == 0:
            print(f"    no '?'  -> the drain reached every promoted entry; the batch"
                  f" ran to the end of the array")
        else:
            print(f"    {nq} '?' -> the drain stopped before {s.index('?')}; everything"
                  f" from there on was promoted and never attempted")
        if ns == 0:
            print(f"    no 'S'  -> every driver that loaded also started; all"
                  f" {s.count('L')} failures are at CoreLoadImage, so no driver code"
                  f" in this array ran and failed")
        else:
            print(f"    {ns} 'S' -> {ns} driver(s) ran and returned an error; those"
                  f" are the rows P2 ERR spells in words")
        if s.count("L") and s.count("s"):
            # The run structure is what a reader would otherwise count by eye, and
            # miscount by one. A single contiguous failure block says the batch
            # broke once and never recovered; two blocks with a survivor between
            # them says something got through and is the more interesting shape.
            runs, cur = [], None
            for i, ch in enumerate(s):
                if cur is None or ch != cur[0]:
                    cur = [ch, i, i]
                    runs.append(cur)
                else:
                    cur[2] = i
            print("    runs: " + ", ".join(
                f"{ch}x{end - start + 1} (pos {start}-{end})"
                for ch, start, end in runs))

        # The one hypothesis the shape above suggests and the host can test before
        # anyone photographs the panel: that the batch broke once and never
        # recovered because a pooled resource ran out, in which case the failures
        # should be the *big* ones and the survivor in the middle should be small.
        # Confirmed, this points at the allocator; refuted, the cause is per-driver
        # and P2 WHY's letters will differ across the block.
        #
        # Caveat that keeps this from being over-read: `size` is the FFS file's
        # size including its header, not what CoreLoadImage allocates, which is the
        # sum of the sections it actually reads. The two track each other closely
        # for these drivers but they are not the same number, so this is a
        # discriminator and not a measurement of the heap.
        sized = [(ch, e) for i, ch in enumerate(s)
                 for e in [entry_at(entries, pos, i)] if e is not None]
        for want, name in (("s", "loaded and started"), ("L", "CoreLoadImage failed")):
            grp = sorted(e["size"] for ch, e in sized if ch == want)
            if not grp:
                continue
            print(f"    {name}: {len(grp)} files, {grp[0]:,} .. {grp[-1]:,} bytes"
                  f", median {grp[len(grp) // 2]:,}")
        biggest_ok = max((e["size"] for ch, e in sized if ch == "s"), default=0)
        small = [e["name"] for ch, e in sized
                 if ch == "L" and 0 < e["size"] < biggest_ok]
        if small:
            print(f"    failing files smaller than the largest succeeding one:"
                  f" {', '.join(small)}")
    return bad


def sizes_table(entries, pos, ctx, seq=None):
    """The measurement docs/08 step 4.37 turns on, re-derivable with one command.

    Printed as a table and then, when a `SEQ` string is given, split into the two
    groups the string names. The second half is the useful half: it is the test
    that retired "these 27 failed because they needed more room", and it retired
    it because `PdcDxe` and `ShmBridgeDxe` come out with the same `SizeOfImage` to
    the byte and opposite letters.
    """
    print(f"\n  file  pos  letter      ffs  pe32  sizeofimage  machine  name")
    rows = []
    for e in entries:
        ffs, pe32, soi, mach = pe_facts(e, ctx)
        rows.append((e, ffs, pe32, soi, mach))
    letters = {}
    if seq:
        s = seq.strip().strip("[]").replace(" ", "")
        letters = {p: ch for p, ch in enumerate(s)}

    for e, ffs, pe32, soi, mach in rows:
        p = pos.get(e["file"])
        ch = letters.get(p, " ") if p is not None else " "
        mach_s = f"{mach:#x}" if mach else "-"
        print(f"  {e['file']:4d}  {p if p is not None else '-':>3}  {ch:>6}  "
              f"{ffs:>9,}  {pe32 if pe32 else -1:>9,}  "
              f"{soi if soi else -1:>11,}  {mach_s:>7}  {e['name'] or '(no name)'}")

    if not letters:
        return 0

    print()
    # (soi, name, position), so every later step needs no second lookup.
    groups = {}
    for e, _ffs, _pe32, soi, _mach in rows:
        p = pos.get(e["file"])
        if p is None or p not in letters:
            continue
        groups.setdefault(letters[p], []).append((soi or 0, e["name"], p))
    for ch, lbl in (("s", "loaded and started"),
                    ("L", "CoreLoadImage failed"),
                    ("S", "EntryPoint returned an error"),
                    ("?", "promoted, never attempted")):
        grp = groups.get(ch)
        if not grp:
            continue
        sois = sorted(x[0] for x in grp if x[0])
        print(f"  {ch}: {len(grp)} entries - {lbl}")
        if sois:
            print(f"       SizeOfImage {sois[0]:,} .. {sois[-1]:,},"
                  f" median {sois[len(sois) // 2]:,}")

    if not (groups.get("s") and groups.get("L")):
        return 0

    # A single number, rather than a list of size coincidences: sweep every
    # threshold and count how many of the 46 entries a "too big to fit" rule would
    # get wrong. If some threshold gets them all right, the failures are about room
    # after all and the whole step 4.37 conclusion is wrong; if even the best
    # threshold is wrong about a third of them, no size rule explains this run.
    obs = [(soi, ch, name, p) for ch in ("s", "L") for soi, name, p in groups[ch]]
    best = None
    for t in sorted({soi for soi, _c, _n, _p in obs if soi}):
        wrong = [o for o in obs if (o[1] == "L" and o[0] < t)
                 or (o[1] == "s" and o[0] >= t)]
        if best is None or len(wrong) < len(best[1]):
            best = (t, wrong)
    t, wrong = best
    print(f"\n  best size rule: \"refuse anything at or above {t:,} bytes\"")
    print(f"  -> right about {len(obs) - len(wrong)}/{len(obs)}, wrong about"
          f" {len(wrong)}; no threshold does better")
    print(f"     misclassified: "
          + ", ".join(f"{n} ({c}, pos {p})" for _s, c, n, p in sorted(wrong)))

    # And the sharpest single instance, chosen rather than stumbled on: the failing
    # entry and the loading entry with equal SizeOfImage that are closest together
    # in dispatch order. Two drivers that asked for the same amount of room in the
    # same run, with opposite outcomes - which no account of this as an allocation
    # failing can produce.
    pairs = sorted((abs(p1 - p2), n1, p1, n2, p2, s1)
                   for s1, n1, p1 in groups["L"]
                   for s2, n2, p2 in groups["s"]
                   if s1 and s1 == s2)
    if pairs:
        dist, f_name, f_pos, o_name, o_pos, soi = pairs[0]
        print(f"  !! closest such pair: {f_name} (position {f_pos}, failed) and"
              f" {o_name} (position {o_pos}, loaded)\n     both ask for {soi:,}"
              f" bytes - {dist} dispatch slots apart")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image", help="the payload .img, or an FD (SILICIUM_UEFI.fd)")
    ap.add_argument("--at", type=int, metavar="N", help="promoted position N")
    ap.add_argument("--file-at", type=int, metavar="N", help="Apriori file index N")
    ap.add_argument("--skip", type=int, action="append", default=[], metavar="J",
                    help="Apriori file index J matched nothing (repeatable); "
                         "docs/08's `miss=j<47` row is `--skip J`")
    ap.add_argument("--why", metavar="STRING", help="a P2 WHY string off the panel")
    ap.add_argument("--seq", metavar="STRING", help="a P2 SEQ string off the panel")
    ap.add_argument("--sizes", action="store_true",
                    help="per-entry FFS size, PE32 size and PE SizeOfImage - the "
                         "measurement that decides whether a refusal was about room")
    args = ap.parse_args()

    entries, nfiles, ctx = build(args.image)
    # The suffix case is the default and is what this volume does: only file index
    # 0 (DxeCore) fails to match, and it is a DXE_CORE file rather than a DRIVER.
    # `--skip` is added to that set rather than replacing it. An earlier draft let
    # it replace, which meant `--skip 20` also un-skipped DxeCore and moved every
    # position by one - a decoder that is silently off by one is the exact failure
    # this tool exists to prevent, so the flag cannot be allowed to cause it.
    skips = set(args.skip) | {e["file"] for e in entries if e["type"] != 0x07}
    pos = positions(entries, skips)
    table = why_table()

    print(f"{os.path.basename(args.image)}: FVMAIN {nfiles} files, "
          f"Apriori file {len(entries)} entries ({len(entries) * 16} bytes)")

    bad = 0
    for p, want, got in anchors_hold(entries, pos):
        print(f"  !! ANCHOR BROKEN: position {p} should be {want}, this table says"
              f" {got}.")
        print(f"     The mapping this tool labels letters with is not the mapping"
              f" this firmware uses.")
        bad = 1
    if not bad:
        print(f"  anchors hold: position {ANCHORS[0][0]} is {ANCHORS[0][1]}, "
              f"position {ANCHORS[1][0]} is {ANCHORS[1][1]}"
              f"  (two independent readings agree with this table)")

    if args.sizes:
        bad |= sizes_table(entries, pos, ctx, args.seq or args.why)
        return bad

    if args.why or args.seq:
        if args.why:
            bad |= decode(args.why, entries, pos, table, "WHY")
        if args.seq:
            bad |= decode(args.seq, entries, pos, table, "SEQ")
        return bad

    return show(entries, pos, args.at, args.file_at)


if __name__ == "__main__":
    sys.exit(main())
