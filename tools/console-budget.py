#!/usr/bin/env python3
"""How many rows the P2 digest costs the framebuffer console, and what is left.

`FrameBufferSerialPortLib` is the only console this payload has - `SiliciumPkg.dsc.inc`
maps `SerialPortLib` to it for DEBUG builds - so a reading of the probe is a reading
of one fixed-size screen. Two things about that screen decide whether a line can be
read at all, and neither was known when the digest was written:

  * **It wraps.** `WriteFrameBuffer` increments `XPos` per character and calls
    `AdvanceNewLine` when `XPos >= MaxPosition.XPos`, which puts `XPos` back to 0 and
    adds a row. So a line wider than the screen is not truncated and not lost - it is
    a line that costs two rows. That is the opposite of the guess this tool was
    written to check, and it is why the check is a read of the source and not a
    recollection of it.

  * **It wipes rather than scrolls.** `AdvanceNewLine`, on finding
    `YPos >= MaxPosition.YPos`, calls `ZeroMem` on the entire framebuffer and restarts
    at (0, 0). There is no scrollback: when the cursor passes the last row the whole
    screen goes blank and refills from the top. So the panel holds the *last* rows of
    output, never the first, and the digest is repeated 41 times precisely so that the
    steady state is "all digest" rather than "a race against the wipe".

Geometry, all from sources rather than from this docstring:

    columns = FB_WIDTH  / ((FONT_WIDTH  + 1) * FontScale)
    rows    = FB_HEIGHT / ((FONT_HEIGHT - 4) * FontScale)
    FontScale = (ShorterDimension < 426) ? 1 : ShorterDimension / 426

`FB_WIDTH`/`FB_HEIGHT` come from the platform DSC's PCDs, `FONT_WIDTH`/`FONT_HEIGHT`
from `Font.h`, and the 426 from the C source - the tool reads each rather than
assuming it, so a panel change or a font change moves these numbers.

Then it takes the DEBUG lines out of `P2Retry`, `P2Bins` and `P2Digest` - the three
functions that emit the digest - renders each one at its widest, and reports the row
count against the screen. That answers the question a reader actually has: which rows
of the panel carry the digest, and is the bottom line the one worth reading.

    tools/console-budget.py [--mu DIR] [--platform DSC] [--case observed|worst]

`observed` is this device's run (46 promoted, 27 failures, one status among them);
`worst` is the storage caps. Exit status is 0 whatever it finds.
"""

import argparse
import io
import os
import re
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_MU = os.path.join(REPO, "work", "uefi", "Mu-Silicium")
FB_C = "Silicon/Silicium/SiliciumPkg/Library/FrameBufferSerialPortLib/FrameBufferSerialPortLib.c"
FONT_H = "Silicon/Silicium/SiliciumPkg/Library/FrameBufferSerialPortLib/Font.h"
DISPATCHER_C = "Mu_Basecore/MdeModulePkg/Core/Dxe/Dispatcher/Dispatcher.c"

# The longest names EDK2's `%r` can print, and the two shortest. A rendered width
# is only a bound if the replacement used is stated, so these are the numbers the
# tables are computed against rather than a single guess.
STATUS_WIDEST = 18  # "Security Violation"
STATUS_SUCCESS = 7  # "Success"
STATUS_TYPICAL = 15  # "Out of Resources"
GUID_WIDTH = 36
DIGITS = 5  # counts and indexes on this run: 46, 27, 19 - not ten digits
HEXDIGITS = 8  # %lx here is an address in the DXE heap, not a 64-bit value


