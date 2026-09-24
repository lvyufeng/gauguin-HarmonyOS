#!/usr/bin/env python3
"""Which returns can actually produce a load failure's status, taken from the source.

`P2 DIAG` prints `%c %g %r` - phase, file GUID, status name - and the name is the
whole of the reading: "Out of Resources", "Not Found" and "Security Violation"
have no mechanism in common, so the name picks which of them to go and look at.
That is the point of this tool: given a status name as it appears on the panel,
it lists every place in the load path that can return it, and says which of them
the platform can actually reach.

Two halves, and the split matters:

  * **The sites, extracted.** Every occurrence of the status token in the files
    that make up `CoreLoadImage` -> `CoreLoadPeImage` -> the allocator, with the
    enclosing function, the line, and whether it *produces* the status or merely
    tests or documents it. Mechanical, so it cannot go stale quietly: if the tree
    moves, the list moves - and the produce/test/doc split is what keeps the list
    from reading as if every line were a return site, which most of them are not.

  * **The ledger, anchored.** Each entry is a named mechanism with the source
    text that identifies it and a verdict - reachable, or dead because of
    something else in the same tree. The anchors are checked, and a check that
    fails prints for the way it failed rather than as a verdict: `anchor gone`
    (the mechanism is no longer spelled that way), `moved` (the text is there but
    in another function, so the verdict may be about the right words in the wrong
    code), or, for the one entry that claims a mechanism is *absent*, a report
    that the token has appeared after all. A verdict cannot outlive the code it
    was about, and the anchors are what makes that checkable without re-reading
    everything.

The name-to-number mapping is parsed out of `PrintLibInternal.c`'s status table
rather than assumed, because `%r` prints by index and the index is the thing the
ledger's verdicts are keyed on.

    tools/load-sites.py [--mu DIR] [--status NAME] [--all]

`--all` prints the whole status table, which is what to reach for when the panel
name is one this tool has no ledger for.

Exit status is 0 whatever it finds; this is a report, not a gate.
"""

import argparse
import io
import os
import re
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_MU = os.path.join(REPO, "work", "uefi", "Mu-Silicium")

PRINTLIB = "Mu_Basecore/MdePkg/Library/BasePrintLib/PrintLibInternal.c"
IMAGEC = "Mu_Basecore/MdeModulePkg/Core/Dxe/Image/Image.c"
PAGEC = "Mu_Basecore/MdeModulePkg/Core/Dxe/Mem/Page.c"
POOLC = "Mu_Basecore/MdeModulePkg/Core/Dxe/Mem/Pool.c"
MEMPROT = "Mu_Basecore/MdeModulePkg/Core/Dxe/Misc/MemoryProtection.c"
PECOFPROT = "Mu_Basecore/MdeModulePkg/Core/Dxe/Misc/MemoryProtectionSupport.c"
BASEPE = "Mu_Basecore/MdePkg/Library/BasePeCoffLib/BasePeCoff.c"
DXESVC = "Mu_Basecore/MdePkg/Library/DxeServicesLib/DxeServicesLib.c"

# The files a load failure's status can come out of. `CoreStartImage` is in here
# because a driver that loads and then fails to start is recorded as 'S' and
# leaves no trace in the SEQ, so its statuses share the same `P2 ERR` groups.
PATH_FILES = [IMAGEC, PAGEC, POOLC, MEMPROT, PECOFPROT, DXESVC, BASEPE]

