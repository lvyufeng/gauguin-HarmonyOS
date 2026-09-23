#!/usr/bin/env python3
"""List what is actually inside the firmware volume of a built `Mu-<device>.img`.

This exists because the obvious check is misleading. The `.img` is an Android
boot image whose kernel payload is `gzip(BootShim.bin + SILICIUM_UEFI.fd)`, and
`SILICIUM_UEFI.fd` is **not** the firmware volume — it is `FVMAIN_COMPACT`, a
0x300000-byte volume whose only real content is `FVMAIN`, a ~7 MB volume, held
inside a single compressed GUIDed section.

So every driver name and every driver GUID is inside a compressed stream. A
walk of the outer volume reports two files and none of the drivers, which
looks exactly like a firmware that shipped empty, and is not.

    python3 tools/fv-inventory.py Mu-gauguin.img            # list everything
    python3 tools/fv-inventory.py Mu-gauguin.img --usb      # just the USB stack
    python3 tools/fv-inventory.py --verify uefi/Binaries/gauguin ...
    python3 tools/fv-inventory.py Mu-gauguin.img --against Build/.../FVMAIN.Fv.txt

`--verify DIR` compares the driver .inf FILE_GUIDs under DIR against the ones
really present, which is the check that answers "is the driver I packaged
actually in the image" - and it is answered by GUID, not by name, because the
names are inside compressed sections and searching the raw bytes for them
silently finds nothing.

`--against MAP` compares the walk against GenFv's own `FVMAIN.Fv.txt`, which is
the half of the check this tool cannot make about itself: it is an independent
record of what the build put where, so it catches a reader that is wrong in a way
that still looks plausible. It exits nonzero on any mismatch, and mismatching a
map from a different platform is the test that it can fail at all.
"""
import argparse
import glob
import os
import re
import struct
import sys
import zlib

# EFI_FIRMWARE_VOLUME_HEADER. The offsets are from the PI spec rather than
# guessed: guessing HeaderLength as 0x2c instead of 0x30 costs an hour of
# "the volume is empty".
FV_FVLEN       = 0x20
FV_SIG         = 0x28
FV_HEADERLEN   = 0x30

FX_FILE_DATA_VALID = 0xF8          # 0x07 in a volume with erase polarity set

SECTION_USER_INTERFACE = 0x15
SECTION_GUID_DEFINED   = 0x02
SECTION_FV_IMAGE       = 0x17

FILE_FIRMWARE_VOLUME_IMAGE = 0x0B    # 0x07 is EFI_FV_FILETYPE_DRIVER
FILE_SECURITY_CORE         = 0x03

COMPRESSION_GUIDS = {
    "EE4E5898-3914-4259-9D6E-DC7BD79403CF": "LZMA",
    "A31280AD-481E-41B6-95E8-127F4C984779": "Tiano",
    "D42AE6BD-1352-4BFB-909A-CA72A6EAE889": "LZMAF86",
}


def guid_str(b):
    return (f"{struct.unpack('<I', b[0:4])[0]:08X}-"
            f"{struct.unpack('<H', b[4:6])[0]:04X}-"
            f"{struct.unpack('<H', b[6:8])[0]:04X}-"
            f"{b[8:10].hex().upper()}-{b[10:16].hex().upper()}")


def _walk_from(fv, start, end):
    out, off = [], start
    while off + 24 <= end:
        g = fv[off:off + 16]
        if g == b"\xff" * 16 or g == b"\x00" * 16:
            return out
        size = fv[off + 20] | (fv[off + 21] << 8) | (fv[off + 22] << 16)
        if size < 24 or off + size > end:
            return out
        out.append((g, fv[off + 18], size, off, fv[off + 23]))
        # GenFv pads each file to the next 8-byte boundary; the size field does
        # not include that padding. Stepping by `size` alone leaves the next
        # header 4 bytes early, which yields a GUID of
        # FFFFFFFF-CB7F-D6A2-186A-2F4EB43B9920 - the previous file's last four
        # bytes glued to the next GUID - and the walk ends after two files.
        off = (off + size + 7) & ~7
    return out


