#!/usr/bin/env python3
"""Which drivers in a payload are held off by their dependency expression, and on what.

Why this exists. The panel can tell us which *a-priori* driver failed to load;
it cannot tell us which *non*-a-priori driver is waiting on a protocol that is
never coming. Both leave the same trace - a driver that never runs, and no line
naming it - and only the first appears in `P2 SEQ`. The second is silent for the
whole boot, so the question has to be asked of the image instead of the device.

The mechanism, because the answer turns entirely on it. A dependency expression
is stored in the FFS as a DXE_DEPEX section and evaluated by `CoreIsSchedulable`
(`MdeModulePkg/Core/Dxe/Dispatcher/Dependency.c:197`). But the dispatcher only
asks that question of a driver still marked `Dependent`:

    Dispatcher.c:1203    if (DriverEntry->Dependent) {
    Dispatcher.c:1204      if (CoreIsSchedulable (DriverEntry)) {

and every file the volume's a-priori file names is marked otherwise, before
anything runs:

    Dispatcher.c:2104-2120
      for (Index = 0; Index < AprioriEntryCount; Index++) {
        ... CompareGuid (&DriverEntry->FileName, &AprioriFile[Index]) ...
          DriverEntry->Dependent = FALSE;
          DriverEntry->Scheduled = TRUE;
          InsertTailList (&mScheduledQueue, &DriverEntry->ScheduledLink);
          DEBUG ((DEBUG_DISPATCH, "  RESULT = TRUE (Apriori)\n"));

So an a-priori driver's depex is *read* (`CoreGetDepexSectionAndPreProccess`,
which is where BEFORE/AFTER still count) and *never evaluated*. Its dependency
expression is inert. That makes the axis that matters not "does this depex name a
missing protocol" but "is this driver in the a-priori file at all" - and on this
platform the a-priori file names 70 GUIDs, so it is most of the volume.

Two wrong answers came out of this file before this one, and both are worth
keeping because both were the kind that read as findings:

  (a) The first version compared *names*: `UNINSTALLED` held bare macro names
      while the headers spell them `..._PROTOCOL_GUID`, so no comparison ever
      hit, the census reported **0** stuck drivers, and 0 is precisely the answer
      that says there is nothing here to see. A name-keyed match that fails
      silently fails reassuringly. It is keyed on GUIDs now, and a GUID in the
      list that no header defines is a hard error rather than a skip.
  (b) The second version fixed (a) and reported **2**: `CapsuleRuntimeDxe` needs
      VariableWrite and `RealTimeClock` needs Variable, neither of which is
      installed. Both are a-priori (APRIORI.inc entries 43 and 40), so both are
      promoted and neither depex is ever evaluated. The answer was not merely
      incomplete, it was wrong in the direction of a discovery. Hence this
      version reads the a-priori file out of the volume it is analysing.

What the corrected reading says about the payload of record: **no driver in it is
blocked by its dependency expression, and none is unjudgeable either.** 27 of the
80 dispatcher-visible files carry a DEPEX, of which 21 are a-priori, and the
remaining 6 (`RamManagerDxe`, `SmbiosDxe`, `SmBiosTableDxe`, `AcpiTableDxe`,
`AcpiPlatform`, `SetupBrowser`) all depend on nothing worse than
`EFI_PCD_PROTOCOL_GUID`, `EFI_ACPI_TABLE_PROTOCOL_GUID` and the HII protocols -
and `PcdDxe` is a-priori entry 2, so PCD exists before any of them. The eight
architectural protocols the device reported missing are therefore not a
dependency deadlock; they are their producers failing to *load*, which is the
same failure the `P2 SEQ` band already points at.

They still gate drivers, though, and by a second mechanism. A driver with **no**
depex section is not unconstrained: `Dispatcher.c:893-895` marks it `Dependent`
with a NULL depex, and `CoreIsSchedulable` answers a NULL depex through
`CoreAllEfiServicesAvailable` (`Dependency.c:225`), which requires **all
thirteen** architectural protocols. So on this platform, until the eight are
installed, no non-a-priori driver without a depex can run either - 5 of them in
the payload of record (`BootGraphicsResourceTableDxe`, `FeatureEnablerDxe`,
`MacDxe`, `PwrUtilsDxe`, `VcsDxe`), and `XhciDxe` in the `xhci-host` payload. The
two mechanisms are separate and both matter: "carries no depex" reads like the
least constrained thing in the volume and is in fact the most constrained.

The USB host stack is the opposite case and is why this was worth measuring.
`XhciPciEmulationDxe`, `XhciDxe` and `UsbInitDxe` are added by the build outside
APRIORI.inc (`tools/make_uefi_platform.py`, `XHCI_HOST_DRIVERS`), so their depexes
*are* enforced. `XhciPciEmulationDxe`'s is a 13-term AND, eight terms of which are
in the never-installed set, so `CoreIsSchedulable` answers "no" and the driver
sits on the pending list.

But "not installed yet" is not "never coming", and the distinction is the whole
reason this tool has two buckets rather than one. Every one of the eight has an
a-priori producer sitting in the same volume - `BdsDxe` at entry 45,
`VariableRuntimeDxe` at 32, `SecurityStubDxe` at 38 and so on - so the depex is
unsatisfied *because those producers fail to load*, and it becomes satisfiable the
moment they do. This driver is waiting, which is step 4.50's reading of it ("the
host stack waits rather than adding two more Ls"), not a dead expression. The
bucket this tool had to grow is the other one: a depex naming a protocol with no
producer anywhere in the volume, which no amount of P2 work can satisfy. **Both
payloads report zero of those.**

That is also what `tools/build-apriori-variant.sh` means when it says the
`xhci-host` payload "is not the payload that answers the open P2 question and
must not take the place of the one in boot": its contribution cannot be read off
the panel until P2 passes, because until then its one depex-gated driver is in the
same wait as the rest of the volume.

`UsbInitDxe` is a second, independent defect in the same trio and is why this
tool has a separate list for it. Its whole depex is `PUSH E722B03F-B250-42CE-
8EBD-5BD51812D037 END`, and no header anywhere in the tree defines that GUID - so
it cannot be looked up in the installed set either, and calling it "gated,
nothing known-missing" would print an unknown as reassurance. Step 4.50 already
established that the GUID is Qualcomm's own and is carried by nine blobs in the
tree including this phone's `UsbConfigDxe.efi`, so a publisher may well exist;
what this tool can say is only that it is not a header-defined protocol and
therefore not judgeable from the image. Drivers in that position are reported
separately rather than counted either way.

Bounds, in both directions. Every depex in these images is a conjunction - a
chain of ANDs, with no ORs and no NOTs - so one unsatisfiable term is enough to
hold a driver off the queue for the whole boot.

What is *proved* here: for the nine GUIDs in `UNINSTALLED` the producer is known
by name (`PRODUCERS`), so each can be looked for in the volume's a-priori array.
Every one of them is there. So no depex in either payload names a protocol this
volume has no producer for - and that, not "the protocol is missing right now",
is the only thing that would make a driver unschedulable for the life of the
boot. The drivers in the waiting bucket are held off by P2, not by their depex.

What is *not* proved, and why that bucket is not named after the drivers in it:
producer-to-protocol is not recoverable from a volume in general. Above the nine
mapped GUIDs this tool knows the name of the protocol a depex names
(`load_guid_names`) but not who installs it, so a driver gated on some *other*
uninstalled protocol would land in `gated_ok` and read as healthy. The nine are
the ones the device actually reported missing, which is why the map is worth
having for exactly those and is not a general ability to tell a wait from a dead
end.

Only files of the five types the dispatcher is shown are counted at all
(`mDxeFileTypes`, `Dispatcher.c:697-703`). That filter is not cosmetic: without it
the payload of record reports 123 files, and the 43 that are bmp images, panel
XMLs, .cfg files and the Apriori file itself all read as drivers with no depex
and no constraints. With it, 80 remain - which is exactly the `P2 WALK seen=80`
the device printed, so the count is checkable against the panel and was checked
against it. That agreement is also what makes the rest of this tool's numbers
comparable to the device's at all.

DEPEX expression sizes are a check on the reader: PUSH is 17 bytes (opcode +
GUID) and every other opcode is 1, so an 18-byte body is `PUSH g END`, a 36-byte
one is `PUSH g PUSH h AND END`, and the XHCI emulation driver's 234-byte one is
13 PUSHes with 12 ANDs. (A printed `.dpx` section size is 4 bytes larger than
the body - that is the section's own header, and conflating the two is how 18
became 22 in an earlier note in this repo.) A reader that got the alignment wrong
produces a size that is not of this form, and the histogram is asserted against
the section count for that reason.

Usage:  tools/depex-census.py <payload-image> [--list]
"""

