# 07 — The UEFI platform package (P2)

P2 is the phase where the device stops being an Android phone with a weird
bootloader and becomes a machine that can run a UEFI operating system. It is
also, per `docs/00-plan.md`, "the real wall": no Bitra/SM7225 device has ever
had a UEFI port, so there is no working configuration to copy.

What follows is what was built, what it is made of, and — importantly — what is
still unverified.

## The approach: recover, do not reconstruct

Two things about this device change the shape of the problem.

**XBL is a UEFI firmware.** The phone's bootloader is not a bespoke
bootloader with a UEFI-shaped corner; it is a complete PI-spec firmware with 86
signed AArch64 DXE drivers inside it. `tools/xbl_extract.py` pulls them out.
That means the drivers which know how to talk to this exact UFS controller,
this exact PMIC, this exact display panel and this exact clock tree are already
on hand, signed by Qualcomm, and correct — because they are the ones the phone
already boots with every day.

**The phone describes itself.** Qualcomm ships `uefiplat.cfg` inside XBL: 74
memory regions, 33 configuration values, with addresses and sizes for this
board. It is the exact input its own UEFI build used.

So P2 is mostly a *packaging* problem. The hard part is not writing drivers for
unknown hardware; it is arranging known-good parts the way Mu-Silicium's build
expects. That is why almost everything under `uefi/` is generated rather than
written, and why the generator scripts are the real deliverable.

## What gets generated

```
tools/make_xbl_binaries.py   ->  uefi/Binaries/gauguin/
tools/make_uefi_platform.py  ->  uefi/Silicon/Qualcomm/BitraPkg/
                                uefi/Platforms/Xiaomi/gauguinPkg/
                                uefi/Resources/Configs/gauguin.toml
tools/sync-uefi-platform.sh  ->  installs all of it into a Mu-Silicium checkout
```

### Binaries/gauguin/

Of the 86 drivers in XBL, 55 are Qualcomm drivers that get packaged as binary
modules. The other 30 are generic EDK2 modules — DxeCore, RuntimeDxe,
VariableDxe, and so on — which must **not** be shipped as blobs: Mu-Silicium
builds those from source, and mixing a vendor's build of DxeCore with the rest
of the tree is a way to get a firmware that fails in ways nothing explains. The
generator names them separately so the omission is deliberate and visible
rather than silent.

One Qualcomm driver, `MiTokenDxe`, has no Mu-Silicium equivalent and is left
out entirely.

Each packaged driver gets a small `.inf` with a FILE_GUID derived
deterministically from its name. The reference packages carry vendor GUIDs
which cannot be reproduced, and they are not needed: the GUID identifies the
file inside the firmware volume, while the driver is loaded by its own internal
PE identity and its dependency expression.

### The driver load lists

`Include/DXE.inc` and `Include/APRIORI.inc` say which drivers go into the
firmware volume and in what order. Rather than invent an order, the generator
takes **suryaPkg**'s (`Xiaomi POCO X3 NFC / SM7150`, the closest sibling) and
rewrites it for gauguin, filtering to blobs that actually exist so the FDF
cannot reference a driver that is not there.

surya predates four drivers this SoC has — `PwrUtilsDxe`, `VcsDxe`,
`FeatureEnablerDxe`, `MacDxe`. Those are inserted at the anchor lines
**aliothPkg** (`SM8250`) puts them after, so their relative position is a
reference board's, not this generator's guess.

Eleven drivers are packaged but appear in neither reference list, so they are
**not** in the firmware volume. `QcomBds` is one of them, and that is correct —
Mu-Silicium uses EDK2's standard `BdsDxe` instead of Qualcomm's boot menu. All
eleven are named in `DXE.inc`'s header comment, because a driver that is built
and then silently omitted is exactly the kind of thing that costs a day later.

### MemoryMapLib and ConfigurationMapLib

These two libraries are the entire board description UEFI is given: the DDR and
MMIO map, and the platform tuning values. They are pure data, and hand-
transcribing 74 regions is a reliable way to introduce a hang that presents as
a blank screen.

`tools/make_uefi_platform.py` reads `uefiplat.cfg` and emits them. The
self-consistency check is that the emitted descriptor count matches
Qualcomm's own `MaxMemoryRegions = 74` — 18 DDR regions plus 56 register
regions.

