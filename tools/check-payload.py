#!/usr/bin/env python3
"""Check a boot image before it costs a device cycle.

Why this exists: the P1 payloads differ only in things that `ls -la` cannot
show — whether the kernel is compressed, which form the arm64 image header is
in, what `text_offset` says. Mixing two of them up mid-session produces a
result that reads as "this variable does not matter" when in fact the wrong
file was flashed, and each such mistake costs a physical reset of the phone.

So this prints the properties that are being varied, next to the same
properties read out of the device's own `boot` image, and fails loudly on
anything structurally wrong (bad magic, a DTB that is not where the shape says
it is, declared regions that do not add up to the file size, an AVB footer).

There are two shapes here and the difference between them is not a defect, so
which one applies is read off the image rather than asserted: `stock` is the
phone's own `boot` partition (v2 header, 4096-byte pages, the tree in a region
the header declares) and `silicon` is what Mu-Silicium's builder emits for this
device (v1, 2048-byte pages, no room for a tree in the header at all, so it is
glued onto the kernel blob). Judging the second against the first produces a
list of the ways it is deliberately different and calls it a fault - which is
what this did until the corrected ABL replay in docs/07 showed that shape is
admissible when it carries the current tree. What decides whether a payload is
actually takeable is `tools/abl-boot-check.py`, which replays ABL's own checks
and does not care which shape an image is.

It also checks the log region a payload declares, but only against the payload
itself: one ramoops node, and a device tree and a command line that agree on the
region. It used to compare our address and record geometry against the stock
image's DTB slot, on the premise that Android reads the region back and parses it
by position — a premise that is false on this device, since the vendor kernel has
no /sys/fs/pstore and its panic log goes through mtdoops to a raw partition. The
node it was compared against is in a tree nothing selects. Placement is checked
against the *device*, by tools/abl-boot-check.py. See docs/07.

Usage:
    tools/check-payload.py work/out/boot-pstore*.img
"""
import argparse
import gzip
import io
import os
import struct
import sys

MAGIC = b"ANDROID!"
ARM64_MAGIC = b"ARMd"
AVB_MAGIC = b"AVB0"
DTB_MAGIC = b"\xd0\x0d\xfe\xed"

# The two shapes a payload here is built in. This is not a list of what is
# allowed: it is what each shape *is*, so that a difference between an image and
# one of them can be reported as a difference rather than as a fault. A shape is
# not correct because it matches one of these - it is correct if ABL takes it,
# which is `tools/abl-boot-check.py`'s question and not this file's.
PROFILES = {
    # The phone's own `boot` partition, which is what a stock-shaped payload is
    # reproducing: v2 header, 4096-byte pages, the tree in a region the header
    # declares at its own offset and size.
    "stock": dict(name="stock", header_version=2, page_size=0x1000,
                  dtb_in_header=True),
    # What Mu-Silicium's builder emits for this device: v1 header, 2048-byte
    # pages, no room in the header for a tree at all, so the tree is glued onto
    # the kernel blob and ABL's decompressor is what finds it (docs/07).
    "silicon": dict(name="silicon", header_version=1, page_size=0x800,
                    dtb_in_header=False),
}


def detect_profile(f):
    """Which of the two shapes this image is, read off the image itself.

    A v2 header is the only one that has somewhere to write a DTB size, so an
    image that declares one is judged as `stock`; everything else keeps its tree
    on the kernel blob the way `silicon` does. Deriving this rather than taking
    it as an argument means an image cannot be judged leniently by naming the
    wrong profile - the lenient one is the one whose tree location the header
    cannot express in the first place, so it is the shape that has to be
    detected, not the shape that gets asserted.
    """
    return "stock" if f["header_version"] >= 2 else "silicon"


