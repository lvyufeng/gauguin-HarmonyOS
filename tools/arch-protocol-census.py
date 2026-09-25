#!/usr/bin/env python3
"""Which of the thirteen architectural protocols DxeCore demands was installed, and why not.

`CoreAllEfiServicesAvailable` (`DxeProtocolNotify.c:81-93`) returns `EFI_NOT_FOUND`
at the first entry of `mArchProtocols[]` that was never installed, and
`CoreDisplayMissingArchProtocols` (`:263`) prints one line per missing entry using
the printable name in `mMissingProtocols[]` (`:63`). So the panel can say **which**
protocols are absent, and the answer is a set of names off a photograph.

What the panel cannot say is why, and what a person reading the photograph cannot
easily check is whether the set is complete. Both are decidable here, and this
tool is the third time the same question has had to be decided by hand.

**The reading this tool was written for.** Step 4.9 recorded the panel as showing
**eight** names - `Security, Bds, Watchdog, Variable, Capsule, Monotonic, Reset,
Real Time Clock` - and, from those eight, asserted that five were present
including `Variable Write`. That assertion is impossible on the source alone.
`Variable` and `Variable Write` are installed two statements apart by one driver:
`VariableDxe.c:578` installs the first inside `VariableServiceInitialize`, and
`:396` installs the second inside `VariableWriteServiceInitializeDxe`, which is
only reached *from* that same entry point (`:601` when
`PcdEmuVariableNvModeEnable` is TRUE, otherwise through `FtwNotificationEvent` at
`:499`). Nothing else in the tree installs either one - measured, not assumed -
and `VariableRuntimeDxe` is `ap31 pos 30` in `P2 SEQ`, where the letter is `L`.
`L` is stamped before the entry point is called, so the driver's code never ran
and **both** protocols are absent. The set has nine names, and the transcript was
short one.

That is the class of error Step 4.94 was about, one step removed: not a number a
reader could not ask again, but a *list* a reader could not check. The panel's
one line per missing entry is easy to transcribe one short when two adjacent
entries of `mArchProtocols[]` share a producer, and `Variable`/`Variable Write`
are adjacent and do.

**What makes the set checkable.** The volume's `P2 SEQ` letter string is positional
and independently corroborated - 46 letters against `P2 STATS apriori=46/70`, with
two anchors from a volume reading that did not come from the string - so it is the
better record of the two. And on this firmware the partition is exact: nine
protocols are absent and nine of the thirteen producers carry `L`; four are present
and the four remaining producers carry `s`. No residue either way. The tool
asserts that bijection rather than printing the two counts side by side, because
two counts that agree are a coincidence until the *same* nine names land on the
same nine letters.

**Where the producer map comes from, and why it is measured.** Producer-to-protocol
is not recoverable from a volume: an FFS file records the protocols it consumed
and not the ones it installs, so the map has to be read from the source. It is read
here by finding, for each GUID identifier `mArchProtocols[]` names, the
`InstallProtocolInterface` / `InstallMultipleProtocolInterfaces` calls whose
argument list literally names that identifier - the argument list being delimited
by parenthesis depth rather than by a character window, so an identifier mentioned
in a comment or an unrelated call cannot be counted - and then reducing each file
to its module through the `.inf` that owns it. The tree contains several installers
per protocol (`UefiCpuPkg`'s `CpuDxe` beside `ArmPkg`'s, `PcAtChipsetPkg`'s
`HpetTimerDxe` beside `ArmPkg`'s `TimerDxe`, `VariableSmmRuntimeDxe` beside
`VariableRuntimeDxe`), and the volume decides which of them is real. The ones the
volume does not carry are named in a note under the table rather than dropped:
"this protocol has one installer" and "this protocol has one installer *in this
volume*" are different sentences, and only the second is a fact about the build
being read. The membership test is against the volume's **whole** 123-file roster,
not its 70-entry a-priori front, because a producer that is in the volume without
being in the a-priori array is a third answer and a different one: such a driver
may still be scheduled by the dispatcher, depex-gated, and its protocol's absence
would not be a load failure. Collapsing that into "not built" would hide exactly
the distinction the letters are read through. A protocol whose *every* installer is
outside the volume is reported as a finding with no letter at all.

`tools/depex-census.py` carries the same thirteen-to-nine map as a hand-typed
`PRODUCERS` dict. This tool is where it is derived, and the two agreeing is the
check on both.

**One near-miss, recorded because it very nearly shipped as a result.** The first
version of this tool resolved each identifier through `depex-census.py`'s
GUID-to-name map, inverted. That map is keyed by whichever spelling of a GUID a
file reached first, and for these thirteen it is the header macro -
`MdePkg/Include/Protocol/Bds.h` writes `#define EFI_BDS_ARCH_PROTOCOL_GUID`, and
`MdePkg/MdePkg.dec` writes the same GUID as `gEfiBdsArchProtocolGuid`, which is
the spelling `mArchProtocols[]` uses - so the inversion answered **0 for all
thirteen**. The tool exited on the first one, and its own guard is what stopped
it: a lookup that returns nothing for every input looks exactly like a firmware
with nothing to find, and had it printed that count instead of exiting, the
result would have been "no producer for any architectural protocol", which is a
sentence about the search and reads like a sentence about the firmware. The
definitions are read directly now, keyed on the identifier itself.

Usage:
    tools/arch-protocol-census.py work/out/p2-4.94/Mu-gauguin-silicon-gzip.img
    tools/arch-protocol-census.py IMG --seq "ssssssssssssssssssLLLsLLLLL..." \\
        --panel "Security,Bds,Watchdog,Variable,Capsule,Monotonic,Reset,Real Time Clock"
    tools/arch-protocol-census.py IMG --tree DIR

Without `--seq` every row is undecided by construction and the tool reports the
build: thirteen protocols, the producer the volume gives each, and which installers
the volume does not carry. With it, the letters decide absent from present, the
partition is asserted as a bijection, and the exit status is nonzero when any row
is undecided or when `--panel` disagrees. `--panel` alone is a transcription
check; `--seq` alone is the volume's own answer.
"""

