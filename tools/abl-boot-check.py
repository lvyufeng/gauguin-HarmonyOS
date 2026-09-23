#!/usr/bin/env python3
"""Replay ABL's boot-image decision path over a payload, offline.

Why this exists: the five P1 variants in work/out differ in a handful of header
properties, and docs/07 built a hypothesis on each of them being consulted by the
bootloader. Reading Qualcomm's own `QcomModulePkg` says otherwise for most of
them, and the cost of finding that out by flashing is five device sessions on a
device that needs a physical reset to recover from a bad one.

So this walks the same code the bootloader runs, in the same order, and prints
the verdict along with the vendor's own error text for whichever check fails:

    CheckImageHeader        magic, page size, sizes, the v2 dtb_size, overflow
    DTBImgCheckAndAppendDT  where ABL computes the DTB to be, vs where it is
    UpdateKernelModeAndPkg  gzip or raw, patched-kernel magic, ARM\\x64 magic
    UpdateBootParams        the load addresses, from the platform memory map
    GZipPkgCheck            decompress, then the two runtime size checks

and then the stage that decides what the kernel is actually handed, which is not
in the image at all - the `dtbo` partition, whose presence and shape choose
between ABL's two DTB paths (`--dtbo`, defaulting to the local dump):

    LoadAndValidateDtboImg  the table header, entry size, entry count
    GetSocDtb               which tree out of the boot image's DTB slot is used
    GetBoardDtb             which dtbo entry counts as the board's overlay
    ApplyOverlay            whether that overlay merges into our tree

The last one is where this device parts company with a plain payload. Validating
dtbo puts ABL on the *overlay* path, and the overlay is a dtc `-@` blob whose
fragments carry `target = <0xffffffff>` plus a `__fixups__` table saying which
symbol each placeholder stands for. `ufdt_overlay_do_fixups` rewrites those from
the *main* tree's `/__symbols__`, and returns -1 if either node is missing - so a
tree built without `-@` cannot carry the overlay, and ABL answers

    ApplyOverlay: ufdt apply overlay failed

with EFI_NOT_FOUND, before the kernel's first instruction. That refusal is
indistinguishable from a payload that never ran, which is why it is measured here
rather than by flashing.

The geometry is the part that is not in the image. ABL reads `KernelBaseAddr`
and `KernelSize` as UEFI runtime variables, so it is set by the stage before it;
the defaults below come from this device's own memory map, in
device/config/uefiplat.cfg, whose entry

    0xA2400000, 0x08000000, "Kernel", AddMem, SYS_MEM, ..., Reserv

is the region ABL carves the kernel, ramdisk and device tree out of. If the
variables are set, they are set to this - the region is named for it - but it is
an inference from the map and not a measurement, so it is printed rather than
assumed, and --kernel-base/--kernel-size override it.

Usage:  tools/abl-boot-check.py work/out/boot-pstore-*.img
        tools/abl-boot-check.py --kernel-base 0xA2400000 --kernel-size 0x8000000 <image>
        tools/abl-boot-check.py --dtbo ~/backup/gauguin/images/part-dtbo.img <image>

It exits nonzero if any image fails, so tools/build-p1-payloads.sh runs it as the
last step and no payload reaches a device without having been replayed first.
"""
import argparse
import glob
import os
import struct
import sys
import zlib

# tools/ - fdt.py is the shared reader for the two containers ABL consumes, and
# gauguin.py the two device ids this replay and make_dtbo_sinks.py both key off.
# Neither tool owns them, so the two cannot disagree about what the phone is.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fdt      # noqa: E402
import gauguin  # noqa: E402

# --- constants, from QcomModulePkg ------------------------------------------
MAGIC = b"ANDROID!"
PAGE_MAX = 4096                     # BOOT_IMG_MAX_PAGE_SIZE
DT_SIZE_2MB = 2 * 1024 * 1024       # reserved for the DT inside the kernel region
KERNEL_64BIT_LOAD_OFFSET = 0x80000  # added to KernelBaseAddr before the kernel is copied
KERNEL64_HDR_MAGIC = 0x644D5241     # "ARM\x64", at kernel64_hdr offset 56
PATCHED_KERNEL_MAGIC = b"UNCOMPRESSED_IMG"
PATCHED_KERNEL_HEADER_SIZE = 20
DTB_OFFSET_LOCATION_IN_ARCH32_KERNEL_HDR = 0x2C
KERNEL64_HDR_SIZE = 64              # Code0,Code1,TextOffset,ImageSize,Flags,Res2..4,magic,Res5

# From device/config/uefiplat.cfg - the "Kernel" memory-map entry.
GAUGUIN_KERNEL_BASE = 0xA2400000
GAUGUIN_KERNEL_SIZE = 0x08000000