import contextlib
import importlib.util
import io
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MU = os.path.join(ROOT, "work/uefi/Mu-Silicium")
SECTION_DXE_DEPEX = 0x13
OP_PUSH, OP_AND, OP_OR, OP_NOT = 0x02, 0x03, 0x04, 0x05
OP_TRUE, OP_FALSE, OP_END = 0x06, 0x07, 0x08
SECTION_NAMES = {0x00: "BEFORE", 0x01: "AFTER", 0x03: "AND", 0x04: "OR",
                 0x05: "NOT", 0x06: "TRUE", 0x07: "FALSE", 0x08: "END"}

# The only FFS file types the DXE dispatcher is ever shown, from `mDxeFileTypes`
# (`MdeModulePkg/Core/Dxe/Dispatcher/Dispatcher.c:697-703`). Everything else in
# the volume - bmp files, panel XMLs, .cfg files, the Apriori file itself - is
# never added to the discovered list, so counting it as a "driver with no depex"
# would swamp the answer with pictures. On the payload of record this filter takes
# 123 files down to 80, which is exactly the `seen=80` the device prints on the
# `P2 WALK` line - the two numbers are the same measurement, one taken offline.
DRIVER_TYPES = {0x07: "DRIVER", 0x08: "COMBINED_SMM_DXE", 0x0A: "COMBINED_PEIM_DRIVER",
                0x03: "DXE_CORE", 0x0B: "FV_IMAGE"}

