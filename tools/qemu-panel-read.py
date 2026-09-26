#!/usr/bin/env python3
"""Read the bring-up console off a firmware running under QEMU, by sampling its own memory.

The panel is the only channel this firmware has. `SerialPortLib` is bound to
`FrameBufferSerialPortLib` in a DEBUG build, so every `DEBUG ((...))` in the tree
comes out as pixels in the "Display Reserved" region and nowhere else, and every
reading this phase has produced has been a person looking at a screen and typing
back what they saw - which is why `panel-text.py` exists and why its history is a
list of mis-transcriptions.

The screen is not the only thing that holds those pixels. Under QEMU the firmware
runs in a process that can dump its own memory, so the same console can be read
without a photograph, without a phone, and without a transcription: dump the region
`GetFrameBufferMemory` prints into and hand it to `panel-text.py --fb`, which
decodes it exactly - the values are only 0 and 1 and the origin is known, so there
is nothing to estimate.

What this tool adds is the part the console makes hard: **it wipes.** When the
cursor passes the last row, `AdvanceNewLine` zeroes the whole buffer rather than
scrolling, so one dump is one screen - the last 100 printed rows - and a boot log
is longer than that. So the region is sampled repeatedly and the screens are joined
on content (`panel-text.py`'s `fb_overlap`), which recovers the stream as long as
consecutive samples share a line. At `PcdFrameBufferDelay` 10000 us per newline a
full screen takes about a second, so the default quarter-second interval has room
to spare; an interval longer than a screenful of output is the one way to lose
text, and it is reported as a gap rather than spliced over.

The region's address and length are read out of the platform's `MemoryMapLib.c` and
not passed in, for the same reason `panel-text.py` reads the font out of `Font.h`:
a remembered 0xA0000000 is a guess that stops being true silently.

    # launch the payload and sample it for a minute
    tools/qemu-panel-read.py --kernel /tmp/gauguin-kernel.raw --seconds 60

    # the same, under an EL3 that exists, so an SMC returns instead of faulting
    tools/qemu-panel-read.py --kernel /tmp/gauguin-kernel.raw --el3-stub --seconds 60

    # and with an address the machine does not decode reading as zero, so that a
    # driver which reads a pointer out of one can be got past
    tools/qemu-panel-read.py --kernel /tmp/gauguin-kernel.raw --el3-stub \
        --el3-zero-mem --seconds 60

    # or attach to a QEMU already running with -monitor unix:/tmp/qmon.sock,...
    tools/qemu-panel-read.py --socket /tmp/qmon.sock --seconds 60

The output file carries the run's provenance - payload hash, the command line, the
region, the sample count, the gaps - because a boot log with no record of what was
booted is a paragraph someone typed.
"""

import argparse
import hashlib
import importlib.util
import io
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_MU = os.path.join(REPO, "work", "uefi", "Mu-Silicium")
PANEL_TEXT = os.path.join(REPO, "tools", "panel-text.py")
# Where BootShim expects to be loaded. `_Payload` is copied from here to the FD
# base in the header it carries, so this is an address the payload names itself
# rather than one chosen for it - see the REQUIRES_KERNEL_HEADER block.
DEFAULT_LOAD_ADDR = 0x48000000
# How far above the stub the payload is placed. The stub is 16 KB with the
# stage-2 tables in it and under 5 KB without, so this is alignment slack rather
# than a fit, and it keeps the two apart by more than either can grow into.
STUB_GAP = 0x10000

# The address EnvDxe reads at Apriori slot 2 and cannot get under `virt`.
#
# The address is not a guess and not a constant of the tool's own choosing: it is
# in the driver's image. EnvDxe loads 0x01F00000 from RVA 0xC7D0, adds the pair
# (0x0D4000, 0x0D4004) from RVA 0xC7F4, and dereferences the sum, and the `ldr`
# that faults has 0x1FD4000 in FAR - the two agree, and `qemu-el3-stub.S` carries
# the same derivation. It is *inside* `TCSR_TCSR_REGS`, 0x01FC0000 for 0x40000,
# which this board's memory map declares; so is the second address the guest
# stops on, 0x0C264000, which is `PSHOLD`. Both aborts are EC 0x25 with DFSC
# 0b010000 - a synchronous external abort, which is a bus with nothing at the
# address, not a translation fault, which is what an unmapped region looks like.
# So this is kept as the spot the read-back checks, and not as the one address the
# instrument aims at - see LOW_MMIO_LIMIT.
ZERO_MEM_IPA = 0x01FD4000

