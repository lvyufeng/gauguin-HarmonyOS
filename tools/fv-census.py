#!/usr/bin/env python3
"""Census of FVMAIN, joined three ways: physical order, Apriori order, and the
device's own SEQ reading.

Why this exists: the panel reads

    P2 SEQ ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL

which is 46 characters - 18 s, 3 L, 1 s, 24 L - while the Apriori file names 70
GUIDs. `mP2Apriori` counts Apriori entries that matched an entry the walk had
handed to CoreAddToDriverList, so 46 means 23 of the 70 names matched nothing:
their drivers are not in mDiscoveredList at all. That is not the same question
as "why do 27 loads fail", and the two have been conflated in this repo before.

The temptation is to read the split as a *physical* cutoff in the volume, and
this tool exists to measure that rather than assert it. It prints the volume in
physical order with the Apriori index overlaid, replays FvCheck's scan exactly,
and then joins `Apriori[k + 1]` to the volume's file table and reports the
lowest physical index among the failed loads against the highest among the
successful ones. Measured: **5** (SecurityStubDxe, Apriori 37) failed while
**74** (ShmBridgeDxe, Apriori 22) succeeded, so no physical cutoff exists - not
as a boundary, not as a suffix, not at all. The Apriori *index* is the only
ordering in which the promoted set is contiguous.

Two other candidate mechanisms for a partial mDiscoveredList are disposed of
here too, both by measurement rather than argument:

  (a) FvCheck truncating the FFS file list. It walks from the first file and
      stops at the first 24 bytes that read as erased. On this volume that stop
      is at 0x702308, at the very end, after all 123 files - so it hides
      nothing. (An earlier draft of this tool called the truncated list "a
      physical cutoff and nothing else is". The scan is real; the conclusion it
      was pointed at is gone.)
  (b) FvCheck returning EFI_VOLUME_CORRUPTED, in which case the FV protocol is
      never installed and there are no drivers at all - falsified by the 19
      successful loads, and replayed here only to be ruled out.

And the volume is clean at the layer below that: `FFS_ATTRIB_CHECKSUM` is clear
on all 123 files, which makes FvCheck's memory-mapped `AllocateCopyPool` branch
dead code and puts every file through the exact `IsValidFfsFile` test (EDK2's
`CalculateCheckSum8`, which is `(0x100 - sum) & 0xFF` and not the raw sum).

    tools/fv-census.py                      # the build tree's FVMAIN.Fv
    tools/fv-census.py <FVMAIN.Fv | .img>   # any volume or payload
"""
import importlib.util
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FVDIR = os.path.join(ROOT, "work/uefi/Mu-Silicium/Build/gauguinPkg/DEBUG_CLANGPDB/FV")
FV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(FVDIR, "FVMAIN.Fv")
XREF = os.path.join(FVDIR, "Guid.xref")

spec = importlib.util.spec_from_file_location(
    "fvinv", os.path.join(ROOT, "tools/fv-inventory.py"))
fvinv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fvinv)

# The SEQ string off the panel, verbatim:
#   len=46, s=19, L=27
SEQ = "s" * 18 + "L" * 3 + "s" + "L" * 24
assert len(SEQ) == 46 and SEQ.count("s") == 19

names = {}
for ln in open(XREF):
    p = ln.split()
    if len(p) >= 2:
        names.setdefault(p[0].upper(), p[1])

fv = open(FV, "rb").read()
if fv[:8] == b"ANDROID!":
    # A payload .img holds FVMAIN inside FVMAIN_COMPACT inside a gzip stream
    # with the DTB appended, so it cannot be walked raw. fv-inventory.py's
    # unpack is the descent that gets that right (and has been wrong twice in
    # ways that still produced a plausible file list), so it is used rather
    # than re-deriving the offsets here.
    _, _, _, fv = fvinv.unpack(FV)
    if fv is None:
        sys.exit(f"{FV}: no FVMAIN inside it")
files = fvinv.fv_files(fv)

base = fv.find(b"_FVH") - 0x28
fvlen, = struct.unpack("<Q", fv[base + 0x20:base + 0x28])
hlen, = struct.unpack("<H", fv[base + 0x30:base + 0x32])
ext_off, = struct.unpack("<H", fv[base + 0x34:base + 0x36])
ext = base + ext_off if ext_off else 0
ext_size, = struct.unpack("<I", fv[ext + 16:ext + 20]) if ext else (0,)
first = (ext + ext_size + 7) & ~7 if ext else (base + hlen)

print(f"FVMAIN.Fv {len(fv):#x} bytes, FvLength {fvlen:#x}, files {len(files)}")
print(f"  HeaderLength {hlen:#x}  ExtHeaderOffset {ext_off:#x}  "
      f"ExtHeaderSize {ext_size:#x}  first file at {first:#x} "
      f"(align8 ({ext_size and ext or 0:#x} + {ext_size:#x}))")
print()

# ---------------------------------------------------------------------------
# FvCheck, replayed exactly (FwVol.c:444-548 + Ffs.c). ErasePolarity is 1 for
# this volume, so "erased" is 0xFF and file state is read inverted.
# ---------------------------------------------------------------------------
ERASE_POLARITY = 1
ERASE_BYTE = 0xFF