# --- header field offsets ---------------------------------------------------
OFF_KERNEL_SIZE, OFF_KERNEL_ADDR = 8, 12
OFF_RAMDISK_SIZE, OFF_RAMDISK_ADDR = 16, 20
OFF_SECOND_SIZE = 24
OFF_PAGE_SIZE, OFF_HEADER_VERSION = 36, 40
OFF_RECOVERY_DTBO_SIZE = 1632      # sizeof(boot_img_hdr) == 1632, packed
OFF_HEADER_SIZE = 1644
OFF_DTB_SIZE = OFF_RECOVERY_DTBO_SIZE + 16   # v1 struct is 16 packed
OFF_DTB_ADDR = OFF_DTB_SIZE + 4


def r32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def r64(d, o):
    return struct.unpack_from("<Q", d, o)[0]


def round_page(n, page):
    """ROUND_TO_PAGE(n, page - 1): the vendor's macro is mask-based, not div."""
    mask = page - 1
    return (n + mask) & ~mask if mask else n


def add_of(a, b):
    """ADD_OF: the vendor's checked 32-bit add, 0 meaning it wrapped."""
    s = (a + b) & 0xFFFFFFFF
    return 0 if s < a else s


class Report:
    def __init__(self):
        self.failed = None
        self.notes = []

    def fail(self, check, message):
        self.failed = (check, message)
        return False

    def note(self, text):
        self.notes.append(text)


def pick_board_entry(variant, entries):
    """Which dtbo entry GetBoardDtb() lands on, as far as its inputs allow.

    GetBoardDtb runs ReadDtbFindMatch(..., VARIANT_MATCH) over every entry and
    keeps the best. ReadDtbFindMatch compares each entry's `qcom,board-id` cell 0
    against the *device's* variant id from the CDT - not against the base tree,
    which is why the stock base tree declaring `<0 0>` does not stop ABL from
    picking the Gauguin overlay. Entry 13 is the only one carrying 0x23.
    """
    if variant is None:
        return None
    for e in entries:
        if e.get("ok") and e.get("board_id") and e["board_id"][0] == variant:
            return e
    return None


def device_variant(tree):
    """The variant id to rank dtbo entries by.

    A base tree that declares a nonzero `qcom,board-id` cell 0 is asserting the
    variant it is for, and ReadDtbFindMatch hard-fails it if that is not the
    device's. A tree declaring 0 is a default match - it asserts nothing, so the
    variant has to come from the device, which offline means the constant.
    """
    bid = tree.get("board_id") or ()
    if bid and bid[0]:
        return bid[0], "declared by the base tree"
    return gauguin.GAUGUIN_VARIANT_ID, "from the device (the base tree declares none)"


def overlay_gate(tree, entry):
    """ufdt_overlay_do_fixups(), for one board overlay against one main tree.

    Returns (ok, missing) where `missing` is every symbol in the overlay's
    `__fixups__` that the main tree cannot resolve. The function returns -1 the
    moment one is absent - and ABL turns that into "ApplyOverlay: ufdt apply
    overlay failed", EFI_NOT_FOUND, and a boot that never reaches the kernel.
    """
    if not tree.get("symbols_node"):
        return False, list(entry.get("fixups") or {})
    syms = tree.get("symbols") or {}
    return True, [s for s in (entry.get("fixups") or {}) if s not in syms]


def check_dtbo(path):
    """Which of ABL's two DTB paths this device's dtbo selects."""
    ok, f, reason, entries = fdt.dtbo_table(path)
    print(f"dtbo {path}")
    if not ok:
        print(f"     \033[33mdoes not validate\033[0m: {reason}")
        print("     -> DTBImgCheckAndAppendDT takes the DeviceTreeAppended branch: "
              "the boot image's\n        DTB slot is walked as a table and an entry "
              "is copied in. No overlay.")
        return None
    print(f"     \033[32mvalid\033[0m  {f['entry_count']} entries, "
          f"TotalSize {f['total_size']:,}, entry size {f['entry_size']}")
    print("     -> LoadAndValidateDtboImg succeeds, so ABL takes the *overlay* "
          "branch: GetSocDtb\n        picks a tree out of the boot image's DTB slot, "
          "then the matching board\n        overlay is merged into it. (The caller "
          "names its result `DtboImgInvalid`\n        and tests `if (!DtboImgInvalid)`, "
          "but the function returns TRUE on success -\n        so the branch that "
          "looks like \"dtbo is fine, use the appended tree\" is the\n        one that "
          "skips the appended tree. Read the function, not the name.)")
    d = open(path, "rb").read()
    entries = fdt.dtbo_entries(d, entries)
    total = sum(len(e.get("frags") or ()) for e in entries if e.get("ok"))
    targets = {}
    for e in entries:
        for t in e.get("frags") or ():
            targets[t] = targets.get(t, 0) + 1
    print(f"     {total} fragments across the entries; "
          f"{sum(len(e.get('fixups') or {}) for e in entries if e.get('ok'))} "
          f"fixup symbols in total")
    board = [e for e in entries if e.get("ok") and e.get("board_id")
             and e["board_id"][0] == gauguin.GAUGUIN_VARIANT_ID]
    if board:
        print(f"     GetBoardDtb() ranks these by the device's variant id "
              f"{gauguin.GAUGUIN_VARIANT_ID:#x}; entry "
              + ", ".join(str(e["index"]) for e in board)
              + f" is the one carrying it ({board[0]['model']})")
    if total:
        shown = ", ".join(f"{t:#x} x{n}" if isinstance(t, int) else f"{t} x{n}"
                          for t, n in sorted(targets.items(), key=lambda kv: -kv[1])[:4])
        print(f"     fragment targets (before fixups): {shown}")
        if set(targets) == {0xFFFFFFFF}:
            print("     every fragment target is still the 0xffffffff placeholder, "
                  "which is what a\n     dtc `-@` overlay looks like *before* "
                  "ufdt_overlay_do_fixups() rewrites\n     it from __symbols__ - not "
                  "an overlay that does nothing.")
    print()
    return entries


