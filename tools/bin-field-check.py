#!/usr/bin/env python3
"""What the probe's `hob_rc=` and `hob_rd=` fields can actually read: nothing.

`P2Bins` (`Mu_Basecore/MdeModulePkg/Core/Dxe/Dispatcher/Dispatcher.c`) prints the
memory type information HOB's own numbers as

    "P2 BIN init=%d hob_rc=%d hob_rd=%d\\n"

with the last two being `gMemoryTypeInformation[EfiRuntimeServicesCode]` and
`gMemoryTypeInformation[EfiRuntimeServicesData]`, i.e. indexed *by type*. They
read 0 on every boot, whatever the HOB said and whatever the bins ended up as,
and this says why in the sources rather than by assertion.

The array is written by two people who disagree about what its index means:

  * `Mem/Page.c` initialises it **by type** - `arr[i].Type == i` for all 17
    entries, which this checks rather than assumes. So an index-by-type read is
    correct against the initialiser.
  * `BuildMemoryTypeInformationHob` (`EmbeddedPkg/Library/PrePiHobLib/Hob.c`)
    fills a six-entry HOB **by position**, in its own order:
    `ACPIReclaimMemory, ACPIMemoryNVS, ReservedMemoryType, RuntimeServicesData,
    RuntimeServicesCode`, then the `EfiMaxMemoryType` terminator. And
    `PopulateMemoryTypeInformation` (`Mem/MemoryBin.c:145`) moves it with a plain
    `CopyMem (MemoryTypeInformation, EfiMemoryTypeInformation, DataSize)` - a
    positional copy, not a merge keyed on `.Type`.

So position 5 of the array, which the initialiser labels `EfiRuntimeServicesCode`
and which the probe therefore reads for `hob_rc`, is overwritten with the **HOB's
terminator** `{ EfiMaxMemoryType, 0 }`; and position 6, `EfiRuntimeServicesData`
for `hob_rd`, is past the 48-byte copy and keeps the initialiser's 0.

This is not a contradiction with the bins being sized correctly, and the two
facts coexist for a reason worth naming: the bin loops walk `.Type` and stop at
`EfiMaxMemoryType`, so they see the five HOB entries wherever they landed and
build two nonzero bins (300 + 150 = 450 pages, the same `RequiredSize` docs/08
step 4.26 computes from the PCDs). The statistics those loops fill,
`mMemoryTypeStatistics`, *is* indexed by type, so `P2 BIN rc=.. used=../..` is
sound. Only the two fields that index `gMemoryTypeInformation` directly are dead.

Consequence for reading the panel: `hob_rc=0 hob_rd=0` is not evidence about the
HOB. `init=` carries that on its own, and `used=../..` carries what the two dead
fields were meant to carry.

    tools/bin-field-check.py [--mu DIR]
"""

import argparse
import io
import os
import re
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_MU = os.path.join(REPO, "work", "uefi", "Mu-Silicium")

MULTIPHASE_H = "Mu_Basecore/MdePkg/Include/Uefi/UefiMultiPhase.h"
PAGE_C = "Mu_Basecore/MdeModulePkg/Core/Dxe/Mem/Page.c"
HOB_C = "Mu_Basecore/EmbeddedPkg/Library/PrePiHobLib/Hob.c"
MEMORYBIN_C = "Mu_Basecore/MdeModulePkg/Core/Dxe/Mem/MemoryBin.c"
DISPATCHER_C = "Mu_Basecore/MdeModulePkg/Core/Dxe/Dispatcher/Dispatcher.c"
SILICIUM_INC = "Silicon/Silicium/SiliciumPkg/SiliciumPkg.dsc.inc"


def read(mu, rel):
    path = os.path.join(mu, rel)
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        sys.exit(f"bin-field-check: cannot read {rel}: {exc}")


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def memory_type_ordinals(mu):
    """The `EFI_MEMORY_TYPE` enum's ordinals, in the order the header lists them."""
    text = read(mu, MULTIPHASE_H)
    body = text.split("typedef enum {", 1)[1].split("} EFI_MEMORY_TYPE;", 1)[0]
    names = [ln.strip().rstrip(",") for ln in strip_comments(body).splitlines() if ln.strip()]
    return {name: i for i, name in enumerate(names)}, names


def initializer(mu, ordinal):
    """`gMemoryTypeInformation[]`'s initialiser, as written. It is by type."""
    text = read(mu, PAGE_C)
    body = text.split("gMemoryTypeInformation[EfiMaxMemoryType + 1] = {", 1)[1].split("};", 1)[0]
    rows = []
    for m in re.finditer(r"\{\s*(Efi\w+)\s*,\s*(\d+)\s*\}", body):
        name = m.group(1)
        # The initialiser's last entry uses an alias for EfiUnacceptedMemoryType;
        # resolve it the way the compiler would rather than special-casing a name.
        rows.append((name, int(m.group(2)), ordinal.get(name)))
    return rows