def read(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        sys.exit(f"console-budget: cannot read {path}: {exc}")


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def function_body(text, name):
    """The body of `STATIC VOID name (`, by brace matching from its opening brace."""
    m = re.search(r"\n" + name + r"\s*\(\n", text)
    if not m:
        sys.exit(f"console-budget: no definition of {name} in the Dispatcher")
    start = text.index("{", m.end())
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    sys.exit(f"console-budget: unbalanced braces after {name}")


def debug_literals(body):
    """Every (format, args) pair in the DEBUG( ... ) calls of a function body."""
    out = []
    for m in re.finditer(r"DEBUG\s*\(\s*\(", body):
        depth = 1
        i = m.end()
        j = i
        while j < len(body) and depth:
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
            j += 1
        call = body[i : j - 1]
        lm = re.search(r'"((?:[^"\\]|\\.)*)"', call)
        if lm:
            # One line per format string, and the trailing newline is not a column
            # of it: the digest has no multi-line formats, so drop the escapes
            # rather than measuring them.
            out.append(lm.group(1).encode("utf-8").decode("unicode_escape").rstrip("\n"))
    return out


def render_width(fmt, strlen, status):
    """The column width `fmt` occupies, with each conversion at the stated width."""
    width = 0
    i = 0
    while i < len(fmt):
        if fmt[i] != "%":
            width += 1
            i += 1
            continue
        m = re.match(r"%[-+ #0]*\d*(?:\.\d+)?(?:ll|l|h)?([a-zA-Z%])", fmt[i:])
        if not m:
            width += 1
            i += 1
            continue
        spec, conv = m.group(0), m.group(1)
        if conv == "%":
            width += 1
        elif conv == "g":
            width += GUID_WIDTH
        elif conv == "r":
            width += status
        elif conv == "a":
            width += strlen
        elif conv in "diu":
            width += DIGITS
        elif conv in "xX":
            width += HEXDIGITS
        elif conv == "c":
            width += 1
        else:
            width += 1
        i += len(spec)
    return width


def group_key(fmt):
    """Which printed line this format string is a variant of.

    An if/else on the same reading prints one of two formats and costs one row,
    so the two have to be one group - and grouping by their leading words alone
    would merge lines that are genuinely different (`P2 APRI bytes=` and
    `P2 APRI first=`). What separates the two cases is the set of named fields:
    `P2 APRI matched=none unhit=%d` and `P2 APRI matched=%d..%d unhit=%d` name the
    same fields and are one line; the others do not. The lead is the first two
    words, which is the `P2 XXX` tag the digest is read by.
    """
    words = strip_comments(fmt).split()
    lead = " ".join(words[:2])
    fields = frozenset(re.findall(r"\b(\w+)=", fmt))
    return lead, fields


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mu", default=DEFAULT_MU)
    ap.add_argument("--platform", default=None)
    ap.add_argument("--case", choices=("observed", "worst"), default="observed")
    ap.add_argument("--strlen", type=int, default=46,
                    help="width to assume for %%a (the SEQ/WHY lines are 46)")
    args = ap.parse_args()

    mu = args.mu
    dsc = args.platform or os.path.join(mu, "Platforms", "Xiaomi", "gauguinPkg", "gauguin.dsc")
    fb = read(os.path.join(mu, FB_C))
    font = read(os.path.join(mu, FONT_H))
    dsc_text = read(dsc)

    print("console-budget: the panel the P2 digest is read off\n")
    for rel in (os.path.relpath(dsc, mu), FB_C, FONT_H, DISPATCHER_C):
        print(f"  reads: {rel}")
    print()

    fw = int(re.search(r"#define FONT_WIDTH\s+(\d+)", font).group(1))
    fh = int(re.search(r"#define FONT_HEIGHT\s+(\d+)", font).group(1))
    pcd = dict(re.findall(r"PcdFrameBuffer(\w+)\|(\d+)", dsc_text))
    width, height = int(pcd["Width"]), int(pcd["Height"])

    # The scale factor, from the C that computes it rather than from a photo of text.
    m = re.search(r"ShorterDimension\s*<\s*(\d+)\)\s*\?\s*1\s*:[^;]*?"
                  r"ShorterDimension\s*/\s*(\d+)", fb)
    if not m or m.group(1) != m.group(2):
        sys.exit("console-budget: cannot read the FontScale rule out of the framebuffer library")
    pivot = int(m.group(1))
    shorter = min(width, height)
    scale = 1 if shorter < pivot else shorter // pivot

    columns = width // ((fw + 1) * scale)
    rows = height // ((fh - 4) * scale)
    print(f"panel: {width}x{height}, {pcd['ColorDepth']}bpp from {os.path.basename(dsc)}")
    print(f"font: FONT_WIDTH={fw} FONT_HEIGHT={fh}, glyph {fw + 1}x{fh - 4} cells at scale {scale}")
    print(f"  FontScale = ({shorter} < {pivot}) ? 1 : {shorter} / {pivot} = {scale}")
    print(f"  columns = {width} / (({fw}+1) * {scale}) = {columns}")
    print(f"  rows    = {height} / (({fh}-4) * {scale}) = {rows}")
    print()

    # The two behaviours the row count depends on, quoted from the source so the
    # numbers above are used the way the console uses them.
    wraps = "CurrentPosition->XPos >= MaxPosition.XPos" in fb and "CurrentPosition->XPos++" in fb
    wipes = "ZeroMem ((VOID *)FbBase, FB_WIDTH * FB_HEIGHT * FB_BPP)" in fb
    print(f"  wraps at column {columns}: {'yes' if wraps else 'NO'} "
          f"(WriteFrameBuffer: XPos++ then the MaxPosition.XPos test)")
    print(f"  wipes instead of scrolling: {'yes' if wipes else 'NO'} "
          f"(AdvanceNewLine: ZeroMem of the whole framebuffer, cursor back to 0,0)")
    print(f"  -> a line longer than {columns} columns costs another row, and the panel")
    print(f"     holds the last {rows - 1} rows of output, never the first")
    print()

    body = read(os.path.join(mu, DISPATCHER_C))
    lines = []
    for name in ("P2Retry", "P2Bins", "P2Digest"):
        for fmt in debug_literals(function_body(body, name)):
            lines.append((name, fmt))

    # The counters: DIAG is one line per load failure, ERR one per *distinct*
    # status, WALK one per file type the volume walk was handed.
    diag_cap = int(re.search(r"#define P2BRINGUP_DIAG_MAX\s+(\d+)", body).group(1))
    walk_cap = int(re.search(r"mP2WalkSeen\[(\d+)\]\s*=", body).group(1)) + 1
    if args.case == "observed":
        diag, err, walk = 27, 1, 2
        label = "this device's run: 27 load failures, one status among them, 2 of 8 walk types used"
    else:
        diag, err, walk = diag_cap, 27, walk_cap
        label = f"the storage caps: diag={diag_cap}, one distinct status per failure, all {walk_cap} walk types"

    # Group the variants of one line (an if/else prints one of them) and let the
    # widest stand for the group. Each group then costs its wrap rows.
    print(f"digest lines, at the stated replacement widths (%g -> {GUID_WIDTH}, %d -> "
          f"{DIGITS}, %lx -> {HEXDIGITS}, %r -> {STATUS_WIDEST}):")
    groups = {}
    for name, fmt in lines:
        key = group_key(fmt)
        w = render_width(fmt, args.strlen, STATUS_WIDEST)
        if key not in groups or w > groups[key][0]:
            groups[key] = (w, fmt)
    per_record = ("P2 DIAG", "P2 ERR", "P2 WALK")
    fixed_rows = 0
    for (lead, _fields), (w, fmt) in groups.items():
        rows_here = 1 if w <= columns else -(-w // columns)
        if lead in per_record:
            rows_here = 0  # counted below, once per record
        fixed_rows += rows_here
        flag = "  <- wraps" if rows_here > 1 else ""
        print(f"  {w:>4} cols  {rows_here or '-':>2} row{'s' if rows_here > 1 else ' '}  "
              f"{fmt}{flag}")
    print()

    total = fixed_rows + diag + err + walk
    print(f"  fixed lines: {fixed_rows} rows   (one row per line, plus a row where it wraps)")
    print(f"  per-record:  diag={diag} err={err} walk={walk} = {diag + err + walk} rows")
    print(f"case: {label}")
    print(f"  -> one copy of the digest is about {total} rows against a {rows - 1}-row panel")
    print(f"     ({total * 2} for two copies, {total * 3} for three; the wipe is at row {rows})")
    if total >= rows - 1:
        print("     !! one copy nearly fills the panel: the reader sees one copy and a")
        print("        fraction, and its head is wiped as its tail is being printed")
    else:
        print(f"     -> {(rows - 1) // total} copies fit at once, so once the first wipe has")
        print("        passed, every populated row of the panel is a digest row - which is")
        print("        what the 41 repetitions were for. The panel is part-filled, though:")
        print("        after each wipe the rows below the cursor are blank until they fill")
    print()

    # The one line whose width is not fixed by the format. Its six statuses are
    # names of 7 to 18 columns, so the same line is one row or two depending on
    # what the run did - and a two-row one breaks inside a field.
    for name, fmt in lines:
        if "bs9=" in fmt:
            fixed = len(re.sub(r"%[-+ #0]*\d*(?:ll|l|h)?[a-zA-Z]", "", fmt))
            budget = columns - fixed
            lo = fixed + 6 * STATUS_SUCCESS
            hi = fixed + 6 * STATUS_WIDEST
            print(f"the RETRY line: {fixed} columns of fixed text, six status names sharing the")
            print(f"  remaining {budget} of each {columns} (Success {STATUS_SUCCESS}, "
                  f"Out of Resources {STATUS_TYPICAL}, Security Violation {STATUS_WIDEST})")
            print(f"  -> {lo} to {hi} columns, so {'one row' if hi <= columns else 'one or two rows'};")
            print(f"     it is one row only if the six names fit in {budget} columns, "
                  f"which is not")
            print("     the common case, and then the break falls in the middle of a field")
            print()
            break

    print("Where the RETRY line sits on the panel, and why that is the one row to read.")
    print("`P2Bins` calls `P2Retry()` to take the readings and prints its line last, so")
    print("the RETRY line is the last line of every copy, and every copy is followed by")
    print("P2Hold's pause - 2e9 volatile read-modify-writes, seconds each, 40 of them.")
    print("So the printing stops on that line and does not resume for seconds:")
    ordered = [fmt for name, fmt in lines if name == "P2Bins"]
    for n, fmt in enumerate(reversed(ordered), 1):
        print(f"  {n} from the bottom of a copy: {fmt}")
    print()
    print("  And the wipe does not move it: when the cursor crosses the last row the")
    print("  panel is cleared and the *rest of the copy in progress* prints from the top,")
    print(f"  which ends on the same line. So the last populated row of the panel is the")
    print("  RETRY line in every state, with blank rows beneath it - not a filled panel")
    print("  whose bottom row is the reading.")
    print()
    print(f"  `bs9=` is the second field from the end. If the line ran past {columns} columns it")
    print("  wrapped, and then its *tail* is the last populated row: `bs9=` and `bs16=` go")
    print("  there and the row above ends mid-field. Either way - one row or two - the last")
    print("  text on the panel carries `bs9=`. That is the row to photograph.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