def check_image_header(d, r):
    """CheckImageHeader(), for the non-recovery case this project boots."""
    if d[:8] != MAGIC:
        return r.fail("CheckImageHeader", "Invalid boot image header")
    hv = r32(d, OFF_HEADER_VERSION)
    if hv >= 3:
        return r.fail("CheckImageHeader",
                      "header_version 3 needs a vendor_boot image; this project "
                      "builds v2")
    ks, rs, ss = r32(d, OFF_KERNEL_SIZE), r32(d, OFF_RAMDISK_SIZE), r32(d, OFF_SECOND_SIZE)
    page = r32(d, OFF_PAGE_SIZE)
    if not ks or not page:
        return r.fail("CheckImageHeader",
                      f"Invalid image Sizes (KernelSize={ks}, PageSize={page})")
    if page != PAGE_MAX and page > PAGE_MAX:
        return r.fail("CheckImageHeader",
                      f"Invalid image pagesize (MAX={PAGE_MAX}, PageSize={page})")
    kpa = round_page(ks, page)
    if not kpa:
        return r.fail("CheckImageHeader", f"Integer Overflow: Kernel Size = {ks}")
    rpa = round_page(rs, page)
    if rs and not rpa:
        return r.fail("CheckImageHeader", f"Integer Overflow: Ramdisk Size = {rs}")
    dt = r32(d, OFF_DTB_SIZE) if hv == 2 else 0
    dta = round_page(dt, page)
    if dt and not dta:
        return r.fail("CheckImageHeader", f"Integer Overflow: dt Size = {dt}")
    size = add_of(page, kpa)
    if not size:
        return r.fail("CheckImageHeader", f"Integer Overflow: Actual Kernel size = {kpa}")
    size = add_of(size, rpa)
    if not size:
        return r.fail("CheckImageHeader", "Integer Overflow: ImgSizeActual")
    size = add_of(size, dta)
    if not size:
        return r.fail("CheckImageHeader", "Integer Overflow: ImgSizeActual, DtSizeActual")
    r.header = dict(hv=hv, kernel_size=ks, ramdisk_size=rs, second_size=ss, page=page,
                    dtb_size=dt, dtb_addr=r64(d, OFF_DTB_ADDR) if hv >= 2 else 0,
                    header_size=r32(d, OFF_HEADER_SIZE) if hv >= 1 else 0,
                    kernel_addr=r32(d, OFF_KERNEL_ADDR),
                    ramdisk_addr=r32(d, OFF_RAMDISK_ADDR),
                    recovery_dtbo_size=r32(d, OFF_RECOVERY_DTBO_SIZE) if hv >= 1 else 0,
                    kernel_pages=kpa // page, ramdisk_pages=rpa // page,
                    second_pages=round_page(ss, page) // page,
                    image_size_actual=size)
    return True


