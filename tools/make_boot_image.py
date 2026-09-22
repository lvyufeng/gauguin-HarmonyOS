#!/usr/bin/env python3
"""Wrap a Mu-Silicium firmware image into an Android boot image ourselves.

Why this exists rather than just using `build_uefi.py`:

Mu-Silicium's builder never passes `--pagesize` to mkbootimg, so every image it
produces has page_size 2048 and header_version taken from the platform's own
toml — which for every reference platform is 1. This device's own `boot`
partition is **header_version 2, page_size 0x1000**, and its ABL carries
explicit validation strings for the header it expects:

    Invalid boot image header / Invalid boot image header: %d
    Image Header version     : 0x%x
    Integer Overflow: PageSize=%u, KernelSizeActual=%u
    DTB offset is incorrect, kernel image does not have appended DTB

So there are two profiles here:

  silicon   reproduces exactly what Mu-Silicium's builder produces
            (v1, page 0x800, 0x10008000-ish addresses, DTB glued into
            kernel_size). Byte-comparable against `Mu-<device>.img` as a
            regression check on this script.

  stock     matches the phone's own boot image field for field:
            v2, page 0x1000, kernel 0x8000, ramdisk 0x1000000, tags 0x100,
            dtb_addr 0x1F00000, and the DTB in its own declared region after
            the ramdisk rather than inside the kernel.

`stock` is a hypothesis under test, not a claim. See docs/07: a stock-shaped
image was built for P1 and ABL still rejected it with
"Failed to load/authenticate boot image", so header shape is demonstrably not
sufficient on its own.

Usage:
    make_boot_image.py --fd SILICIUM_UEFI.fd --bootshim BootShim.bin \\
        --dtb gauguin.dtb --profile stock -o Mu-gauguin-stock.img
"""
import argparse
import gzip
import hashlib
import os
import struct
import sys

MAGIC = b"ANDROID!"

# Boot image header field offsets. v0 is 1632 bytes; v1 adds
# recovery_dtbo_size(4) + recovery_dtbo_offset(8) + header_size(4) -> 1648;
# v2 adds dtb_size(4) + dtb_addr(8) -> 1660.
OFF_KERNEL_SIZE, OFF_KERNEL_ADDR = 8, 12
OFF_RAMDISK_SIZE, OFF_RAMDISK_ADDR = 16, 20
OFF_SECOND_SIZE, OFF_SECOND_ADDR = 24, 28
OFF_TAGS_ADDR, OFF_PAGE_SIZE, OFF_HEADER_VERSION = 32, 36, 40
OFF_OS_VERSION = 44
OFF_HEADER_SIZE = 1644
OFF_DTB_SIZE, OFF_DTB_ADDR = 1648, 1652

PROFILES = {
    # what the phone's own `boot` partition says (measured, see docs/07)
    "stock": dict(
        header_version=2, page_size=0x1000,
        kernel_addr=0x00008000, ramdisk_addr=0x01000000, tags_addr=0x00000100,
        dtb_addr=0x01F00000, dtb_in_header=True, os_version=0,
    ),
    # what Mu-Silicium's builder produces for this platform
    "silicon": dict(
        header_version=1, page_size=0x800,
        kernel_addr=0x10008000, ramdisk_addr=0x11000000, tags_addr=0x10000100,
        dtb_addr=0, dtb_in_header=False, os_version=0,
    ),
}


def pad_to(data, page):
    rem = len(data) % page
    return data if rem == 0 else data + b"\x00" * (page - rem)


def build(kernel, ramdisk, dtb, p):
    """Return the boot image bytes for profile `p`."""
    page, hv = p["page_size"], p["header_version"]

    # In the stock profile the DTB is a declared region of its own, after the
    # ramdisk. In the silicon profile it is glued onto the end of the kernel
    # blob, which is what Mu-Silicium's append_dtb does - and which is why its
    # kernel_size covers the DTB.
    if p["dtb_in_header"]:
        if hv < 2:
            sys.exit("dtb_in_header requires header_version >= 2")
        dtb_size, dtb_addr = len(dtb), p["dtb_addr"]
    else:
        if dtb:
            kernel = kernel + dtb
        dtb_size, dtb_addr = 0, 0

    hdr = bytearray(1660)
    hdr[0:8] = MAGIC
    struct.pack_into("<I", hdr, OFF_KERNEL_SIZE, len(kernel))
    struct.pack_into("<I", hdr, OFF_KERNEL_ADDR, p["kernel_addr"])
    struct.pack_into("<I", hdr, OFF_RAMDISK_SIZE, len(ramdisk))
    struct.pack_into("<I", hdr, OFF_RAMDISK_ADDR, p["ramdisk_addr"])
    struct.pack_into("<I", hdr, OFF_SECOND_SIZE, 0)
    struct.pack_into("<I", hdr, OFF_SECOND_ADDR, 0)
    struct.pack_into("<I", hdr, OFF_TAGS_ADDR, p["tags_addr"])
    struct.pack_into("<I", hdr, OFF_PAGE_SIZE, page)
    struct.pack_into("<I", hdr, OFF_HEADER_VERSION, hv)
    struct.pack_into("<I", hdr, OFF_OS_VERSION, p["os_version"])
    if hv >= 1:
        struct.pack_into("<I", hdr, OFF_HEADER_SIZE, 1648 if hv == 1 else 1660)
    if hv >= 2:
        struct.pack_into("<I", hdr, OFF_DTB_SIZE, dtb_size)
        struct.pack_into("<Q", hdr, OFF_DTB_ADDR, dtb_addr)

    # Same id semantics as mkbootimg: sha1 over the sizes and the addresses.
    sha = hashlib.sha1()
    sha.update(struct.pack("<I", len(kernel)))
    sha.update(struct.pack("<I", p["kernel_addr"]))
    sha.update(struct.pack("<I", len(ramdisk)))
    sha.update(struct.pack("<I", p["ramdisk_addr"]))
    sha.update(struct.pack("<I", 0))   # second_size
    sha.update(struct.pack("<I", 0))   # second_addr
    sha.update(struct.pack("<I", p["tags_addr"]))
    sha.update(struct.pack("<I", page))
    sha.update(bytearray(hdr[44:576]))          # os_version, name, cmdline
    sha.update(b"\x00" * 32)                     # id itself is zeroed
    sha.update(bytearray(hdr[608:1632]))         # extra_cmdline
    hdr[576:608] = sha.digest() + b"\x00" * 12

    out = pad_to(bytes(hdr[:page]), page)        # header occupies one page
    out += pad_to(kernel, page)
    out += pad_to(ramdisk, page)
    if dtb_size:
        out += pad_to(dtb, page)
    return out