def read_image(path):
    """Parse the Android boot image header. Returns a dict, or None."""
    d = open(path, "rb").read()
    if d[:8] != MAGIC:
        return None
    ps, = struct.unpack_from("<I", d, 36)
    hv, = struct.unpack_from("<I", d, 40)
    f = dict(
        path=path, size=len(d), page=ps, header_version=hv,
        kernel_size=struct.unpack_from("<I", d, 8)[0],
        kernel_addr=struct.unpack_from("<I", d, 12)[0],
        ramdisk_size=struct.unpack_from("<I", d, 16)[0],
        ramdisk_addr=struct.unpack_from("<I", d, 20)[0],
        tags_addr=struct.unpack_from("<I", d, 32)[0],
        os_version=struct.unpack_from("<I", d, 44)[0],
        cmdline=d[64:64 + 512].split(b"\x00")[0].decode(errors="replace"),
    )
    if hv >= 2:
        f["dtb_size"] = struct.unpack_from("<I", d, 1648)[0]
        f["dtb_addr"] = struct.unpack_from("<Q", d, 1652)[0]
    else:
        f["dtb_size"] = f["dtb_addr"] = 0

    # region offsets, as mkbootimg lays them out
    nk = (f["kernel_size"] + ps - 1) // ps
    nr = (f["ramdisk_size"] + ps - 1) // ps if f["ramdisk_size"] else 0
    nd = (f["dtb_size"] + ps - 1) // ps if f["dtb_size"] else 0
    f["kernel_off"] = ps
    f["ramdisk_off"] = ps * (1 + nk)
    f["dtb_off"] = ps * (1 + nk + nr)
    f["regions_end"] = ps * (1 + nk + nr + nd)

    f["kernel"] = d[f["kernel_off"]:f["kernel_off"] + f["kernel_size"]]
    f["ramdisk"] = d[f["ramdisk_off"]:f["ramdisk_off"] + f["ramdisk_size"]]
    f["dtb"] = d[f["dtb_off"]:f["dtb_off"] + f["dtb_size"]]
    return f


def arm64_header(blob):
    """The 64-byte arm64 image header, from a raw Image or a gzip of one."""
    if blob[:2] == b"\x1f\x8b":
        try:
            blob = gzip.GzipFile(fileobj=io.BytesIO(blob)).read(0x40)
        except Exception:
            return None
        compressed = True
    else:
        blob = blob[:0x40]
        compressed = False
    if len(blob) < 0x40 or blob[0x38:0x3c] != ARM64_MAGIC:
        return dict(compressed=compressed, magic=False)
    code0, code1 = struct.unpack_from("<II", blob, 0)
    text, size, flags = struct.unpack_from("<QQQ", blob, 8)
    res5, = struct.unpack_from("<I", blob, 0x3c)
    return dict(compressed=compressed, magic=True, code0=code0, code1=code1,
                text_offset=text, image_size=size, flags=flags, res5=res5)


# --- device tree, minimally ------------------------------------------------
#
# Enough of a parser to read the ramoops node's address and record geometry out
# of the DTB a payload carries. There is no external reference to compare it
# against, and there should not be: the region is ours to place, and what makes
# an address wrong is a property of the phone's memory map rather than of some
# other tree's opinion. tools/abl-boot-check.py checks it against the device.

def fdt_nodes(blob):
    """(path, properties) for every node in an FDT. Properties are bytes."""
    magic, total, off_struct, off_strings = struct.unpack_from(">IIII", blob, 0)
    if magic != 0xd00dfeed:
        raise ValueError(f"not an FDT (magic {magic:#x})")
    strings_off = off_strings
    pos, path, out = off_struct, [], []
    while pos < total:
        tok, = struct.unpack_from(">I", blob, pos)
        pos += 4
        if tok == 1:                                  # FDT_BEGIN_NODE
            end = blob.index(b"\x00", pos)
            name = blob[pos:end].decode(errors="replace")
            pos = (end + 4) & ~3
            path.append(name)
        elif tok == 2:                                # FDT_END_NODE
            path.pop()
        elif tok == 3:                                # FDT_PROP
            ln, nameoff = struct.unpack_from(">II", blob, pos)
            pos += 8
            end = blob.index(b"\x00", strings_off + nameoff)
            key = blob[strings_off + nameoff:end].decode(errors="replace")
            out.append(("/" + "/".join(p for p in path if p), key,
                        blob[pos:pos + ln]))
            pos = (pos + ln + 3) & ~3
        elif tok == 4:                                # FDT_NOP
            continue
        elif tok == 9:                                # FDT_END
            break
        else:
            raise ValueError(f"bad FDT token {tok} at {pos - 4:#x}")
    by_path = {}
    for p, k, v in out:
        by_path.setdefault(p, {})[k] = v
    return by_path