def get_file_state(raw_state):
    """Ffs.c: GetFileState returns the HIGHEST set bit, not the whole byte.

    A valid file carries State 0x07 (HEADER_CONSTRUCTION|HEADER_VALID|
    DATA_VALID) on disk, i.e. 0xF8 under erase polarity. Returning the whole
    byte 0x07 makes IsValidFfsHeader's switch fall to `default` and declare
    every file in the volume corrupt, which is how a replay of FvCheck can
    claim 0 files in a volume the device happily walks.
    """
    st = (~raw_state) & 0xFF if ERASE_POLARITY else raw_state
    bit = 0x80
    while bit and not (bit & st):
        bit >>= 1
    return bit


def sum8(buf):
    return sum(buf) & 0xFF


def checksum8(buf):
    """EDK2's CalculateCheckSum8: the value that makes the sum come out zero.

    `(0x100 - sum) & 0xFF`, NOT the raw sum. Using the raw sum here is the
    mistake this function exists to prevent: it is off by exactly the value it
    is checking, so for the FFS_ATTRIB_CHECKSUM case the replay compares
    `h[0x11] == sum8(body)` where the device compares `h[0x11] == (0x100 - sum8(body)) & 0xFF`.
    Both tests can only pass on a file whose checksum field is zero.
    """
    return (0x100 - sum8(buf)) & 0xFF


HEADER_SIZE = 24


def verify_header_checksum(h):
    s = sum8(h[:HEADER_SIZE]) - h[0x17] - h[0x11]
    return (s & 0xFF) == 0


def header_state_ok(st):
    return st in (0x02, 0x04, 0x08, 0x10)      # HEADER_VALID/DATA_VALID/MFU/DELETED


def data_checksum_ok(h, whole):
    st = get_file_state(h[0x17])
    if st not in (0x10, 0x04, 0x08):
        return False
    want = 0xAA                                # FFS_FIXED_CHECKSUM
    if (h[0x13] & 0x40) == 0x40:               # FFS_ATTRIB_CHECKSUM
        want = checksum8(whole[HEADER_SIZE:])
    return h[0x11] == want


print("=== FvCheck replay (FwVol.c:444) ===")
off = first
listed, stop, corrupt = [], None, None
while off + 24 <= base + fvlen:
    test = min(24, base + fvlen - off)
    if all(b == ERASE_BYTE for b in fv[off:off + test]):
        stop = ("erased run", off)
        break
    h = fv[off:off + 24]
    st = get_file_state(h[0x17])
    if not header_state_ok(st) or not verify_header_checksum(h):
        if st in (0x20, 0x01):                 # HEADER_INVALID / CONSTRUCTION
            off += HEADER_SIZE                 # resync: skip 24 and continue
            continue
        corrupt = (f"header checksum bad at {off:#x} state {st:#04x}", off)
        break
    size = h[20] | (h[21] << 8) | (h[22] << 16)
    whole = fv[off:off + size]
    if not data_checksum_ok(h, whole):
        corrupt = (f"file checksum bad at {off:#x} size {size:#x}", off)
        break
    if st != 0x10:                             # not EFI_FILE_DELETED
        listed.append((off, size, h[18]))
    off = (off + size + 7) & ~7

print(f"  files added to FfsFileListHeader: {len(listed)}")
print(f"  scan ended: "
      f"{'erased run at ' + hex(stop[1]) if stop else 'end of volume'}")
print(f"  corruption: {corrupt[0] if corrupt else 'none'}")
if stop:
    hidden = [f for f in files if f[3] >= stop[1]]
    print(f"  -> the stop is at {stop[1]:#x}, {base + fvlen - stop[1]:#x} bytes "
          f"before the end of the volume, and it hides {len(hidden)} files")
    if not hidden:
        print("     -> nothing follows the erased run, so the truncated-list "
              "mechanism hides nothing")
else:
    print("  -> the walk sees every file in the volume")
print()

# ---------------------------------------------------------------------------
# FFS attributes, and the exact IsValidFfsFile test over every file.
#
# This is what makes the "FvCheck is exonerated" claim a measurement rather
# than a replay of one path. FvCheck's only allocation on a memory-mapped
# volume is `AllocateCopyPool (WholeFileSize, CacheFfsHeader)`, and it is
# gated on FFS_ATTRIB_CHECKSUM (h[0x13] & 0x40). If no file sets that bit,
# the branch is unreachable and its EFI_OUT_OF_RESOURCES site cannot fire.
# ---------------------------------------------------------------------------
print("=== FFS attributes, and IsValidFfsFile over every file ===")
OFF = first
hist, attr03, pass_n, fail = {}, 0, 0, []
for g, t, size, off, state in files:
    h = fv[off:off + 24]
    hist[h[0x13]] = hist.get(h[0x13], 0) + 1
    if h[0x13] & 0x40:
        attr03 += 1
    if data_checksum_ok(h, fv[off:off + size]):
        pass_n += 1
    else:
        fail.append((names.get(fvinv.guid_str(g), "?"), hex(off)))

