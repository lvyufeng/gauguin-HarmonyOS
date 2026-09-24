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
    python3 tools/fv-inventory.py Mu-gauguin.img --dump-fvmain /tmp/FVMAIN.Fv

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
import hashlib
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
SECTION_RAW            = 0x19

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
    """[(type, body)] for the section stream that starts `blob`.

    Sections are padded to 4-byte boundaries (PI 2.3.1), so the next header is
    at align4(off + size) and not at off + size. Stepping by `size` alone
    drifts: the sizes are rounded up to 4 anyway for most section bodies, but
    not for the odd-length ones, and after one odd section every following
    header is read four bytes early - which still yields plausible-looking
    section types, so it fails silently. Measured on FVMAIN.Fv: stepping by
    `size` reports 83 sections across 123 files; stepping by align4 reports
    123 files' worth, and every file gains the trailing section that the
    unaligned walk had been losing.
    """
    out, off, n = [], 0, len(blob)
    while off + 4 <= n:
        sz = blob[off] | (blob[off + 1] << 8) | (blob[off + 2] << 16)
        st = blob[off + 3]
        if sz < 4 or off + sz > n:
            break
        out.append((st, blob[off + 4:off + sz]))
        off = (off + sz + 3) & ~3
    return out


def acpi_sections(files, offsets, inner):
    """[(offset, sig, body)] for every ACPI table in the volume.

    A table reaches the volume as a RAW section (`0x19`) of the `AcpiTables` FFS
    file, one section per `ASL|` entry in `AcpiTables.inf`, and `AcpiTableDxe`
    finds them by scanning for sections whose first four bytes are a table
    signature. So this walks every file's sections and keeps the raw ones that
    start with four printable ASCII characters, which is what the firmware does
    - keying on the `AcpiTables` FILE_GUID would work today and would quietly
    stop working the day a table ships from somewhere else.

    The offset reported is the table's own first byte, in FVMAIN coordinates,
    which is the offset a readback quotes ("the `DSDT` sits at `0x54d4c8`").
    """
    out = []
    for (g, t, s, nm, st), off in zip(files, offsets):
        body = inner[off + 24:off + s]
        pos, n = 0, len(body)
        while pos + 4 <= n:
            sz = body[pos] | (body[pos + 1] << 8) | (body[pos + 2] << 16)
            st2 = body[pos + 3]
            if sz < 4 or pos + sz > n:
                break
            sbody = body[pos + 4:pos + sz]
            if st2 == SECTION_RAW and len(sbody) >= 36:
                sig = sbody[:4]
                # An ACPI table's length field covers exactly the table, and the
                # table is the whole section body. Requiring that is what keeps a
                # .bmp logo out of this list: its first four bytes are printable
                # too, and without the length check it reports as a 1-byte table.
                length, = struct.unpack_from("<I", sbody, 4)
                if length == len(sbody) and all(
                        0x41 <= c <= 0x5A or 0x30 <= c <= 0x39 or c == 0x20 for c in sig):
                    out.append((off + 24 + pos + 4, sig.decode("ascii"), sbody))
            pos = (pos + sz + 3) & ~3
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