def ramoops_spec(dtb):
    """The ramoops node's address and record geometry, or None if there is none.

    `reg` is read as two address cells and two size cells, which is what every
    Qualcomm reserved-memory node uses; the record sizes are single cells.
    """
    nodes = fdt_nodes(dtb)
    found = [(p, v) for p, v in nodes.items()
             if v.get("compatible", b"").rstrip(b"\x00") == b"ramoops"]
    if not found:
        return None
    path, props = found[0]
    cells = struct.unpack(f">{len(props['reg']) // 4}I", props["reg"])
    spec = dict(node=path,
                count=len(found),
                address=(cells[0] << 32) | cells[1],
                size=(cells[2] << 32) | cells[3])
    for key, prop in (("record_size", "record-size"),
                      ("console_size", "console-size"),
                      ("ftrace_size", "ftrace-size"),
                      ("pmsg_size", "pmsg-size"),
                      ("ecc_size", "ecc-size")):
        spec[key], = struct.unpack(">I", props.get(prop, b"\x00\x00\x00\x00"))
    return spec


def cmdline_ramoops(cmdline):
    """The ramoops parameters a cmdline sets, or None if it sets none.

    These shadow the device tree: `ramoops_init` is a `postcore_initcall` and
    registers its dummy device before `platform_driver_register`, while device
    tree nodes are not populated until `arch_initcall_sync` - one initcall level
    later - and `ramoops_probe` refuses any probe after the first. So when a
    cmdline and a tree disagree, this is what actually gets used, and it is what
    has to match the phone's own layout.
    """
    keys = dict(mem_address="address", mem_size="size", record_size="record_size",
                console_size="console_size", ftrace_size="ftrace_size",
                pmsg_size="pmsg_size")
    spec, seen = {}, False
    for word in cmdline.split():
        if not word.startswith("ramoops."):
            continue
        key, _, val = word.partition("=")
        key = key[len("ramoops."):]
        if not val:
            continue
        seen = True
        if key == "ecc":
            # `ramoops.ecc=1` means 16 bytes, the same special case ram.c makes.
            spec["ecc_size"] = 16 if int(val, 0) == 1 else int(val, 0)
        elif key in keys:
            spec[keys[key]] = int(val, 0)
    if not seen:
        return None
    # An unset record size is zero, not absent: the region is a fixed layout and
    # anything not named gets no room in it.
    for short in keys.values():
        spec.setdefault(short, 0)
    spec.setdefault("ecc_size", 0)
    return spec


def format_spec(s):
    if s is None:
        return "none"
    if isinstance(s, str):
        return s
    return (f"{s.get('address', 0):#x}+{s.get('size', 0):#x} "
            f"record {s.get('record_size', 0):#x} "
            f"console {s.get('console_size', 0):#x} "
            f"ftrace {s.get('ftrace_size', 0):#x} "
            f"pmsg {s.get('pmsg_size', 0):#x} ecc {s.get('ecc_size', 0):#x}")


def appended_dtb(f):
    """(file offset, blob) of the tree glued onto the kernel blob, or (None, None).

    ABL finds that tree from the offset its own decompressor reports - the end of
    the gzip member plus its trailer - and the header has no field for it, so the
    header cannot be asked where it is. What can be asked is whether one is there
    at all and whether it is complete: `d00dfeed` appears only at the start of an
    fdt, so the last one in the blob is the candidate, and its `totalsize` has to
    land exactly on the end of the kernel region.
    """
    blob = f["kernel"]
    pos = blob.rfind(DTB_MAGIC)
    if pos < 0:
        return None, None
    total, = struct.unpack_from(">I", blob, pos + 4)
    if pos + total != len(blob):
        return None, None
    return f["kernel_off"] + pos, blob[pos:pos + total]


