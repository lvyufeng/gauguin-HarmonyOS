#!/usr/bin/env python3
"""Which P2 instruments are inside an image, decided by content.

This exists because of one number that is a lie: **every payload built for this
phase is 1,142,784 bytes.** Measured 2026-09-24 on the three payloads of record -

    work/out/boot-now-0923.img                  1,142,784   sha256 3547fd04...
    work/out/p2-4.20/Mu-gauguin-silicon-gzip.img 1,142,784   sha256 dbf131d2...
    work/out/p2-variants/Mu-gauguin-silicon-gzip.img 1,142,784  sha256 7c8fdb5a...

- so a size is not evidence, and neither is a filename: two of the three are called
`Mu-gauguin-silicon-gzip.img`, and step 4.30 already recorded a control image that
"stopped being the bytes it named without the name changing". What separates them
is a handful of `DEBUG` format strings inside `DxeCore`, and which of those are
present decides what the panel *can* print. That is the question this tool
answers, and it answers it the same way for a payload and for a readback of
`boot`, because both are Android boot images with the same payload shape.

The third line of that table is itself an instance of the rule, and the tidy-up
step 4.98 owed: `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` no longer hashes
`7c8fdb5a...`. It is the 4.74 set now, **`90b21643...`**, still 1,142,784 bytes, same
name. The table is kept as measured on that date rather than refreshed, because the
point it makes is about the date it was measured on; what a reader wants is the
command, not a number that ages.

The three images are a ladder, and the ladder is the point. It has fourteen rungs
since step 4.98 added four; the values below are what `probe-fingerprint.py IMG`
prints now, not a column someone typed:

    boot-now-0923   FREE SEQ DIAG STATS WALK NOLOAD                        6/14
    p2-4.20         the same six, plus APRI WHY ERR BIN RETRY             11/14
    p2-variants     all fourteen                                         14/14
    p2-4.94         all fourteen                                         14/14
    p2-freewhy      all fourteen, P2 FWHY in the older spelling          14/14

    rung      row it is                    boot-now-0923
    --------  ---------------------------  -------------
    P2Digest  P2 FREE largest=             present
    P2Seq     P2 SEQ [%a]                  present
    P2Why     P2 WHY [%a]                  ABSENT
    P2ErrRow  P2 ERR %r x%d                ABSENT
    P2Apri    P2 APRI bytes=               ABSENT
    P2Diag    P2 DIAG %c %g %r             present
    P2Stats   P2 STATS discovered=         present
    P2Walk    P2 WALK t=                   present
    P2NoLoad  P2 NOLOAD                    present
    P2Bins    P2 BIN init=                 ABSENT
    P2Retry   P2 RETRY                     ABSENT
    P2Key     KEY                          ABSENT
    P2Tick    K %d %c%c                    ABSENT
    P2FreeWhy P2 FWTY / P2 FWHY            ABSENT

**`P2 WHY` and `P2 ERR` are why the ladder grew past six rungs, and they are the two
rows that decide whether a reading is even possible.** `boot-now-0923`
prints a `P2 SEQ` line and no `WHY` beside it and no `ERR` below it, because
`P2WhyLetter`/`P2MarkSeq` and the grouped `P2 ERR %r x%d` were added together, after
that payload was built. `P2 SEQ` has been read off this panel three times and
`P2 WHY` has been read zero times, which is exactly what a ladder whose oldest rung
prints SEQ alone predicts. What that costs is the whole reading: `SEQ` is a string of
`s` and `L`, and `L` means only *the load failed* - it names no status. `WHY` is the
same positions with the status class in each one, and `P2 ERR` is those statuses
again **grouped and counted**, which makes one short line - `P2 ERR Out of
Resources x27`, or three lines if the causes differ - the complete answer to "did the
batch fail for one reason or twenty-seven".

**But the older spelling of that sentence was too strong, and step 4.98 is the step
that measured it.** `boot-now-0923` does carry a row that names a status in words:
`P2 DIAG %c %g %r`, one row per failure, `L <guid> Out of Resources`. What it lacks
is `P2 WHY`, the *position table* that would let a reader index the `SEQ` string. So
the corrected form of the claim is narrower and more useful:

    **A panel showing `P2 SEQ` with no `P2 WHY` anywhere on it is running
    `boot-now-0923`, and the `SEQ` string's positions cannot be resolved from that
    screen - but its `P2 DIAG` rows name the failing status in words anyway, and
    they are the rows one line below the `SEQ`.** `--expect P2ErrRow` is still the
    gate that makes the next flash worth making; `--rows` is the gate that says
    whether a payload prints `P2 DIAG` at all.

The distinction is not pedantry, it is the difference between "read more carefully"
and "build a new payload". `P2 DIAG` is not a rung of this ladder because a rung is
an *instrument* - a function a build either has or has not - and `P2Digest` owns
fifteen rows and is named by one of them. Step 4.97 read a rung report as an
inventory of everything a payload can print, and paid for it; `--rows` below is the
mode that answers the question it thought it was asking.

**`P2 FW` is the rung that is not Dispatcher.c's.** The other thirteen print from the
digest, which runs *after* the batch, so every one of them describes the heap the run
left behind. The question they were built to answer -
why a request byte for byte identical to one that succeeded a few milliseconds later
was refused - is about the heap *at the failure*, and the only place that state exists
is inside `FindFreePages`, at the instruction where the allocator gives up
(`Mem/Page.c`: `if (!PromoteMemoryResource ())`, the one place a failure becomes
terminal). So the record is taken there and printed by the digest, as two lines and
never more - rows are the scarce thing, and a third would cost the panel its second
copy. `P2 FWTY` is the terminal failures counted by memory type, which falsifies
cheaply the belief that all 27 asked for `EfiBootServicesCode`; `n` is the whole
count, so `n=0` is no terminal failure at all and `n` above zero with no second
line is terminal failures that were all smaller than four pages. `P2 FWHY` is the
first image-sized refusal and the map it was refused in: `t=`/`np=`/`a=` the
request, `big=` the largest free run the same search would have accepted, `raw=`
the largest run before the alignment clip, `free=` every conventional page left,
`c=` the descriptor count. `big` against the request decides whether a run existed
at all; `free` against `big` separates a full heap from a fragmented one; `raw`
against `big` says whether the alignment clip was the cost. **`n=0` in `P2 FWTY`,
beside 27 recorded `L`s, is also an answer** - it puts the failure outside the page
allocator entirely, in `CoreLoadPeImage`'s own `AllocateRuntimePool`.

**`P2 KEY` is what makes the bottom row of the panel readable, and it is in exactly
one of the three.** `P2Digest` calls `P2Bins()` and then `P2Key()` last
(`Dispatcher.c:2464` and `:2470`, with the comment above the second one saying so),
and `P2Key`
prints one of two lines unconditionally - so on the newest build the last populated
row of the panel is *always* the `KEY` line, in every state, including after a
wipe. On `p2-4.20` there is no `P2Key`, so the last populated row is the `P2 RETRY`
line instead: that is step 4.29's reading, and it is not a contradiction of
`tools/console-budget.py`'s "the last populated row is the KEY line" - the two are
describing two different builds.

Which gives the categorical discriminator this tool was written for:

    **A `P2 RETRY` row as the last populated row of the panel means the payload
    on the phone is the `p2-4.20`-class build - the flash did not take the newest
    one.** On the newest build that row is followed by `P2Key` with nothing in
    between, so it cannot be the last row of a run that got that far.

So the markers are read out of the sources rather than typed here. That is not
tidiness: the first draft of this tool carried a hand-typed `err=%a at=%d` for
`P2Key`, the real format is `err=%r at=%d` (`Dispatcher.c:286`), and the tool
therefore
reported `P2Key` absent from an image that has it. A tool whose job is to tell
which instrument is in an image cannot invent its own fingerprints; it reads them,
and it fails loudly if the function it names no longer contains the line it
expects.

Usage:
    tools/probe-fingerprint.py IMG...                    # the ladder, per image
    tools/probe-fingerprint.py --rows IMG...             # every P2 row, not just rungs
    tools/probe-fingerprint.py --expect P2Key IMG        # pre-flight gate, exit 1
    tools/probe-fingerprint.py --read                    # dd `boot` first (TWRP)
    tools/probe-fingerprint.py --markers                 # what the names mean

`--expect` is the use this is for before a flash: it is the check that the payload
being written is one whose screen the reader can actually decode. It costs nothing
and it is the check that a size comparison silently passes.

`--rows` exists because `--expect` was once mistaken for an inventory of everything
a payload can print. It is not: it names instruments, and an instrument may own
many rows. Step 4.98 measured the difference - `boot-now-0923` reports `6 of 14` on
the ladder and prints 8 of the 27 rows the sources contain, and among the eight is
`P2 DIAG`, the only row in the block that names a failing status in words.

Exit status is 0 when every named instrument is resolved and every `--expect` holds;
1 when an `--expect` fails, when an image cannot be walked, or when a marker no
longer resolves in the source.
"""

