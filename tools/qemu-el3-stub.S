/*
 * An EL3 that exists, so an SMC does not fault - and, optionally, a stage 2 that
 * gives the guest back a physical region QEMU does not decode.
 *
 * QEMU's `-M virt` without `secure=on` has no EL3, so `smc #0` executed at EL1
 * is architecturally undefined and QEMU raises it as a synchronous exception with
 * EC 0x00 ("unknown reason"). That is not what a Qualcomm SoC does: there, SMC
 * from EL1 traps to EL3 and either reaches the secure world or returns an error.
 * The firmware in this tree is full of SMC - EnvDxe, ScmDxe, TzDxe and the whole
 * Qualcomm block call it - and under QEMU the first one it takes kills the boot
 * at Apriori slot 2, so nothing past it can be observed.
 *
 * The fix is not to pretend the secure world exists. It is to give QEMU the EL3
 * the architecture assumes and answer every SMC honestly: "this environment has
 * no secure service", which is true, and is a status the callers already handle
 * because a device without a secure world is a case their code carries.
 *
 * The second thing is about memory, and it is not about the secure world at all.
 * It is that this firmware talks to its SoC. EnvDxe, at Apriori slot 2, reads a
 * 64-bit value from physical 0x01FD4000: it loads 0x01F00000 from its own literal
 * pool at RVA 0xC7D0, adds (0x0D4000, 0x0D4004) from RVA 0xC7F4, and dereferences
 * the sum - `ldr w8,[x13]` at 0x9CBBD50C with x13 = 0x1FD4000, which is exactly
 * what FAR holds when the guest's handler prints the abort. Those three constants
 * are in the image, not in uninitialised RAM, so this is a deliberate read of a
 * low physical region. 0x01FD4000 is inside `TCSR_TCSR_REGS`, 0x01FC0000 for
 * 0x40000, which is a region this board's own memory map declares; the same is
 * true of the second address the guest stops on, 0x0C264000, which is `PSHOLD` in
 * that same map. Neither abort is a missing region. Both are EC 0x25 with a DFSC
 * of 0b010000 - a synchronous *external* abort, which is what a bus says when it
 * has nothing at an address, and not a translation fault, which is what it would
 * say if the region were unmapped.
 *
 * Under `virt` the guest cannot reach them. The machine's own address spaces say
 * so: `virt.flash0` is mapped into `cpu-secure-memory-0` alone, so the Non-secure
 * `system` space has no mapping at all below 0x04000000, and no `-drive if=pflash`
 * changes that - placement is decided by slot index in `virt_flash_map` and not by
 * the device's `secure` property, so a 64 MB bank at physical zero is invisible to
 * the guest rather than useful to it. Every register the firmware touches below
 * 0x40000000 therefore aborts, the guest's own handler prints a dump and asserts,
 * and the dispatcher never reaches the end of its Apriori batch - which is why the
 * `P2 FREE largest=`, `P2 RETRY` and `KEY` rows, printed after the batch, cannot
 * be read from a QEMU run.
 *
 * ZERO_MEM gives those regions back the way a machine with a hypervisor would:
 * HCR_EL2's stage 2 is enabled and identity-maps the guest's 4 GB, except that
 * each 2 MB block containing a region this board's memory map declares below
 * 0x40000000 is pointed at its own 2 MB of untouched RAM below the payload. The
 * reads then succeed and return zero, because that RAM is untouched - which is the
 * one value EnvDxe's code wants: it builds a pointer as two 32-bit halves and
 * guards the result with `cbz`, so zero takes the driver's "nothing there" path
 * instead of a dereference. Writes land in that RAM and are read back by the same
 * guest as what it wrote, which is what a register file does. Nothing in the
 * payload is modified; the payload's instructions are untouched; the guest's own
 * page tables are untouched.
 *
 * The table is not written here. It is generated - by the launcher, out of the
 * platform's own `MemoryMapLib.c` - and `.include`d below, so that the set of
 * redirects cannot disagree with the set of regions the firmware under test
 * believes it has. That is the whole argument for this being a *model* of a
 * machine with no peripherals rather than a patch aimed at one address: the
 * declared set is the fixed point, and an abort in a later run is then a region
 * the firmware touches that its own map does not declare, which is a fact worth
 * seeing rather than hiding.
 *
 * Not by SCR_EL3.EA, and that is a measurement rather than a preference. Routing
 * the abort to EL3 and rewriting the faulting instruction to `movz Rt,#0` was the
 * first attempt and it is dead: QEMU does not route a synchronous data abort to
 * EL3 through EA. `-d int` says so in one line - "Taking exception 4 [Data Abort]
 * ... from EL1 to EL1" with `mov x0,#0x509` in SCR_EL3 and the `smc` beside it
 * arriving at EL3 normally. Whether that is a QEMU divergence from the
 * architecture or the architecture's own rule for a synchronous abort is a
 * question for another day; what matters here is that the emulation has to be
 * built where QEMU will honour it, which is stage 2.
 *
 * What it costs in fidelity, stated so it is not discovered later: the input
 * address is 32 bits, so an access at or above 4 GB is a level 1 translation
 * fault at stage 2 that the firmware would not see on the device. PCIe's ECAM at
 * 0x4010000000 and its high MMIO window at 0x8000000000 are both above that, so
 * a PCIe probe taken far enough to touch them stops here. Widening it means a
 * level 0 table and T0SZ 16 with SL0 2, and nothing has needed it yet. Second, a
 * register that answers zero is not a register that answers its real value: a
 * driver that reads a version or a status and branches on it will take the zero
 * branch, and no run under this instrument can say what the device would have
 * done there.
 *
 * It buys this: the dispatcher runs past slot 2 instead of stopping there. No
 * unseeded run under this instrument gets past slot 2 at all, so the only record
 * of what the batch does is a photograph of a phone; a seeded run carries 18 rows
 * of it. What those rows show is that the batch is where this stops and not where
 * it resumes: the highest progress any seeded run has reached is 17 starts
 * (`K 18 Ss 17/69`), sixteen of the eighteen ticks are `Ss`, DALSYS's is `SO` and
 * CmdDbDxe's is `SU`, and the index and `mP2Started` never diverge in any capture.
 * The run still dies *inside* the batch, on Apriori 19, so the digest rows
 * `DxeCore` prints once the batch is done - the `P2 SEQ` / `P2 WHY` / `P2 ERR` /
 * `P2 RETRY` measurements the P2 diagnosis turns on, and which until now could
 * only be had by photographing a phone - are in no host-side capture yet.
 * Reaching the place a device-side run already reached is not the same as getting
 * past it, and this file is where that distinction is easy to lose.
 *
 * And it does not buy that on its own, which is the correction this file carries.
 * Giving the machine back its registers is not enough to get past EnvDxe, because
 * EnvDxe does not stop on a register it cannot reach: it stops on a *value* it
 * reads from one, the address of the SMEM target-info structure, which on a real
 * board XBL and the secure world wrote there before the firmware ran. Zero is not
 * a missing register to that code, it is a failed lookup, and it asserts. So a
 * ZERO_MEM run reaches the same assert as a plain one, and the digest rows stay
 * unwritten. SEED_SMEM is what gets past it, by fabricating the three words the
 * driver's own parse reads and the one word it then reads out of SMEM - and a
 * run that carries those rows carries them because of values this file invented.
 * Every capture made under this instrument records which of the two it was,
 * because a reader who takes an unseeded run for a seeded one, or the reverse,
 * will read the log backwards.
 *
 * It is loaded at EL3_LOAD and branches to PAYLOAD, which is where the real
 * payload would have been loaded. Both are build-time symbols so that the stub
 * does not quietly disagree with the run it is part of, and the launcher reads
 * the tables back out of the guest afterwards, so the two ends cannot drift.
 *
 *   aarch64-linux-gnu-as --defsym PAYLOAD=0x48010000 --defsym EL3_LOAD=0x48000000 \
 *       --defsym ZERO_MEM=1 --defsym SEED_SMEM=1 --defsym SEED_SMEM_BLOCK=0x40c00000 \
 *       --defsym SEED_SMEM_IPA=0x01FD4000 --defsym SEED_SMEM_BASE=0x80900000 \
 *       --defsym SEED_SMEM_SIZE=0x200000 \
 *       -I <dir with s2_l2.inc> -o stub.o stub.S
 *   aarch64-linux-gnu-objcopy -O binary stub.o stub.bin
 *
 * `tools/qemu-panel-read.py --el3-stub --el3-zero-mem --el3-seed-smem` does
 * exactly this and passes the addresses through, so the numbers have one home.
 */

    /*
     * The address this stub is loaded at has to be known to the assembler, not
     * because the code reads it - everything here is PC-relative - but because a
     * stage 2 table entry is an absolute physical address and the L1 table has to
     * name where the L2 table is. Passed in rather than written down twice.
     */
    .ifndef EL3_LOAD
    .equ  EL3_LOAD, 0x48000000
    .endif

    /*
     * The stage-2 emulation is written in the source and selected at assembly
     * time, so that a capture with it off is the same capture as before it
     * existed and the difference between two logs is one flag.
     */
    .ifndef ZERO_MEM
    .equ  ZERO_MEM, 0
    .endif

    /*
     * SEED_SMEM puts one fabricated value into the redirected block that stands
     * in for SMEM, and it is the one thing in this file that is not a model of
     * anything: it is a lie, told once, so that a run can get past the driver
     * that would otherwise stop it.
     *
     * The case for telling it. EnvDxe, at Apriori slot 2, reads a 64-bit value
     * from 0x01FD4000 - SMEM + 0x0D4000 - and treats it as the address of the
     * SMEM target-info structure: `ldr w8,[x13]; ldr w9,[x14]; bfi x8,x9,#32,#32`
     * at RVA 0x950C..0x9518, then `cbz x8, <assert>`. Zero is not the value it
     * wants; zero is the failure it prints. On the device the word is written by
     * XBL and TZ before the firmware runs, and no stage-2 table can supply it,
     * because a table moves addresses and this is a value. So the guest stops at
     * slot 2 in every configuration of this instrument, the dispatcher never
     * finishes its Apriori batch, and the P2 digest - which DxeCore prints after
     * `CoreDispatcher ()` returns - is never written. Those rows are the whole
     * point of the instrument, so the alternative to seeding is not a purer run;
     * it is no run.
     *
     * What is fabricated, exactly. One qword at the redirected offset for
     * 0x01FD4000 (the pointer), three qwords at the address it names (the
     * structure the driver parses field by field), and one word inside SMEM
     * itself, at SMEM + 0xC0. Nothing else. The payload's bytes are not touched -
     * this is the machine's RAM, arranged before the guest starts - so the
     * property that the instrument changes no byte of what it watches is kept.
     *
     * The fourth write is not part of the container and is not the same kind of
     * lie. The container is a thing this file builds and the driver reads; SMEM is
     * a region that exists on the device whether or not this file does, and the
     * word at +0xC0 is one EnvDxe reads out of it directly - `ldr w9,[x8,#192];
     * cmp w9,#1; b.ne <assert>` at RVA 0x823C, which is `smem.c +671` "SMEM is
     * not initialized by Boot." On the device XBL leaves 1 there before the
     * firmware starts, so writing 1 is restoring a value rather than inventing
     * one; but it is still this instrument writing into a region the firmware
     * owns, which the other three writes are not, and a run that stops on the
     * word at SMEM + 0xC0 is a run that stopped on this line.
     *
     * The three fields are not invented, and that is the difference between this
     * and a run that simply makes things up. The driver stores the structure's
     * first qword, its `+8` and its `+16` into its own `.data + 0xA40` and then
     * reads them back through three accessors: `[+8]` is the SMEM base, `[+4]` -
     * the *high* half of the first qword - is the size, `[+0]` is checked against
     * the magic, and the low half of `[+16]` is a count that falls back to 498
     * when it is zero (RVA 0x946c, 0x947c and 0x948c read 0xA48, 0xA44 and 0xA50;
     * RVA 0x9524-0x9554 writes them). So the base and size seeded here are this
     * board's own SMEM region - 0x80900000 for 0x00200000, from
     * `device/config/uefiplat.cfg` by way of the platform's `MemoryMapLib.c`, and
     * passed in rather than written down - and the magic is taken from the
     * image's own compare (`mov w13,#0x4953; movk w13,#0x4949,lsl #16` at RVA
     * 0x9530). Only the existence of the structure is invented; its contents are
     * the board's declaration.
     *
     * What it costs in fidelity, and this is the part a reader has to carry. A
     * run with SEED_SMEM on can say EnvDxe returned, and it cannot say what the
     * device would have done with the real structure. Two of the fields are the
     * board's own values carried by an invented container, which is a smaller
     * lie than an invented value; the third is left zero because nothing in the
     * driver's image says what belongs there, and 498 is only the default it
     * falls back to. If a later driver branches on that third field, this
     * seeding is where the wrong answer would come from, and it should be the
     * first thing suspected.
     *
     * The larger cost is not any one field: it is that SMEM is now a region
     * holding four words this file wrote and zeros everywhere else, and drivers
     * allocate from it. The first seeded run that got past EnvDxe carried
     * `smem_get_addr: SMEM get addr failed! smem_type=402` and `smem_alloc: SMEM
     * allocation failed! smem_type=404, buf_size=8192K` in its own log, which is
     * the instrument talking and not the firmware. Any driver failure downstream
     * of those lines has to be read with that in front of it.
     *
     * The addresses are `--defsym`s for the same reason every other address here
     * is: SEED_SMEM_BLOCK is the pool address `l2_plan` gave the block holding
     * 0x01FD4000, and the launcher computes both out of the platform's own
     * memory map so that neither end can drift from the run it is part of.
     */
    .ifndef SEED_SMEM
    .equ  SEED_SMEM, 0
    .endif

    .ifndef SEED_SMEM_IPA
    .equ  SEED_SMEM_IPA, 0x01FD4000
    .endif

    /*
     * How far past the pointer the structure goes: 16 bytes, inside the *same*
     * 4 KB page, and that is the whole reason for the number.
     *
     * The driver reads the pointer at 0x01FD4000 and then dereferences it. If the
     * structure were anywhere else - the next page, or a block of its own - the
     * guest would have to have that address mapped in its *own* stage-1 tables,
     * and whether it does is a property of the firmware's MMU setup that this
     * instrument would be guessing at. Sixteen bytes past the pointer page's base
     * removes the question: the page is the one the driver already reads
     * successfully in every run, and the redirect maps the whole 2 MB block, so
     * the byte the guest fetches is the byte written below.
     */
    .equ  SEED_SMEM_OFF, 0x10

    /* The magic EnvDxe compares the structure's first word against, from RVA 0x9530. */
    .equ  SEED_SMEM_MAGIC, 0x49494953

    /*
     * The board's own SMEM region, which is what the two fields of the fabricated
     * structure have to hold: the driver reads `+8` as the base and the high half
     * of `+0` as the size, and asserts `smem.c +659` when the base is zero. These
     * are `--defsym`s - the launcher reads them out of `uefiplat.cfg` through the
     * platform's `MemoryMapLib.c` - so the values are this board's declaration
     * rather than this file's guess. The defaults are that declaration, kept so
     * the file assembles on its own; a run that used them without the launcher's
     * would be a run whose numbers nothing checked.
     */
    .ifndef SEED_SMEM_BASE
    .equ  SEED_SMEM_BASE, 0x80900000
    .endif

    .ifndef SEED_SMEM_SIZE
    .equ  SEED_SMEM_SIZE, 0x00200000
    .endif

    /*
     * The word EnvDxe reads out of SMEM itself, at SMEM + 0xC0, and compares
     * against 1 - `ldr w9,[x8,#192]; cmp w9,#1; b.ne <assert>` at RVA 0x823C,
     * which is `smem.c +671` "SMEM is not initialized by Boot." Unlike the three
     * fields above, this one is not read out of the structure this file builds;
     * it is read out of the region the structure *names*. So the write below
     * lands at SEED_SMEM_BASE + 0xC0, and what it overwrites is the first word of
     * the header XBL would have written on the device.
     */
    .equ  SEED_SMEM_FLAG_OFF, 0xC0
    .equ  SEED_SMEM_FLAG, 1

    /*
     * Where the two translation tables go, inside the stub's own 64 KB. `.org`
     * places them and the L1 entry below names the second one through the same
     * pair of numbers, so the table it points at is the table that is there.
     *
     * 0x2000 and 0x3000 and not 0x1000: VTTBR_EL2's alignment is 4 KB and the
     * walk reads 512 entries of 8 bytes, so a 4 KB table cannot start at 0x1000
     * without running into whatever is at 0x2000. The pair costs 4 KB of the
     * 64 KB and there is nothing else in it.
     */
    .equ  S2_L1_OFF, 0x2000
    .equ  S2_L2_OFF, 0x3000

    /*
     * Four words of read-back, so that a run can say what the walk was actually
     * given instead of what this source meant to give it. The registers that
     * decide the walk - VTCR_EL2 and VTTBR_EL2 for the tables, HCR_EL2 for
     * whether stage 2 is on at all, SCR_EL3 for the state this stub was entered
     * in - are architectural and should read back exactly as written, and the
     * first run of this instrument hung the guest on a translation fault with
     * every one of them apparently correct. A record of them, read after they
     * were written and dumped by the launcher alongside the tables, is the
     * difference between narrowing that and guessing at it.
     */
    .equ  S2_DIAG_OFF, 0x1F00

    /*
     * And a second record, for the seed. Next to the first so that the two are
     * read by the same `--read-back` pass and a capture cannot carry one without
     * the other.
     */
    .equ  SEED_DIAG_OFF, 0x1F40

    /*
     * A stage-2 block descriptor, and the layout is not the stage-1 one, which is
     * the trap: S2AP is bits [7:6] rather than an AP field at [7:6] with the same
     * meaning by accident, MemAttr is four bits at [5:2], shareability is [9:8],
     * AF is [10], and bits [1:0] are 0b01 for a block. QEMU's `target/arm/ptw.c`
     * extracts exactly those - `ap = extract32(attrs, 6, 2)`,
     * `cacheattrs.attrs = extract32(attrs, 2, 4)`,
     * `cacheattrs.shareability = extract32(attrs, 8, 2)`, AF tested at `1 << 10`
     * - and it is the decoder for this run, so it is the authority rather than a
     * remembered table.
     *
     * 0x7FD is therefore: block, MemAttr 0b1111 (Normal, write-back, non-
     * transient both sides), S2AP 0b11 (read and write from EL1), shareability
     * 0b11 (inner shareable), AF set. XN at [54:53] stays zero, because the guest
     * has to be able to execute from the identity-mapped RAM it was loaded into.
     */
    .equ  S2_BLOCK, 0x7FD

    /*
     * The size of one stage-2 block, named here because the seeding block below
     * has to reduce an IPA to its offset within its block, and `tools/
     * qemu-panel-read.py` reduces the same number the same way when it works out
     * which pool block that IPA's 2 MB got. Two places, one constant, and the
     * launcher asserts they agree by passing the block's base rather than the
     * offset - the addition is this file's.
     */
    .equ  S2_MOVED_BLOCK, 0x200000

    /*
     * The address the guest reads and cannot get. Kept as a symbol because the
     * launcher's read-back checks *that* address - the one EnvDxe faults on, the
     * one with an image constant behind it - against the entry the table gave it,
     * so the two ends name the same spot without either repeating the other's
     * arithmetic.
     */
    .ifndef S2_MOVED_IPA
    .equ  S2_MOVED_IPA, 0x01FD4000
    .endif

    .section .text
    .globl _start

