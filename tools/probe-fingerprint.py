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

    P2 FREE (digest)   P2 APRI   P2 BIN   P2 RETRY   P2 KEY   P2 TICK   <- instrument
    -----------------  -------   ------   --------   ------   -------   ----------
    boot-now-0923        yes       no       no        no       no        no
    p2-4.20              yes      yes      yes       yes       no        no
    p2-variants          yes      yes      yes       yes      yes       yes

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

So the markers are read out of `Dispatcher.c` rather than typed here. That is not
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
    tools/probe-fingerprint.py --list                    # what the names mean

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
DISPATCHER = os.path.join(
    ROOT, "work", "uefi", "Mu-Silicium", "Mu_Basecore",
    "MdeModulePkg", "Core", "Dxe", "Dispatcher", "Dispatcher.c")
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


# An instrument is (the function that owns the line, a token that picks it out of
# that function). Both halves are resolved against `Dispatcher.c` at run time, so
# renaming a function or editing a format string breaks this loudly instead of
# quietly reporting the wrong thing. `note` is what the instrument lets a reader
# see, which is the reason anyone cares whether it is present.
INSTRUMENTS = [
    ("P2Digest", "P2Digest", "P2 FREE largest=",
     "the per-record census: P2 DIAG, P2 ERR, P2 WALK, and the largest allocation"),
    ("P2Apri",   "P2Digest", "P2 APRI",
     "what the Apriori file read as, and which entries matched nothing"),
    ("P2Bins",   "P2Bins",   "P2 BIN init=",
     "the runtime bins' windows and the memory type information HOB"),
    ("P2Retry",  "P2Bins",   "P2 RETRY",
     "the six re-issued allocations, bs9= and bs16= among them"),
    ("P2Key",    "P2Key",    "KEY ",
     "the one-line reading, printed last - the bottom row of the panel"),
    ("P2Tick",   "P2Tick",   "K %d %c%c",
     "one row per attempted dispatch, so a run that stops inside the loop says where"),
]


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
    """[(name, function, [marker bytes], note)] - or exit, naming what broke.

    An instrument is a *group* of format strings, and it counts as present only
    when all of them are in the image. That is not a convenience: a `DEBUG` call
    compiles its literal unconditionally on this platform - `PcdDebugPrintErrorLevel`
    is inert here, so the level is tested at run time by `DebugPrintLevelEnabled`
    and never at build time - which means every line in a function body is in the
    image together or the function is not. Requiring all of them is therefore the
    stronger test and the honest one: `P2 APRI` alone is six literals, an if/else
    on each of three counts, and finding one of the six says nothing about whether
    the census is there.
    """
    try:
        text = open(DISPATCHER, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        sys.exit(f"probe-fingerprint: cannot read the Dispatcher: {exc}")

    resolved = []
    for name, func, token, note in INSTRUMENTS:
        body = function_body(text, func)
        if body is None:
            sys.exit(f"probe-fingerprint: {func}() is no longer defined in"
                     f" {os.path.relpath(DISPATCHER, ROOT)} - the instrument list"
                     f" needs revisiting, not patching")
        token_b = token.encode()
        hits = [lit for lit in debug_literals(body) if token_b in lit]
        if not hits:
            sys.exit(f"probe-fingerprint: no line in {func}() contains {token!r}."
                     f" The source moved; the marker is stale, and a stale marker"
                     f" reports an instrument absent that is present.")
        resolved.append((name, func, hits, note))
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
    for name, _func, marker, _note in markers:
        for (g, _t, s, nm, _st), o in zip(files, offs):
            body = inner[o:o + s]
            if all(lit in body for lit in marker):
                found.add(name)
                where[name] = f"{nm or g} ({len(marker)} line{'s' if len(marker) > 1 else ''})"
                break
    missing = {n for n, _f, _m, _n2 in markers} - found
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
        print(f"markers read from {os.path.relpath(DISPATCHER, ROOT)}\n")
        for name, func, marker, note in markers:
            print(f"  {name:9s} {func}()  {len(marker)} line"
                  f"{'s' if len(marker) > 1 else ' '}  {marker[0]!r}"
                  f"{f' (+{len(marker) - 1} more)' if len(marker) > 1 else ''}")
            print(f"  {'':9s} {note}")
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

    names = [n for n, _f, _m, _n in markers]
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
                print(f"  {name:9s} present   in {where[name]}")
            else:
                print(f"  {name:9s} ABSENT")
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
