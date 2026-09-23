# gauguin-HarmonyOS

**Bringing Windows 11 on ARM to the Xiaomi Redmi Note 9 Pro 5G (codename `gauguin`, Snapdragon 750G).**

> The repository name is a leftover from an earlier goal (HarmonyOS). The active goal is
> now a full Windows on ARM port. The name is kept so the existing remote keeps working.

---

## What this is

A from-scratch Qualcomm platform bring-up. The device has **no existing UEFI port** — no
`edk2-msm`, `mu_aloha_platforms` or `Mu-Silicium` target covers `gauguin`, its SoC
(`SM7225` / BSP codename **Bitra**), or any Bitra-family device. Everything here is new
work, built on top of:

- the **signed DXE drivers already inside the phone's own XBL** (extracted — see `device/`)
- **mainline Linux support** for the sibling SoC `SM6350`/`SM7225` (Fairphone 4 is fully
  mainlined, same `msm-id 459`, same `qcom,sm7225` compatible)
- the **Project Mu / Mu-Silicium** firmware framework and its closest-spec reference
  platforms (`RennellPkg` SM7125, `MooreaPkg` SM7150)

## Read this first: what "all hardware working" can and cannot mean

The goal as stated is *"all hardware drivable, feature complete"*. That is **not fully
achievable**, and no amount of work changes it. Being precise about the ceiling up front:

| Subsystem | Outlook | Why |
|---|---|---|
| Display (DSI + Adreno 619) | ✅ achievable | mdss/sde is mainlined for SM6350; DisplayDxe already in our XBL |
| Storage (UFS 3.1) | ✅ achievable | `UFSDxe` in our XBL, mainline `ufs-qcom` works on SM6350 |
| USB (device + host) | ✅ achievable | `UsbfnDwc3Dxe`/`UsbDeviceDxe`/`UsbConfigDxe` present |
| Buttons, power, charging | ✅ achievable | `ButtonsDxe`, `PmicDxe`, `QcomChargerDxeLA` present |
| Touchscreen | ⚠️ likely | SPI Novatek panel; needs a Windows HID miniport (community has several) |
| Wi-Fi (WCN3990) | ⚠️ hard | needs a Windows driver for `wcn3990`; done for some devices, always painful |
| Bluetooth | ⚠️ hard | same chip, same class of problem |
| GPU acceleration | ⚠️ partial | Adreno 619 is `a6xx`; a WoA driver exists but needs INF re-binding; CPU rendering is the fallback |
| Audio | ⚠️ partial | needs a UCM-equivalent for Windows; often the last thing to work |
| Sensors, vibrator, flashlight | ⚠️ partial | piecemeal |
| **Cellular modem (calls / SMS / data)** | ❌ **never** | no public WoA driver for Qualcomm MDM/modem on Android hardware — universal across every WoA-on-phone project |
| **Cameras** | ❌ **never** | ISP is unsupported on WoA, no exceptions |

So the realistic end state is: **a Windows 11 ARM tablet** — screen, touch, storage, USB,
battery, Wi-Fi, GPU. **Not a phone.** No SIM functionality, no cameras, ever.

## Hardware summary

| | |
|---|---|
| Board | `gauguin` (Redmi Note 9 Pro 5G / Mi 10T Lite / Mi 10i) |
| SoC | Qualcomm **SM7225**, BSP **Bitra** (`SM_BITRA_H`), `soc_id 459` |
| CPU | 2× Cortex-A77 + 6× Cortex-A55 |
| GPU | Adreno 619 (`Adreno619v1`) |
| RAM / storage | 8 GB LPDDR4X / 128 GB UFS |
| Display | 1080×2400 IPS LCD, DSI |
| Firmware | XBL `BOOT.XF.3.3-00285-BITRALAZ-4` / `BitraPkgLAA` |
| Bootloader | **unlocked** (`verifiedbootstate=orange`) |
| Current OS | ported Smartisan R2 ROM (Android 11), `user/dev-keys`, **no public image exists** |

Full details in [`docs/01-hardware.md`](docs/01-hardware.md).

## The non-negotiable first step: back up everything

The Smartisan R2 port on this device is a `user/dev-keys` build with **no downloadable
image**. If a repartition or a bad flash destroys it, the device is a brick with no
recovery path. Every partition (except `userdata`) has been dumped to
`~/backup/gauguin/images/` — see [`docs/02-partitions.md`](docs/02-partitions.md) for the
map and the restore procedure.

## Phase plan