def check_dtb_offset(d, r):
    """DTBImgCheckAndAppendDT(), v2 branch.

    ABL does not read dtb_addr. It computes where the DTB must be:
        page * (1 + kernel + ramdisk + second + recovery_dtbo pages)
    and then requires a valid fdt there whose totalsize fits in the region the
    header declared (dtb_size).
    """
    h = r.header
    pages = 1 + h["kernel_pages"] + h["ramdisk_pages"] + h["second_pages"]
    pages += round_page(h["recovery_dtbo_size"], h["page"]) // h["page"]
    off = h["page"] * pages
    h["abl_dtb_offset"] = off
    region = h["dtb_size"] + off          # ABL's `ImageSize` for this function
    if off >= region:
        return r.fail("DTBImgCheckAndAppendDT", "Dtb offset goes beyond the image size")
    totalsize = fdt.header_ok(d, off)
    if totalsize is None:
        h["dtb_present"] = False
        return r.fail("DTBImgCheckAndAppendDT",
                      f"no valid fdt at ABL's computed offset {off:#x}")
    h["dtb_present"] = True
    h["dtb_totalsize"] = totalsize
    if (region - off) < totalsize:
        return r.fail("DTBImgCheckAndAppendDT", "Dtb offset goes beyond the image size")
    # The slot is a *table*: GetSocDtb() walks it as concatenated fdt blobs and
    # ReadDtbFindMatch() on each, keeping the best. A tree with no qcom,msm-id is
    # never selectable at all (GetPlatformMatchDtb leaves DtMatchVal at
    # NONE_MATCH), and one whose platform id is another SoC is rejected outright,
    # so what matters is the tree that answers to this device's own chip id.
    trees, p = [], off
    while p + 40 <= region:
        t = fdt.header_ok(d, p)
        if t is None or p + t > region:
            break
        props = fdt.root_props(d, p, t)
        trees.append(dict(off=p, totalsize=t, props=props,
                          msm_id=fdt.idcells(props.get("qcom,msm-id")),
                          board_id=fdt.idcells(props.get("qcom,board-id")),
                          model=fdt.strval(props.get("model", b"")),
                          symbols_node=bool(fdt.child_props(d, p, t, "__symbols__")),
                          symbols=fdt.symbols(d, p, t),
                          phandles=fdt.phandles(d, p, t)))
        p += t
    h["dtb_trees"] = trees
    h["dtb_is_multi"] = len(trees) > 1

    def matches(t):
        return any((c & 0xFFFF) in gauguin.GAUGUIN_PLATFORM_IDS for c in (t["msm_id"] or ()))

    selected = next((t for t in trees if matches(t)), None)
    if selected is None:
        selected = next((t for t in trees if t["msm_id"]), trees[0] if trees else None)
        if selected is not None:
            r.note(f"no tree in the slot carries msm-id {gauguin.GAUGUIN_PLATFORM_IDS} - "
                   f"GetSocDtb() would print \"No match found for Soc Dtb type\" and "
                   f"hand over nothing; reading {selected['off']:#x} anyway")
    h["dtb_selected"] = selected
    if h["dtb_is_multi"]:
        r.note(f"the slot holds {len(trees)} concatenated trees, not one - ABL "
               f"selects among them by qcom,msm-id; using the one at "
               f"{selected['off']:#x} ({selected['model'] or 'unnamed'})")
    ROOT = ("qcom,msm-id", "qcom,board-id", "qcom,pmic-id", "qcom,foundry-id",
            "qcom,softsku-id")
    h["dtb_root"] = {k: selected["props"].get(k) for k in ROOT}
    h["dtb_has_symbols"] = selected["symbols_node"]
    return True


def check_overlay(d, r, dtbo_entries):
    """ApplyOverlay(), the stage that decides what tree the kernel is handed.

    Validating dtbo puts ABL on the overlay path, and there is no way off it:

      1. GetSocDtb() only skips the overlay (`DtboNeed = FALSE`) when the tree it
         selected sets every bit of ALL_BITS_SET - which includes a PMIC model
         match for all sixteen indices and SOFTSKU_EXACT_MATCH, so it needs
         `qcom,pmic-id` and `qcom,softsku-id`. No tree in this device's boot image
         declares either; not ours, not the stock one. Dead branch.
      2. GetBoardDtb() then ranks every dtbo entry by the *device's* variant id,
         and lands on entry 13 - the only `board-id = <0x23 0>` in the table.
      3. ufdt_overlay_do_fixups() has to resolve that entry's `__fixups__` symbols
         against the selected tree's `/__symbols__`, and returns -1 if either node
         is missing. ABL turns that into "ApplyOverlay: ufdt apply overlay failed",
         EFI_NOT_FOUND, and a boot that never reaches the kernel.
    """
    tree = r.header.get("dtb_selected")
    if not tree or not dtbo_entries:
        return True
    variant, why = device_variant(tree)
    entry = pick_board_entry(variant, dtbo_entries)
    if entry is None:
        r.note(f"no dtbo entry carries board-id <{variant:#x} 0>, so GetBoardDtb "
               f"would find no board tree for this device; the overlay gate was "
               f"not evaluated")
        return True
    r.header["overlay_entry"] = entry
    r.note(f"the board overlay is dtbo entry {entry['index']} "
           f"({entry['model'] or 'unnamed'}): the device's variant id {variant:#x} "
           f"comes {why}")
    if not (tree["props"].get("qcom,pmic-id")
            and tree["props"].get("qcom,softsku-id")):
        # ALL_BITS_SET wants a PMIC model match for every index plus SOFTSKU, so
        # the "exact DTB match, no dtbo search" branch is unreachable without one.
        r.note("this tree declares neither qcom,pmic-id nor qcom,softsku-id, so "
               "CheckAllBitsSet() cannot pass and DtboNeed stays TRUE - the "
               "overlay is appended either way")
    ok, missing = overlay_gate(tree, entry)
    if ok:
        if missing:
            r.note(f"overlay entry {entry['index']} resolves against this tree "
                   f"({len(missing)} of {len(entry['fixups'])} symbols absent)")
        return sink_check(tree, entry, r)
    r.header["overlay_missing"] = missing
    n, total = len(missing), len(entry.get("fixups") or {})
    have = "no /__symbols__ at all" if not tree["symbols_node"] else \
        f"missing {n} of {total} symbols"
    return r.fail("ApplyOverlay",
                  f"ufdt apply overlay failed: this tree has {have}, and the board "
                  f"overlay (dtbo entry {entry['index']}, "
                  f"{entry['model'] or 'unnamed'}) arrives with "
                  f"{total} __fixups__ it has to resolve against them")