import argparse
import hashlib
import importlib.util
import io
import os
import contextlib
import re
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DXE = os.path.join(ROOT, "work", "uefi", "Mu-Silicium", "Mu_Basecore",
                   "MdeModulePkg", "Core", "Dxe")
DISPATCHER = os.path.join(DXE, "Dispatcher", "Dispatcher.c")
PAGE = os.path.join(DXE, "Mem", "Page.c")
BY_NAME = "/dev/block/by-name/boot"
READ_SIZE = 4 << 20


def load_sibling(name, filename):
    """Import a tool from this directory - `tools/` is not a package."""
    path = os.path.join(ROOT, "tools", filename)
    if not os.path.isfile(path):
        sys.exit(f"probe-fingerprint: {filename} is not beside this tool")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# An instrument is (name, source file, the function that owns the lines, a token
# that picks them out of that function, what it lets a reader see). Every part is
# resolved against the sources at run time, so renaming a function or editing a
# format string breaks this loudly instead of quietly reporting the wrong thing.
#
# The source column is why `P2 FW` is a different kind of entry from the other
# thirteen: they are Dispatcher.c's, and `P2FreeWhy` is Mem/Page.c's,
# because the record it prints has to be taken where the free map is - inside
# FindFreePages, at the moment an allocation becomes terminal - and that state
# cannot be recovered from anywhere else.
INSTRUMENTS = [
    ("P2FreeWhy", PAGE, "P2FreeWhyReport", "P2 FW",
     "the free map at the moment a page allocation became terminal: the request, "
     "the largest run the search would have accepted, and how much of the map is "
     "still conventional"),
    ("P2NoLoad", DISPATCHER, "CoreDisplayDiscoveredNotDispatched", "P2 NOLOAD ",
     "the drivers that were discovered and never dispatched because their depex "
     "was false - six rows and a total, and the only rows printed before the digest"),
    ("P2Digest", DISPATCHER, "P2Digest", "P2 FREE largest=",
     "the per-record census: P2 DIAG, P2 ERR, P2 WALK, and the largest allocation"),
    ("P2Apri",   DISPATCHER, "P2Digest", "P2 APRI",
     "what the Apriori file read as, and which entries matched nothing"),
    ("P2Seq",    DISPATCHER, "P2Digest", "P2 SEQ [",
     "the batch as one character per entry, in dispatch order"),
    ("P2Why",    DISPATCHER, "P2Digest", "P2 WHY [",
     "the same positions as status classes - the row that has never been read"),
    ("P2ErrRow", DISPATCHER, "P2Digest", "P2 ERR ",
     "the failures grouped by status and counted, in words - the readable spelling"),
    ("P2Diag",   DISPATCHER, "P2Digest", "P2 DIAG %c",
     "one row per failure, carrying the driver's GUID and the status in words - "
     "the per-driver half of P2ErrRow's grouping"),
    ("P2Stats",  DISPATCHER, "P2Digest", "P2 STATS ",
     "the six counts in one row: discovered, apriori promoted of the array, started, "
     "diag and noload"),
    ("P2Walk",   DISPATCHER, "P2Digest", "P2 WALK t=",
     "one row per file type: how many were seen, how many iterations, which last"),
    ("P2Bins",   DISPATCHER, "P2Bins",   "P2 BIN init=",
     "the runtime bins' windows and the memory type information HOB"),
    ("P2Retry",  DISPATCHER, "P2Bins",   "P2 RETRY",
     "the six re-issued allocations, bs9= and bs16= among them"),
    ("P2Key",    DISPATCHER, "P2Key",    "KEY ",
     "the one-line reading, printed last - the bottom row of the panel"),
    ("P2Tick",   DISPATCHER, "P2Tick",   "K %d %c%c",
     "one row per attempted dispatch, so a run that stops inside the loop says where"),
]