import argparse
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TREE = os.path.join(ROOT, "work", "uefi", "Mu-Silicium")

# The C file that owns both tables. Reading the tables rather than restating them
# is the whole point: `mMissingProtocols[]` is what the panel prints, so a decoder
# that typed the names would be checking a transcription against a transcription.
NOTIFY = os.path.join("Mu_Basecore", "MdeModulePkg", "Core", "Dxe", "DxeMain",
                      "DxeProtocolNotify.c")

# The two calls a module can install a protocol with.
# `InstallMultipleProtocolInterfaces` is the one that matters here - it is how
# `VariableDxe.c` installs both of the variable protocols and how most of the
# locks and checks are installed - and leaving it out would report the variable
# stack as having no producer at all.
INSTALL = re.compile(r"\bInstall(?:Multiple)?ProtocolInterface\w*\s*\(")


def load_sibling(name, filename):
    """Import a tool beside this one; the names carry hyphens, so not by module."""
    path = os.path.join(ROOT, "tools", filename)
    if not os.path.isfile(path):
        sys.exit(f"arch-protocol-census: {filename} is not beside this tool")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def strip_comments(text):
    """C comments out, string literals left alone.

    A GUID identifier in a comment is not an install site, and this tree's comments
    quote source lines often enough (`VariableDxe.c:502` quotes the one above it)
    that leaving them in would credit a module with installing a protocol it only
    discusses.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def arch_table(tree):
    """[(identifier, printable)] for the thirteen, in `mArchProtocols[]` order.

    Both arrays are read and asserted to agree. They are two spellings of one list,
    and if they ever drift the printable name would no longer belong to the GUID
    whose presence is being decided - which is a wrong answer that reads as right.
    """
    path = os.path.join(tree, NOTIFY)
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        sys.exit(f"arch-protocol-census: cannot read {path}: {exc}")

    m = re.search(r"mArchProtocols\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if not m:
        sys.exit("arch-protocol-census: mArchProtocols[] is no longer in "
                 "DxeProtocolNotify.c - the table this reads has moved")
    ordered = re.findall(r"\{\s*&(\w+)\s*,", m.group(1))

    m = re.search(r"mMissingProtocols\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if not m:
        sys.exit("arch-protocol-census: mMissingProtocols[] is no longer in "
                 "DxeProtocolNotify.c - the names the panel prints have moved,"
                 " and this tool has nothing to check a transcription against")
    named = dict(re.findall(r"\{\s*&(\w+)\s*,\s*\"([^\"]*)\"\s*\}", m.group(1)))

    if len(ordered) != 13:
        sys.exit(f"arch-protocol-census: mArchProtocols[] has {len(ordered)} "
                 "entries; the UEFI architectural protocol set is thirteen, so"
                 " either the array or this tool is wrong")
    if [i for i in ordered if i not in named]:
        sys.exit("arch-protocol-census: mArchProtocols[] names "
                 + ", ".join(i for i in ordered if i not in named)
                 + ", which mMissingProtocols[] does not - the printable name of"
                 " a missing protocol is not known, and an absent name prints as"
                 " absence of the wrong thing")
    return [(i, named[i]) for i in ordered]


def arg_span(text, open_paren):
    """The span of an argument list, from its `(` to the `)` that closes it.

    Depth-counted rather than windowed. A fixed window is a guess about how long an
    argument list is, and this tree has `InstallMultipleProtocolInterfaces` calls
    that run to a dozen lines; a window that reaches past the closing paren into
    the next statement would credit a module with installing whatever comes next.
    """
    depth, i = 0, open_paren
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
        i += 1
    return None


def sources(tree):
    """{path: comment-stripped text} for every .c under the tree, once."""
    out = {}
    for dirpath, dirs, files in os.walk(tree):
        if ".git" in dirpath.split(os.sep):
            continue
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if not f.endswith(".c"):
                continue
            p = os.path.join(dirpath, f)
            try:
                out[p] = strip_comments(open(p, encoding="utf-8",
                                             errors="replace").read())
            except OSError:
                continue
    return out


def module_of(path):
    """The module name a source file belongs to, or None.

    A module directory holds its `.inf` beside its sources, and where it does not
    the INF is one level up; the search is bounded at three so a source with no
    INF anywhere near it comes back None rather than being billed to whichever
    INF happens to be nearest at the tree root.
    """
    d = os.path.dirname(path)
    for _ in range(3):
        if not os.path.isdir(d):
            return None
        infs = sorted(f for f in os.listdir(d) if f.endswith(".inf"))
        if infs:
            for f in infs:
                try:
                    t = open(os.path.join(d, f), encoding="utf-8",
                             errors="replace").read()
                except OSError:
                    continue
                if os.path.basename(path) in t:
                    return base_name(t, f)
            if len(infs) == 1:
                try:
                    t = open(os.path.join(d, infs[0]), encoding="utf-8",
                             errors="replace").read()
                except OSError:
                    return None
                return base_name(t, infs[0])
        d = os.path.dirname(d)
    return None


def base_name(inf_text, inf_file):
    """The name the built FFS file carries, which is what the UI section holds.

    `BASE_NAME` when the INF states one, and the INF's own stem when it does not -
    `MdeModulePkg/Universal/WatchdogTimerDxe/WatchdogTimer.inf` has no `BASE_NAME`
    and builds to a file the volume lists as `WatchdogTimer`, which is how the
    volume names it and therefore the name that has to match.
    """
    m = re.search(r"^\s*BASE_NAME\s*=\s*(\S+)", inf_text, re.M)
    return m.group(1) if m else os.path.splitext(inf_file)[0]


def producers(tree, ident, texts):
    """{module name: [source files]} for every module installing `ident`'s GUID."""
    out = {}
    for path, text in texts.items():
        if ident not in text:
            continue
        for m in INSTALL.finditer(text):
            span = arg_span(text, m.end() - 1)
            if span is None:
                continue
            if not re.search(r"\b" + re.escape(ident) + r"\b", span):
                continue
            mod = module_of(path)
            if mod is None:
                out.setdefault(f"(no .inf near {os.path.relpath(path, tree)})",
                               []).append(path)
            else:
                out.setdefault(mod, []).append(path)
            break
    return out