_start:
    /*
     * The vector table has to be at a 2 KB boundary before anything can trap.
     *
     * `adr` and not `ldr x0, =vectors`: the table has to be found without a
     * relocation, because this is raw bytes loaded at an address QEMU is told
     * rather than a linked image, and a symbol whose value nothing has fixed up
     * assembles to a literal zero - which would put the vectors at 0x800 of
     * whatever the address space happens to hold and turn every trap into a jump
     * into nowhere. `adr` reaches ±1 MB, and the table is 0x800 away.
     */
    adr   x0, vectors
    msr   vbar_el3, x0
    isb

    /*
     * SCR_EL3. NS=1 so the lower exception levels are Non-secure, RW=1 so EL2 is
     * AArch64, HCE=1 so `hvc` traps rather than being undefined, and SMD=0 - the
     * whole point - so that `smc` from EL1 traps here instead of faulting.
     *
     * EA stays clear. Setting it was tried and it changes nothing about a data
     * abort in QEMU; the header has that measurement. Leaving it clear means the
     * guest's own exception handler still prints the dump it prints today, which
     * is the one reading that says which address is missing.
     */
    mov   x0, #0x501
    msr   scr_el3, x0
    isb

    /*
     * HCR_EL2.RW=1: EL1 is AArch64. Everything else at its reset value, and
     * stage 2 off - the guest's addresses are its own.
     */
    mov   x0, #1
    lsl   x0, x0, #31
