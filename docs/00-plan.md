# 00 — Phase plan

Goal: **Windows 11 on ARM running on the gauguin test phone**, with as much of the hardware
driven as is physically possible. See [`README.md`](../README.md) for the honest ceiling —
cellular and cameras are permanently out of reach, Wi-Fi/audio/GPU are hard but plausible.

Each phase ends with a commit. A phase is only "done" when its gate has actually been
observed on hardware, not when the code compiles.

## Where things actually stand (2026-09-23)

Nothing below is "done" except P0, and the only gate observed on hardware is
P0's plus the first half of P2's (see its row). This table is the honest state;
the sections under it are the plan.

| phase | gate | state |
|---|---|---|
| **P0** survey + backup | partitions dumped and verified; no existing port | **done** — 74 partitions carved and signature-checked, `boot`/`abl`/`recovery` hashes match the device, 86 XBL drivers recovered, and `git ls-files Silicon/Qualcomm` confirms no SM7225 package upstream |
| **P1** mainline kernel | device boots mainline and prints something | **not done** — `work/out/boot-pstore.img` is built, reproducible (`make_boot_image.py --kernel`), and carries both channels a device with no UART needs: the panel itself (`simple-framebuffer` + `simpledrm` + fbcon, so the boot log is photographed off the screen) and pstore (`console-ramoops-0`, readable from Android after a warm reboot). The earlier `fastboot boot` was refused with `Failed to load/authenticate boot image` on the RAM path, and the partition path has never been tried |
| **P2** UEFI skeleton | the boot manager draws on the phone's screen and UFS appears as a block device | **half met, and it is the half that decides viability** — **our firmware executes on this phone**. The image carrying the current device tree was written to `boot`, and on the reboot the panel filled with our own output, ending in `ASSERT [DxeCore] DxeMain.c(593)`. That text can only come from us: `DxeMain.c:593` is our line, and in a DEBUG build `SerialPortLib` is bound to `FrameBufferSerialPortLib`, so every `DEBUG ()` string is drawn into the framebuffer — which is why the firmware can talk while there is no shell, no boot-manager menu and no UART. (It also means text on the panel is not evidence that BDS ran.) What remains is the second half: DXE stops because at least one *architectural protocol* was never installed, and the name of the first missing one is printed two lines above the assert, on a screen that is legible by design (`GetFontScale ()` gives 10×24 glyphs, ~90×100 of them) and is wiped only when it scrolls. `docs/08` step 4.8 has the reading, and the dep chain that narrows it. The three earlier attempts stopped before any of our code for a reason now fixed: the tree in the image had no `/__symbols__`, so ABL refused the vendor overlay (`docs/07`). The gate said "reaches a shell" until the volume was inventoried and the shell turned out to be absent from *every* platform in the tree, `suryaPkg` included, so it would not have distinguished our firmware from a working one |
| **P3** ACPI | Windows installer boots and sees UFS | **item 1 half done, 2–4 not started** — the DSDT, `APIC`, `FACP`, `FACS`, `GTDT` and the shared `SSDT` are in the build and were read back out of the artifact; UFS and USB are described and I2C, GPIO, buttons and thermal are not. Those four need `_HID`s a device tree does not carry, and the reference corpus shows the id is `QCOM<family byte><block index>`. Both inputs are now known: the index is measured off the 66-table corpus, and the byte is `0A`, measured against the SC7280/Kodiak Windows driver set (8 of gauguin's 10 blocks named, 0 of 10 under every other byte) and corroborated by the only two corpus tables carrying those ids, `Xiaomi/lisa` and `Samsung/a52sxq`. TSENS is the one block neither source names — the set's thermal driver claims the *zone* ids `QCOM04B4`–`QCOM04CE`, not a sensor. No `.inf` gates the UEFI phase. `AcpiTableUpdate` is a no-op in ours alone: all 13 sibling packages implement it, 1,188–11,685 bytes, and every one patches the DSDT and reinstalls it. The two nearest SoCs, Kodiak (SM7325) and Rennell (SM7125), write 32 named fields; even the smallest sibling writes two. So the machinery exists and what is missing is the SMEM-derived values our DSDT does not declare. 2–4 are **not** volume-gated: see the space note below |
| **P4** Windows | desktop appears | not started — destroys `userdata` |
| **P5** peripherals | touch, Wi-Fi, GPU, audio | not started |

The commits so far are checkpoints inside P2, not a completed phase. Read the
phase state from this table, not from the commit titles.

### The one thing blocking progress

It is no longer a physical reset — that was the previous entry, and the reset
happened. What blocks progress now is **one line of text on the phone's screen**:
the name of the first architectural protocol DXE could not find, which
`CoreDisplayMissingArchProtocols ()` printed at `DxeMain.c:568`, two or three
lines above the assert. The phone is a distance away and holds that screen until
the next reset, so reading it costs nothing and every alternative costs a build
and a flash cycle. `docs/08` step 4.8 has what to look for and the dependency
chain that narrows the thirteen candidates.

If the screen cannot be read, the fallback is a firmware change rather than a
guess: make the missing-protocol report unconditional, or print it a second time
after the assert. That is a rebuild —
`./build_uefi.py -d gauguin -r DEBUG -c` in `work/uefi/Mu-Silicium`, then
`tools/build-p2-payloads.sh`, then `tools/flash-boot.sh` — and it should be one
cycle that answers the question whether or not anyone can read the panel.

The pieces a device session uses — the full sequence, with what each outcome
means and which payload to try next, is
[`08-device-session.md`](08-device-session.md):

1. `tools/fastboot-capture.sh` — first thing it asks is `oem fbreason`, which
   reports why ABL entered fastboot and can say `Reason:LoadImageAndAuth Fail`
   or `Reason:BootLinux Fail`. Either of those means the payload was reached.
   Then `oem uefilog` / `lkmsg` / `lpmsg`, plus `slot-unbootable` /
   `slot-retry-count`. If ABL is silent it runs `tools/unwedge-fastboot.py`,
   which classifies *which* silence it is — a reply left unread (recoverable by
   draining the endpoint), a download left waiting (recoverable in principle),
   or a fastboot thread stuck behind a still-live USB stack (not recoverable;
   power button). That was the state before the payload of step 4.8 ran; it is
   not the state now, and the difference is worth keeping straight, because it
   looked the same from the host both times.
   Note that `oem fbreason` and `oem uefilog` are commands **this phone's**
   ABL has and a Mu-Silicium-built one does not (`docs/07`), so their absence
   is not by itself a wedged fastboot.
2. `tools/pull-bootloader-log.sh` + `tools/read-logfs.py` — the device's third
   log channel, and the only one that does not depend on the payload running.
   ABL writes a log of every boot into the `logfs` partition: a FAT12 volume
   holding a ring of five 32 KiB `UEFILOG*.TXT` files, read here as a stage
   table with the last stage reached marked. Baseline, from the P0 dump: all
   five recorded boots reached `Start EBS`, i.e. handed over — so a slot that
   stops earlier is the answer, and `Apply Overlay` / `DTB offset is NULL` are
   both in ABL's own string table, meaning a refusal is written down even
   though the phone says nothing. Routes: `fastboot oem uefilog` (never yet
   returned anything here), `dd` of `/dev/block/by-name/logfs` from TWRP or
   root Android, or the P0 dump. `docs/07` has the format; `docs/08` step 4.6
   has how to read an answer.
3. `tools/restore-stock-boot.sh` — the A/B control: put the stock `boot` back
   and see whether Android returns.
4. `tools/flash-boot.sh` — the one command that writes a payload to `boot`, over
   whichever route answers. It exists because step 1b can make the fastboot route
   unreachable (the image in `boot` wedging ABL, so a reset reproduces the wedge)
   and TWRP is then not a fallback but the only way in; it also reads the
   partition back and compares the hash, so "the write landed" is established
   rather than assumed.
5. `work/out/boot-pstore.img` and its five siblings `-raw`, `-raw-txt`,
   `-raw-noefi`, `-gz-noefi`, `-gz-fixedsz` — P1's mainline kernel in the six
   shapes that differ on the properties separating our images from the one the
   phone boots (raw vs compressed, EFI-stub form of the arm64
   header, `text_offset`), each with `CONFIG_PSTORE_CONSOLE`/`PSTORE_RAM` and a
   cmdline that puts the kernel log in a pstore region **we choose**
   (`0xd0000000`, `ramoops@d0000000`, `no-map`). The phone declares no ramoops
   region of its own — its panic log goes through `mtdoops` to a raw partition —
   so there is nothing to match and the address is checked against the phone's
   DRAM partitions *and* its `no-map` carveouts (`tools/abl-boot-check.py`); the
   two earlier addresses were wrong in exactly those two ways, one outside every
   RAM partition and one inside the bootloader's `removed-dma-pool` for the modem
   and DSPs. The cmdline carries `reboot=panic_warm`, so a payload with no UART
   and no screen driver can still be read back at
   `/sys/fs/pstore/console-ramoops-0` after the phone reboots itself (step 4.5 in
   the runbook; the reasoning is in `docs/07`). `tools/build-p1-payloads.sh`
   builds the DTB and all of the images in one pass and refuses to ship a tree
   that is missing either log channel — one `ramoops` node at the address above,
   **or a `/chosen` with no `simple-framebuffer`** — because both are in that one
   file and a payload missing either is indistinguishable from one that works;
   `tools/check-payload.py` refuses to let a structurally wrong one reach the
   device. The screen half is the one that
   needs no round trip: the logo being replaced by the kernel log, and then by
   init's `alive: N s uptime` heartbeat, is P1's gate observed directly. The log
   half needs the phone to restart itself, so the kernel is also built to panic on
   the two failures this bring-up is most likely to hit (an oops in a probe, and a
   spin waiting on a clock or regulator that never comes ready) — otherwise both
   end in a kernel that neither prints nor reboots, and the ring is never read.
6. `work/out/p2-variants/Mu-gauguin-stock-{none,gzip}.img` — two stock-shaped
   builds, one per surviving candidate (uncompressed vs gzip kernel). Each pairs
   with a P1 variant on the compression property, which is what makes the pair —
   and not the individual attempt — the thing to read: see step 4b/4c in the
   runbook. A third image sits beside them, `Mu-gauguin-silicon-gzip.img`,
   deliberately not part of the pair: it varies the header version, the page size
   and where the tree lives all at once, so it cannot be read as a one-variable
   experiment, and it is kept as the fallback for the case where both stock
   variants are refused for a reason that turns out to be the stock shape itself.
   All three are built by `tools/build-p2-payloads.sh`, and the earlier build
   of the pair — made by hand, never flashed — could not have run at all: it
   carried a stale device tree with no `/__symbols__`, so ABL would have refused
   the vendor overlay (`docs/07`). What was in `boot` when the first attempt was
   made was the same shape as the third image — v1, page 2048, tree after the
   gzip stream — and carried that same stale tree, 71,737 bytes of it against
   the current build's 87,594; both images hold a *byte-identical* firmware
   (`SILICIUM_UEFI.fd` `md5 9c104725…`), so the tree is the only variable
   between them and step 4.8 is a clean one-variable experiment. `boot` now
   holds `Mu-gauguin-silicon-gzip.img`, `sha256 816b1d41…`.

Those pieces are backed by the offline tools below, which exist because a device
cycle is expensive and a bad image costs a physical reset. Every defect this
project has found in a payload was found by one of them rather than by the phone:

- `tools/abl-boot-check.py` — replays ABL's decision path over a built image and
  says whether *this phone* would take it: the arm64 header check, the
  `msm-id`/`board-id` selection, the overlay's fixups against our `/__symbols__`
  (replayed in Python *and* merged for real by `fdtoverlay`, since libfdt and
  libufdt disagree on a tree whose symbols point at phandle-less nodes — one
  refuses, the other boots with the fragments silently dropped), and the two
  placement questions (inside a DRAM partition, outside every `no-map` carveout)
  for the ramoops region and the framebuffer. Run by
  `tools/build-p1-payloads.sh` at the end, so a payload that fails it never
  reaches anyone.
- `tools/gauguin.py` — this phone's memory model in one place: the DRAM
  partitions and the `no-map` carveouts, both measured from the running phone's
  `/proc/device-tree` rather than read out of a tree's source.
- `tools/fdt.py` — just enough flattened-device-tree parsing to ask questions
  about a blob: the header, the node walk, `/__symbols__`, the overlay's
  `__fixups__`/`__overlay__` fragments, and the `qcom,msm-id`/`qcom,board-id`
  cells the selection turns on.
- `tools/make_dtbo_sinks.py` — generates the `/__symbols__` and the empty sink
  nodes the vendor overlay's 158 fixups resolve against. An input to the build
  rather than a check: without it ABL refuses every payload with
  `ApplyOverlay: ufdt apply overlay failed`, and with it the overlay lands in a
  subtree nothing binds to.
- `tools/build-device-tree.sh` — builds the one device tree every payload
  carries, and checks the built blob rather than the source it came from: the
  `/__symbols__` count, exactly one `ramoops` node at `0xd0000000`, and the four
  `/chosen/framebuffer` properties. It is a script of its own because the tree
  has two consumers — the P1 payloads and the UEFI ones — and holding it in one
  builder is what stops the second consumer building against a stale copy, which
  is what happened (`docs/07`).
- `tools/build-p1-payloads.sh` / `tools/build-p2-payloads.sh` — the two payload
  sets, each ending in both checkers so a payload that fails one never reaches
  anyone.
- `tools/read-logfs.py` — reads the bootloader's own per-boot log out of the
  `logfs` partition, from a raw image, an extracted slot, or a `oem uefilog`
  dump, and prints it as a stage table with the last stage reached marked. It is
  the one reader here that is aimed at the device rather than at a file we built,
  and it is offline in the sense that matters for the current blocker: the P0
  dump answers it with no phone at all.

The reference for all of these is Qualcomm's own `QcomModulePkg` (the ABL
source), vendored at `work/ref/mu_qcommodulepkg` from
`Daniel224455/mu_qcommodulepkg` and validated byte-for-byte against the PE
extracted from this phone. It is gitignored — a large copy of someone else's
tree — and used as the authority for what ABL does, which is why `docs/07`'s
claims about `CheckAllBitsSet`, `GZipPkgCheck` and the ramoops address can be
read against `file:line` instead of inferred from behaviour.

