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
36,864-byte request with opposite results.  `SectionAlignment` is not a PE fact
either - it is the module type, because `SiliciumPkg.dsc.inc` links
`DXE_RUNTIME_DRIVER` with `/ALIGN:0x10000` and everything else with
`/ALIGN:0x1000` - and the eight drivers in that family come out `sssLLLLL`, so it
straddles too, with a 64 KiB size penalty (`ImageSize + SectionAlignment` in
`CoreLoadPeImage`) that totals 0.4 MiB across the set.

The one field that used to be reported here as a partial mechanism - the absence
of `.reloc` from three of the 27 - is **not** one, and the reason is worth keeping
because the mistake is easy to repeat.  "Relocations stripped" is not
`reloc_size == 0`: `PeCoffLoaderGetImageInfo` (`BasePeCoff.c:660`) sets
`ImageContext->RelocationsStripped` from `IMAGE_FILE_RELOCS_STRIPPED`, which is
`Characteristics` bit 0, and that bit is clear on all 80 DRIVER files in this
volume.  So every promoted image takes the `AllocateAnyPages` fallback and none
of them reaches the page-0 `AllocateAddress` path.  `has_reloc` is a fact about
the file; `reloc_stripped` is what the loader does, and only the second is on the
load path.  Both columns are printed, from their own sources, so the two cannot be
conflated again.