# The statuses worth a ledger: the ones whose name does not say where it came
# from. "Security Violation" and "Load Error" name their own single mechanism.
LEDGER = [
    # ---- EFI_OUT_OF_RESOURCES -------------------------------------------------
    (
        "page",
        IMAGEC,
        "Status = CoreAllocatePages (\n                   AllocateAddress",
        "CoreLoadPeImage",
        "reachable - the page allocator refusing a typed request",
    ),
    (
        "pool",
        IMAGEC,
        "AllocateRuntimePool ((UINTN)(Image->ImageContext.FixupDataSize))",
        "CoreLoadPeImage",
        "reachable - runtime drivers only; a few hundred bytes to a few KiB",
    ),
    (
        "pool",
        IMAGEC,
        "OriginalFilePath = AppendDevicePath (DevicePathFromHandle (DeviceHandle), Node)",
        "CoreLoadImageCommon",
        "reachable - a device path copy, tens of bytes",
    ),
    (
        "pool",
        IMAGEC,
        "AllocateZeroPool (sizeof (LOADED_IMAGE_PRIVATE_DATA))",
        "CoreLoadImageCommon",
        "reachable - a few hundred bytes, and the first pool allocation of the load",
    ),
    (
        "pool",
        IMAGEC,
        "Image->JumpBuffer = AllocatePool (sizeof (BASE_LIBRARY_JUMP_BUFFER)",
        "CoreStartImage",
        "reachable and per-image, tens of bytes - the NULL test below it returns "
        "EFI_OUT_OF_RESOURCES directly from CoreStartImage, so this one is a "
        "*start* failure: it is recorded as 'S' and leaves no trace in the SEQ "
        "while sharing P2 ERR's groups with the load failures",
    ),
    (
        "pool",
        IMAGEC,
        "Image->ExitData     = AllocatePool (Image->ExitDataSize);",
        "CoreExit",
        "reachable but narrow - only when the image called Exit() with data",
    ),
    (
        "preset",
        IMAGEC,
        "    Status = EFI_OUT_OF_RESOURCES;",
        "CoreLoadPeImage",
        "dead - a preset overwritten on every branch that reaches the return",
    ),
    (
        "unreachable",
        POOLC,
        "if (Size > MAX_POOL_SIZE) {",
        "CoreInternalAllocatePool",
        "dead - MAX_POOL_SIZE is MAX_ADDRESS - POOL_OVERHEAD, which no "
        "firmware-sized request can exceed",
    ),
    (
        "unreachable",
        MEMPROT,
        "if (ImageRecord == NULL) {\n    Status = EFI_OUT_OF_RESOURCES;",
        "ProtectUefiImage",
        "dead here - it sits after the `case DO_NOT_PROTECT:` early return, and "
        "GetUefiImageProtectionPolicy answers DO_NOT_PROTECT for every image "
        "because gDxeMps is zero on this platform",
    ),
    (
        "unreachable",
        POOLC,
        "Pool  = LookupPoolHead (PoolType);",
        "CoreAllocatePoolI",
        "dead - LookupPoolHead answers &mPoolHead[Type] for every type below "
        "EfiMaxMemoryType, and the types that could make it return NULL are "
        "rejected by CoreInternalAllocatePool before it is called",
    ),
    (
        "pool",
        POOLC,
        "return (*Buffer != NULL) ? EFI_SUCCESS : EFI_OUT_OF_RESOURCES;",
        "CoreInternalAllocatePool",
        "reachable, and it is the only line in the pool path that actually "
        "*reports* exhaustion - CoreAllocatePoolI returning NULL is the whole "
        "mechanism, and it returns NULL only when CoreAllocatePoolPagesI could "
        "not get its pages. So a pool failure is a page failure with a smaller "
        "request, and the probe cannot separate the two by the name",
    ),
    (
        "unreachable",
        POOLC,
        "  if (EFI_ERROR (Status)) {\n    return EFI_OUT_OF_RESOURCES;\n  }\n"
        "\n  *Buffer = CoreAllocatePoolI",
        "CoreInternalAllocatePool",
        "dead - the Status is CoreAcquireLockOrFail's, which is EFI_ACCESS_DENIED "
        "only on lock reentrancy, and every acquisition on every path is paired "
        "with a release",
    ),
    (
        "unreachable",
        PAGEC,
        "Status = EFI_OUT_OF_RESOURCES;\n      goto Done;",
        "CoreInternalAllocatePages",
        "the FindFreePages refusal - reachable only on the arithmetic, which is "
        "what P2 FREE largest= measures",
    ),
    (
        "absent",
        BASEPE,
        "OUT_OF_RESOURCES",
        "-",
        "the PE/COFF loader cannot return it at all - the token does not occur "
        "in the file, so PeCoffLoaderLoadImage can only give Load Error. This is "
        "the one entry whose anchor is a claim of *absence*: it is checked by "
        "searching for the token and requiring it not to be there",
    ),
    # ---- EFI_NOT_FOUND --------------------------------------------------------
    (
        "page",
        PAGEC,
        "// Page 0 is not allowed to be allocated as it is reserved for null pointer detection",
        "CoreInternalAllocatePages",
        "reachable - AllocateAddress at 0 is refused before anything is searched, "
        "which is the branch a reloc-stripped image with ImageBase 0 takes",
    ),
    (
        "fragmentation",
        PAGEC,
        "covers multiple entries",
        "CoreConvertPagesEx",
        "reachable - a typed conversion needs one memory-map entry, so a free run "
        "spanning two is refused with NOT_FOUND and not OUT_OF_RESOURCES",
    ),
]