def sink_check(tree, entry, r):
    """What the tree's symbols point at, and whether that is safe to merge into.

    `ufdt_overlay_apply_fragment()` does not merge a fragment into the node the
    symbol *names* - it reads that node's phandle, then looks the phandle up in a
    table built from the whole tree. So a tree whose symbols point at real device
    nodes hands the vendor overlay those nodes to merge into, and a tree whose
    sink phandles collide with a real node's sends the overlay somewhere else
    entirely. tools/make_dtbo_sinks.py puts every symbol under `/__sink__`, which
    is a node with no `compatible`: `of_platform_bus_create()` skips such a node
    without recursing, so nothing merged inside it is ever populated as a device
    on the Linux side either.
    """
    syms = tree.get("symbols") or {}
    needed = [s for s in (entry.get("fixups") or {}) if s in syms]
    sinks = [s for s in needed if syms[s].startswith("/__sink__/")]
    other = [s for s in needed if not syms[s].startswith("/__sink__/")]
    ph = tree.get("phandles") or {}
    if not sinks:
        return True
    used = {syms[s] for s in sinks}
    taken = {p: v for p, v in ph.items() if len(v) > 1}
    if taken:
        where = "; ".join(f"{p:#x} at {', '.join(v)}" for p, v in list(taken.items())[:3])
        return r.fail("ApplyOverlay",
                      f"{len(taken)} phandle(s) have more than one owner ({where}). "
                      f"ufdt_get_node_by_phandle() binary-searches a sorted table of "
                      f"them and returns whichever sorts first, so the vendor overlay "
                      f"would merge into an arbitrary one of the two nodes.")
    sink_ph = [p for p, v in ph.items() if v[0] in used]
    r.note(f"{len(syms)} symbols, {len(sinks)} of the overlay's {len(needed)} pointing "
           f"into /__sink__ ({len(sink_ph)} phandles, "
           f"{min(sink_ph):#x}-{max(sink_ph):#x}), none of them shared with a real node"
           if sink_ph else
           f"{len(syms)} symbols, {len(sinks)} of the overlay's {len(needed)} pointing "
           f"into /__sink__, which carries no phandles at all")
    if other:
        r.note(f"{len(other)} of the overlay's symbols resolve to nodes outside "
               f"/__sink__ ({', '.join(other[:4])}) - the vendor overlay merges into "
               f"those, which are real nodes in this tree")
    return True