### Standing decisions, with one amendment

The original rule was "**Never write to the device's storage until P4**". That was
relaxed once, with the user's explicit authorization, to write the `boot`
partition for the P2 test — and `boot` was backed up and verified beforehand
precisely so that relaxation would be safe. The rule stands for everything else:
`userdata`, the partition table, and the firmware LUNs are still off limits
until P4.

---

## P0 — Survey, backup, firmware inventory

**Why first:** the installed ROM is a `user/dev-keys` Smartisan port with no public image.
Anything that re-partitions storage before a backup exists risks an unrecoverable device.
And the port itself cannot be scoped until we know which of Qualcomm's signed drivers this
phone's firmware actually contains.

Work:

1. Identify the hardware correctly (the device lies about its model — see `01-hardware.md`)
2. Dump every partition to `~/backup/gauguin/images/`
3. Recover the DXE driver set and platform config from the phone's own XBL
4. Establish that no existing UEFI port covers this SoC

**Gate:** every partition except `userdata` dumped and verified; DXE inventory extracted;
confirm by search that no `gauguin`/`SM7225`/`Bitra` UEFI port exists anywhere public.

**Result:** satisfied. 86 signed AArch64 DXE drivers recovered, `uefiplat.cfg` recovered,
no existing port found.

---

## P1 — Mainline Linux bring-up