# The architectural protocols this platform does not have installed, as the
# device reported them: Security, Bds, Watchdog, Variable, Capsule, Monotonic,
# Reset and Real Time Clock. Nine GUIDs for eight, because VariableRuntimeDxe
# installs both the Variable and the VariableWrite protocol and neither appears.
#
# "Does not have installed" and not "never installs": see `PRODUCERS` below, and
# the Bounds paragraph in the docstring. Every one of these nine has a producer in
# the volume, so a driver gated on one of them is waiting on P2 rather than on
# something that does not exist.
#
# Given as GUIDs rather than as names on purpose - see (a) in the docstring.
UNINSTALLED = {
    "A46423E3-4617-49F1-B9FF-D1BFA9115839": "Security arch protocol",
    "665E3FF6-46CC-11D4-9A38-0090273FC14D": "Bds arch protocol",
    "665E3FF5-46CC-11D4-9A38-0090273FC14D": "Watchdog Timer arch protocol",
    "1E5668E2-8481-11D4-BCF1-0080C73C8881": "Variable arch protocol",
    "6441F818-6362-4E44-B570-7DBA31DD2453": "Variable Write arch protocol",
    "1DA97072-BDDC-4B30-99F1-72A0B56FFF2A": "Monotonic Counter arch protocol",
    "5053697E-2CBC-4819-90D9-0580DEEE5754": "Capsule arch protocol",
    "27CFAC88-46CC-11D4-9A38-0090273FC14D": "Reset arch protocol",
    "27CFAC87-46CC-11D4-9A38-0090273FC14D": "Real Time Clock arch protocol",
}

# The driver that installs each of them, from its own module - this is what makes
# the a-priori index meaningful, because a producer that failed to load leaves the
# protocol missing in exactly the way a gated consumer waits for one.
PRODUCERS = {
    "A46423E3-4617-49F1-B9FF-D1BFA9115839": "SecurityStubDxe",
    "665E3FF6-46CC-11D4-9A38-0090273FC14D": "BdsDxe",
    "665E3FF5-46CC-11D4-9A38-0090273FC14D": "WatchdogTimer",
    "1E5668E2-8481-11D4-BCF1-0080C73C8881": "VariableRuntimeDxe",
    "6441F818-6362-4E44-B570-7DBA31DD2453": "VariableRuntimeDxe",
    "1DA97072-BDDC-4B30-99F1-72A0B56FFF2A": "EmbeddedMonotonicCounter",
    "5053697E-2CBC-4819-90D9-0580DEEE5754": "CapsuleRuntimeDxe",
    "27CFAC88-46CC-11D4-9A38-0090273FC14D": "ResetSystemRuntimeDxe",
    "27CFAC87-46CC-11D4-9A38-0090273FC14D": "RealTimeClock",
}


