#!/usr/bin/env python3
"""Which image is on `boot` right now, decided by content and not by memory.

This exists because of one rule that has already been broken once by hand: the
control image must be read *before* the partition is overwritten. Step 4.30 read
`boot` back with `dd` and then compared it against the candidates with an ad-hoc
loop; the comparison was right and the loop was thrown away, so the next session
that wants the same answer has to invent it again - and the session that invents
it under time pressure, in TWRP, on a phone whose USB link drops about once a
minute, is the session that gets it wrong.

So the comparison is a tool, and it answers the only question that matters at
that moment: **is the image on the partition the one I think it is.** It does not
care which image that should be; it reports the best match and how close the rest
came, so "none of them" is as legible as a match.

**The comparison window is the whole readback, and that is not a detail.** The
first draft of this tool compared the leading 280 64-byte blocks - 17,920 bytes -
because that is what step 4.30's hand comparison used. That window is worthless:
two *different* builds, `p2-4.19` and `p2-4.20`, agree on **278 of those 280
blocks** and disagree on **17,413 of the file's 17,824** - they differ only in the
header page, and everything after it in the first 17 KB happens to be identical
because the kernel's leading bytes are BootShim. A 280-block comparison reports a
near-match between images that are 97% different. The window has to cover the
file, and the number worth reading is the fraction over all of it.

Two more things it is careful about, both learned from the reads that failed:

  * **The readback and the image are different lengths, and which is longer
    changes the question.** A candidate is graded over *its own* extent, so a
    candidate shorter than the readback is not punished for the tail behind it,
    and one longer than the readback is not called a mismatch for bytes that were
    never read. The second case is the one that bit step 4.30: `dd` read 1,140,736
    bytes, the image is 1,142,784, and the last 2,048 bytes of the image were
    simply never on the wire. That is reported as *identical over every byte read*
    with the un-read count named - not as "not a match", which is what a naive
    comparison of unequal lengths says.

  * The BOOT partition is larger than any image in it: writes leave the previous
    image's tail behind. So a stale tail is not a mismatch. The report says how
    many blocks past the candidate's end matched anyway, which is how a short
    write is told from a long image.

Usage:
    tools/identify-boot.py work/out/boot-readback.bin
    tools/identify-boot.py --read                     # dd it off the phone first
    tools/identify-boot.py --read --candidates work/out/p2-variants

`--read` needs the phone in TWRP with adbd up (`adb shell` is root there). It
reads with a 1 MiB `dd` block size, not 64 bytes: reading 1.14 MB in 64-byte
blocks is 17,856 `adb` invocations, which on a link that drops about once a
minute does not finish. Comparison granularity is separate from transfer
granularity and stays at 64 bytes.

Exit status is 0 when the whole readback is explained by one candidate - an exact
match, or a candidate that continues past the end of a short read - and 1 when no
candidate does. A 1 is not a failure of the tool; it is the answer "the payload on
the phone is not one of these", which is itself worth knowing before writing over
it.
"""

import argparse
import glob
import hashlib
import os
import struct
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_CANDIDATES = os.path.join(ROOT, "work", "out")
BLOCK = 64
MAGIC = b"ANDROID!"

# Where TWRP puts the boot partition. The by-name link is what the earlier reads
# used; the raw path is kept beside it because a by-name symlink that points at a
# slot rather than the active one would be a silent wrong answer.
BY_NAME = "/dev/block/by-name/boot"

# Enough to cover every candidate with room to see the start of a stale tail: the
# largest image built here is 3,248,128 bytes.
DEFAULT_READ = 4 << 20