def fv_files(fv):
    """[(guid, type, size, offset, state)] for every FFS file in a volume.

    The file area does not always begin at `HeaderLength`. When the volume has
    an extension header (ExtHeaderOffset != 0, which GenFv emits on the volumes
    it wraps around a compressed image), the files begin after it — and the
    end of the extension header is only 4-byte aligned, while FFS files must
    be 8-byte aligned, so GenFv pads to the next 8.

    Both details matter and both fail silently in the same way: start four
    bytes early and the first GUID reads as `ffffffffe70e51fcdcffd411bd410080`,
    which is not a GUID, so the walk returns nothing and the volume looks
    empty. That is a bug in the reader, not a hole in the firmware.

    For FVMAIN of the gauguin build: HeaderLength 0x48, ExtHeaderOffset 0x60,
    ExtHeaderSize 0x14, so files start at align8(0x60 + 0x14) = 0x78 — which is
    exactly where the build's own `FVMAIN.Fv.txt` map puts the first file.
    """
    base = fv.find(b"_FVH") - FV_SIG
    if base < 0:
        return []
    fvlen, = struct.unpack("<Q", fv[base + FV_FVLEN:base + FV_FVLEN + 8])
    hlen, = struct.unpack("<H", fv[base + FV_HEADERLEN:base + FV_HEADERLEN + 2])
    ext_off, = struct.unpack("<H", fv[base + 0x34:base + 0x36])
    end = min(base + fvlen, len(fv))

    starts = [base + hlen]
    if ext_off:
        e = base + ext_off
        ext_size, = struct.unpack("<I", fv[e + 16:e + 20])
        starts.insert(0, (e + ext_size + 7) & ~7)

    best = []
    for s in starts:
        got = _walk_from(fv, s, end)
        if len(got) > len(best):
            best = got
    return best


def sections(blob):
    """[(type, body)] for the section stream that starts `blob`."""
    out, off = [], 0
    while off + 4 <= len(blob):
        sz = blob[off] | (blob[off + 1] << 8) | (blob[off + 2] << 16)
        st = blob[off + 3]
        if sz < 4 or off + sz > len(blob):
            break
        out.append((st, blob[off + 4:off + sz]))
        off += sz
    return out


def gui_name(blob):
    for st, body in sections(blob):
        if st == SECTION_USER_INTERFACE:
            return body.decode("utf-16-le", "replace").split("\x00")[0]
    return None


def decompress_guided(body):
    """Return the decompressed payload of an EFI_GUID_DEFINED section, or None.

    `DataOffset` is counted from the start of the section header, which
    includes the 4-byte EFI_COMMON_SECTION_HEADER that the caller has already
    stripped. Reading the stream from `body[DataOffset]` instead of
    `body[DataOffset - 4]` starts four bytes late and the LZMA decoder rejects
    it — which is why this returns None rather than a wrong answer.
    """
    if len(body) < 20:
        return None
    g = guid_str(body[0:16])
    data_off, = struct.unpack("<H", body[16:18])
    if COMPRESSION_GUIDS.get(g) not in ("LZMA", "LZMAF86"):
        return None
    start = data_off - 4
    if start < 0 or start >= len(body):
        return None
    stream = body[start:]
    import lzma
    # EDK2 writes an LZMA-alone stream (5 props bytes + u64 size + data). The
    # decoded buffer carries an extra 8-byte length prefix, so the volume is
    # located by its own magic rather than assumed to start at byte 0.
    try:
        out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(stream)
    except Exception:
        try:
            out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(stream[8:])
        except Exception:
            return None
    i = out.find(b"_FVH")
    return out[i - FV_SIG:] if i >= FV_SIG else out


def unpack(img):
    """Walk image -> FD -> FVMAIN -> ([(guid, type, size, name, state)], fv_len)."""
    d = open(img, "rb").read()
    if d[:8] != b"ANDROID!":
        sys.exit(f"{img}: not an Android boot image")
    ks, ps = struct.unpack("<I", d[8:12])[0], struct.unpack("<I", d[36:40])[0]
    # Both payload shapes reach here and both are legal: a gzip package, which is
    # what Mu-gauguin-stock-gzip.img carries, and a raw one, which is what
    # Mu-gauguin-stock-none.img carries. Reading the magic rather than assuming
    # gzip is what keeps this tool usable on the uncompressed half of the pair -
    # and the pair is the experiment, so a tool that could read only one half of
    # it would be checking the thing that is easy and skipping the other.
    blob = d[ps:ps + ks]
    if blob[:2] == b"\x1f\x8b":
        do = zlib.decompressobj(16 + zlib.MAX_WBITS)
        payload = do.decompress(blob)
    else:
        payload = blob
    if payload[:2] != b"\x81\x03":          # BootShim's adr/b instructions
        sys.exit("payload does not start with BootShim")

    # BootShim.bin is 112 bytes with REQUIRES_KERNEL_HEADER=1 (2 instructions +
    # 6 .quads); the FD follows. Find it by the FV magic rather than by a fixed
    # offset, so a change in that header size is not silently wrong.
    sig = payload.find(b"_FVH")
    fd = payload[sig - FV_SIG:]
    outer = fv_files(fd)
    print(f"{os.path.basename(img)}: payload {len(payload):#x}, "
          f"FD (FVMAIN_COMPACT) {len(fd):#x}, {len(outer)} top-level FFS files")

    for g, typ, size, off, state in outer:
        body = fd[off + 24:off + size]
        print(f"  file {guid_str(g)}  type {typ:#04x} size {size:#x} "
              f"state {state:#04x} ({'valid' if state == FX_FILE_DATA_VALID else 'NOT VALID'})")
        if typ != FILE_FIRMWARE_VOLUME_IMAGE:
            continue
        for st, sbody in sections(body):
            if st != SECTION_GUID_DEFINED:
                continue
            inner = decompress_guided(sbody)
            if inner is None:
                print(f"    GUIDed section {guid_str(sbody[0:16])}: not decompressed")
                continue
            print(f"    -> inner FV {len(inner):#x} bytes")
            out, offs = [], []
            for g2, t2, s2, o2, st2 in fv_files(inner):
                nm = gui_name(inner[o2 + 24:o2 + s2])
                out.append((guid_str(g2), t2, s2, nm, st2))
                offs.append(o2)
            return out, len(inner), offs
    return [], None, []


