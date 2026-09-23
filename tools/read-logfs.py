#!/usr/bin/env python3
"""Read the bootloader's own log off the `logfs` partition.

Why this exists: the device has no UART, and the two channels the project has
been using both fail exactly when they are needed. The screen shows a logo and
nothing else, and ABL's fastboot answers only while ABL's command loop is alive
- which is the thing that was stuck for hours. Neither can say what ABL did with
a payload it refused.

This device keeps a log, and it keeps it in a form that needs neither. `logfs`
is a FAT12 volume holding a ring of five 32 KiB text files, `UEFILOG0.TXT`
through `UEFILOG4.TXT`, written by ABL as it shuts down its boot services. On
this phone that is `BOOT.XF.3.3-00285-BITRALAZ-4`, Qualcomm's own edk2 build, so
the log covers the whole of ABL's life: the panel it selected, the boot image it
loaded, the overlay it applied, the device tree it updated, and the reason it
recorded for this boot.

Three ways in, and the tool takes all of them:

    # the raw partition, from a backup or `dd` on the device
    tools/read-logfs.py ~/backup/gauguin/images/part-logfs.img

    # a log pulled off the phone with `fastboot oem uefilog`
    tools/read-logfs.py work/fb-capture-.../uefilog.txt

    # an already-extracted slot
    tools/read-logfs.py work/logfs/UEFILOG0.TXT --full

`fastboot oem uefilog` is the fastest route and it needs ABL to be answering.
The partition route needs nothing at all - it works from the P0 backup, and on a
wedged phone it works from TWRP, which is the only route that has been reliable
here. tools/pull-bootloader-log.sh picks whichever one answers.

The summary is the point, not the text dump. A successful boot walks a fixed
sequence of stages, so the interesting question about a log is always "how far
did this one get", and the answer is read off the last stage it reached. The
stages below are the ones observed in five real boots from this device, in order.

`UEFILOG0.TXT` is the newest, and that is read off the volume rather than assumed:
the live entries and the deleted ones beside them both run 4, 3, 2, 1, 0 in
directory order, and directory entries append in write order, so the last name
written is the one ending in `0`. That is also why slots are printed in name
order - the first table is the most recent boot.

Usage:  tools/read-logfs.py IMAGE [--full] [--slot N] [-o DIR]
        tools/read-logfs.py --baseline DIR   # compare slots against each other
"""
import argparse
import os
import struct
import sys

# The stage markers, in the order ABL emits them, each with what it means for a
# payload. Anything *after* "Load Image boot" is where our image's fate is
# decided, which is why the interesting column is "last stage reached".
STAGES = [
    ("UEFI Start", "ABL's DXE core started"),
    ("DisplayDxe:", "panel selected and initialised"),
    ("Platform Init", "BDS: DXE is up, platform initialised"),
    ("POST Time", "OS Loader: the boot image path begins"),
    ("Load Image vbmeta", "vbmeta partition read"),
    ("Load Image boot", "boot partition read - our payload is being loaded"),
    ("Load Image dtbo", "dtbo partition read"),
    ("Apply Overlay", "the vendor overlay was merged into the tree we shipped"),
    ("Cmdline:", "the kernel command line was composed"),
    ("Update Device Tree", "the tree was handed to the kernel"),
    ("Shutting Down UEFI Boot Services", "handing over"),
    ("Start EBS", "handover complete"),
]

# Lines worth lifting out verbatim: they identify the build, the board, the
# panel, and the boot the log belongs to.
INTERESTING = [
    "QC_IMAGE_VERSION_STRING=", "OEM_IMAGE_VERSION_STRING=", "IMAGE_VARIANT_STRING=",
    "UEFI Ver", "Build Info", "Platform          ", "Chip Name", "Chip Ver",
    "Chip Serial", "UFS INQUIRY ID", "DDR Frequency", "Total RAM",
    "PON Reason is", "pureason =", "KeyPress:", "BootReason:", "Booting Into",
    "DisplayDxe: MDPPLATFORM", "DisplayDxe: Resolution", "GetPanelId",
    "boot state is", "RAM Partitions", "ERROR:", "Failed", "error",
]