print(f"  attr byte histogram: "
      f"{ {hex(k): v for k, v in sorted(hist.items())} }")
print(f"  FFS_ATTRIB_CHECKSUM set on {attr03} files -> "
      f"FvCheck's AllocateCopyPool total is {0 if not attr03 else 'nonzero'}")
if not attr03 and pass_n:
    print("  -> the memory-mapped FvCheck copy branch is DEAD CODE on this "
          "volume; every file is read in place")
print(f"  files passing the exact IsValidFfsFile data-checksum test: "
      f"{pass_n}  failures: {fail if fail else 'none'}")
print()

# ---------------------------------------------------------------------------
# Apriori payload -> volume
# ---------------------------------------------------------------------------
g0, t0, s0, o0, st0 = files[0]
payload = fvinv.sections(fv[o0 + 24:o0 + s0])[0][1]
apriori = [fvinv.guid_str(payload[i:i + 16]) for i in range(0, len(payload) - 15, 16)]
by_guid = {fvinv.guid_str(g): i for i, (g, *_rest) in enumerate(files)}
ap_phys = {i: by_guid.get(g) for i, g in enumerate(apriori)}

TYPE = {0x01: "SECCORE", 0x02: "FREEFORM", 0x03: "SECURITY", 0x04: "PEI_CORE",
        0x05: "DXE_CORE", 0x06: "PEIM", 0x07: "DRIVER", 0x08: "COMB_PEIM",
        0x09: "APPLICATION", 0x0A: "SMM", 0x0B: "FV_IMAGE", 0xF0: "PAD"}


def nm(g):
    return names.get(g, "(unnamed)")


print("=== Volume in physical order, Apriori index overlaid ===")
print(f"{'phys':>4} {'off':>9} {'type':>11} {'size':>8} {'attr':>5} {'apri':>4}  "
      f"{'name':44} guid")
phys_of_ap = {}
for i, (g, t, size, off, state) in enumerate(files):
    gs = fvinv.guid_str(g)
    a = next((k for k, v in ap_phys.items() if v == i), None)
    if a is not None:
        phys_of_ap.setdefault(a, i)
    mark = f"{a:>4}" if a is not None else "   -"
    print(f"{i:>4} {off:>#9x} {TYPE.get(t, hex(t)):>11} {size:>8} "
          f"{fv[off + 0x13]:>#5x} {mark}  {nm(gs):44} {gs}")

print()
print(f"files: {len(files)}   Driver(0x07): {sum(1 for _, t, *_ in files if t == 0x07)}"
      f"   Apriori payload: {len(apriori)} GUIDs")
absent = [i for i, g in enumerate(apriori) if by_guid.get(g) is None]
print(f"Apriori entries with no file in the volume: {absent}")

# ---------------------------------------------------------------------------
# The join. ap0 is DxeCore and is never in mDiscoveredList (the DXE_CORE branch
# of the walk only fills gDxeCoreLoadedImage->FilePath), so SEQ[0] is ap1.
# ---------------------------------------------------------------------------
print()
print("=== SEQ join: ap1..ap69 vs the 46 characters ===")
print(f"{'seq':>3} {'ap':>3} {'res':>3} {'phys':>5} {'type':>11}  name")
skipped_L, skipped_s = [], []
for k, ch in enumerate(SEQ):
    a = k + 1
    p = ap_phys.get(a)
    t = TYPE.get(files[p][1], "?") if p is not None else "-"
    print(f"{k:>3} {a:>3} {ch:>3} "
          f"{(str(p) if p is not None else '-'):>5} {t:>11}  "
          f"{nm(apriori[a]) if a < len(apriori) else '(past end)'}")
    (skipped_L if ch == 'L' else skipped_s).append(a)

tail = list(range(len(SEQ) + 1, 70))
print()
print(f"loaded  (s): {len(skipped_s)}  -> ap {skipped_s}")
print(f"failed  (L): {len(skipped_L)}  -> ap {skipped_L}")
print(f"never promoted: ap {tail}")
print(f"physical indices of the failed set: {sorted(ap_phys[a] for a in skipped_L)}")
print(f"physical indices of the loaded set: {sorted(ap_phys[a] for a in skipped_s)}")
lo = min(ap_phys[a] for a in skipped_L)
hi = max(ap_phys[a] for a in skipped_s)
print(f"lowest physical index among the FAILED loads: {lo} "
      f"(ap{[a for a in skipped_L if ap_phys[a] == lo]})")
print(f"highest physical index among the LOADED ones: {hi} "
      f"(ap{[a for a in skipped_s if ap_phys[a] == hi]})")
print(f"-> a physical cutoff is "
      f"{'CONSISTENT' if lo > hi else 'IMPOSSIBLE'}: "
      f"a load at physical {lo} failed while physical {hi} succeeded")
print()
print("drivers the walk registered but the Apriori file does not name:")
notin = [fvinv.guid_str(g) for g, t, *_ in files
         if t == 0x07 and fvinv.guid_str(g) not in set(apriori)]
print(f"  {len(notin)}: {[nm(g) for g in notin]}")