# The rungs above were ten until step 4.98, and the four that were added then are
# the four whose absence caused that step's error. They are a different kind of
# entry from the other ten: `P2 DIAG`, `P2 STATS` and `P2 WALK` are *rows of
# P2Digest*, not functions, and they were skipped because the ladder was read as an
# inventory of what a payload can print. It is not - it is a list of instruments,
# and `P2Digest` deliberately names only one of its own rows (`P2 FREE largest=`)
# because a function is the unit a build either has or has not. The consequence was
# measured: `boot-now-0923` was reported "2 of 10" and read as a payload with almost
# nothing to say, when its `DxeCore` carries eight `P2 ` rows - and the row one line
# below the `SEQ` string it has been transcribed for is `P2 DIAG L <guid> <status>`,
# the only row anywhere on this panel that names a failing status in words. `--rows`
# below is the mode that answers the question the ladder was mistaken for.
#
# `P2 NOLOAD` is a rung and not a `P2Digest` row: it is
# `CoreDisplayDiscoveredNotDispatched`'s, printed at `:2512` and `:2522`, *before*
# the digest's first call at `:2552`. That order matters to a reader - the digest is
# repeated forty-one times so that the panel ends up holding nothing but copies of
# it, and the six NOLOAD rows are printed once and then wiped.