.if ZERO_MEM
    /*
     * ... except when ZERO_MEM asks for stage 2, which is HCR_EL2.VM.
     *
     * VTCR_EL2 describes the input the tables are indexed by. T0SZ 32 is a 32-bit
     * input - the 4 GB the fidelity note above is about - and SL0 1 is the start
     * level. That last one is not obvious: QEMU's `check_s2_mmu_setup` computes
     * `startlevel = 2 - sl0` for the 4 KB granule and then checks the result
     * against the input size, so SL0 1 means the walk begins at level 1 and one
     * L1 entry is a gigabyte. T0SZ 32 with start level 1 passes that check
     * (levels 2, minimum 31 bits, maximum 43, and 32 is inside it), which is why
     * the L1 table below has four entries rather than a level 0 table above it.
     *
     * The rest is ordinary: inner and outer write-back write-allocate, inner
     * shareable, 4 KB granule (TG0 0b00), 40-bit output (PS 0b010, which is what
     * a cortex-a53 has), and the RES1 bit at 31.
     *
     * VTTBR_EL2 names the L1 table. VMID 0 and CnP 0 are its reset values and
     * bits [63:48] are left zero, which is what makes this the one VM.
     */
    orr   x0, x0, #1
    ldr   x1, =0x80023D60
    msr   vtcr_el2, x1
    adr   x2, s2_l1
    msr   vttbr_el2, x2
    isb
