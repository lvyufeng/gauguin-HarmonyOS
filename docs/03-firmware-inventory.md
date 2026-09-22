# 03 — Firmware inventory: the signed DXE drivers inside XBL

The single most valuable thing found in P0. Qualcomm's **XBL** is not just a bootloader —
it is a complete PI-spec UEFI firmware containing the entire signed DXE driver set for this
SoC. Those drivers already know how to drive this exact board, and they are what a port is
built on: nobody writes an Adreno/UFS/PMIC driver from scratch, they reuse Qualcomm's.

`tools/xbl_extract.py` recovers them from the device's own XBL dump.

## Where they are

```
LUN-sdb.img  →  partition `xbl`  @ 24,576, size 7,327,744 bytes
                 └─ aarch64 ELF
                     └─ PT_LOAD  seg @ file 0x80450, vaddr 0x9FC00000, 2,359,296 bytes
                         └─ EFI_FIRMWARE_VOLUME (FvLength 0x240000, fs GUID 8c8ce578-…)
                             ├─ SECURITY_CORE  (8af09f13-…,  229,400 B)  ← XBL core
                             ├─ FREEFORM       uefiplat.cfg  (2,823 B)
                             └─ FV_IMAGE       (9e21fd93-…, 2,035,185 B)
                                 └─ **gzip** (GUID-defined section 1d301fe9-…)
                                     └─ nested FV, 6,384,200 bytes
                                         └─ 118 FFS files: 86 PE32 drivers + config + panels
```

The VA of the firmware volume, `0x9FC00000`, matches the `"UEFI FD"` region declared in
`uefiplat.cfg` (`0x9FC00000, 0x00300000`) — the platform config and the binary agree.

Extracted to `device/dxe/` as `<Name>.efi` (bare PE32), `<Name>.ffs` (whole FFS file, for
rebuilding the volume) and `<Name>.raw` (config/panel payloads).

## The driver set — 86 PE32 images, all `Machine=0xAA64`, `Subsystem=11`


**Core & runtime** — `DxeCore`, `RuntimeDxe`, `ArmCpuDxe`, `ArmGicDxe`, `ArmTimerDxe`, `WatchdogTimer`, `CapsuleRuntimeDxe`, `MetronomeDxe`, `RealTimeClock`, `ResetRuntimeDxe`, `EmbeddedMonotonicCounter`, `RscRtDxe`, `SCHandlerRtDxe`, `VariableDxe`, `VcsDxe`, `ScmDxe`, `TzDxe`, `ShmBridgeDxe`, `PILDxe`, `PILProxyDxe`, `MinidumpTADxe`, `VerifiedBootDxe`, `SecurityStubDxe`, `SecRSADxe`, `ASN1X509Dxe`, `HashDxe`, `CipherDxe`, `MacDxe`, `RngDxe`, `MiTokenDxe`, `FeatureEnablerDxe`, `ChipInfo`, `PlatformInfoDxeDriver`, `DDRInfoDxe`, `SmemDxe`, `CmdDbDxe`, `ULogDxe`, `EnvDxe`, `FvDxe`, `FvSimpleFileSystem`, `DiskIoDxe`, `PartitionDxe`, `Fat`, `LimitsDxe`, `QcomWDogDxe`, `QcomMpmTimerDxe`, `NpaDxe`, `PwrUtilsDxe`, `RpmhDxe`, `CPRDxe`, `PdcDxe`, `DALSys`, `HALIOMMU`

**Clocks / PMIC / power** — `ClockDxe`, `PmicDxe`, `AdcDxe`, `TsensDxe`, `ChargerExDxe`, `QcomChargerDxeLA`, `QcomChargerApp`, `UsbPwrCtrlDxe`

**Storage** — `UFSDxe`, `SdccDxe`

**Buses** — `I2C`, `SPMI`, `GpiDxe`, `DALTLMM`, `HWIODxeDriver`

**Display** — `DisplayDxe`, `GraphicsConsoleDxe`, `FontDxe`, `HiiDatabase`

**USB** — `UsbfnDwc3Dxe`, `UsbDeviceDxe`, `UsbConfigDxe`, `UsbMsdDxe`

**Input / UI** — `ButtonsDxe`, `VibratorDxe`, `ConPlatformDxe`, `ConSplitterDxe`, `SimpleTextInOutSerial`, `DevicePathDxe`, `PrintDxe`, `EnglishDxe`, `QcomBds`

**DSP / audio** — `ADSPDxe`

## Not present, and what that means

Two things a Windows boot needs are **not** in the XBL driver set:

- **`UsbBusDxe` / `UsbKbDxe` / `UsbMassStorageDxe`** — USB *host* support. XBL only ever
  acts as a USB *device*, so it ships device-side drivers only. Windows setup needs host
  mode to read the installer from a USB stick. The community supplies open-source
  `UsbBusDxe`/`UsbKbDxe`/`UsbMassStorageDxe` in `edk2-porting/edk2-msm` under `QcomPkg`;
  these get compiled from source into the port.
- **`AdrenoDxe`** — GPU. Nothing in XBL initialises the GPU. Mu-Silicium adds an
  `AdrenoDxe` in source form, and the Windows-side acceleration comes from the Adreno
  WoA driver package, not from UEFI.

Everything else needed for a first boot is already present as signed binaries.

## Config and panel files recovered alongside the drivers

| File | Why it matters |
|---|---|
| `uefiplat.cfg` | the platform definition: full DDR memory map and MMIO register map. See `docs/04-uefi-platform-config.md`. |
| `BDS_Menu.cfg` | BDS menu entries, including Secure Boot toggling |
| `uefipil.cfg` | which images the peripheral image loader may load |
| `SecParti.cfg` | RPMB/GPT security partition map |
| `QcomChargerCfg.cfg` | charging profile config |
| `BATTERY.PROVISION` | battery profile (gauge coefficients, thermistor curve) |
| `Panel_*.xml` (19 files) | DSI panel timings. Includes `Panel_J17_36_02_0a_lcd_dsc_vid.xml` and `Panel_J17_42_02_0b_lcd_dsc_vid.xml` — the **LCD** (not AMOLED) panels, i.e. gauguin's. |
| `logo1.bmp`, `battery_symbol_*.bmp`, `tsens_*.bmp` | the graphics XBL draws on screen |

These are copied to `device/config/` (the panel XMLs stay in `device/dxe/`).

## Reproducing

```sh
python3 tools/xbl_extract.py ~/backup/gauguin/images/LUN-sdb.img device/dxe
```

