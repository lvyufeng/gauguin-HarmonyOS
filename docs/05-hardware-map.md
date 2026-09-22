# 05 — Hardware map, measured from the device

Everything here comes from this phone's own `/proc/device-tree` (dumped with
`tools/device-backup.sh`), cross-checked against the panel XMLs and config in
`device/`, and compared with mainline's `sm7225-fairphone-fp4.dts`.

The purpose is to convert "we think gauguin is like a Fairphone 4" into a table
of facts we can build a UEFI platform package from. The headline result is that
**the SoC-level infrastructure really is identical** — not just similar.

## Power: the decisive comparison

Resolving the supply phandles in the device's own tree gives the RPMh regulator
each block is wired to. Mainline names the same regulators with a suffix letter
that encodes the PMIC (`a` = PM6350, `e` = PM6150L):

| Consumer | Device tree says | RPMh node | Mainline name | FP4 uses |
|---|---|---|---|---|
| UFS `vcc` | `regulator-pm6150a-l7` | `ldoe7` | `vreg_l7e` | `vreg_l7e` ✅ |
| UFS `vccq2` | `regulator-pm6350-l12` | `ldoa12` | `vreg_l12a` | `vreg_l12a` ✅ |
| UFS PHY `vdda-phy` | `regulator-pm6350-l18` | `ldoa18` | `vreg_l18a` | `vreg_l18a` ✅ |
| UFS PHY `vdda-pll` | `regulator-pm6350-l22` | `ldoa22` | `vreg_l22a` | `vreg_l22a` ✅ |
| Panel `vddio` | `regulator-pm6150a-l1` | `ldoe1` | `vreg_l1e` | (FP4 uses its own panel) |

UFS PHY current limits as the device declares them: `vdda-phy` 62,900 µA,
`vdda-pll` 18,300 µA. FP4 uses 800,000 µA for `vcc`/`vccq2`, which is what we
should carry over.

The vendor names `pm6150a` are misleading — the regulator *node* names (`ldoe7`,
`ldoa12`) are the authoritative ones, and they line up exactly with FP4.

## Identity

| | Device tree | Mainline FP4 | Match |
|---|---|---|---|
| `qcom,msm-id` | `<434 0x10000>, <459 0x10000>` | `<434 0x10000>, <459 0x10000>` | **identical** |
| `qcom,board-id` | `<35 0>` | `<8 32>` | differs (expected) |
| `compatible` | `qcom,lagoon-qrd`, `qcom,lagoon`, `qcom,qrd` | `fairphone,fp4`, `qcom,sm7225` | vendor vs mainline naming |
| `soc_id` | 459 | 459 | **identical** |

The `msm-id` list being byte-identical is what makes the FP4 board file a valid
starting point rather than a guess.

## PMICs

Present on the SPMI bus at `soc/qcom,spmi@c440000` — the same set FP4 uses:

```
pm6150l@4   pm6150l@5   pm6350@0   pm6350@1   pm7250b@2   pm7250b@3   pmk8350@6
```

## Storage — UFS 3.1

```
ufshc@1d84000      compatible qcom,ufshc              status ok
                   reg 0x1d84000+0x3000, 0x1d90000+0x8000
                   interrupts 0 0x109 4
                   clocks: core_clk bus_aggr_clk iface_clk core_clk_unipro
                           core_clk_ice ref_clk tx_lane0_sync_clk
                           rx_lane0_sync_clk rx_lane1_sync_clk

ufsphy_mem@1d87000 compatible qcom,ufs-phy-qmp-v3     status ok
                   reg 0x1d87000+0xe00
                   clocks: ref_clk_src ref_clk ref_aux_clk
```

Mainline equivalents: `qcom,sm6350-ufshc` and `qcom,sm6350-qmp-ufs-phy`.

## Display

The panel is an **IPS LCD**, not AMOLED — confirmed twice over: the phone's XBL
ships `Panel_J17_36_02_0a_lcd_dsc_vid.xml`, and the DT panel node reports
`qcom,mdss-dsi-bl-pmic-control-type = bl_ctrl_wled` (WLED backlight).

Active panel node: `qcom,mdss_dsi_j17_36_02_0a_dsc_video`
(`qcom,mdss-dsi-default-panel` on `qcom,dsi-display-primary` points at it)

| Property | Value |
|---|---|
| panel name | `xiaomi 36 02 0a video mode dsc dsi panel` |
| model | `xiaomi 36 02 0a VIDEO PANEL` |
| resolution | 1080 × 2400 |
| framerate | 120 Hz |
| mode | `dsi_video_mode`, `burst_mode` |
| bpp | 24 |
| h front / back / pulse | 50 / 40 / 12 |
| v front / back / pulse | 33 / 30 / 2 |
| `t-clk-post` / `t-clk-pre` | 13 / 47 |
| reset sequence | `(1, 5, 0, 1, 1, 10)` |
| backlight | `bl_ctrl_wled`, levels 2 … 4095 |
| blackness level | 3230 |
| physical size | 69.50 mm × 154.44 mm |

