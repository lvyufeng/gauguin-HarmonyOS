#!/usr/bin/env python3
"""Recover the UEFI firmware volume (and its FFS files) from a Qualcomm XBL image.

XBL on this platform is an aarch64 ELF. One of its PT_LOAD segments is a
PI-spec EFI_FIRMWARE_VOLUME containing the entire signed DXE driver set. Inside
that FV there is also a gzip-compressed nested FV — that nested volume is where
the real drivers live.

This script walks: ELF -> FV -> FFS -> sections, gunzips the nested volume, and
dumps every driver and config file to an output directory.

Usage:  xbl_extract.py XBL.img OUTDIR
"""
import gzip
import os
import struct
import sys
import uuid

FFS_TYPES = {
    0x01: "RAW", 0x02: "FREEFORM", 0x03: "SECURITY_CORE", 0x04: "PEI_CORE",
    0x05: "DXE_CORE", 0x06: "PEIM", 0x07: "DRIVER", 0x08: "COMBINED",
    0x09: "APPLICATION", 0x0A: "MM", 0x0B: "FV_IMAGE", 0x0C: "COMBINED_MM_DXE",
    0x0E: "MM_STANDALONE", 0x0F: "MM_CORE_STANDALONE", 0xF0: "PAD",
}
# Sections that carry a human-readable name (UI-ish), in preference order.
NAME_SECTIONS = (0x15, 0x14, 0x13, 0x18)
RAW_SECTION = 0x19
PE32_SECTION = 0x10
GUIDED_SECTION = 0x02


def ffs_files(fv, start, end):
    """Yield (offset, guid, ftype, size) for each FFS file in an FV."""
    p = start
    while p + 24 <= end:
        name = fv[p:p + 16]
        if name == b"\xff" * 16 or name == b"\x00" * 16:
            p += 8
            continue
        ftype = fv[p + 18]
        size = struct.unpack("<I", fv[p + 20:p + 24])[0] & 0xFFFFFF
        if ftype not in FFS_TYPES or size < 24 or p + size > end:
            p += 8
            continue
        yield p, uuid.UUID(bytes_le=name), ftype, size
        p = (p + size + 7) & ~7


def sections(fv, start, end):
    p = start
    while p + 4 <= end:
        size = struct.unpack("<I", fv[p:p + 4])[0] & 0xFFFFFF
        stype = fv[p + 3]
        if size < 4 or p + size > end:
            return
        yield p, stype, size
        p = (p + size + 3) & ~3


def file_name(fv, p, size):
    """Read the UI/name section of an FFS file, else ''."""
    best = ""
    for sp, stype, ssize in sections(fv, p + 24, p + size):
        if stype in NAME_SECTIONS:
            txt = fv[sp + 4:sp + ssize].decode("utf-16-le", errors="ignore")
            txt = "".join(c for c in txt if 32 <= ord(c) < 127).strip()
            # Some drivers carry a stray single character in an earlier section;
            # prefer the longest plausible identifier-looking string.
            if len(txt) > len(best) and not txt.isdigit():
                best = txt
    return best


def file_raw(fv, p, size):
    """Largest RAW section of an FFS file."""
    best = b""
    for sp, stype, ssize in sections(fv, p + 24, p + size):
        if stype == RAW_SECTION and ssize - 4 > len(best):
            best = fv[sp + 4:sp + ssize]
    return best


def file_pe32(fv, p, size):
    """First PE32 section of an FFS file, if it is a driver/application."""
    for sp, stype, ssize in sections(fv, p + 24, p + size):
        if stype == PE32_SECTION:
            return fv[sp + 4:sp + ssize]
    return b""


