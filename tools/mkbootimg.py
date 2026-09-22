#!/usr/bin/env python3
"""Build an Android boot image (header v2).

Written rather than pulled in as a dependency because the format is small and
fully specified, and because we need to control the DTB and cmdline directly.
The field layout below is the stock boot image header v2, and the geometry
defaults match what the gauguin bootloader ships with (read off the device's own
boot partition).

Usage:
  mkbootimg.py --kernel Image --ramdisk initramfs.cpio.gz --dtb foo.dtb \
               --cmdline "..." --out boot.img
"""
import argparse
import os
import struct
import sys

MAGIC = b"ANDROID!"

# From gauguin's stock boot header.
DEFAULT_KERNEL_ADDR = 0x00008000
DEFAULT_RAMDISK_ADDR = 0x01000000
DEFAULT_TAGS_ADDR = 0x00000100
DEFAULT_DTB_ADDR = 0x01F00000
DEFAULT_PAGE_SIZE = 4096


def pad(data, page):
    rem = len(data) % page
    return data if rem == 0 else data + b"\x00" * (page - rem)


def build(kernel, ramdisk, dtb, cmdline, name="", page_size=DEFAULT_PAGE_SIZE,
          kernel_addr=DEFAULT_KERNEL_ADDR, ramdisk_addr=DEFAULT_RAMDISK_ADDR,
          tags_addr=DEFAULT_TAGS_ADDR, dtb_addr=DEFAULT_DTB_ADDR):

    kernel_size = len(kernel)
    ramdisk_size = len(ramdisk)
    dtb_size = len(dtb)

    hdr = bytearray(1660)
    hdr[0:8] = MAGIC
    struct.pack_into("<I", hdr, 8, kernel_size)
    struct.pack_into("<I", hdr, 12, kernel_addr)
    struct.pack_into("<I", hdr, 16, ramdisk_size)
    struct.pack_into("<I", hdr, 20, ramdisk_addr)
    struct.pack_into("<I", hdr, 24, 0)                       # second_size
    struct.pack_into("<I", hdr, 28, 0)                       # second_addr
    struct.pack_into("<I", hdr, 32, tags_addr)
    struct.pack_into("<I", hdr, 36, page_size)
    struct.pack_into("<I", hdr, 40, 2)                       # header_version
    struct.pack_into("<I", hdr, 44, 0)                       # os_version
    hdr[48:48 + len(name.encode())] = name.encode()
    cb = cmdline.encode()
    if len(cb) > 512:
        sys.exit(f"cmdline too long ({len(cb)} > 512)")
    hdr[64:64 + len(cb)] = cb
    struct.pack_into("<I", hdr, 1632, 0)                     # recovery_dtbo_size
    struct.pack_into("<Q", hdr, 1636, 0)                     # recovery_dtbo_offset
    struct.pack_into("<I", hdr, 1644, 1660)                  # header_size
    struct.pack_into("<I", hdr, 1648, dtb_size)
    struct.pack_into("<Q", hdr, 1652, dtb_addr)
    # id[] left zero: only used for cache invalidation, not by the bootloader.

    out = pad(bytes(hdr), page_size)
    out += pad(kernel, page_size)
    out += pad(ramdisk, page_size)
    out += pad(dtb, page_size)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--ramdisk", default=None)
    ap.add_argument("--dtb", default=None)
    ap.add_argument("--cmdline", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    kernel = open(a.kernel, "rb").read()
    ramdisk = open(a.ramdisk, "rb").read() if a.ramdisk else b""
    dtb = open(a.dtb, "rb").read() if a.dtb else b""

    img = build(kernel, ramdisk, dtb, a.cmdline, a.name, a.page_size)
    with open(a.out, "wb") as fh:
        fh.write(img)

    print(f"{a.out}: {len(img):,} bytes "
          f"(kernel {len(kernel):,}, ramdisk {len(ramdisk):,}, dtb {len(dtb):,})")


if __name__ == "__main__":
    main()