What is left, and what the tail of the output now measures, is the allocator
side. Every promoted request is `EfiRuntimeServicesCode` - that is the
`AllocateAnyPages` fallback every one of the 46 takes - and on AArch64 that type
is rounded up to a 16-page multiple and forced to 64 KiB alignment inside
`CoreInternalAllocatePages` (`Page.c:1160`, rounding at `:1217`, alignment from
`RUNTIME_PAGE_ALLOCATION_GRANULARITY` at `ProcessorBind.h:169`). So the 1562
pages of `SizeOfImage` demand are 1824 pages as the allocator sees them, and the
cumulative column in that unit is what rules out a running-total boundary:
`NpaDxe` succeeds at 672 pages, `RpmhDxe` fails at 688, `ClockDxe` fails at 752,
and `ShmBridgeDxe` then succeeds at 768 with a request the same size as the one
that failed at 688. The bin that type prefers is 150 pages of
`RuntimeServicesCode` against 1824 pages of demand, so it is exhausted within the
first few drivers and the fallthrough to the default bin is load-bearing rather
than hypothetical - and the default bin spans the heap below the 450-page bin
block, i.e. most of 35.4 MiB, which is why the failure is a `FindFreePages`
boundary condition and not exhaustion.

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
    "NumberOfRvaAndSizes", "reloc_size", "has_reloc", "reloc_stripped", "sections",
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
    # COFF header: Machine(2) NumberOfSections(2) TimeDateStamp(4) PointerToSymbolTable(4)
    # NumberOfSymbols(4) SizeOfOptionalHeader(2) Characteristics(2).  The two
    # fields read here are 18 and 20 bytes past the signature, not 16 and 18 -
    # reading them two bytes early yields SizeOfOptionalHeader in the
    # `Characteristics` column, which is 0x00F0 on every driver and looks like a
    # field that is constant when it is in fact a different field.
    optsz, = struct.unpack_from("<H", body, e + 20)
    chars, = struct.unpack_from("<H", body, e + 22)
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
        # What `PeCoffLoaderGetImageInfo` actually sets, and it is not `has_reloc`.
        # `IMAGE_FILE_RELOCS_STRIPPED` is bit 0 of Characteristics; on this volume
        # it is clear on all 80 DRIVER files, so every promoted image is loaded
        # with `AllocateAnyPages` and the page-0 `AllocateAddress` path in
        # `CoreLoadPeImage` is taken by none of them.
        "reloc_stripped": (chars & 0x0001) != 0,
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

    # The two facts that were conflated. `has_reloc == False` is a property of the
    # section table; `reloc_stripped` is the one `CoreLoadPeImage` branches on, and
    # it comes from a PE header bit.
    strip_l = [r[1] for r in rows.values() if r[0] == "L" and r[2]["reloc_stripped"]]
    strip_s = [r[1] for r in rows.values() if r[0] == "s" and r[2]["reloc_stripped"]]
    print(f"RelocationsStripped (Characteristics bit 0) among the L: {strip_l}")
    print(f"RelocationsStripped (Characteristics bit 0) among the s: {strip_s}")
    if not strip_l and not strip_s:
        print("  -> every promoted image takes the AllocateAnyPages branch; the "
              "page-0\n     AllocateAddress path is taken by none of them, so "
              '"no .reloc" above\n     is not a mechanism for any of the 27')

    # The exact request, in promotion order: `CoreLoadPeImage` adds SectionAlignment
    # to ImageSize when it exceeds a page, then rounds to pages. This is the number
    # CoreAllocatePages is asked for, not SizeOfImage.
    #
    # The `r16` column beside it is the number the allocator actually sees, and it
    # is not decoration. Every one of these requests is made with
    # MemoryType = EfiRuntimeServicesCode -- that is the `!RelocationsStripped`
    # branch at Image.c:730-737, and the block above shows all 46 take it -- and
    # CoreInternalAllocatePages (Page.c:1160) forces
    # Alignment = RUNTIME_PAGE_ALLOCATION_GRANULARITY for that type. On AArch64
    # that is 0x10000 (ProcessorBind.h:169, against DEFAULT 0x1000 at :165), so
    # before the request reaches FindFreePages it is rounded up to a multiple of
    # EFI_SIZE_TO_PAGES (Alignment) = 16 pages at Page.c:1217 and must land
    # 64-KiB aligned. A 9-page driver costs 16 pages; a 46-driver run costs 1824
    # pages rather than 1562, and the 262-page difference is pure rounding.
    print(f"\n{'res':>3} {'name':40} {'SizeOfImage':>11} {'saln':>8} "
          f"{'req bytes':>10} {'pages':>6} {'r16':>4} {'cum pages':>10} "
          f"{'cum r16':>8} {'cum bytes':>10}")
    cum, cum_s, cum_l, rcum = 0, 0, 0, 0
    marks = []          # (ch, name, pages, rounded, cumulative-rounded)
    for k, ch in enumerate(args.seq):
        gs = apriori[k + 1] if k + 1 < len(apriori) else None
        if gs not in rows:
            continue
        _, name, pe = rows[gs]
        req = pe["SizeOfImage"] + (pe["SectionAlignment"]
                                   if pe["SectionAlignment"] > 0x1000 else 0)
        pg = -(-req // 0x1000)
        r16 = -(-pg // 16) * 16
        cum += pg
        rcum += r16
        if ch == "s":
            cum_s += pg
        else:
            cum_l += pg
        marks.append((ch, name, pe["Subsystem"], pg, r16, rcum))
        print(f"{ch:>3} {name:40} {pe['SizeOfImage']:>11} "
              f"{pe['SectionAlignment']:>#8x} {req:>10} {pg:>6} {r16:>4} {cum:>10} "
              f"{rcum:>8} {cum * 0x1000:>10}")
    print(f"\ntotal: {cum} pages = {cum * 0x1000} B "
          f"({cum * 0x1000 / (1024 * 1024):.2f} MiB)")
    print(f"  s: {cum_s} pages = {cum_s * 0x1000} B")
    print(f"  L: {cum_l} pages = {cum_l * 0x1000} B")
    print(f"as the allocator sees it, at 16-page granularity: {rcum} pages = "
          f"{rcum * 0x1000} B ({rcum * 0x1000 / (1024 * 1024):.2f} MiB), "
          f"+{rcum - cum} pages of rounding")

    # The rounded column is what rules out a running-total threshold, and it is the
    # only thing here that does. The last success before the first failure, the
    # first failure, and the first success after it are three consecutive runs
    # whose cumulative demand is strictly increasing -- so if the boundary were a
    # total, the later success is impossible. Printed as measured rather than
    # described, because "a later request succeeded" is the whole verdict and a
    # reader should be able to see the three numbers that make it one.
    if marks:
        i = next((k for k, m in enumerate(marks) if m[0] == "L"), None)
        j = next((k for k in range(i + 1, len(marks))
                  if marks[k][0] == "s"), None) if i is not None else None
        if i and j is not None:
            print(f"\nthe boundary is not a running total. {marks[i - 1][1]} is the "
                  f"last success, at\n{marks[i - 1][5]} pages of demand, and "
                  f"{marks[j][1]} succeeds again at {marks[j][5]} pages with\n"
                  f"a {marks[j][4]}-page request, after {marks[j - 1][1]}'s "
                  f"{marks[j - 1][4]}-page request at {marks[j - 1][5]} and\n"
                  f"{marks[i][1]}'s {marks[i][4]}-page request at {marks[i][5]} "
                  f"have both failed. The last of those is\nthe same size as the "
                  f"one that succeeds, so the deciding factor is not the request.")

    # And the bins those requests prefer, which is the other half of the
    # comparison. SiliciumPkg.dsc.inc gives
    # PcdMemoryTypeEfiRuntimeServicesCode|150 and
    # PcdMemoryTypeEfiRuntimeServicesData|300, and AllocateMemoryTypeInformationBins
    # (MemoryBin.c:447) carves exactly 450 pages for them in one contiguous block
    # off the top of the heap, then drops *DefaultMaximumAddress to just below it.
    # Both figures are the platform's, not this volume's, so they are printed
    # rather than derived here.
    rt = [m for m in marks if m[2] == 12]
    if rt:
        rt_pages = sum(m[4] for m in rt)
        # Each of them then makes two more runtime-typed pools: RuntimeData is
        # one EFI_RUNTIME_IMAGE_ENTRY (Image.c:837) and FixupData is
        # reloc_size/2*8 bytes (BasePeCoff.c:1515, allocated at Image.c:793 only
        # for EFI_IMAGE_SUBSYSTEM_EFI_RUNTIME_DRIVER, which is exactly this set).
        # Both are EfiRuntimeServicesData, so both round the same way and both
        # come out of the 300-page bin rather than the 150-page one.
        pools = 0
        for m in rt:
            pools += 16                                    # RuntimeData, 1 page
        print(f"\nruntime family (Subsystem 12): {len(rt)} of the {len(marks)} "
              f"promoted, {rt_pages} pages of image demand")
        print(f"  against PcdMemoryTypeEfiRuntimeServicesCode = 150 pages, so the "
              f"code bin empties well\n  before the run ends and the fallthrough "
              f"to the default bin is load-bearing")
        print(f"  their RuntimeData pools add {pools} more pages of "
              f"EfiRuntimeServicesData, against\n  "
              f"PcdMemoryTypeEfiRuntimeServicesData = 300 -- close enough to "
              f"matter, and not over it")
    # The comparison below is only worth making if that region really is
    # EfiConventionalMemory, and that is two tree facts rather than an
    # assumption. Both were checked, and one of them is easy to get backwards:
    #
    #   1. MemoryMapLib.h's SYS_MEM_CAP is PRESENT|INITIALIZED|TESTED plus the
    #      cacheability bits and the three *_PROTECTABLE bits. Every one of those
    #      outside the first three is also outside Gcd.c's MEMORY_ATTRIBUTE_MASK,
    #      so `attr & MASK` is exactly TESTED_MEMORY_ATTRIBUTES (0x7) and the
    #      region is EfiGcdMemoryTypeSystemMemory, not Reserved. The mask holds
    #      *_PROTECTED (EXECUTION_PROTECTED 0x200), not *_PROTECTABLE
    #      (EXECUTION_PROTECTABLE 0x400000); reading one for the other makes this
    #      look like a Reserved region and the comparison below look invalid.
    #
    #   2. MemoryInitPei.c builds a memory allocation HOB for each such region,
    #      and CoreInitializeGcdServices hands that HOB's own MemoryType to
    #      CoreAddMemoryDescriptor (Gcd.c:2776). For the Conv column that is
    #      EfiConventionalMemory.
    #
    # The other three rows carrying Conv are MMAP_IO rather than SYS_MEM, so they
    # are EfiGcdMemoryTypeMemoryMappedIo and never reach that call: the DXE Heap
    # is the only row that is both SYS_MEM/SYS_MEM_CAP and Conv.
    print("against the DXE heap, which is the only EfiConventionalMemory region "
          "on this\nplatform: {\"DXE Heap\", 0x9B800000, 0x02360000, AddMem, "
          "SYS_MEM, SYS_MEM_CAP, Conv,\nWRITE_BACK_XN} = 35.4 MiB")


if __name__ == "__main__":
    main()