.endif
    msr   hcr_el2, x0
    isb

.if ZERO_MEM
    /*
     * Read back the four registers that decide the walk, into `s2_diag` where the
     * launcher dumps them. Written here and after both `isb`s rather than beside
     * the writes, because a register that is written and silently ignored looks
     * exactly like a register that was written and obeyed until someone reads it
     * back - which is the state this instrument was in before this block existed.
     */
    mrs   x3, vtcr_el2
    mrs   x4, vttbr_el2
    mrs   x5, hcr_el2
    mrs   x6, scr_el3
    adr   x7, s2_diag
    stp   x3, x4, [x7]
    stp   x5, x6, [x7, #16]
.endif

.if SEED_SMEM
    /*
     * The lie, written before the guest exists. Four stores into the container:
     * the pool block standing in for the 2 MB holding 0x01FD4000 gets the
     * pointer, and the pointer's target gets the three words the driver reads. A
     * fifth store, further down, is not into the container at all. The header
     * above has the argument for doing this at all and the list of what it costs.
     *
     * Both addresses are *pool* addresses and not the guest's. That is the part
     * that is easy to get wrong and was got wrong first: at EL3 there is no stage
     * 2, because stage 2 applies to accesses made *from* EL1 and EL0 - so a store
     * to the raw IPA 0x01FD4010 from here is a physical store to 0x01FD4010,
     * which under `virt` is inside the secure flash bank and not RAM at all. The
     * value written has to be the IPA, because that is what the guest will
     * dereference; the place it is written has to be the pool block, because that
     * is where the machine keeps the bytes the guest's own stage-2 redirect will
     * fetch.
     *
     * `str x3, [x0]` is one 64-bit store and it is why the pointer is one
     * instruction and not two: the driver reads the low word at +0 and the high
     * word at +4 and concatenates them, so a little-endian 64-bit store is
     * exactly the pair it will read back. Both addresses are below 4 GB, so the
     * high word is zero and the two forms agree anyway - worth stating rather
     * than relying on, because a target at or above 4 GB would make them differ
     * and this comment is the only place that would say so.
     */
    ldr   x0, =SEED_SMEM_BLOCK
    add   x0, x0, #(SEED_SMEM_IPA & (S2_MOVED_BLOCK - 1))
    add   x1, x0, #SEED_SMEM_OFF          /* the structure, in the pool */
    ldr   x3, =SEED_SMEM_IPA
    add   x3, x3, #SEED_SMEM_OFF          /* the pointer, as the guest will see it */

    str   x3, [x0]

    /*
     * The three words of the structure, and each of them is a field the driver
     * reads and not a filler. The driver stores the first qword at its own
     * `.data + 0xA40` and later takes `[+4]` of that as the SMEM *size* and
     * checks `[+0]` against the magic; it takes `[+8]` as the SMEM *base*, which
     * is the value whose being zero is the next assert the boot reaches; and it
     * takes the low half of `[+16]` as a count that falls back to 498 when zero.
     * So the size and base are the board's own - 0x80900000 for 0x200000, out of
     * `device/config/uefiplat.cfg` through `MemoryMapLib.c`, passed in as
     * SEED_SMEM_BASE and SEED_SMEM_SIZE - and the third word is left zero, which
     * is both what the driver tolerates and what this instrument can honestly
     * say: it does not know what belongs there.
     */
    ldr   x5, =SEED_SMEM_SIZE
    lsl   x5, x5, #32
    mov   w2, #0x4953
    movk  w2, #0x4949, lsl #16
    orr   x5, x5, x2                      /* magic in the low half, size in the high */
    ldr   x6, =SEED_SMEM_BASE
    str   x5, [x1]
    str   x6, [x1, #8]
    str   xzr, [x1, #16]

    /*
     * One more word, and this one is an experiment rather than a fix. With the
     * pointer supplied, EnvDxe gets past `smem_target.c +435`; with the
     * structure's base field supplied as well, it gets past `smem.c +659`; and
     * then it stops at `smem.c +671`, which asserts unless the word at
     * SMEM + 0xC0 is 1:
     *
     *     ldr w9, [x8, #192]      ; x8 = the base it just read out of the structure
     *     cmp w9, #1
     *     b.ne <assert, "SMEM is not initialized by Boot.">
     *
     * at RVA 0x823C. On the device that word is 1 because XBL wrote SMEM's header
     * before any of this ran. Under `virt` the address is RAM holding zero, so the
     * driver's conclusion - that the boot stage did not initialise SMEM - is
     * *correct about the machine it is running on*, and the assert is not a model
     * artefact at all.
     *
     * Which is why this is written to find out what the wall is made of rather
     * than to get past it. If EnvDxe proceeds, the mirror needs a handful of flag
     * words and is within reach. If it stops on the next thing it reads out of
     * SMEM, then what the mirror is missing is SMEM's *contents* - a structure
     * this file has no honest way to supply, because the device's own copy lives
     * in RAM that XBL fills at boot and that no partition backup can contain.
     * The run this was added for is the answer, and `docs/08` carries which it
     * was.
     */
    ldr   x7, =SEED_SMEM_BASE
    mov   w8, #SEED_SMEM_FLAG
    str   w8, [x7, #SEED_SMEM_FLAG_OFF]

    /* A record, so the launcher can read the seed back out of the machine's own
     * memory rather than out of the arguments it passed: what was written and
     * what was meant to be written are different claims, and only the first is a
     * run. Every field is a value this code computed and not a symbol it was
     * given, so the record is evidence rather than an echo. */
    adr   x4, seed_diag
    stp   x0, x3, [x4]
    stp   x1, x5, [x4, #16]
.endif

    /* No FP or SIMD trapping anywhere below. */
    msr   cptr_el3, xzr
    mov   x0, #0x33ff
    msr   cptr_el2, x0

    /* EL1h with DAIF masked, which is the state QEMU enters a plain virt guest in. */
    mov   x0, #0x3c5
    msr   spsr_el3, x0

    ldr   x0, =PAYLOAD
    msr   elr_el3, x0
    isb
    eret

    /*
     * Sixteen entries of 0x80, in the architecture's order. Slot 8 - synchronous
     * exception from a lower EL, AArch64, which is what an SMC from EL1 arrives as
     * - is the only one with anything to do. The other fifteen land on a spin:
     * nothing else at EL3 is expected to happen, and if it does it is a bug in
     * this stub rather than a condition the guest can be told about, so there is
     * nothing to discriminate and nothing to report beyond stopping where a dump
     * will show the PC.
     */
    .balign 0x800
vectors:
    .macro vec_entry
    b     el3_unexpected
    .balign 0x80
    .endm

    .rept 8
    vec_entry
    .endr
    b     smc_handler
    .balign 0x80
    .rept 7
    vec_entry
    .endr

el3_unexpected:
    b     el3_unexpected

    /*
     * x0 = -1 on return. Not zero: zero is Qualcomm's success, and answering
     * success to a service that is not there would hand the caller a result it
     * would believe. -1 is the status a caller takes when the SMC itself failed,
     * and it cannot be mistaken for a buffer address either.
     *
     * x1..x7 are left alone. They are the caller's arguments and it is about to
     * read x0 and carry on; clobbering them would be this stub inventing evidence.
     * Nothing else is touched, so there is no register to save - and nothing is
     * recorded here either: the register dump the guest's handler prints carries
     * x0..x3 at the moment of the call, and a second record written from EL3 would
     * be a second answer to a question the panel answers once.
     */
    .balign 4
smc_handler:
    movn  x0, #0
    eret

.if ZERO_MEM
    /*
     * The stage-2 tables, placed by `.org` so that the offsets at the top of this
     * file are where they land and so that the launcher can find them by symbol
     * and read them back out of the running guest. Nothing else is in this 8 KB.
     *
     * L1 has four entries because that is all a 32-bit input reaches at a
     * gigabyte each. Entry 0 is a table, because the low gigabyte is the one that
     * has to be resolved in finer steps than a gigabyte; entries 1 to 3 are
     * gigabyte blocks that say "where the guest says, which is where it goes".
     *
     * The absolute address in entry 0 is the L2 table's physical address, which
     * is why EL3_LOAD has to be passed in - the assembler cannot know where this
     * code was loaded, and a table descriptor pointing at 0x3000 would send every
     * walk into the first pages of RAM.
     */
    .globl s2_diag
    .org  S2_DIAG_OFF
s2_diag:
    .space 32

    /*
     * What SEED_SMEM actually wrote: the two pool addresses, the pointer as the
     * guest will read it, the structure's first qword as this file recorded
     * writing it - and nothing about the word at SMEM + 0xC0, because that one
     * is not in this stub's memory for a record to be taken from. The launcher
     * fetches it by its address in SMEM instead, which is the one read that would
     * still be right if this file were wrong about where it wrote. Read back out
     * of the machine after the run; placed with `.org` for the same reason the
     * tables are - the launcher finds it by symbol and the offset is in one file.
     */
    .globl seed_diag
    .org  SEED_DIAG_OFF
seed_diag:
    .space 32

    .globl s2_l1
    .org  S2_L1_OFF
s2_l1:
    .quad EL3_LOAD + S2_L2_OFF + 0x3
    .quad S2_BLOCK | 0x40000000
    .quad S2_BLOCK | 0x80000000
    .quad S2_BLOCK | 0xC0000000

    /*
     * L2 resolves the low gigabyte in 2 MB blocks. Every entry is the identity -
     * the guest's byte at 0x09200000 is the machine's byte at 0x09200000 - except
     * the ones covering a region this board's memory map declares below
     * 0x40000000, which are pointed at untouched RAM below the payload.
     *
     * The 512 `.quad`s are generated, not written here: `qemu-panel-read.py` reads
     * the platform's own `MemoryMapLib.c`, works out which blocks those regions
     * occupy, assigns each its own 2 MB of the pool, and writes the file this
     * `.include`s. A table copied in beside the map it is meant to model is a
     * second answer to what this machine has, and the whole value of the run is
     * that the answer is the firmware's own.
     *
     * Each redirected block gets a block of its own rather than sharing one page:
     * a driver that writes a register and reads it back has to see what it wrote,
     * and aliasing 55 regions onto one page would invent a memory that remembers
     * the last write anywhere - a lie with a symptom, which is worse than a zero.
     */
    .globl s2_l2
    .org  S2_L2_OFF
s2_l2:
    .include "s2_l2.inc"
.endif