def compare_map(files, offsets, map_path, fv_len=None):
    """Check this image's FVMAIN against GenFv's own map of the volume it built.

    `Build/.../FV/FVMAIN.Fv.txt` is written by GenFv as it lays the volume out,
    so it is the build's record of what it put where - an independent opinion
    about the same bytes, and the only one available that was not produced by
    this reader. Agreeing with it on every offset and every GUID is what makes
    "the volume contains 122 files" a measurement rather than a claim this tool
    makes about itself. Disagreement is worth more than agreement: the walk here
    has already been wrong twice in ways that still produced a plausible-looking
    list (see `_walk_from` and `fv_files`), and both times the map would have
    caught it.
    """
    want, total = [], None
    for line in open(map_path):
        line = line.strip()
        if line.startswith("EFI_FV_TOTAL_SIZE"):
            total = int(line.split("=")[1], 16)
            continue
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith("0x"):
            want.append((int(parts[0], 16), parts[1].upper()))
    got = [(o, g) for (g, _, _, _, _), o in zip(files, offsets)]

    bad = 0
    if fv_len is not None and total is not None and total != fv_len:
        print(f"  MISMATCH: map says the volume is {total:#x}, "
              f"the image's is {fv_len:#x}")
        bad += 1
    if len(want) != len(got):
        print(f"  MISMATCH: map lists {len(want)} files, the walk found {len(got)}")
        bad += 1
    for i, ((wo, wg), (go, gg)) in enumerate(zip(want, got)):
        if wo != go or wg != gg:
            print(f"  MISMATCH at #{i}: map {wo:#010x} {wg}  walk {go:#010x} {gg}")
            bad += 1
    if bad:
        print(f"  {bad} mismatch(es) against {os.path.basename(map_path)}")
        return False
    print(f"  matches {os.path.basename(map_path)}: "
          f"{len(want)} offsets and GUIDs, zero mismatches")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?")
    ap.add_argument("--verify", metavar="DIR",
                    help="a Binaries/<device> tree whose driver .inf FILE_GUIDs "
                         "to check against the image")
    ap.add_argument("--usb", action="store_true", help="only USB-related files")
    ap.add_argument("--against", metavar="FVMAIN.Fv.txt",
                    help="compare this image's FVMAIN against GenFv's own map "
                         "of the volume it built")
    args = ap.parse_args()
    if not args.image:
        sys.exit(__doc__)

    files, fv_len, offsets = unpack(args.image)
    print(f"\nFVMAIN: {len(files)} FFS files, "
          f"{sum(s for _, _, s, _, _ in files):#x} bytes of file headers+data")

    if args.against:
        if not compare_map(files, offsets, args.against, fv_len):
            sys.exit(1)
        return

    if args.usb:
        for g, t, s, n, st in files:
            if n and re.search(r"usb|dwc3|fn", n, re.I):
                print(f"  {n:28s} type {t:#04x} size {s:>7,}  {g}")
        return

    if args.verify:
        # Key by the .inf file name, not by its directory: two drivers ship from
        # the same package (TzDxe/TzDxeLA.inf and TzDxe/ScmDxeLA.inf), so keying
        # by directory silently drops one and the total looks like 54 of 55.
        inf_guids = {}
        for inf in glob.glob(os.path.join(args.verify, "QcomPkg", "Drivers", "*", "*.inf")):
            m = re.search(r"FILE_GUID\s*=\s*([0-9A-Fa-f-]+)", open(inf).read())
            if m:
                inf_guids[os.path.basename(inf)] = m.group(1).upper()
        present = {g for g, _, _, _, _ in files}
        have = sorted(k for k, v in inf_guids.items() if v in present)
        miss = sorted(k for k, v in inf_guids.items() if v not in present)
        print(f"\npackaged Qcom drivers: {len(inf_guids)}  "
              f"in FVMAIN: {len(have)}  absent: {len(miss)}")
        if miss:
            print("  absent:", ", ".join(miss))
        return

    named = sorted((n for _, _, _, n, _ in files if n))
    print(f"\n{len(named)} with a UI name; the rest are unnamed sections/PEIMs")
    for n in named:
        print("  ", n)


if __name__ == "__main__":
    main()