# The ladder, in the order the rows appear on the panel. The three payloads of record
# were built before `P2FreeWhy` existed, so its column is 'no' for all three. It is
# built now, in `work/out/p2-freewhy` and `work/out/p2-freewhy-g`, which are the first
# two rungs of the ladder to carry all ten of the original instruments - and the first
# two that need the head markers in `resolve_markers()`, since the second build's
# `P2 FWHY` literal is not the first's. `P2 FW` is what a build has to carry before the
# free map at the failure is worth a flash: see docs/08-device-session.md, step 4.18
# onward. The four rungs added above in step 4.98 predate all three payloads of record,
# so they widen what those three report without changing which of them is complete.


def literal_unescape(raw):
    """A C string literal body as the bytes the compiler emits.

    Only the escapes this file actually uses, applied in the order a compiler
    would. `unicode_escape` is deliberately not used: it re-encodes non-ASCII and
    would silently rewrite a byte the search then fails to find.
    """
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            table = {"n": 10, "r": 13, "t": 9, "0": 0, '"': 34, "\\": 92}
            if nxt in table:
                out.append(table[nxt])
                i += 2
                continue
        out.append(ord(raw[i]))
        i += 1
    return bytes(out)


def function_body(text, name):
    """The body of `STATIC VOID name (` - the same brace walk console-budget uses."""
    m = re.search(r"\n" + re.escape(name) + r"\s*\(\s*\n", text)
    if not m:
        return None
    start = text.index("{", m.end())
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def debug_literals(body):
    """Every DEBUG( ... ) format literal in a body, as bytes, in source order."""
    out = []
    for m in re.finditer(r"DEBUG\s*\(\s*\(", body):
        depth, i, j = 1, m.end(), m.end()
        while j < len(body) and depth:
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
            j += 1
        lm = re.search(r'"((?:[^"\\]|\\.)*)"', body[i:j])
        if lm:
            out.append(literal_unescape(lm.group(1)))
    return out


