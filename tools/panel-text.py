#!/usr/bin/env python3
"""Read the bring-up console off a photograph of the panel, because the panel is the only channel.

Every `P2 …` reading this phase has produced came from a person looking at a
1080x2400 screen and typing back a row of 46 unbroken characters. That has cost
three sessions, and step 4.39 found the reason it kept costing them: the row that
*can* be transcribed and the row that *answers the question* are different rows,
and the build on the phone printed only the transcriber's row. A reader who
mis-transcribes one `L` in a run of 46 `s` and `L` has no way to know, and neither
does anyone reading the transcription afterwards.

So this tool removes the transcription. Everything it needs is in the image
already: the font is `Font.h`, the geometry is `FrameBufferSerialPortLib.c` plus
the platform's `PcdFrameBuffer*`, and the drawing rules are the C in between.
Nothing here is typed by hand - the 96 glyphs are parsed out of the header and the
cell size is computed from the same constants the firmware computes it from, so a
font change or a panel change moves the numbers instead of silently invalidating
them.

The font, decoded (this is the part that has to be right, and none of it is
visible in the header):

    `DrawGlyph` splits the 64-bit constant into TopGlyph = high dword and
    BottomGlyph = low dword. Rows 0-5 come from TopGlyph, rows 6-11 from
    BottomGlyph, five bits per row, `RowData >>= 5` between rows. `DrawRow` takes
    the *low* bit first and advances the cursor right, so within each 5-bit group
    **bit 0 is the leftmost pixel**. Checked against the glyphs that are not
    mirror-symmetric - '1', '[', ']', '/', 'B', 'b' - because '0', 'A' and 'X' read
    the same either way and cannot settle it.

    The top half is 6 rows x 5 bits = 30 bits of a 32-bit dword, so the top two
    bits of TopGlyph are unused. Read that as a fact before reading it as a bug.

Geometry, computed from the sources: panel 1080x2400 -> scale 2 -> 90 columns x
100 rows, 12x24 px per cell, of which the left 10 px carry ink (the 12th column of
each cell is the inter-character gap, and `DrawGlyph`'s stride arithmetic is what
puts it there). The console **wipes rather than scrolls** when the cursor passes
the last row, so the panel holds the last 100 rows of output and never the first.
`--render` models that faithfully, which is what makes it usable as a reference
sheet beside a photograph, and what makes `--selftest` a test rather than a round
trip through one function.

Reading a photograph, and the two things that actually make it hard:

  * **The screen is somewhere in the picture, rotated and at the wrong scale.**
    A phone photographed flat on a desk is not level and the screen is not 1080 px
    wide in the file. So the tool does not assume a grid: it estimates the cell
    size from the periodicity of the picture's own ink profile, then searches
    rotation and magnification for the transform that puts the console's lattice
    back on whole pixels - scoring each candidate by how much of the ink lands in
    the cells' ink columns and how little lands in their gaps. Only then does it
    sample, and it samples on an integer grid, because that is the difference
    between a decode that can be trusted and one that is a coin flip per character.
  * **The lens is not sharp.** Blur spreads a 2-px stroke past its own cell, so the
    tool matches each cell against the font *and* against slightly blurred versions
    of the font, and keeps whichever explains the picture best. This is not
    cosmetic: at radius 2 the sharp templates lose about half the characters.

    tools/panel-text.py --render "P2 ERR Out of Resources x27"   # reference sheet
    tools/panel-text.py --decode PHOTO.jpg                       # text back out
    tools/panel-text.py --selftest                               # does it survive
    tools/panel-text.py --fb fb.bin --fb fb2.bin                 # off the firmware's own memory

`--fb` is the same panel read through a different channel, and the channel is the
one that has no photograph in it. Dump the "Display Reserved" region of the running
firmware (`pmemsave 0x... 0x... fb.bin` in a QEMU monitor) and the console's
content comes back exactly - see `fb_ink` for what that removes. Pass the flag more
than once and the dumps are joined into one stream in the order given, which is how
the console's wipe is answered: it clears the screen rather than scrolling it, so a
boot longer than one screen is only ever readable from a series of dumps.

`--selftest` is the honest part. It renders text through the console model, places
it inside a larger black scene the way a phone appears in a photograph, applies the
degradations a hand-held photograph actually has - blur, noise, a brightness
gradient, a rotation, a scale error, an offset - and requires the decoder to get
the text back exactly. Each is applied alone and then all together, and each result
is reported, because the useful output is not "it works" but *which* degradation it
survives and which it does not.

It passes 11 of 11 as of 2026-09-24, and getting there from 0 of 11 is where five of
the fixes in this file came from - each of them a wrong reading that looked like a
right one, which is the failure mode this tool exists to remove. The scaffold itself
was one of them: it used to render the panel at one photograph pixel per panel pixel,
where a glyph stroke is two pixels wide and a blur of radius 2 deletes it before the
decoder ever runs. A real photograph of this phone has the panel at two to three
pixels per panel pixel. `make_photo`'s `scale` is that, and setting it to 2 turned
three "failures" into passes without the decoder changing at all.

Exit status is 0 on success; 1 when a decode finds characters below `--min-margin`,
when `--selftest` fails, or when a source it reads has moved.
"""

import argparse
import io
import os
import re
import sys

import numpy as np
from PIL import Image, ImageFilter

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_MU = os.path.join(REPO, "work", "uefi", "Mu-Silicium")
FB_DIR = "Silicon/Silicium/SiliciumPkg/Library/FrameBufferSerialPortLib"
FB_C = f"{FB_DIR}/FrameBufferSerialPortLib.c"
FONT_H = f"{FB_DIR}/Font.h"

GLYPH_ROWS = 12  # FONT_HEIGHT - 4, the rows DrawGlyph actually draws
# The bound on what the alignment is ever handed, and it is applied to the text
# block rather than to the photograph - see `decimate`. 3000 is also as large as
# it can safely be: 3000 px across 90 columns is a 33 px cell, and the cell
# search in `align` looks from 0.4 to 3.0 cells (`:544`), i.e. up to 36 px. Raise
# this past 3240 and the estimator stops being able to describe what it is
# looking at.
MAX_SIDE = 3000
# The frame bound, which is only about memory: `Otsu` and `lit_mask` each walk the
# whole picture before anything is cropped, and a 12 Mpx photograph is already 48
# MB per float32 array with several alive at once. 6000 px of long side is 36 Mpx
# worst case, which is as much as this is willing to materialise.
MAX_FRAME = 6000


def die(msg):
    sys.exit(f"panel-text: {msg}")


def read(path, what):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        die(f"cannot read {what} at {path}: {exc}")


# ---------------------------------------------------------------- sources


def load_geometry(mu, platform=None):
    """Everything the decoder needs to know about the screen, read from source.

    Returned as a dict with a `where` beside it rather than as arguments, so no
    caller can substitute a remembered number for a read one. When a decode comes
    out wrong the first question is which number was wrong, and the answer has to
    be a file rather than a value someone typed into this file in 2026.
    """
    dsc = platform or os.path.join(mu, "Platforms", "Xiaomi", "gauguinPkg",
                                  "gauguin.dsc")
    fb = read(os.path.join(mu, FB_C), "the framebuffer library")
    font = read(os.path.join(mu, FONT_H), "the font table")
    dsc_text = read(dsc, "the platform DSC")

    pcd = dict(re.findall(r"PcdFrameBuffer(\w+)\|(\d+)", dsc_text))
    if not {"Width", "Height", "ColorDepth"} <= set(pcd):
        die(f"{os.path.basename(dsc)} has no PcdFrameBufferWidth/Height/ColorDepth"
            f" - the platform moved, and a remembered 1080x2400 would be a guess")
    width, height, bpp = int(pcd["Width"]), int(pcd["Height"]), int(pcd["ColorDepth"])

    m = re.search(r"#define FONT_WIDTH\s+(\d+)", font)
    n = re.search(r"#define FONT_HEIGHT\s+(\d+)", font)
    if not (m and n):
        die("Font.h no longer defines FONT_WIDTH/FONT_HEIGHT")
    fw, fh = int(m.group(1)), int(n.group(1))

    s = re.search(r"ShorterDimension\s*<\s*(\d+)\)\s*\?\s*1\s*:[^;]*?"
                  r"ShorterDimension\s*/\s*(\d+)", fb)
    if not s or s.group(1) != s.group(2):
        die("cannot read the FontScale rule out of the framebuffer library")
    pivot = int(s.group(1))
    shorter = min(width, height)
    scale = 1 if shorter < pivot else shorter // pivot

    glyph_w = (fw + 1) * scale
    glyph_h = (fh - 4) * scale

    if "ZeroMem ((VOID *)FbBase, FB_WIDTH * FB_HEIGHT * FB_BPP)" not in fb:
        die("AdvanceNewLine no longer wipes the framebuffer - the console model in"
            " this tool, and the reading rule that the panel holds the *last* rows,"
            " are both built on that, so it has to be rechecked before use")

    return {
        "width": width, "height": height, "bpp": bpp,
        "font_w": fw, "font_h": fh, "scale": scale,
        "cell_w": glyph_w, "cell_h": glyph_h,
        "ink_w": fw * scale, "ink_h": glyph_h,
        "columns": width // glyph_w, "rows": height // glyph_h,
        "where": {"panel": os.path.basename(dsc),
                  "scale": "FrameBufferSerialPortLib.c", "font": "Font.h"},
    }