# Everything at or above this is left alone by the stage-2 instrument, and
# everything below it that the platform's memory map declares is backed with RAM.
#
# The line is not arbitrary. It is where `-M virt`'s own RAM begins, so a
# redirect destination below it would be RAM the guest can also reach by identity;
# and it is where this platform's DDR ends and its register blocks stop: every
# register region in `device/config/uefiplat.cfg` is below 0x40000000, and the
# only DDR region below it is `LLCC0` at 0x09200000.
LOW_MMIO_LIMIT = 0x40000000

# The pool the redirected blocks are pointed at: `-M virt`'s RAM base, below the
# payload's own load address, so the blocks are real memory nothing else claims.
# `l2_plan` assigns them densely from here and refuses to run if they would reach
# the payload.
ZERO_MEM_POOL_BASE = 0x40000000

# The size of a stage-2 level 2 block, which is what a redirected region is.
STAGE2_BLOCK = 0x200000

# A stage-2 level 2 table has 512 entries covering the low gigabyte.
STAGE2_L2_ENTRIES = 512


def die(msg):
    sys.exit(f"qemu-panel-read: {msg}")


def load_panel_text():
    """`panel-text.py` as a module, so there is one decoder and not two.

    Read by path rather than imported by name because it is a script in `tools/`
    and this is another one; a second font parser here would be a second answer to
    what the screen says.
    """
    spec = importlib.util.spec_from_file_location("panel_text", PANEL_TEXT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def platform_paths(mu, pkg):
    """(the package directory, its DSC) for a platform, defaulting to gauguin.

    Two files, one place: `panel-text.py` wants the DSC and the memory map is
    beside it, and deriving the second from the first is what keeps a `--platform`
    pointing at one device rather than a pair that can disagree.
    """
    pkg = pkg or os.path.join(mu, "Platforms", "Xiaomi", "gauguinPkg")
    base = os.path.basename(pkg.rstrip("/"))
    dsc = os.path.join(pkg, base[:-3] + ".dsc" if base.endswith("Pkg") else base + ".dsc")
    if not os.path.exists(dsc):
        die(f"no DSC at {dsc} - --platform takes a package directory such as"
            f" Platforms/Xiaomi/gauguinPkg")
    return pkg, dsc


def fb_region(pkg):
    """(name, base, length) of the framebuffer, out of the platform's memory map.

    The console finds its buffer with `LocateMemoryRegionByName ("Display
    Reserved", ...)`, so the region that matters is the one the platform declares
    under that name. Both spellings are in the tree - the library is tried with
    each in turn - so either matches.
    """
    path = os.path.join(pkg, "Library", "MemoryMapLib", "MemoryMapLib.c")
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        die(f"cannot read the platform memory map at {path}: {exc}")
    m = re.search(r'\{\s*"Display[_ ]Reserved"\s*,\s*(0x[0-9A-Fa-f]+)\s*,'
                  r'\s*(0x[0-9A-Fa-f]+)', text)
    if not m:
        die(f"{os.path.relpath(path, REPO)} has no \"Display Reserved\" region - the"
            f" console would have nothing to print into, so this tool's whole"
            f" premise needs rechecking before its numbers are used")
    return "Display Reserved", int(m.group(1), 16), int(m.group(2), 16)


class Monitor:
    """QEMU's monitor, one command at a time, waiting for the prompt.

    The prompt and not a sleep: `pmemsave` of a few megabytes takes as long as it
    takes, and a sampler that guesses has to guess long enough for the worst case
    and therefore samples the console more slowly than it could. Readline's echo
    and bracketed-paste noise are stripped, because a command is recognised by its
    result and not by its own echo coming back.
    """

    def __init__(self, path, timeout=10.0):
        self.s = socket.socket(socket.AF_UNIX)
        deadline = time.time() + timeout
        while True:
            try:
                self.s.connect(path)
                break
            except OSError:
                if time.time() > deadline:
                    die(f"no QEMU monitor at {path} after {timeout:.0f}s - pass"
                        f" --launch, or start QEMU with -monitor unix:{path},"
                        f"server=on,wait=off")
                time.sleep(0.1)
        self.s.settimeout(timeout)
        self._read_until_prompt()

    def _read_until_prompt(self, timeout=10.0):
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            self.s.settimeout(max(0.05, end - time.time()))
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if buf.rstrip().endswith(b"(qemu)"):
                break
        return re.sub(rb"\x1b\[[0-9;]*[A-Za-z]", b"", buf).decode("latin1", "replace")

    def cmd(self, text, timeout=10.0):
        self.s.sendall(text.encode() + b"\n")
        return self._read_until_prompt(timeout)


def launch(kernel, sock, machine, memory, load_addr, extra, stub=None):
    """Start QEMU with the payload loaded where BootShim expects it.

    `-device loader,force-raw=on` and not `-kernel`: this is not an ELF and not a
    Linux image, it is the raw BootShim + FD that Android's boot image carries, and
    QEMU's `-kernel` would try to find a header in it. The `-M virt` flat RAM is
    what makes the platform's fixed addresses (FD 0x9FC00000, DXE heap 0x9B800000,
    Display Reserved 0xA0000000) reachable at all.

    `-serial none` and `-display none` because the console is in memory and
    `pmemsave` is how it is read; a serial port here would be a second, emptier
    channel that invites the wrong conclusion when it stays silent.

    With `stub`, the EL3 stub is the thing QEMU resets into and the payload is
    loaded one gap above it, so that the stub's `eret` is what starts the
    firmware. Only then is `cpu-num=0` the stub's and not the payload's.
    """
    argv = ["qemu-system-aarch64", "-M", machine, "-cpu", "max", "-m", str(memory),
            "-nic", "none", "-display", "none", "-serial", "none",
            "-monitor", f"unix:{sock},server=on,wait=off"]
    if stub:
        argv += ["-device", f"loader,file={stub},addr={load_addr:#x},force-raw=on,cpu-num=0",
                 "-device", f"loader,file={kernel},addr={load_addr + STUB_GAP:#x},force-raw=on"]
    else:
        argv += ["-device", f"loader,file={kernel},addr={load_addr:#x},force-raw=on,cpu-num=0"]
    argv += extra
    print("  " + " ".join(argv))
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def low_regions(pkg):
    """Every region the platform's own memory map puts below `LOW_MMIO_LIMIT`.

    Read out of the *generated* `MemoryMapLib.c` and not out of the generator's
    input, because the claim this list supports is about the firmware under test:
    what it believes it has is what its own map says, and a table built from the
    configuration instead would be modelling the intent rather than the image.

    Both classes come back: register regions, which under `virt` have nothing at
    them at all, and any DDR region down here - `LLCC0` is one - which has nothing
    at it either. They are handed back the same way because the machine cannot
    reach the difference, and because giving each its own RAM makes a DDR region
    behave like memory rather than like a register file that reads zero.
    """
    path = os.path.join(pkg, "Library", "MemoryMapLib", "MemoryMapLib.c")
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        die(f"cannot read the platform memory map at {path}: {exc}")
    found = re.findall(r'\{\s*"([^"]+)"\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*(0x[0-9A-Fa-f]+)',
                       text)
    regions = [(n, int(b, 16), int(s, 16)) for n, b, s in found if int(b, 16) < LOW_MMIO_LIMIT]
    if not regions:
        die(f"{os.path.relpath(path, REPO)} declares nothing below"
            f" {LOW_MMIO_LIMIT:#x}; there is then no reason for --el3-zero-mem to"
            f" exist and the flag is being asked to model a machine this map does"
            f" not describe")
    for n, b, s in regions:
        if s == 0 or b + s > LOW_MMIO_LIMIT:
            die(f"{os.path.relpath(path, REPO)}'s {n} runs {b:#x}..{b + s:#x}, which"
                f" crosses the {LOW_MMIO_LIMIT:#x} line this instrument stops at;"
                f" the partial-block arithmetic below would be wrong and guessing at"
                f" it is worse than stopping")
    return regions


def l2_plan(regions, load_addr):
    """`{block index: scratch address}` for the stage-2 level 2 table.

    One 2 MB block of the pool per 2 MB block of low address that a region
    occupies, assigned densely from `ZERO_MEM_POOL_BASE`. Dense and not
    identity-offset, because the pool has to stay below the payload: the block for
    low address 0x08000000 would land on the payload if the two indices were made
    to agree, and the payload is the one thing here that must not be written.

    Refuses rather than overlaps. A pool that reached the stub or the payload is a
    run whose lie would land on the code under test, and the symptom - a guest
    that dies somewhere else entirely - would look like a finding.
    """
    blocks = sorted({blk for _n, b, s in regions
                     for blk in range(b // STAGE2_BLOCK, (b + s - 1) // STAGE2_BLOCK + 1)})
    top = ZERO_MEM_POOL_BASE + len(blocks) * STAGE2_BLOCK
    if top > load_addr:
        die(f"{len(blocks)} blocks of low memory need"
            f" {len(blocks) * STAGE2_BLOCK:#x} bytes of pool, which runs"
            f" {ZERO_MEM_POOL_BASE:#x}..{top:#x} and reaches the load address"
            f" {load_addr:#x}; the instrument would be writing the payload it is"
            f" meant to be observing")
    return {blk: ZERO_MEM_POOL_BASE + i * STAGE2_BLOCK for i, blk in enumerate(blocks)}


def build_el3_stub(load_addr, out, zero_mem=False, plan=None):
    """Assemble `qemu-el3-stub.S` to the addresses the run will use.

    Every address the stub has to agree with is a `--defsym` and not a `.set` in
    the source: the payload's, because the stub's `eret` has to land on the same
    byte the `-device loader` line puts there; its own, because a stage-2 table
    entry is an absolute physical address and the stub is raw bytes at an address
    rather than a linked image; and the address whose stage-2 block is redirected,
    because the same number has to be read back out of the guest afterwards. A
    number written in two files is a number that can differ. The source's own
    header has the argument for existing at all.

    `zero_mem` adds the stage-2 half, which is assembled in or out rather than
    branched over, so a capture with it off is the same capture as before it
    existed. With it on, `plan` is the redirect table `l2_plan` worked out and it
    is written out as `s2_l2.inc` for the source to `.include`: the assembler has
    no way to read a memory map, and a table transcribed into the source by hand
    would be a second description of this machine that could drift from the one
    the firmware was built with.

    The object is *linked* and only then turned into raw bytes, and that is not
    decoration. A PC-relative reference to a global symbol is not something the
    assembler may resolve - the linker could still move it - so `as` emits a
    relocation and leaves the field zero. `objcopy -O binary` on the unlinked
    object therefore produces a stub whose `adr x2, s2_l1` loads the address of
    the `adr` itself: the stage-2 half was inert for two runs, with VTTBR_EL2
    pointing at the stub's own instructions, and the symptom was a boot that
    looked like a stage-2 permission fault. Linking the object is what makes the
    addresses in the binary the addresses the assembly names. Local symbols were
    resolved all along, which is why only the stage-2 half was affected and the
    plain EL3 stub worked.

    The symbol table the launcher reads offsets from stays the object's - a linked
    ELF's symbols are absolute and `stub_symbol`'s offsets would stop being
    offsets - so only the binary comes from the linked image.
    """
    src = os.path.join(REPO, "tools", "qemu-el3-stub.S")
    asm = shutil.which("aarch64-linux-gnu-as")
    ld = shutil.which("aarch64-linux-gnu-ld")
    objcopy = shutil.which("aarch64-linux-gnu-objcopy")
    if not asm or not ld or not objcopy:
        die(f"--el3-stub needs an AArch64 binutils ({os.path.basename(src)} is"
            f" assembled and linked with it); aarch64-linux-gnu-as, its ld or its"
            f" objcopy was not on PATH")
    obj = out + ".o"
    elf = out + ".elf"
    defsym = ["--defsym", f"PAYLOAD={load_addr + STUB_GAP:#x}",
              "--defsym", f"EL3_LOAD={load_addr:#x}"]
    include = []
    if zero_mem:
        if not plan:
            die("--el3-zero-mem needs the redirect table it is supposed to"
                " assemble; the caller has to have read a memory map first")
        inc = os.path.join(os.path.dirname(os.path.abspath(out)), "s2_l2.inc")
        with io.open(inc, "w", encoding="utf-8") as fh:
            fh.write(f"/* GENERATED by tools/qemu-panel-read.py - do not edit by hand.\n"
                     f" *\n"
                     f" * The stage-2 level 2 table for the low gigabyte: identity for\n"
                     f" * every 2 MB block except the {len(plan)} that the platform's own\n"
                     f" * MemoryMapLib.c declares below {LOW_MMIO_LIMIT:#x}, which point at\n"
                     f" * {ZERO_MEM_POOL_BASE:#x} + n * {STAGE2_BLOCK:#x} instead.\n"
                     f" */\n")
            for i in range(STAGE2_L2_ENTRIES):
                if i in plan:
                    fh.write(f"    .quad S2_BLOCK | {plan[i]:#x}"
                             f"    /* block {i}, {i * STAGE2_BLOCK:#x} - redirected */\n")
                else:
                    fh.write(f"    .quad S2_BLOCK | {i * STAGE2_BLOCK:#x}\n")
            fh.write("\n")
        include = ["-I", os.path.dirname(inc)]
        defsym += ["--defsym", "ZERO_MEM=1",
                   "--defsym", f"S2_MOVED_IPA={ZERO_MEM_IPA:#x}"]
    for argv in ([asm] + defsym + include + ["-o", obj, src],
                 [ld, "-Ttext", f"{load_addr:#x}", "--build-id=none",
                  "-o", elf, obj],
                 [objcopy, "-O", "binary", elf, out]):
        run = subprocess.run(argv, stderr=subprocess.PIPE)
        if run.returncode:
            die(f"{argv[0]} failed: {run.stderr.decode('utf-8', 'replace').strip()}")
    return out, obj


def stub_symbol(obj, name):
    """Where `name` lands inside the stub's own 64 KB, as an offset from its load.

    Asked of the object file rather than written down here. The stub's layout is
    the assembly's business and a number repeated in this file would be a second
    answer to where its tables are. For a relocatable object `readelf` prints the
    section offset, and `.text` is the one section `objcopy -O binary` lays down,
    so the section offset is the offset in the binary.
    """
    readelf = shutil.which("aarch64-linux-gnu-readelf")
    if not readelf:
        die("reading the stub's tables back needs aarch64-linux-gnu-readelf to"
            " find them; it was not on PATH")
    run = subprocess.run([readelf, "-sW", obj], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    for line in run.stdout.decode("utf-8", "replace").splitlines():
        field = line.split()
        if len(field) >= 8 and field[7] == name:
            return int(field[1], 16)
    die(f"the stub object has no symbol {name!r} - the stage-2 tables were renamed"
        f" or assembled out, and a header that claimed to report on them would be"
        f" reporting on nothing")


def stub_tables(mon, load_addr, obj, plan):
    """The stage-2 evidence, read out of the running guest.

    Returns `(l1_0, l2, backing, diag)` or None if the read failed, where `l2` is
    the 512 level 2 entries as the guest's memory holds them, `backing` is what the
    address `ZERO_MEM_IPA` is redirected to actually contains, and `diag` is
    `(vtcr, vttbr, hcr, scr)` as the guest itself read them back.

    This is the read-back that keeps the header honest. A capture that says the
    machine answered a register read has to be able to show the entries that
    answered it, and the only place those entries exist is the guest's memory - so
    they are read there, after the guest has been running, rather than written
    beside it. The whole table and not just the one entry: the claim is now about
    a set of regions, and a set is exactly what one sampled entry cannot support.
    The caller checks the entries against the plan it handed over, so the two ends
    of the instrument are compared rather than each being announced.

    `backing` is what the driver will see at the address it faults on; the whole
    point of pointing it at untouched RAM is that this is zero, and that is worth
    checking rather than asserting.

    The four registers are here because the first run of this instrument was
    reverted by the machine - the guest stopped on a translation fault for the
    payload's own first instruction - and nothing but a read-back distinguishes "the
    registers say stage 2 is on" from "the registers were written and ignored".
    """
    scratch = tempfile.mkdtemp(prefix="qemu-el3-tbl-")
    dump = os.path.join(scratch, "tables.bin")
    try:
        diag_off = stub_symbol(obj, "s2_diag")
        l1_off = stub_symbol(obj, "s2_l1")
        l2_off = stub_symbol(obj, "s2_l2")
        span = l2_off - l1_off + 8 * STAGE2_L2_ENTRIES
        mon.cmd(f'pmemsave {load_addr + l1_off:#x} {span:#x} "{dump}"', timeout=10.0)
        with open(dump, "rb") as fh:
            blob = fh.read()
        diag = os.path.join(scratch, "diag.bin")
        mon.cmd(f'pmemsave {load_addr + diag_off:#x} 32 "{diag}"', timeout=10.0)
        with open(diag, "rb") as fh:
            words = struct.unpack("<QQQQ", fh.read(32))
        blk = ZERO_MEM_IPA & ~(STAGE2_BLOCK - 1)
        backing_at = plan[(blk // STAGE2_BLOCK) & (STAGE2_L2_ENTRIES - 1)]
        backing_at += ZERO_MEM_IPA - blk
        far = os.path.join(scratch, "backing.bin")
        mon.cmd(f'pmemsave {backing_at:#x} 16 "{far}"', timeout=10.0)
        with open(far, "rb") as fh:
            backing = fh.read(16)
    except (OSError, SystemExit):
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    l2_at = l2_off - l1_off
    if len(blob) < l2_at + 8 * STAGE2_L2_ENTRIES or len(backing) < 16:
        return None
    return (struct.unpack_from("<Q", blob, 0)[0],
            struct.unpack_from(f"<{STAGE2_L2_ENTRIES}Q", blob, l2_at),
            struct.unpack_from("<QQ", backing, 0),
            words)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kernel", help="the raw BootShim + FD payload to launch")
    ap.add_argument("--socket", default="/tmp/qmon.sock")
    ap.add_argument("--machine", default="virt",
                    help="QEMU machine. Plain `virt`: an SMC at EL1 is then"
                         " architecturally undefined and QEMU raises it as EC 0x00,"
                         " which is not what the hardware does and stops the boot at"
                         " Apriori slot 2. `--el3-stub` is how that is answered.")
    ap.add_argument("--el3-stub", action="store_true",
                    help="run under an EL3 that exists, so an SMC returns an error"
                         " instead of faulting. Sets the machine to virt,secure=on;"
                         " see tools/qemu-el3-stub.S for what that buys and what it"
                         " costs in fidelity")
    ap.add_argument("--el3-zero-mem", action="store_true",
                    help="with --el3-stub: turn on stage 2 and give the guest a"
                         " 4 GB identity map in which every 2 MB block holding a"
                         " region the platform's own MemoryMapLib.c declares below"
                         " 0x40000000 is pointed at its own 2 MB of untouched RAM"
                         " below the payload, so that a driver reading a register"
                         " virt does not decode gets zero back instead of an abort."
                         " The payload is not touched; the guest's own page tables"
                         " are not touched; an access at or above 4 GB is a"
                         " translation fault that only this instrument produces")
    ap.add_argument("--memory", type=int, default=4096, help="MB")
    ap.add_argument("--load-addr", default=DEFAULT_LOAD_ADDR, type=lambda v: int(v, 0))
    ap.add_argument("--extra", action="append", default=[], metavar="ARG",
                    help="one more QEMU argument (repeatable)")
    ap.add_argument("--mu", default=DEFAULT_MU)
    ap.add_argument("--platform", default=None,
                    help="the platform package directory, if not"
                         " Platforms/Xiaomi/gauguinPkg")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--min-margin", type=float, default=1.5)
    ap.add_argument("--out", default=os.path.join(REPO, "work", "out", "qemu-panel.txt"))
    ap.add_argument("--quiet", action="store_true",
                    help="report only the stream, not each sample's progress")
    args = ap.parse_args()

    if args.kernel and not os.path.exists(args.kernel):
        die(f"no payload at {args.kernel} - decompress the boot image's kernel first"
            f" (the payload is a gzip member followed by the device tree, so it needs"
            f" zlib.decompressobj(31) and not gzip.decompress)")
    if os.path.exists(args.socket):
        os.unlink(args.socket)

    # The stub needs secure=on and the payload needs an address, so the two are
    # decided together and neither is a default a caller can half-set. The
    # platform comes first here because the stage-2 instrument is built out of
    # the platform's own memory map.
    pkg, dsc = platform_paths(args.mu, args.platform)
    stub, stub_obj, plan = None, None, None
    if args.el3_stub:
        if args.machine != "virt":
            die(f"--el3-stub and --machine {args.machine} disagree about whether the"
                f" guest has an EL3; the stub replaces the answer rather than adding"
                f" to it")
        args.machine = "virt,secure=on"
        if args.el3_zero_mem:
            # What is redirected is not one address but every region this board's
            # map declares below LOW_MMIO_LIMIT, because the firmware's use of its
            # SoC is not one read: the same run that stops on TCSR at slot 2 has
            # PSHOLD two instructions behind it, and the drivers after that have
            # the rest. Modelling one address at a time would be a boot that dies
            # somewhere new each run, which is indistinguishable from progress.
            regions = low_regions(pkg)
            plan = l2_plan(regions, args.load_addr)
            print(f"stage 2  {len(regions)} region(s) below {LOW_MMIO_LIMIT:#x} in"
                  f" {os.path.basename(pkg)}, {len(plan)} 2 MB block(s) redirected to"
                  f" {ZERO_MEM_POOL_BASE:#x}..{ZERO_MEM_POOL_BASE + len(plan) * STAGE2_BLOCK:#x}")
            # Stage 2 is only in force if the machine gave the CPU an EL2 at all.
            # QEMU's virt machine turns EL2 on for the CPU it creates as part of
            # `virtualization=on`, and without it the stub's `msr hcr_el2` is
            # accepted - it comes from EL3, so the access check passes - and then
            # ignored, because QEMU's translation path reads HCR_EL2 through
            # `arm_hcr_el2_eff()` and that returns zero when EL2 is absent. The
            # first run of this instrument showed exactly that: the tables in the
            # guest, the entry pointing at the RAM, and the same external abort,
            # because nothing consulted them.
            #
            # GICv2 is pinned to what `secure=on` chose on its own. Left to itself
            # `virtualization=on` can select GICv3, which moves the distributor,
            # adds a redistributor, and changes the interrupt topology the firmware
            # under test is looking at - one variable too many in a log whose whole
            # value is that the only difference from the previous one is a flag.
            args.machine += ",virtualization=on,gic-version=2"
        stub, stub_obj = build_el3_stub(
            args.load_addr,
            os.path.join(tempfile.mkdtemp(prefix="qemu-el3-"), "el3.bin"),
            args.el3_zero_mem, plan)
    elif args.el3_zero_mem:
        die("--el3-zero-mem is the stub's behaviour and means nothing without"
            " --el3-stub; an abort it did not answer would still stop the boot")

    pt = load_panel_text()
    geo = pt.load_geometry(args.mu, dsc)
    font = pt.load_font(args.mu)
    name, base, length = fb_region(pkg)
    need = geo["width"] * geo["height"] * (geo["bpp"] // 8)
    if length < need:
        die(f"{name} is {length:#x} bytes and the console draws {need:#x} - the"
            f" platform's region is too small for its own panel, which is a source"
            f" disagreement and not something to sample around")

    print(f"panel   {geo['width']}x{geo['height']} {geo['bpp']}bpp, cell"
          f" {geo['cell_w']}x{geo['cell_h']}, {geo['columns']}x{geo['rows']} cells")
    print(f"region  {name} {base:#x}..{base + length:#x}, of which the console draws"
          f" {need:#x}")

    proc = None
    if args.kernel:
        proc = launch(args.kernel, args.socket, args.machine, args.memory,
                      args.load_addr, args.extra, stub)
        time.sleep(0.5)
    mon = Monitor(args.socket)

    lines, screens, gaps = [], 0, []
    tables = None
    worst, weak = 60.0, 0
    scratch = tempfile.mkdtemp(prefix="qemu-panel-")
    dump = os.path.join(scratch, "fb.bin")
    # Only the drawn part is dumped: the region is 0x2400000 here and the console
    # touches 0x9E3400 of it, so the other 28 MB would be sampled and decoded as
    # black forty times a second.
    sample = f'pmemsave {base:#x} {need:#x} "{dump}"'
    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            tick = time.time()
            mon.cmd(sample, timeout=max(5.0, args.interval * 4))
            try:
                got, report, _t = pt.fb_lines(dump, geo, font, args.min_margin)
            except SystemExit as exc:            # a short dump: say so, keep going
                print(f"  {tick - t0:6.2f}s  dump not usable: {exc}")
                continue
            for rep in report:
                worst = min(worst, rep["worst"])
                weak += len(rep["weak"])
            k = pt.fb_overlap(lines, got)
            if screens and got and k == 0:
                gaps.append((tick - t0, len(got)))
            new = got[k:]
            if new and not args.quiet:
                for line in new:
                    print(f"  {tick - t0:6.2f}s  {line}")
            lines += new
            if got:
                screens += 1
            time.sleep(max(0.0, args.interval - (time.time() - tick)))
    except KeyboardInterrupt:
        pass
    finally:
        # The stage-2 tables are in the guest's own memory, so they are read while
        # it is still running and before anything is torn down. A header that says
        # an address was answered read as zero has to be able to show the entry
        # that answered it.
        if stub and args.el3_zero_mem:
            tables = stub_tables(mon, args.load_addr, stub_obj, plan)
        shutil.rmtree(scratch, ignore_errors=True)
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    digest = hashlib.sha256(open(args.kernel, "rb").read()).hexdigest() if args.kernel else None
    with io.open(args.out, "w", encoding="utf-8") as fh:
        fh.write(f"# qemu-panel-read.py - the console off a running firmware's own memory\n")
        fh.write(f"# sampled {screens} screens over {time.time() - t0:.1f}s at"
                 f" {args.interval}s intervals\n")
        if args.kernel:
            fh.write(f"# payload {args.kernel}\n#         sha256 {digest}\n")
            fh.write(f"# machine {args.machine}, -cpu max, -m {args.memory},"
                     f" load {args.load_addr:#x}\n")
            if stub:
                where = os.path.relpath(stub, REPO) if stub.startswith(REPO) else stub
                fh.write(f"# el3 stub {where}, sha256"
                         f" {hashlib.sha256(open(stub, 'rb').read()).hexdigest()},"
                         f" payload at {args.load_addr + STUB_GAP:#x} - an SMC in this"
                         f" log returned an error rather than faulting\n")
                if not args.el3_zero_mem:
                    fh.write(f"# el3 zero-mem off - a physical address virt does not"
                             f" decode stopped this run where the guest's own"
                             f" exception handler printed it\n")
                elif tables is None:
                    fh.write(f"# el3 zero-mem on, but the stage-2 tables could not be"
                             f" read back out of the guest; nothing is claimed about"
                             f" them and the run below is only as good as its own"
                             f" evidence\n")
                else:
                    l1_0, l2, backing, diag = tables
                    l2_addr = args.load_addr + stub_symbol(stub_obj, "s2_l2")
                    # Every entry is checked against the plan this end handed over,
                    # and every entry that is not in the plan is checked for being
                    # the identity. Not the one address this end named: what the
                    # instrument now claims is a *set* of regions - the platform's
                    # own - and one sampled entry cannot support a claim about a set.
                    # The two ends of the instrument are compared here rather than
                    # each announcing itself.
                    wrong = [i for i, pa in sorted(plan.items())
                             if (l2[i] & 0x3) != 0x1
                             or (l2[i] & ~(STAGE2_BLOCK - 1)) != pa]
                    ident = sum(1 for i, e in enumerate(l2)
                                if i not in plan and (e & 0x3) == 0x1
                                and (e & ~(STAGE2_BLOCK - 1)) == i * STAGE2_BLOCK)
                    want_ident = STAGE2_L2_ENTRIES - len(plan)
                    ok = ((l1_0 & 0x3) == 0x3 and (l1_0 & ~0xFFF) == l2_addr
                          and not wrong and ident == want_ident)
                    fh.write(f"# el3 zero-mem on: stage 2 gives the guest a 4 GB"
                             f" identity map, with the {len(plan)} 2 MB block(s)"
                             f" holding this platform's declared regions below"
                             f" {LOW_MMIO_LIMIT:#x} redirected to"
                             f" {ZERO_MEM_POOL_BASE:#x}.."
                             f"{ZERO_MEM_POOL_BASE + len(plan) * STAGE2_BLOCK:#x},"
                             f" one block each so that a register written and read"
                             f" back is the value that was written. No byte of the"
                             f" payload was changed\n")
                    fh.write(f"#   read back out of the guest: L1[0] = {l1_0:#x},"
                             f" {len(plan) - len(wrong)}/{len(plan)} redirected"
                             f" entries and {ident}/{want_ident} identity entries"
                             f" are what this header describes -"
                             f" {'the walk is the one this header describes' if ok else 'THE WALK IS NOT THE ONE THIS HEADER DESCRIBES'}\n")
                    if wrong:
                        i = wrong[0]
                        fh.write(f"#   L2[{i}] = {l2[i]:#x}, but block"
                                 f" {i * STAGE2_BLOCK:#x} is declared and the plan"
                                 f" put it at {plan[i]:#x}\n")
                    if not ok and (l1_0 & ~0xFFF) != l2_addr:
                        fh.write(f"#   L1[0] should name the L2 table at"
                                 f" {l2_addr:#x} as a table descriptor"
                                 f" ({l2_addr + 0x3:#x})\n")
                    fh.write(f"#   so {ZERO_MEM_IPA:#x} reads what"
                             f" {ZERO_MEM_IPA - (ZERO_MEM_IPA & ~(STAGE2_BLOCK - 1)):#x}"
                             f" bytes into a redirected block, which holds"
                             f" {backing[0]:#010x} {backing[1]:#010x}\n")
                    fh.write(f"#   as the guest read them back after writing them:"
                             f" VTCR_EL2={diag[0]:#x} VTTBR_EL2={diag[1]:#x}"
                             f" HCR_EL2={diag[2]:#x} SCR_EL3={diag[3]:#x}\n")
                    fh.write(f"#   an access at or above 4 GB is a stage-2 translation"
                             f" fault here; the device it is standing in for has no"
                             f" such input limit\n")
        fh.write(f"# region  {name} at {base:#x}, dumping {need:#x} bytes\n")
        fh.write(f"# {len(lines)} rows, weakest margin {worst:.2f} of 60 sub-blocks,"
                 f" {weak} characters under {args.min_margin}\n")
        if gaps:
            fh.write(f"# {len(gaps)} GAP(S) - samples that shared no line with the"
                     f" stream. Text between them was printed and lost; the stream"
                     f" below is not continuous across these points.\n")
            for t, n in gaps:
                fh.write(f"#   at {t:.2f}s, {n} rows, none shared\n")
        for i, line in enumerate(lines):
            fh.write(f"{i:4d} |{line}|\n")

    print(f"\n{len(lines)} rows from {screens} screens;"
          f" weakest margin {worst:.2f}, {weak} characters under {args.min_margin}")
    if tables:
        _l1_0, l2, backing, diag = tables
        wrong = [i for i, pa in sorted(plan.items())
                 if (l2[i] & 0x3) != 0x1
                 or (l2[i] & ~(STAGE2_BLOCK - 1)) != pa]
        print(f"stage 2 redirected {len(plan) - len(wrong)}/{len(plan)} of the"
              f" platform's low regions to RAM of their own, one 2 MB block each;"
              f" {ZERO_MEM_IPA:#x} reads {backing[0]:#x}")
        print(f"  as the guest reads them back: VTCR_EL2={diag[0]:#x}"
              f" VTTBR_EL2={diag[1]:#x} HCR_EL2={diag[2]:#x} SCR_EL3={diag[3]:#x}")
    if gaps:
        print(f"{len(gaps)} sample(s) shared no line with the stream - see the header"
              f" of {os.path.relpath(args.out, REPO)}: the log is not continuous there")
    print(f"wrote {os.path.relpath(args.out, REPO)}")
    return 1 if (gaps or weak) else 0


if __name__ == "__main__":
    sys.exit(main())
