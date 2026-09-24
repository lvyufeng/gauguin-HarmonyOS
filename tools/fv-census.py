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

The second half of the tool turns the one reading that *was* taken - the 46
characters of `P2 SEQ` - into an answer about mechanism rather than a location.
`len(P2 SEQ)` is `mP2Apriori`, the count of Apriori entries the walk promoted, so
the tool first tabulates `stop -> miss -> seen -> SEQ len` and finds the stops
that could produce 46: physical 49 (`seen=48`) and physical 50 (`seen=49`), both
`miss=14 PlatformInfoDxeDriver`. It then checks the SEQ's *content* against each
of them, and that is what closes the branch rather than narrowing it - the
`predicted SEQ` for a stop holds different drivers in the later slots, and both
stops are refuted at slots 17 and 21. Slot 21 is ShmBridgeDxe: it sits at
physical 74, no stop below 74 can promote it, and every driver a stop at 49 or
50 can promote into that slot has an observed `L` while the panel shows `s`.

So the 46 characters are not a stopped discovery walk, and the digest was built to
leave what remains as a fork between two readings of `P2 APRI`, both of which
produce the observed SEQ exactly: the array read whole (`entries=70`, and then 23
names matched nothing, which a complete walk forbids) or the array read 368 bytes
short (`entries=47`, `unhit=1`, the promotion loop never looking past ap46).

**The volume closes that fork, and it closes it against the second reading.**
`bytes=752` needs `SizeOfBuffer` to come back 752 while the section declares 1124,
and the path cannot do that: `FvReadFileSection` hands `BufferSize` straight to
`GetSection` (`MdeModulePkg/Core/Dxe/SectionExtraction/CoreSectionExtraction.c:1245`),
which takes `SectionSize = CopySize` *before* the `*BufferSize < CopySize` clamp and
then writes `*BufferSize = SectionSize` after the `CopyMem` - so a successful
`ReadSection` reports the section's *declared* size and never the bytes the caller
had room for. The declared size is in the volume, four bytes at `0x90`:
`64 04 00 19` is 1124, `EFI_SECTION_RAW`, i.e. 1120 bytes of GUIDs, the erased pad
four bytes past the 70th, and 1124 is exactly the FFS size (1148) less its 24-byte
header - so no size field anywhere on the path is stale, and `entries=70` is forced.
The tool still prints the checksum at every 16-byte boundary, because it is what a
short read *would* have printed and it is what a mis-transcribed `sum=` looks like;
what it no longer is is a fingerprint of length. `a998b263` over the whole section
is instead a fingerprint of **identity** - a panel `sum=` that differs says the
running image is not this image. (The panel prints `%x` without zero-padding, so a
short value has to be read left-padded.)

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
# The Apriori section as the device reads it, and a replay of the promotion
# loop over this volume.
#
# This is the half of the measurement that the device cannot make about itself:
# `Fv->ReadSection` hands the dispatcher a buffer and a size, and everything
# downstream is `size / 16` entries.  So the numbers below - the section's declared
# size, the FFS size it should agree with, an additive checksum over the bytes, and
# the first and last GUID - are printed here for comparison against the panel's
# `P2 APRI` lines, which the firmware prints from exactly these values.  Two
# readings of the same section out of the same image agree or they do not, and a
# disagreement is a finding rather than a rounding error: the SEQ line is only
# `entries` characters long, so "46 characters" has two arithmetically different
# explanations - 46 entries scanned with every one matched, or 70 scanned with 46
# matched.
#
# Only the second is reachable, and the header line above is why: `entries` is
# `SizeOfBuffer / 16`, `SizeOfBuffer` is what `GetSection` reports, and `GetSection`
# reports the section's declared size - so `entries` is a property of the volume and
# not of the run. It is 70 here and it is 70 on the device, and the live question is
# instead `miss`, which separates a contiguous tail (miss=47) from a scattered set
# (miss<47).
#
# The replay below is the naive prediction: with the volume as this tool just walked
# it, every one of the 70 GUIDs is present as a type 0x07 file - except entry 0,
# which is DxeCore and matches nothing by construction, because the DXE_CORE branch
# of the walk fills in gDxeCoreLoadedImage->FilePath instead of calling
# CoreAddToDriverList. The firmware's own replay skips index 0 for exactly that
# reason, so this does too; otherwise both would report a miss at 0 on every boot
# and hide the one being looked for. So the replay says matched 1..69, miss none -
# and the run did not, which is the finding: `miss=` names where the volume and the
# device's discovered list stop agreeing.
# ---------------------------------------------------------------------------
print()
print("=== The Apriori section, as the device reads it ===")