def safe_name(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def find_fv_offsets(d):
    out = []
    i = 0
    while True:
        j = d.find(b"_FVH", i)
        if j < 0:
            return out
        out.append(j - 0x28)  # _FVH lives at offset 0x28 in the FV header
        i = j + 1


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    xbl, outdir = argv[1], argv[2]
    os.makedirs(outdir, exist_ok=True)

    d = open(xbl, "rb").read()

    if d[:4] != b"\x7fELF":
        sys.exit("not an ELF image")

    is64 = d[4] == 2
    if is64:
        phoff, = struct.unpack("<Q", d[0x20:0x28])
        phentsize, phnum = struct.unpack("<HH", d[0x36:0x3A])
    else:
        phoff, = struct.unpack("<I", d[0x1C:0x20])
        phentsize, phnum = struct.unpack("<HH", d[0x2A:0x2E])

    segs = []
    for i in range(phnum):
        o = phoff + i * phentsize
        if is64:
            p_type, p_flags, p_off, p_va, _pa, p_fsz, _msz, _al = \
                struct.unpack("<IIQQQQQQ", d[o:o + 56])
        else:
            p_type, p_off, p_va, _pa, p_fsz, _msz, p_flags, _al = \
                struct.unpack("<IIIIIIII", d[o:o + 32])
        if p_type == 1 and p_fsz:  # PT_LOAD
            segs.append((p_off, p_fsz, p_va, p_flags))

    print(f"{xbl}: ELF{'64' if is64 else '32'}, {len(segs)} PT_LOAD segments")

    # The firmware volume is whichever segment actually contains an FV header.
    extracted = 0
    for seg_off, seg_sz, seg_va, seg_flags in segs:
        seg = d[seg_off:seg_off + seg_sz]
        for fv_off in find_fv_offsets(seg):
            if fv_off < 0 or fv_off + 0x38 > len(seg):
                continue
            fsguid = uuid.UUID(bytes_le=seg[fv_off + 0x10:fv_off + 0x20])
            fvlen, = struct.unpack("<Q", seg[fv_off + 0x20:fv_off + 0x28])
            hlen, = struct.unpack("<H", seg[fv_off + 0x30:fv_off + 0x32])
            if fvlen < 0x1000 or fv_off + fvlen > len(seg):
                continue
            if str(fsguid) != "8c8ce578-8a3d-4f1c-9935-896185c32dd3":
                continue
            print(f"  FV at file 0x{seg_off + fv_off:x} va=0x{seg_va + fv_off:x} "
                  f"len=0x{fvlen:x} hdrlen={hlen}")
            fv = seg[fv_off:fv_off + fvlen]
            with open(os.path.join(outdir, "fv-outer.bin"), "wb") as fh:
                fh.write(fv)

            for p, guid, ftype, size in ffs_files(fv, hlen, len(fv)):
                name = file_name(fv, p, size)
                tag = name or str(guid)
                print(f"    {FFS_TYPES[ftype]:14} size={size:#010x} {tag}")

                # A GUID-defined section is the gzip-compressed nested FV.
                for sp, stype, ssize in sections(fv, p + 24, p + size):
                    if stype != GUIDED_SECTION:
                        continue
                    dofs, = struct.unpack("<H", fv[sp + 20:sp + 22])
                    payload = fv[sp + dofs:sp + ssize]
                    if payload[:2] != b"\x1f\x8b":
                        continue
                    inner = gzip.decompress(payload)
                    path = os.path.join(outdir, "fv-inner.bin")
                    with open(path, "wb") as fh:
                        fh.write(inner)
                    print(f"      -> nested FV decompressed: {len(inner):,} bytes")
                    nhlen, = struct.unpack("<H", inner[8 + 0x30:8 + 0x32])
                    for p2, g2, t2, s2 in ffs_files(inner, 8 + nhlen, len(inner)):
                        nm2 = file_name(inner, p2, s2) or str(g2)
                        # Keep the whole FFS file so the volume can be rebuilt,
                        # plus the bare PE32 image when there is one.
                        with open(os.path.join(outdir, f"{safe_name(nm2)}.ffs"), "wb") as fh:
                            fh.write(inner[p2:p2 + s2])
                        pe = file_pe32(inner, p2, s2)
                        if pe:
                            with open(os.path.join(outdir, f"{safe_name(nm2)}.efi"), "wb") as fh:
                                fh.write(pe)
                        raw = file_raw(inner, p2, s2)
                        if raw:
                            with open(os.path.join(outdir, f"{safe_name(nm2)}.raw"), "wb") as fh:
                                fh.write(raw)
                        extracted += 1
                        kind = f"pe32 {len(pe)}B" if pe else (f"raw {len(raw)}B" if raw else "")
                        print(f"      {FFS_TYPES[t2]:14} size={s2:#010x} {nm2}  {kind}")

    print(f"\n{extracted} nested FFS files written to {outdir}/")


if __name__ == "__main__":
    main(sys.argv)