One difference is deliberate and worth recording: `uefiplat.cfg` calls the GIC
distributor `APSS_GIC600_GICD`, while mainline calls it GIC500. Same address
(`0x17A00000`), same device, a marketing name in one place and a driver name in
the other.

### PlatformSecLib

SEC runs before there is a driver model, and this is where the watchdog gets
turned off. Skip it and the phone resets a few seconds into the firmware, which
looks exactly like a bad image.

The watchdog register offset is **verified rather than inherited**: the mainline
device tree declares the watchdog as

```dts
compatible = "qcom,apss-wdt-sm6350", "qcom,kpss-wdt";
```

and `drivers/watchdog/qcom-wdt.c` matches on the *second* compatible, giving
`match_data_kpss` and therefore `WDT_EN = 0x8`. (The driver's other layout,
`match_data_apcs_tmr`, puts it at `0x40` — that is for the older
`qcom,kpss-timer`, and picking it would leave the watchdog running.)

The MDP stream IDs handed to `ArmSmmuDetach` are **not** verified. Only `0x800`
is confirmed — it is the SID the SM6350 device tree gives the display
subsystem. The remaining seven are inherited from MooreaPkg, which is the same
generation of display block. Detaching a SID that is not in use is harmless, so
the risk is a missing one rather than an extra; the comment in the source says
so and names P5 as where to confirm it.

### The device tree

`build_uefi.py` appends `Resources/DTBs/gauguin.dtb` to the UEFI image, and the
Android bootloader validates that DTB's `qcom,msm-id` before it will accept the
image at all. So this must be *our* device tree — the one from
`dts/sm7225-xiaomi-gauguin.dts` with `<434 0x10000>, <459 0x10000>` — and not a
placeholder. The sync script prefers the kernel build's output and falls back to
running `cpp` and `dtc` itself.

### Two bugs in Qualcomm's own data, found by the build

**Bitmap offsets.** Seven bitmaps extracted from XBL — the battery and thermal
indicator images — declare a `bfOffBits` exactly **2 bytes short** of where
their pixel data begins. Everything else about them is a standard BMP, and the
correct offset is not a guess: it is `filesize - height * stride`, which the
file size, dimensions and a 4-byte-aligned row stride all independently agree
on. EDK2's decoder would have read 2 bytes early and sheared every row.
`make_xbl_binaries.py` corrects the field when it packages them and leaves the
extracted originals in `device/dxe/` untouched.

**Missing Android udev rules on the build host** — see `docs/06-host-usb.md`.

## What was verified, and what was not

**Verified:**

- The build completes: 47 images validated, `Return Code: 0x00000000`.
- `Mu-gauguin.img` is produced, 1,210,368 bytes, as an Android boot image.
- The firmware volume is well formed: `EFI_FV_TOTAL_SIZE = 0x753000`,
  `EFI_FV_TAKEN_SIZE = 0x752050`, 129 FFS files.
- 44 of the 55 packaged Qualcomm drivers are present in that volume. The 11
  absent are precisely the ones the generator reports as unlisted, so the two
  agree.
- The FD is exactly `FD_SIZE` = `0x300000`, matching the "UEFI FD" region in
  `uefiplat.cfg`, and BootShim is compiled with `FD_BASE=0x9fc00000`, also
  matching.
- The device tree lands in the image with the right `qcom,msm-id`.

**Not verified — and this is the whole of the P2 gate:**

- **The firmware has never run.** It has never been loaded by a bootloader and
  it has never executed an instruction. Everything above is static inspection
  of a build product.
- The ACPI tables are absent by choice. The surya DSDT describes a different
  board, and shipping it would tell any OS that boots here a set of confident
  lies about where the interrupt controllers and UART are. `AcpiTableUpdate`
  is a deliberate no-op with a P3 TODO. The P2 gate is a shell, which does not
  need ACPI; Windows does, which is why that is P3.
- The MDP SIDs, as noted above.

## Status

The P2 gate is **"UEFI boots to a shell on the phone"**, and it is **open**.
The firmware builds; it has not been observed to run.

## The first attempt to run it, and what it established

`Mu-gauguin.img` was built, verified byte by byte, and loaded with
`fastboot boot` — the method that writes nothing to the device. The transfer
succeeded:

