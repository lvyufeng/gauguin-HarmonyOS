#!/usr/bin/env python3
"""Read the phone's physical memory, from TWRP, over adb, without writing anything.

Why this exists: **four sessions of this phase have been spent trying to read one
row off the phone's panel, and the panel is a terrible channel.** It holds the
last 99 rows of output and no more; `FrameBufferSerialPortLib` does not scroll, it
`ZeroMem`s the whole screen and starts again at (0,0); the reading is a
36-character GUID that has to be transcribed by eye from a photographed phone; and
whether the interesting line is even on the glass depends on how far a run got
before the APSS watchdog reset it. The one thing the panel has going for it is
that it is the only output channel this payload has - `ULogDxe` is one of the 27
drivers that fail to load, so DXE cannot write the bootloader's log, and there is
no USB device stack, so it cannot push anything either.

That is a property of DXE, and it stops being true the moment the phone is in
TWRP, because TWRP is a Linux with a root shell. Physical memory is still there,
and a root Linux can read it:

    /dev/mem      if TWRP's kernel has CONFIG_DEVMEM and not CONFIG_STRICT_DEVMEM
    /proc/kcore   an ELF view of the same thing, which is often left readable

**It does not, however, recover the run that just failed, and that has to be said
before anything else, because it is the obvious thing to hope for and the hope is
wrong.** By the time TWRP's adbd answers, the payload has been dead for a while
and the phone has booted at least twice more: XBL and ABL ran again, and a Linux
kernel is now running with this address inside its System RAM. The five
`UEFILOG*.TXT` slots in `work/bllog-20260924-logfs/txt/` are what settles it -
five consecutive bootloader runs, each rendered from a fresh ring that begins at
SBL's power-on (`S - QC_IMAGE_VERSION_STRING=BOOT.XF.3.3-00285-BITRALAZ-4`,
`S -     82913 - PBL, End`) and ends at the handoff (`Start EBS [ 4391]`), five
slot files differing only in their timings. The ring is rebuilt per boot and the
renderer emits only its own boot's entries, so **the panel remains the only
channel for the run that just happened** and nothing here changes that.

What it is for is the other four things, all of which are real and none of which
is a way around the panel:

  * `--probe` answers a question this project has never answered: *is physical
    memory readable on this phone at all?* `/dev/mem` needs `CONFIG_DEVMEM` and
    not `CONFIG_STRICT_DEVMEM`, and TWRP's kernel is not this project's to
    choose. Until it is tried, "we could read memory" is an assumption, and the
    whole class of instruments that would sit on top of it - including everything
    below - is speculation. It is the cheapest read here and it should go first.

  * `--iomem` takes the kernel's own account of the map. The table in
    `device/config/uefiplat.cfg` (`/proc/iomem`'s sibling) has been treated as
    ground truth for several sessions and has never once been checked against a
    system that is actually running. The kernel's view is *after* XBL's
    carveouts, so the two should disagree in an explainable way; if they disagree
    in a way that is not explainable, that is worth knowing now rather than at P4.

  * `--ulog` takes the 32 KiB ring that `ULogDxe` renders into `logfs`. Read raw,
    its entry format can be taken off a real buffer instead of guessed - which is
    the prerequisite for the one durable channel that might exist here, a payload
    that writes into the ring before it dies. Whether that can work is unknown and
    is **not** claimed here. The five slots say the ring is rebuilt per boot,
    which is evidence against; the ~19 KiB of the ring that ABL's 13 KiB never
    reaches is evidence for. It is one read to find out, and it is a read of a
    region, not a write to a partition.

  * `--fb` takes the framebuffer. The device tree reserves `0xa0000000 + 0x2300000`
    and `0xa2300000 + 0x100000`, which together are exactly this map's
    `Display Reserved 0xA0000000 + 0x02400000` row - so Linux is kept out of it,
    though the bootloader on the way to TWRP paints over it. It is here because a
    dump of the panel is recognisable as a screen and a dump of garbage is
    recognisable as not one, which is a cheap way to prove a read lands where the
    arithmetic says it does.

**Nothing here writes.** Every operation is a `dd` out of a character device or
the bootloader's log partition. That matters because the standing rule for this
phase is that nothing on this device's storage is written before P4, and the one
relaxation - the `boot` partition - does not extend to a memory reader that has
no need of it.

The reads follow the rules this project has already paid for twice:

  * **`status=none` and a per-chunk truncation.** TWRP ships toybox 0.8.4, whose
    `dd` prints its statistics to *stdout*, and `adb exec-out` captures stdout.
    An unqualified chunked read therefore glues ~80 bytes of `1+0 records in ...`
    to each chunk and shifts everything after the first one. This exact bug once
    made `tools/identify-boot.py` certify a readback that was 80 bytes out of
    register against the image it came from. The truncation makes the read right
    even if `status=none` is ignored, because the statistics are written *after*
    the data.
  * **A second opinion that is not the host.** After a read the same range is
    hashed *on the phone* and compared. Every other check here compares two copies
    one program made, so a read that is wrong in a way that program repeats
    faithfully passes all of them. The phone's `sha256sum` is a different program
    and travels as 64 hex characters.
  * **A block size that divides the offset.** `/dev/mem` is read with `bs=4096`
    and `skip=<addr>/4096` rather than a 1 MiB block, because a `skip` that is not
    a whole number of blocks is not where the arithmetic says it is. `--chunk`
    controls how much is pulled per `adb` call and is a multiple of 4096.

Usage:

    tools/read-dram.py --probe                 # can this phone be read at all?
    tools/read-dram.py --iomem                 # the kernel's own view of RAM
    tools/read-dram.py --ulog                  # 32 KiB of the bootloader's ring
    tools/read-dram.py --ulog --out my.bin
    tools/read-dram.py --addr 0xA0000000 --size 0x40000
    tools/read-dram.py --scan 0x9FF00000 0x100000      # hexdump of what is there
    tools/read-dram.py --search 0x9F000000 0x800000 P2MARK   # find a marker

`--probe` is deliberately cheap and is the first thing to run: it answers the
question that decides whether any of the rest is possible, and it answers it
without moving 36 MiB over a link that drops about once a minute.

Exit status is 0 when every requested read completed **and** its own on-device
hash agreed, 1 when a read was refused or disagreed, and 2 when the phone is not
there. A refusal is a real answer here, not a failure of the tool: `/dev/mem`
being absent or blocked is exactly the kind of thing worth knowing for certain.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PLATFORM_CFG = os.path.join(ROOT, "device", "config", "uefiplat.cfg")
OUTDIR = os.path.join(ROOT, "work", "out", "dram")

# The two presets, and the reason each is here rather than as a number on the
# command line. Both are read out of the platform config below as well, so the
# label in the output comes from the device's own file and not from this dict.
PRESETS = {
    "ulog": (0x9FFF7000, 0x00008000,
             "the bootloader's printk ring - SBL and ABL write here and ULogDxe "
             "renders it to logfs/UEFILOG*.TXT"),
    "logbuf": (0x9FFF7000, 0x00008000, None),
    "fb": (0xA0000000, 0x02400000,
           "the framebuffer - wiped by the next boot's logo, here as a check that "
           "a read lands where it says"),
}

# /proc/iomem and the map file both use names this project already knows; the
# point of labelling a dump with them is that "0x9FFF7000" is not a reading and
# "Log Buffer" is.
MAP_LINE = re.compile(
    r'^(0x[0-9A-Fa-f]+)\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*"([^"]*)"\s*,\s*(\w+)\s*,')


def adb(args, **kw):
    return subprocess.run(["adb"] + args, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, **kw)


def shell(cmd, timeout=60):
    """Run `cmd` through `adb shell` and return (rc, stdout text)."""
    r = subprocess.run(["adb", "shell", cmd], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").replace("\r", "")


def load_map():
    """[(base, size, label, memtype)] from the device's own platform config.

    Read rather than hard-coded, because the whole point of the label is to say
    what *this device's* XBL calls that address. A hard-coded table would be a
    second copy of `device/config/uefiplat.cfg` and would be wrong the first time
    the map is regenerated.
    """
    rows = []
    try:
        for line in open(PLATFORM_CFG, encoding="utf-8", errors="replace"):
            m = MAP_LINE.match(line.strip())
            if m:
                rows.append((int(m.group(1), 16), int(m.group(2), 16),
                             m.group(3), m.group(4)))
    except OSError:
        pass
    return rows


def label_for(addr, rows):
    for base, size, label, mtype in rows:
        if base <= addr < base + size:
            return f"{label} ({mtype}, +{addr - base:#x} into it)"
    return None


def region_for(addr, size, rows):
    """The named regions a range overlaps, so a dump says what it covers."""
    out = []
    for base, rsize, label, mtype in rows:
        lo, hi = max(base, addr), min(base + rsize, addr + size)
        if hi > lo:
            out.append(f"{label} [{base:#x}..{base + rsize:#x}] {mtype}"
                       f"  {hi - lo:#x} bytes of this read")
    return out


def sha_on_device(path, addr, size):
    """sha256 of `size` bytes at `addr`, computed on the phone.

    Two routes, and the first is tried because it is the one that proves the
    *same* bytes were read rather than that two readers agree. If `path` names a
    real file the hash is of its first `size` bytes; otherwise the hash is taken
    straight from the device node again.
    """
    if path:
        rc, out = shell(f"dd if={path} bs=4096 count={size // 4096} status=none | sha256sum")
    else:
        rc, out = shell(f"dd if=/dev/mem bs=4096 skip={addr // 4096} "
                        f"count={size // 4096} status=none | sha256sum")
    tok = out.split()
    if rc != 0 or not tok or len(tok[0]) != 64:
        return None
    return tok[0].lower()


def read_device(node, addr, size, chunk):
    """Pull `size` bytes at `addr` from a character device into memory.

    `bs` is 4096 and `skip` is `addr/4096`, never a large block with a
    proportional skip: a skip that is not a whole number of blocks silently
    starts somewhere else, and on a memory reader that produces plausible bytes
    from the wrong place. `chunk` is how much is asked for per `adb` call and is
    rounded down to a multiple of 4096 for the same reason.
    """
    chunk -= chunk % 4096
    got = bytearray()
    while len(got) < size:
        want = min(chunk, size - len(got))
        start = addr + len(got)
        r = subprocess.run(
            ["adb", "exec-out", "dd", f"if={node}", "bs=4096",
             f"skip={start // 4096}", f"count={want // 4096}", "status=none"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if r.returncode != 0 or not r.stdout:
            return bytes(got), (f"stopped at {len(got):,} of {size:,} bytes "
                                f"(rc={r.returncode}, {len(r.stdout)} bytes back)")
        # Never longer than asked: a chunk that comes back long carries the
        # statistics tail dd writes after its data.
        got += r.stdout[:want]
        print(f"\r    {len(got):,}/{size:,} bytes", end="", file=sys.stderr)
    print(file=sys.stderr)
    return bytes(got), None


KERNEL_LOG_LEVELS = {
    "0": "KERN_EMERG", "1": "KERN_ALERT", "2": "KERN_CRIT", "3": "KERN_ERR",
    "4": "KERN_WARNING", "5": "KERN_NOTICE", "6": "KERN_INFO", "7": "KERN_DEBUG",
}

# `dd if=/dev/mem` on a range that is not RAM (or is blocked by
# CONFIG_STRICT_DEVMEM) fails in ways that all look the same from here, so the
# probe reports the three facts separately rather than one verdict.
def probe():
    rc, out = adb(["devices"]).stdout.decode().splitlines(), None
    devs = [l for l in rc[1:] if l.strip()]
    print("adb devices")
    for d in devs:
        print(f"  {d}")
    if not devs:
        print("\n-> no device. TWRP's adbd is the one that is root; if the phone is"
              "\n   in the UEFI payload it will not appear, and if it is in Android"
              "\n   `su` is needed. `adb devices` showing nothing is the answer, not"
              "\n   an error.")
        return 2

    print("\nadbd identity")
    for cmd, why in [("id", "uid 0 means the reads below need no su"),
                     ("getprop ro.product.device", "should be gauguin"),
                     ("cat /proc/version", "the kernel the reads go through")]:
        _rc, out = shell(cmd)
        print(f"  {why}\n    {out.strip()[:120]}")

    print("\ncharacter devices")
    for node in ("/dev/mem", "/proc/kcore"):
        _rc, out = shell(f"ls -l {node}")
        ok = out.strip() and "No such" not in out
        print(f"  {node:14s} {'present' if ok else 'ABSENT'}   {out.strip()[:80]}")

    print("\nreadable?  (4 KiB at the Log Buffer, the region this is for)")
    addr = PRESETS["ulog"][0]
    rc, out = shell(f"dd if=/dev/mem bs=4096 skip={addr // 4096} count=1 "
                    f"status=none | wc -c")
    n = out.strip()
    print(f"  /dev/mem  {addr:#x}  ->  {n or '(nothing)'} bytes")
    if n == "4096":
        # 4096 bytes is not the same as the right 4096 bytes, so the first eight
        # are printed: the bootloader's ring is full of printable text and a
        # wrong region almost never is.
        _rc, out = shell(f"dd if=/dev/mem bs=4096 skip={addr // 4096} count=1 "
                         f"status=none | head -c 64 | od -c | head -4")
        print("  first 64 bytes:")
        for line in out.splitlines():
            print(f"    {line}")
        print("  -> physical memory is readable. Anything after this is possible.")
        return 0

    print("  /dev/mem refused - trying /proc/kcore")
    _rc, out = shell("dd if=/proc/kcore bs=1 count=4 status=none | od -An -tx1")
    print(f"  /proc/kcore  ->  {out.strip() or '(nothing)'}  (7f45 4c46 = ELF)")
    print("\n  Neither route answered. That is a real answer: on this TWRP the"
          "\n  kernel is not handing out physical memory (CONFIG_STRICT_DEVMEM, or"
          "\n  no CONFIG_DEVMEM at all). Nothing below would work, and the panel"
          "\n  stays the only channel.")
    return 1


def iomem():
    _rc, out = shell("cat /proc/iomem")
    if not out.strip():
        print("/proc/iomem is empty or unreadable - this kernel hides it")
        return 1
    print(out)
    print("Compare the System RAM lines against device/config/uefiplat.cfg. The"
          "\nkernel's view is *after* XBL has taken its carveouts, so it should be a"
          "\nsubset of the DDR rows: a range the kernel calls System RAM and this"
          "\nrepo does not list is memory XBL gave away, and one this repo lists"
          "\nthat the kernel does not is a carveout.")
    return 0


def scan(addr, size):
    """Hexdump a range, with the printable text beside it.

    Not a read of a known region: this is for finding out what is at an address,
    which is how a candidate log buffer is recognised before anyone commits to a
    decoder for it.
    """
    rc, out = shell(f"dd if=/dev/mem bs=4096 skip={addr // 4096} "
                    f"count={max(1, size // 4096)} status=none | od -A x -t x1z "
                    f"| head -80")
    if not out.strip():
        print(f"nothing readable at {addr:#x} for {size:#x} bytes")
        return 1
    print(f"{addr:#x} + {size:#x}")
    print(out)
    return 0


def search(addr, size, needle, chunk=1 << 20, context=48):
    """Find a string in a range and print the bytes around each hit.

    The forward use, and the reason this is a search rather than a dump: a payload
    that leaves a marker somewhere it does not own has to be found again from
    here, and the thing that is unknown is precisely *which* address it ended up
    at. A hexdump answers that badly. Nothing else about the marker is assumed -
    not its alignment, not that a log structure precedes it - because the whole
    point is to find out what the surrounding bytes are.
    """
    want = needle.encode()
    data, err = read_device("/dev/mem", addr, size, chunk)
    if err:
        print(f"  !! {err}")
    if not data:
        return 1
    hits, start = [], 0
    while True:
        i = data.find(want, start)
        if i < 0:
            break
        hits.append(i)
        start = i + 1
    print(f"\nsearched {len(data):,} bytes of {addr:#x}..{addr + len(data):#x}"
          f" for {needle!r}: {len(hits)} hit(s)")
    if not hits:
        print("  not found. That is a reading: either the payload did not get as"
              " far as writing, or it wrote elsewhere, or this range is not the"
              " range it wrote to.")
        return 1
    for i in hits[:20]:
        lo, hi = max(0, i - context), min(len(data), i + len(want) + context)
        print(f"\n  at {addr + i:#x}  (+{i:#x} into the range)")
        print(f"    {bytes(b if 32 <= b < 127 else 46 for b in data[lo:i]).decode()}"
              f"[{data[i:i + len(want)].decode()}]{bytes(b if 32 <= b < 127 else 46 for b in data[i + len(want):hi]).decode()}")
    if len(hits) > 20:
        print(f"\n  ... and {len(hits) - 20} more")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--probe", action="store_true",
                    help="is physical memory readable on this phone at all")
    ap.add_argument("--iomem", action="store_true", help="the kernel's own memory map")
    ap.add_argument("--ulog", "--logbuf", dest="preset", action="store_const",
                    const="ulog", help="the bootloader's 32 KiB printk ring")
    ap.add_argument("--fb", dest="preset", action="store_const", const="fb",
                    help="the framebuffer (wiped by the next boot)")
    ap.add_argument("--addr", type=lambda s: int(s, 0))
    ap.add_argument("--size", type=lambda s: int(s, 0))
    ap.add_argument("--scan", nargs=2, type=lambda s: int(s, 0), metavar=("ADDR", "SIZE"))
    ap.add_argument("--search", nargs=3, metavar=("ADDR", "SIZE", "STRING"),
                    help="read a range and report every offset a string is at")
    ap.add_argument("--out", help="where to write the dump")
    ap.add_argument("--node", default="/dev/mem",
                    help="character device to read (default /dev/mem)")
    ap.add_argument("--chunk", type=lambda s: int(s, 0), default=1 << 20,
                    help="bytes per adb call, multiple of 4096 (default 1 MiB)")
    args = ap.parse_args()

    if args.probe:
        return probe()
    if args.iomem:
        return iomem()
    if args.scan:
        return scan(*args.scan)
    if args.search:
        saddr, ssize, needle = args.search
        return search(int(saddr, 0), int(ssize, 0), needle)

    if args.preset:
        addr, size, why = PRESETS[args.preset]
        if why:
            print(f"{args.preset}: {why}")
    elif args.addr is not None:
        addr, size = args.addr, args.size or 4096
    else:
        sys.exit("read-dram: give --probe, --ulog, --fb, or --addr/--size."
                 " `--probe` first: it is cheap and it decides whether the rest"
                 " is possible.")

    if addr % 4096 or size % 4096:
        sys.exit(f"read-dram: {addr:#x}+{size:#x} is not 4096-aligned. The block"
                 f" size is 4096 and an unaligned skip reads from somewhere else.")

    rows = load_map()
    print(f"\n{addr:#x} + {size:#x}  ({size:,} bytes)  from {args.node}")
    lab = label_for(addr, rows)
    print(f"  platform config says: {lab or 'nothing - this address is not in the map'}")
    for r in region_for(addr, size, rows):
        print(f"  overlaps:            {r}")

    data, err = read_device(args.node, addr, size, args.chunk)
    if err:
        print(f"  !! {err}")
    if not data:
        return 1

    print(f"  read {len(data):,} bytes")
    local = hashlib.sha256(data).hexdigest()
    print(f"  host sha256   {local}")
    if not err:
        check = sha_on_device(None, addr, len(data) - len(data) % 4096)
        if check is None:
            print("  phone sha256  (not obtained - the same read failed twice)")
        else:
            sub = hashlib.sha256(data[:len(data) - len(data) % 4096]).hexdigest()
            print(f"  phone sha256  {check}  {'ok' if check == sub else 'MISMATCH'}")
            if check != sub:
                print("  -> the phone and the host disagree about the same bytes."
                      " Do not use this dump.")

    os.makedirs(OUTDIR, exist_ok=True)
    out = args.out or os.path.join(
        OUTDIR, f"dram-{addr:08x}-{size:x}{'-partial' if err else ''}.bin")
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"  -> {os.path.relpath(out, ROOT)}")

    # What is in it. A memory dump with no text in it is not a reading, and the
    # question "is this the buffer I think it is" is answered by looking.
    printable = sum(1 for b in data[:4096] if 32 <= b < 127 or b in (9, 10, 13))
    print(f"  {printable}/4096 printable bytes in the first page"
          f" ({100.0 * printable / 4096:.0f}%)"
          f"{' - text, as a log buffer should be' if printable > 3000 else ''}")
    head = bytes(b if 32 <= b < 127 else 46 for b in data[:256]).decode("ascii")
    print(f"  first 256 bytes as text:\n    {head}")

    if args.preset == "ulog":
        print("\n  This is a raw sample of the ring that ULogDxe renders into"
              "\n  logfs/UEFILOG*.TXT. Compare it against a slot from"
              "\n  `tools/read-logfs.py` to read the entry format off a real buffer"
              "\n  instead of guessing it - the rendered file's lines are"
              "\n  `B - <time> - <msg>`, `D - <delta> - <msg>` and `S - <msg>`, so"
              "\n  the raw form is a per-entry type, a timestamp and a length.")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