def describe(f, prof=PROFILES["stock"]):
    hdr = arm64_header(f["kernel"])
    comp = "gzip" if f["kernel"][:2] == b"\x1f\x8b" else "raw"
    lines = [
        f"== {f['path']}",
        f"   size {f['size']:,}  header v{f['header_version']}  page {f['page']:#x}",
        f"   kernel  {f['kernel_size']:>12,} @ {f['kernel_addr']:#x}  {comp}"
        f"  first4 {f['kernel'][:4]!r}",
        f"   ramdisk {f['ramdisk_size']:>12,} @ {f['ramdisk_addr']:#x}",
    ]
    if prof["dtb_in_header"]:
        lines.append(f"   dtb     {f['dtb_size']:>12,} @ {f['dtb_addr']:#x}  file "
                     f"{f['dtb_off']:#x}  first4 {f['dtb'][:4]!r}")
    else:
        off, blob = appended_dtb(f)
        lines.append(f"   dtb     {len(blob) if blob else 0:>12,}  appended to the "
                     f"kernel blob"
                     + (f"  file {off:#x}  first4 {blob[:4]!r}" if blob else
                        "  ** none found **"))
    if hdr and hdr.get("magic"):
        form = "MZ/PE (EFI stub)" if (hdr["code0"] & 0xffff) == 0x5a4d else \
               ("branch" if (hdr["code0"] >> 26) == 0b000101 else
                f"{hdr['code0']:#010x}")
        lines.append(
            f"   arm64 header: code0 {form}  text_offset {hdr['text_offset']:#x}"
            f"  image_size {hdr['image_size']:#x}  res5 {hdr['res5']:#x}")
    elif hdr:
        lines.append("   arm64 header: ** ARMd magic not at 0x38 **")
    else:
        lines.append("   arm64 header: ** kernel does not decompress **")

    tree_blob = f["dtb"] if prof["dtb_in_header"] else appended_dtb(f)[1]
    if tree_blob and tree_blob[:4] == DTB_MAGIC:
        try:
            tree = ramoops_spec(tree_blob)
        except ValueError as e:
            tree = f"** unparseable: {e} **"
        cmd = cmdline_ramoops(f["cmdline"])
        lines.append(f"   dtb ramoops:    {format_spec(tree)}")
        lines.append(f"   cmdline pstore: {format_spec(cmd)}"
                     + ("   (the cmdline wins - see docs/07)" if cmd else ""))
    return "\n".join(lines)


def problems(f, prof=PROFILES["stock"]):
    """Structural faults that would waste the device cycle outright.

    The reference here is a boot image shape, not a check of its own: `stock` is
    the phone's own `boot` partition, because that is what this wrapper exists to
    reproduce, and `silicon` is the shape Mu-Silicium's builder produces - v1,
    page 2048, the tree glued onto the kernel blob. Comparing the second shape
    against the first is not a fault report; it is a list of the ways it is
    deliberately different. `tools/abl-boot-check.py` is what judges a payload
    regardless of shape, by replaying the checks ABL makes.
    """
    out = []
    if f["header_version"] != prof["header_version"]:
        out.append(f"header_version is {f['header_version']}, "
                   f"{prof['name']} is {prof['header_version']}")
    if f["page"] != prof["page_size"]:
        out.append(f"page_size is {f['page']:#x}, {prof['name']} is "
                   f"{prof['page_size']:#x}")
    if not f["kernel_size"]:
        out.append("no kernel")
    if prof["dtb_in_header"]:
        if f["dtb_size"] and f["dtb"][:4] != DTB_MAGIC:
            out.append(f"DTB region at {f['dtb_off']:#x} does not start with the "
                       f"DTB magic (got {f['dtb'][:4]!r})")
        if not f["dtb_size"]:
            out.append("no DTB declared in the header")
    else:
        # Where the tree is is decided by the compression, not by a header field:
        # ABL's decompressor reports the offset of whatever follows the gzip
        # member, and a raw kernel blob has nothing to report at all (docs/07).
        # So the check is that a complete tree is glued on.
        if not f["kernel"][:2] == b"\x1f\x8b" and f["kernel"][:16] != \
                b"UNCOMPRESSED_IMG":
            out.append("the kernel is neither a gzip package nor a patched "
                       "kernel, so nothing supplies ABL with a DTB offset and "
                       "this shape cannot locate a tree (docs/07)")
        if appended_dtb(f)[1] is None:
            out.append("no complete DTB appended to the kernel blob (no "
                       "d00dfeed, or its totalsize does not end the blob)")
    if f["regions_end"] > f["size"]:
        out.append(f"declared regions end at {f['regions_end']:#x}, past the "
                   f"file ({f['size']:#x})")
    if f["regions_end"] < f["size"] and f["size"] - f["regions_end"] > f["page"]:
        out.append(f"{f['size'] - f['regions_end']:,} bytes after the last "
                   "declared region")
    if f["size"] >= 64 and f["size"] - 64 >= 0:
        tail = open(f["path"], "rb").read()[-4096:]
        if AVB_MAGIC in tail:
            out.append("AVB footer present")
    hdr = arm64_header(f["kernel"])
    if hdr is None or not hdr.get("magic"):
        out.append("the kernel carries no arm64 image header (no ARMd at 0x38) "
                   "- ABL's kernel-mode check reads exactly that")
    out += ramoops_problems(f, prof)
    return out