def resolve_markers():
    """[(name, source, function, [marker bytes], note)] - or exit, naming what broke.

    An instrument is a *group* of format strings, and it counts as present only
    when all of them are in the image. That is not a convenience: a `DEBUG` call
    compiles its literal unconditionally on this platform - `PcdDebugPrintErrorLevel`
    is inert here, so the level is tested at run time by `DebugPrintLevelEnabled`
    and never at build time - which means every line in a function body is in the
    image together or the function is not. Requiring all of them is therefore the
    stronger test and the honest one: `P2 APRI` alone is six literals, an if/else
    on each of three counts, and finding one of the six says nothing about whether
    the census is there.

    Each instrument names its own source file, and a source is read once however
    many instruments live in it. That is the whole reason this is a loop over
    sources rather than the one `open` it used to be: `P2FreeWhy` is Mem/Page.c's,
    and a tool that resolved every marker out of the Dispatcher would report it
    absent from an image that has it - the same failure mode as the hand-typed
    format string this docstring's caller records.
    """
    texts = {}
    for path in {src for _n, src, _f, _t, _no in INSTRUMENTS}:
        try:
            texts[path] = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            sys.exit(f"probe-fingerprint: cannot read"
                     f" {os.path.relpath(path, ROOT)}: {exc}")

    resolved = []
    for name, src, func, token, note in INSTRUMENTS:
        body = function_body(texts[src], func)
        if body is None:
            sys.exit(f"probe-fingerprint: {func}() is no longer defined in"
                     f" {os.path.relpath(src, ROOT)} - the instrument list"
                     f" needs revisiting, not patching")
        token_b = token.encode()
        hits = [lit for lit in debug_literals(body) if token_b in lit]
        if not hits:
            sys.exit(f"probe-fingerprint: no line in {func}() contains {token!r}."
                     f" The source moved; the marker is stale, and a stale marker"
                     f" reports an instrument absent that is present.")
        # The head of each literal - everything before its first conversion
        # specifier - is a second, weaker marker, kept for the case that makes
        # this tool worth having. A format string gets edited in this phase about
        # as often as anything else does, and every such edit orphans the literal
        # the older payloads were built with: `P2 FWHY ... c=%d` became
        # `... c=%d g=%d` in step 4.42, after which an exact-literal-only match
        # reported the *previous* build - which does carry the instrument - as
        # ABSENT. That is this module's own warned-about failure mode arriving
        # through the marker instead of through the search.
        #
        # A head is only kept when it is strictly longer than the token, so a
        # head match always says more than a token match would. Without that
        # guard the fallback invents instruments: `P2Tick`'s literal is
        # `K %d %c%c %d/%d free=%d %g` and its head is the two bytes `K `, which
        # match some body in almost any image - measured, it made `p2-4.20` and
        # its readback report `P2Tick` present, in a build that has no `P2Tick`
        # at all. Where the guard rejects the head, the literal itself is used,
        # which leaves that instrument exact-only and is the honest answer.
        heads = []
        for lit in hits:
            head = lit.split(b"%", 1)[0]
            heads.append(head if len(head) > len(token_b) else lit)
        resolved.append((name, src, func, hits, heads, note))
    return resolved