**Why this and not UEFI directly:** a UEFI platform package is mostly a hardware
description — clock trees, regulator relationships, GPIO pins, panel timings, MMIO bases.
Writing that description blind, against a device that only has a 4.19 vendor kernel and no
schematics, is guesswork. Mainline Linux already has all of it **for the same silicon**:
`arch/arm64/boot/dts/qcom/sm7225.dtsi` + `sm6350.dtsi` describe SM7225 exactly, and
`sm7225-fairphone-fp4.dts` is a worked example for the same `msm-id 459`. Booting mainline
converts guesswork into measurement, and it is a small amount of work because the
bootloader is already unlocked.

Work:

1. Fetch a mainline kernel and `sm6350`/`sm7225` DTS support
2. Write `arch/arm64/boot/dts/qcom/sm7225-xiaomi-gauguin.dts` — clone the Fairphone 4
   board file, change panel, touch controller, regulators, and the `qcom,board-id`
3. Build `Image` + `dtb`, wrap into an Android boot image — with our tree in the
   boot image's DTB slot. That does **not** make ABL use it as-is: matching
   `msm-id`/`board-id` gets two of the six bits `CheckAllBitsSet` needs, and the
   tree declares no `pmic-id`, `softsku-id`, `platform-subtype` or `foundry-id`,
   so the vendor overlay is applied to our tree on every boot. The tree therefore
   carries the `/__symbols__` the overlay's fixups resolve against
   (`tools/make_dtbo_sinks.py`), and a payload built without it is refused before
   the kernel runs (`docs/07`). Add the pstore cmdline so the boot leaves a
   readable log at an address we choose, checked against the phone's own DRAM map
   and carveouts.