def check_regions(d, r):
    """The two addresses in the payload that have to be inside *free* DRAM to work.

    ABL checks neither. It looks at the header, the image size, the DTB slot and
    the overlay - not at what the tree says about memory, because it is about to
    *overwrite* that part of the tree itself (UpdateDeviceTree() replaces the
    /memory node's reg with the RAM partition table before the jump). So a
    reserved-memory region that is not inside any of those partitions is a
    payload bug that only shows up inside the kernel, where a payload with no
    console cannot report it.

    It is computable offline because both halves are known: the regions from the
    tree we built, the partitions and the firmware's own carveouts from the phone
    (gauguin.DRAM and gauguin.PHONE_RESERVED, both measured from the running
    phone's /proc/device-tree, i.e. after ABL had patched it).

    There are two ways to get this wrong and both have been made here. The log
    region was first copied from the boot image's "APQ 8016 SBC" tree, where
    0xbff00000 is the top of that board's RAM; on this phone it is in the hole
    between bank 0 (ends 0xbbb00000) and bank 1 (starts 0xc0000000). Moving it
    into bank 1 fixed that and made the second mistake: 0xc4000000 is inside the
    0x7b00000 the phone's tree removes from the *start* of bank 1, so it was in
    DRAM and in the modem-and-DSP's memory at once. Both faults fire from a
    postcore_initcall, before any console exists, so both look like a payload that
    never ran.

    The command line is checked as well as the node, because with
    `ramoops.mem_address=` present it is the command line that wins:
    ramoops_register_dummy() and platform_driver_register() are in the same
    postcore_initcall, while the device-tree probe waits for the platform devices
    of_platform_default_populate() creates at arch_initcall_sync. "Only a single
    ramoops area allowed at a time" then rejects whichever registered second.
    """
    tree = r.header.get("dtb_selected")
    if not tree:
        return True
    off, total = tree["off"], tree["totalsize"]
    nodes = {}
    for path, k, v in fdt.paths(d, off, total):
        nodes.setdefault(path, {})[k] = v

    def cells(props, name):
        return fdt.idcells(props.get(name))

    def region(props):
        """A `reg` as (address, size) for a 2/2 tree, or (None, None)."""
        c = cells(props, "reg")
        if not c or len(c) < 4:
            return None, None
        return (c[0] << 32) | c[1], (c[2] << 32) | c[3]

    ok = True
    for path, props in sorted(nodes.items()):
        compat = props.get("compatible") or b""
        what = None
        if b"ramoops" in compat:
            what = "the log region"
        elif b"simple-framebuffer" in compat:
            what = "the console's framebuffer"
        if what is None:
            continue
        addr, size = region(props)
        if addr is None:
            continue
        where = "in" if gauguin.in_dram(addr, size) else "OUTSIDE"
        r.note(f"{what}: {path.lstrip('/')} at {addr:#x}+{size:#x} - {where} this "
               f"phone's RAM partitions")
        if not gauguin.in_dram(addr, size):
            ok = False
            r.fail("reserved-memory",
                   f"{path.lstrip('/')} is at {addr:#x}, and this phone has no RAM "
                   f"there: ABL writes its RAM partition table into /memory "
                   f"('{', '.join(f'{b:#x}+{s:#x}' for b, s in gauguin.DRAM)}'), and "
                   f"the address falls outside all of it. A no-map reservation there "
                   f"reserves nothing, and the first access is a fault - from a "
                   f"postcore_initcall, so before any console")
        # Being in DRAM is not enough, and this is the half that is easy to miss:
        # the partition table says bank 1 is RAM from 0xc0000000, while the phone's
        # own tree removes the first 0x7b00000 of it for a subsystem.
        # `removed-dma-pool` means the bootloader handed that DRAM to something that
        # is not Linux, so a region placed inside it is at once in RAM and in
        # somebody else's memory.
        clash = gauguin.reserved_overlaps(addr, size)
        if clash:
            ok = False
            r.fail("reserved-memory",
                   f"{path.lstrip('/')} at {addr:#x}+{size:#x} is inside "
                   f"{', '.join(clash)} - memory this phone's own tree marks no-map, "
                   f"i.e. DRAM the bootloader has given to something that is not "
                   f"Linux. It is inside a RAM partition and still not ours")
        elif what != "the log region":
            # The console's framebuffer is the one region placed inside memory the
            # phone reserves, so say which and why rather than leaving it looking
            # like an oversight: it is the bootloader's live scanout buffer.
            base, size_ = gauguin.SPLASH_BUFFER
            if addr == base and size <= size_:
                r.note(f"  and it is the bootloader's live scanout buffer "
                       f"(cont_splash_region, {base:#x}+{size_:#x}), adopted rather "
                       f"than avoided")
            else:
                r.note(f"  note: not inside cont_splash_region "
                       f"({base:#x}+{size_:#x}), so the panel has to have been "
                       f"re-initialised for this to be drawn on")

    cmdline = d[64:64 + 512].split(b"\x00")[0].decode("ascii", "replace")
    words = dict(w.split("=", 1) for w in cmdline.split() if "=" in w)
    if "ramoops.mem_address" in words:
        try:
            addr = int(words["ramoops.mem_address"], 0)
            size = int(words.get("ramoops.mem_size", "0"), 0)
        except ValueError:
            return r.fail("reserved-memory",
                          f"unparsable ramoops.mem_address="
                          f"{words['ramoops.mem_address']!r}")
        where = "in" if gauguin.in_dram(addr, size) else "OUTSIDE"
        r.note(f"the command line puts ramoops at {addr:#x}+{size:#x} - {where} this "
               f"phone's RAM partitions, and the command line is the copy that wins")
        if not gauguin.in_dram(addr, size):
            ok = False
            r.fail("reserved-memory",
                   f"ramoops.mem_address={addr:#x} is not in this phone's RAM "
                   f"partitions - ramoops ioremap()s it and writes to it from "
                   f"postcore_initcall")
        clash = gauguin.reserved_overlaps(addr, size)
        if clash:
            ok = False
            r.fail("reserved-memory",
                   f"ramoops.mem_address={addr:#x} is inside {', '.join(clash)} - "
                   f"DRAM this phone's own tree marks no-map, so the log would be "
                   f"written into memory the bootloader has given away")
        # The two have to agree, or which region the log lands in depends on
        # probe order rather than on anything a reader of the payload can see.
        for path, props in sorted(nodes.items()):
            if b"ramoops" in (props.get("compatible") or b""):
                a2, s2 = region(props)
                if a2 is not None and (a2, s2) != (addr, size):
                    r.note(f"the tree's ramoops node says {a2:#x}+{s2:#x} while the "
                           f"command line says {addr:#x}+{size:#x}; the command line "
                           f"wins, so the node is misleading")
    return ok