# Verdicts that account for a whole file's produce sites without the verdict
# living in that file. Two files in the path produce the status in ways that are
# answered elsewhere in the tree, and saying so here is better than leaving them
# in the ledger where they would read as unexplained:
#
#   * DxeServicesLib reads a whole image file into a pool buffer - the largest
#     allocation a load makes - and reports every failure of it as NULL, which
#     `CoreLoadImageCommon` turns into Not Found rather than this status.
#
#   * MemoryProtectionSupport's producers are all reached, if at all, from
#     `ProtectUefiImage`, which calls the one that runs here as a statement and
#     discards its status.
#
# Each entry carries the anchor of the check that justifies it, in the file that
# check lives in, so these claims are checked the same way the ledger's are and
# print the same way when they fail: (rel, function, anchor, reason).
COVERS = {
    DXESVC: (
        IMAGEC,
        "CoreLoadImageCommon",
        "if (FHand.Source == NULL) {",
        "a NULL file buffer is mapped to Not Found there, so every producer in "
        "DxeServicesLib reports as that name and never as this one - and the "
        "buffer itself is the largest single allocation a load makes, which is "
        "why its absence from the ledger is worth stating rather than assuming",
    ),
    PECOFPROT: (
        MEMPROT,
        "ProtectUefiImage",
        "CreateNonProtectedImagePropertiesRecord ((EFI_PHYSICAL_ADDRESS)(UINTN)"
        "LoadedImage->ImageBase, LoadedImage->ImageSize);",
        "called as a statement with its status discarded, on the one branch every "
        "image takes on this platform; this file's remaining producers hang off "
        "the protection machinery below `Finish:`, which is commented out",
    ),
}