def dd_read(device, size, out):
    """Read `size` bytes off the phone in 1 MiB `dd` blocks.

    One `adb exec-out` per megabyte and not per comparison block: the granularity
    that makes the comparison legible is 64 bytes, and that granularity over the
    wire would be 17,856 invocations. A dropped link here costs one megabyte.
    """
    chunk = 1 << 20
    got = 0
    with open(out, "wb") as fh:
        while got < size:
            cmd = ["adb", "exec-out", "dd", f"if={device}", "bs=%d" % chunk,
                   "skip=%d" % (got // chunk), "count=1"]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if r.returncode != 0 or not r.stdout:
                print(f"  stopped at {got:,} bytes (rc={r.returncode},"
                      f" {len(r.stdout)} bytes back)", file=sys.stderr)
                break
            fh.write(r.stdout)
            got += len(r.stdout)
    return got


def header(text):
    """The arm64 boot image header, or None if it does not start with MAGIC."""
    if text[:8] != MAGIC:
        return None

    def u32(off):
        return struct.unpack("<I", text[off:off + 4])[0]

    h = {
        "header_version": u32(40),
        "page_size": u32(36),
        "kernel_size": u32(8),
        "kernel_addr": u32(12),
        "ramdisk_size": u32(16),
        "ramdisk_addr": u32(20),
        "tags_addr": u32(32),
    }
    if h["header_version"] >= 2:
        h["dtb_size"] = u32(1648)
        h["dtb_addr"] = struct.unpack("<Q", text[1652:1660])[0]
    return h


def compare(readback, cand_path, readback_blocks):
    """Grade a candidate over its *own* extent, and report the tail separately.

    This is the prefix trap made explicit. The partition is 2 MiB or more and the
    images in it are 1.1 to 3.1 MiB, so a readback is usually longer than the
    image that was written to it and its tail is whatever the previous write left
    there. Grading over the readback's length would then call a perfect match a
    54% match - which is what the first draft of this function did to the
    archived Sep 23 readback, against the very image it contains.

    So: `within` is the candidate's length rounded up to whole comparison blocks,
    and `same` counts only inside it. `tail_same` counts matching blocks past the
    candidate's end, which is not part of the verdict - a stale tail can coincide
    - but says whether anything recognisable follows the image, which is how a
    short write and a long image are told apart.

    A candidate *longer* than the readback is graded only where the two overlap.
    If it agrees everywhere the readback reaches it is `prefix`, not `match`: the
    readback is explained by this image and the unread tail is a hole in the read,
    not evidence against it. Only a candidate that also disagrees is `truncated`
    without `prefix`, and then it really did not match anything it was compared to.
    """
    cand = open(cand_path, "rb").read()
    within = (len(cand) + BLOCK - 1) // BLOCK
    truncated = within > readback_blocks
    span = min(within, readback_blocks)

    same = 0
    first_diff = None
    for i in range(span):
        off = i * BLOCK
        if readback[off:off + BLOCK] == cand[off:off + BLOCK]:
            same += 1
        elif first_diff is None:
            first_diff = off

    tail_same = 0
    for i in range(within, readback_blocks):
        off = i * BLOCK
        if readback[off:off + BLOCK] == cand[off:off + BLOCK]:
            tail_same += 1

    return {
        "same": same,
        "within": span,
        "declared": within,
        "truncated": truncated,
        "missing": max(0, len(cand) - len(readback)),
        "first_diff": first_diff,
        "tail_same": tail_same,
        "tail_blocks": max(0, readback_blocks - within),
        "clen": len(cand),
        "csha": hashlib.sha256(cand).hexdigest(),
        # Identical wherever the two overlap, over every byte the readback has.
        "sameness": same == span,
        # ... and the candidate continues past the readback, so a few of its bytes
        # were never compared. Not a mismatch, but not fully checked either.
        "prefix": (same == span) and truncated,
        "match": (not truncated) and same == span,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("readback", nargs="?", help="the dd'd boot partition")
    ap.add_argument("--read", action="store_true",
                    help="dd it off the phone first (TWRP, adbd as root)")
    ap.add_argument("--device", default=BY_NAME, help=f"partition to read (default {BY_NAME})")
    ap.add_argument("--size", type=lambda s: int(s, 0), default=DEFAULT_READ,
                    help=f"bytes to read with --read (default {DEFAULT_READ:,})")
    ap.add_argument("--candidates", default=DEFAULT_CANDIDATES,
                    help="directory to search for *.img (this level and one below)")
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "out", "boot-readback.bin"))
    args = ap.parse_args()

    if args.read:
        if not args.readback:
            args.readback = args.out
        print(f"reading up to {args.size:,} bytes from {args.device} ...")
        n = dd_read(args.device, args.size, args.readback)
        print(f"  -> {args.readback} ({n:,} bytes)")
        if n < 2048:
            sys.exit("identify-boot: the read came back empty - the phone is not"
                     " there, or adbd is not up; nothing to compare")

    if not args.readback or not os.path.isfile(args.readback):
        sys.exit("identify-boot: give a readback file, or --read to take one")

    readback = open(args.readback, "rb").read()
    blocks = len(readback) // BLOCK
    if blocks == 0:
        sys.exit(f"identify-boot: {args.readback} is empty")

    print(f"\nreadback  {os.path.relpath(args.readback, ROOT)}")
    print(f"          {len(readback):,} bytes = {blocks:,} x {BLOCK}-byte blocks")
    print(f"          sha256 {hashlib.sha256(readback).hexdigest()}")

    h = header(readback)
    print("\nheader")
    if h is None:
        print("  !! does not start with ANDROID! - this is not a boot image, or the"
              " read started at the wrong offset")
    else:
        print(f"  header_version  {h['header_version']}   page_size {h['page_size']:#x}")
        print(f"  kernel          {h['kernel_size']:,} @ {h['kernel_addr']:#x}")
        print(f"  ramdisk         {h['ramdisk_size']:,} @ {h['ramdisk_addr']:#x}")
        print(f"  tags_addr       {h['tags_addr']:#x}")
        if "dtb_size" in h:
            print(f"  dtb             {h['dtb_size']:,} @ {h['dtb_addr']:#x}")
        if h["ramdisk_size"] < 64:
            print(f"  note: a {h['ramdisk_size']}-byte ramdisk is the stock"
                  f" `ramdisk = 5` placeholder, not a real initramfs")

    # Every .img under the candidate dir, this level and one below, because the
    # payloads of record live in dated subdirectories (work/out/p2-4.20/, ...).
    cands = sorted(set(glob.glob(os.path.join(args.candidates, "*.img")) +
                       glob.glob(os.path.join(args.candidates, "*", "*.img"))))
    if not cands:
        sys.exit(f"identify-boot: no *.img under {args.candidates}")

    print(f"\ncomparing against {len(cands)} candidate(s), each over its own length"
          f" (readback {blocks:,} blocks)")
    results = []
    for path in cands:
        results.append((compare(readback, path, blocks), path))
    # Exact match first, then a candidate that explains the whole readback with a
    # few unread bytes, then by how much of the candidate's own extent matched.
    results.sort(key=lambda r: (not r[0]["match"], not r[0]["sameness"],
                                -r[0]["same"] / max(1, r[0]["within"])))

    width = len(f"{blocks:,}")
    for rank, (r, path) in enumerate(results[:6]):
        rel = os.path.relpath(path, ROOT)
        pct = 100.0 * r["same"] / max(1, r["within"])
        if r["match"]:
            mark = "  <- IDENTICAL over its whole length"
        elif r["prefix"]:
            mark = (f"  <- identical over every byte read; {r['missing']:,} bytes of"
                    " the candidate were never compared")
        elif r["truncated"]:
            mark = "  <- longer than the readback, so its extent was not fully read"
        else:
            mark = ""
        print(f"  {r['same']:>{width},}/{r['within']:,}  {pct:5.1f}%  {rel}{mark}")
        if r["first_diff"] is not None and pct > 25:
            print(f"          first difference at offset {r['first_diff']:#x}"
                  f" ({r['first_diff']:,})")
        if r["tail_blocks"]:
            note = (f"          candidate ends at {r['clen']:,} bytes; the"
                    f" {r['tail_blocks']:,} blocks past it are tail")
            if r["tail_same"]:
                note += f", {r['tail_same']:,} of which happen to match"
            print(note)
    if len(results) > 6:
        tail = ", ".join(f"{os.path.basename(p)} {r['same']}/{r['within']}"
                         for r, p in results[6:])
        print(f"  ... and {len(results) - 6} more, all below: {tail[:160]}")

    r, best_path = results[0]
    print()
    if r["sameness"]:
        print(f"best match: {os.path.relpath(best_path, ROOT)}")
        print("  -> the partition holds this image. Safe to treat as the control.")
        # Both hashes, because a path is not a fixation: `work/out/p2-4.20/` was a
        # build directory before it was rebuilt, and step 4.30's control image
        # stopped being the bytes it named without the name changing. A sha256 in
        # the notes is what survives that.
        print(f"     candidate sha256 {r['csha']}")
        print(f"     readback  sha256 {hashlib.sha256(readback).hexdigest()}")
        print(f"     Pin both before the next write. Its {r['clen']:,} bytes are the whole of")
        print(f"     it; the reference to keep is the readback itself"
              f" ({os.path.basename(args.readback)}).")
        if r["prefix"]:
            print(f"     Caveat, and it is only a caveat: the read stopped at"
                  f" {len(readback):,} bytes, so the")
            print(f"     last {r['missing']:,} bytes of the image were never compared."
                  f" Re-read with")
            print(f"     `--size {max(args.size, ((r['clen'] >> 20) + 1) << 20):,}` to close"
                  f" that, or accept it if the two")
            print(f"     files' sizes already agree ({r['clen']:,}).")
        return 0

    print(f"best match: {os.path.relpath(best_path, ROOT)} at"
          f" {r['same']:,}/{r['within']:,} blocks - NOT a match")
    if r["truncated"]:
        print(f"  -> and it is longer than the readback ({r['clen']:,} > {len(readback):,}),")
        print("     so it was never fully compared. Re-read with a larger --size.")
    print("  -> the payload on the phone is not any image here. Do not assume it is one")
    print("     of them: check the header above, and compare against the archives an")
    print("     earlier session kept (work/out/boot-now-0923.img,")
    print("     work/out/p2-silicon-gzip-preread-0923d.img, work/out/boot-*.img) or")
    print("     ~/backup/gauguin/images/part-boot.img, which is the stock image.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