4. `fastboot boot boot.img` — nothing written to the device
5. Use `extract_dtb` / `/proc/device-tree` output as the hardware reference

**Gate:** the device boots mainline, prints to a serial console or on-screen framebuffer,
and enumerates UFS. That output is the input to P2.

**Risk:** moderate. Panel and touch are the fiddly parts; both have mainline drivers for
this SoC (`mdss`/`dsi` and `novatek-nvt-ts` respectively).

---

## P2 — UEFI skeleton

**Why:** this is the phase that decides whether the project is viable. Before writing any
Windows-specific code, UEFI has to run at all on this board.

Approach, mirroring what Mu-Silicium does for other SoCs:

1. Clone `Project-Silicium/Mu-Silicium` (Project Mu — BSD licensed, the community standard)
2. Create `Silicon/Qualcomm/BitraPkg` by adapting `RennellPkg` (SM7125) and `MooreaPkg`
   (SM7150) — the closest-spec existing packages
3. Create `Platforms/Xiaomi/gauguinPkg` from the same references, parameterised with the
   values in `04-uefi-platform-config.md`
4. Feed in the drivers extracted in P0 where the open-source equivalents are not needed
5. Compile the missing open-source pieces from `edk2-porting/edk2-msm`'s `QcomPkg`
   (`UsbBusDxe`, `UsbKbDxe`, `UsbMassStorageDxe`, `AdrenoDxe`)
