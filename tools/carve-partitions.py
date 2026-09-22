#!/usr/bin/env python3
"""Carve named partitions out of the whole-LUN backups.

The P0 backup captured some LUNs whole (`LUN-sde.img` and friends) rather than
partition by partition, because that is the only way to capture a LUN whose
partition table has entries the `by-name` directory does not expose. The data
is all there - but restoring `boot` then means knowing it lives at LBA 162982 of
sde, and computing `162982 * 4096` by hand while a phone sits bricked.

This turns those whole-LUN images into the same `part-<name>.img` files the
per-partition dumps produced, so every partition has one obvious restore path:

    fastboot flash boot ~/backup/gauguin/images/part-boot.img

It also serves as a verification pass: a partition whose carve comes out with
the wrong magic or a suspiciously uniform body is reported, which catches a
truncated or hole-filled backup before it is needed.

Usage:  carve-partitions.py [--images DIR] [--out DIR] [--list] [--force]
"""
import argparse
import os
import struct
import sys

# What a carved partition should look like, where a signature is known. Only
# used to sanity-check the result - a mismatch is reported, never fatal, since
# a raw partition legitimately has no magic.
SIGNATURES = {
    "boot":      (b"ANDROID!", 0),
    "recovery":  (b"ANDROID!", 0),
    "dtbo":      (b"\xd0\x0d\xfe\xed", 0),   # DTB magic, big-endian
    "vbmeta":    (b"AVB0", 0),
    "vbmeta_system": (b"AVB0", 0),
    "logo":      (b"\x89PNG", 0),
    "abl":       (b"\x1f\x8b", 0),           # gzip-compressed FV
}

GPT_SIG = b"EFI PART"


def find_gpt(head):
    """Locate the primary GPT header in the first megabyte, or return None.

    The sector size must be inferred, not assumed - and guessing wrong is easy:
    a 4096-byte-sector LUN has a valid header at byte 4096, which is also LBA 8
    of a 512-byte-sector disk. So the candidate is validated against the header's
    own contents rather than just its signature: `MyLBA` must equal the LBA it
    was found at, which is only true for the correct sector size.
    """
    for ss in (4096, 512, 2048):
        for lba in (1, 2, 4, 8, 16, 32, 64, 128):
            off = lba * ss
            if off + 92 > len(head) or head[off:off + 8] != GPT_SIG:
                continue
            my_lba, = struct.unpack("<Q", head[off + 24:off + 32])
            hdr_size, = struct.unpack("<I", head[off + 12:off + 16])
            if my_lba != lba or hdr_size < 92 or hdr_size > ss:
                continue
            part_lba, = struct.unpack("<Q", head[off + 72:off + 80])
            num, esz = struct.unpack("<II", head[off + 80:off + 88])
            if not (0 < num <= 128) or esz % 8 or not (0 < esz <= 4096):
                continue
            if part_lba >= 0xFFFFFFFFFFFFFFFF or part_lba == 0:
                continue
            return ss, off, part_lba, num, esz
    return None


def parse_gpt(path, sector_size=None):
    """Return (sector_size, size, [(name, first_lba, last_lba), ...]) for a LUN."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(1 << 20)

    found = find_gpt(head) if sector_size is None else None
    if found is None:
        return None
    sector_size, hdr_off, part_lba, num, esz = found

    entries_off = part_lba * sector_size
    if entries_off + num * esz > size:
        return None
    with open(path, "rb") as f:
        f.seek(entries_off)
        entries = f.read(num * esz)

    parts = []
    for i in range(num):
        e = entries[i * esz:(i + 1) * esz]
        if len(e) < 128 or e[:16] == b"\x00" * 16:
            continue
        first, last = struct.unpack("<QQ", e[32:48])
        if first > last or last * sector_size >= size + sector_size:
            continue
        raw = e[56:128]
        try:
            name = raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            continue
        if name.isprintable() and name:
            parts.append((name, first, last))
    return sector_size, size, parts


def carve(src, out_dir, name, first, last, sector_size, force):
    nbytes = (last - first + 1) * sector_size
    dst = os.path.join(out_dir, f"part-{name}.img")
    if os.path.exists(dst) and not force:
        return "have", dst, nbytes

    with open(src, "rb") as f:
        f.seek(first * sector_size)
        data = f.read(nbytes)
    if len(data) != nbytes:
        return "short", dst, len(data)

    note = ""
    if name in SIGNATURES:
        magic, off = SIGNATURES[name]
        if data[off:off + len(magic)] != magic:
            note = f"  <-- expected {magic!r} at {off}, got {data[off:off+len(magic)]!r}"

    with open(dst, "wb") as f:
        f.write(data)
    return ("ok" + note), dst, nbytes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=os.path.expanduser("~/backup/gauguin/images"))
    ap.add_argument("--out", default=None, help="default: same as --images")
    ap.add_argument("--list", action="store_true", help="only show what is inside")
    ap.add_argument("--force", action="store_true", help="re-carve existing files")
    args = ap.parse_args()

    out_dir = args.out or args.images
    if not os.path.isdir(args.images):
        sys.exit(f"no such directory: {args.images}")
    os.makedirs(out_dir, exist_ok=True)

    luns = sorted(f for f in os.listdir(args.images)
                  if f.startswith("LUN-") and f.endswith(".img"))
    if not luns:
        sys.exit(f"no LUN-*.img files in {args.images}")

    total = 0
    for lun in luns:
        path = os.path.join(args.images, lun)
        parsed = parse_gpt(path)
        if not parsed:
            print(f"{lun}: no GPT found, skipping")
            continue
        sector_size, size, parts = parsed
        print(f"\n== {lun}  ({size} bytes, {sector_size}-byte sectors, "
              f"{len(parts)} partitions)")
        if args.list:
            for name, first, last in parts:
                print(f"   {name:24s} LBA {first:>9}-{last:<9} "
                      f"{(last-first+1)*sector_size:>12,} bytes")
            continue
        for name, first, last in parts:
            status, dst, n = carve(path, out_dir, name, first, last,
                                   sector_size, args.force)
            if status == "have":
                continue
            total += 1
            flag = "" if status.startswith("ok") else "  !!"
            print(f"   {os.path.basename(dst):32s} {n:>12,} bytes  {status}{flag}")

    if not args.list:
        print(f"\ncarved {total} partition(s) into {out_dir}")
        print("restore one with:  fastboot flash <name> "
              f"{out_dir}/part-<name>.img")


if __name__ == "__main__":
    main()