def guid_of(ident, guids):
    """The GUID string `ident` is defined as, from the definition it is defined in.

    It is looked up by the identifier `mArchProtocols[]` writes, and not through
    `tools/depex-census.py`'s GUID-to-name map, because that map is keyed by
    whichever spelling a file reached first and for these thirteen it is the
    header macro: `MdePkg/Include/Protocol/Bds.h` writes
    `#define EFI_BDS_ARCH_PROTOCOL_GUID {...}` and is walked before
    `MdePkg/MdePkg.dec`, whose `[Protocols]` section writes the same GUID as
    `gEfiBdsArchProtocolGuid`. Inverting that map therefore answers nothing for any
    of the thirteen - it reported **0** for all of them on the first run of this
    tool, which is the failure mode a name-keyed lookup produces: a clean zero that
    reads as a fact about the firmware rather than about the lookup.

    The definition is read instead, from the `.dec`/`.h` file that carries it, and
    an identifier with no definition found is a broken lookup rather than a
    protocol without a GUID.
    """
    g = guids.get(ident)
    if g is None:
        sys.exit(f"arch-protocol-census: {ident} has no `{{ ... }}` definition under"
                 " the tree searched; a protocol whose GUID is not resolved cannot"
                 " be looked for, and reporting it as absent would be reporting the"
                 " lookup's failure as the firmware's")
    return g