6. Emit an Android boot image, `fastboot boot` it

**Gate:** the boot manager draws on the phone's screen, and the storage it lists includes
UFS as a block device. If this fails, stop and reconsider — everything downstream depends
on it.

**Status:** the first half of the gate is met and the second is not. Our firmware runs
and draws its own DEBUG stream on the panel — the whole of `docs/08` step 4.8 — and
then halts in `DxeMain` because an architectural protocol is missing. The boot manager
is not reached, so nothing is listed yet. Read the gate as "runs" (answered yes) and
"hands off to BDS" (open).

The gate originally said "`fastboot boot` shows the UEFI Shell". That was wrong twice
over, and `docs/07` has the evidence: this platform boots via `fastboot flash boot` and
not `fastboot boot` (different ABL code paths), and **no Mu-Silicium phone platform ships
the shell at all** — `ShellPkg/Application/Shell/Shell.inf` is referenced by no platform
under `Platforms/`. A gate the reference for this same `msm-id` cannot meet is not a gate.
The shell is a decision to revisit after the first execution; what the volume does
provide, and what the gate now asks for, is `BootManagerMenuApp` drawing on the panel.

**Risk:** **high, and this is the real wall.** No Bitra-family device has ever had a UEFI
port. The signed blobs are unlikely to load cleanly into a different DXE core on the first
attempt; expect a long debugging loop, and serial output is essential (the device has no
exposed UART — plan on the UEFI `ULogDxe` log buffer or on-screen debug). That is how it
went, and the on-screen half is the half that worked: the DEBUG build's console *is* the
framebuffer, so the firmware's own `DEBUG ()` strings are readable off the panel with no
UART and no shell. What it does not give is scrollback — the console clears itself when
it runs off the bottom — and it is write-only, so nothing can be read back after the
fact.

---

## P3 — Full UEFI with ACPI

**Why:** Windows does not read device trees. It needs ACPI tables describing the board, and
it needs the display, storage, USB host and input all working *inside UEFI* to install.

Work:

