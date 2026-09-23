#!/usr/bin/env python3
"""Per-driver PE facts, and which of them separate the loads that succeeded from
the ones that did not.

The measurement to explain: the panel's `P2 SEQ` line reports 46 promoted
drivers as 19 `s` and 27 `L`, and the failures are not contiguous in any
physical or size ordering. The first hypothesis anyone reaches for is "some
property of the images", so this prints every header field for all 80 DRIVER
files and then, for each field, reports whether its value **separates** the two
sets - i.e. whether some value is taken only by `s` or only by `L`.

Measured on the payload of record: **no field separates them** - and the output
prints the two questions that fact is made of, because they are easy to
conflate.  The equality-class test ("does some value appear only under one
result?") comes out `disjoint` for the size fields and means nothing: every
driver has its own `SizeOfImage`, so of course no two share one.  What would be
a mechanism is a *threshold*, and the threshold column says `interleaved` for
every size field - `s` and `L` values overlap in range.  Everything else is
`shared`: all 80 files have ImageBase 0x0, `IMAGE_FILE_RELOCS_STRIPPED`
(Characteristics bit 0) is clear on all 80, and `SectionAlignment` is 0x1000
except for the Runtime family at 0x10000, which straddles the boundary in both
directions.  Concretely: `WatchdogTimer` fails at 20,580 B while `NpaDxe`
succeeds at 81,966 B, and `PdcDxe` and `ShmBridgeDxe` make the identical
36,864-byte request with opposite results.  The one field with any signal at all
is the presence of `.reloc`, and it is a partial mechanism, not the answer:
three of the 27 failures (`EmbeddedMonotonicCounter`, `RealTimeClock`,
`CapsuleRuntimeDxe`) have no `.reloc` and `ImageBase 0x0`, so
`CoreLoadPeImage` takes the `AllocateAddress`-at-zero path with no
`AllocateAnyPages` fallback and `CoreInternalAllocatePages` rejects `Start == 0`
with `EFI_NOT_FOUND` - while one of the 19 successes
(`StatusCodeHandlerRuntimeDxe`) is in the same state.

The PE is located through the FFS file's EFI_SECTION_PE32 (type 0x10) and then
through `e_lfanew` at 0x3C. Scanning the file body for the literal `PE\\0\\0`
finds the first occurrence anywhere in it, which is how a reader can report a
header for a driver that has none.

    tools/pe-facts.py                       # the build tree's FVMAIN.Fv
    tools/pe-facts.py <FVMAIN.Fv | .img>    # any volume or payload
    tools/pe-facts.py <fv> --sep            # only the separator verdicts
"""
import argparse
import importlib.util
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FVDIR = os.path.join(ROOT, "work/uefi/Mu-Silicium/Build/gauguinPkg/DEBUG_CLANGPDB/FV")
DEFAULT_FV = os.path.join(FVDIR, "FVMAIN.Fv")

SECTION_PE32 = 0x10
FFS_ATTRIB_LARGE_FILE = 0x01

# The panel reading, verbatim: 46 promoted entries, in promotion order. It is
# indexed by Apriori position k, and SEQ[k] is Apriori entry k + 1 (entry 0 is
# DxeCore, which the walk never hands to CoreAddToDriverList).
SEQ = "s" * 18 + "L" * 3 + "s" + "L" * 24

# Field name -> how to compare it. Strings and ints are both fine; the separator
# test only needs equality classes.
FIELDS = [
    "ffs_size", "pe_size", "Machine", "NumberOfSections", "SizeOfOptionalHeader",
    "Characteristics", "Magic", "SizeOfCode", "SizeOfInitializedData",
    "SizeOfUninitializedData", "AddressOfEntryPoint", "BaseOfCode", "ImageBase",
    "SectionAlignment", "FileAlignment", "SizeOfImage", "SizeOfHeaders",
    "Subsystem", "DllCharacteristics", "SizeOfStackReserve", "SizeOfHeapReserve",
    "NumberOfRvaAndSizes", "reloc_size", "has_reloc", "sections",
]