def all_functions(text):
    r"""[(name, body_start, body_end, line)] for every function defined in `text`.

    `function_body()` walks to the body of one named function; this walks to the
    body of every one, so a literal can be attributed to its owner without the
    owner being named in advance. That difference is the whole point: the rung
    list above is hand-written and was wrong about which rows exist, and a census
    that has to be told the function names repeats the mistake it is here to
    catch.

    `line` is the line of the opening brace, which is the number a reader gets
    from `grep -n '^\s*DEBUG'` on the literal's own line only after adding the
    newlines between. Pointing it at the `Name (` line instead would be off by
    the two or three lines of the parameter list, which is exactly the kind of
    citation drift step 4.98 had to correct by hand.

    The guard that keeps a multi-line *call* from being taken for a definition is
    the semicolon: a definition's parameter list is followed by `)` and then `{`,
    a call's by `)` and then `;`. Without it `Foo (\n  arg\n  );` matches the same
    regex as `Foo (\n  VOID\n  )` and the brace that follows belongs to whatever
    is defined next.
    """
    out = []
    for m in re.finditer(r"\n([A-Za-z_]\w*)[ \t]*\([ \t]*\n", text):
        j = text.index(")", m.end() - 1)
        head = text[j + 1:]
        k = head.find("{")
        if k < 0 or ";" in head[:k]:
            continue
        start = j + 1 + k
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), start, i + 1,
                                text.count("\n", 0, start) + 1))
                    break
    return out


def source_rows():
    """[(owner, line, literal)] - every P2BRINGUP row the sources can print.

    A row is a `DEBUG` format string that begins `P2 `, `K ` or `KEY `. That is
    read off the sources rather than listed here for the reason the rungs are:
    a hand-typed list of rows is a list of the rows someone remembered. The
    prefixes cover all three spellings in this block - the digest's `P2 ...`
    rows, `P2Tick`'s one-line `K %d ...`, and `P2Key`'s `KEY ...`.

    The literals are taken with their offsets inside the enclosing function so
    the row can be reported against a line number in the file a reader can open,
    which is the whole reason this walk does not reuse `debug_literals()`.
    """
    prefixes = (b"P2 ", b"K ", b"KEY ")
    out = []
    for path in (DISPATCHER, PAGE):
        text = open(path, encoding="utf-8", errors="replace").read()
        for owner, start, end, fline in all_functions(text):
            body = text[start:end]
            for m in re.finditer(r"DEBUG\s*\(\s*\(", body):
                depth, i, j = 1, m.end(), m.end()
                while j < len(body) and depth:
                    if body[j] == "(":
                        depth += 1
                    elif body[j] == ")":
                        depth -= 1
                    j += 1
                lm = re.search(r'"((?:[^"\\]|\\.)*)"', body[i:j])
                if not lm:
                    continue
                lit = literal_unescape(lm.group(1))
                if lit.startswith(prefixes):
                    line = fline + body.count("\n", 0, i + lm.start())
                    out.append((owner, line, lit))
    return out


def rows_in(img):
    """[(owner, line, literal, state, where)] - one entry per source row.

    `state` is `present`, `older` (the row is in the image under a different
    spelling of the same leading text - the image predates an edit to the format
    string) or `ABSENT`. The distinction is the one the rungs already draw, and it
    matters more here: a row that is `older` is a row the screen will show in a
    shape the transcriptions in docs/08 were not written against.
    """
    files, offs, inner, err = walk_fv(img)
    if err:
        return None, err
    out = []
    for owner, line, lit in source_rows():
        nm = find_literal(files, offs, inner, [lit])
        if nm:
            out.append((owner, line, lit, "present", nm))
            continue
        head = lit.split(b"%", 1)[0]
        nm = find_literal(files, offs, inner, [head]) if len(head) >= 4 else None
        out.append((owner, line, lit, "older" if nm else "ABSENT", nm or "-"))
    return out, None


def walk_fv(img):
    """(files, offs, inner, err) - the FFS files and the decompressed volume.

    The walk is `fv-inventory`'s, not a second copy of it: the descent from the
    Android boot image through BootShim, FVMAIN_COMPACT and the LZMA GUIDed
    section has padding rules in it, and a second implementation of those rules is
    a second chance to get them wrong. Every driver name and every format string
    lives inside that compressed stream, which is why grepping the `.img` finds
    nothing and why the walk is not optional.
    """
    fvi = load_sibling("fvi", "fv-inventory.py")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            files, fv_len, offs, inner = fvi.unpack(img)
    except SystemExit as exc:
        # fv-inventory exits with its own message, which already begins with the
        # path - so it is passed through rather than prefixed a second time.
        return None, None, None, str(exc)
    except Exception as exc:                                   # noqa: BLE001
        return None, None, None, f"{os.path.basename(img)}: not walkable ({exc})"
    if not inner:
        return None, None, None, (f"{os.path.basename(img)}: the walk found no"
                                  f" decompressible FVMAIN - nothing to fingerprint")
    return files, offs, inner, None