def fvmain_of_fd(fd, verbose=True):
    """Walk an FD (FVMAIN_COMPACT) -> (files, fv_len, offsets, inner FV bytes).

    Split out of `unpack` because `tools/apriori-order.py` needs the same walk
    and the same decompressed volume, and because the descent - not the payload
    wrapper - is the part with the padding rules in it (see `_walk_from` and
    `fv_files`). A second copy of this would be a second set of those rules.
    """
    outer = fv_files(fd)
    if verbose:
        print(f"FD (FVMAIN_COMPACT) {len(fd):#x}, {len(outer)} top-level FFS files")

    for g, typ, size, off, state in outer:
        body = fd[off + 24:off + size]
        if verbose:
            print(f"  file {guid_str(g)}  type {typ:#04x} size {size:#x} "
                  f"state {state:#04x} ({'valid' if state == FX_FILE_DATA_VALID else 'NOT VALID'})")
        if typ != FILE_FIRMWARE_VOLUME_IMAGE:
            continue
        for st, sbody in sections(body):
            if st != SECTION_GUID_DEFINED:
                continue
            inner = decompress_guided(sbody)
            if inner is None:
                if verbose:
                    print(f"    GUIDed section {guid_str(sbody[0:16])}: not decompressed")
                continue
            if verbose:
                print(f"    -> inner FV {len(inner):#x} bytes")
            out, offs = [], []
            for g2, t2, s2, o2, st2 in fv_files(inner):
                nm = gui_name(inner[o2 + 24:o2 + s2])
                out.append((guid_str(g2), t2, s2, nm, st2))
                offs.append(o2)
            return out, len(inner), offs, inner
    return [], None, [], None


def unpack(img):
    """Walk image -> FD -> FVMAIN -> ([(guid, type, size, name, state)], fv_len,
    offsets, inner FV bytes)."""
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
    print(f"{os.path.basename(img)}: payload {len(payload):#x}")
    return fvmain_of_fd(fd)


def guid_key(s):
    """A GUID reduced to what survives being read off a photograph.

    Case and hyphens carry no information, so both are dropped, and what is left
    is the 32 hex digits. Everything below compares on this and never on the
    printable form, because the printable form is what a human transcribes.
    """
    return re.sub(r"[^0-9a-f]", "", s.lower())


def roster(files, offsets, inner):
    """[(guid string, type, size, name)] - every FFS file, named or not.

    This exists for one line of the panel. `P2Tick` prints
    `K <n> <phase><why> <i>/<j> free=<pages> %g` (`Dispatcher.c:599-628`), and the
    `%g` is `DriverEntry->FileName` - the driver's FILE_GUID. Until this session
    the row above it named the same driver in English, because `CoreLoadPeImage`
    printed `Loading driver at 0x... EntryPoint=0x... <Name>.efi`; that print is
    now silenced along with the PDB name beside it (`Image/Image.c:890-937`), so
    **on the current build a `K` row identifies a driver by GUID and by nothing
    else**. The GUID is 36 characters of hexadecimal on a 90-column panel, and
    the mapping from it to a name lives in the firmware volume, not on the screen.
    So it is read out of the volume, here.

    A UI-section name is the `.inf`'s `BASE_NAME`-ish label the build put in the
    file, which is the same string the old `Loading driver at` row would have
    shown minus the `.efi`. Files with no UI section keep an empty name rather
    than being omitted: "this GUID is in the volume and has no name" is a
    different answer from "this GUID is not in the volume", and conflating them
    is how a mistyped digit becomes a wrong conclusion.
    """
    out = []
    for (g, t, s, n, _st), o in zip(files, offsets):
        nm = n or gui_name(inner[o + 24:o + s]) or ""
        out.append((g, t, s, nm))
    return out