def load_font(mu):
    """(96, 12, 5) of 0/1 - each glyph as drawn, top row first, left first.

    Parsed out of the header rather than copied, for the same reason
    `probe-fingerprint.py` reads its markers out of `Dispatcher.c`: a tool that
    decides what the panel says cannot be carrying its own idea of the alphabet.
    """
    text = read(os.path.join(mu, FONT_H), "the font table")
    m = re.search(r"#define\s+GLYPH_FONT\s*(.*?)\n\n", text, re.S)
    if not m:
        die("Font.h no longer has a GLYPH_FONT block")
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    vals = re.findall(r"0x([0-9A-Fa-f]{16})", body)
    if len(vals) != 96:
        die(f"GLYPH_FONT has {len(vals)} constants, not 96 - the table changed shape"
            f" and ASCII 32+i no longer indexes it")

    out = np.zeros((96, GLYPH_ROWS, 5), dtype=np.uint8)
    for i, v in enumerate(vals):
        g = int(v, 16)
        top, bot = (g >> 32) & 0xFFFFFFFF, g & 0xFFFFFFFF
        for r in range(GLYPH_ROWS):
            bits = ((top if r < 6 else bot) >> (5 * (r % 6))) & 0x1F
            for c in range(5):
                out[i, r, c] = (bits >> c) & 1   # bit 0 is the leftmost pixel
    return out


# ---------------------------------------------------------------- console model


def console_render(lines, geo, font):
    """Text through the console's own rules, as ink coverage 0..1.

    `WriteFrameBuffer` transcribed: the leading-space skip, the wrap at
    `MaxPosition.XPos`, and - the one that matters for reading a panel - the
    `ZeroMem` wipe in `AdvanceNewLine`. A reference sheet that scrolled where the
    console wipes would disagree with the phone about which rows are on screen,
    which is exactly the disagreement this tool exists to settle.
    """
    fb = np.zeros((geo["height"], geo["width"]), dtype=np.float32)
    cw, ch, iw, sc = geo["cell_w"], geo["cell_h"], geo["ink_w"], geo["scale"]
    x = y = 0
    for line in lines:
        for ch_ in line + "\n":
            if ord(ch_) >= 127:
                continue
            if ord(ch_) < 32:
                if ch_ == "\n":
                    x, y, fb = _advance(y, fb, geo)
                elif ch_ == "\r":
                    x = 0
                continue
            if x == 0 and ch_ == " ":
                continue
            bits = font[ord(ch_) - 32]
            y0, x0 = y * ch, x * cw
            block = fb[y0:y0 + ch, x0:x0 + iw]
            for r in range(GLYPH_ROWS):
                for c in range(5):
                    if bits[r, c]:
                        block[r * sc:(r + 1) * sc, c * sc:(c + 1) * sc] = 1.0
            x += 1
            if x >= geo["columns"]:
                x, y, fb = _advance(y, fb, geo)
    return fb


def _advance(y, fb, geo):
    y += 1
    if y >= geo["rows"]:
        fb[:] = 0.0
        return 0, 0, fb
    return 0, y, fb


# How many rows the panel holds, for `console_rows` to model the wipe against. A
# module-level cell rather than a parameter because the one caller is the
# scaffold's reporting path, where the geometry is already in hand and threading
# it through would only make that call site noisier.
PANEL_ROWS = [100]


def console_rows(lines, columns):
    """The rows the console would hold, as strings - wrap and wipe included.

    `console_render` gives the same layout as ink, which is what the decoder is
    tested against; this gives it as *text*, which is what a reader needs. The
    distinction matters because two conventions are both plausible and only one
    is right: a decoder could return one row per line that was printed, or one
    row per row of the panel. `decode` does the second, because that is what a
    photograph contains - so the scaffold now wraps, and a row-by-row comparison
    against the input has to know which rows are continuations.

    It mirrors `console_render` rather than sharing code with it, so the two can
    drift apart. `selftest` renders this back through `console_render` and
    requires the ink to match, so a drift is reported as a scaffold fault rather
    than blamed on the decoder.
    """
    rows = [""]
    x = 0
    for line in lines:
        for ch in line + "\n":
            if ord(ch) >= 127:
                continue
            if ord(ch) < 32:
                if ch == "\n":
                    rows.append("")
                    x = 0
                elif ch == "\r":
                    x = 0
                continue
            if x == 0 and ch == " ":
                continue
            rows[-1] += ch
            x += 1
            if x >= columns:
                rows.append("")
                x = 0
    # A trailing newline leaves an empty row behind, and blank rows carry no
    # reading either way, so they are dropped rather than compared.
    rows = [r for r in rows if r]
    # The wipe: only the rows still on the glass when the cursor passed the last
    # one are in the photograph, and they are the last PANEL_ROWS of them.
    if len(rows) > PANEL_ROWS[0]:
        rows = rows[len(rows) - PANEL_ROWS[0]:]
    return rows


# ------------------------------------------------ the framebuffer, read directly


def fb_ink(path, geo):
    """Ink 0/1 from a dump of the panel's own memory - no photograph in the way.

    Ask the running firmware where its console is (`GetFrameBufferMemory` locates
    the region by name, so the answer is "Display Reserved" and, under QEMU, the
    address and length the platform's own device tree declares), dump that region
    with `pmemsave`, and what comes back is the panel's content byte for byte.
    Everything the photograph path exists to estimate is then already known:

      * **The values are 0 and 1 and nothing else.** `DrawRow` writes
        `0xFFFFFFFF` or `0x00000000` per pixel, so there is no threshold to
        choose here - an Otsu cutoff would be a way of estimating a number that is
        in the source, and a way of getting it wrong.
      * **There is no blur, so there is nothing to soften.** One template pass,
        not three.
      * **The origin is known, not searched.** `WriteFrameBuffer` places cell
        (r, c) at `(c * cell_w, r * cell_h)` from the buffer's base, the buffer's
        base is cell (0, 0), and `ClearFrameBuffer` zeroes the cursor before
        anything is printed - so `dx` and `dy` are zero, and the alignment search
        that a photograph needs is skipped rather than tuned.

    A dump may be *longer* than the drawn area: the region is `FbLength` bytes and
    the console only ever touches `FB_WIDTH * FB_HEIGHT * FB_BPP` of them (on this
    platform 0x9E3400 of 0x2400000). It may not be *shorter*, which is reported
    and not padded - a padded tail reads as a screen that happened to end, and
    that is the wrong answer to "why is the last line blank".

    `path` must therefore be a dump taken from the region's **base**. A dump from
    an offset inside it decodes as a screen whose first rows are missing, and
    nothing here can tell that apart from a console that started printing later.
    """
    raw = open(path, "rb").read()
    need = geo["width"] * geo["height"] * (geo["bpp"] // 8)
    if len(raw) < need:
        die(f"{path} is {len(raw)} bytes, and the console draws {need} "
            f"({geo['width']}x{geo['height']} at {geo['bpp']}bpp) - a short dump "
            f"cannot be told from a screen that ended")
    px = np.frombuffer(raw[:need], dtype="<u4").reshape(geo["height"], geo["width"])
    return (px != 0).astype(np.float32)


def fb_lines(path, geo, font, min_margin):
    """(lines, report, template name) for one framebuffer dump.

    The origin is pinned to (0, 0) and both search radii to zero, which is the
    whole difference from the photograph call: the two numbers the photo path
    spends its search on are read off `WriteFrameBuffer` instead.
    """
    ink = fb_ink(path, geo)
    return decode(ink, geo, font, {"dx": 0, "dy": 0}, 0, 0, min_margin)


def fb_overlap(seen, lines):
    """How many of `lines` the stream `seen` already ends with.

    The longest tail of `seen` that is also the head of `lines`. Longest, not
    first: a line can legitimately repeat - a retry loop prints the same row
    twice - and a join on the first match would splice the stream back onto a
    copy of itself and quietly delete everything between.
    """
    for cand in range(min(len(seen), len(lines)), 0, -1):
        if seen[-cand:] == lines[:cand]:
            return cand
    return 0


def fb_merge(paths, geo, font, min_margin):
    """One stream out of many dumps, because the console wipes instead of scrolling.

    One dump is one screen: the last `PANEL_ROWS` lines the firmware printed, and
    never the first, since `AdvanceNewLine` zeroes the whole buffer when the
    cursor passes the last row. Reading a boot off this channel therefore means
    sampling it, and sampling means most lines arrive in several dumps. So the
    screens are joined on content - the longest tail of what has been collected
    that is also the head of the next screen is the overlap, and everything after
    it is new.

    A screen that overlaps by *nothing* is reported, not silently appended. Two
    dumps that share no line may be adjacent (the console printed more than a
    screenful between them) or may be a screen the sampler missed the start of,
    and only the reader can tell those apart. Either way a gap in the middle of a
    boot log is a hole in the evidence, and the one thing this must not do is make
    it invisible.

    Returns (lines, screens kept, [(path, lines in it)] for the screens that
    overlapped nothing, and the weakest per-character margin seen anywhere).
    """
    out, kept, gaps, worst, weak = [], 0, [], 60.0, 0
    for p in paths:
        lines, report, _t = fb_lines(p, geo, font, min_margin)
        if not lines:
            continue
        for rep in report:
            worst = min(worst, rep["worst"])
            weak += len(rep["weak"])
        k = fb_overlap(out, lines)
        if kept and k == 0:
            gaps.append((p, len(lines)))
        out += lines[k:]
        kept += 1
    return out, kept, gaps, worst, weak


def soften(bits, passes):
    """A glyph template spread over its neighbours, in cell units.

    A blurred stroke stops being a set of whole sub-blocks and starts being a
    gradient, so a template of hard 0/1 scores worse than a template of the same
    shape already softened. `passes` counts box blurs with a 3-tap kernel in the
    5x12 sub-block grid - roughly one block of spread per pass at scale 2, which
    is what a one-pixel-radius defocus does to a 2-px stroke on this panel.
    """
    t = bits.astype(np.float32)
    lead = ((0, 0),) * (t.ndim - 2)

    def box(axis):
        widths = list(lead) + [(0, 0), (0, 0)]
        widths[-2 + axis] = (1, 1)
        p = np.pad(t, widths, mode="edge")
        if axis == 0:
            return (p[..., :-2, :] + 2.0 * t + p[..., 2:, :]) / 4.0
        return (p[..., :-2] + 2.0 * t + p[..., 2:]) / 4.0

    for _ in range(passes):
        t = box(1)
        t = box(0)
    return t


# ---------------------------------------------------------------- ink


def normalise(a):
    """Ink coverage 0..1, from the picture's own black and white.

    Otsu, not a fixed percentile, and the reason is a failure this tool had. The
    obvious choice - "the 99.5th percentile is the text" - is wrong for exactly
    the pictures that matter: a panel carrying one line of output, a build that
    died early, which is the interesting build, has about 0.1% of its pixels
    inked, so every high percentile lands in the background and the letterbox
    comes out black. Otsu splits the histogram where the two levels actually are,
    and the threshold and both levels are returned so a picture it cannot read
    says why instead of decoding into noise.

    The white level is the mean of the brightest 1% *of the pixels above Otsu's
    threshold*, and both halves of that are answers to a measured failure rather
    than a preference.

      * Not their mean. On a blurred picture most of the above-threshold pixels are
        half-lit edges rather than glyph interiors, so their mean sits at a third of
        white (measured: 0.29 on a radius-2 blur of the clean scaffold) and dividing
        by it saturates the strokes to 1.0 *and* the space between them. Every cell
        then reads as full and the text comes back as a row of `#`.

      * Not the 90th percentile of them either, which was the next attempt and is
        what this tool shipped with. On a *noisy* picture the above-threshold class
        is mostly noise, so a percentile inside it lands far below white: measured on
        the combined scaffold - blur, glare, sensor noise at sigma 0.2 and a tilt -
        Otsu split the noise at 0.131, the 90th percentile of that class was 0.472
        against a true white of 1.0, and dividing by 0.472 *inflated the noise by
        2.1x*. Background pixels then read as 0.7 of white, `lit_mask` fires across
        the whole scene, and `crop_to_ink` returns the entire photograph - after
        which every alignment score is under 0.07 because there is no lattice in a
        picture of noise.

      * The top 1% mean is the robust maximum of the class: if any real ink is above
        the threshold at all, the brightest of it is what the top of that class is
        made of. Measured across the three hardest cases - clean 1.000, blur radius
        2 at 0.813, the combined scene at 0.842 - and it decodes all three exactly.
        The tenth of a percent that is the *peak* mean is too high (0.866 on the blur
        case, which then loses the first row), so the level is deliberately the top
        percent rather than the top of the distribution.
    """
    hist, _edges = np.histogram(a, bins=256, range=(0.0, 1.0))
    p = hist.astype(np.float64) / max(1.0, float(hist.sum()))
    levels = (np.arange(256) + 0.5) / 256.0
    omega = np.cumsum(p)
    mu = np.cumsum(p * levels)
    denom = omega * (1.0 - omega)
    with np.errstate(invalid="ignore", divide="ignore"):
        between = np.where(denom > 1e-12,
                           (mu[-1] * omega - mu) ** 2 / np.maximum(denom, 1e-12), 0.0)
    thr = float(levels[int(between.argmax())])
    lo, hi = a[a <= thr], a[a > thr]
    if lo.size == 0 or hi.size == 0:
        die("the picture is one flat tone - this does not look like a screen with"
            " text on it, or the crop is off it")
    top = hi[hi >= np.percentile(hi, 99)]
    bg = float(lo.mean())
    fg = float(top.mean()) if top.size else float(hi.max())
    if fg - bg < 0.08:
        die(f"the picture has no bimodal split to read: Otsu put the threshold at"
            f" {thr:.3f} but the two sides are {bg:.3f} and {fg:.3f} - either it is"
            f" not a photograph of the panel, or the crop is off it")
    return np.clip((a - bg) / (fg - bg), 0.0, 1.0), thr, bg, fg


def decimate(a, max_side=MAX_SIDE):
    """(image, factor) - the picture with its longest side no longer than `max_side`.

    Applied to the text block rather than to the frame it was cut out of, and that
    is the point rather than a detail. The bound exists because the alignment's
    cost is linear in pixels and because no glyph needs that many; the background
    around the screen is pixels that cost the same and that nothing samples. So
    decimating the frame first spends the whole budget on the wall behind the
    phone.

    Measured on this tool's own scaffold - a 5080x2380 photograph of a block that
    is 2207x1137, tilted 2.5 degrees - decimating the frame first gave a 14.2 px
    cell and read **917 of 1064 characters wrong**: the continuation row of
    `P2 APRI first=…` came back as ``3-S3-E-J-F- | - `|`` instead of `31`.
    Decimating the block leaves the cell at 24 px, and the same photograph then
    reads **one** character wrong - the `R` the report already flags as doubtful.
    """
    k = 1.0
    if max_side and max(a.shape) > max_side:
        k = max_side / max(a.shape)
        im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                       Image.LANCZOS)
        a = np.asarray(im, dtype=np.float32) / 255.0
    return a, k