def guid_definitions(tree, idents):
    """{identifier: GUID string} read from the definitions themselves.

    `Build/` and `Binaries/` are skipped: the first is this build's own object
    tree and the second holds prebuilt firmware for other phones, so a definition
    found only there is not this platform's. The walk is by extension and gated on
    the identifier appearing at all, so the thirteen cost one pass over the
    headers rather than a parse of every one of them.
    """
    pat = re.compile(
        r"\b(\w+)\s*=?\s*\{\s*"
        r"0x([0-9A-Fa-f]{1,8}),\s*0x([0-9A-Fa-f]{1,4}),\s*0x([0-9A-Fa-f]{1,4}),\s*"
        r"\{\s*0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2}),\s*"
        r"0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2}),\s*"
        r"0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2}),\s*"
        r"0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2})\s*\}\s*\}", re.S)
    want = set(idents)
    out = {}
    for dirpath, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "Build", "Binaries")]
        for f in files:
            if not f.endswith((".h", ".dec")):
                continue
            path = os.path.join(dirpath, f)
            try:
                t = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if not any(i in t for i in want):
                continue
            for m in pat.finditer(t):
                g = m.groups()
                if g[0] not in want or g[0] in out:
                    continue
                out[g[0]] = (f"{int(g[1], 16):08X}-{int(g[2], 16):04X}-"
                             f"{int(g[3], 16):04X}-"
                             f"{int(g[4], 16):02X}{int(g[5], 16):02X}-"
                             + "".join(f"{int(x, 16):02X}" for x in g[6:12]))
    return out