def parse(path):
    d = open(path, "rb").read()
    if d[:8] != MAGIC:
        return None
    ps, = struct.unpack("<I", d[36:40])
    hv, = struct.unpack("<I", d[40:44])
    f = dict(size=len(d), page_size=ps, header_version=hv,
             kernel_size=struct.unpack("<I", d[8:12])[0],
             kernel_addr=struct.unpack("<I", d[12:16])[0],
             ramdisk_size=struct.unpack("<I", d[16:20])[0],
             ramdisk_addr=struct.unpack("<I", d[20:24])[0],
             tags_addr=struct.unpack("<I", d[32:36])[0])
    f["kernel_off"] = ps
    nk = (f["kernel_size"] + ps - 1) // ps
    f["ramdisk_off"] = ps * (1 + nk)
    nr = (f["ramdisk_size"] + ps - 1) // ps if f["ramdisk_size"] else 0
    f["dtb_off"] = ps * (1 + nk + nr)
    if hv >= 2:
        f["header_size"] = struct.unpack("<I", d[1644:1648])[0]
        f["dtb_size"] = struct.unpack("<I", d[1648:1652])[0]
        f["dtb_addr"] = struct.unpack("<Q", d[1652:1660])[0]
    return d, f


def describe(d, f, label):
    print(f"{label}")
    print(f"  size            {f['size']:,}")
    print(f"  header_version  {f['header_version']}   page_size {f['page_size']:#x}")
    print(f"  kernel          {f['kernel_size']:#x} @ {f['kernel_addr']:#x}"
          f"   file {f['kernel_off']:#x}  first4 {d[f['kernel_off']:f['kernel_off']+4]!r}")
    print(f"  ramdisk         {f['ramdisk_size']:#x} @ {f['ramdisk_addr']:#x}"
          f"   file {f['ramdisk_off']:#x}")
    if f["header_version"] >= 2:
        print(f"  header_size     {f['header_size']}")
        print(f"  dtb             {f['dtb_size']:#x} @ {f['dtb_addr']:#x}"
              f"   file {f['dtb_off']:#x}  first4 {d[f['dtb_off']:f['dtb_off']+4]!r}")
    print(f"  tags_addr       {f['tags_addr']:#x}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fd", required=True, help="SILICIUM_UEFI.fd")
    ap.add_argument("--bootshim", required=True, help="BootShim.bin")
    ap.add_argument("--dtb", required=True, help="the device DTB")
    ap.add_argument("--ramdisk", default=None,
                    help="default: Mu-Silicium's Resources/ramdisk (5 bytes, 'dummy')")
    ap.add_argument("--compression", choices=("gzip", "none"), default="gzip")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="stock")
    ap.add_argument("--compare", metavar="IMG",
                    help="also parse this image and print both, for comparison")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    p = PROFILES[args.profile]
    shim = open(args.bootshim, "rb").read()
    fd = open(args.fd, "rb").read()
    dtb = open(args.dtb, "rb").read()
    ramdisk = open(args.ramdisk, "rb").read() if args.ramdisk else b"dummy"

    # The FD must be exactly FD_SIZE, because BootShim copies FD_SIZE bytes from
    # its own load address to FD_BASE and does not stop early.
    if len(fd) != 0x300000:
        sys.exit(f"FD is {len(fd):#x}, expected 0x300000 (FD_SIZE in the toml)")

    payload = shim + fd
    kernel = gzip.compress(payload, mtime=0) if args.compression == "gzip" else payload

    img = build(kernel, ramdisk, dtb, p)
    open(args.output, "wb").write(img)

    print(f"profile {args.profile}: compression={args.compression} "
          f"dtb={'declared' if p['dtb_in_header'] else 'appended to kernel'}")
    print(f"  bootshim {len(shim)} + fd {len(fd):#x} -> kernel {len(kernel):#x}, "
          f"dtb {len(dtb):#x}")
    print()
    got, gf = parse(args.output)
    describe(got, gf, f"-> {args.output}")
    if args.compare:
        cd, cf = parse(args.compare)
        print()
        describe(cd, cf, f"-> {args.compare} (reference)")


if __name__ == "__main__":
    main()
