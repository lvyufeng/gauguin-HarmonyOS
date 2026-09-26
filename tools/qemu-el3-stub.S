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
 * will read the log backwards. A third switch, SEED_AOP, is off by default and
 * separate for the same reason: it writes a value the model has no AOP to supply,
 * so a run with it on is one more thing than a run with it off, and a log that
 * does not say which it was cannot be read either way.
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
 *       --defsym SEED_AOP=1 --defsym SEED_AOP_BLOCK=0x42a00000 \
 *       --defsym SEED_AOP_IPA=0x0C3F000C --defsym SEED_AOP_STATE=1 \
 *       --defsym SEED_AOP_MAGIC=0x0C0330DB \
 *       -I <dir with s2_l2.inc> -o stub.o stub.S
 *   aarch64-linux-gnu-objcopy -O binary stub.o stub.bin
 *
 * `tools/qemu-panel-read.py --el3-stub --el3-zero-mem --el3-seed-smem` does
 * exactly this and passes the addresses through, so the numbers have one home;
 * `--el3-seed-aop` is the third switch and adds the mailbox triple - with the
 * word's address and the two values it points at decoded out of the driver's own
 * instructions, which is why they appear in this line and not only in the source.
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
     * The rung above the flag, and the one step 4.126 designed and step 4.127
     * reads: SMEM's partition table. The flag at +0xC0 gets `smem_init` past its
     * own check; it does not give SMEM a single readable item, and the first
     * seeded runs said so in SMEM's own words - `smem_get_addr: SMEM get addr
     * failed! smem_type=402` from EnvDxe's own start, and `smem_alloc: SMEM
     * allocation failed! smem_type=404, buf_size=8192` nine Apriori entries
     * later. Both are one absence, and the disassembly says which.
     *
     * `smem_partition_init` (EnvDxe RVA 0x9160, and the strings it asserts with
     * name `smem_partition.c`) does not find SMEM's heap by looking for a heap:
     * it takes the last page of the region `SEED_SMEM_BASE`/`_SIZE` name and
     * looks for the 32-bit tag `$TOC` there (`0x434F5424`, `mov w9,#0x5424;
     * movk w9,#0x434f,lsl#16` at RVA 0x91B8). With that page zero it returns
     * *silently* - RVA 0x91C8 branches to the routine's epilogue at 0x9310, and
     * there is no print and no assert on that path, which is why a seeded run
     * says nothing about the TOC and its absence has to be read out of the code.
     * What it would have filled is the eleven 0x30-byte item descriptors at
     * `.data + 0x830`, one per tag 0..10, and those stay zero instead.
     *
     * Then the failure itself. `smem_get_addr` arrives at the legacy get-addr
     * (RVA 0x8B2C), which hands the shared body at 0x8DE0 a tag of 0xFFFF - a
     * tag that body resolves to descriptor slot 0 (`cmp w8,#0xffff; csel x20,
     * x11, x12, eq`) - and 0x8E24 returns `0xFFFFFFFC`, "not populated", the
     * moment the descriptor's size word is zero. One zero where a TOC should be,
     * eleven dead descriptors, and every SMEM item the firmware asks for
     * reported missing by whichever consumer asks first.
     *
     * So this rung writes four structures, each the minimum the code reading it
     * accepts. Where they go is the board's arithmetic and not this file's: the
     * TOC is at `base + size - 0x1000` because that is where `smem_partition.c`
     * computes it from the region the platform's map declares, and the partition
     * is at +0x1000 because an entry's offset must be page-aligned and non-zero
     * (the asserts at `smem_partition.c` +976 and +978).
     *
     *   1. the TOC: `$TOC`, version 1 (+922 asserts on anything else), one entry
     *      (+923 allows up to SMEM_MAX_PARTITIONS = 0x14).
     *   2. the entry at TOC+0x20: offset 0x1000, size 0x100000, and host0 and
     *      host1 - the two halfwords of +0x0C and +0x0E, which is what the
     *      routine at RVA 0x9208 reads to choose the descriptor slot - both
     *      0xFFFE, which is its "any host" value and the one that selects slot 0,
     *      the slot `smem_get_addr` reaches. Zero is not that value: zero
     *      resolves to 0xFFFF, which the routine treats as *skip this entry*.
     *      Its +0x10 word is 0x1000, the divisor the allocator divides by at RVA
     *      0x8F14. That value is the seed's own - nothing in the tree says what
     *      the device's TOC holds - but it is not a second answer to the
     *      driver's arithmetic: the driver reads the divisor out of the entry the
     *      seed wrote, so the number the seed divides by and the number the
     *      driver divides by are the same word, and changing it moves both ends
     *      together.
     *   3. the partition header at +0x1000: `$PRT` (`0x54525024`,
     *      `SMEM_PARTITION_HEADER_ID`, asserted at +977), with its size word at
     *      +8 equal to the entry's size - the routine compares the two and prints
     *      `Invalid partition size` at RVA 0x9288 when they differ - and the data
     *      area described by +0x0C and +0x10.
     *   4. the item itself, at the address the allocator's own arithmetic hands
     *      back: a heap block whose first halfword is `0xA5A5` (the tag its block
     *      walker checks at RVA 0x9030) with the memory partition table ten bytes
     *      into it - the two magics `0x9DA5E0A8` and `0xAF9EC4E2` that EnvDxe's
     *      own parse compares at RVA 0x52A8, and a count at +8.
     *
     * (4)'s address is the part worth arguing about, because it is not written
     * here as a number. RVA 0x8F04..0x8F30 computes
     * `partition_base + size - (((div + 0xF) / div) * div + 0xFFF0 & 0xFFFF) - 0x10`,
     * so the assembler evaluates that expression out of the symbols below and the
     * block lands where the code will look - one derivation, two readers. If this
     * file's reading of that routine is wrong, the guest reads the address the
     * arithmetic really produced, which is RAM holding zero, and what the parse
     * then says is a plain failure and not a silent mismatch. The seed's first
     * answer here was 0x10000 low - it left out the partition's offset and the
     * truncation - and `tools/qemu-panel-read.py` now checks the address the stub
     * recorded against this expression rather than trusting that the two agree.
     *
     * What it costs, and this is the largest lie in the file. SMEM is no longer
     * four words in an otherwise empty region: it is five nested structures -
     * TOC, entry, partition header, heap block, partition table - every one of
     * them this file's invention, standing in for values XBL wrote on the device
     * and that nothing in this tree records. A driver downstream of SMEM is now
     * reading a mirror of a structure rather than a structure, and a run that
     * behaves differently from the device there can be differing because of any
     * of the five. The table's own *contents* are not fabricated and are not
     * either: the count is 1 and the entries are absent, so a consumer that
     * walks it reads zeros for its first entry. That is the flag's trade one
     * level deeper, and it is why the run this rung produces is read for one
     * thing: whether `K 17`'s `SU` moves.
     */
    .equ  SEED_SMEM_TOC_MAGIC, 0x434F5424       /* "$TOC" */
    .equ  SEED_SMEM_TOC_VERSION, 1
    .equ  SEED_SMEM_PART_MAGIC, 0x54525024      /* "$PRT" */
    .equ  SEED_SMEM_PART_OFF, 0x1000
    .equ  SEED_SMEM_PART_SIZE, 0x00100000
    .equ  SEED_SMEM_PART_HEADER, 0x20           /* where the partition's data area starts */
    .equ  SEED_SMEM_PART_DATA, 0x000F0000
    .equ  SEED_SMEM_ENTRY_DIV, 0x1000           /* the TOC entry's +0x10 word */
    /*
     * The TOC entry's host0 and host1 - the two halfwords at +0x0C and +0x0E -
     * and this is the other correction step 4.127 carries. They were written as
     * zero, on the reading that slot 0 is the slot nothing selects. The routine
     * at RVA 0x9160 says otherwise, and it says it in four instructions:
     *
     *     9208: ldrh w4, [x13, #0xc]      ; host0
     *     920c: ldrh w5, [x13, #0xe]      ; host1
     *     9210: cmp  w4, w28              ; w28 = 0xFFFE
     *     9214: ccmp w5, w28, #0x0, eq
     *     9218: b.eq 0x9238               ; both 0xFFFE -> w20 = 0
     *     ...
     *     921c: cmp  w5, #0x0
     *     9220: csel w10, w4, w19, eq     ; w19 = 0xFFFF
     *     9224: cmp  w4, #0x0
     *     9228: csel w20, w5, w10, eq
     *     922c: cmp  w20, #0xa
     *     9230: b.ls 0x923c
     *     9234: b    0x9300               ; -> the next entry
     *     ...
     *     923c: cmp  w20, w19
     *     9240: b.eq 0x9300               ; slot 0xFFFF -> the next entry
     *
     * So 0xFFFE in both fields means slot 0 (`mov w20, wzr`), a zero field means
     * the other field decides, both zero means 0xFFFF - which is *skip*, not slot
     * 0 - and a slot above 10 (SMEM_MAX_PARTITIONS is 0x14 but the descriptors
     * are eleven) is skipped too. With the fields at zero the seed's entry was
     * therefore not installed at all: every descriptor stayed zero, and the
     * `smem_type=402` and `smem_type=404` failures the first seeded runs printed
     * are the driver reporting the absence this line created.
     */
    .equ  SEED_SMEM_ENTRY_HOST, 0xFFFE          /* "any host", both halves */
    /*
     * The heap block, and each of these three is a field a routine reads rather
     * than a filler. `SEED_SMEM_BLOCK_TAG` is not decoration and not a magic in
     * the "is this ours" sense: the routine at RVA 0x901C does
     *
     *     9034: ldrh w8, [x3]                ; x3 is the candidate block
     *     9038: cmp  w8, #0xa5a5
     *     903c: b.ne <print "Invalid SMEM canary 0x%X at location 0x%...",  return 0>
     *
     * so the tag is a canary, and a block without it is skipped and the scan
     * continues rather than stopping. `SEED_SMEM_BLOCK_SIZE` goes at +4 and is
     * compared with the partition's own size at 0x9040-0x9050: the block is taken
     * down the normal path when it is at or below that size, which is how a heap
     * carved out of the partition is meant to look, and down an
     * "Invalid SMEM item size:%d, Partition size:%d" print when it is larger. The
     * +2 halfword is read at 0x9048 and handed back in `w4`, and the routine's
     * caller compares it at 0x90C0 against `[x19+4]` - an id from the caller's own
     * descriptor - so the two have to agree for the block to be accepted. Nothing
     * in the tree says what that id is, and this file writes zero there: if the
     * scan reaches the block and is rejected at 0x90C0, this field is where to
     * look, and the word is a second missing value of the same kind as the AOP
     * pointer rather than something a longer read of the image would settle.
     */
    .equ  SEED_SMEM_BLOCK_TAG, 0xA5A5
    .equ  SEED_SMEM_BLOCK_SIZE, 0x000F0000
    .equ  SEED_SMEM_PTABLE_MAGIC0, 0x9DA5E0A8
    .equ  SEED_SMEM_PTABLE_MAGIC1, 0xAF9EC4E2
    .equ  SEED_SMEM_PTABLE_ENTRIES, 1

    /*
     * Where inside SMEM the allocator's arithmetic puts the item, from the
     * routine at RVA 0x8F04 and not from a number copied out of a run. Read
     * literally, that routine is
     *
     *     8f04: ldp  w14, w12, [x20, #0x8]   ; w14 = the partition's size,
     *                                        ; w12 = the descriptor's +0xC word
     *     8f08: orr  w15, wzr, #0xfff0
     *     8f10: add  x13, x12, #0xf
     *     8f14: udiv x8, x13, x12            ; (w12 + 0xF) / w12
     *     8f18: ldr  w13, [x20]              ; the partition's base
     *     8f1c: madd w21, w8, w12, w15       ; ... * w12 + 0xFFF0
     *     8f20: add  w10, w14, w13           ; base + size
     *     8f28: sub  w14, w10, w21, uxth     ; ... - (w21 & 0xFFFF)
     *     8f2c: sub  x9, w14, #0x10          ; ... - 0x10
     *
     * and it is `uxth` - the truncation to sixteen bits - that the expression
     * below has to reproduce, because it is what makes the answer land where the
     * routine looks rather than where a reader would first guess.
     *
     * This is the correction step 4.127 carries, and the old expression was 0x10000
     * low. Without the `& 0xFFFF` it measured back over 0x10FF0 where the routine
     * measures back over 0xFF0, and the difference is one 16-bit carry. With this
     * file's own numbers - size 0x100000, divisor 0x1000, partition base 0x80901000
     * - the routine's w21 is 1 * 0x1000 + 0xFFF0 = 0x10FF0, `uxth` keeps its low
     * sixteen bits and so measures back over 0xFF0, and `base + size - 0xFF0 - 0x10`
     * is 0x80A00000. The un-truncated 0x10FF0 gives 0x809F0000, and the seeded runs
     * said so in the driver's voice: `smem_get_addr: SMEM get addr failed!
     * smem_type=402` and `smem_alloc: SMEM allocation failed! smem_type=404` are what
     * a heap whose item is not where the allocator looks reports, and this line is
     * part of why they were there. Both are now the seed's own arithmetic to answer
     * for.
     *
     * The value below is measured from the *partition's* base, because that is
     * what the line that uses it adds: `base + SEED_SMEM_PART_OFF +
     * SEED_SMEM_TABLE_REL`. So the partition's size is what is counted back over,
     * and the partition's offset appears once, in the sum where it belongs. It is
     * not a coincidence that the result is SMEM's base plus one PART_SIZE: the
     * partition runs from base + 0x1000 to base + 0x101000, and the item the
     * allocator hands out is the last 0x1000-aligned block inside it.
     *
     * The 0xFFF0 is `orr w15,wzr,#0xfff0` at 0x8F08 and the 0x10 is the item
     * header the address is measured back over.
     */
    .equ  SEED_SMEM_TABLE_REL, ((SEED_SMEM_PART_SIZE - ((((SEED_SMEM_ENTRY_DIV + 0xF) / SEED_SMEM_ENTRY_DIV) * SEED_SMEM_ENTRY_DIV + 0xFFF0) & 0xFFFF)) - 0x10)

    /*
     * ------------------------------------------------------------------------
     * The second seed, and the one this rung exists for: the AOP mailbox word
     * that CmdDbDxe's own entry gate reads, at 0x0C3F000C.
     *
     * What the gate is, from the driver's bytes and not from a pattern. CmdDbDxe
     * is Apriori 17 and its entry is 0x1000 -> 0x1150 -> 0x123c -> 0x19a0 ->
     * 0x1398 (a bare `b 0x13a4`) -> 0x13a4 -> 0x2540, and 0x2540 is:
     *
     *     2550: bl   0x45c0
     *     2558: str  x0, [x20, #0x100]      ; x20 = 0x6000, the cache slot
     *     255c: cbz  x0, 0x2594             ; -> the failure epilogue
     *     2564: ldr  x8, [x20, #0x100]      ; x8 = the word that was read
     *     2568: ldr  w9, [x8]               ; and it is a *pointer*
     *     256c: cmp  w9, #0x1
     *     2570: b.ne 0x2594
     *     2574: ldr  w10, [x8, #0x4]
     *     2578: mov  w11, #0x30db
     *     257c: movk w11, #0xc03, lsl #16   ; w11 = 0x0C0330DB
     *     2580: cmp  w10, w11
     *     2584: b.ne 0x2594
     *
     * and 0x45c0 is the read itself, three instructions and a static store:
     *
     *     45c0: mov  w8, #0xc
     *     45c4: movk w8, #0xc3f, lsl #16    ; w8 = 0x0C3F000C
     *     45cc: ldr  w0, [x8]               ; not [x20+...]: an absolute read
     *
     * `ldr w0` zero-extends, so x0 is a 32-bit address. The gate is therefore
     * `p = *(u32 *)0x0C3F000C; p != 0 && p[0] == 1 && p[1] == 0x0C0330DB`, and
     * the two values the driver is asking for are at the pointer and the pointer
     * plus four - *not* at 0x0C3F0010, which nothing in any image reads. When the
     * gate fails, 0x2594 sets w19 = 1 and 0x263c returns it; 0x13b4 turns that
     * into `mov x0, #0x8000000000000003`, which is EFI_UNSUPPORTED, and EFI_
     * UNSUPPORTED is the `U` the panel prints for tick 17.
     *
     * There is no other way out of the driver and no other source of that
     * refusal: the entry's *other* guard, at 0x116c, reads `ImageBase + 0x46b4`
     * and returns EFI_ABORTED - `O` on the panel, not `U` - and the word it reads
     * is zero in the image and written by nothing in it, so that guard is inert
     * here and on the device alike. The `U` is this gate and only this gate.
     *
     * The same gate, twice more in the tree, and both agree that the word is a
     * pointer. `part-aop.img` carries the AOP's own copy of it at 0x29318 - Thumb
     * code this tree cannot assemble but can read - and it is the same three
     * comparisons in the same order:
     *
     *     2931a: bl   0x294ac            ; its own read of the mailbox
     *     29322: str  r0, [r1]           ; r1 = a global at 0x0E43D8
     *     29324: beq  0x29338            ; p == 0 -> failure
     *     29326: ldr  r2, [r0]           ; *p
     *     29328: cmp  r2, #0x1
     *     2932a: bne  0x29338
     *     2932c: ldr  r0, [r0, #0x4]     ; *(p + 4)
     *     2932e: ldr  r2, [pc, #0x14]    ; the literal at 0x29344
     *     29330: cmp  r0, r2             ; 0x0C0330DB
     *     29332: bne  0x29338
     *
     * So the AOP tests the published record exactly as the driver does, and the
     * 0x0C0330DB at 0x29344 is *that* compare's literal - one of the two the AOP
     * image was found to carry, the other being the same constant in a second
     * function's pool at 0x294a8. The producer and the consumer are reading one
     * structure through one pointer.
     *
     * Why the model reads zero there. 0x0C3F000C is inside `AOP_SS_MSG_RAM`,
     * 0x0C300000 for 0x00100000, which this board's own memory map declares
     * (MemoryMapLib.c line 40), so --el3-zero-mem gives that 2 MB block back as
     * untouched RAM and the read succeeds and returns zero. On the device the
     * word is not the firmware's and not XBL's: it is the AOP's. This instrument
     * has no AOP - nothing in the payload boots one, and part-aop.img is not a
     * participant in the run - so the mirror is a machine whose application
     * processor is absent, and the driver is right about the machine it is
     * running on. Exactly the shape of the SMEM flag at +0xC0, one region over.
     *
     * Why the *values* are not invented. Three independent things say what the
     * record holds, and they agree:
     *
     *   - the phone's own capture. `docs/08-device-session.md` step 4.12 has the
     *     device's `P2 SEQ`, 46 characters, and index 16 of it - Apriori 17,
     *     CmdDbDxe - is `s`. The dispatcher prints that letter from the status the
     *     driver's entry *returned*, so on the device the entry passed this gate,
     *     and passing it means the record held (1, 0x0C0330DB) at that moment.
     *     The values are read off a photograph of the device, not chosen.
     *   - the driver's own compare, which is where the magic first came from -
     *     the `movk w11,#0xc03,lsl #16` above - and its own literal for the state.
     *   - the AOP firmware's own image, whose copy of the gate is quoted above. The
     *     same handshake's address appears in three other firmware components that
     *     read it - `part-hyp.img` at 0x2fa50 and `part-tz.img` at 0x9dd94 and
     *     0xcddd0 - while neither the address nor the magic appears in
     *     part-xbl.img, part-abl.img, part-devinfo.img or part-cdt.img. So the
     *     producer is the AOP and the consumers are hyp, TZ and this driver.
     *
     * What is invented is the *pointer*, and only the pointer: nothing in the tree
     * records the address the device's publisher used, and both ends of the
     * handshake only dereference it, so the one thing the seed's choice has to
     * satisfy is that the record it names is readable by the guest. The record is
     * therefore placed inside the same 2 MB block as the mailbox word itself,
     * SEED_AOP_REC_OFF bytes further on: the block is already declared by the
     * board's map and already redirected by --el3-zero-mem, so the guest's read of
     * the pointer lands on the bytes this file wrote - and the launcher reads them
     * back out of the pool and says whether that held.
     *
     * What it costs, and it is a smaller lie than the container above. The
     * container is a structure this file builds; this is three words put back into
     * a mailbox the SoC's own map declares and that a processor this instrument
     * does not model would have filled. What it does *not* model is everything
     * behind them: the driver, once past the gate, builds a command-DB lookup out
     * of the AOP resource names `"MC0"`, `"mss.lvl"` and `"cx.lvl"` (RVAs 0x54f9,
     * 0x54fd, 0x5505) by reading the AOP command DB at 0x80860000, which under
     * this instrument is RAM holding zero - and 0x80860000 is itself a constant
     * the AOP image carries, beside its copy of the magic. A run that gets past
     * the gate therefore says the gate opened and says nothing about whether the
     * driver got what it came for - if it fails further in, that failure is the
     * absent command DB and not the seed.
     *
     * And it is observable only with SEED_SMEM on: without it EnvDxe asserts at
     * Apriori slot 2 and nothing ever reaches Apriori 17. So a run with SEED_AOP
     * and not SEED_SMEM writes a word no reader of the log will see read - the
     * launcher refuses that combination rather than making the run.
     */
    .ifndef SEED_AOP
    .equ  SEED_AOP, 0
    .endif

    /*
     * The word and its address. The address is a `--defsym` for the same reason
     * every other address here is: `tools/qemu-panel-read.py` reads it out of the
     * driver's own `mov`/`movk` pair, works out which pool block stands in for it,
     * and passes both in - so the block the seed writes into and the address the
     * guest reads are one number with one home. The three values below are passed
     * in for the same reason turned around: the launcher decodes them out of the
     * driver's own instructions, so the numbers the seed writes are the numbers
     * the driver compares against, and this file's copies are the standalone
     * fallback rather than a second opinion.
     */
    .ifndef SEED_AOP_IPA
    .equ  SEED_AOP_IPA, 0x0C3F000C     /* `movk w8, #0xc3f, lsl #16` at 0x45c4 */
    .endif

    /* `cmp w9, #1` at RVA 0x256c: the state at *the pointer*. */
    .ifndef SEED_AOP_STATE
    .equ  SEED_AOP_STATE, 1
    .endif

    /* `cmp w10, w11` at RVA 0x2580 against 0x0C0330DB, set at 0x2578. */
    .ifndef SEED_AOP_MAGIC
    .equ  SEED_AOP_MAGIC, 0x0C0330DB
    .endif

    /*
     * How far past the mailbox word the record it points at goes. This is the one
     * number here that is the file's own: the device's publisher chose its own
     * address and nothing in the tree records it, so the seed's pointer has to
     * name something, and eight bytes on in the same block is the choice - far
     * enough not to overwrite the mailbox word itself, close enough that both land
     * in the one 2 MB block the redirect covers. The launcher reads it back out of
     * this file's symbol table rather than repeating it.
     */
    .equ  SEED_AOP_REC_OFF, 8

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

    /*
     * And the four structures of the rung above it, in the order the driver
     * meets them: the TOC in the region's last page, the entry inside the TOC,
     * the partition header the entry's offset names, and the item the allocator
     * will hand to whoever asks for smem_type 402. The header above has the
     * argument for each field; what is here is only where it goes.
     *
     * `x9` is the TOC page and `x16` is the item, and both are kept: they are
     * the two of the four that the launcher cannot find by a symbol of its own
     * - they live in SMEM rather than in this stub - so they go into the record
     * at the end of this block and the read-back fetches them by address.
     */
    ldr   x9, =SEED_SMEM_BASE
    ldr   x10, =SEED_SMEM_SIZE
    add   x9, x9, x10
    sub   x9, x9, #0x1000                  /* base + size - 0x1000: the TOC page */

    mov   w11, #0x5424
    movk  w11, #0x434f, lsl #16            /* "$TOC" */
    str   w11, [x9]
    mov   w11, #SEED_SMEM_TOC_VERSION
    str   w11, [x9, #4]
    mov   w11, #1                          /* one entry; +923 allows 0x14 */
    str   w11, [x9, #8]

    mov   w12, #SEED_SMEM_PART_OFF
    mov   w13, #SEED_SMEM_PART_SIZE
    lsl   x13, x13, #32
    orr   x13, x13, x12                    /* entry+0: offset, entry+4: size */
    str   x13, [x9, #0x20]
    str   wzr, [x9, #0x28]                 /* entry+8: the TOC's own four bytes */
    mov   w12, #SEED_SMEM_ENTRY_HOST
    movk  w12, #SEED_SMEM_ENTRY_HOST, lsl #16
    str   w12, [x9, #0x2c]                 /* entry+0xC/+0xE: host0 and host1 */
    mov   w14, #SEED_SMEM_ENTRY_DIV
    str   x14, [x9, #0x30]                 /* entry+0x10: the allocator's divisor */

    ldr   x15, =SEED_SMEM_BASE
    add   x15, x15, #SEED_SMEM_PART_OFF
    mov   w11, #0x5024
    movk  w11, #0x5452, lsl #16            /* "$PRT" */
    str   w11, [x15]
    str   wzr, [x15, #4]
    mov   w11, #SEED_SMEM_PART_SIZE
    str   w11, [x15, #8]
    mov   w11, #SEED_SMEM_PART_HEADER
    str   w11, [x15, #0xc]
    mov   w11, #SEED_SMEM_PART_DATA
    str   w11, [x15, #0x10]

    ldr   x16, =SEED_SMEM_BASE
    add   x16, x16, #(SEED_SMEM_PART_OFF + SEED_SMEM_TABLE_REL)
    mov   w11, #SEED_SMEM_BLOCK_TAG
    strh  w11, [x16]                       /* the heap block's 0xA5A5 tag */
    mov   w11, #SEED_SMEM_BLOCK_SIZE
    str   w11, [x16, #4]
    mov   w11, #0xe0a8
    movk  w11, #0x9da5, lsl #16            /* the partition table, ten bytes in */
    str   w11, [x16, #0x10]
    mov   w11, #0xc4e2
    movk  w11, #0xaf9e, lsl #16
    str   w11, [x16, #0x14]
    mov   w11, #SEED_SMEM_PTABLE_ENTRIES
    str   w11, [x16, #0x18]

    /*
     * The AOP mailbox, and it is the same kind of write as the one above and not
     * the same kind as the container: three words put back into a window the
     * board's own memory map declares and that a processor this instrument does
     * not run would have filled. The header above carries the argument for the
     * values; what is here is where they go - the pointer at the mailbox word, and
     * the record's two words at the pointer. `x17` is left at zero when the switch
     * is off, so the record below has one form and the launcher can tell "not
     * asked for" from "asked for" by remembering which it asked.
     */
    mov   x17, xzr
.if SEED_AOP
    ldr   x17, =SEED_AOP_BLOCK
    /*
     * Two adds and not one, and the reason is a fact about the encoding rather
     * than about the address: an ADD's immediate field is twelve bits, and the
     * assembler will only fold the rest into the `lsl #12` form when the value is
     * a multiple of 0x1000 (which is how the line above reaches 0x01FD4000). This
     * address's offset within its 2 MB block is 0x1F000C, and 0xC is not a
     * multiple of anything - so it comes in as the page part and the remainder.
     */
    add   x17, x17, #(SEED_AOP_IPA & (S2_MOVED_BLOCK - 1) & ~0xFFF)
    add   x17, x17, #(SEED_AOP_IPA & 0xFFF)
    ldr   w11, =SEED_AOP_IPA            /* the value the guest will dereference */
    add   w11, w11, #SEED_AOP_REC_OFF
    str   w11, [x17]                    /* the mailbox word: a pointer, not a state */
    mov   w11, #SEED_AOP_STATE
    str   w11, [x17, #SEED_AOP_REC_OFF]           /* *p */
    mov   w11, #(SEED_AOP_MAGIC & 0xFFFF)
    movk  w11, #(SEED_AOP_MAGIC >> 16), lsl #16
    str   w11, [x17, #(SEED_AOP_REC_OFF + 4)]     /* *(p + 4) */
.endif

    /* A record, so the launcher can read the seed back out of the machine's own
     * memory rather than out of the arguments it passed: what was written and
     * what was meant to be written are different claims, and only the first is a
     * run. Every field is a value this code computed and not a symbol it was
     * given, so the record is evidence rather than an echo - and the middle two
     * are the two addresses in SMEM itself, which is where the rung above the
     * flag put the only things the launcher cannot find by one of this file's
     * own symbols. The last is the pool address of the AOP pair, which is the
     * same kind of thing for the second seed, and zero when that seed is off. */
    adr   x4, seed_diag
    stp   x0, x3, [x4]
    stp   x1, x5, [x4, #16]
    stp   x9, x16, [x4, #32]
    stp   x17, xzr, [x4, #48]
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
     * writing it - and, since the TOC rung, the two addresses of the structures
     * it wrote into SMEM itself, which are the ones no symbol of this file can
     * name. Nothing about the word at SMEM + 0xC0, because that one
     * is not in this stub's memory for a record to be taken from. The launcher
     * fetches it by its address in SMEM instead, which is the one read that would
     * still be right if this file were wrong about where it wrote. The seventh
     * qword is SEED_AOP's mailbox address in the pool, zero when that seed is off,
     * and the eighth is left zero so that the record has a whole number of pairs
     * and a reader who counts them cannot land on a half-written one. Read back out
     * of the machine after the run; placed with `.org` for the same reason the
     * tables are - the launcher finds it by symbol and the offset is in one file.
     */
    .globl seed_diag
    .org  SEED_DIAG_OFF
seed_diag:
    .space 80

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