1. Add `Silicium-ACPI`'s Qualcomm Moorea/Rennell tables as a starting point, write the
   gauguin tables (DSDT/SSDT: UFS, XHCI, I2C, GPIO, buttons, thermal zones)
2. Bring up `DisplayDxe` + framebuffer
3. Bring up USB host (`UsbBusDxe`) — needed to install from a USB stick
4. Bring up `ButtonsDxe`, and a way to choose boot entries

**Gate:** a Windows 11 ARM64 installer boots off a USB stick and sees the internal UFS.

**Status (2026-09-25): item 1 is done for UFS, USB and the PMIC family, and the GPIO
controller is declared; it is still blocked for I2C, buttons and thermal zones.
2–4 are not started, and none of them can be assessed until P2 hands off to BDS.**
The tables exist, are wired into `gauguin.fdf`
and `gauguin.dsc`, and are in the firmware volume of the build behind the staged P2
payload — `DSDT` (2,275 bytes, gauguin's own, `SM7225`, eight `ACPI0007` CPU devices,
the UFS and USB nodes, `SPMI`/`PMIC`/`PM01` and `GIO0`), `APIC`, `FACP`, `FACS`, `GTDT`,
plus the shared `SSDT`. The tables were read back out of the built artifact, the `APIC`
parsed subtable by subtable — its two INTIDs and its redistributor base match this
board's device tree — and the `DSDT` decompiled back out of the volume so that `GIO0`
could be read as the firmware will see it rather than as it was written.
`docs/07`'s P3 groundwork section carries the detail. Note that the DSDT here is
smaller in scope than the list above — I2C, buttons and thermal zones are not
in it yet, and the `GIO0` controller is declared without the corpus's per-pin
interrupt catalogue because that catalogue is board data and this board's device
tree does not carry it — so item 1 is done for UFS, USB and the PMIC family and *not*
done for the rest.

**And the rest is blocked on an input, not on effort.** Every one of those nodes
needs an ACPI `_HID`, and a device tree does not carry ACPI names: it has registers
and pins, which is the half that is knowable. `tools/acpi-hid-census.py` measures what
the reference DSDTs supply, and across all 66 of them the same block at the same
address carries a different `_HID` on every SoC — the TLMM window `0xF100000` is
`QCOM1A0C` on vili, lemonade and venus, `QCOM0A0C` on lisa and a52sxq, `QCOM250C` on
alioth, `QCOM090C` on renoir, `QCOM0C0C` on Kailua. Both TSENS blocks are described
by none of the 66: the corpus has no thermal-sensor device of any kind. No
Bitra-family reference exists either: `Platforms/Realme/bitra/DSDT.aml`, the source
of this file's form, has the same five devices gauguin has and nothing more.

The count is not the point; the decomposition is. A Qualcomm scoped `_HID` is
`QCOM<family byte><block index>`, and the *index* is fixed per generation of the
table generator — measured across 66 tables, the GPIO controller is `..0C` under
every modern family and `..0D` under the three older ones, the arbiter is `..0B`
and `..0C` respectively. Ten of gauguin's twelve blocks therefore have a known
index in each measured generation, and the one input left was which family byte
SM7225 carries. That byte does not follow the marketing name — pipa and
alioth both declare `SDM8250` and carry 05 and 25 — so it has to be read off a
driver set, and `--drivers DIR` tries all 256 bytes against each measured index
table and reports the one that names all ten blocks.

**That byte has now been measured, and it is `0A`.** The set is the SC7280 /
Kodiak one, `WOA-Project/Qualcomm-Reference-Drivers` → `7280_CLS/200.0.4.0`,
112 `.cab` files fetched from Windows Update by the reference-laptop OEM and
extracted to 112 `.inf`. Under the modern index table it names **8 of gauguin's
10 blocks**, and 0 of 10 under each of the other 255 bytes:

| block | id | block | id |
|---|---|---|---|
| SE0u | `QCOM0A16` | SE5 | `QCOM0A10` |
| SE1 | `QCOM0A10` | SE7 | `QCOM0A10` |
| SE2 | `QCOM0A10` | SPMI | `QCOM0A0B` |
| SE3 | `QCOM0A10` | TLMM | `QCOM0A0C` |