def load_photo(path, invert=False, rotate=0.0, crop=None):
    """The photograph as a float ink image, at its own scale and in its own frame.

    The only decimation here is the frame bound, which is a memory guard: the
    bound that decides how finely the *text* is sampled is applied to the crop,
    after the wall around it has been thrown away. Both are in `decimate`.
    """
    try:
        img = Image.open(path)
    except OSError as exc:
        die(f"cannot open {path}: {exc}")
    img = img.convert("L")
    if crop:
        try:
            x0, y0, x1, y1 = (int(v) for v in crop.split(","))
        except ValueError:
            die("--crop wants x0,y0,x1,y1 in source pixels")
        if not (0 <= x0 < x1 <= img.width and 0 <= y0 < y1 <= img.height):
            die(f"--crop {crop} is not inside the {img.width}x{img.height} image")
        img = img.crop((x0, y0, x1, y1))
    if rotate:
        img = img.rotate(rotate, resample=Image.BILINEAR, fillcolor=0)
    if max(img.size) > MAX_FRAME:
        k = MAX_FRAME / max(img.size)
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))),
                         Image.LANCZOS)
    a = np.asarray(img, dtype=np.float32) / 255.0
    return (1.0 - a) if invert else a


# ---------------------------------------------------------------- alignment


def estimate_cell(prof, lo, hi):
    """The cell size in the picture, from the periodicity of its ink profile.

    Autocorrelation on the mean-subtracted profile, then the smallest *local
    maximum* that reaches 60% of the strongest lag. The two guards are for the two
    ways this goes wrong: without subtracting the mean the slow envelope of the
    profile dominates every lag, and without taking the smallest peak the answer is
    as likely to be two cells as one - which is not a harmless factor of two, since
    it puts a decode off by every other character.
    """
    p = prof - prof.mean()
    n = len(p)
    lo_i, hi_i = max(2, int(lo)), min(n // 2, int(hi))
    if hi_i <= lo_i:
        die(f"cannot estimate the cell size: a profile of {n} px leaves no room for"
            f" a search from {lo_i} to {hi_i}")
    score = np.full(n, -np.inf)
    for lag in range(lo_i, hi_i + 1):
        score[lag] = float((p[:n - lag] * p[lag:]).sum()) / (n - lag)
    best = float(score[lo_i:hi_i + 1].max())
    if best <= 0:
        die("the ink profile has no periodicity at all - there is no text in this"
            " picture, or it is not the console")
    for lag in range(lo_i + 1, hi_i):
        if (score[lag] >= 0.6 * best and score[lag] >= score[lag - 1]
                and score[lag] >= score[lag + 1]):
            return float(lag)
    return float(int(np.argmax(score)))


def lattice_pattern(geo, font):
    """The expected ink profile of one cell, in both directions, from the font.

    Not a hand-drawn comb. The x pattern is flat across the ink columns and zero
    across the gap because the gap is structural; the y pattern is the font's own
    mean ink per sub-row, which is what makes it usable - sub-rows 0, 10 and 11 are
    nearly empty across the alphabet but '[' and '_' are not, so a hand-drawn comb
    would either miss those or claim a gap that is not there.
    """
    pat_x = np.zeros(geo["cell_w"], dtype=np.float64)
    pat_x[:geo["ink_w"]] = 1.0
    sub = font.mean(axis=(0, 2))                     # (12,) ink fraction per sub-row
    pat_y = np.repeat(sub, geo["scale"])
    pat_y = np.concatenate([pat_y, np.zeros(geo["cell_h"] - len(pat_y))])
    return pat_x, pat_y[:geo["cell_h"]]


def phase_score(prof, pattern):
    """(correlation, best phase) - how well the comb explains a profile.

    A proper correlation coefficient in [-1, 1]: the comb has to be extended to the
    profile's own length before it is normalised against it. Normalising against the
    twelve-element comb instead gives numbers above 1, and the two directions are
    then added together as an alignment objective - so a coefficient that is not a
    coefficient quietly weights the axes differently at every magnification.
    """
    p = prof - prof.mean()
    pat = pattern - pattern.mean()
    n = len(p)
    best = (-2.0, 0)
    for ph in range(len(pattern)):
        # profile[x] pairs with pattern[(x - ph) % len(pattern)]
        q = pat[(np.arange(n) - ph) % len(pattern)]
        denom = float(np.sqrt((p * p).sum() * (q * q).sum()))
        if denom <= 0:
            continue
        s = float((p * q).sum()) / denom
        if s > best[0]:
            best = (s, ph)
    return best


def to_image(a):
    """An ink array as the 8-bit image the warp works on.

    Split out from `warp` because the alignment search calls `warp` a thousand
    times on the *same* array, and the float->uint8 conversion and the PIL image
    construction are then paid a thousand times for one picture's worth of work.
    Measured on the selftest's eleven cases: fourteen milliseconds per warp with
    the conversion inside, five with it hoisted.
    """
    return Image.fromarray((np.clip(a, 0.0, 1.0) * 255).astype(np.uint8))


def warp_image(im, rot_deg, mag):
    """Resample an 8-bit image so content magnified `mag` and turned `rot_deg`
    lands at native scale. Output -> input, which is the direction PIL's AFFINE
    wants, and the sign of the rotation is left to the search rather than reasoned
    about here: PIL's y axis points down, and a sign error in a trig convention is
    the kind of bug that produces a plausible wrong answer instead of an obvious
    one.
    """
    cx, cy = im.width / 2.0, im.height / 2.0
    th = np.radians(rot_deg)
    ca, sa = np.cos(th), np.sin(th)
    a11, a12 = mag * ca, -mag * sa
    a21, a22 = mag * sa, mag * ca
    data = (a11, a12, cx - a11 * cx - a12 * cy,
            a21, a22, cy - a21 * cx - a22 * cy)
    return im.transform(im.size, Image.AFFINE, data, resample=Image.BILINEAR)


def warp(a, rot_deg, mag):
    """`warp_image` on an array, for callers that have one picture and one warp."""
    out = warp_image(to_image(a), rot_deg, mag)
    return np.asarray(out, dtype=np.float32) / 255.0


def align(ink, geo, font, rot_max=3.0, verbose=True):
    """Where the console's lattice is in this picture: (rot, mag, dx, dy).

    Coarse-to-fine over rotation and magnification, scored by how much ink the
    console's comb explains. Two passes rather than one because the objective is
    smooth in both parameters - a second pass at a tenth of the step is what turns
    "roughly level" into "level", and level is what the sampler needs; a rotation
    error of a quarter of a degree drifts a whole pixel across ninety columns, and
    a pixel is half a stroke on this panel.
    """
    pat_x, pat_y = lattice_pattern(geo, font)
    colp = ink.sum(0)
    rowp = ink.sum(1)
    est_w = estimate_cell(colp, geo["cell_w"] * 0.4, geo["cell_w"] * 3.0)
    est_h = estimate_cell(rowp, geo["cell_h"] * 0.4, geo["cell_h"] * 3.0)
    # The magnification comes from the *column* period alone. The row profile also
    # has a period, but it is a weak one: the font's ink is spread over most of its
    # twelve sub-rows (measured, sub-rows 0-11 run 0.03 to 0.39), so there is no
    # structural gap in y to lock onto and the estimator picks a spurious lag - on
    # this tool's own clean selftest it read 9 px where the cell is 24, which is a
    # magnification of 0.69 and a decode of nothing. The column period has the
    # 10-px-ink / 2-px-gap structure and gets it right, and the cell's aspect ratio
    # is fixed by the font, so the column period determines the row period. The row
    # estimate is kept as a cross-check and reported when it disagrees.
    mag0 = est_w / geo["cell_w"]
    want_aspect = geo["cell_h"] / geo["cell_w"]
    seen_aspect = est_h / max(1e-9, est_w)
    aspect_odd = abs(seen_aspect - want_aspect) > 0.15 * want_aspect

    def score_of(a):
        sx, _ = phase_score(a.sum(0), pat_x)
        sy, _ = phase_score(a.sum(1), pat_y)
        return sx + sy

    # The magnification is searched over an absolute range, not a window around the
    # estimate - and that is the fix for the failure this tool had on its own
    # blurred scaffold. `est_w` reads 12 px for a clean screen and 36 for a blurred
    # one, so `mag0` was 0.92 where the truth was 1.00; a search of `mag0 * (1 +/- 8%)`
    # then spans 0.84 to 0.99 and *excludes the answer by two percent*. Measured, the
    # objective at the true magnification is 0.96 against 0.31 ten percent away, so
    # the peak is real and narrow and the estimate is the unreliable part. Hence:
    # search wide enough that the estimate cannot exclude the truth, and let the
    # objective choose. `mag0` is kept only to report how far the answer had to move
    # from the estimate, which is the signature of a picture that is not the console.
    def search(mag_lo, mag_hi, mag_step, rot_span, rot_step):
        """Score the objective over a rot x mag grid, keeping the best seen so far.

        The ranges are absolute (mag) or relative to the current best (rot), and the
        starting `best` is deliberately -inf rather than the unwarped picture. The
        first version of this initialised `best` to `(score_of(ink), 0.0, mag0)`,
        which is a score measured at magnification 1.0 *labelled* as mag0 - so when
        the estimate was wrong, the answer was compared against a number it could
        never beat and the search could not move at all. Measured: it returned
        `mag0` to four decimals on a case whose true magnification was 1.00, because
        0.917 was frozen as unbeatable.
        """
        nonlocal best
        rots = np.arange(max(-rot_max, best[1] - rot_span),
                         min(rot_max, best[1] + rot_span) + 1e-9, rot_step)
        mags = np.arange(mag_lo, mag_hi + 1e-12, mag_step)
        for rot in rots:
            for mag in mags:
                s = score_of(np.asarray(warp_image(im, float(rot), float(mag)),
                                        dtype=np.float32))
                if s > best[0]:
                    best = (s, float(rot), float(mag))

    im = to_image(ink)
    best = (-np.inf, 0.0, mag0)
    # coarse: a quarter of a degree and four percent, over the range a photograph of
    # this panel can plausibly be at. Then two refinements inside the coarse best.
    search(0.45, 2.20, 0.04, 2.0, 0.25)
    search(best[2] * 0.93, best[2] * 1.07, 0.01, 0.4, 0.1)
    search(best[2] * 0.985, best[2] * 1.015, 0.002, 0.06, 0.02)

    _s, rot, mag = best
    out = warp(ink, rot, mag)
    sx, dx = phase_score(out.sum(0), pat_x)
    sy, dy = phase_score(out.sum(1), pat_y)
    info = {"rot": rot, "mag": mag, "dx": int(dx), "dy": int(dy),
            "est_w": est_w, "est_h": est_h, "score_x": sx, "score_y": sy,
            "mag0": mag0, "aspect_odd": aspect_odd}
    if verbose:
        drift = mag / mag0 if mag0 else 0.0
        print(f"  cell in the picture: {est_w:.2f} px wide, so {mag0:.4f}x native"
              f" ({geo['cell_w']}x{geo['cell_h']})")
        if aspect_odd:
            print(f"  !! the row profile reads {est_h:.2f} px tall, an aspect of"
                  f" {seen_aspect:.2f} where the font's is {want_aspect:.2f} - the"
                  f" row estimate is the unreliable one, but a disagreement this"
                  f" large usually means the picture is not square on")
        print(f"  aligned by: rot={rot:+.2f} deg  mag={mag:.4f}x  phase=({dx},{dy})"
              f"  lattice r={sx:.3f}/{sy:.3f}")
        if not 0.85 <= drift <= 1.18:
            print(f"  !! the search settled on {drift:.3f}x the estimate - the profile"
                  f" said the cell was {est_w:.1f} px and the lattice says it is"
                  f" {mag * geo['cell_w']:.1f}. One of the two is reading the wrong"
                  f" period; check with --grid-check before trusting the text.")
        if sx < 0.25 or sy < 0.25:
            print(f"  !! the lattice correlation is weak - the grid this decode uses"
                  f" may not be the console's. Check with --grid-check.")
    return out, info


# ---------------------------------------------------------------- sampling


class Grid:
    """Summed-area sampling of the panel grid, and the search that rides on it.

    A summed-area table makes each of a cell's 60 sub-blocks a four-lookup mean, so
    trying a hundred candidate origins costs about what trying one costs. That is
    what makes the per-row offset search affordable, and the per-row search is what
    makes a hand-held photograph usable: a phone photographed flat is not rotated
    but it is very often a few pixels of trapezoid, and one global origin would
    smear that across the bottom of the screen, which is exactly where the row
    worth reading sits.
    """

    def __init__(self, ink, geo):
        self.geo = geo
        self.ink = ink
        self.sat = np.zeros((ink.shape[0] + 1, ink.shape[1] + 1), dtype=np.float64)
        self.sat[1:, 1:] = ink.cumsum(0).cumsum(1)
        self.cw, self.ch, self.iw = geo["cell_w"], geo["cell_h"], geo["ink_w"]
        self.sc = geo["scale"]

    def cells(self, cols, row, dx, dy):
        """(len(cols), 12, 5) mean ink per sub-block, for one printed row.

        Five sub-columns, not six: the 6th boundary is the cell's edge and the 6th
        sub-column would be the inter-character gap, which carries no ink by
        construction. Counting it makes the template 72 wide while the font is 60,
        which is the shape mismatch this cost.
        """
        x0 = dx + np.asarray(cols) * self.cw
        xa = x0[:, None] + np.arange(5)[None, :] * self.sc
        xb = xa + self.sc
        ya = dy + row * self.ch + np.arange(GLYPH_ROWS) * self.sc
        yb = ya + self.sc
        H, W = self.ink.shape
        for arr, hi in ((xa, W), (xb, W), (ya, H), (yb, H)):
            np.clip(arr, 0, hi, out=arr)
        S = self.sat
        y1, y0_ = yb[None, :, None], ya[None, :, None]
        x1, x0_ = xb[:, None, :], xa[:, None, :]
        tot = S[y1, x1] - S[y0_, x1] - S[y1, x0_] + S[y0_, x0_]
        area = ((yb - ya)[None, :, None] * (xb - xa)[:, None, :]).astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(area > 0, tot / np.maximum(area, 1), 0.0).astype(np.float32)

    def row_has_ink(self, row, dy):
        """Whether a printed row band carries a stroke anywhere along its width.

        Along the whole width, not just the first column: the crop leaves a margin
        so the text starts in cell 1, and a test on column 0 alone would declare the
        panel empty while looking straight at eight rows of text.

        `lit_mask`, not a sum of the ink: on a noisy photograph the background alone
        sums to hundreds over a band this size, so every row of an empty panel would
        read as populated. The mask is the same one `crop_to_ink` uses, so the two
        agree about what counts as a stroke.
        """
        y0 = max(0, dy + row * self.ch)
        y1 = min(self.ink.shape[0], dy + (row + 1) * self.ch)
        return y1 > y0 and bool(lit_mask(self.ink[y0:y1]).any())


def templates(font, passes=3):
    """The glyph templates to try: the font, then softened copies of it.

    Each is returned with its own ink count, because the objective below needs it.

    The second and third are a fallback that has never been observed to engage:
    the pick in `decode` chooses the sharp font at every blur radius the scaffold
    can make, up to and including radii where the decode has already failed. The
    measurement is at the pick, and it is the reason these are kept rather than
    assumed to be doing something.

    `WriteFrameBuffer` returns early for `Character >= 127`, so the 96th constant -
    which is blank, and therefore scores exactly what a space scores - can never be
    drawn. It needs no special case here: a blank template ties with the space on a
    blank cell, and `argmax` returns the *lowest* index, so index 0 wins and index
    95 can never be selected. An earlier version carried a `-1e9` for it at three
    call sites, which was the same rule stated three times.
    """
    out = []
    for k in range(passes):
        t = soften(font, k).reshape(96, -1).astype(np.float32)
        out.append((f"soft{k}", t, t.sum(1).astype(np.float32)))
    return out


def score_cells(cells, t, pop):
    """(per-glyph scores, per-cell value) for a batch of sampled cells.

    The objective is the negative squared distance from the sampled cell to the
    best-matching template, and it is written this way because the two obvious
    spellings are both broken, each in a way that produces a plausible wrong
    answer rather than a failure:

      * `sum_k [ t*c + (1-t)*(1-c) ]` has a constant 60 per cell, so summed over
        ninety cells it is 5400 plus a few hundred, and the part that depends on
        where the grid sits is four percent of the total. Measured: it preferred
        sampling *off* the text, because moving the window off the ink lowers the
        sampled ink and the sampled ink is part of what that sum maximises. Row 1
        of a one-degree tilt decoded to nothing while rows 2 to 8 beside it decoded
        exactly - the offsets were chosen per row, and row 1 chose the empty band.

      * Dropping the constant to leave `(2t-1).c` - the fix that stopped 'S'
        decoding as '6' - rewards blank glyphs and makes the margin between the
        best and the runner-up collapse exactly where the picture is blurred.

    Expanding the square gives `-sum(c^2) + 2*sum_{ink} c - popcount`. The first
    term is the same for every glyph in a cell and different between cells, so the
    per-cell value is `max_g(2*sum_{ink_g} c - popcount_g) - sum(c^2)`: zero when
    the cell *is* a glyph, negative otherwise, and worse the further off the grid
    sits. Summed over cells that is a count of sub-blocks the offset gets right,
    which is what an alignment objective has to be.
    """
    sc = 2.0 * (cells @ t.T) - pop[None, :]
    return sc, sc.max(1) - (cells * cells).sum(1)


def decode(ink, geo, font, info, drift=6, drift_x=3, min_margin=1.5):
    """(lines, report) - the text per printed row, and what it cost to get it.

    Rows with no ink are skipped rather than decoded: an all-black row of the panel
    is either a gap the console left or output past the end, and eighty blank lines
    would bury the ten that carry the reading.

    Rows and columns are both taken from the ink, so neither the panel's 100 rows nor
    its 90 columns is used as a window onto the picture - see the comment on the
    column range below for the failure that made the column half load-bearing.
    """
    grid = Grid(ink, geo)
    tmpl = templates(font)
    x0, y0 = info["dx"], info["dy"]

    rows_present = [r for r in range(geo["rows"]) if grid.row_has_ink(r, y0)]
    if not rows_present:
        return [], [], None

    # **The columns come from where the ink is, not from how wide the panel is.**
    # `np.arange(geo["columns"])` was the first spelling and it silently drops the
    # end of a line: the grid is anchored at `x0`, the crop puts one cell of margin
    # around the text, and the warp then re-centres whatever it was given - so the
    # text starts at an arbitrary cell and a ninety-cell window from cell zero can
    # end before the line does. Measured on this tool's own scaffold: a sixty-char
    # `KEY`/`K 19 Ss` row landed in cells 30 to 89 on a clean screen and decoded
    # exactly, and in cells 31 to *90* once the alignment came out one pixel over -
    # and cell 90 is not in `arange(90)`, so the last character was read as a blank
    # and the row came back fifty-nine characters long. Nothing about that is
    # specific to the scaffold: any photograph whose text block is not flush with
    # the crop's left edge loses its right-hand end the same way, and the last
    # character of a `KEY` line is the most important one on the panel.
    lit_cols = np.flatnonzero(lit_mask(ink).any(0))
    if lit_cols.size == 0:
        return [], [], None
    cw = geo["cell_w"]
    first = max(0, int(np.floor((lit_cols[0] - x0) / cw)))
    last = int(np.ceil((lit_cols[-1] + 1 - x0) / cw))
    # ... and not past the picture: a cell wholly outside it has no area and would
    # be read as blank anyway, but a row of them would pad every line with spaces
    # that the strip then hides - which is how a wrong column count stays invisible.
    last = min(last, int(np.ceil((ink.shape[1] - x0) / cw)))
    if last < first:
        return [], [], None
    cols = np.arange(first, last + 1)
    n = len(cols)

    def score_rows(rows, dy, dxs, t, pop):
        """(best total, the dx that earned it, {row: its scores at *that* dx}).

        The scores have to come back from the winning `dx` and not from the last one
        tried. Returning the last is a silent, plausible-looking bug: the offsets
        searched are `x0 - drift_x .. x0 + drift_x`, so the last is `x0 + drift_x`,
        and every cell is then sampled three pixels to the right of where the
        reported answer says it was. The glyph boundaries are twelve pixels apart, so
        a three-pixel shift lands inside the next cell's ink and the line decodes to
        something that is recognisably text and is not the text - which is worse than
        a failure, and was measured here: a row whose cells were a hand-verified
        `P` and `2` at `dx=0` decoded as `|--l ... }|` while the report said `dx=0`.
        """
        best = None
        for dx in dxs:
            s, here = 0.0, {}
            for r in rows:
                cells = grid.cells(cols, r, dx, dy).reshape(n, -1)
                sc, value = score_cells(cells, t, pop)
                s += float(value.sum())
                here[r] = sc
            if best is None or s > best[0]:
                best = (s, dx, here)
        return best

    # Which softness explains the picture? Decided once, globally: the blur is a
    # property of the lens and the exposure, not of a row.
    #
    # Measured, because the answer is not the one the ladder's name suggests: this
    # picks `soft0` at every blur radius the scaffold can make, from a clean render
    # to r=6 - including r=4 and r=6, where the decode has already collapsed to 840
    # and 1042 wrong characters. The lead narrows as the blur grows (soft0 beats
    # soft1 by 10,300 on the probe at r=0 and by 1,155 at r=6) and never closes, so
    # neither softened copy is ever selected. Forcing one is worse than inert: at
    # r=1, where the shipped pick decodes exactly, forcing soft1 costs 1051
    # characters and soft2 1058. So "the sharp font wins" is load-bearing rather
    # than a formality, and nothing measured here says the softened copies help -
    # which is why they are neither trusted nor deleted. A real photograph blurrier
    # than r=3 is what would settle that, and no photograph has been read yet.
    probe = rows_present[:8]
    pick = None
    for name, t, pop in tmpl:
        total, _, _here = score_rows(probe, y0,
                                     range(x0 - drift_x, x0 + drift_x + 1), t, pop)
        if pick is None or total > pick[0]:
            pick = (total, name, t, pop)
    _v, tname, t, pop = pick

    lines, report = [], []
    for r in rows_present:
        got = None
        for dy in range(y0 - drift, y0 + drift + 1):
            total, dx, scores = score_rows([r], dy,
                                           range(x0 - drift_x, x0 + drift_x + 1),
                                           t, pop)
            if got is None or total > got[0]:
                got = (total, dx, dy, scores[r])
        _v, dx, dy, sc = got
        idx = sc.argmax(1)                      # argmax: on a tie the space must win
        runner = sc.copy()
        runner[np.arange(n), idx] = -np.inf
        second = runner.argmax(1)
        margin = sc[np.arange(n), idx] - runner[np.arange(n), second]
        text, weak, rival = [], [], {}
        for c in range(n):
            ch = chr(int(idx[c]) + 32)
            text.append(ch)
            if ch != " " and margin[c] < min_margin:
                weak.append(c)
                # The runner-up is kept, not just the fact of the doubt. A flag
                # that says "column 89 is uncertain" asks the reader to squint at
                # a photograph; one that says "column 89 is P or R" tells them
                # which two shapes to compare, and on this panel that is the whole
                # difference between a usable reading and a re-shoot. It is also
                # the case the scaffold found: `R` and `P` differ by four pixels -
                # a diagonal leg at rows 6 to 9, `#.#..` and `#..#.` against
                # `#....` - and one 3-tap pass of `soften` puts those four at
                # 0.56, 0.38, 0.63 and 0.56, straddling the half-ink line, with a
                # second pass taking all four under it. They are the only pixels
                # in the glyph with no inked neighbour, and `R` is the letter this
                # device's status line is made of.
                rival[c] = chr(int(second[c]) + 32)
        line = "".join(text).strip()
        # `weak` is reported as a position in the line the reader is holding, not as
        # a cell index: the cell range now starts wherever the ink starts, so a cell
        # number is a number from an array nobody can see, and the whole point of
        # the flag is that someone reads a character off the panel and checks it.
        lead = len(text) - len("".join(text).lstrip()) if line else 0
        ink_cols = [c for c in range(len(text)) if text[c] != " "]
        lines.append(line)
        report.append({"row": r, "dx": dx, "dy": dy,
                       "weak": [c - lead + 1 for c in weak], "text": line,
                       "rivals": {c - lead + 1: rival[c] for c in weak},
                       "margin": float(margin[ink_cols].mean()) if ink_cols else 60.0,
                       "worst": float(margin[ink_cols].min()) if ink_cols else 60.0})
    return lines, report, tname


# ---------------------------------------------------------------- selftest


def make_photo(geo, lines, font, pad=(110, 140), scale=2):
    """The screen inside a black scene - a phone in a picture, not a screenshot.

    The padding is the point of this function. Rendering the text and then rotating
    it would turn the first and last characters into partial data, and a decoder
    that has lost the first column of the first character has failed for a reason
    that does not exist in the field: a photograph of a phone has the whole screen
    inside it. Every degradation below is applied to the scene, so the alignment
    stage has to find the screen, which is the actual problem.

    `scale` is how many photograph pixels there are per panel pixel, and it is not
    decoration. The panel is 1080 px wide carrying a 90-column console, so a glyph
    stroke is two panel pixels; at one photograph pixel per panel pixel a blur of
    radius two erases them, and this scaffold failed exactly those cases while the
    decoder was working - measured, `lens blur r=2` decoded to `''` at scale 1 with
    the alignment correct to 0.04 degrees and 1.000x. Nearest-neighbour is the right
    resampling because the panel's pixels are physical squares and the photograph
    only samples them more finely; a real photograph of this phone has the panel at
    roughly two to three photograph pixels per panel pixel, and the degradation
    parameters below are in photograph pixels, so scale 1 was testing a resolution
    no real photograph of this phone will have.
    """
    screen = console_render(lines, geo, font)
    sh, sw = screen.shape
    if scale != 1:
        im = Image.fromarray((np.clip(screen, 0, 1) * 255).astype(np.uint8))
        screen = np.asarray(im.resize((sw * scale, sh * scale), Image.NEAREST),
                            dtype=np.float32) / 255.0
        sh, sw = screen.shape
    canvas = np.zeros((sh + 2 * pad[1], sw + 2 * pad[0]), dtype=np.float32)
    canvas[pad[1]:pad[1] + sh, pad[0]:pad[0] + sw] = screen
    return canvas


def _blur(a, radius):
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0


def _rotate(a, deg):
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    return np.asarray(im.rotate(deg, resample=Image.BILINEAR, fillcolor=0),
                      dtype=np.float32) / 255.0


def _zoom(a, factor):
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    w, h = int(im.width * factor), int(im.height * factor)
    im = im.resize((w, h), Image.BILINEAR).resize(im.size, Image.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


def _shift(a, dx, dy):
    b = np.zeros_like(a)
    H, W = a.shape
    b[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)] = \
        a[max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]
    return b


def _noise(a, sigma, seed=7):
    return a + np.random.default_rng(seed).normal(0, sigma, a.shape)


def _glare(a, strength):
    return a * np.linspace(1.0 - strength, 1.0 + strength, a.shape[0])[:, None]


# The screen the selftest asks the decoder for. Every line is a format string
# out of the DxeCore Dispatcher or Mem/Page.c with this device's readings
# substituted, because a scaffold is only worth its runtime if it renders the
# screen that will actually be photographed - and the earlier fixture did not.
# It was written from memory of the digest rather than from it, and three of its
# eight lines were not lines this firmware can print at all:
#
#   * `P2 APRI ... promoted=46` - there is no `promoted` field. Dispatcher.c:2279
#     is `P2 APRI bytes=%d entries=%d sum=%x`, and `%x` prints bare hex, so
#     `sum=0xa998b263` was the wrong spelling of a value that is real.
#   * `P2 BIN init=1 code=150 data=800 bs9=Success bs16=Success` - `code=`,
#     `data=`, `bs9=` and `bs16=` are not fields of any `P2 BIN` line. The four
#     BIN formats (Dispatcher.c:466-489) carry `init=/hob_rc=/hob_rd=`,
#     `rc=..used=`, `rd=..used=` and `def=`. `bs9=`/`bs16=` are the last two of
#     the six `%r` fields on `P2 RETRY` (:495) - so the fixture tested `bs9=`
#     in a position it never occupies, and a decoder that dropped it in its real
#     one, fifth of six and past a wrap, would still have passed.
#   * `P2 FREE largest=16 MiB in EfiRuntimeServicesCode` - :2462 prints
#     `P2 FREE largest=%d pages`; the words are not in the format.
#
# The two things the fixture most needed to exercise and did not are the ones
# that have already cost this project readings. The SEQ line is 46 characters of
# `s` and `L`, and the earlier fixture rendered 46 `s` - so the one discrimination
# the single most important reading depends on, `L` against the `l` and `1` that
# surround it in a hex GUID, was never tested. And no line in it was long enough
# to wrap, so the console's wrap - which is what puts half of `P2 RETRY` and the
# tail of `P2 APRI first=` on an unlabelled row of their own - went untested too.
# Both are here now, and the wrap is covered by the assertion rather than by a
# comment: `console_render` lays these lines out with the real wrap, so the
# decoder has to return the continuation rows *and* join back to the input.
SEQ_LINE = "ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL"
P2_SCREEN = [
    "P2 APRI bytes=1120 entries=70 sum=a998b263",           # :2279, %x bare
    # 97 columns against a 90-column console: always wraps, and the last two
    # hex digits of the second GUID land alone on the next row.
    "P2 APRI first=EBF342FE-B1D3-4EF8-957C-8048606FF671 "
    "last=462CAA21-7614-4503-836E-8AB6F4662331",            # :2286
    "P2 APRI matched=0..45 unhit=24",                       # :2295
    "P2 APRI miss=18 6D6F6475-6C65-0000-0000-000000000000",  # :2316
    f"P2 SEQ [{SEQ_LINE}]",                                  # :2348
    # WHY is the same 46 positions with a status letter each, out of
    # `P2WhyLetter` (:197): s, R, N, X, D, E, P, U and O for anything else. The
    # letters are the reading; `P2 ERR` below is the same statuses in words.
    "P2 WHY [ssssssssssssssssssRRRsRNNNXDDEPPUOOOOOOOOOOOO]",  # :2349
    "P2 ERR Out of Resources x27",                          # :2403
    "P2 DIAG L EBF342FE-B1D3-4EF8-957C-8048606FF671 Out of Resources",  # :2422
    "P2 STATS discovered=69 apriori=46/70 started=19 diag=27 noload=27",  # :2431
    "P2 WALK t=0 seen=80 iter=81 "
    "last=EBF342FE-B1D3-4EF8-957C-8048606FF671",            # :2454
    "P2 FREE largest=1024 pages",                           # :2462, P2LargestAlloc
    "P2 BIN init=1 hob_rc=Success hob_rd=Success",          # :466
    "P2 BIN rc=6D6F6475..6C65 used=150/200",                # :473
    "P2 BIN rd=6D6F6475..6C65 used=150/200",                # :481
    "P2 BIN def=6D6F6475..6C65",                            # :489
    # Six `%r` fields, all six at the status this run produced. That is 140
    # columns against the 90-column console, so it wraps, and it wraps *before*
    # `bs9=` - the field name starts at column 98, on the second row, with the
    # first four readings' tail on row 1 and no label on row 2 at all. This is
    # the arrangement the reading of `bs9=` has to survive and the reason the
    # line is here; the all-Success form is 86 columns and does not wrap, so the
    # harder case tests strictly more.
    "P2 RETRY rc16=Out of Resources rc48=Out of Resources "
    "rc112=Out of Resources rd16=Out of Resources "
    "bs9=Out of Resources bs16=Out of Resources",           # :495
    "P2 FWTY bc=12 bd=3 rt=8 oth=1 n=24",                   # Page.c, P2FreeWhyReport
    "P2 FWHY t=0 np=4096 a=1 big=1024 raw=2048 free=1024 c=1 g=0",  # Page.c
    "K 19 Ss 19/46 free=1024 "
    "6D6F6475-6C65-0000-0000-000000000000",                 # the per-dispatch line
    "KEY 27/46 err=Out of Resources at=18 free=1024 miss=18",  # P2Key, printed last
]


def selftest(geo, font, verbose=True):
    """Render, degrade, decode - and require the text back exactly.

    Returns `(results, tally)`: one bool per case, and how many characters across
    all the failures were flagged weak against how many were not. The bools are
    the test; the tally is what says whether a failure is the decoder being wrong
    or the flag doing its job, and it is returned rather than printed so a caller
    can state it without parsing the report.

    The scaffold asserts its own coverage before it runs, which is the lesson of
    the fixture above: a test whose fixture quietly stops containing the case it
    was written for reports success for the wrong reason, and 11/11 from a
    screen that cannot fail on `L` against `1` is worth less than a smaller
    number that can. Both properties below are properties of the fixture, so
    they are checked against `geo`'s own column count rather than a literal 90.
    """
    text = P2_SCREEN
    cols = geo["columns"]
    seq = P2_SCREEN[4]
    if "L" not in seq or "s" not in seq:
        sys.exit("panel-text: the SEQ fixture no longer mixes s and L, which is the one "
                 "discrimination the probe reading depends on - see SEQ_LINE")
    wrapping = [ln for ln in text if len(ln) > cols]
    if not wrapping:
        sys.exit(f"panel-text: no fixture line exceeds the {cols}-column console, so the "
                 "wrap the digest relies on is not being tested")
    retry = next((ln for ln in text if "bs9=" in ln and "P2 RETRY" in ln), None)
    if retry is None:
        sys.exit("panel-text: the fixture has no P2 RETRY line, so bs9= is not tested "
                 "in the position it actually occupies")
    beyond = retry.index("bs9=") >= cols
    if not beyond:
        sys.exit(f"panel-text: P2 RETRY's bs9= starts at column {retry.index('bs9=')} of "
                 f"{cols}, so the fixture no longer tests the case where the field name "
                 "lands on the wrapped row below its line")
    ref = "".join(text)
    scene = make_photo(geo, text, font)
    # `console_rows` is a second spelling of `console_render`'s rules, so it is
    # checked against it before it is used to judge anything. Only the ink
    # identity is required, and only when it holds exactly: a continuation row
    # that begins with a space would be dropped as a leading space when rendered
    # on its own but kept inside the line it wrapped from, so in that one case
    # the two models are allowed to differ and the scaffold says so rather than
    # claiming a decoder fault.
    PANEL_ROWS[0] = geo["rows"]
    ROWS = console_rows(text, cols)
    same_ink = np.array_equal(console_render(ROWS, geo, font),
                              console_render(text, geo, font))
    if not same_ink and any(r[:1] == " " for r in ROWS):
        print("  note: console_rows and console_render differ (a wrapped row begins "
              "with a space); the row-level report below is approximate")

    cases = [
        ("clean screen in a scene", scene),
        ("lens blur r=1", _blur(scene, 1.0)),
        ("lens blur r=2", _blur(scene, 2.0)),
        ("sensor noise sigma=0.08", _noise(scene, 0.08)),
        ("room glare, 25% ramp", _glare(scene, 0.25)),
        ("camera 1.0 deg off level", _rotate(scene, 1.0)),
        ("camera 2.5 deg off level", _rotate(scene, 2.5)),
        ("screen 4% too small", _zoom(scene, 0.96)),
        ("screen 5% too large", _zoom(scene, 1.05)),
        ("screen 60 px off centre", _shift(scene, 60, 70)),
        ("all of it", _shift(_rotate(_noise(_glare(_blur(_zoom(scene, 0.98), 1.2),
                                                   0.05), 0.2), 1.5), 35, -25)),
    ]

    results = []
    tally = {"flagged": 0, "unannounced": 0}
    for name, arr in cases:
        ink, thr, bg, fg = normalise(np.clip(arr, 0.0, 1.0))
        cropped, _ = crop_to_ink(ink, geo)
        try:
            warped, info = align(cropped, geo, font, verbose=False)
            lines, report, tname = decode(warped, geo, font, info)
        except SystemExit:
            lines, info, tname = [], {}, "?"
        joined = "".join(lines)
        ok = joined == ref
        results.append(ok)
        # Every disagreement is classified, whether or not the caller asked to see
        # the report, because "wrong" is not one thing: a position `decode` flagged
        # is the tool reporting doubt, and a position it did not is the tool
        # asserting something false. Only the second is a defect, and a count that
        # adds them together hides which of the two a failure is - which matters
        # here because the case that fails on this scaffold fails by one character
        # whose margin is 0.02, and the flag names that character and its rival.
        flagged, confident = [], []
        if not ok:
            for i, (want, got) in enumerate(zip(ROWS, lines)):
                if want == got:
                    continue
                weak = set(report[i]["weak"]) if i < len(report) else set()
                for c, (a, b) in enumerate(zip(got, want)):
                    if a != b:
                        (flagged if c + 1 in weak else confident).append((i, c + 1, a, b))
            tally["flagged"] += len(flagged)
            tally["unannounced"] += len(confident)
        if verbose:
            note = "" if ok else f"   rot={info.get('rot', 0):+.2f} mag={info.get('mag', 0):.3f}"
            print(f"  {'ok  ' if ok else 'FAIL'}  {name}{note}")
            if not ok:
                # Row-by-row against the *input* no longer lines up: the fixture
                # wraps, so `decode` returns more rows than the input has lines
                # and each continuation row belongs to the line above it. So the
                # first comparison is on the joined text, and the row-level one
                # is against `console_rows` - the console's own layout - rather
                # than against the input, which is what made the earlier version
                # of this report blame the wrapped line for a fault elsewhere.
                at = next((i for i, (a, b) in enumerate(zip(joined, ref))
                           if a != b), min(len(joined), len(ref)))
                start = 0
                for src in text:
                    if start + len(src) > at:
                        break
                    start += len(src)
                print(f"        {len(lines)} rows decoded, {len(ROWS)} laid out, "
                      f"{len(ref)} columns expected")
                print(f"        first difference at column {at} of the whole screen,")
                print(f"        in the line beginning {src[:24]!r} at column {at - start}")
                print(f"        got  {joined[max(0, at - 24):at + 24]!r}")
                print(f"        want {ref[max(0, at - 24):at + 24]!r}")
                if len(lines) != len(ROWS):
                    print(f"        !! {len(lines)} rows returned for {len(ROWS)} on the "
                          "panel: a row is missing or spurious, which no character "
                          "comparison can see")
                for i, (want, got) in enumerate(zip(ROWS, lines)):
                    if want != got:
                        print(f"        first differing panel row {i}:")
                        print(f"          got  {got!r}")
                        print(f"          want {want!r}")
                        break
                if flagged or confident:
                    print(f"        {len(flagged) + len(confident)} characters differ: "
                          f"{len(flagged)} flagged weak, {len(confident)} unannounced")
                for i, c, a, b in confident[:3]:
                    print(f"        UNANNOUNCED  row {i} column {c}: got {a!r} want {b!r}")
                for i, c, a, b in flagged[:3]:
                    print(f"        flagged      row {i} column {c}: got {a!r} want {b!r}"
                          f" - either {a} or {b}, to be read off the panel")
    return results, tally


def lit_mask(ink):
    """Where there is a stroke, as opposed to where there is a lit pixel.

    A threshold on the ink alone answers "is this pixel bright", and on a photograph
    those are different questions. Sensor noise on the black background of the
    panel reaches the same level as the faint edges of a stroke, so `ink > 0.5`
    fires on scattered background pixels - and measured on this tool's own combined
    scaffold (glare, noise, blur, tilt, zoom and a shift together) the ink
    rectangle then came out as the entire scene, 2679x1277 where the text block is
    a few hundred pixels across. Every consequence of that is silent: the cell-size
    estimate reads the picture's frame instead of the font, the alignment
    correlation falls to 0.1 because there is no lattice in an empty field, and the
    decode reports nothing - which is at least honest - or the whole scene at the
    wrong offsets, which is not.

    A 3x3 box mean first is the matched filter for a stroke: it divides the noise
    by three and barely touches a two-pixel stem. That is the whole difference
    between "bright" and "ink", and it is why this is a function and not a
    comparison written out at the two places that need it.
    """
    p = np.pad(ink, 1, mode="edge")
    m = (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
         + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
         + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9.0
    return m > 0.5


def crop_to_ink(ink, geo):
    """The lit part of the picture, with a cell of margin - the text block.

    Cropping is not just an optimisation. The alignment objective is a correlation
    over the whole array, and a margin of black around the text dilutes it without
    informing it; worse, the estimate of the cell size reads the *profile*, and a
    long run of empty rows at the bottom of a portrait photograph is a strong
    periodicity that has nothing to do with the font.

    The `lit_mask` threshold rather than `ink > 0.5` is load-bearing for the same
    reason: a crop that swallows the whole scene makes everything downstream
    meaningless while looking like it ran.
    """
    lit = lit_mask(ink)
    rows = np.flatnonzero(lit.any(1))
    cols = np.flatnonzero(lit.any(0))
    if rows.size == 0 or cols.size == 0:
        die("no ink in the picture at all")
    my, mx = geo["cell_h"], geo["cell_w"]
    r0 = max(0, int(rows[0]) - my)
    r1 = min(ink.shape[0], int(rows[-1]) + 1 + my)
    c0 = max(0, int(cols[0]) - mx)
    c1 = min(ink.shape[1], int(cols[-1]) + 1 + mx)
    return ink[r0:r1, c0:c1], (c0, r0)


# ---------------------------------------------------------------- main


def grid_check(ink, geo, font, info):
    """An ASCII picture of the aligned ink against the grid it is being read on.

    The one diagnostic that answers "is the grid in the right place" without a
    photograph to compare against: '#' is ink inside a cell's ink columns, 'o' is
    ink where a gap should be, '.' is an empty ink column and ':' an empty gap.
    A correct alignment shows almost no 'o' and almost no ':' among the '#'.
    """
    a = warp(ink, info["rot"], info["mag"]) if "rot" in info else ink
    dx, dy = info.get("dx", 0), info.get("dy", 0)
    out = []
    for row in range(0, min(a.shape[0], 240), geo["cell_h"]):
        line = []
        for x in range(0, min(a.shape[1], 720)):
            v = a[row:row + geo["cell_h"], x].mean()
            gap = ((x - dx) % geo["cell_w"]) >= geo["ink_w"]
            line.append(("o" if v > 0.35 else ":") if gap
                        else ("#" if v > 0.35 else "."))
        out.append("".join(line))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image", nargs="?", help="a photograph of the panel, for --decode")
    ap.add_argument("--mu", default=DEFAULT_MU)
    ap.add_argument("--platform", default=None)
    ap.add_argument("--render", action="append", default=[], metavar="TEXT",
                    help="render TEXT through the console model (repeatable)")
    ap.add_argument("--render-file", help="render every line of this file")
    ap.add_argument("--render-out", default=None, help="PNG to write")
    ap.add_argument("--decode", action="store_true", help="decode the image argument")
    ap.add_argument("--fb", action="append", default=[], metavar="DUMP",
                    help="decode a raw dump of the panel's own memory; repeat to"
                         " join several dumps into one stream, in the order given")
    ap.add_argument("--crop", help="x0,y0,x1,y1 of the screen in the source image")
    ap.add_argument("--rotate", type=float, default=0.0,
                    help="degrees to undo before decoding")
    ap.add_argument("--invert", action="store_true",
                    help="the picture is dark-on-light, which it should not be")
    ap.add_argument("--no-crop", action="store_true",
                    help="do not crop to the ink block before aligning")
    ap.add_argument("--drift", type=int, default=6, help="rows: +/- px of origin search")
    ap.add_argument("--drift-x", type=int, default=3, help="cols: +/- px of origin search")
    ap.add_argument("--min-margin", type=float, default=1.5,
                    help="a character whose best glyph beats the runner-up by less"
                         " than this many sub-blocks is reported weak (of 60)")
    ap.add_argument("--geometry", action="store_true", help="print the panel geometry")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--grid-check", action="store_true",
                    help="ASCII picture of the aligned ink against the grid")
    ap.add_argument("--font-preview", action="store_true",
                    help="print the whole alphabet as ASCII, to check the bit order")
    args = ap.parse_args()

    geo = load_geometry(args.mu, args.platform)
    font = load_font(args.mu)

    if args.geometry:
        g = geo
        print(f"panel: {g['width']}x{g['height']}, {g['bpp']}bpp   from {g['where']['panel']}")
        print(f"font:  FONT_WIDTH={g['font_w']} FONT_HEIGHT={g['font_h']}"
              f"   from {g['where']['font']}")
        print(f"scale: {g['scale']}   (FontScale in {g['where']['scale']})")
        print(f"cell:  {g['cell_w']}x{g['cell_h']} px, of which"
              f" {g['ink_w']}x{g['ink_h']} carry ink")
        print(f"grid:  {g['columns']} columns x {g['rows']} rows"
              f" = {g['columns'] * g['rows']} characters before the wipe")
        print("wipe:  the console clears the framebuffer instead of scrolling, so")
        print(f"       the panel holds the last {g['rows']} rows of output")
        pat_x, pat_y = lattice_pattern(geo, font)
        print(f"comb:  x pattern {pat_x.astype(int).tolist()}")
        print(f"       y pattern (ink per sub-row, from the font) "
              f"{[round(v, 2) for v in font.mean(axis=(0, 2))]}")

    if args.font_preview:
        print()
        for base in range(0, 96, 8):
            for r in range(GLYPH_ROWS):
                print("  " + "  ".join(
                    "".join("#" if font[i, r, c] else "." for c in range(5))
                    for i in range(base, min(base + 8, 96))))
            print("  " + "  ".join(f"{chr(32 + i):^5}" for i in range(base, min(base + 8, 96))))
            print()

    if args.render or args.render_file:
        lines = list(args.render)
        if args.render_file:
            lines += read(args.render_file, "the render input").splitlines()
        fb = console_render(lines, geo, font)
        out = args.render_out or os.path.join(REPO, "work", "out", "panel-render.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        Image.fromarray((np.clip(fb, 0, 1) * 255).astype(np.uint8)).save(out)
        print(f"\nrendered {len(lines)} line{'s' if len(lines) != 1 else ''} of"
              f" {geo['columns']} columns to {os.path.relpath(out, REPO)}")
        print(f"  {geo['width']}x{geo['height']}, cell {geo['cell_w']}x{geo['cell_h']},"
              f" {len(lines)}/{geo['rows']} rows used,"
              f" {geo['rows'] - len(lines)} left blank")

    if args.selftest:
        print("\npanel-text: does the decoder survive a photograph?\n")
        results, tally = selftest(geo, font)
        print(f"\n  {sum(results)}/{len(results)} degraded photographs decoded exactly")
        # The tally is printed before the exit code, because it is the part a
        # reader needs to judge the number above it. A failed case whose differing
        # characters were all flagged is not the same result as a failed case whose
        # decoder asserted a character the panel does not have, and this scaffold
        # has both kinds of outcome available to it; which one it produced is a
        # measurement, so it is stated rather than assumed either way.
        if tally["flagged"] or tally["unannounced"]:
            print(f"  {tally['flagged']} differing characters were flagged weak and "
                  f"{tally['unannounced']} were not flagged")
            if tally["unannounced"]:
                print("  An unannounced difference is the decoder being confidently "
                      "wrong - the one outcome worth fixing. A flagged one is the "
                      "reading itself saying which two glyphs it is between.")
        if not all(results):
            return 1

    if args.fb:
        lines, kept, gaps, worst, weak = fb_merge(args.fb, geo, font, args.min_margin)
        print(f"\n{len(args.fb)} framebuffer dump{'s' if len(args.fb) != 1 else ''},"
              f" {kept} carrying text, {len(lines)} rows merged")
        if not lines:
            print("  no dump carries text - nothing to read")
            return 1
        if len(args.fb) == 1:
            # One screen: the row numbers are the panel's own, which is what a
            # reader comparing against a photograph of the same screen needs.
            _l, report, tname = fb_lines(args.fb[0], geo, font, args.min_margin)
            print(f"  templates: {tname}")
            print(f"  rows carrying text start at panel row {report[0]['row']}\n")
            for rep in report:
                flag = ""
                if rep["weak"]:
                    pairs = ", ".join(f"{p} ({rep['text'][p-1]} or {rep['rivals'][p]})"
                                      for p in rep["weak"])
                    flag = f"   <- {len(rep['weak'])} weak: {pairs}"
                print(f"  {rep['row']:3d} |{rep['text']}|{flag}")
        else:
            print("  the merge is on content, so a row's position in this list is a"
                  " position in the stream and not on any one screen\n")
            for i, line in enumerate(lines):
                print(f"  {i:4d} |{line}|")
        print(f"\n  weakest row margin {worst:.2f} of 60 sub-blocks;"
              f" --min-margin is {args.min_margin}")
        if weak:
            print(f"  {weak} characters are below it, and are flagged above only when"
                  f" a single dump was given - with a merged stream each row's flags"
                  f" belong to the screen it came from.")
        if gaps:
            print(f"  {len(gaps)} dump{'s' if len(gaps) != 1 else ''} overlapped"
                  f" nothing already read: a gap in the stream, not a join")
            for p, n in gaps:
                print(f"    {os.path.basename(p)}: {n} rows, none shared")
            return 1
        if weak:
            return 1

    if args.decode:
        if not args.image:
            die("--decode needs an image argument")
        photo = load_photo(args.image, args.invert, args.rotate, args.crop)
        ink, thr, bg, fg = normalise(photo)
        print(f"\n{args.image}")
        print(f"  {photo.shape[1]}x{photo.shape[0]} px, Otsu threshold {thr:.3f},"
              f" black {bg:.3f}, white {fg:.3f}")
        src, _off = (ink, (0, 0)) if args.no_crop else crop_to_ink(ink, geo)
        src, dec = decimate(src)
        if dec != 1.0:
            print(f"  the text block is {src.shape[1]}x{src.shape[0]} px, decimated"
                  f" from the photograph by {dec:.3f} - the bound applies to the"
                  f" block, not to the frame")
        # Ink against the edge of the picture is the one failure here that no flag
        # can report, because the missing characters are not in the image. Measured
        # by rotating one of this tool's own renders - which has text at x=0 - by
        # 2.5 degrees: every row lost its first three or four characters, and a
        # line the panel prints as `P2 RETRY` decoded as `ETRY` with nothing
        # marked. The crop has a cell of margin, so ink on the crop's border means
        # the block ran into the frame and was cut, not that the margin is thin.
        edge = lit_mask(src)
        if edge[0].any() or edge[-1].any() or edge[:, 0].any() or edge[:, -1].any():
            print("  !! ink reaches the edge of the picture: the text block is cut")
            print("     off, and the characters outside the frame cannot be decoded"
                  " or flagged.")
            print("     Re-shoot with the whole screen well inside the frame.")
        if src.shape[0] < geo["cell_h"] * 2 or src.shape[1] < geo["cell_w"] * 2:
            print("  the lit region is smaller than two cells - nothing to read")
            return 1
        warped, info = align(src, geo, font)
        if args.grid_check:
            print()
            print(grid_check(warped, geo, font, dict(info, rot=0, mag=1)))
            print()
        lines, report, tname = decode(warped, geo, font, info, args.drift,
                                      args.drift_x, args.min_margin)
        if not lines:
            print("  no rows carry text - nothing to read")
            return 1
        print(f"  templates: {tname}")
        print(f"  {len(lines)} rows carry text; the first is panel row"
              f" {report[0]['row']} of the cropped block, the last is"
              f" {report[-1]['row']}\n")
        weak_total = 0
        for rep in report:
            flag = ""
            if rep["weak"]:
                weak_total += len(rep["weak"])
                # Each doubtful position is printed with its runner-up, so the two
                # candidate shapes are named rather than left to a squint. That is
                # the difference between a flag and an instruction.
                pairs = ", ".join(f"{p} ({rep['text'][p-1]} or {rep['rivals'][p]})"
                                  for p in rep["weak"])
                flag = f"   <- {len(rep['weak'])} weak: {pairs}"
            print(f"  {rep['row']:3d} |{rep['text']}|{flag}")
        print(f"\n  weakest row margin {min(r['worst'] for r in report):.2f} of 60"
              f" sub-blocks; --min-margin is {args.min_margin}")
        if weak_total:
            print(f"  {weak_total} characters are below it. Each is printed above as the")
            print("  two glyphs it is between - the first is the one on the line, the")
            print("  second the runner-up - so check those positions against the panel")
            print("  rather than trusting the line as read.")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
