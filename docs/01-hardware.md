# 01 — Hardware

The device reports two different identities and only one of them is true.

## The identity trap

The phone is a Xiaomi **gauguin** (Redmi Note 9 Pro 5G / Mi 10T Lite / Mi 10i) running a
**ported Smartisan R2 ROM**. Most Android properties therefore report the *donor* device:

| Property | Reported value | Truth |
|---|---|---|
| `ro.product.brand` | `SMARTISAN` | Xiaomi |
| `ro.product.manufacturer` | `deltainno` | Xiaomi |
| `ro.product.model` | `DT2002C` | Redmi Note 9 Pro 5G |
| `ro.product.device` / `ro.product.codename` | `darwin` / `Smartisan R2` | `gauguin` |
| `ro.build.fingerprint` | `SMARTISAN/aries/aries:11/RKQ1.201217.002/...:user/dev-keys` | — |
| **`ro.product.board`** | **`gauguin`** | ✅ true |
| **`/sys/devices/soc0/hw_platform`** | **`GAUGUIN`** | ✅ true |
| **`/sys/devices/soc0/machine`** | **`SM7225`** | ✅ true |

Any script keyed on `ro.product.model` / `ro.product.device` / `ro.product.codename` will
conclude this is a Smartisan phone and refuse to proceed. Identify it by
`ro.product.board` or `sys/devices/soc0/machine`.

Separately, the Chinese name *"红米 Note 9 Pro"* is ambiguous: the 4G India/global model is
**curtana** (SM7125, Adreno 618), a completely different device from this one.

## SoC identity

```
/proc/device-tree/compatible : qcom,lagoon-qrd  qcom,lagoon  qcom,qrd
/sys/devices/soc0/chip_name  : SM_BITRA_H
/sys/devices/soc0/machine    : SM7225
/sys/devices/soc0/soc_id     : 459
/sys/devices/soc0/revision   : 1.0
/proc/cpuinfo Hardware        : Qualcomm Technologies, Inc SM7225
/sys/class/kgsl/kgsl-3d0/gpu_model : Adreno619v1
```

Firmware version strings, all agreeing on the BSP name:

```
BOOT.XF.3.3-00285-BITRALAZ-4      Variant: BitraPkgLAA
TZ.XF.5.10-00201-1                Variant: SAJAANAAA
MPSS.HI.2.0.1.c7-00168-BITRA_GEN_PACK-1.370163.15
ADSP.VT.5.6-00587-BITRA-1
CDSP.VT.2.6-00487-BITRA-1
NPU.FW.2.3-00046-BITRA_NPU_PACK-1
```

**The BSP codename is `Bitra`, not `lito`.** `ro.board.platform=lito` is a stale value in
Xiaomi's vendor config — `lito` is the SM7250 (Snapdragon 765G) BSP. Everything that
matters (XBL, TZ, modem, ADSP, CDSP, NPU) is built from `BitraPkg`.

This matters because `SM7225`/`Bitra` is the same SoC family as `SM6350` — and
**`SM6350`/`SM7225` is fully mainlined in Linux** (the Fairphone 4 is a `sm7225` device
with `qcom,msm-id = <459 0x10000>`, byte-for-byte the same `msm-id` this phone reports).
That upstream work is the hardware map this port will be built from.

## Spec

| | |
|---|---|
| SoC | Qualcomm SM7225 (Snapdragon 750G), BSP `Bitra` |
| CPU | 2× Cortex-A77 + 6× Cortex-A55 (arm64-v8a) |
| GPU | Adreno 619 |
| RAM | 8 GB LPDDR4X |
| Storage | 128 GB UFS 3.1 |
| Display | 1080×2400 IPS LCD, DSI, 430 dpi |
| Touch | Novatek, SPI (`NVT-ts-spi` driver bound in the running kernel) — confirmed against the live tree in docs/08 step 4.54; the Goodix part on this board is the fingerprint reader |
| Wi-Fi/BT | Qualcomm `wcn3990` (BT soc name `cherokee`) |
| Kernel (current) | `4.19.113-perf`, LA.UM.9.12.r1-08000-SMxx50.0 |
| Android | 11 / SDK 30, `ro.build.type=user`, arm64-v8a |
| Bootloader | **unlocked** — `ro.boot.verifiedbootstate=orange` |
| Partitions | dynamic (`super`), A-only, no A/B slots |
| Root | Magisk installed; `su` available to `adb shell` |

## Practical consequences

- **Unlocked bootloader + dynamic partitions + A-only** is the easy configuration: `abl`
  will boot any well-formed Android boot image, so all early testing is
  `fastboot boot <img>` with nothing written to the device.
- **`ro.build.type=user`** means `adb root` is refused; root comes from Magisk via
  `adb shell su -c ...`.
- **Kernel source** for a port would be `MiCode/Xiaomi_Kernel_OpenSource` branch
  `gauguin-r-oss` (LA.UM.9.12.r1-08000-SMxx50.0 — matches the running kernel).
- Mainline Linux reference: `arch/arm64/boot/dts/qcom/sm7225-fairphone-fp4.dts` plus
  `sm7225.dtsi` → `sm6350.dtsi`, PMICs `pm6350`, `pm7250b`, `pm6150l`, `pmk8350`.
