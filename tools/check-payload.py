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


def describe(f):
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
    return "\n".join(lines)


def problems(f):
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
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--stock", default=None,
                    help="the device's own boot image, to print alongside")
    args = ap.parse_args()

    bad = 0
    if args.stock:
        f = read_image(args.stock)
        if f is None:
            sys.exit(f"{args.stock}: not an Android boot image")
        print(describe(f))
        print(f"   (the reference: this is the image the phone boots today)")
        print()
    for p in args.images:
        f = read_image(p)
        if f is None:
            print(f"== {p}\n   ** not an Android boot image **\n")
            bad += 1
            continue
        print(describe(f))
        probs = problems(f)
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