def apriori_sum(data, upto=None):
    """The firmware's own `mP2ApriSum` (Dispatcher.c): ((sum * 31) + byte) over
    exactly `SizeOfBuffer` bytes, as UINT32. Replayed, not approximated."""
    s = 0
    for b in (data if upto is None else data[:upto]):
        s = (s * 31 + b) & 0xFFFFFFFF
    return s


ap_sum = apriori_sum(payload)
print(f"  bytes {len(payload)}  entries {len(payload) // 16}  sum {ap_sum:#x}")
print(f"  first {apriori[0]}  last {apriori[-1]}")
hdr = fv[o0 + 24:o0 + 28]
hdr_size = hdr[0] | (hdr[1] << 8) | (hdr[2] << 16)
print(f"  section header {hdr.hex(' ')} -> declared size {hdr_size} "
      f"(type {hdr[3]:#04x} = EFI_SECTION_RAW), i.e. {hdr_size - 4} bytes of GUIDs")
print(f"  FFS size {s0} - 24-byte header - 4-byte section header = {s0 - 28} "
      f"-> {'agrees' if s0 - 28 == hdr_size - 4 else 'DISAGREES'}")

# The panel's `P2 APRI bytes= entries= sum=` was written to say *which* of two
# failure shapes a run has: an Apriori read that came back short, or a discovered
# list that came up short. The first of those is not reachable - see the header
# above and `GetSection`'s `*BufferSize = SectionSize` - so what the triple says
# now is whether the running image is this image. `mP2ApriSum` is taken over
# `SizeOfBuffer`, and `SizeOfBuffer` is the declared size, so `sum=` is a checksum
# of the whole 1120 bytes and moves the moment any GUID in the array moves.
# Printing every 16-byte prefix keeps the old reading visible - it is what a short
# read would have printed, and `b4ba9d75` (47 entries) remains the value that
# would have meant one - and it is also the lookup for a `sum=` that was
# transcribed a digit wrong: a 4 for a 7, a 6 for an 8.
print("  sum at every 16-byte boundary, kept as what a short read would have printed:")
print("  (the panel prints `%x`, which does NOT zero-pad: read 921dcf9 as 0921dcf9)")
for start in range(0, len(payload) // 16, 4):
    row = []
    for n in range(start + 1, min(start + 5, len(payload) // 16 + 1)):
        row.append(f"{n:>3} {apriori_sum(payload, n * 16):08x}")
    print("     " + "   ".join(row))

ap_drivers = {fvinv.guid_str(g) for g, t, *_ in files if t == 0x07}
matched = [i for i, g in enumerate(apriori) if g in ap_drivers]
miss = next((i for i in range(1, len(apriori)) if apriori[i] not in ap_drivers), None)
print(f"  replay of the promotion loop over this volume (index 0 skipped): "
      f"matched {matched[0]}..{matched[-1]} of {len(apriori)} entries, "
      f"miss {miss if miss is not None else 'none'}")
if len(matched) == len(apriori) - 1 and miss is None:
    print("  -> over THIS volume entries 1..69 all match, so a panel `miss=none`\n"
          "     would mean the array was not this volume's, and any `miss=` value is\n"
          "     the entry where the device's discovered list stops agreeing with the\n"
          "     file table above - `miss=47` being where a contiguous tail begins")
else:
    print(f"  -> {len(apriori) - 1 - len(matched)} entries match no DRIVER file "
          f"here; the first is\n     ap{miss} {apriori[miss]} "
          f"({nm(apriori[miss])}), and the panel's miss GUID should be this one")

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

# ---------------------------------------------------------------------------
# The `miss` decoder.
#
# The panel's `P2 APRI miss=` is the lowest Apriori index whose GUID matched no
# entry in mDiscoveredList. If that list is short because the walk stopped, the
# stop has a position, and the walk is in physical order - so `miss` is a
# monotone step function of the stop, and it only takes as many values as there
# are gaps in the Apriori-named files. Printing that function turns the number
# from "a failure happened" into "the walk stopped here", which is the difference
# between a symptom and a location.
#
# It is also how the two hypotheses are told apart on the panel. A cut is one of
# these eight values; the tail assumption (ap1..ap46 matched, ap47..ap69 did not)
# is `miss=47`, which is not on the list at all - because a cut leaves a physical
# *suffix* missing and the tail set reaches back to the console drivers.
#
# The `SEQ len` column is what makes the table a decoder rather than a key. `P2
# SEQ` has been read three times and `P2 APRI miss=` never, and the length of the
# SEQ line *is* `mP2Apriori`, i.e. the promoted count - so a `miss` value is only
# admissible if some stop in its band also produces the SEQ length that was read.
# The table prints the range each band can produce; the block after it does the
# join against the length actually read off the panel.
# ---------------------------------------------------------------------------
print()
print("=== The `miss` decoder: which stop position each value names ===")
n, seen_at = 0, {}
for i, (g, t, *_r) in enumerate(files):
    if t == 0x07:
        n += 1
    seen_at[i] = n


def first_miss(stop):
    """`mP2ApriMiss` when the walk's last file was physical `stop`."""
    c = sorted(a for a in range(1, len(apriori))
               if (by_guid.get(apriori[a]) or 0) > stop)
    return c[0] if c else None


def promoted(stop):
    """How many Apriori entries match, i.e. `mP2Apriori`, and so the length of the
    `P2 SEQ` line: the entries whose file is at or before `stop`."""
    return sum(1 for a in range(1, len(apriori))
               if (by_guid.get(apriori[a]) or 0) <= stop)


NULLGUID = "0" * 32


def walk_last(stop):
    """What `P2 WALK last=` reads when the walk's last file was physical `stop`.

    The dispatcher's walk is type-filtered (`Type = mDxeFileTypes[Index]` before
    every GetNextFile, Dispatcher.c:1586-1602), so `mP2WalkLast` is copied only
    on a *successful* return and is therefore the last file **of that type** the
    walk got back - not the file at the stop position, which may be FREEFORM,
    PAD or DXE_CORE. Those differ on exactly the read this decoder is being used
    for: stops 49 and 50 are a driver and a driver, but the same query one file
    later is answered by a file the type filter would never have returned.
    `iter` is seen + 1 on a pass that ends by running out of files
    (`EFI_NOT_FOUND`), which is the pass a cut walk ends on.
    """
    best = NULLGUID
    for i in range(0, min(stop + 1, len(files))):
        if files[i][1] == 0x07:
            best = fvinv.guid_str(files[i][0])
    return best


# k runs over "the last physical file the walk looked at", not over a count, so
# the band printed is directly comparable to `P2 WALK seen` and not a translation
# of it. k = -1 is the degenerate case of a walk that listed nothing.
#
# The third column is the band's `mP2Apriori`, which is the *length of the `P2 SEQ`
# line* - and that is the column that matters, because the SEQ line's length is a
# reading this project has taken and `miss` is one it has not. `P2 SEQ` is built by
# walking the Apriori array and appending one character per entry that was
# promoted, so its length equals the promoted count, and `P2 APRI matched=..
# unhit=N` closes the identity `promoted + unhit = entries`. That means a stop
# position has to satisfy two panel readings at once, and most bands cannot.
bands, prev = [], "unset"
for k in range(-1, len(files)):
    m = first_miss(k)
    if m != prev:
        bands.append([k, k, m])
        prev = m
    else:
        bands[-1][1] = k
print(f"{'miss':>5}  {'names':36} {'stop in phys':>14} {'seen':>11} {'SEQ len':>11}")
for lo, hi, m in bands:
    seen_lo = seen_at.get(lo, 0) if lo >= 0 else 0
    seen_hi = seen_at.get(hi, 0) if hi >= 0 else 0
    who = f"{m} {nm(apriori[m])}" if m is not None else "none (every entry matched)"
    seq_lo = promoted(lo) if lo >= 0 else 0
    seq_hi = promoted(hi)
    print(f"{str(m) if m is not None else 'none':>5}  {who:36} "
          f"{lo:>6}..{hi:<6} {seen_lo:>4}..{seen_hi:<6} "
          f"{(str(seq_lo) if seq_lo == seq_hi else f'{seq_lo}..{seq_hi}'):>11}")
print(f"\n  known values: {sorted((b[2] for b in bands), key=lambda v: (v is None, v))}")
print("  -> a `miss` of anything else is not a short walk at all: the device is "
      "looking at a\n     volume whose Apriori-named files sit where this one's "
      "do not. The tail assumption\n     of step 4.12 wants miss=47, which is "
      "not on this list, because a cut leaves a\n     physical suffix missing "
      "and ap47..ap69 include files at physical 14, 20, 21 and 22.")
print("  -> and miss=22 ShmBridgeDxe is closed by the 5-vs-74 result above: a "
      "walk that stopped\n     before physical 74 did not load ShmBridgeDxe at "
      "74, so that row cannot be this run.")

# ---------------------------------------------------------------------------
# The join, and why the cut branch closes instead of narrowing.
#
# `len(P2 SEQ)` is `mP2Apriori`, so a stopped walk has to produce exactly 46
# promotions - and only two stops do (physical 49 and 50). But the SEQ line's
# *content* is a second constraint on the same stop, and it is the one that
# closes the branch. Each character is the load result of the driver promoted
# into that slot, and 22 is the one that settles it: ShmBridgeDxe sits at
# physical 74, so no stop below 74 can promote it, while every driver a stop at
# 49 or 50 *can* promote into that slot has an observed `L`. The string stops
# matching at two slots, and no other stop produces 46 promotions at all.
#
# What is left is not a location but the Apriori read itself, and the two readings
# of it that produce the observed SEQ exactly. Only one of them is reachable: the
# section header declares 1124 and `GetSection` reports the declared size, so the
# read cannot come back short and `entries=70` is forced. What the panel can still
# decide is `miss=` - whether the 23 entries that matched nothing are the array's
# tail (miss=47, the name tables stand) or scattered through it (miss<47, the
# tables shift from that index on) - and, if the image on the device is not this
# one, the `sum=`.
# ---------------------------------------------------------------------------
print()
print(f"=== The join against the `P2 SEQ` line actually read ({len(SEQ)} characters) ===")
obs = SEQ
cands = [k for k in range(-1, len(files)) if promoted(k) == len(SEQ)]
print(f"  stops whose promotion count is exactly {len(SEQ)}: "
      f"{', '.join(f'physical {k} (seen={seen_at[k]})' for k in cands)}")
for k in cands:
    matched = [a for a in range(1, len(apriori)) if (ap_phys.get(a) or 0) <= k]
    pred = "".join(obs[a - 1] if a - 1 < len(obs) else "?" for a in matched)
    diffs = [i for i, (p, o) in enumerate(zip(pred, obs)) if p != o and p != "?"]
    print(f"  stop at physical {k}: predicted SEQ {pred}")
    print(f"       {'REFUTED' if diffs else 'consistent'}"
          + (f" at slot(s) {diffs}" if diffs else ""))
    for i in diffs:
        m = matched[i]
        print(f"       slot {i}: a stop here promotes {nm(apriori[m])} (ap{m}, "
              f"physical {ap_phys[m]}),\n         whose own result is '{obs[m - 1]}'; "
              f"the panel shows '{obs[i]}', the result of\n         "
              f"{nm(apriori[i + 1])} (ap{i + 1}, physical {ap_phys[i + 1]}).")
print("  -> no stop on this volume produces the observed SEQ, so those 46 characters "
      "are not a\n     stopped discovery walk. The 23 absent Apriori entries were "
      "never asked for, or were\n     asked for by a walk that cannot lose a file "
      "it was handed.")
print("     (slot 17 shifts because ap14 PlatformInfoDxeDriver is at physical 52 - the "
      "lowest\n     Apriori-named file above the stop - so a stop leaves it out and "
      "every later slot\n     holds the next driver instead.)")
print()
print("  Two readings of `P2 APRI` produce the observed SEQ exactly, and only one of "
      "them is\n  reachable from the volume:")
print(f"    `bytes={47 * 16} entries=47 sum={apriori_sum(payload, 47 * 16):#x}`")
print(f"       needs the section header at 0x90 to declare {47 * 16 + 4} where it "
      f"declares\n       {hdr_size}. It does not, and `GetSection` reports what the "
      f"header declares, so the\n       Apriori section cannot come back "
      f"{len(payload) - 47 * 16} bytes short. REFUTED.")
print(f"    `bytes={len(payload)} entries={len(apriori)} sum={ap_sum:#x}`")
print("       the array was read whole, so 24 of its names matched nothing - index 0 "
      "(DxeCore,\n       never in mDiscoveredList) and the 23 drivers named at "
      "ap47..ap69, which must\n       therefore be missing from mDiscoveredList. "
      "That is the reading the run has.")
print("  `P2 STATS apriori=46/70` follows: `mP2AprioriCount` is the largest Apriori file "
      "size ever\n  seen, so the second number is 70 and `apriori=46/47` would mean the "
      "running image is not\n  this one. `P2 APRI last=` agrees: the whole array ends at "
      f"{apriori[-1]} (GraphicsConsoleDxe),\n  and D06A77F4-4874-5898-9421-303158ECEA1A "
      "(I2C, ap46) is where the refuted reading stops.")
print("  None of this explains the 27 failures. Every one of them - ap19, ap20, ap21 "
      "and\n  ap23..ap46 - is inside the first 46, so it is promoted under either "
      "reading, and `P2 ERR`\n  is the line that answers the 27. These 46 characters "
      "answer a different question: why the\n  batch is 46 long.")

_n07 = sum(1 for f in files if f[1] == 0x07)
_last07 = max(i for i, f in enumerate(files) if f[1] == 0x07)
print()
print(f"  -> and `P2 WALK t=0 seen={_n07} iter={_n07 + 1} "
      f"last={walk_last(len(files) - 1)}` is now a prediction rather\n     than a "
      f"fork: with every stop refuted, the walk reached all {_n07} DRIVER files. "
      f"That\n     `last=` is the last *driver* file and not the volume's last file - "
      f"the walk is\n     type-filtered, so the {len(files) - 1 - _last07} "
      f"FREEFORM/PAD/DXE_CORE files after physical {_last07} are never\n     returned "
      f"at t=0 and never update `mP2WalkLast`.")
print(f"  -> with the second reading refuted, the run's shape is fixed: ap1..ap46 "
      f"promoted and\n     ap47..ap69 not, i.e. `P2 APRI matched=1..46 unhit=24 "
      f"miss=47`. The SEQ matches it\n     by construction - which is exactly why the "
      f"SEQ cannot be used to support it - and what\n     is left to explain is the "
      f"other end: the 23 must be absent from a walk that this\n     tool predicts "
      f"reaches all {_n07} DRIVER files, while the absent set is contiguous in\n     "
      f"Apriori index ({47}..{69}) and scattered in physical order:")
print("     " + " ".join(f"ap{a}:{ap_phys.get(a)}" for a in range(47, 70)))

# ---------------------------------------------------------------------------
# The five `P2 WALK` lines the device should print, generated rather than
# transcribed.
#
# `mDxeFileTypes` in Dispatcher.c is
#   { EFI_FV_FILETYPE_DRIVER, EFI_FV_FILETYPE_COMBINED_SMM_DXE,
#     EFI_FV_FILETYPE_COMBINED_PEIM_DRIVER, EFI_FV_FILETYPE_DXE_CORE,
#     EFI_FV_FILETYPE_FIRMWARE_VOLUME_IMAGE }
# and the probe prints the *position* in that array, not the type value - so the
# DRIVER pass is t=0 and a t=7 on the panel is a line the code cannot emit. The
# walk is a do/while around GetNextFile, and the terminating call that returns
# EFI_NOT_FOUND is counted in `iter` and not in `seen`, so each line should read
# iter = seen + 1 on a pass that ended by running out of files. `last` is the GUID
# of the last file the pass was handed, which on a complete pass is the volume's
# highest-offset file of that type.
# ---------------------------------------------------------------------------
print()
print("=== The `P2 WALK` lines this volume should produce ===")
TYPES = [
    ("EFI_FV_FILETYPE_DRIVER", 0x07),
    ("EFI_FV_FILETYPE_COMBINED_SMM_DXE", 0xF3),
    ("EFI_FV_FILETYPE_COMBINED_PEIM_DRIVER", 0x08),
    ("EFI_FV_FILETYPE_DXE_CORE", 0x05),
    ("EFI_FV_FILETYPE_FIRMWARE_VOLUME_IMAGE", 0x0B),
]
for idx, (tname, tval) in enumerate(TYPES):
    of = [f for f in files if f[1] == tval]
    seen, itr = len(of), len(of) + 1
    lastg = fvinv.guid_str(of[-1][0]) if of else "0" * 36
    print(f"  P2 WALK t={idx} seen={seen} iter={itr} last={lastg}")
    print(f"      ({tname} = {tval:#04x}, {seen} file(s) in this volume)")
print("\n  -> t=0 seen=80 iter=81 is the line that matters, and the join above has "
      "already made it\n     a prediction rather than a fork: it refutes the two stops "
      "on the SEQ's *content*,\n     not merely on its length, so nothing short of a "
      "complete walk fits. The other four\n     lines are the control, and they should "
      "read exactly as above.")