def load_fv_inventory():
    """Import tools/fv-inventory.py, whose FV walk this needs and must not copy."""
    path = os.path.join(ROOT, "tools/fv-inventory.py")
    spec = importlib.util.spec_from_file_location("fv_inventory", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_volume(fvi, path):
    d = open(path, "rb").read()
    if d[:8] == b"ANDROID!":
        _, _, _, inner = fvi.unpack(path)
        if inner is None:
            sys.exit(f"{path}: no FVMAIN inside it")
        return inner
    return d


def pe32_section(fvi, fv, off, size):
    """The body of the file's EFI_SECTION_PE32, or None.

    An FFS file is a section stream, not an image: the PE sits inside one
    section of type 0x10. Searching the whole file for `PE\\0\\0` instead would
    happily match bytes inside a tail that is not a PE, so the section stream is
    walked and the type is checked.
    """
    for st, body in fvi.sections(fv[off + 24:off + size]):
        if st == SECTION_PE32:
            return body
    return None


def parse_pe(body):
    """PE32+ fixed and optional header fields, plus the section table.

    `e_lfanew` at 0x3C is the only correct entry point. A magic scan finds the
    first `PE\\0\\0` in the buffer, which for a PE with a trailing section is
    still right and for anything else is silently wrong.
    """
    if body is None or len(body) < 0x40 or body[:2] != b"MZ":
        return None
    e = struct.unpack_from("<I", body, 0x3C)[0]
    if e + 24 > len(body) or body[e:e + 4] != b"PE\0\0":
        return None
    machine, nsec = struct.unpack_from("<HH", body, e + 4)
    optsz, chars = struct.unpack_from("<HH", body, e + 20)
    o = e + 24
    if o + optsz > len(body):
        return None
    magic, = struct.unpack_from("<H", body, o)
    if magic != 0x20B:                       # PE32+ only; ARM64 is always this
        return None

    def u32(at):
        return struct.unpack_from("<I", body, o + at)[0]

    def u64(at):
        return struct.unpack_from("<Q", body, o + at)[0]

    def u16(at):
        return struct.unpack_from("<H", body, o + at)[0]

    nrva = u32(0x6C)
    # DataDirectory index 5 is IMAGE_DIRECTORY_ENTRY_BASERELOC, and on a
    # reloc-stripped image its Size is 0 and its RVA is 0.
    reloc_size = 0
    if nrva > 5:
        reloc_size = u32(0x70 + 5 * 8 + 4)

    sects, sv = [], e + 24 + optsz
    for i in range(nsec):
        b = sv + i * 40
        if b + 40 > len(body):
            break
        name = body[b:b + 8].rstrip(b"\0").decode("ascii", "replace")
        sects.append(f"{name}{struct.unpack_from('<I', body, b + 36)[0]:#010x}")

    return {
        "pe_size": len(body),
        "Machine": machine,
        "NumberOfSections": nsec,
        "SizeOfOptionalHeader": optsz,
        "Characteristics": chars,
        "Magic": magic,
        "SizeOfCode": u32(4),
        "SizeOfInitializedData": u32(8),
        "SizeOfUninitializedData": u32(12),
        "AddressOfEntryPoint": u32(16),
        "BaseOfCode": u32(20),
        "ImageBase": u64(24),
        "SectionAlignment": u32(0x20),
        "FileAlignment": u32(0x24),
        "SizeOfImage": u32(0x38),
        "SizeOfHeaders": u32(0x3C),
        "Subsystem": u16(0x44),
        "DllCharacteristics": u16(0x46),
        "SizeOfStackReserve": u64(0x48),
        "SizeOfHeapReserve": u64(0x58),
        "NumberOfRvaAndSizes": nrva,
        "reloc_size": reloc_size,
        "has_reloc": reloc_size > 0,
        "sections": ",".join(sects),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("volume", nargs="?", default=DEFAULT_FV)
    ap.add_argument("--seq", default=SEQ,
                    help="the promoted entries' result letters, in promotion order")
    ap.add_argument("--sep", action="store_true",
                    help="print only the per-field separator verdicts")
    args = ap.parse_args()

    fvi = load_fv_inventory()
    xref = os.path.join(FVDIR, "Guid.xref")
    names = {}
    for line in open(xref, encoding="utf-8", errors="replace"):
        m = line.split()
        if len(m) >= 2:
            names.setdefault(m[0].upper(), m[1])

    fv = read_volume(fvi, args.volume)
    files = fvi.fv_files(fv)

    # Apriori array -> the volume's file table, so each SEQ letter resolves to a
    # driver GUID. This is the same join tools/fv-census.py makes.
    g0, _, s0, o0, _ = files[0]
    payload = fvi.sections(fv[o0 + 24:o0 + s0])[0][1]
    apriori = [fvi.guid_str(payload[i:i + 16]) for i in range(0, len(payload) - 15, 16)]
    by_guid = {fvi.guid_str(g): (g, t, size, off) for g, t, size, off, _ in files}

    rows = {}
    for k, ch in enumerate(args.seq):
        a = k + 1
        if a >= len(apriori):
            break
        gs = apriori[a]
        e = by_guid.get(gs)
        if e is None or e[1] != 0x07:
            continue
        _, _, size, off = e
        pe = parse_pe(pe32_section(fvi, fv, off, size))
        if pe is None:
            print(f"  no PE32 section in {names.get(gs, gs)} at {off:#x}")
            continue
        pe["ffs_size"] = size
        rows[gs] = (ch, names.get(gs, "(unnamed)"), pe)

    if not rows:
        sys.exit(f"{args.volume}: no promoted DRIVER resolved to a PE")

    if not args.sep:
        head = ["result", "apri", "ffs_size", "SizeOfImage", "saln", "chars",
                "ImageBase", "has_reloc", "subsys", "sections"]
        print(f"{'res':>3} {'name':40} " +
              " ".join(f"{h:>10}" for h in head[2:]))
        for k, ch in enumerate(args.seq):
            gs = apriori[k + 1] if k + 1 < len(apriori) else None
            if gs not in rows:
                continue
            _, name, pe = rows[gs]
            print(f"{ch:>3} {name:40} "
                  f"{pe['ffs_size']:>10} {pe['SizeOfImage']:>10} "
                  f"{pe['SectionAlignment']:>#10x} {pe['Characteristics']:>#10x} "
                  f"{pe['ImageBase']:>#10x} {str(pe['has_reloc']):>10} "
                  f"{pe['Subsystem']:>10} {pe['sections']}")
        print()

    # Two different questions, printed separately because they are easy to
    # conflate. "Value sets disjoint" is the equality-class test: does some value
    # appear only under one result? On its own it is nearly vacuous for a field
    # that is almost unique per image - every driver has its own SizeOfImage, so
    # the two sets come out disjoint and the field appears to separate the
    # classes, when it has explained nothing. What would be a mechanism is a
    # *threshold*: every s below some value and every L above it. So both are
    # printed, and the summary is drawn from the second.
    print(f"{'field':26} {'s vals':>6} {'L vals':>6}  {'values':>10}  threshold")
    disjoint = []
    for f in FIELDS:
        s_vals = {r[2][f] for g, r in rows.items() if r[0] == "s" and f in r[2]}
        l_vals = {r[2][f] for g, r in rows.items() if r[0] == "L" and f in r[2]}
        overlap = "shared" if (s_vals & l_vals) else "disjoint"
        # A value held by both classes already forbids any threshold, so the
        # column is only meaningful for a disjoint field.
        numeric = all(isinstance(v, int) for v in s_vals | l_vals)
        if not numeric:
            split = "-"
        elif s_vals & l_vals:
            split = "n/a"
        elif max(s_vals) < min(l_vals):
            split = f"s < L (s<={max(s_vals)}, L>={min(l_vals)})"
        elif max(l_vals) < min(s_vals):
            split = f"L < s (L<={max(l_vals)}, s>={min(s_vals)})"
        else:
            split = "interleaved"
        if overlap == "disjoint":
            disjoint.append(f"{f} ({split})")
        print(f"{f:26} {len(s_vals):>6} {len(l_vals):>6}  {overlap:>10}  {split}")

    # The verdict line, not just the table: a reader who skims must not come away
    # with "SizeOfImage is disjoint, so size explains it".
    print(f"\nevery field's value sets overlap between s and L except: "
          f"{disjoint if disjoint else 'none'}")
    print(f"-> {'no field separates' if all('interleaved' in d for d in disjoint) else 'CHECK'} "
          f"the {sum(1 for r in rows.values() if r[0] == 's')} s from the "
          f"{sum(1 for r in rows.values() if r[0] == 'L')} L by a single value "
          f"or a threshold")

    n_s = sum(1 for r in rows.values() if r[0] == "s")
    n_l = sum(1 for r in rows.values() if r[0] == "L")
    reloc_l = [r[1] for r in rows.values() if r[0] == "L" and not r[2]["has_reloc"]]
    reloc_s = [r[1] for r in rows.values() if r[0] == "s" and not r[2]["has_reloc"]]
    print(f"\npromoted DRIVERs resolved: {n_s} s, {n_l} L")
    print(f"no .reloc among the L: {reloc_l}")
    print(f"no .reloc among the s: {reloc_s}")


if __name__ == "__main__":
    main()