def fat12_entries(d):
    """Every directory entry in the root of a FAT12 volume, live and deleted.

    The geometry is read from the boot sector rather than assumed: this device's
    logfs is 4096-byte sectors with 1 sector per cluster, but nothing in the
    format requires that and a tool that hardcodes it reports nothing on the
    first volume that differs. Deleted entries are returned too - the ring
    rotates, so they are how many boots back the log goes.
    """
    if d[3:11] not in (b"MSDOS5.0", b"MSWIN4.1") and b"FAT12" not in d[54:62]:
        return None
    bps, = struct.unpack_from("<H", d, 11)
    spc = d[13]
    resv, = struct.unpack_from("<H", d, 14)
    nfat = d[16]
    nroot, = struct.unpack_from("<H", d, 17)
    spf, = struct.unpack_from("<H", d, 22)
    if not bps or not spf:
        return None
    fat_start = resv * bps
    root_off = (resv + nfat * spf) * bps
    data_off = root_off + nroot * 32
    fat = d[fat_start:fat_start + spf * bps]

    def next_cluster(c):
        off = c + c // 2
        if off + 1 >= len(fat):
            return 0
        v, = struct.unpack_from("<H", fat, off)
        return (v >> 4) if (c & 1) else (v & 0xfff)

    def read_chain(cluster, size):
        out, c, guard = b"", cluster, 0
        while c and c < 0xff8 and len(out) < size and guard < 0x10000:
            start = data_off + (c - 2) * bps * spc
            out += d[start:start + bps * spc]
            c = next_cluster(c)
            guard += 1
        return out[:size]

    entries = []
    for i in range(nroot):
        e = d[root_off + i * 32: root_off + i * 32 + 32]
        if len(e) < 32 or e[0] == 0x00:
            break
        if e[11] & 0x0F == 0x0F:                    # long-file-name fragment
            continue
        name = (e[:8].decode("latin1").rstrip() + "." +
                e[8:11].decode("latin1").rstrip()).strip(".")
        if not name or name == "LOGFS":
            continue
        cluster, = struct.unpack_from("<H", e, 26)
        size, = struct.unpack_from("<I", e, 28)
        deleted = e[0] == 0xE5
        entries.append(dict(index=i, name=name, size=size, deleted=deleted,
                            data=None if deleted else read_chain(cluster, size)))
    return entries


def text_of(path):
    """The log text in a file, whichever of the three shapes it is.

    A raw logfs image, or an extracted UEFILOG*.TXT, or the output of `fastboot
    oem uefilog`, which is the same text with whatever the transport added.
    """
    d = open(path, "rb").read()
    entries = fat12_entries(d)
    if entries is not None:
        return None, entries
    txt = d.decode("latin1", "replace")
    return txt, None


def slots(txt):
    """Split a `oem uefilog` dump into its per-slot logs, if it holds several.

    Each slot starts at the "Format: Log Type" banner. A dump of one slot is one
    log; a dump of the whole partition is five.
    """
    banner = "Format: Log Type"
    if txt.count(banner) < 2:
        return [txt]
    parts = txt.split(banner)
    return [banner + p for p in parts[1:]]


def summarise(txt):
    """Where this log got to, and the lines that say which boot it was."""
    lines = [l.rstrip() for l in txt.splitlines()]
    last = None
    reached = []
    for label, _ in STAGES:
        hit = next((l for l in lines if label in l), None)
        if hit:
            reached.append((label, hit.strip()))
            last = label
    # A log that stops before "Start EBS" stopped early - either the boot did
    # not hand over, or the log was written before it did.
    complete = last == "Start EBS"
    stripped = [l for l in lines if l.strip()]
    return dict(reached=reached, last=last, complete=complete,
                lines=len(stripped), tail=stripped[-1] if stripped else "",
                interesting=[l.strip() for l in lines if l.strip() and
                             any(k in l for k in INTERESTING)])


def print_one(name, txt, full=False):
    s = summarise(txt)
    print(f"== {name}   {s['lines']} lines")
    if full:
        print(txt.rstrip())
        return
    for line in s["interesting"]:
        print(f"   {line}")
    if s["interesting"]:
        print()
    print("   stage                                    reached at")
    for label, _ in STAGES:
        hit = next((h for l, h in s["reached"] if l == label), None)
        mark = "  ->" if label == s["last"] else "    "
        print(f"   {mark} {label:<38} {hit or '-'}")
    if s["complete"]:
        print(f"\n   handed over to whatever it loaded (last line: {s['tail']!r})")
    else:
        print(f"\n   STOPPED EARLY - last stage reached: {s['last']}"
              f"\n   last line: {s['tail']!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+",
                    help="a logfs partition image, an extracted UEFILOG*.TXT, "
                         "or the output of `fastboot oem uefilog`")
    ap.add_argument("--full", action="store_true", help="dump the log text")
    ap.add_argument("--slot", type=int, help="only this slot (0 is the newest)")
    ap.add_argument("-o", "--out", help="write the slot text files here")
    args = ap.parse_args()

    rc = 0
    for path in args.images:
        txt, entries = text_of(path)
        if entries is not None:
            live = [e for e in entries if not e["deleted"]]
            print(f"== {path}")
            print(f"   FAT12 volume, {len(live)} live log file(s), "
                  f"{len(entries) - len(live)} deleted - {len(live)} per cycle, "
                  f"so the ring has been overwritten "
                  f"{(len(entries) - len(live)) // max(len(live), 1)} times")
            for e in sorted(live, key=lambda e: e["name"]):
                print(f"     {e['name']:16} {e['size']:>7,} bytes")
            print()
            if args.out:
                os.makedirs(args.out, exist_ok=True)
            items = []
            for e in live:
                body = e["data"].rstrip(b"\x00").decode("latin1", "replace")
                items.append((e["name"], body))
                if args.out:
                    with open(os.path.join(args.out, e["name"]), "w") as fh:
                        fh.write(body)
            # UEFILOG0 is the newest slot; sort so that is printed first.
            items.sort(key=lambda kv: kv[0])
        else:
            items = [(f"{os.path.basename(path)}#{i}", t)
                     for i, t in enumerate(slots(txt))]
        for i, (name, body) in enumerate(items):
            if args.slot is not None and i != args.slot:
                continue
            print_one(name, body, full=args.full)
            print()
    return rc


if __name__ == "__main__":
    sys.exit(main())