def ramoops_problems(f, prof=PROFILES["stock"]):
    """Whether the log region this image declares is one a boot could use.

    What is checked here is only what this image says about itself: there is one
    ramoops node (two would make which one wins a question of node order), and
    its device tree and its command line agree on the region. The command line is
    the copy that wins - `ramoops_init` is a `postcore_initcall` and registers the
    cmdline's dummy platform device before `platform_driver_register`, while the
    DT nodes are not populated until `arch_initcall_sync`, one initcall level
    later, and `ramoops_probe` refuses any probe after the first - so a
    disagreement is a tree that lies about where the log is.

    Where the region has to *be* is a different question and is not asked here.
    This function used to compare our address and record geometry against the
    ramoops node in the vendor base tree out of the stock image, on the premise
    that Android reads the region back and parses it by position. That premise is
    false on this device: the vendor kernel has no /sys/fs/pstore, its panic log
    goes through mtdoops to a raw partition instead, and the node it was compared
    against is in a tree nothing ever selects. A comparison against a reader that
    does not exist can only produce false failures. Placement is checked against
    the *device* by `tools/abl-boot-check.py` (DRAM partitions and the phone's own
    no-map carveouts, from tools/gauguin.py), which runs as part of
    tools/build-p1-payloads.sh.

    The tree read here is whichever one this shape carries - the header's region
    for `stock`, the blob glued onto the kernel for `silicon` - since both are
    the tree ABL ends up handing the kernel.
    """
    tree_blob = f["dtb"] if prof["dtb_in_header"] else appended_dtb(f)[1]
    if not tree_blob or tree_blob[:4] != DTB_MAGIC:
        return []
    try:
        tree = ramoops_spec(tree_blob)
    except ValueError as e:
        return [f"the device tree in this image does not parse ({e})"]

    out = []
    if tree is None:
        out.append("this image's device tree has no ramoops node, so the pstore "
                   "region is ordinary System RAM and the log cannot be trusted")
        return out
    if tree["count"] != 1:
        out.append(f"{tree['count']} ramoops nodes in the device tree; only one "
                   "area is allowed and which one wins is not obvious")

    # The cmdline shadows the tree, so the two have to say the same thing.
    cmd = cmdline_ramoops(f["cmdline"])
    effective = cmd if cmd is not None else tree
    for key in ("address", "record_size", "console_size", "ftrace_size"):
        if tree.get(key) != effective.get(key):
            out.append(f"this image's device tree and its cmdline disagree on "
                       f"ramoops {key} ({tree.get(key, 0):#x} vs "
                       f"{effective.get(key, 0):#x})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--profile", choices=["auto", *PROFILES], default="auto",
                    help="which shape to judge each image against; `auto` reads "
                         "the shape off the image, and is what the build scripts "
                         "use")
    args = ap.parse_args()

    bad = 0
    for p in args.images:
        f = read_image(p)
        if f is None:
            print(f"== {p}\n   ** not an Android boot image **\n")
            bad += 1
            continue
        name = detect_profile(f) if args.profile == "auto" else args.profile
        prof = PROFILES[name]
        print(describe(f, prof) + f"\n   shape:  {name}")
        probs = problems(f, prof)
        if probs:
            for x in probs:
                print(f"   !! {x}")
            bad += 1
        else:
            print("   ok")
        print()
    if bad:
        print(f"{bad} image(s) have problems - do not flash them")
        return 1
    print("all images structurally check out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