GPIOs:

| Signal | Controller | Pin |
|---|---|---|
| panel reset | `soc/qcom,spmi@c440000/qcom,pm6150l@4/pinctrl@c000` | 9 |
| tear effect (TE) | `soc/pinctrl@f100000` (TLMM) | 23 |

Display controller blocks: `qcom,dispcc@af00000` (`DISP_CC`),
`qcom,mdss_mdp`, `qcom,mdss_dsi0_ctrl`, `qcom,mdss_dsi0_pll`,
`qcom,mdss_dsi_phy0`, `qcom,dsi-display-primary`.

The bootloader's framebuffer lives at **`0xA0000000`**, matching the
`Display Reserved` region (36 MB) declared in `uefiplat.cfg`. A 1080×2400×4
frame is 10.4 MB, so it fits comfortably.

## Touch — Goodix over SPI, and a conflict to resolve

```
soc/spi@880000          compatible qcom,spi-geni, reg 0x880000+0x4000, status ok
  └── touch_spi@0       compatible xiaomi,spi-for-tp, spi-max-frequency 10 MHz
```

The controller itself is a **Goodix** part. Xiaomi does not drive it from a
normal kernel input driver — the bound SPI driver is `touch_xsfer` (a transport
shim) and events arrive through **`uinput-goodix`**, i.e. a userspace daemon
speaking the Goodix protocol over `touch_xsfer` and injecting into `uinput`.
Consequences:

- The chip speaks a proprietary Goodix protocol over SPI, not HID-over-I2C, so
  P5 will need a real driver rather than a re-bind of an existing one.
- The DT also declares `focaltech@38` on `i2c@988000`, so Xiaomi supports a
  second touch vendor on this platform. **This unit has Goodix** (that is what
  `input1` reports on the running system), which is what the port has to target.

Mainline `sm6350.dtsi` declares **`i2c0` at the very same address `0x880000`**,
because the GENI serial engine can be strapped as I2C, SPI or UART and upstream
only ever described the I2C configuration. On gauguin, SE0 is a **SPI**.

Touch is **not** required for the first boot, so the DTS generated in P1 leaves
it out; recorded here for the follow-up.

### QUIP serial engines as this device actually straps them

Read from each node's `status` on the device — only the `ok` ones are real:

| Address | Block | Status | Attached |
|---|---|---|---|
| `0x880000` | `spi@880000` | **ok** | `touch_spi@0` (Goodix) — **mainline calls this `i2c0`** |
| `0x888000` | `spi@888000` / `i2c@888000` | disabled | — |
| `0x980000` | `spi@980000` / `i2c@980000` | disabled | — |
| `0x984000` | `i2c@984000` | **ok** | `cs35l41@40`, `cs35l41@41` (Cirrus Logic speaker amps) |
| `0x988000` | `i2c@988000` | **ok** | `focaltech@38` (touch, unused here), `nq@28` (NXP NFC) |
| `0x98c000` | `spi@98c000` | **ok** | `irled@0` (`ir-spi`) |
| `0x990000` | `i2c@990000` | **ok** | `aw8624_haptic@5A`, `bq25970-standalone@66`, `fsa4480@42`, `pm8008@8`/`@9`, `smb1396@34/@35`, `smb1398@36` |

Only `0x880000` collides with mainline; the rest either match or are simply
disabled upstream.

## USB

```
soc/ssusb@a600000     supplies: dpdm-supply, USB3_GDSC-supply
soc/qusb@88e3000      supplies: vdda18-supply, vdd-supply, refgen-supply, vdda33-supply
```

Register block `USB30_PRIM` at `0x0A600000` per `uefiplat.cfg`. Mainline drives
this as `usb_1` / `usb_1_dwc3` with `qcom,sm6350-dwc3`.

## Wi-Fi / Bluetooth

`wcn3990`, with the Bluetooth firmware node `bt_wcn3990` present in the tree and
`vendor.qcom.bluetooth.soc = cherokee` in the Android properties. This is the
same combo chip FP4 uses, which is why FP4's `&wifi` regulator block is a usable
starting point.

## Runtime PM / interconnect

The device tree also carries `ad-hoc-bus`, `apps-smmu@15000000`,
`arm,smmu-kgsl@3d40000`, `cache-controller@9200000` and the RPMh RSC at
`18200000` — all matching mainline `sm6350.dtsi`, and all matching the
`SMMU` / `APSS_RSC_RSCCR` entries in `uefiplat.cfg`.

## What this buys P2

The UEFI platform package has to describe the board to Windows. From the above,
the following are now *measured* rather than assumed: UFS base/size/interrupt,
UFS PHY base and its four supply rails, the display controller bases, the panel's
exact timing set and reset/TE GPIOs, the PMIC inventory and SIDs, the USB base
addresses, and the full register map from `uefiplat.cfg`.

The two things still missing for a complete P2 are the **GPIO pin assignments
per peripheral** and the **ACPI table contents** — both of which P1's boot will
let us verify empirically rather than guess.