def report(rows, label):
    print(f"== the thirteen architectural protocols  [{label}]")
    print(f"   {'#':>2}  {'protocol':18s} {'guid':36s} {'producer':28s}"
          f" {'ap':>3} {'pos':>4} {'SEQ':>3}  verdict")
    for n, r in enumerate(rows, 1):
        where = f"{r['ap']:3d} {r['pos']:4d}" if r["ap"] else "  -    -"
        print(f"   {n:>2}  {r['name']:18s} {r['guid']} {r['module']:28s}"
              f" {where} {r['letter'] or ' ':>3}  {r['verdict']}")
    print()
    # A protocol several modules in the tree install is the normal case - the
    # variable stack has two installers, the CPU protocol three, the timer three
    # - and the volume decides which one is real. Printing only the one that won
    # would make "this protocol has one installer" and "this protocol has one
    # installer *in this volume*" the same line of output, and the second is a
    # fact about the build. The losers are named for that reason.
    for r in rows:
        if r.get("others"):
            print(f"   note: {r['name']} is also installed by "
                  + ", ".join(r["others"]) + ", not built into this volume")


def summarize(rows):
    absent = [r for r in rows if r["verdict"] == "absent"]
    present = [r for r in rows if r["verdict"] == "present"]
    unknown = [r for r in rows if r["verdict"] not in ("absent", "present")]
    if absent or present or not unknown:
        print(f"  {len(absent)} absent, {len(present)} present"
              + (f", {len(unknown)} undecided (no SEQ letter)" if unknown else ""))
    for word, group in (("absent ", absent), ("present", present)):
        if group:
            print(f"  {word}: " + ", ".join(
                f"{r['name']} ({r['module']} ap{r['ap']} {r['letter']})"
                for r in group))
    return absent, present, unknown


def bijection(rows):
    """[] or the ways the L/present partition and the protocol partition differ.

    This is the assertion the step turns on. Nine producers carry `L` and four
    carry `s`; nine protocols are absent and four are present - and two counts that
    agree prove nothing until the same names are on both sides of the pairing. A
    set difference is what says which name is on one side and not the other, and
    it is the check that would have caught a missing name in a transcription
    before this tool existed.
    """
    bad = []
    for r in rows:
        if r["letter"] == "L" and r["verdict"] != "absent":
            bad.append(f"{r['name']}: producer {r['module']} is `L` but the "
                       f"protocol reads {r['verdict']}")
        if r["letter"] == "s" and r["verdict"] != "present":
            bad.append(f"{r['name']}: producer {r['module']} is `s` but the "
                       f"protocol reads {r['verdict']}")
        if r["letter"] in ("L", "s"):
            continue
        bad.append(f"{r['name']}: producer {r['module']} carries "
                   f"{r['letter']!r} and the partition is undecided")
    return bad


