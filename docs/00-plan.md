# 00 — Phase plan

Goal: **Windows 11 on ARM running on the gauguin test phone**, with as much of the hardware
driven as is physically possible. See [`README.md`](../README.md) for the honest ceiling —
cellular and cameras are permanently out of reach, Wi-Fi/audio/GPU are hard but plausible.

Each phase ends with a commit. A phase is only "done" when its gate has actually been
observed on hardware, not when the code compiles.

---

## P0 — Survey, backup, firmware inventory ← **current**

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
3. Build `Image` + `dtb`, wrap into an Android boot image
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

**Gate:** `fastboot boot` shows the UEFI Shell on the phone's screen, and UFS enumerates as
a block device. If this fails, stop and reconsider — everything downstream depends on it.

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