def find_literal(files, offs, inner, lits):
    """The name of the first FFS file whose body contains every literal, or None."""
    for (g, _t, s, nm, _st), o in zip(files, offs):
        body = inner[o:o + s]
        if all(lit in body for lit in lits):
            return nm or g
    return None


def instruments_in(img, markers, verbose=True):
    """(present set, absent set, the FFS file each marker was found in)."""
    files, offs, inner, err = walk_fv(img)
    if err:
        return None, None, None, err

    where, found = {}, set()
    for name, _src, _func, marker, heads, _note in markers:
        # Exact first, then the heads: see resolve_markers() for why there are
        # two, and note that a head match is *reported*, not silently accepted -
        # "this image has the instrument in an older spelling" is a different
        # statement from "present", and the difference is the whole reason the
        # `g=` field exists.
        for exact, tag in ((marker, ""), (heads, ", head-matched")):
            nm = find_literal(files, offs, inner, exact)
            if nm:
                found.add(name)
                plural = "s" if len(exact) > 1 else ""
                where[name] = f"{nm} ({len(exact)} line{plural}{tag})"
                break
    missing = {n for n, _s, _f, _m, _h, _n2 in markers} - found
    return found, missing, where, None


def dd_read(size, out):
    """A readback of `boot`, by the rules identify-boot.py learned the hard way.

    Duplicated in three lines rather than imported because the important part is
    not the loop: `status=none` *and* the per-chunk truncation, because TWRP's
    toybox 0.8.4 prints dd's statistics to stdout where `adb exec-out` picks them
    up, and that shift of 80 bytes once certified a readback as an image it was
    not. If this ever grows a third caller the loop should move into
    identify-boot.py; at two, the comment is cheaper than the indirection.
    """
    chunk = 1 << 20
    got = 0
    with open(out, "wb") as fh:
        while got < size:
            r = subprocess.run(
                ["adb", "exec-out", "dd", f"if={BY_NAME}", f"bs={chunk}",
                 f"skip={got // chunk}", "count=1", "status=none"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if r.returncode != 0 or not r.stdout:
                print(f"  stopped at {got:,} bytes (rc={r.returncode})",
                      file=sys.stderr)
                break
            data = r.stdout[:chunk]
            fh.write(data)
            got += len(data)
    return got


def rows_main(images):
    """The row census: what the sources print, and which of it each image can.

    This is the mode that answers the question the ladder is not. The ladder
    asks "does the image carry this instrument", fourteen times; a reader who
    wants to know what a screen will show has to ask "which of the rows the
    source prints are in this image", and the two questions came apart in step
    4.98 with a cost of several sessions: `P2 DIAG` is a row of `P2Digest`, it
    was not a rung, and the rung report "2 of 10" was read as an inventory of the
    payload's vocabulary when it is only a count of instruments.
    """
    rows = source_rows()
    print(f"the sources print {len(rows)} P2 rows, "
          f"{len({o for o, _l, _x in rows})} owners:")
    for owner in dict.fromkeys(o for o, _l, _x in rows):
        print(f"  {owner}()  {sum(1 for o, _l, _x in rows if o == owner)} rows")
    bad = 0
    for img in images:
        if not os.path.isfile(img):
            print(f"\n{img}: no such file")
            bad = 1
            continue
        raw = open(img, "rb").read()
        rel = os.path.relpath(img, ROOT) if img.startswith(ROOT) else img
        print(f"\n{rel}")
        print(f"  {len(raw):,} bytes   sha256 {hashlib.sha256(raw).hexdigest()}")
        census, err = rows_in(img)
        if err:
            print(f"  !! {err}")
            bad = 1
            continue
        have = [r for r in census if r[3] != "ABSENT"]
        for owner, line, lit, state, nm in census:
            tag = {"present": "  present", "older": "  OLDER  ",
                   "ABSENT": "  ABSENT "}[state]
            print(f"  {tag} {owner}():{line:<5} "
                  f"{lit.decode('latin-1').splitlines()[0][:52]!r}")
        print(f"  -> prints {len(have)} of the {len(census)} rows the source has; "
              f"{sum(1 for r in have if r[3] == 'present')} in the spelling the "
              f"source now uses")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("images", nargs="*", help="payload .img, or a readback of `boot`")
    ap.add_argument("--expect", action="append", default=[], metavar="NAME",
                    help="exit 1 unless this instrument is in every image (repeatable)")
    ap.add_argument("--read", action="store_true",
                    help="dd `boot` off the phone first (TWRP, adbd as root)")
    ap.add_argument("--device", default=BY_NAME)
    ap.add_argument("--size", type=lambda s: int(s, 0), default=READ_SIZE)
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "out",
                                                  "boot-readback.bin"))
    ap.add_argument("--markers", action="store_true",
                    help="print the format string each name resolves to and stop")
    ap.add_argument("--rows", action="store_true",
                    help="census every P2 row in the sources against every image, "
                         "rather than the fourteen named instruments")
    args = ap.parse_args()

    markers = resolve_markers()

    if args.markers:
        print("markers read from the sources under"
              f" {os.path.relpath(DXE, ROOT)}\n")
        for name, src, func, marker, _heads, note in markers:
            print(f"  {name:10s} {os.path.relpath(src, DXE)}  {func}()")
            print(f"  {'':10s} {len(marker)} line"
                  f"{'s' if len(marker) > 1 else ' '}  {marker[0]!r}"
                  f"{f' (+{len(marker) - 1} more)' if len(marker) > 1 else ''}")
            print(f"  {'':10s} {note}")
        return 0

    images = list(args.images)
    if args.read:
        print(f"reading up to {args.size:,} bytes from {args.device} ...")
        n = dd_read(args.size, args.out)
        print(f"  -> {args.out} ({n:,} bytes)")
        if n < 2048:
            sys.exit("probe-fingerprint: the read came back empty - the phone is"
                     " not there, or adbd is not up")
        images.append(args.out)

    if not images:
        sys.exit("probe-fingerprint: give an image, or --read to take one off the"
                 " phone. `--markers` prints what the names mean.")

    if args.rows:
        return rows_main(images)

    names = [n for n, _s, _f, _m, _h, _n in markers]
    bad = 0
    for img in images:
        if not os.path.isfile(img):
            print(f"{img}: no such file")
            bad = 1
            continue
        raw = open(img, "rb").read()
        rel = os.path.relpath(img, ROOT) if img.startswith(ROOT) else img
        print(f"\n{rel}")
        print(f"  {len(raw):,} bytes   sha256 {hashlib.sha256(raw).hexdigest()}")
        found, missing, where, err = instruments_in(img, markers)
        if err:
            print(f"  !! {err}")
            bad = 1
            continue
        for name in names:
            if name in found:
                print(f"  {name:10s} present   in {where[name]}")
            else:
                print(f"  {name:10s} ABSENT")
        absent = [n for n in names if n in missing]
        if absent:
            print(f"  -> carries {len(found)}/{len(names)}; missing: {', '.join(absent)}")
        else:
            print(f"  -> the full ladder: every instrument is in this image")

    if args.expect:
        print()
        for img in images:
            if not os.path.isfile(img):
                continue
            found, missing, _w, err = instruments_in(img, markers)
            for want in args.expect:
                if want not in names:
                    sys.exit(f"probe-fingerprint: --expect {want} is not a known"
                             f" instrument. Known: {', '.join(names)}")
                ok = (not err) and want in found
                print(f"  {'ok  ' if ok else 'FAIL'}  --expect {want}"
                      f"  {os.path.basename(img)}")
                if not ok:
                    bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main())