def check(path, kernel_base, kernel_size, dtbo_entries=None):
    d = open(path, "rb").read()
    r = Report()
    if not check_image_header(d, r):
        return r
    if not geometry(r, kernel_base, kernel_size):
        return r
    if not check_dtb_offset(d, r):
        return r
    check_kernel_mode(d, r)
    # check_regions() before check_overlay(), only because Report keeps the last
    # failure: a payload that trips both would be refused by ABL at the overlay
    # stage first, before any address of ours was ever touched.
    check_regions(d, r)
    check_overlay(d, r, dtbo_entries)
    return r


def check_kernel_mode(d, r, decompressed_kernel=None):
    """UpdateKernelModeAndPkg() plus GZipPkgCheck()'s two runtime checks."""
    h = r.header
    page = h["page"]
    raw = d[page:page + h["kernel_size"]]
    h["gzip"] = len(raw) >= 10 and raw[0] == 0x1F and raw[1] == 0x8B and raw[2] == 0x08
    if h["gzip"]:
        h["patched"] = False
        try:
            out = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
        except zlib.error as e:
            return r.fail("GZipPkgCheck",
                          f"Decompressing kernel image failed: {e}. ABL's decompress "
                          f"gets {h['out_avail']:,} bytes of room")
        h["out_len"] = len(out)
        h["decompressed_image_size"] = struct.unpack_from("<Q", out, 16)[0]
        h["decompressed_magic"] = struct.unpack_from("<I", out, 56)[0]
        if h["out_len"] <= 8:
            return r.fail("GZipPkgCheck",
                          "Decompress kernel size is smaller than image header size")
        kptr_magic = h["decompressed_magic"]
        kptr_image_size = h["decompressed_image_size"]
    else:
        h["patched"] = raw[:len(PATCHED_KERNEL_MAGIC)] == PATCHED_KERNEL_MAGIC
        base = PATCHED_KERNEL_HEADER_SIZE if h["patched"] else 0
        if len(raw) < base + KERNEL64_HDR_SIZE:
            return r.fail("UpdateKernelModeAndPkg",
                          f"kernel is {len(raw)} bytes, shorter than one arm64 header")
        h["text_offset"] = struct.unpack_from("<Q", raw, base + 8)[0]
        h["image_size"] = struct.unpack_from("<Q", raw, base + 16)[0]
        h["flags"] = struct.unpack_from("<Q", raw, base + 24)[0]
        kptr_magic = struct.unpack_from("<I", raw, base + 56)[0]
        kptr_image_size = h["image_size"]
        h["res5"] = struct.unpack_from("<I", raw, base + 60)[0]
        if kptr_magic != KERNEL64_HDR_MAGIC:
            # Not fatal here: ABL decides it is a 32-bit kernel and boots that way.
            r.note("no ARM\\x64 magic at kernel+56 - ABL sets BootingWith32BitKernel "
                   "and would enter AArch32")
    h["kptr_magic_ok"] = kptr_magic == KERNEL64_HDR_MAGIC
    h["kptr_image_size"] = kptr_image_size
    if kptr_magic == KERNEL64_HDR_MAGIC:
        if kptr_image_size > h["headroom"]:
            return r.fail("GZipPkgCheck",
                          f"DTB header can get corrupted due to runtime kernel size: "
                          f"image_size {kptr_image_size:#x} > headroom {h['headroom']:#x}")
    return True


def geometry(r, kernel_base, kernel_size):
    """UpdateBootParams(), for the 64-bit path."""
    h = r.header
    page = h["page"]
    kernel_load = kernel_base + KERNEL_64BIT_LOAD_OFFSET
    kernel_end = kernel_base + kernel_size
    ramdisk_load = kernel_end - (round_page(h["ramdisk_size"], page) + page)
    dt_load = ramdisk_load - (DT_SIZE_2MB + page)
    h["kernel_load_addr"] = kernel_load
    h["kernel_end_addr"] = kernel_end
    h["ramdisk_load_addr"] = ramdisk_load
    h["device_tree_load_addr"] = dt_load
    h["headroom"] = dt_load - kernel_load
    if dt_load <= kernel_load:
        return r.fail("UpdateBootParams", "Not Enough space left to load kernel image")
    h["out_avail"] = h["headroom"]
    return True