```
Sending 'boot.img' (1182 KB)                       OKAY [  0.041s]
Booting                                            OKAY [  0.134s]
```

**The firmware did not run.** The evidence is that XBL never left fastboot:

- `fastboot getvar all` still answered, seconds later, with the complete XBL
  variable table (`variant: SM_ UFS`, a `token` whose base64 decodes to
  `...gauguin`). A bootloader that had jumped to the payload would not be
  listening.
- `usb 3-1` never re-enumerated. A chain-load either re-initialises USB (new
  enumeration) or crashes (reset, new enumeration). Neither happened.
- The screen stayed on the Redmi logo, which **is** XBL's fastboot screen on
  this device — not a crash screen.

So `Booting OKAY` means "the instruction was accepted", not "control
transferred". Repeating it after `fastboot reboot-bootloader` (the workaround
recorded for the P1 attempts) produced the same result, so this is not the
stale-state problem that troubled P1.

### A structural fact worth knowing: ABL is AArch32

Carved from the backup and read directly:

| partition | format | architecture |
|---|---|---|
| `xbl` | ELF64 | AArch64 |
| `tz`, `hyp`, `devcfg` | ELF64 | AArch64 |
| `abl` | **ELF32** | **ARM (AArch32)** |
| `aop` | ELF32 | ARM (AArch32) |

`abl` is the Android Bootloader — the component that serves the `fastboot`
protocol. On this device it is a **32-bit ARM** executable. That is a real
constraint on any chain-load: whatever ABL hands control to, it does so from
AArch32, and a payload that assumes it was entered in AArch64 state will fault
immediately. On a device with no UART that presents as "nothing happened".

This is offered as the leading hypothesis for the failed jump, **not as an
established cause.** ABL clearly can start a 64-bit kernel in normal operation,
so it has an AArch64 transition; whether its `fastboot boot` path takes that
transition, or takes it with the same entry conventions a chain-loaded payload
needs, is not known.

### Why the supported path is probably `fastboot flash boot`

`Mu-gauguin.img` is not an arbitrary payload. It is an Android boot image, and
BootShim is built with `REQUIRES_KERNEL_HEADER=1`, which makes it carry the
literal `ARM\x64` kernel magic. Those are the two things a bootloader's
**normal** boot path checks when it loads the `boot` partition. The image is
shaped to be accepted from `boot`, not to be chain-loaded from RAM.

The reference build agrees: `Mu-surya.img` and `Mu-gauguin.img` have identical
header layout (`header_version = 1`, `page_size = 0x800`,
`kernel_addr = 0x10008000`), which is the framework's expectation and not this
device's. The stock boot image differs on every one of those
(`header_version = 2`, `page_size = 0x1000`, `kernel_addr = 0x8000`) — and ABL
evidently accepts both, since it boots the stock one and accepts ours as a
download.

**This has a consequence that is the user's call, not this project's:** passing
the P2 gate may require `fastboot flash boot`, which writes to the device —
something the P0 discipline deferred to P4. Two things make it far less
frightening than it was when that rule was written:

1. `boot` is now backed up and verified (`part-boot.img`, `ANDROID!` magic),
   which it was not until this session.
2. There are **no A/B slots** — `fastboot getvar current-slot` returns
   `GetVar Variable Not found` — so there is exactly one `boot`, and restoring
   it is a single command.

The restore path is `fastboot flash boot ~/backup/gauguin/images/part-boot.img`.

## A warning about aborted transfers

A diagnostic attempt to `fastboot boot` the stock recovery image (128 MB, known
good) **wedged the device**: the transfer stalled at
`Sending 'boot.img' (131072 KB)`, a 180-second timeout killed the host side, and
XBL was left waiting for data it would never receive. It then ignored every
subsequent `fastboot` command until a 20-second power-button reset.

Two lessons:

- **Do not abort a `fastboot` transfer.** The protocol has no cancel; killing
  the client strands the bootloader mid-download.
- **The Thunderbolt port cannot do large transfers.** 1.2 MB completed in
  0.041 s; 128 MB did not complete at all. That port drops its PCIe link
  roughly once a minute, and every drop kills the transfer. P4 moves several
  gigabytes. It must not be used.

`boot` was never at risk in either incident: `fastboot boot` writes nothing, and
the wedge was in ABL's download state machine, not in the partition.
