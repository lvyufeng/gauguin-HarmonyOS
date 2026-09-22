# 04 — The platform definition: `uefiplat.cfg`

Extracted from the XBL firmware volume (`device/config/uefiplat.cfg`, 216 lines). This is
Qualcomm's own description of the board's UEFI environment — the memory map, the MMIO
register map, and the platform tuning parameters. A port's platform package has to encode
exactly this information, so having the vendor's own numbers removes most of the guesswork
from P2.

## `[Config]`

```
Version = 3
MaxMemoryRegions            = 74
UnusableDDRMemoryAtBeginning = 1
UnusableDDRMemoryStartAddr   = 0x80000000
UnusableDDRMemorySizeAtBeginning = 0x600000
```

Note `UnusableDDRMemoryAtBeginning` — the first 6 MB at `0x80000000` is reserved from
Linux's perspective (TZ/secure region), which is why physical RAM starts at `0x80860000`.

## `[MemoryMap]` — DDR layout

| Base | Size | Label | Type | Attributes |
|---|---|---|---|---|
| `0x80860000` | 128 KB | AOP CMD DB | Reserv | uncached, XN |
| `0x80900000` | 2 MB | SMEM | Reserv | uncached, XN |
| `0x86000000` | 344 MB | PIL Reserved | Reserv | uncached, XN |
| `0x09200000` | 320 KB | LLCC0 | BsData | write-back, XN |
| `0x9B800000` | 35.4 MB | DXE Heap | Conv | write-back, XN |
| `0x9DB60000` | 4 MB | Sched Heap | BsData | write-back, XN |
| `0x9F800000` | 2 MB | FV Region | BsData | write-back, XN |
| `0x9FA00000` | 2 MB | ABOOT FV | Reserv | write-back, XN |
| **`0x9FC00000`** | **3 MB** | **UEFI FD** | BsData | write-back |
| `0x9FF00000` | 560 KB | SEC Heap | BsData | write-back, XN |
| `0x9FF8C000` | 4 KB | CPU Vectors | BsData | write-back |
| `0x9FF8D000` | 12 KB | MMU PageTables | BsData | write-back, XN |
| `0x9FF90000` | 256 KB | UEFI Stack | BsData | write-back, XN |
| `0x9FFF7000` | 32 KB | Log Buffer | RtData | write-back, XN |
| `0x9FFFF000` | 4 KB | Info Blk | RtData | write-back, XN |
| `0xA0000000` | 36 MB | **Display Reserved** | Reserv | write-through, XN |
| `0xA2400000` | 128 MB | Kernel | Reserv | write-back, XN |
| `0xAFAA0000` | 20 MB | DBI Dump | RtData | write-back, XN |

The `UEFI FD` region at `0x9FC00000` is where the firmware volume found in XBL is linked
to run. That VA is the fixed load address a replacement UEFI image has to match.

## `[RegisterMap]` — MMIO bases that matter for a port

| Base | Size | Block |
|---|---|---|
| `0x00100000` | 2 MB | GCC (global clock controller) |
| `0x01D80000` | 128 KB | **UFS** |
| `0x01DC0000` | 256 KB | CRYPTO0 |
| `0x00790000` | 64 KB | PRNG_CFG |
| `0x03D7D000` | 48 KB | GPU_GMU_CX_BLK |
| `0x03D90000` | 36 KB | **GPU_CC** |
| `0x03D9A000` | 16 KB | GPU_CPR |
| `0x0F000000` | 16 MB | **TLMM** (pin control) |
| `0x08800000` | 2 MB | PERIPH_SS |
| `0x09980000` | 64 KB | NPU_CC |
| `0x0A600000` | 2 MB | **USB30_PRIM** |
| `0x0A800000` | 1.1 MB | USB30_SEC |
| `0x0AE00000` | 2 MB | **MDSS** (display controller) |
| `0x0AF00000` | 128 KB | **DISP_CC** (display clocks) |
| `0x0B2A0000` | 64 KB | PDC_DISPLAY |
| `0x0B4A0000` | 64 KB | PDC_DISP_SEQ |
| `0x0BA00000` | 2 MB | RPMH_BCM |
| `0x0C200000` | 64 KB | RPMH_CPRF |
| `0x0C222000` / `0x0C223000` | 4 KB each | TSENS0 / TSENS1 |
| `0x0C400000` | 40 MB | PMIC ARB SPMI |
| `0x17A00000` | 1.4 MB | APSS GIC600 (GICD) |
| `0x17A60000` | 1 MB | APSS GIC500 (GICR) |
| `0x17C00000` | 1.1 MB | QTIMER |
| `0x18200000` | 192 KB | APSS RSC / RSCC |
| `0x18280000` | 4 KB | SILVER_CLK_CTL |
| `0x18282000` | 4 KB | GOLD_CLK_CTL |
| `0x18284000` | 4 KB | L3_CLK_CTL |
| `0x15000000` | 2 MB | SMMU |
| `0x0C300000` | 1 MB | AOP_SS_MSG_RAM |
| `0x14680000` | 184 KB | IMEM |
| `0x146AA000` | 4 KB | IMEM Cookie Base |

`SILVER` / `GOLD` / `GOLDPLUS` clock domains confirm the 2+6 A77/A55 cluster layout.

## `[ConfigParameters]` — selected values worth knowing

| Key | Value | Meaning |
|---|---|---|
| `PlatConfigFileName` | `uefiplatLA.cfg` | LA = "Linux/Android" platform variant |
| `EnableShell` | `0x1` | **UEFI Shell is enabled** — a very convenient bring-up target |
| `DefaultBDSBootApp` | `LinuxLoader` | default BDS app on this platform |
| `DefaultChargerApp` | `QcomChargerApp` | shown when charging with the device off |
| `NumCpus` / `NumActiveCores` | 8 / 8 | all cores enabled in UEFI |
| `SharedIMEMBaseAddr` | `0x146AA000` | |
| `SpecialLogPartition` | `LOGFS:\` | maps to the `logfs` partition |
| `UfsSmmuConfigForOtherBootDev` | `1` | UFS SMMU config for non-boot devices |
| `SecurityFlag` | `0xC4` | `SecBootEnable(0x1)` is **off**; `CommonMbnLoad(0x4)`, `LoadSecApp(0x40)`, `LoadKeymaster(0x80)` on |
| `TzAppsRegnAddr` / Size | `0xC1700000` / 100 MB | TZ apps region |
| `ShmBridgememSize` | `0xA00000` | 10 MB |
| `EnableMultiThreading` | `1`, `MaxCoreCnt 8`, `EarlyInitCoreCnt 1` | |
| `AllowNonPersistentVarsInRetail` | `0x1` | |
| `EnableLogFsSyncInRetail` | `0x1` | |
| `EnableSecurityHoleForSplashPartition` | `0x0` | |

`SecBootEnableFlag` being off in the shipped config, combined with the unlocked
bootloader, is why an unsigned boot image will run.

## `[ConfigParameters]` — charging / SDCC

```
Sdc1GpioConfigOn  = 0x1E92      Sdc1GpioConfigOff = 0xA00
Sdc2GpioConfigOn  = 0x1E92      Sdc2GpioConfigOff = 0xA00
EnableSDHCSwitch  = 0x1
PwrBtnShutdownFlag = 0x0
```

These pin-config magic numbers are the kind of thing that is impossible to guess and would
otherwise take days to reverse — directly reusable in the platform package.