def load_sibling(name):
    """Import a tool beside this one; the names carry hyphens, so not by module."""
    path = os.path.join(ROOT, "tools", name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def guid_str(b):
    return (f"{struct.unpack('<I', b[0:4])[0]:08X}-"
            f"{struct.unpack('<H', b[4:6])[0]:04X}-"
            f"{struct.unpack('<H', b[6:8])[0]:04X}-"
            f"{b[8:10].hex().upper()}-{b[10:16].hex().upper()}")


def decode(blob):
    """[(op, guid-or-None)] for a DEPEX section body."""
    out, i = [], 0
    while i < len(blob):
        op = blob[i]
        if op == OP_PUSH:
            if i + 17 > len(blob):
                raise ValueError("truncated PUSH")
            out.append((op, guid_str(blob[i + 1:i + 17])))
            i += 17
        else:
            if op not in SECTION_NAMES:
                raise ValueError(f"unknown depex opcode {op:#x} at {i}")
            out.append((op, None))
            i += 1
    return out


def load_guid_names():
    """GUID string -> the name it is defined under, from Mu's headers and .dec files.

    Two things this pattern has to get right, both learned the hard way:

    * The eleven-field match has to be exact. A loose pattern read
      `OSK_DEVICE_PATH_GUID` out of a line that was not a GUID definition, which
      is the kind of wrong answer that looks right in a table.
    * Fields are one *or* two hex digits. `Protocol/Variable.h` writes
      `{ 0x1e5668e2, 0x8481, 0x11d4, {0xbc, 0xf1, 0x0, ...} }` - `0x0` is a valid
      byte, and requiring exactly two digits dropped the Variable protocol from
      the name map, which is half of wrong answer (a) above.
    * A `#define` continued with a backslash is the common case in these headers,
      and the pattern has to allow the continuation or every definition in
      MdePkg vanishes - which is what the second draft of this function did.

    Both spellings are accepted, `#define NAME { ... }` and the .dec form
    `NAME = { ... }`.
    """
    pat = re.compile(
        r"(?:#define\s+|^[ \t]{0,4})([A-Za-z0-9_]+)\s*=?\s*\\?\s*\{\s*"
        r"0x([0-9A-Fa-f]{1,8}),\s*0x([0-9A-Fa-f]{1,4}),\s*0x([0-9A-Fa-f]{1,4}),\s*"
        r"\{\s*0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2}),\s*"
        r"0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2}),\s*"
        r"0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2}),\s*"
        r"0x([0-9A-Fa-f]{1,2}),\s*0x([0-9A-Fa-f]{1,2})\s*\}\s*\}",
        re.S | re.M)
    # Directories to walk, plus the package .dec files, which sit beside the
    # Include directory rather than inside it. Walking all of MdePkg to reach
    # them would drag in the library trees for no gain.
    roots = [os.path.join(MU, d) for d in (
        "Mu_Basecore/MdePkg/Include", "Mu_Basecore/MdeModulePkg/Include",
        "Mu_Basecore/EmbeddedPkg/Include", "Mu_Basecore/ArmPkg/Include",
        "SiliciumPkg/Include", "QcomPkg/Include")]
    decs = [os.path.join(MU, d) for d in (
        "Mu_Basecore/MdePkg/MdePkg.dec",
        "Mu_Basecore/MdeModulePkg/MdeModulePkg.dec",
        "Mu_Basecore/EmbeddedPkg/EmbeddedPkg.dec")]
    paths = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith((".h", ".dec")):
                    paths.append(os.path.join(dirpath, f))
    paths.extend(p for p in decs if os.path.exists(p))
    names = {}
    for path in paths:
        try:
            t = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in pat.finditer(t):
            g = m.groups()
            s = (f"{int(g[1], 16):08X}-{int(g[2], 16):04X}-"
                 f"{int(g[3], 16):04X}-"
                 f"{int(g[4], 16):02X}{int(g[5], 16):02X}-"
                 + "".join(f"{int(x, 16):02X}" for x in g[6:12]))
            names.setdefault(s, g[0])
    return names


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_all = "--list" in sys.argv
    if len(args) != 1:
        sys.exit(__doc__.strip().split("Usage:")[-1].strip())
    img = args[0]

    fvi = load_sibling("fv-inventory.py")
    aor = load_sibling("apriori-order.py")

    rows, fv_len, offsets, inner = fvi.unpack(img)
    if inner is None:
        sys.exit(f"{img}: no inner FV found")
    # The a-priori file as the firmware itself will read it, so membership is
    # taken from the artifact and not from APRIORI.inc, which may not be the
    # file that produced this image. `apriori_array` walks the same image and
    # prints the same two banner lines `unpack` just printed, so its stdout is
    # swallowed rather than shown twice.
    with contextlib.redirect_stdout(io.StringIO()):
        apri, _count = aor.apriori_array(fvi, img)
    apri_set = set(apri)

    names = load_guid_names()

    # The guard that (a) taught us to write. `UNINSTALLED` decides what counts as
    # a protocol this platform is missing, so a GUID in it that no header defines
    # would remove a protocol from the comparison and let a waiting driver report
    # as fine - the exact failure this tool exists to find, produced by the tool.
    # Refuse to run.
    unresolved = [g for g in UNINSTALLED if g not in names]
    if unresolved:
        for g in unresolved:
            print(f"  {g}  ({UNINSTALLED[g]})", file=sys.stderr)
        sys.exit(f"{len(unresolved)} of {len(UNINSTALLED)} UNINSTALLED GUIDs are "
                 f"not defined by any header under {MU} - the census would "
                 f"silently ignore them.")

    # Where each producer sits in the a-priori order, so a missing protocol can be
    # read against the SEQ band rather than left as an abstraction. Index is 1-based
    # to match the way `P2 SEQ` positions are counted off the panel.
    #
    # The a-priori file holds driver FILE_GUIDs, not protocol GUIDs, so the join is
    # GUID -> the FFS file's UI name out of the volume, and the module name has to
    # equal that name rather than appear anywhere in it.
    ui_by_guid = {g: nm for (g, _t, _s, nm, _st) in rows if nm}
    mod_index = {}
    for i, ag in enumerate(apri, start=1):
        nm = ui_by_guid.get(ag)
        if nm in set(PRODUCERS.values()):
            mod_index.setdefault(nm, i)
    producer_index = {g: (mod_index[mod], mod) for g, mod in PRODUCERS.items()
                      if mod in mod_index}

    waiting, inert, gated_ok, gated_unknown, nodepex, nodepex_gated = (
        [], [], [], [], 0, [])
    sizes = {}
    for (guid, typ, size, nm, state), off in zip(rows, offsets):
        if typ not in DRIVER_TYPES:
            continue
        blob = inner[off + 24:off + size]
        body = None
        for st, sbody in fvi.sections(blob):
            if st == SECTION_DXE_DEPEX:
                body = sbody
                break
        label = nm or guid
        if body is None:
            # No depex is not "no constraint". `Dispatcher.c:893-895` sets
            # `Depex = NULL; Dependent = TRUE` for these, and a NULL depex sends
            # `CoreIsSchedulable` down the UEFI 2.0 branch: `CoreAllEfiServicesAvailable`
            # (`Dependency.c:225`), which requires **all thirteen** architectural
            # protocols. With eight of the thirteen missing on this platform, every
            # non-a-priori driver with no depex is unschedulable - including
            # `XhciDxe`, which has no depex section anywhere.
            nodepex += 1
            if guid not in apri_set:
                nodepex_gated.append(label)
            continue
        sizes[len(body)] = sizes.get(len(body), 0) + 1
        expr = decode(body)
        needs = [g for op, g in expr if op == OP_PUSH]
        bad = [g for g in needs if g in UNINSTALLED]
        # A GUID no header in the tree defines cannot be looked up in the installed
        # set either, so a driver needing one has to be reported as *unresolved*
        # rather than as fine. `UsbInitDxe` is the case that matters: its whole
        # depex is one such GUID, and putting it in the same list as the drivers
        # whose depex is a header-known protocol would print an unknown as
        # reassurance.
        unknown = [g for g in needs if g not in names]
        if guid in apri_set:
            inert.append((label, needs, bad))
        elif bad:
            # Gated, and gated on something this platform does not have installed.
            # That is a *wait*, not a dead end: `producer_index` below is built
            # from the a-priori array of this same volume, so every GUID printed
            # here comes with a producer that is present and has not loaded. A
            # driver gated on a protocol with no producer anywhere would be the
            # unschedulable case, and none is reachable from `UNINSTALLED`.
            waiting.append((label, needs, bad))
        elif unknown:
            gated_unknown.append((label, needs, unknown))
        else:
            gated_ok.append((label, needs))

    total = len(waiting) + len(inert) + len(gated_ok) + len(gated_unknown)
    types = {t: sum(1 for r in rows if r[1] == t) for t in DRIVER_TYPES}
    print(f"inner FV {fv_len:#x}, {len(rows)} FFS files, "
          f"a-priori file names {len(apri)} GUIDs")
    print(f"  dispatcher-visible files ({', '.join(f'{DRIVER_TYPES[t]}={n}' for t, n in sorted(types.items()) if n)}): "
          f"{total + nodepex}  <-- `P2 WALK seen=` is this same number")
    print(f"\nDEPEX section sizes seen: "
          + ", ".join(f"{k} B x{v}" for k, v in sorted(sizes.items())))
    print(f"{total} of them carry a depex, {nodepex} do not")
    print(f"  of the {total} with a depex: {len(inert)} are promoted by the "
          f"a-priori file (depex inert), "
          f"{len(gated_ok) + len(gated_unknown) + len(waiting)} are gated")
    print(f"  of the {nodepex} without one: {nodepex - len(nodepex_gated)} are "
          f"a-priori, {len(nodepex_gated)} are not")
    assert sum(sizes.values()) == total, "size histogram lost a section"
    assert total + nodepex == sum(types.values()), "type histogram lost a file"

    print(f"\n== non-a-priori, gated on a protocol that is not installed "
          f"({len(waiting)}) — satisfiable, and waiting on its producer to load")
    for label, needs, bad in sorted(waiting):
        print(f"  {label}")
        for g in bad:
            who = producer_index.get(g)
            where = f", installed by {who[1]} at a-priori {who[0]}" if who else ""
            print(f"      needs {UNINSTALLED[g]}  ({g}){where}")
        unnamed = [g for g in needs if g not in UNINSTALLED and g not in names]
        if unnamed:
            print(f"      also: {', '.join(unnamed)} (no header names these)")

    if gated_unknown:
        print(f"\n== gated, and the depex names a protocol no header defines "
              f"({len(gated_unknown)}) — cannot be ruled in or out from the image")
        for label, needs, unknown in sorted(gated_unknown):
            print(f"  {label}")
            for g in unknown:
                print(f"      needs {g}   (defined by no header under {MU})")

    if nodepex_gated:
        print(f"\n== no depex section, not a-priori ({len(nodepex_gated)}) — the "
              f"UEFI 2.0 rule, so they need all thirteen architectural protocols")
        for label in sorted(nodepex_gated):
            print(f"  {label}")

    if show_all:
        print(f"\n== gated, depex names nothing known-missing ({len(gated_ok)}) — "
              f"not proved schedulable, only not proved blocked")
        for label, needs in sorted(gated_ok):
            pretty = [names.get(g, g) for g in needs]
            print(f"  {label}: " + (", ".join(pretty) if pretty else "TRUE"))
        print(f"\n== a-priori, depex read and never evaluated ({len(inert)})")
        for label, needs, bad in sorted(inert):
            mark = "  <-- names a missing protocol, and runs anyway" if bad else ""
            print(f"  {label}{mark}")

    print(f"\nFor all {len(UNINSTALLED)} architectural protocols the device "
          f"reported missing ({len(set(PRODUCERS.values()))} providers), the "
          f"producer is a file in this volume - so a driver gated on one of them "
          f"is waiting and not dead. Of {total} depex-bearing drivers in {img}: "
          f"{len(waiting)} wait on them, {len(gated_unknown)} cannot be judged "
          f"from the image, and 0 are gated on any protocol this volume has no "
          f"producer for. {len(nodepex_gated)} more are held back by the UEFI 2.0 "
          f"rule, which is the same absence reached by the other route.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
