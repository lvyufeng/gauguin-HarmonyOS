#!/usr/bin/env python3
"""Check a boot image before it costs a device cycle.

Why this exists: the P1 payloads differ only in things that `ls -la` cannot
show — whether the kernel is compressed, which form the arm64 image header is
in, what `text_offset` says. Mixing two of them up mid-session produces a
result that reads as "this variable does not matter" when in fact the wrong
file was flashed, and each such mistake costs a physical reset of the phone.

So this prints the properties that are being varied, next to the same
properties read out of the device's own `boot` image, and fails loudly on
anything structurally wrong (bad magic, a DTB that is not at the offset the
header declares, declared regions that do not add up to the file size, an AVB
footer).

It also checks the one thing about a payload that is judged *after* the fact:
where its log will land. The pstore region is parsed by Android by position, so
a payload can be structurally perfect and still leave a log nobody can read. The
reference for that check is not a constant in this file — it is read out of the
device's own `boot` image, whose DTB slot carries the vendor base tree, which is
the tree whose layout the vendor kernel will apply when it reads the region
back. See docs/07.

Usage:
    tools/check-payload.py work/out/boot-pstore*.img
    tools/check-payload.py --stock ~/backup/gauguin/images/part-boot.img img...
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
# The pstore region is the one thing about a payload that is judged *after* the
# fact, by Android, out of a region it parses by position. A payload can be
# structurally perfect and still leave a log nobody can read, so the shape of
# that region is checked here rather than trusted.
#
# The reference is not a constant in this file: it is read out of the phone's
# own boot image, which carries the vendor's base tree in its DTB slot. That is
# the tree whose layout the vendor kernel will apply when it reads the region
# back, so it is the only honest thing to compare against.

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


def describe(f, ref=None):
    hdr = arm64_header(f["kernel"])
    comp = "gzip" if f["kernel"][:2] == b"\x1f\x8b" else "raw"
    lines = [
        f"== {f['path']}",
        f"   size {f['size']:,}  header v{f['header_version']}  page {f['page']:#x}",
        f"   kernel  {f['kernel_size']:>12,} @ {f['kernel_addr']:#x}  {comp}"
        f"  first4 {f['kernel'][:4]!r}",
        f"   ramdisk {f['ramdisk_size']:>12,} @ {f['ramdisk_addr']:#x}",
        f"   dtb     {f['dtb_size']:>12,} @ {f['dtb_addr']:#x}  file "
        f"{f['dtb_off']:#x}  first4 {f['dtb'][:4]!r}",
    ]
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

    if f["dtb_size"] and f["dtb"][:4] == DTB_MAGIC:
        try:
            tree = ramoops_spec(f["dtb"])
        except ValueError as e:
            tree = f"** unparseable: {e} **"
        cmd = cmdline_ramoops(f["cmdline"])
        lines.append(f"   dtb ramoops:    {format_spec(tree)}")
        lines.append(f"   cmdline pstore: {format_spec(cmd)}"
                     + ("   (the cmdline wins - see docs/07)" if cmd else ""))
        if ref is not None:
            lines.append(f"   the phone's own:{format_spec(ref)}")
    return "\n".join(lines)


def problems(f, ref=None):
    """Structural faults that would waste the device cycle outright."""
    out = []
    if f["header_version"] != 2:
        out.append(f"header_version is {f['header_version']}, stock is 2")
    if f["page"] != 0x1000:
        out.append(f"page_size is {f['page']:#x}, stock is 0x1000")
    if not f["kernel_size"]:
        out.append("no kernel")
    if f["dtb_size"] and f["dtb"][:4] != DTB_MAGIC:
        out.append(f"DTB region at {f['dtb_off']:#x} does not start with the "
                   f"DTB magic (got {f['dtb'][:4]!r})")
    if not f["dtb_size"]:
        out.append("no DTB declared in the header")
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
    out += ramoops_problems(f, ref)
    return out


def ramoops_problems(f, ref):
    """The log region has to be somewhere Android will look, laid out its way.

    A payload that boots and dies with its log written to the wrong address, or
    with a different record layout at the right address, is indistinguishable
    from one that never ran - which is the whole reason the log channel exists.
    """
    if not f["dtb_size"] or f["dtb"][:4] != DTB_MAGIC:
        return []
    try:
        tree = ramoops_spec(f["dtb"])
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
    if ref is None:
        return out

    # The cmdline shadows the tree, so it is the one that has to be right.
    cmd = cmdline_ramoops(f["cmdline"])
    effective = cmd if cmd is not None else tree
    where = "cmdline" if cmd is not None else "device tree"
    for key, label in (("address", "address"), ("size", "size"),
                       ("record_size", "record-size"),
                       ("console_size", "console-size"),
                       ("ftrace_size", "ftrace-size"),
                       ("pmsg_size", "pmsg-size"),
                       ("ecc_size", "ecc-size")):
        if effective.get(key) != ref.get(key):
            out.append(
                f"the {where}'s ramoops {label} is "
                f"{effective.get(key, 0):#x}, the phone's own is "
                f"{ref.get(key, 0):#x} - Android parses this region by position, "
                f"so the log would be unreadable (docs/07)")
    # And the tree must agree with it, because "which of the two wins" should
    # never be the reason a log is missing.
    for key in ("address", "record_size", "console_size", "ftrace_size"):
        if tree.get(key) != effective.get(key):
            out.append(f"this image's device tree and its cmdline disagree on "
                       f"ramoops {key} ({tree.get(key, 0):#x} vs "
                       f"{effective.get(key, 0):#x})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--stock", default=None,
                    help="the device's own boot image, to print alongside and to "
                         "take the pstore region's address and record geometry "
                         "from (its DTB slot carries the vendor base tree)")
    args = ap.parse_args()

    bad, ref = 0, None
    if args.stock:
        s = read_image(args.stock)
        if s is None:
            sys.exit(f"{args.stock}: not an Android boot image")
        print(describe(s))
        print("   (the reference: this is the image the phone boots today)")
        if s["dtb_size"] and s["dtb"][:4] == DTB_MAGIC:
            try:
                ref = ramoops_spec(s["dtb"])
            except ValueError as e:
                print(f"   ** its device tree does not parse: {e} **")
        if ref is None:
            print("   ** and it declares no ramoops region, so there is nothing "
                  "to check the payloads' log layout against **")
        print()
        # The reference image is old and is not what we flash; do not report its
        # own header as a fault.
    for p in args.images:
        f = read_image(p)
        if f is None:
            print(f"== {p}\n   ** not an Android boot image **\n")
            bad += 1
            continue
        print(describe(f, ref))
        probs = problems(f, ref)
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