def check_panel(rows, text):
    """Compare a transcribed panel list against the measurement.

    The transcription is the thing being checked, so every way it can disagree is
    reported: a name it contains that `mMissingProtocols[]` does not define (then
    the transcription is of something else), a name missing from it whose producer
    is `L` (the error this tool was written for), and a name in it whose producer
    is `s` (which would mean the panel and the volume disagree about the run).

    A name that is not defined but *is a prefix of* a defined one is called out as
    an abbreviation rather than as an invention. Both are wrong transcriptions and
    the remedy is different: `Watchdog` for `Watchdog Timer` is a compression of a
    line the panel printed in full, while a name that is nobody's prefix means the
    reading is of some other output, and the two should not read the same.
    """
    known = {r["name"] for r in rows}
    said = [s.strip() for s in text.split(",") if s.strip()]
    bad = [s for s in said if s not in known]
    abbreviated = {}
    if bad:
        for s in bad:
            full = sorted(n for n in known if n.startswith(s) and n != s)
            if full:
                abbreviated[s] = full
                print(f"  !! the transcription says {s!r}, which"
                      " mMissingProtocols[] does not define; it is an abbreviation"
                      f" of {', '.join(full)} - the panel prints that string in"
                      " full, so the short form is the transcription's, not the"
                      " table's")
            else:
                print(f"  !! the transcription names {s!r}, which"
                      " mMissingProtocols[] does not define and which is no"
                      " abbreviation of any name it does - this reading is of some"
                      " other table's output")
    # A name that a shortened transcription covers is not also reported as
    # missing. The cover has to be an *abbreviation* for that - a string that is
    # not itself a defined name - and the narrowness matters: `Variable` is a
    # prefix of `Variable Write` and is a defined name, so a transcription
    # reading `Variable` and stopping there must still be told it lost
    # `Variable Write`. That pair is the one this tool was written for and a
    # prefix rule applied to `missing` instead of to `bad` would swallow it.
    covered = {n for full in abbreviated.values() for n in full}
    missing = [r for r in rows if r["name"] not in said]
    short = [r for r in missing if r["letter"] == "L" and r["name"] not in covered]
    extra = [r for r in rows if r["name"] in said and r["letter"] == "s"]
    print(f"  transcribed: {len(said)} names; measured absent: "
          f"{sum(1 for r in rows if r['verdict'] == 'absent')}")
    for r in short:
        print(f"  !! {r['name']} is missing from the transcription, and its"
              f" producer {r['module']} is ap{r['ap']} pos {r['pos']} in `P2 SEQ`,"
              f" where the letter is `L`")
    for r in extra:
        print(f"  !! {r['name']} is in the transcription and its producer"
              f" {r['module']} is `s` - the panel and the volume disagree about"
              " this run")
    return bool(bad or short or extra)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="the payload .img, or an FD")
    ap.add_argument("--tree", default=DEFAULT_TREE, help="the Mu-Silicium checkout")
    ap.add_argument("--seq", metavar="STRING",
                    help="a P2 SEQ string off the panel - what makes the verdict a"
                         " reading of a run rather than of the build")
    ap.add_argument("--panel", metavar="NAMES",
                    help="the names a transcription of the missing-protocol lines"
                         " contains, comma separated, to check against")
    args = ap.parse_args()

    if not os.path.isdir(args.tree):
        print(f"no such tree: {args.tree}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.image):
        print(f"no such image: {args.image}", file=sys.stderr)
        return 1

    # Only `apriori-index.py` is imported. The GUID-to-name map in
    # `depex-census.py` is not used and must not be: it is keyed by whichever
    # spelling a file reached first, and for these thirteen that is the header
    # macro, so inverting it answers `0` for every one of them. That is a clean
    # zero that reads as a fact about the firmware; it is a fact about the map.
    ai = load_sibling("apriori_index", "apriori-index.py")

    table = arch_table(args.tree)
    guids = guid_definitions(args.tree, [i for i, _ in table])
    texts = sources(args.tree)
    entries, nfiles, ctx = ai.build(args.image)
    skips = {e["file"] for e in entries if e["type"] != 0x07}
    pos = ai.positions(entries, skips)

    # The whole volume, not just its a-priori front. A producer that is in the
    # volume but *not* in the a-priori array is a materially different finding
    # from one that is not in the volume at all: the first would be a driver the
    # dispatcher is allowed to schedule and a depex away from running, the second
    # is a module this build does not carry. Matching against the a-priori
    # entries alone collapses the two, and the collapse is invisible - both read
    # as "not the producer this protocol got".
    #
    # The walk is `build`'s, taken out of its context rather than repeated:
    # `unpack` prints the FD and its files each time it runs, so a second read of
    # the same volume would put a second copy of that block above this report.
    roster = {t[3] for t in
              ctx["fvi"].roster(ctx["files"], ctx["offsets"], ctx["inner"])
              if t[3]}

    # A UI name is what a producer module is matched by, so two a-priori entries
    # with the same name would make the match ambiguous rather than wrong - which
    # is worse in a table, because it reads as a decision.
    by_name = {}
    for e in entries:
        if e["name"]:
            by_name.setdefault(e["name"], []).append(e)

    print(f"{os.path.basename(args.image)}: FVMAIN {nfiles} files, "
          f"Apriori file {len(entries)} entries")

    letters = None
    if args.seq:
        s = args.seq.strip().strip("[]").replace(" ", "")
        for p, want, got in ai.anchors_hold(entries, pos):
            print(f"  !! ANCHOR BROKEN: position {p} should be {want}, this"
                  f" volume says {got} - the letters would be read against the"
                  " wrong drivers")
            return 1
        letters = s

    rows, stalled, unpromoted = [], [], []
    for n, (ident, printable) in enumerate(table, 1):
        guid = guid_of(ident, guids)
        found = producers(args.tree, ident, texts)
        if not found:
            sys.exit(f"arch-protocol-census: no module under {args.tree} installs"
                     f" {ident}, which mArchProtocols[] requires - the search is"
                     " broken, not the firmware, and an empty producer list would"
                     " report the protocol as having no origin")
        in_volume = {m: None for m in found if m in by_name}
        # The three ways a producer can be missing from the answer, kept apart
        # because they are three different findings: built and promoted (it
        # carries a letter), built and *not* in the a-priori array (a driver the
        # dispatcher may still schedule, gated by a depex), and not in the volume
        # at all (a module this build does not carry).
        built = {m: None for m in found if m in roster}
        others = sorted(m for m in found if m not in by_name)
        if not in_volume:
            if built:
                unpromoted.append((printable, sorted(built)))
                rows.append(dict(name=printable, guid=guid,
                                 module="(in the volume, not a-priori)",
                                 ap=None, pos=None, letter=None, others=others,
                                 verdict="no letter - not promoted"))
            else:
                stalled.append((printable, sorted(found)))
                rows.append(dict(name=printable, guid=guid, module="(none built)",
                                 ap=None, pos=None, letter=None, others=[],
                                 verdict="no producer in this volume"))
            continue
        for mod in sorted(in_volume):
            hits = by_name[mod]
            if len(hits) != 1:
                sys.exit(f"arch-protocol-census: {mod} is {len(hits)} a-priori"
                         " entries; a producer that cannot be pinned to one"
                         " position cannot carry one letter")
            e = hits[0]
            p = pos.get(e["file"])
            ch = letters[p] if (letters and p is not None and p < len(letters)) \
                else None
            rows.append(dict(
                name=printable, guid=guid, module=mod,
                ap=e["file"], pos=p, letter=ch,
                others=others,
                verdict={"L": "absent", "s": "present"}.get(
                    ch, "undecided" if ch is None else "undecided (" + ch + ")")))

    print(f"  {len(table)} protocols, {len(table) - len(stalled) - len(unpromoted)}"
          f" with a promoted producer, {len(unpromoted)} whose producer is in the"
          f" volume but not in the a-priori array, {len(stalled)} with none in the"
          f" volume at all"
          + (f": {', '.join(f'{n} (installer {m[0]})' for n, m in stalled)}"
             if stalled else ""))
    print()
    report(rows, "this volume" if not letters else "this volume and this run")

    absent, present, unknown = summarize(rows)
    if letters:
        bad = bijection(rows)
        if bad:
            print("\n  !! the partition is not the same on both sides:")
            for line in bad:
                print(f"     {line}")
        else:
            print(f"\n  the partition agrees: the {len(absent)} protocols whose"
                  f" producer is `L` are the {len(absent)} absent, and the"
                  f" {len(present)} whose producer is `s` are the {len(present)}"
                  " present - the same names on both sides, not two counts that"
                  " happen to match")
    code = 0
    if args.panel:
        print()
        code |= check_panel(rows, args.panel)
    # Only when a run is being judged. Without `--seq` there is no letter for any
    # row and every one of the thirteen is undecided by construction, which is a
    # fact about the invocation and would make the build-only reading exit 1 on
    # every image.
    if letters and unknown:
        code |= 1
    return code


if __name__ == "__main__":
    sys.exit(main())