def read(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def status_table(printlib):
    """The `%r` table: index -> (printed name, identifier suffix).

    The suffix is the identifier with its `RETURN_`/`EFI_` prefix removed, which
    is what the source files can be searched for: `EFI_OUT_OF_RESOURCES` and
    `RETURN_OUT_OF_RESOURCES` share the number *and* the spelling after the
    prefix, which is exactly why the printed name alone does not say which one a
    failure came back as.
    """
    text = read(printlib)
    if text is None:
        sys.exit(f"load-sites: no {printlib}")
    names = {}
    for m in re.finditer(r'"([^"]+)",\s*//\s*(RETURN|EFI)_([A-Z_0-9]+)\s*=\s*(\d+)', text):
        names[int(m.group(4))] = (m.group(1), m.group(3))
    if not names:
        sys.exit("load-sites: could not parse the status table out of PrintLibInternal.c")
    return names


def functions(text):
    """[(name, first_line, last_line)] for top-level definitions, by brace match."""
    out = []
    for m in re.finditer(r"\n([A-Za-z_]\w*)\s*\(\n", text):
        start = text.index("{", m.end())
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    a = text.count("\n", 0, m.start()) + 2
                    b = text.count("\n", 0, i) + 1
                    out.append((m.group(1), a, b))
                    break
    return out


def enclosing(fns, line):
    """The innermost definition containing `line` - the last one that starts by it."""
    cands = [(a, name) for name, a, b in fns if a <= line <= b]
    return max(cands)[1] if cands else "-"


def classify(line):
    """Whether a line holding the token *produces* it, tests it, or just names it.

    Without this the occurrence list reads as if every line were a return site -
    most of them are not. A produce is a `return EFI_X` or an assignment to a
    status variable; a test is a comparison (which propagates someone else's
    status and is a lead, not a mechanism); everything else is documentation.
    """
    s = line.strip()
    if s.startswith("//") or s.startswith("*") or s.startswith("@retval"):
        return "doc"
    if re.search(r"\breturn\s+(EFI|RETURN)_", s) or re.search(r"\w+\s*=\s*(EFI|RETURN)_", s):
        return "produce"
    if re.search(r"[=!]=\s*(EFI|RETURN)_", s):
        return "test"
    return "doc"


def sites(mu, rel, token):
    """Every line of a file holding the token, with its enclosing function."""
    text = read(os.path.join(mu, rel))
    if text is None:
        return None
    fns = functions(text)
    hits = []
    for i, line in enumerate(text.split("\n"), 1):
        if token in line:
            hits.append((i, enclosing(fns, i), classify(line), line.strip()))
    return hits


def check(mu, rel, anchor, fn, kind):
    """Where the anchor stands now: ok, or one of the four ways it can fail.

    Three of the four are about a tree that has moved, and they are separate
    answers because they need separate responses: `gone` means the mechanism the
    verdict describes is no longer spelled that way, `moved` means the text is
    there but in another function - so the verdict may be about the right words
    and the wrong code - and `missing` means the file itself is not in this tree.

    The fourth is the mirror image: an `absent` entry's claim *is* that the token
    is not there, so it holds when the search finds nothing and fails when the
    loader has gained a way to return the status.
    """
    text = read(os.path.join(mu, rel))
    if text is None:
        return "missing"
    present = anchor in text
    if kind == "absent":
        return "absent" if not present else "present"
    if not present:
        return "gone"
    if fn == "-":
        return "ok"
    line = text.count("\n", 0, text.index(anchor)) + 1
    return "ok" if enclosing(functions(text), line) == fn else "moved"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mu", default=DEFAULT_MU)
    ap.add_argument("--status", default="Out of Resources")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    mu = os.path.normpath(args.mu)
    names = status_table(os.path.join(mu, PRINTLIB))

    print("load-sites: what can return a load failure's status\n")
    if args.all:
        print("the %r table, as PrintLibInternal.c declares it:\n")
        for idx in sorted(names):
            print(f"  {idx:>3}  {names[idx][0]:<22} {names[idx][1]}")
        print()
        print("  (a load failure's own line is `P2 DIAG %c %g %r` - phase, file GUID,")
        print("   status name - so the name is the whole of the reading, and the")
        print("   ledger below is keyed on it.)")
        return 0

    wanted = args.status
    code = next((i for i, (n, _s) in names.items() if n == wanted), None)
    if code is None:
        near = sorted({n for n, _s in names.values() if wanted.lower() in n.lower()})
        sys.exit(f"load-sites: {wanted!r} is not in the %r table"
                 + (f"; did you mean {near}?" if near else "; try --all"))
    name, suffix = names[code]
    print(f"status: {name!r} = EFI_STATUS {code} | MAX_BIT, searched as *{suffix}*\n")

    # The ledger is keyed on the status *identifier*, not on the printed name:
    # `RETURN_OUT_OF_RESOURCES` and `EFI_OUT_OF_RESOURCES` share both the number
    # and the last four words, which is the whole reason a name is ambiguous.
    KEYED = {
        "OUT_OF_RESOURCES": ("page", "pool", "preset", "unreachable", "absent"),
        "NOT_FOUND": ("page", "fragmentation"),
    }
    entries = [e for e in LEDGER if e[0] in KEYED.get(suffix, ())]
    if not entries:
        print("  no ledger for this status: it names its own mechanism, or the")
        print("  reading needs one written first. The raw sites are below.\n")

    print("the ledger - each entry's anchor is checked in the tree, so a verdict")
    print("cannot outlive the code it was about:\n")
    COVERED = []
    for kind, rel, anchor, fn, verdict in entries:
        state = check(mu, rel, anchor, fn, kind)
        if kind == "absent":
            ok = state == "absent"
            note = ("" if ok else
                    "!! the token is in the file now - the PE/COFF loader has gained "
                    "a way to return this status, and the verdict is wrong")
        else:
            ok = state == "ok"
            note = {
                "gone": "!! anchor gone - the source no longer says this, so the "
                        "verdict below is about a tree that has moved",
                "moved": f"!! the text is there, but not in {fn} - it moved, and the "
                         f"verdict may be about the right words in the wrong code",
                "missing": "!! no such file in this tree",
            }.get(state, "")
        mark = "  " if ok else "!!"
        print(f"  {mark} [{kind}] {rel}")
        if note:
            print(f"       {note}")
        where = "(absence)" if kind == "absent" else f"in {fn}: " + anchor.splitlines()[0].strip()[:60]
        print(f"       {where}")
        print(f"       -> {verdict}")
        print()

    print(f"every occurrence of {suffix} in the files a load can fail out of:")
    tally = {"produce": 0, "test": 0, "doc": 0}
    for rel in PATH_FILES:
        hits = sites(mu, rel, suffix)
        if hits is None:
            print(f"  {rel}: MISSING")
            continue
        if not hits:
            print(f"  {rel}: none")
            continue
        produces = [h for h in hits if h[2] == "produce"]
        if rel in COVERS:
            # The produces are accounted for as a group, and the anchor of the
            # accounting is checked, so this reads as a claim and not as a shrug.
            crel, cfn, canchor, reason = COVERS[rel]
            state = check(mu, crel, canchor, cfn, "reachable")
            mark = "  " if state == "ok" else "!!"
            print(f"  {mark} {rel}: {len(produces)} produce, all covered - see the note below")
            if state != "ok":
                print(f"     !! the covering anchor {canchor[:40]!r} in {crel} is {state}")
            for line, fn, kind, text in hits:
                tally[kind] += 1
            COVERED.append((rel, len(produces), crel, cfn, canchor, reason))
            continue
        for line, fn, kind, text in hits:
            tally[kind] += 1
            print(f"  {rel}:{line}  [{fn}]  {kind:<7} {text[:66]}")
    print(f"\n  {tally['produce']} produce the status, {tally['test']} only test it,"
          f" {tally['doc']} are documentation")
    for rel, n, crel, cfn, canchor, reason in COVERED:
        print()
        print(f"  {rel}: {n} producers, none of them reachable as this status.")
        print(f"    checked in {os.path.basename(crel)}, {cfn}:")
        print(f"      {canchor.splitlines()[0].strip()[:70]}")
        for line in reason.split(". "):
            if line.strip():
                print(f"    {line.strip().rstrip('.')}.")
    print()
    print("  The produces are the whole of the question; every one of them should")
    print("  appear in the ledger above or in a covered group here, and one that")
    print("  does neither is a mechanism nobody has written a verdict for yet.")

    print()
    print("Reading it: a status with more than one *reachable* site cannot be")
    print("interpreted from the name alone, and the two fields that separate the")
    print("reachable ones are the probe's own - `P2 FREE largest=` for the page")
    print("allocator and `P2 ERR`'s group counts for how many failures share the")
    print("name. A pool site and a page site both print the same word.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