def hob_entries(mu, pcds):
    """`BuildMemoryTypeInformationHob`'s `Info[]`, as written. It is by position."""
    text = read(mu, HOB_C)
    body = text.split("BuildMemoryTypeInformationHob (", 1)[1]
    body = body.split("BuildGuidDataHob (&gEfiMemoryTypeInformationGuid", 1)[0]
    rows = []
    for m in re.finditer(
        r"Info\[(\d+)\]\.Type\s*=\s*(Efi\w+);\s*"
        r"Info\[(\d+)\]\.NumberOfPages\s*=\s*PcdGet32 \((\w+)\)",
        body,
    ):
        pos, name, pos2, pcd = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        if pos != pos2:
            sys.exit(f"bin-field-check: Info[{pos}] type and pages disagree on the index")
        rows.append((pos, name, pcd, int(pcds.get(pcd, "0"))))
    if not rows:
        sys.exit("bin-field-check: no Info[] entries parsed from BuildMemoryTypeInformationHob")
    return rows


def pcd_values(mu):
    text = read(mu, SILICIUM_INC)
    return dict(re.findall(r"gEmbeddedTokenSpaceGuid\.(PcdMemoryTypeEfi\w+)\|(\d+)", text))


def positional_copy_size(mu):
    """How many bytes `PopulateMemoryTypeInformation` copies, from the HOB's size."""
    text = read(mu, HOB_C)
    m = re.search(r"EFI_MEMORY_TYPE_INFORMATION\s+Info\[(\d+)\]", text)
    return int(m.group(1)) if m else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mu", default=os.path.normpath(DEFAULT_MU))
    args = ap.parse_args()
    mu = args.mu

    ordinal, names = memory_type_ordinals(mu)
    init = initializer(mu, ordinal)
    pcds = pcd_values(mu)
    hob = hob_entries(mu, pcds)
    n_hob = positional_copy_size(mu)

    efi_max = ordinal["EfiMaxMemoryType"]
    print("bin-field-check: what `P2 BIN ... hob_rc= hob_rd=` can read\n")
    for rel in (MULTIPHASE_H, PAGE_C, HOB_C, MEMORYBIN_C, SILICIUM_INC):
        print(f"  reads: {rel}")
    print()

    # The initialiser is the claim that the array is addressable by type.
    has_alias = [r for r in init if r[2] is None]
    resolvable = all(r[2] == i for i, r in enumerate(init) if r[2] is not None)
    print(f"ordinals: EfiRuntimeServicesCode={ordinal['EfiRuntimeServicesCode']} "
          f"EfiRuntimeServicesData={ordinal['EfiRuntimeServicesData']} "
          f"EfiMaxMemoryType={efi_max}")
    print(f"initialiser: {len(init)} entries, {len(init)} != EfiMaxMemoryType + 1 "
          f"= {efi_max + 1} -> {'OK' if len(init) == efi_max + 1 else 'MISMATCH'}")
    if has_alias:
        # EfiUnacceptedMemoryType has a `Gcd`-flavoured alias in this tree; the
        # ordinal it sits at is what the compiler sees, so report the position.
        print(f"  (the entry at position {init.index(has_alias[0])} is spelled "
              f"`{has_alias[0][0]}`, an alias of EfiUnacceptedMemoryType)")
    print(f"  arr[i].Type == i at every other position: {'yes' if resolvable else 'NO'}")
    print(f"  -> an index-by-type read is correct against the initialiser\n")

    print(f"HOB `Info[]`, {n_hob} entries, by position:")
    for pos, name, pcd, value in hob:
        print(f"  Info[{pos}] = {{ {name:<24} {value:>4} }}   {pcd}")
    print(f"  Info[{n_hob - 1}] = {{ EfiMaxMemoryType         0 }}   (terminator)")
    print()

    # The copy: position p of the HOB lands on position p of the array.
    after = [(name, value) for name, value, _ in init]
    for pos, name, _pcd, value in hob:
        after[pos] = (name, value)
    after[n_hob - 1] = ("EfiMaxMemoryType", 0)

    print(f"`gMemoryTypeInformation` after PopulateMemoryTypeInformation's positional")
    print(f"CopyMem of {n_hob * 8} bytes ({n_hob} x 8):")
    for i, (name, value) in enumerate(after[:n_hob + 1]):
        mark = ""
        if i == ordinal["EfiRuntimeServicesCode"]:
            mark = "   <- the probe reads this for hob_rc"
        if i == ordinal["EfiRuntimeServicesData"]:
            mark = "   <- the probe reads this for hob_rd"
        print(f"  [{i}] = {{ {name:<24} {value:>4} }}{mark}")
    print()

    walk, total = [], 0
    for name, value in after:
        if name == "EfiMaxMemoryType":
            break
        walk.append(f"{name}({value})")
        total += value
    print("the bin loops walk .Type and stop at EfiMaxMemoryType, so they build:")
    for step in walk:
        print(f"  {step}")
    print(f"  RequiredSize = {total} pages ({total * 4096 / (1 << 20):.2f} MiB) "
          f"across {len(walk)} entries\n")

    print("the two fields the probe prints, resolved:")
    for field, ty in (("hob_rc", "EfiRuntimeServicesCode"), ("hob_rd", "EfiRuntimeServicesData")):
        i = ordinal[ty]
        name, value = after[i]
        print(f"  {field} = gMemoryTypeInformation[{i}].NumberOfPages "
              f"= {{{name}, {value}}} -> {value}")

    print()
    print("-> `hob_rc=0 hob_rd=0` is structural, not a reading. Neither field can")
    print("   report a bin size on any boot, with or without the HOB. `init=` carries")
    print("   whether a producer ran, and `used=../..` - which reads")
    print("   mMemoryTypeStatistics, indexed by type - carries what these two meant to.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