The two misses are `SE0` and `SE6`, which the set does not name at all; it would
take `QCOM0A0E`, the correct index for a UART in that table. So the misses are
absences in the *set*, not contradictions of the byte — a Kodiak board simply
does not publish those two as ACPI devices. Two further checks agree: the only
two tables in the whole 66-table corpus that carry these ids are
`Platforms/Xiaomi/lisa` and `Platforms/Samsung/a52sxq`, both declaring `SDM7280`,
both with `Device (GIO0) { Name (_HID, "QCOM0A0C") }`; and all eight ids land on
the expected *kind* of block in the right bit positions — `qcpmicgpio7280.inf`
claims `QCOM0A2D` and `qcpmic7280.inf` claims `QCOM0A2B` plus `QCOM0AD3`, where
`0x2D & 0x7F = 0x53` is a PMIC sub-function. A collision would not organise itself
that way across eight independent ids.

Stated honestly: `0A` is measured on Kodiak (SM7325), which is SM7225's sibling in
the same table generation, and no SM7225 Windows driver set and no independent
SM7225 reference DSDT exists to confirm it on the die itself. It is a measurement
with two independent oracles, not a proof. It also does **not** name the TSENS
sensor block: the set has no thermal-*sensor* device id at all, and neither it nor
any of the 66 tables describes one. What the set does name is the thermal **zones**
— `qcthermalmdm7280.inf` claims the consecutive range `QCOM04B4`–`QCOM04CE`,
which contains `QCOM04C0`–`QCOM04C8`, the nine that Step 4.58 found fixed across
six tables in four families with no explanation. So the zones live in a *fixed*
`QCOM04xx` space rather than the family-byte space, and that observation now has a
driver behind it instead of only the corpus. It still does not say which of
gauguin's 40 device-tree zones takes which id.

Read that as the name being **a declaration rather than a hardware fact**: a device
binds to the `_HID` its `.inf` lists and to nothing else, so the tables are written
*to a driver*, not *to the SoC*, and the corpus disagreeing on every SoC is evidence
the choice is free rather than evidence that a correct name is missing. The driver
set was the missing input; it is now in hand and read, and it is what turns
"a plausible `QCOM`-prefixed name" into "the name a real driver will claim".
Copying a name from another SoC *without* a set does not fail loudly — a node whose
`_HID` no driver claims is absent from Device Manager, and the hardware behind it is
silently not there, which is worse than a node that is visibly missing. Note what
that does and does not gate, though: **there is no `.inf` gate on the UEFI phase.**
`_HID` is firmware-supplied, TianoCore's `AcpiTableDxe` auto-computes
`_CID = PNP0C02` for any `QCOM`-prefixed `_HID` in the reserved range, and the
build already ships `QCOM0497`/`QCOM0498`/`QCOM24A5` with no driver set at all — so
none of this can break P2. What it prevents is authoring names that are *silently
wrong for specific UMDF clients later*. This is the same mechanism P5 names as
"re-bind the WoA driver INF"; P3's tables are its first instance. `BTNS` looks like
an exception —
its `_HID` is a standard `ACPI0011` Generic Buttons Device, so no vendor INF is
needed for it — but its `_CRS` names `\_SB.PM01` as the controller its `GpioInt`
resources belong to, and a PMIC node carries the same family byte (`PM01` is
`QCOM<family>2D` in the modern tables and `QCOM<family>30` under 05 08 14). So
the buttons wait for the same byte the rest of the blocks do.

Items 2–4 were said to be gated on volume space: "**the payload has 760 bytes of free
volume**", so DisplayDxe, UsbBusDxe and ButtonsDxe "will not fit" beside the P2
instrumentation. **That is a misreading of the figure, and the gate is not there.**

`FVMAIN` is declared `BlockSize = 0x1000, NumBlocks = 0` in `gauguin.fdf:19-22`, so
GenFv sizes it to the next 4 KiB boundary above its content and the reported "free"
is the slack left in that last block. Measured, it is exactly that and nothing else:
the 2026-09-24 23:30 build reports `0x702d08` taken → `0x703000` → 760 free, today's
01:20 build `0x72ced0` → `0x72d000` → 304 free, and upstream `suryaPkg` `0x676d30` →
`0x677000` → 720 free. All three are `(-taken) % 4096`; the number is always in
[0, 4095] and carries no capacity information at all. `FVMAIN` in fact **grew by
172,032 bytes between those two gauguin builds** and would have grown further.

