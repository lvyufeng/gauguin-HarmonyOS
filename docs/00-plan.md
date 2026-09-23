# 00 — Phase plan

Goal: **Windows 11 on ARM running on the gauguin test phone**, with as much of the hardware
driven as is physically possible. See [`README.md`](../README.md) for the honest ceiling —
cellular and cameras are permanently out of reach, Wi-Fi/audio/GPU are hard but plausible.

Each phase ends with a commit. A phase is only "done" when its gate has actually been
observed on hardware, not when the code compiles.

## Where things actually stand (2026-09-22)

Nothing below is "done" except P0, and no gate has been observed on hardware
except P0's. This table is the honest state; the sections under it are the plan.

| phase | gate | state |
|---|---|---|
| **P0** survey + backup | partitions dumped and verified; no existing port | **done** — 74 partitions carved and signature-checked, `boot`/`abl`/`recovery` hashes match the device, 86 XBL drivers recovered, and `git ls-files Silicon/Qualcomm` confirms no SM7225 package upstream |
| **P1** mainline kernel | device boots mainline and prints something | **not done** — `work/out/boot-pstore.img` is built, reproducible (`make_boot_image.py --kernel`), and carries both channels a device with no UART needs: the panel itself (`simple-framebuffer` + `simpledrm` + fbcon, so the boot log is photographed off the screen) and pstore (`console-ramoops-0`, readable from Android after a warm reboot). The earlier `fastboot boot` was refused with `Failed to load/authenticate boot image` on the RAM path, and the partition path has never been tried |
| **P2** UEFI skeleton | the boot manager draws on the phone's screen and UFS appears as a block device | **gate open** — platform package built, image written to `boot` and verified byte-for-byte, **never observed to execute an instruction**. The gate said "reaches a shell" until the volume was inventoried and the shell turned out to be absent from *every* platform in the tree, `suryaPkg` included — see `docs/07`, so it would not have distinguished our firmware from a working one |
| **P3** ACPI | Windows installer boots and sees UFS | not started — `AcpiTableUpdate` is a deliberate no-op |
| **P4** Windows | desktop appears | not started — destroys `userdata` |
| **P5** peripherals | touch, Wi-Fi, GPU, audio | not started |

The commits so far are checkpoints inside P2, not a completed phase. Read the
phase state from this table, not from the commit titles.

### The one thing blocking progress

The phone needs a **physical reset** — hold the power button ~20s, then a normal
power-on with no keys held. The full sequence for that session, with what each
outcome means and which payload to try next, is
[`08-device-session.md`](08-device-session.md). The pieces it uses:

1. `tools/fastboot-capture.sh` — first thing it asks is `oem fbreason`, which
   reports why ABL entered fastboot and can say `Reason:LoadImageAndAuth Fail`
   or `Reason:BootLinux Fail`. Either of those means the payload was reached.
   Then `oem uefilog` / `lkmsg` / `lpmsg`, plus `slot-unbootable` /
   `slot-retry-count`. If ABL is silent it runs `tools/unwedge-fastboot.py`,
   which classifies *which* silence it is — a reply left unread (recoverable by
   draining the endpoint), a download left waiting (recoverable in principle),
   or a fastboot thread stuck behind a still-live USB stack (not recoverable;
   power button). The current state is the third, and it also means ABL is
   still resident, so the silence is not evidence our image ran.
2. `tools/restore-stock-boot.sh` — the A/B control: put the stock `boot` back
   and see whether Android returns.
3. `tools/flash-boot.sh` — the one command that writes a payload to `boot`, over
   whichever route answers. It exists because step 1b can make the fastboot route
   unreachable (the image in `boot` wedging ABL, so a reset reproduces the wedge)
   and TWRP is then not a fallback but the only way in; it also reads the
   partition back and compares the hash, so "the write landed" is established
   rather than assumed.
4. `work/out/boot-pstore.img` and its five siblings `-raw`, `-raw-txt`,
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
5. `work/out/p2-variants/Mu-gauguin-stock-{none,gzip}.img` — two stock-shaped
   builds, one per surviving candidate (uncompressed vs gzip kernel). Each pairs
   with a P1 variant on the compression property, which is what makes the pair —
   and not the individual attempt — the thing to read: see step 4b/4c in the
   runbook.

Those pieces are backed by four offline tools, which exist because a device cycle
is expensive and a bad image costs a physical reset. Every defect this project has
found in a payload was found by one of them rather than by the phone:

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
exposed UART — plan on the UEFI `ULogDxe` log buffer or on-screen debug).

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
