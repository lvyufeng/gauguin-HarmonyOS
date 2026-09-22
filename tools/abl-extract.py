#!/usr/bin/env python3
"""Extract the PE image ABL actually runs, out of the `abl` partition.

There is a wrong answer available here and it is the obvious one. `abl` is an
ELF32 / ARM container, so reading its header says "ABL is a 32-bit ARM
executable" - and an earlier version of docs/07 drew exactly that conclusion and
built a hypothesis on it. It is wrong. The ELF32 header is packaging; the
payload is:

    abl (ELF32/ARM)
      +-- PT_LOAD vaddr 0x9fa00000  <- the "ABOOT FV" region in uefiplat.cfg
            +-- LZMA stream at segment offset 0x78
                  +-- firmware volume, 917,704 bytes
                        +-- ONE PE32+ image, machine 0xAA64 (AArch64)

This pulls that PE out so it can be disassembled, and reports what it found
rather than assuming.

Usage:  tools/abl-extract.py [--abl part-abl.img] [--out work/abl_pe.bin]
"""
import argparse
import lzma
import os
import struct
import sys

LZMA_DICT_SIZES = (0x10000, 0x40000, 0x100000, 0x400000, 0x1000000, 0x4000000)
MACHINES = {0xAA64: "AArch64", 0x1C0: "ARM (AArch32)", 0x8664: "x86-64",
            0x14C: "x86", 0xAA64 | 0: "AArch64"}


def elf_load_segments(d):
    """[(vaddr, offset, filesz)] for an ELF32 or ELF64 image."""
    if d[:4] != b"\x7fELF":
        return None, None
    is64 = d[4] == 2
    if is64:
        e_phoff, = struct.unpack("<Q", d[0x20:0x28])
        e_phentsize, e_phnum = struct.unpack("<HH", d[0x36:0x3A])
        entry, = struct.unpack("<Q", d[0x18:0x20])
        machine, = struct.unpack("<H", d[0x12:0x14])
    else:
        e_phoff, = struct.unpack("<I", d[0x1C:0x20])
        e_phentsize, e_phnum = struct.unpack("<HH", d[0x2A:0x2E])
        entry, = struct.unpack("<I", d[0x18:0x1C])
        machine, = struct.unpack("<H", d[0x12:0x14])
    segs = []
    for k in range(e_phnum):
        o = e_phoff + k * e_phentsize
        if is64:
            p_type, p_flags = struct.unpack("<II", d[o:o + 8])
            p_offset, p_vaddr, _, p_filesz = struct.unpack("<QQQQ", d[o + 8:o + 40])
        else:
            p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack("<5I", d[o:o + 20])
        if p_type == 1:
            segs.append((p_vaddr, p_offset, p_filesz))
    return dict(kind="ELF64" if is64 else "ELF32", machine=machine,
                entry=entry, segs=segs), is64


def find_lzma(d, lo, hi):
    """First LZMA-alone stream in [lo,hi) that decompresses to something big."""
    for m in range(lo, min(hi, len(d)) - 5):
        if d[m] != 0x5D:
            continue
        dict_size, = struct.unpack("<I", d[m + 1:m + 5])
        if dict_size not in LZMA_DICT_SIZES:
            continue
        try:
            out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(d[m:])
        except Exception:
            continue
        if len(out) > 0x10000:
            return m, out
    return None, None


def find_pe(vol, target):
    """The PE32/PE32+ image in `vol` whose span covers `target`."""
    i = vol.find(b"MZ")
    while i >= 0:
        nxt = i + 1
        lfanew, = struct.unpack("<I", vol[i + 0x3C:i + 0x40]) if i + 0x40 <= len(vol) else (0xFFFFFFFF,)
        if not (0 < lfanew < 0x1000) or vol[i + lfanew:i + lfanew + 4] != b"PE\x00\x00":
            i = vol.find(b"MZ", nxt)
            continue
        coff = i + lfanew + 4
        machine, nsec, _, _, _, optsz, _ = struct.unpack("<HHIIIHH", vol[coff:coff + 20])
        opt = coff + 20
        magic, = struct.unpack("<H", vol[opt:opt + 2])
        isec = opt + optsz
        secs, span = [], 0
        for s in range(nsec):
            o = isec + s * 40
            name = vol[o:o + 8].rstrip(b"\x00").decode("latin1")
            vsz, va, rsz, raw = struct.unpack("<IIII", vol[o + 8:o + 24])
            secs.append((name, va, vsz, raw, rsz))
            span = max(span, raw + rsz)
        if i + span <= len(vol) and i <= target < i + span:
            return dict(base=i, machine=machine, magic=magic, secs=secs, span=span)
        i = vol.find(b"MZ", nxt)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--abl", default=os.path.expanduser(
        "~/backup/gauguin/images/part-abl.img"))
    ap.add_argument("--out", default="work/abl_pe.bin")
    ap.add_argument("--marker", default=b"Failed to load/authenticate boot image: %r",
                    help="string that must be inside the PE, as a sanity check")
    args = ap.parse_args()

    d = open(args.abl, "rb").read()
    print(f"{args.abl}: {len(d):,} bytes")
    elf, _ = elf_load_segments(d)
    if elf is None:
        sys.exit("not an ELF image")
    print(f"  container   {elf['kind']}, machine {elf['machine']:#x}, "
          f"entry {elf['entry']:#x}")
    for k, (va, off, fsz) in enumerate(elf["segs"]):
        print(f"  PT_LOAD[{k}] vaddr {va:#x} offset {off:#x} filesz {fsz:#x}")

    for va, off, fsz in elf["segs"]:
        if fsz < 0x1000:
            continue
        m, vol = find_lzma(d, off, off + fsz)
        if m is None:
            continue
        print(f"\n  LZMA stream at file offset {m:#x} "
              f"(segment vaddr {va + (m - off):#x}) -> {len(vol):,} bytes")
        tgt = vol.find(args.marker)
        if tgt < 0:
            print(f"  marker {args.marker!r} NOT in the volume - wrong stream?")
            continue
        print(f"  marker found at volume offset {tgt:#x}")
        pe = find_pe(vol, tgt)
        if pe is None:
            print("  no PE image covers the marker")
            continue
        name = MACHINES.get(pe["machine"], f"0x{pe['machine']:x}")
        print(f"\n  PE image at volume offset {pe['base']:#x}")
        print(f"    machine  {pe['machine']:#x}  ({name})")
        print(f"    magic    {pe['magic']:#x}  ({'PE32+' if pe['magic']==0x20b else 'PE32'})")
        print(f"    span     {pe['span']:#x}")
        for n, sva, vsz, sraw, srsz in pe["secs"]:
            print(f"      {n:8s} VA {sva:#010x} vsz {vsz:#x} raw {sraw:#x} rsz {srsz:#x}")

        blob = vol[pe["base"]:pe["base"] + pe["span"]]
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        open(args.out, "wb").write(blob)
        print(f"\n  wrote {args.out}  ({len(blob):,} bytes, raw PE, ImageBase 0)")
        print("  disassemble with:")
        print(f"    llvm-objdump -D --triple=aarch64 -b binary {args.out}")
        return

    sys.exit("no compressed volume with the expected marker was found")


if __name__ == "__main__":
    main()
