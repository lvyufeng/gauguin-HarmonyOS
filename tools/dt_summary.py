#!/usr/bin/env python3
"""Summarise a device tree that was dumped from a live /proc/device-tree.

The dump is a plain directory tree: one file per property, one directory per
node, property values in big-endian DT format. That is enough to recover the
board's hardware map without needing the kernel source or a compiler.

Usage:  dt_summary.py DTDIR [--filter SUBSTR] [--props]
"""
import os
import struct
import sys


def read_prop(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def decode(val):
    if val is None:
        return ""
    if val.endswith(b"\x00") and all(32 <= b < 127 or b == 0 for b in val):
        return val.rstrip(b"\x00").decode("ascii", "replace")
    if len(val) % 4 == 0 and len(val) > 0:
        words = struct.unpack(">%dI" % (len(val) // 4), val)
        # A short all-small-integer blob is almost always a cell array.
        if len(words) <= 8:
            return " ".join(hex(w) for w in words)
        return f"<{len(words)} cells> " + " ".join(hex(w) for w in words[:8]) + " …"
    return val[:32].hex()


def walk(node, depth=0, prefix="", out=None, flt=None, show_props=False):
    name = os.path.basename(node) or node
    if flt and flt not in name:
        # still descend, the interesting node may be below
        pass

    kids = []
    props = {}
    try:
        for entry in sorted(os.listdir(node)):
            full = os.path.join(node, entry)
            if os.path.isdir(full):
                kids.append(full)
            else:
                props[entry] = read_prop(full)
    except OSError:
        return

    interesting = (
        "compatible" in props or "reg" in props
        or name in ("model", "compatible", "memory", "chosen")
    )
    if interesting and (flt is None or flt in name or flt in decode(props.get("compatible"))):
        out.append(("  " * depth) + name)
        for key in ("compatible", "status", "model", "device_type", "reg",
                    "interrupts", "clocks", "clock-names", "label", "qcom,board-id",
                    "qcom,msm-id", "spi-max-frequency", "qcom,dsi-default-panel"):
            if key in props:
                out.append(("  " * depth) + f"    {key}: {decode(props[key])}")
        if show_props:
            for key, val in sorted(props.items()):
                if key not in ("compatible", "status", "model", "device_type", "reg",
                               "interrupts", "clocks", "clock-names", "label",
                               "phandle", "linux,phandle", "name"):
                    out.append(("  " * depth) + f"    .{key}: {decode(val)}")

    for kid in kids:
        if os.path.basename(kid) in ("__symbols__", "aliases", "chosen"):
            continue
        walk(kid, depth + 1, prefix, out, flt, show_props)


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    root = argv[1]
    flt = None
    show_props = "--props" in argv
    if "--filter" in argv:
        flt = argv[argv.index("--filter") + 1]

    out = []
    walk(root, 0, "", out, flt, show_props)
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv)