| Phase | Deliverable | Gate | Status |
|---|---|---|---|
| **P0** | Device survey + full partition backup + XBL driver inventory | every partition dumped; DXE set identified | ✅ done |
| **P1** | Mainline Linux on gauguin (`gauguin.dts` + kernel + `fastboot boot`) | framebuffer up, UFS mounted, USB console | **gate not observed** — image builds, `fastboot boot` was refused |
| **P2** | UEFI skeleton (`Silicon/Qualcomm/BitraPkg` + `Platforms/Xiaomi/gauguinPkg`) | boot manager draws on screen, UFS enumerates as a block device | **runs** — the image with the current device tree was written to `boot` and our firmware **executes**: the panel fills with its own DEBUG stream (`SerialPortLib` is the framebuffer in a DEBUG build) and then stops on `ASSERT [DxeCore] DxeMain.c(593)`, one architectural protocol short of handing off to BDS. Three earlier attempts stopped before reaching our code because the tree in the image had no `/__symbols__`. See `docs/08` step 4.8 for that, and step 4.9 for the eight missing protocols and the instrumentation that names the driver |
| **P3** | UEFI with full driver set + ACPI tables | Windows installer boots off USB | **groundwork** — the ACPI table set is decided (Moorea, on the GICC geometry; Kona, which the only Bitra-family platform file uses, matches nothing) and the DSDT's one known device-specific correction is identified (UFS at INTID 265, not bitra's 297). No tables built yet — `docs/07` has the decision and the four things still missing |
| **P4** | Windows 11 ARM64 deployment | Windows desktop on the device | not started |
| **P5** | Hardware enablement in Windows | touch / Wi-Fi / GPU / audio one by one | not started |

**Read the state from this table, not from the commit log.** Every commit so far
is a checkpoint inside P2; none is a completed phase. `docs/00-plan.md` has the
per-phase detail, and `docs/08-device-session.md` has the exact sequence for the
next time the phone is in hand. What is blocking is eight architectural protocols
— Security, Bds, Watchdog, Variable, Capsule, Monotonic, Reset, Real Time Clock —
that DXE never installs. The static pass got as far as it can: the dependency
expressions all resolve, the Apriori file is complete, the images are well-formed,
and the five providers that *did* install are exactly the first five providers in
Apriori order while the eight that did not are all far later in it — so the batch
broke somewhere among the twenty-one modules between them, which install no
architectural protocol and are invisible in the result. The failing driver is
therefore a runtime property, and the firmware has been instrumented to print
which one: one character per Apriori entry, in the order the dispatcher ran them,
`S` for an entry point that returned an error, `L` for one whose image failed to
load. `docs/08` step 4.9 has the line and how to read it.

The instrumentation is written into `boot` and was booted on 2026-09-23; the
reading is outstanding. A later attempt to read it off a photograph of the panel
established only that the photograph is not the panel — near-black is 0.16% of the
frame and no 64×64 block anywhere is darker than 30% of full scale, where a
framebuffer console would make near-black dominate — so the control string still
has not been captured. `docs/08` step 4.11 has the measurements, and `boot` was
re-read and verified to still hold the same image, so re-taking the reading needs
no flash. The depex reading of the same evidence is dead —
four of the eight are `[Depex] TRUE` and the other four need only Timer and
Variable, all installed well before them — so what is left is that something in
the middle of the batch breaks shared state and returns success anyway, which no
dispatch-count line can see. `docs/08` step 4.10 is the experiment that separates
the two readings: `tools/build-apriori-variant.sh` rebuilds the platform with the
eight moved ahead of the Qualcomm block and nothing else changed — the two
decompressed volumes differ in 554 bytes, all of them inside the Apriori file's
GUID array — and `tools/apriori-order.py` proves the flashable image carries the
order that was asked for. If the eight install there, the order was the cause; if
they fail again, they fail wherever they sit.

P0 result: all 38 partition images dumped and size-verified against the GPT; **86
signed AArch64 DXE drivers** and Qualcomm's own `uefiplat.cfg` recovered from the
phone's XBL; confirmed by search that no existing UEFI port covers this SoC.

P1 looks like a detour but is not: it is the cheapest way to obtain a verified hardware
map (clocks, regulators, GPIO, panel timings, MMIO bases) and that map is exactly what the
P2 platform package has to encode.

## Layout

```
docs/       analysis and reference material
device/     config blobs extracted from this phone's own XBL (uefiplat.cfg, BDS_Menu.cfg, …)
tools/      scripts used to inspect the device and its firmware
```

## Safety

- `userdata` (107 GB) is **not** included in the backup — it holds user data and will be
  re-partitioned for Windows. Anything on the phone worth keeping must be copied off first.
- Restoring requires `fastboot` and the backups in `~/backup/gauguin/images/`. Do not
  re-partition `super` or `userdata` until the restore path has been tested.
