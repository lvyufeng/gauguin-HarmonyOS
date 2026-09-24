#!/usr/bin/env python3
"""Pad an AML table to a chosen size *validly*, to measure what the firmware
volume will actually hold.

`[FV.FvMain]` declares `NumBlocks = 0`, so GenFv sizes `FVMAIN` from its
contents instead of the other way round, and the `[99%Full]` that GenFv prints
is a property of 4 KiB rounding rather than of the room left. Two numbers in the
build log look like a budget and are not: the `FVMAIN` percentage, and the free
bytes beside it. The only real cap is the volume `FVMAIN` sits inside,
`FVMAIN_COMPACT` (`0x300000`), which fails loudly when exceeded:

    the required fv image size 0x311bf0 exceeds the set fv image size 0x300000

This script is how that was established, and it is the way to re-establish it
after a toolchain or FDF change. It appends one `Name` object holding a
zero-filled `Buffer` and fixes the header length and checksum, so the result is
a legal table rather than arbitrary bytes — the build packs the `ASL` binary and
never parses it, so a blob would have been a weaker test than it looks, and a
test that passes for the wrong reason is worse than no test.

    python3 tools/acpi-pad.py /tmp/big.aml 0xB00000     # 11.5 MB, builds
    cp /tmp/big.aml .../gauguin/DSDT.aml
    #  then build; expect FVMAIN to auto-size and FVMAIN_COMPACT to stay at 34%
    # 2 MiB from /dev/urandom in the same slot is the negative control: it fails.

**Back up the real table before overwriting it, and restore it after.** The
gauguin `DSDT.aml` is 2,017 bytes, sha256
`8b918a16f0ffac2819a29952e53f6eac18b13b12e5ad1cf3faa2f21e08080183`; the
rebuild that follows a restore should give `FVMAIN` back at
`7352064 used, 256 free`. The device tree is not touched by any of this — it is
a build-time experiment only, and nothing here belongs on the phone.

    python3 tools/acpi-pad.py OUT PAD [IN]

AML encoding used (ACPI 6.x), for the reader who wants to check it by hand:

    NameOp  := 0x08 NameString(4) DataRefObject
    BufferOp:= 0x11 PkgLength BufferSize(4) ByteList

`PkgLength` for >= 0x100000 bytes is the four-byte form, `0x83` followed by a
u32; it counts the bytes of the package *after* the `PkgLength` field itself,
so the buffer size field plus the data.
"""
import argparse
import hashlib
import struct
import sys

GAUGUIN_DSDT = ("work/uefi/Mu-Silicium/Silicium-ACPI/Platforms/Xiaomi/"
                "gauguin/DSDT.aml")
REAL_SHA256 = "8b918a16f0ffac2819a29952e53f6eac18b13b12e5ad1cf3faa2f21e08080183"

# What a Buffer this big would threaten: FVMAIN_COMPACT, the outer volume.
FVMAIN_COMPACT_SIZE = 0x300000


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="table to write")
    ap.add_argument("pad", type=lambda s: int(s, 0),
                    help="bytes of buffer to append, e.g. 0xB00000")
    ap.add_argument("inp", nargs="?", default=GAUGUIN_DSDT,
                    help=f"table to pad (default: {GAUGUIN_DSDT})")
    args = ap.parse_args()

    aml = bytearray(open(args.inp, "rb").read())
    declared, = struct.unpack_from("<I", aml, 4)
    if declared != len(aml):
        sys.exit(f"{args.inp}: header says {declared} bytes, file is "
                 f"{len(aml)} — refusing to pad a table that is already wrong")

    digest = hashlib.sha256(aml).hexdigest()
    if args.inp == GAUGUIN_DSDT and digest != REAL_SHA256:
        print(f"warning: {args.inp} is not the expected real table\n"
              f"  want {REAL_SHA256}\n  got  {digest}", file=sys.stderr)

    payload = struct.pack("<I", args.pad) + b"\x00" * args.pad
    if not len(payload) < 0x10000000:
        sys.exit(f"pad {args.pad} is too large for the four-byte PkgLength form")
    # 0x83 => four length bytes follow, little-endian
    obj = b"\x08BUF0" + b"\x11" + bytes([0x83]) + struct.pack("<I", len(payload)) \
          + payload

    out = aml + bytearray(obj)
    struct.pack_into("<I", out, 4, len(out))       # header: table length
    out[9] = 0
    out[9] = (-sum(out)) & 0xFF                    # header: checksum
    with open(args.out, "wb") as fh:
        fh.write(out)

    print(f"wrote {args.out}: {len(out):,} bytes, checksum {out[9]:#04x}, "
          f"buffer payload {args.pad:,} bytes")
    print(f"  source {args.inp} sha256 {digest}")
    print(f"  {len(out):,} against FVMAIN_COMPACT's {FVMAIN_COMPACT_SIZE:,} "
          f"({len(out)/FVMAIN_COMPACT_SIZE:.2f}x) — the buffer compresses to "
          f"almost nothing, so this is not the test that fails; noise is")


if __name__ == "__main__":
    main()