def describe(path, r, verbose):
    name = os.path.basename(path)
    h = getattr(r, "header", None)
    if r.failed:
        print(f"\033[31mFAIL\033[0m {name}")
        print(f"     {r.failed[0]}: {r.failed[1]}")
        if r.failed[0] == "UpdateBootParams" and h:
            print("     (no header problem - this is a memory-geometry failure)")
        if r.failed[0] == "ApplyOverlay" and h and h.get("overlay_missing"):
            print(f"     missing symbols (first 8): "
                  f"{', '.join(r.header['overlay_missing'][:8])}")
            print("     -> ufdt_overlay_do_fixups() returns -1, "
                  "ufdt_apply_multi_overlay() gives NULL,\n        ApplyOverlay() "
                  "gives EFI_NOT_FOUND, and BootLinux never reaches the kernel.")
        for n in r.notes:
            print(f"     note: {n}")
        return False
    print(f"\033[32m ok \033[0m {name}")
    print(f"     header v{h['hv']}  page {h['page']:#x}  "
          f"kernel {h['kernel_size']:,} ({h['kernel_pages']}p)  "
          f"ramdisk {h['ramdisk_size']:,} ({h['ramdisk_pages']}p)  "
          f"dtb {h['dtb_size']:,}")
    print(f"     ABL computes the DTB at {h['abl_dtb_offset']:#x}"
          + (f", fdt totalsize {h['dtb_totalsize']:#x}" if h.get("dtb_present") else ""))
    if h["gzip"]:
        print(f"     kernel is gzip -> decompressed {h['out_len']:,} bytes, "
              f"image_size {h['decompressed_image_size']:#x}")
    else:
        print(f"     kernel is raw  -> image_size {h['image_size']:#x}, "
              f"text_offset {h['text_offset']:#x} (dead: ABL never reads it), "
              f"res5 {h['res5']:#x}")
    print(f"     headroom {h['headroom']:,} bytes ({h['headroom'] / 1048576:.0f} MB) "
          f"between kernel load and DT load; the largest image_size here is "
          f"{max(h.get('image_size', 0), h.get('decompressed_image_size', 0)):,}")
    if verbose and h.get("overlay_entry"):
        e = h["overlay_entry"]
        print(f"     board overlay: dtbo entry {e['index']} "
              f"({e['model'] or 'unnamed'}), {len(e.get('frags') or ())} fragments, "
              f"{len(e.get('fixups') or {})} fixup symbols")
    if verbose and h.get("dtb_root") is not None:
        have = [k for k, v in h["dtb_root"].items() if v is not None]
        miss = [k for k, v in h["dtb_root"].items() if v is None]
        print(f"     base tree: {h['dtb_selected']['model'] or 'unnamed'}  "
              f"identifiers: {', '.join(have) or 'none'}"
              + (f"; absent: {', '.join(miss)}" if miss else "")
              + ("; __symbols__ present" if h["dtb_has_symbols"]
                 else "; \033[31mno __symbols__\033[0m"))
    if verbose:
        print(f"     kernel_load {h['kernel_load_addr']:#x}  "
              f"ramdisk_load {h['ramdisk_load_addr']:#x}  "
              f"device_tree_load {h['device_tree_load_addr']:#x}")
    for n in r.notes:
        print(f"     note: {n}")
    return True


def find_dtbo():
    """The dtbo dump, if one was taken - this is the file that decides which of
    ABL's two DTB paths runs, so it is reported before the images are."""
    for c in (os.environ.get("DTBO"), os.path.expanduser(
            "~/backup/gauguin/images/part-dtbo.img")):
        if c and os.path.exists(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", help="boot images (globs are fine)")
    ap.add_argument("--kernel-base", default=hex(GAUGUIN_KERNEL_BASE))
    ap.add_argument("--kernel-size", default=hex(GAUGUIN_KERNEL_SIZE))
    ap.add_argument("--dtbo", help="dtbo partition dump (default: the local one)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    paths = []
    for pat in a.images:
        hit = sorted(glob.glob(pat))
        if not hit:
            print(f"{pat}: no such file", file=sys.stderr)
            sys.exit(2)
        paths.extend(hit)

    base, size = int(a.kernel_base, 0), int(a.kernel_size, 0)
    print(f"kernel region {base:#x} + {size:#x}  (--kernel-base/--kernel-size to override)")
    print(f"kernel_load = {base + KERNEL_64BIT_LOAD_OFFSET:#x}\n")
    dtbo = a.dtbo or find_dtbo()
    entries = None
    if dtbo:
        entries = check_dtbo(dtbo)
    else:
        print("no dtbo dump found - pass --dtbo to say which of ABL's two DTB "
              "paths runs.\n")
    bad = 0
    for p in paths:
        if not describe(p, check(p, base, size, entries), a.verbose):
            bad += 1
    print()
    if bad:
        print(f"{len(paths) - bad} pass, {bad} fail - a failing image is not worth a "
              f"device cycle, because ABL rejects it before any code of ours runs.")
    else:
        print("Every image passes the checks ABL makes before it hands control over. "
              "A refusal is therefore not one of them - and the header properties these "
              "variants differ on (text_offset, dtb_addr, and the declared image_size) "
              "are ones ABL does not read. Raw vs gzip is the exception: it selects a "
              "different ABL code path.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