def resolve_guid(query, rows, limit=5):
    """[(distance, guid, type, size, name)] best-first for a transcribed GUID.

    Ranked, not matched, and the distance is printed. A reader copying 32 hex
    digits off a photograph will sometimes get one wrong, and the failure that
    matters is not "not found" - it is a *near* GUID that resolves confidently to
    the wrong driver. So an exact match is distance 0 and anything else carries
    its number of differing digits, which makes a 1-digit answer legible as the
    guess it is instead of looking like the answer.
    """
    want = guid_key(query)
    scored = []
    for g, t, s, nm in rows:
        have = guid_key(g)
        if len(have) != 32:
            continue
        if len(want) == len(have):
            d = sum(1 for a, b in zip(want, have) if a != b)
        else:
            d = 32  # wrong length: cannot be compared digit by digit
        scored.append((d, g, t, s, nm))
    scored.sort(key=lambda r: (r[0], r[4]))
    return scored[:limit]


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
    ap.add_argument("--dump-fvmain", metavar="PATH",
                    help="write the decompressed inner FVMAIN to PATH and print its "
                         "sha256. This is the one artifact of a build that is "
                         "bit-identical across rebuilds: `Sec.efi` carries "
                         "`__TIME__`/`__DATE__` and lives in the *outer* "
                         "`FVMAIN_COMPACT`, not here, so the FD and every payload "
                         "built from it differ from build to build while this does "
                         "not")
    ap.add_argument("--against", metavar="FVMAIN.Fv.txt",
                    help="compare this image's FVMAIN against GenFv's own map "
                         "of the volume it built")
    ap.add_argument("--roster", action="store_true",
                    help="every FFS file as GUID, type, size, name - the table a "
                         "`K` row's %%g is read against")
    ap.add_argument("--acpi", action="store_true",
                    help="every ACPI table in the volume as offset, signature, "
                         "length and checksum - the readback a change to "
                         "tools/acpi/gauguin.asl has to pass")
    ap.add_argument("--name", metavar="GUID",
                    help="resolve a transcribed GUID (case- and hyphen-tolerant, "
                         "and ranked rather than matched)")
    args = ap.parse_args()
    if not args.image:
        sys.exit(__doc__)

    files, fv_len, offsets, inner = unpack(args.image)

    if args.dump_fvmain:
        with open(args.dump_fvmain, "wb") as fh:
            fh.write(inner)
        print(f"{args.dump_fvmain}: {len(inner):,} bytes, sha256 "
              f"{hashlib.sha256(inner).hexdigest()}")
        return

    if args.roster or args.name:
        rows = roster(files, offsets, inner)
        if args.name:
            hits = resolve_guid(args.name, rows)
            if not hits:
                print(f"\nno FFS file in this volume has a GUID near {args.name}")
                sys.exit(1)
            print(f"\n{args.name!r} against {len(rows)} FFS files:")
            for d, g, t, s, nm in hits:
                verdict = "exact" if d == 0 else f"{d} digit(s) differ"
                star = "  <- exact" if d == 0 else ""
                print(f"  {verdict:>16s}  {g}  type {t:#04x} size {s:>8,}  "
                      f"{nm or '(no UI name)'}{star}")
            if hits[0][0] != 0:
                print("  -> none is exact. A 1-digit difference is as likely to be"
                      " a mistranscription\n     as a real neighbour; re-read the"
                      " row before trusting the top one.")
            return
        print(f"\nFVMAIN roster: {len(rows)} FFS files")
        unnamed = sum(1 for _g, _t, _s, nm in rows if not nm)
        for g, t, s, nm in rows:
            print(f"  {g}  type {t:#04x} size {s:>8,}  {nm or '(no UI name)'}")
        print(f"\n{len(rows) - unnamed} named, {unnamed} with no UI section - a"
              f" GUID that appears here with no name is still in the volume")
        return

    if args.acpi:
        tabs = acpi_sections(files, offsets, inner)
        if not tabs:
            print("\nno ACPI tables in this volume")
            sys.exit(1)
        print(f"\nACPI tables: {len(tabs)}")
        bad = []
        for off, sig, body in tabs:
            length, = struct.unpack_from("<I", body, 4)
            ok = (sum(body) & 0xFF) == 0
            if not ok:
                bad.append(sig)
            print(f"  {off:#010x}  {sig}  {length:>7,} bytes  "
                  f"checksum {'valid' if ok else 'NOT valid'}")
        if bad:
            print(f"  {len(bad)} of {len(tabs)} do not checksum: {', '.join(bad)}."
                  " Before AcpiTableDxe runs that is expected of FACP and FACS and"
                  " only those two - AcpiTableDxe writes the DSDT and FACS addresses"
                  " into FACP and recomputes it, and FACS has no checksum field at"
                  " all. Anything else in that list is a real fault.")
        return

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
