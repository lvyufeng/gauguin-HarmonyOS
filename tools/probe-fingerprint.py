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

The three images are a ladder, and the ladder is the point:

    P2 FREE (digest)   P2 SEQ   P2 WHY   P2 ERR   P2 APRI   P2 BIN   P2 RETRY   P2 KEY   P2 TICK   P2 FW
    -----------------  ------   ------   ------   -------   ------   --------   ------   -------   -----
    boot-now-0923        yes      yes      no       no        no       no         no       no       no
    p2-4.20              yes      yes     yes      yes       yes      yes        yes       no       no
    p2-variants          yes      yes     yes      yes       yes      yes        yes      yes       no

**`P2 WHY` and `P2 ERR` are why this ladder has nine rungs and not six, and they are
the two rows that decide whether a reading is even possible.** `boot-now-0923`
prints a `P2 SEQ` line and *nothing else in that block*: no `WHY` beside it and no
`ERR` below it, because `P2WhyLetter`/`P2MarkSeq` and the grouped `P2 ERR %r x%d`
were added together, after that payload was built. `P2 SEQ` has been read off this
panel three times and `P2 WHY` has been read zero times, which is exactly what a
ladder whose oldest rung prints SEQ alone predicts. What that costs is the whole
reading: `SEQ` is a string of `s` and `L`, and `L` means only *the load failed* - it
names no status. `WHY` is the same positions with the status class in each one, and
`P2 ERR` is those statuses again **grouped and counted**, which makes one short line
- `P2 ERR Out of Resources x27`, or three lines if the causes differ - the complete
answer to "did the batch fail for one reason or twenty-seven". So:

    **A panel showing `P2 SEQ` with no `P2 ERR` anywhere on it is running
    `boot-now-0923`, and no amount of reading that screen can produce the status.**
    `--expect P2ErrRow` is the gate that makes the next flash worth making.

**`P2 FW` is the tenth rung and the only one that is not Dispatcher.c's.** Nine of the
ten instruments print from the digest, which runs *after* the batch, so every one of
them describes the heap the run left behind. The question they were built to answer -
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
(`Dispatcher.c:2376`, `:2382`, with the comment at `:2378` saying so), and `P2Key`
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
`P2Key`, the real format is `err=%r at=%d` (`:271`), and the tool therefore
reported `P2Key` absent from an image that has it. A tool whose job is to tell
which instrument is in an image cannot invent its own fingerprints; it reads them,
and it fails loudly if the function it names no longer contains the line it
expects.

Usage:
    tools/probe-fingerprint.py IMG...                    # the ladder, per image
    tools/probe-fingerprint.py --expect P2Key IMG        # pre-flight gate, exit 1
    tools/probe-fingerprint.py --read                    # dd `boot` first (TWRP)
    tools/probe-fingerprint.py --markers                 # what the names mean

`--expect` is the use this is for before a flash: it is the check that the payload
being written is one whose screen the reader can actually decode. It costs nothing
and it is the check that a size comparison silently passes.

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
# The source column is why `P2 FW` is a different kind of entry from the other nine:
# nine of the ten instruments are Dispatcher.c's, and `P2FreeWhy` is Mem/Page.c's,
# because the record it prints has to be taken where the free map is - inside
# FindFreePages, at the moment an allocation becomes terminal - and that state
# cannot be recovered from anywhere else.
INSTRUMENTS = [
    ("P2FreeWhy", PAGE, "P2FreeWhyReport", "P2 FW",
     "the free map at the moment a page allocation became terminal: the request, "
     "the largest run the search would have accepted, and how much of the map is "
     "still conventional"),
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
    ("P2Bins",   DISPATCHER, "P2Bins",   "P2 BIN init=",
     "the runtime bins' windows and the memory type information HOB"),
    ("P2Retry",  DISPATCHER, "P2Bins",   "P2 RETRY",
     "the six re-issued allocations, bs9= and bs16= among them"),
    ("P2Key",    DISPATCHER, "P2Key",    "KEY ",
     "the one-line reading, printed last - the bottom row of the panel"),
    ("P2Tick",   DISPATCHER, "P2Tick",   "K %d %c%c",
     "one row per attempted dispatch, so a run that stops inside the loop says where"),
]

# The ladder, in the order the rows appear on the panel. The three payloads of record
# were built before `P2FreeWhy` existed, so its column is 'no' for all three. It is
# built now, in `work/out/p2-freewhy` and `work/out/p2-freewhy-g`, which are the first
# two rungs of the ladder to carry all ten - and the first two that need the head
# markers in `resolve_markers()`, since the second build's `P2 FWHY` literal is not the
# first's. `P2 FW` is what a build has to carry before the free map at the failure is
# worth a flash: see docs/08-device-session.md, step 4.18 onward.


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


def instruments_in(img, markers, verbose=True):
    """(present set, absent set, the FFS file each marker was found in).

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

    where, found = {}, set()
    for name, _src, _func, marker, heads, _note in markers:
        # Exact first, then the heads: see resolve_markers() for why there are
        # two, and note that a head match is *reported*, not silently accepted -
        # "this image has the instrument in an older spelling" is a different
        # statement from "present", and the difference is the whole reason the
        # `g=` field exists.
        for exact, tag in ((marker, ""), (heads, ", head-matched")):
            for (g, _t, s, nm, _st), o in zip(files, offs):
                body = inner[o:o + s]
                if all(lit in body for lit in exact):
                    found.add(name)
                    plural = "s" if len(exact) > 1 else ""
                    where[name] = f"{nm or g} ({len(exact)} line{plural}{tag})"
                    break
            else:
                continue
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