What the payload is actually bounded by is the enclosing volume: `SILICIUM_UEFI.fd`
is `FVMAIN_COMPACT`, a fixed 3 MiB region, and today it is **35.6% used with
2,025,744 bytes free**. That 3 MiB is not a Mu-Silicium convention either — it is
the device's own memory map, from the `uefiplat.cfg` recovered out of XBL at P0:
`0x9FC00000, 0x00300000, "UEFI FD"`, walled below by `ABOOT FV` (`0x9FA00000`,
`0x00200000`, ending exactly at `0x9FC00000`) and above by `SEC Heap`
(`0x9FF00000`), with BootShim's `_StackSize` and the FV header's `FvLength` both
equal to `0x300000`.

The refutation needs no arithmetic, though: the `USE_CUSTOM_DISPLAY_DRIVER=1` build —
the one that contains `DisplayDxe` — **has already been built and validated** in this
volume (`Mu-gauguin.img` 1,210,368 bytes, `FVMAIN` `0x753000`, 47 images). So P3 items
2–4 are gated on P2 reaching BDS unconditionally, which is a statement about when the
debug instrumentation can be deleted, not about whether their drivers fit.

Step 4.63 then measured the other half of this — where the failure *does* come from.
A valid 11,536,368-byte `DSDT` (generated by `tools/acpi-pad.py`) builds with `FVMAIN
[99%Full] 18886656 (0x1203000) total, 18886408 used, 248 free` and `PROGRESS - Success`
— 2.57× the volume above, still "99% full" — while 2 MiB of incompressible noise in the
same slot fails as `the required fv image size 0x311bf0 exceeds the set fv image size
0x300000`, against `FVMAIN_COMPACT`. So the budget for a new ACPI node is that outer
volume's free space *after compression* (**2,053,056 bytes** as of this writing), and
the `[99%Full]` percentage is a rounding artefact in both directions.

**One consequence for the record-keeping this plan asks for:** a payload hash is a hash
of a build, not of the source. `Silicon/Silicium/SiliciumPkg/Sec/Sec.c:48` compiles
`__TIME__`/`__DATE__` into `Sec.efi`, which lives in the outer volume, so two clean
builds of one tree give two `Mu-gauguin-silicon-gzip.img` hashes and **one**
`FVMAIN.Fv` hash (`85f6f9fc…542b30`). Record the `FVMAIN.Fv` hash — `tools/fv-inventory.py
<img> --dump-fvmain PATH` — when the point is *what the build contains*, and the payload
hash only when the point is *which build*, as `probe-fingerprint.py` and the `boot`
control do.

---

## P4 — Windows deployment

1. Repartition: shrink `userdata`, create an ESP, and add the Windows partition layout
2. Fetch a Windows 11 ARM64 build (UUP dump) and deploy it to the device
3. First boot

**Gate:** Windows desktop appears.

**Point of no return:** this phase destroys `userdata`. Everything the user wants to keep
must be off the device before it starts.

---

## P5 — Hardware enablement, one device at a time

In rough order of value-versus-difficulty:

| Order | Subsystem | Work |
|---|---|---|
| 1 | Storage, USB, power, buttons | should already work from P3 |
| 2 | Touchscreen | Novatek over SPI — a Windows HID miniport; several exist upstream |
| 3 | GPU acceleration | Adreno 619 `a6xx`; re-bind the WoA driver INF. CPU rendering until then |
| 4 | Wi-Fi (`wcn3990`) | hardest of the "should work" items |
| 5 | Bluetooth | follows Wi-Fi |
| 6 | Audio | needs a Windows equivalent of the ALSA UCM config |
| 7 | Sensors, vibrator | piecemeal |
| — | **Modem, cameras** | **not attempted — no driver exists** |

---

## Standing decisions

- **Never write to the device's storage until P4.** Everything through P3 runs via
  `fastboot boot`, which is non-destructive and instantly reversible.
- **Keep the backup current.** Any change to the partition layout invalidates parts of it.
- **Serial/log output before anything else.** A bring-up with no way to see what failed is
  not debuggable.
