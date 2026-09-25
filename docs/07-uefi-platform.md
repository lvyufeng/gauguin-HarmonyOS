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

### The premise was checked, and it holds — but the name is a trap

The claim above is that no SM7225 UEFI port exists to copy, so this has to be
assembled rather than adapted. That was verified rather than assumed:
`git ls-files Silicon/Qualcomm` lists Mu-Silicium's 26 SoC packages — Kona,
Lahaina, Waipio, Moorea, Kodiak, Napali, Rennell and the rest — and **none of
them is SM7225 or SM6350**. `Silicon/Qualcomm/BitraPkg` is this project's, and
is untracked in the Mu-Silicium checkout.

The trap: Mu-Silicium already has a platform called **`bitra`** — and it is
`Platforms/Realme/bitraPkg`, the Realme GT NEO 2, which includes
`KonaPkg/KonaPkg.dsc.inc` and declares `0 = SM8250, 1 = SM8250-AB,
2 = SM8250-AC`. That is **Snapdragon 870, a Kona part**. This device's socinfo
also says `SM_BITRA_H`, for SM7225. Two unrelated things called bitra — a
vendor's *board* name in one case and Qualcomm's *silicon* name in the other.

So "there is a bitraPkg, this must be the one" is the mistake to avoid, and
`Silicon/Qualcomm/BitraPkg` in this project means SM7225 and only SM7225. The
package was named after the SoC codename the device reports; if that ever
causes confusion the rename is cheap and the `!include` is one line.

## What gets generated

```
tools/make_xbl_binaries.py   ->  uefi/Binaries/gauguin/
tools/make_uefi_platform.py  ->  uefi/Silicon/Qualcomm/BitraPkg/
                                uefi/Platforms/Xiaomi/gauguinPkg/
                                uefi/Resources/Configs/gauguin.toml
tools/sync-uefi-platform.sh  ->  installs all of it into a Mu-Silicium checkout
```

And one that reads the result back rather than producing it:

```
tools/fv-inventory.py  ->  lists what is really inside a built Mu-<device>.img
```

`fv-inventory.py` answers "is this driver in the image", which is not the same
question as "is it in the source tree", and which cannot be answered by grepping
the image — see "Reading the volume takes some care" below.

### Some of the firmware edits are not in this repository's tree

`work/` is ignored, so a Mu-Silicium or Mu_Basecore checkout living there is not
versioned here at all, and the edits made directly in those checkouts — the timer
frequency fallback, the boot menu, the USB bus TPL, the authenticated-variable
write guard, and the temporary P2 dispatcher instrumentation — would otherwise
exist only on this one machine and never reach the remote. They are carried as
`uefi/patches/mu-basecore-local.patch`, which is tracked, documents each hunk in
its own header, and is applied by `tools/sync-uefi-platform.sh` as part of the
sync (idempotently — the reverse-check tells it the patch is already in).

Regenerating it after editing the checkout:

```sh
tools/regen-mu-basecore-patch.sh
```

Not a bare `git diff >`, which is what the first version of this note said and
which is wrong: the header is hand-written prose that no diff contains, so a
redirect over the file deletes it. The header now lives in its own tracked file,
`uefi/patches/mu-basecore-local.header`, and the script concatenates the two and
then checks the result both ways — that it applies to a pristine upstream `HEAD`
and that it reverse-applies to the working tree it came from, which is exactly
what makes the sync idempotent.

Two things about that checkout that cost time to discover: `Mu_Basecore/.git` is a
**file**, not a directory, because it is a submodule of the Mu-Silicium checkout
and its git dir is `Mu-Silicium/.git/modules/Mu_Basecore`; and `git diff` there
reports the five source edits and nothing else, so an empty diff means the edits
were lost rather than that the tree is clean. The regeneration script refuses to
write an empty patch for exactly that reason. Both scripts ask git
(`rev-parse --git-dir`) rather than testing for the directory.

The patch is not committed into Mu_Basecore itself: that checkout's `origin` is
`microsoft/mu_basecore` and its HEAD is detached at the revision Mu-Silicium
pins, so a commit there would be neither pushable nor meaningful.

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

### The two build switches

Both live in `gauguin.dsc` and both wrap their INF lines in
`!if $(NAME) == N` inside `DXE.inc`, so one value switches a whole block and the
generated lists show where the block is.

`USE_CUSTOM_DISPLAY_DRIVER` is the display choice (`0` = `SimpleFbDxe`, `1` =
Qualcomm `DisplayDxe`) and is documented above and in `docs/08`.

`USE_XHCI_HOST_DRIVER` is `0` by default and brings up the USB **host**
controller, which is what P3 needs — a Windows installer has to arrive on a USB
stick. It is the one switch that changes not only what is in the volume but
where a driver came from: this phone's XBL has no host-controller driver at all
(no occurrence of the string `xhci` anywhere in `xbl.img`, while `usbfn`, the
device-mode driver, is there once), so `XhciPciEmulationDxe`, `XhciDxe` and
`UsbInitDxe` come from `Binaries/bitra/` — the SM7225 sibling, the same SoC this
platform's `BitraPkg` is built on. All three go into `DXE.inc` and none into
`APRIORI.inc`, which is a deliberate departure from bitra: a driver in the
a-priori batch has its dependency expression bypassed, and
`XhciPciEmulationDxe`'s is a conjunction of thirteen architectural protocols.
`docs/08` step 4.50 has the depex read out of the binary and the rest of the
reasoning.

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

The tree has to be ours — the one from `dts/sm7225-xiaomi-gauguin.dts`, declaring
`qcom,msm-id <434 0x10000>, <459 0x10000>` — and not a placeholder, because
`GetSocDtb()` walks the boot image's DTB region and picks the tree whose `msm-id`
matches the SoC; a tree that does not match is not fallen back to, it is
`NULL`, and `DTBImgCheckAndAppendDT` returns `EFI_NOT_FOUND` with "No match found
for Soc Dtb type".

Where that tree has to *be* is the part an earlier version of this section got
wrong, and it is not where Mu-Silicium's builder puts it. `build_uefi.py` has
exactly one way to ship a DTB — `append_dtb`, which concatenates
`Resources/DTBs/gauguin.dtb` onto the end of the kernel blob and lets
`kernel_size` cover it — and nothing downstream reads it there (below). The tree
is a declared region of its own, at the offset ABL computes from the page counts
in the header, which is what `tools/make_boot_image.py --profile stock` writes and
what `tools/build-p2-payloads.sh` builds. The sync script still generates
`Resources/DTBs/gauguin.dtb` from the kernel build's output (falling back to `cpp`
and `dtc`) because the UEFI package's own config references it, but it is not the
copy that reaches the phone.

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

Two builds exist and the numbers are different, so they are given separately.
**Simple** is `USE_CUSTOM_DISPLAY_DRIVER=0` (SiliciumPkg `SimpleFbDxe`) and is
what is flashed; **Qcom** is `=1` (Qualcomm `DisplayDxe`) and is the goal.

| | Simple (flashed) | Qcom |
|---|---|---|
| images validated | 48 | 47 |
| `Mu-gauguin.img` | 1,122,304 bytes | 1,210,368 bytes |
| FVMAIN total / taken | `0x702000` / `0x7015c8` | `0x753000` / `0x752050` |
| FFS files in FVMAIN | 122 | (not on disk any more) |
| packaged Qcom drivers present | 42 of 55 | 44 of 55 |

**Verified:**

- Both builds complete with `Return Code: 0x00000000`.
- The FD is exactly `FD_SIZE` = `0x300000`, matching the "UEFI FD" region in
  `uefiplat.cfg`, and BootShim is compiled with `FD_BASE=0x9fc00000`, also
  matching.
- The device tree lands in the image with the right `qcom,msm-id` and
  `qcom,board-id`.
- **The flashed image's firmware volume was enumerated and checked.** 122 FFS
  files, every one in state `EFI_FILE_DATA_VALID`. The tool that does it,
  `tools/fv-inventory.py`, reproduces GenFv's own `FVMAIN.Fv.txt` map exactly —
  122 offsets, 122 GUIDs, zero mismatches — which is what makes the rest of the
  numbers here trustworthy rather than plausible. That comparison is a mode of
  the tool rather than something that was done once by hand, so it stays
  re-runnable:

  ```
  python3 tools/fv-inventory.py work/out/p2-variants/Mu-gauguin-stock-gzip.img \
      --against work/uefi/Mu-Silicium/Build/gauguinPkg/DEBUG_CLANGPDB/FV/FVMAIN.Fv.txt
  ```

  It exits nonzero on a mismatch, and pointing it at `suryaPkg`'s map — the other
  platform's, which is a different volume — reports 95 mismatches and exits 1.
  A check that cannot fail is not a check, so that direction is tested too.
- Of the 42 drivers present in the flashed build: `UFSDxe`, the whole USB device
  stack (`UsbConfigDxe`, `UsbDeviceDxe`, `UsbfnDwc3Dxe`, `UsbMsdDxe`,
  `UsbPwrCtrlDxe`, plus EDK2's `UsbBusDxe`/`UsbKbDxe`/`UsbMassStorageDxe`),
  `SimpleFbDxe`, `GraphicsConsoleDxe`, `ConSplitterDxe`, `ConPlatformDxe`,
  `BdsDxe`, `BootManagerMenuApp`, `SetupBrowser`, `DiskIoDxe`, `PartitionDxe`,
  `Fat`, and the panel XML set. That is every driver a UEFI Interactive Shell
  needs, and it is present in the image that is on the phone.

**The 13 absent drivers are all accounted for**, and none is accidental:

- `DisplayDxe`, `CPRDxe` are behind `!if $(USE_CUSTOM_DISPLAY_DRIVER) == 1` in
  `DXE.inc`; `DisplayReEnablerDxe` is in the same block. They are absent
  *because* this is the SimpleFbDxe build, which is the check that the display
  switch does what it claims.
- The other 11 are the ones the generator reports as unlisted — packaged but
  given no INF line by the reference package. They are named in `DXE.inc`'s
  header comment so the omission stays visible. `PILDxe`/`PILProxyDxe`/
  `ADSPDxe` load firmware to DSPs, `QcomBds` is replaced by EDK2's `BdsDxe`,
  `VerifiedBootDxe`/`SecRSADxe` are authentication paths this build does not
  use. None is a prerequisite of the console.
- `VibratorDxe` and `QcomChargerApp` are the two in that list that are absent by
  choice rather than by irrelevance, and the difference matters for where they
  get picked up. The haptics part is real and driven on this board: `aw8624` is
  at `i2c@990000` address `0x5A` in the hardware map (`docs/05`), and the
  phone's *own* ABL log carries `VibratorDxe: aw8624 read id ok`. That line is
  ABL's firmware, not ours — a different build with a different `DXE.inc` — so it
  says the driver binds this part on this hardware, not that our volume has it.
  Neither driver is needed for a console, so neither is in P3's way; they are P5
  items (haptics; charging UI), and the blobs are already extracted.

`QcomWDogDxe` being in that list is the one worth naming explicitly: the
watchdog is disabled by `PlatformSecLib` in SEC, before the driver model
exists, which is why it does not need a DXE driver. If that SEC code were
wrong, the phone would reset a few seconds in — which is a distinguishable
outcome, not an invisible one.

**The shell the volume does not contain — and the reference platform does not
either.** The drivers a shell needs are all present, which is not the same as the
shell being present. `ShellPkg/Application/Shell/Shell.inf` appears in neither
`gauguin.dsc`, `gauguin.fdf`, `Include/{APRIORI,DXE,RAW}.inc`,
`QcomPkg/Extra.fdf.inc` nor `SiliciumPkg/Common.fdf.inc`, and no `PcdShellFile` is
set anywhere. The built `FVMAIN` was inventoried file by file to confirm it: 122
FFS files, 99 with UI section names — `BootManagerMenuApp`, `BDS_Menu.cfg`,
`SetupBrowser`, `GraphicsConsoleDxe`, `UFSDxe`, `UsbBusDxe`, `ULogDxe`, the panel
XMLs, `logo1.bmp` — and no `Shell.efi`.

The first reading of that was a platform omission. Searching the tree makes it
something else: **no platform under `Platforms/` ships `Shell.inf` at all.** The
only references are `ShellPkg/ShellPkg.dsc` (its own build) and
`Common/Mu_OEM_Sample/FrontpageDsc.inc` (a sample), and no DSC anywhere sets
`PcdShellFile`. So the shell is not a file this platform forgot; it is a file no
Mu-Silicium phone platform ships, including `suryaPkg` — the reference for this
same `msm-id`, and the image this project compared against byte for byte.

That matters because the phase plan states P2's gate as "UEFI reaches a shell on
the phone". **The gate as written is not one the reference platform for this SoC
meets either**, so failing to reach a shell would not distinguish our firmware
from a working one. What the volume does provide is the thing
`tools/make_uefi_platform.py:395` already calls "the difference between a working
firmware and a dead one" on a phone with no UART: `BdsDxe`'s
`PcdBootManagerMenuFile` resolves to `BootManagerMenuApp`, which draws
`[Volume Up] Boot Manager` on the panel during its timeout, with `SetupBrowser`
behind it.

So the honest form of the gate is **"the boot manager draws on the panel and the
storage it lists includes UFS"**, and the shell is an optional upgrade rather than
a missing piece. Two things are worth checking before treating it as free, which
is why it is not being done now:

* our build replaces the vendor BDS with edk2's — `QcomPkg/Drivers/QcomBds/QcomBds.inf`
  is commented out in `Include/DXE.inc:15` — so this board's own
  `{"EnableShell", 0x1}` (transcribed from `uefiplat.cfg` into
  `ConfigurationMapLib.c`, and `0x1` on every platform in the tree) is read by a
  binary that is not in the image. It is a fact about Qualcomm's firmware, not a
  request made of ours.
* adding `Shell.inf` means adding `ShellPkg` to the DSC's packages, building the
  shell's libraries, and pointing a boot option at
  `7C04A583-9E3E-4f1c-AD65-E05268D0B4D1` — a real change to the image, which is the
  wrong thing to spend on while the open question is still whether the image runs.

The shell is therefore recorded as a decision to make after the first execution,
not as a repair to make now. Nothing in the P2 gate depends on it.

**Was not verified when this was written, and is now verified the other way:**

- **The firmware has run.** At the time, it had never been loaded by a bootloader
  and never executed an instruction — everything above was static inspection of a
  build product, and enumerating the volume proved the software was *in* the image
  without saying anything about whether the image was reached. It has since been
  reached: the payload carrying the current device tree was written to `boot`, and
  the panel came up full of our own `DEBUG ()` output, ending in
  `ASSERT [DxeCore] DxeMain.c(593)` — a line of our source. `docs/08` step 4.8 has
  the record. The conclusion below, that enumerating a volume is not evidence the
  build works, still stands and is what made the distinction worth drawing.

- **The `boot` path and the `fastboot boot` path are different code.** This is
  not a caveat, it is a reason for optimism that deserves to be stated with the
  evidence. P1's mainline kernel was rejected by `fastboot boot` with `Failed to
  load/authenticate boot image: %r`, and for a long time that was read as
  evidence that the device refuses *our images*. It is evidence about one code
  path.

  Disassembling ABL's LinuxLoader shows what its `Kernel mode check 32/64` does:

  ```
  0x02b15c  ldr   w2, [x22, #0x38]      ; the u32 at offset 0x38 of the payload
  0x02b160  mov   w3, #0x5241
  0x02b168  movk  w3, #0x644d, lsl #16  ; w3 = 0x644d5241 = "ARMd"
  0x02b164  adrp  x1, <the message>
  ...                                   ; compare, and report
  ```

  That is the point of BootShim's `REQUIRES_KERNEL_HEADER=1`: the byte at offset
  0x38 of the payload is the ARM64 kernel magic. **Our images carry it, and the
  stock `boot` does not** — the stock kernel is raw and has no such header. So
  the two are distinguishable by ABL, and there is no reason to assume the
  partition path rejects for the same reason the RAM path did.

- The related string is `ERROR: Failed to switch to 32 bit mode` at VA
  `0x17638`. It is worth naming for the same reason: it makes a *32-bit* kernel
  path explicit, and no successful boot on this device would take it

- The ACPI tables are absent by choice. The surya DSDT describes a different
  board, and shipping it would tell any OS that boots here a set of confident
  lies about where the interrupt controllers and UART are. `AcpiTableUpdate`
  is a deliberate no-op with a P3 TODO — see the section below for what the sibling
  packages do instead. The P2 gate is the boot manager drawing
  on the panel and UFS enumerating, which does not need ACPI; Windows does, which
  is why that is P3.
- The MDP SIDs, as noted above.

### The volume is not short of room, and `AcpiTableUpdate` is a no-op in ours alone

Both of these were on the "what is missing" list as constraints and neither is one.

**`FVMAIN`'s reported free space is block-alignment slack.** `gauguin.fdf:19-22`
declares `BlockSize = 0x1000, NumBlocks = 0`, so GenFv sizes the volume to the next
4 KiB boundary above its content and "free" is the remainder in that last block. It is
therefore always in [0, 4095] and says nothing about capacity:

| build | `EFI_FV_TAKEN_SIZE` | `EFI_FV_TOTAL_SIZE` | reported free |
|---|---|---|---|
| gauguin, 2026-09-24 23:30 | `0x702d08` | `0x703000` | 760 |
| gauguin, 2026-09-25 01:20 | `0x72ced0` | `0x72d000` | 304 |
| upstream `suryaPkg` | `0x676d30` | `0x677000` | 720 |

All three are `(-taken) % 4096`. The two gauguin rows are 172,032 bytes apart: the
volume grew and the "free" figure fell, which is the opposite of what a capacity limit
does. What bounds the payload is the enclosing volume — `SILICIUM_UEFI.fd` is
`FVMAIN_COMPACT`, a fixed 3 MiB, today 35.6% used with 2,025,744 bytes free. And that
3 MiB is the device's, not this port's: `uefiplat.cfg:20` (recovered from XBL at P0)
declares `0x9FC00000, 0x00300000, "UEFI FD"`, walled below by `ABOOT FV`
(`0x9FA00000, 0x00200000`, ending exactly at `0x9FC00000`) and above by `SEC Heap`
(`0x9FF00000`), with BootShim's `_StackSize` and the FV header's `FvLength` both
`0x300000`. The decisive measurement is simpler still: the
`USE_CUSTOM_DISPLAY_DRIVER=1` build, which contains `DisplayDxe`, was built and
validated in this same volume at `FVMAIN` `0x753000` with 47 images.

**`AcpiTableUpdate` is a deliberate no-op, but that is our choice, not the platform's.**
All thirteen sibling packages implement `UpdateAcpiTables ()`, and every one of them
patches the DSDT and reinstalls it — ours is the only one of the fourteen that patches
nothing. Sizes run 1,188–11,685 bytes and `AslUpdateName` call counts run 2–32. The two
SoCs nearest SM7225 in the tree are the heaviest of the small ones: `KodiakPkg` (SM7325,
7,142 bytes) and `RennellPkg` (SM7125, 5,897) call `LocateTableBySignature` for the
DSDT, locate the ChipInfo, SMEM and PlatformInfo protocols, then write **32 named
fields** with `AslUpdateName (DsdtTable, SIGNATURE_32 (...), …)` — `SOID SKUV SDDR STOR
SIDV SVMJ SVMI SDFE SIDM SUFS PUS3 SUS3 SIDT SJTG EMUL SOSN PLST RMTB RMTX RFMB RFMS
RFAB RFAS TPMA TDTV TCMA TCML SOSI PRP0 PRP1 SIDS UAON` — and finish with
`ReinstallTable (DsdtTable, &DsdtHandle)`. Even the floor is not zero: `MolokaiPkg`'s
1,188 bytes read one value out of MMIO and write two fields.

So the machinery for patching a table at runtime is two directories away, on the SoCs
whose generation byte SM7225 most plausibly shares. Our source comment says the reason
is "there is no DSDT for gauguin yet" — that is now stale, since the DSDT exists and is
in the firmware volume. The live reason is narrower and better: all 32 of those fields
are modem, ADSP and TZ shared-memory values read out of SMEM, and `tools/acpi/gauguin.asl`
declares none of them. Editing that comment is deferred — a comment still moves debug
line numbers and so moves the hash of the staged payload, and the staged payload is what
the P2 gate is waiting on.

### Reading the volume takes some care, and got it wrong twice

Worth recording, because both mistakes produce the same convincing answer —
"the firmware volume is empty" — and neither is about the firmware.

**`SILICIUM_UEFI.fd` is not the firmware volume.** It is `FVMAIN_COMPACT`: a
0x300000-byte volume whose only substantial content is `FVMAIN`, a 7 MB volume,
inside a single LZMA GUIDed section. Every driver name and driver GUID is
therefore inside a compressed stream, so grepping the raw image for
`UsbConfigDxe` finds nothing — 0 occurrences, which reads exactly like a
firmware that shipped without USB. It did not; the name is just compressed.

**Two off-by-four errors in the FFS walk, both silent.** The file area does not
begin at `HeaderLength` when the volume has an extension header:
`ExtHeaderOffset + ExtHeaderSize` is only 4-byte aligned and FFS files must be
8-byte aligned, so GenFv pads to the next 8 — `0x60 + 0x14` rounds up to
`0x78`. And GenFv pads *between* files the same way, without counting that
padding in the size field, so stepping by `size` lands 4 bytes early and reads
a GUID of `FFFFFFFF-CB7F-D6A2-186A-2F4EB43B9920` — the previous file's last
four bytes glued to the next GUID. Each mistake ends the walk after one or two
files.

The fix for all of it is to check the reader against something the reader
cannot influence: GenFv writes its own map, `FVMAIN.Fv.txt`, into the build
tree. The walker now has to reproduce that map exactly or it is not believed.

There is a second trap for the same class of mistake: **counting packaged
drivers by directory name instead of by `.inf` name**. `ScmDxeLA.inf` and
`TzDxeLA.inf` both live in `QcomPkg/Drivers/TzDxe/`, so a directory-keyed count
silently drops one and reports 54 of 55.

## Status

The P2 gate is **"the boot manager draws on the phone's screen and UFS enumerates
as a block device"**, and it is **open**. (It read "boots to a shell" until the
volume was inventoried; see the note above on why no platform in this tree meets
that, `suryaPkg` included.) The firmware builds; it has not been observed to run.

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

### Corrected: the ELF32 header is a container, and the code inside is AArch64

Reading the partition header gives this, and it is where an earlier version of
this document stopped:

| partition | container | architecture of the container |
|---|---|---|
| `xbl` | ELF64 | AArch64 |
| `tz`, `hyp`, `devcfg` | ELF64 | AArch64 |
| `abl` | ELF32 | ARM (AArch32) |
| `aop` | ELF32 | ARM (AArch32) |

From which the earlier text concluded "ABL is a 32-bit ARM executable, so a
chain-loaded payload entered in AArch32 state would fault immediately", and
offered that as the leading hypothesis for the failed jump. **That conclusion
was wrong, and the way it was wrong is worth recording**, because everything
needed to see it was one level further in.

`abl` is ELF32 with a single real `PT_LOAD`: `vaddr 0x9fa00000`, `filesz
0x30000`. That address is the **"ABOOT FV"** region from this board's own
`uefiplat.cfg` (`0x9FA00000, 0x00200000`). At segment offset `0x78` — file
offset `0x3078` — sits an LZMA stream. Decompressing it gives 917,704 bytes,
and that is a firmware volume containing exactly **one PE image**:

```
machine  0xAA64        (IMAGE_FILE_MACHINE_ARM64 - AArch64, not ARM32)
sections .text VA 0x1000 size 0xC2000
         .data VA 0xC3000 size 0x1C000
         .reloc VA 0xDF000 size 0x1000
span     0xE0000
```

Every fastboot string and every boot-decision string lives in **that AArch64
image's `.text`**: the command table (`oem uefilog`, `oem lkmsg`, `flash:`,
`erase:`), `CmdBoot`, `FindBootableSlot`, `HandleActiveSlotUnbootable`, and
`Failed to load/authenticate boot image: %r`.

So the component that serves `fastboot` and decides whether to boot is
**AArch64**, and the ELF32/ARM header is the container XBL ships it in, not the
code. The AArch32 hand-off hypothesis is therefore unsupported and is withdrawn:
there is no AArch32 state for ABL to hand control from.

Two things this does *not* change: the failed jump is still unexplained, and
`fastboot boot`'s silence is still unexplained. It removes one candidate rather
than supplying an answer. But it removes the one that was doing the most work in
the reasoning — "a 64-bit payload cannot survive an AArch32 hand-off" was a tidy
explanation and it is not available.

### Why the supported path is probably `fastboot flash boot`
`Mu-gauguin.img` is not an arbitrary payload. It is an Android boot image, and
BootShim is built with `REQUIRES_KERNEL_HEADER=1`, which places the literal
`ARM\x64` magic at **offset 0x38 of the payload** — the ARM64 kernel image
header convention, and the thing a bootloader checks to recognise a kernel. The
image is shaped to be accepted from `boot`, not to be chain-loaded from RAM.

*(Verified against the source rather than assumed: BootShim.S's `_Head` is two
4-byte instructions, then four `.quad`s, putting `.ascii "ARM\x64"` at byte 56
= 0x38. That is exactly right.)*

### What ABL actually does, read out of ABL itself

The `abl` partition decompresses — the GUIDed section payload is an LZMA stream
(`props = 0x5D`, dict size `0x01000000`) yielding a 917,704-byte EFI volume.
Reading its strings answers the question that the silent failure left open.

**`fastboot boot` is implemented.** ABL contains:

```
Fastboot boot command is not available in locked device
Boot Command is not allowed in Lock State
CmdBoot: ClearUnbootable failed
```

This device reports `unlocked:yes`, so the command is not being refused for lock
state. It ran.

**And the P1 error came from here:**

```
Failed to load/authenticate boot image: %r
```

with `%r` printing as `Load Error` — which is exactly the string P1's
`fastboot boot` of the mainline kernel produced. So ABL *does* report failures on
this path; it is not mute.

### P1's image was stock-shaped, and ABL refused it anyway

This is the correction that matters most, and it came from measuring rather than
reasoning. P1's `work/out/boot.img` — the mainline kernel — was parsed field by
field against the phone's own `boot` partition:

| field | P1's boot.img | the phone's own boot | |
|---|---|---|---|
| `header_version` | 2 | 2 | same |
| `page_size` | 0x1000 | 0x1000 | same |
| `header_size` | 1660 | 1660 | same |
| `kernel_addr` | 0x8000 | 0x8000 | same |
| `ramdisk_addr` | 0x1000000 | 0x1000000 | same |
| `tags_addr` | 0x100 | 0x100 | same |
| `dtb_addr` | 0x01F00000 | 0x01F00000 | same |
| `dtb_size` | 0x11839 | 0x1D8CDD | different file, same convention |
| DTB file offset | 0x0E79000 | 0x02BFC000 | both = `page*(1+nk+nr)` |
| DTB magic there | `d0 0d fe ed` | `d0 0d fe ed` | same |

So P1 was not a malformed image. It used this device's own header parameters,
declared its DTB the way this device does, and put the DTB at exactly the offset
this device's layout implies. **ABL rejected it with
`Failed to load/authenticate boot image`.**

That kills the comfortable hypothesis. It is not true that our images merely had
the wrong header and would work once the header matched — a stock-shaped image
was tried, and it failed. Header shape is at best necessary and demonstrably not
sufficient, and the earlier framing in this document (that the stock/ours header
difference was a leading explanation) was wrong to lean on it.

**Two of the rows above are not header fields at all.** `dtb_addr` and
`tags_addr`, and the `kernel_addr`/`ramdisk_addr` pair, are never read by ABL —
not "read and checked", not read. It computes where the DTB goes from the region
sizes and ignores the declared address entirely:

```c
BootParamlistPtr->DtbOffset = BootParamlistPtr->PageSize *
                 (NumHeaderPages + NumKernelPages + NumRamdiskPages +
                  NumSecondPages + NumRecoveryDtboPages);    /* BootLinux.c:478 */
ImageSize = BootImgHdrV2->dtb_size + BootParamlistPtr->DtbOffset;
```

and then requires a valid fdt *at that computed offset* whose `totalsize` fits
inside `ImageSize`. So the table's last three rows are not evidence of anything
being honoured: the DTB offset is the same in both images because the same
arithmetic gives the same answer for the same sizes, and the magic is there
because both writers put it there. What the rows do show — that P1's image is
laid out the way this device's images are laid out — stands.

### Corrected: what ABL reads, from Qualcomm's own source

Everything above was reconstructed from strings and byte offsets in the
extracted PE. The source is public now
(`Daniel224455/mu_qcommodulepkg`, vendored under `work/ref/`), and its
`QcomModulePkg/Library/BootLib/BootLinux.c` matches the extracted binary
string for string — including the original build path baked into the error
messages, `/home/work/gauguin-r-stable-build-normal/bootable/bootloader/edk2/`,
which is this device's own build. So the decision path below is read rather
than inferred, and `tools/abl-boot-check.py` replays it offline.

| what the payload declares | does ABL read it? |
|---|---|
| `kernel_size`, `ramdisk_size`, `second_size`, `page_size`, `header_version` | **yes** — `CheckImageHeader`, and the sizes drive everything |
| `recovery_dtbo_size`, `dtb_size` | **yes** — they are part of the computed DTB offset |
| `kernel_addr`, `ramdisk_addr`, `tags_addr` | no — addresses come from the platform memory map |
| `dtb_addr` | no — the offset is computed, then checked |
| `text_offset` | no — the field does not appear in any function |
| the kernel's `image_size` | only as a runtime headroom guard, never as a pre-jump check |
| `res5` / the EFI-stub `code0` | no — the bytes are copied and jumped to |
| raw vs gzip | **yes, and it changes the code path** (`UpdateKernelModeAndPkg`) |
| the DTB's `msm-id` / `board-id` | **yes** — `GetSocDtb` will not select a tree without them |

Replaying the whole chain over the six P1 payloads **and** the stock image
gives the same verdict for all seven: every check that runs before the jump
passes, the computed DTB offset lands exactly on the appended tree
(`totalsize 0x1562a`), each gzip payload inflates to its kernel's size
(46,891,520 for the EFI-stub one, 45,711,368 for the no-EFI one), and the
headroom is 125 MB against a largest `image_size` of 47,775,744. **A refusal is
not one of these checks.** That reframes both P1 and P2: whatever is happening
happens at or after the handover, or in a stage this tool does not model.

**What is common to P1 and P2, and absent from the image that boots:**

| | P1 (refused, "Load Error") | P2 (silent) | stock (boots) |
|---|---|---|---|
| header | stock-shaped v2 | v1 / page 0x800 | stock-shaped v2 |
| kernel | gzip | gzip | **raw** (`00 00 86 14`) |
| kernel's own arm64 header | `MZ`, `res5=0x40` (EFI stub) | `ARMd` via BootShim, `res5=0` | a branch, `res5=0` (**no EFI stub**) |
| `text_offset` | 0 | 0 | **0x80000** |
| ramdisk | real (279,090 B) | 5-byte `dummy` | real (951,844 B) |
| AVB footer | absent | absent | **absent too** |

Two candidates survive: **compressed kernels**, and the **`dummy` ramdisk**. The
third — AVB — is ruled out by the fourth column, since the image that boots has
no AVB footer either.

**Three structural differences, all three now controllable offline.** The rows
above were measured, not reasoned about, and they are the whole reason the next
session's payload list is worth spending a cycle on:

1. **raw vs compressed.** The stock kernel region is an uncompressed arm64
   `Image`: `b` at offset 0, `image_size = 0x3598000`, `ARMd` at 0x38, no
   compression magic that decompresses. (Careful with this one — a plain search
   for `1f 8b 08` in that 45 MB region *does* hit, at `+0x2954`, and that hit is
   the only one in the whole region and does not decompress. Testing whether the
   candidate actually inflates is the difference between confirming this row and
   "correcting" a row that was right.) The earlier draft of this table is
   confirmed, not amended.

2. **The kernel's own arm64 header is overlaid on a PE header.** `code0` is a
   branch (`0x14860000`) in the stock kernel and `0xfa405a4d` — whose low half is
   `MZ` — in ours, with `res5` (offset 0x3c, "used for PE COFF offset") `0` versus
   `0x40`. That is not damage: the arm64 image header *is* laid out so it can
   double as a PE/COFF header, and a kernel built with `CONFIG_EFI=y` fills the
   MZ form in. So **the stock kernel is built without the EFI stub**, and ours has
   it. ABL is itself UEFI, sits between the two, and has strings for both paths,
   so "it does not care" is an assumption rather than a fact.

   The mechanism is one macro, `efi_signature_nop` in
   `arch/arm64/kernel/efi-header.S`, which `head.S` emits as `code0`:

   ```asm
   .macro efi_signature_nop
   #ifdef CONFIG_EFI
           ccmp    x18, #0, #0xd, pl   /* opcode spells "MZ" */
   #else
           nop                         /* 0xd503201f */
   #endif
   ```

   With EFI on, `__EFI_PE_HEADER` then lays a real PE header at `. - .L_head`,
   which is why `res5` reads `0x40` — one 64-byte arm64 header further on — and
   with EFI off `res5` stays `0`. The comment above the `nop` is the part worth
   keeping: *"Bootloaders may inspect the opcode at the start of the kernel image
   to decide if the kernel is capable of booting via UEFI. So put an ordinary NOP
   here."* Some bootloader, at some point, has inspected that opcode.

   **It is not this one, and the row is a control rather than a candidate.** In
   both forms the first word is followed by the same `b primary_entry`, so a
   kernel that is *jumped to* rather than PE-loaded behaves identically either
   way: the `ccmp` writes flags and falls through, and the branch after it does
   not read them. `QcomModulePkg` never looks at `code0` — it copies
   `KernelSize` bytes from `ImageBuffer + PageSize` to `KernelLoadAddr` and
   jumps there (`BootLinux.c:702`, and the gzip branch decompresses first, then
   does the same). `boot-pstore-raw-noefi.img` exists to make that checkable on
   the device rather than arguable: if it and `boot-pstore-raw-txt.img` behave
   differently, the difference is somewhere that has not been modelled yet.

3. **`text_offset`.** Stock declares `0x80000`, ours `0x0`. 0 is correct for
   Linux 6.12 — `TEXT_OFFSET` was removed from arm64 in 6.6, and the field is
   bootloader metadata the kernel never reads back. And ABL does not read it
   either: `TextOffset` appears in `QcomModulePkg` only in the struct definition
   in `BootImage.h`, and nowhere in any function. So this field is not a lever
   at all — `boot-pstore-raw.img` and `boot-pstore-raw-txt.img` are the same
   payload as far as the bootloader can tell, and they too are a control. That
   is worth knowing before spending a device cycle on the pair, and worth
   knowing afterwards: a difference between them is not the field.

### ABL decompresses the kernel, and says so

Another string found in ABL while looking for the header check:

```
Invalid boot image header:%r          Invalid boot image header size: %u
Invalid image Sizes                   Integer Overflow: Kernel Size = %u
Integer Overflow: Actual Kernel size = %u
Kernel Size 1            : 0x%x       Kernel Size 2            : 0x%x
Failed Kernel Size   : 0x%x
Decompress kernel size is smaller than image header size
Image Header version     : 0x%x
Kernel Load Address: 0x%x             Kernel Size Actual: 0x%x
Ramdisk Load Address: 0x%x            Device Tree Load Address: 0x%x
```

**Which two sizes those are is now settled, and the earlier reading here was
wrong.** `GZipPkgCheck` in the vendor's own source (below) does this after the
decompressor returns:

```c
if (OutLen <= sizeof (struct kernel64_hdr *)) {           /* BootLinux.c:664 */
  DEBUG ((EFI_D_ERROR,
          "Decompress kernel size is smaller than image header size\n"));
```

`OutLen` is how many bytes came out, and the thing it is compared against is the
**size of a pointer** — 8. "image header size" in the message means the arm64
image header, and the test is only that something longer than one header came
out of the decompressor. A 46 MB `Image.gz` passes it without trying; so does
anything else that inflates. It was never a candidate, and `gz-fixedsz`'s lowered
`image_size` was built to test a check that does not exist.

The same function's other size check is the one that reads a size out of the
kernel, and it is a memory check rather than an image one:

```c
if (Kptr->ImageSize > (DeviceTreeLoadAddr - KernelLoadAddr))   /* BootLinux.c:710 */
      DEBUG ((EFI_D_ERROR,
            "DTB header can get corrupted due to runtime kernel size\n"));
```

That is the headroom between where the kernel is copied and where the device tree
goes: **131,305,472 bytes (125 MB)** under the region this device's memory map
carves out, 83 MB under ABL's PCD fallback. The largest `image_size` anywhere in
this project's payloads is 47,775,744. Neither check has ever been close to
firing, which is what `tools/abl-boot-check.py` prints rather than argues —
the wrong table that used to be in this section compared two numbers the
bootloader never compares.

### Six raw and compressed variants, built

`--kernel` takes an uncompressed `Image` straight through; `--text-offset` and
`--image-size` rewrite the two header fields that differ. Reproduce all of them
with `tools/build-p1-payloads.sh`, and check them with
`tools/check-payload.py <images>` before flashing:

```
boot-pstore-raw-noefi.img  46,092,288  raw,  no EFI stub,  text 0x80000, size 0x2c50000
boot-pstore-gz-noefi.img   14,766,080  gzip, no EFI stub,  text 0x80000, size 0x2c50000
boot-pstore-raw-txt.img    47,271,936  raw,  EFI stub,     text 0x80000, size 0x2d90000
boot-pstore-raw.img        47,271,936  raw,  EFI stub,     text 0,       size 0x2d90000
boot-pstore-gz-fixedsz.img 15,282,176  gzip, EFI stub,    text 0,       size 0x2cb8200
boot-pstore.img            15,278,080  gzip, EFI stub,    text 0,       size 0x2d90000
```

`raw-noefi` is the closest match to what the phone's own `boot` carries and
therefore the most likely to work. **`raw-noefi` and `gz-noefi` are one
experiment in two halves**: they are the same kernel, built the same way, with
the same `text_offset` and the same declared `image_size`, and differ only in
whether that kernel is a gzip stream. That is the one property left that can be
blamed for the pattern actually observed — every compressed image refused, every
raw one booting — and the pair changes nothing else, so the *difference* between
flashing them is attributable to one variable rather than to three. The earlier
set tested compression by comparing `raw-noefi` against `gz-fixedsz`, which also
moves `text_offset` and `image_size`; those two are now known to be fields ABL
never reads (above), so that comparison was never going to be readable.
`gz-fixedsz` is kept because it is built, not because it is a candidate.

### Two stock-shaped variants, built and validated offline

For the next device attempt, `tools/make_boot_image.py` builds the image itself
rather than letting Mu-Silicium's builder do it, because that builder never
passes `--pagesize` and so every image it makes is page 2048 / header v1. The
two profiles:

- `silicon` reproduces `Mu-gauguin.img` **in shape**: same 1,122,304 bytes, same
  header fields, same region offsets. That is the regression check that says the
  wrapper is right. It is not byte-identical and should not be expected to be —
  the reference kernel blob is a gzip stream from a different implementation
  (it carries `FNAME = "SILICIUM_UEFI.fd-"`, ours does not, and the deflate body
  differs by 26 bytes). Checking this with `cmp` was a mistake made twice;
  `--compare` and the two field dumps side by side is the check.
- `stock` matches the phone's own `boot` field for field, including
  `dtb_addr = 0x01F00000`, `header_size = 1660`, and the DTB placed after the
  ramdisk at `page*(1+nk+nr)` rather than glued into `kernel_size`.

Both stock variants are built and pass a 9-point structural check (magic, v2
constants, addresses, DTB magic at the declared offset, file size equals the
declared regions, BootShim's `adr`/`b` prologue, `ARMd` at 0x38, `_StackBase`
and `_StackSize` equal to `FD_BASE`/`FD_SIZE`):

```
work/out/p2-variants/Mu-gauguin-stock-none.img   3,248,128  kernel=raw   9/9
work/out/p2-variants/Mu-gauguin-stock-gzip.img   1,146,880  kernel=gzip  9/9
```

(Those are the corrected sizes. The pair first built from this script was 16,384
bytes smaller on both, because its device tree was stale — see "The P2 payload
could not have executed, and one of the two reasons first given for that was
wrong" below. That pair was never written to the device; the image in `boot` is
`Mu-gauguin.img`, which carries the same stale tree and is dealt with in the same
place.)

The pair exists because the two surviving candidates are exactly "compressed vs
raw kernel", so one device session can separate them.

**The failure we hit is therefore after the `OKAY`, and that is the problem.**
`fastboot boot` is answered with `OKAY` as soon as the command is parsed, and the
boot itself happens afterwards. Everything past that point is validated by a
large set of checks and reported **only to a UART this phone does not have**:

```
Invalid boot image header / Invalid boot image header: %d
Image Header version     : 0x%x
Device Magic does not match
BootImage is Incomplete
Decompressing kernel image failed!!!
Decompress kernel size is smaller than image header size
DTB offset is incorrect, kernel image does not have appended DTB
Error: Ramdisk size is over the limit
Failed Kernel Size   : 0x%x
```

Ten distinct ways to fail, one silent outcome. **Diagnosing this by guessing at
the payload is not a plan** — there is no feedback channel on the chain-load
path at all.

That asymmetry is itself the argument for `fastboot flash boot`: the *normal*
boot path is the one ABL is built and tested around, and the one whose failures
it reports. The chain-load path is a side door with no dashboard.

The reference build agrees: `Mu-surya.img` and `Mu-gauguin.img` have identical
header layout (`header_version = 1`, `page_size = 0x800`,
`kernel_addr = 0x10008000`), which is the framework's expectation and not this
device's. The stock boot image differs on every one of those
(`header_version = 2`, `page_size = 0x1000`, `kernel_addr = 0x8000`) — and ABL
evidently accepts both, since it boots the stock one and accepts ours as a
download.

**This has a consequence that is the user's call, not this project's:** passing
the P2 gate may require `fastboot flash boot`, which writes to the device —
something the P0 discipline deferred to P4. Three things make it far less
frightening than it was when that rule was written:

1. `boot` is now backed up and verified (`part-boot.img`, `ANDROID!` magic),
   which it was not until this session.
2. There are **no A/B slots** — `fastboot getvar current-slot` returns
   `GetVar Variable Not found` — so there is exactly one `boot`, and restoring
   it is a single command.
3. **TWRP is installed on the `recovery` partition**, which is a recovery
   environment that does not go through ABL's fastboot at all.

The restore path is `fastboot flash boot ~/backup/gauguin/images/part-boot.img`.

### The backups are verified byte-for-byte against the device

A TWRP session with root shell made it possible to stop trusting the backup and
check it. SHA-256 of three partitions, read from the live device against the
backup files on the host:

| partition | device | backup | |
|---|---|---|---|
| `boot` (sde55) | `50ef59be…8ef3` | `50ef59be…8ef3` | identical |
| `abl` (sde37) | `6f0b51e2…91f3` | `6f0b51e2…91f3` | identical |
| `recovery` (sda29) | `8f488a0a…5fb1b` | `8f488a0a…5fb1b` | identical |

This matters more than it looks. Until now the backups were only known to have
the right magic at the right offset — a check that would pass on a truncated or
partially-holed dump. A matching hash is a different order of confidence, and it
is the thing that makes `fastboot flash boot` an acceptable risk rather than a
gamble on an unverified file.

### TWRP is the real safety net, not fastboot

The `recovery` partition holds **TWRP** (`twrp_gauguin`, `2717:ff68`), and the
backup of it is byte-identical to what is on the device. That means the recovery
path does not depend on ABL serving `fastboot` correctly — which is the exact
thing that wedged earlier. If `boot` is flashed into a non-booting state, the
way back is:

1. Power + Volume Up → TWRP
2. `adb push` the 128 MB backup, `adb shell dd of=/dev/block/by-name/boot`
3. Reboot

**Correction to the section above:** the 128 MB image used for the diagnostic
that wedged the device was described there as stock recovery. It is not — the
`recovery` partition contains TWRP. That does not change the conclusion (TWRP is
a known-good image and it wedged the device just the same, which is the point),
but the earlier wording was wrong about what the image was.

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

## The image is written to `boot`, and the first real boot attempt

With the user's authorization, `Mu-gauguin.img` was written to the `boot`
partition — the first write to device storage in this project. It was done from
TWRP with `dd`, not with `fastboot flash`, so TWRP stayed available throughout:

```
dd if=/tmp/mu-gauguin.img of=/dev/block/by-name/boot bs=4096 conv=notrunc
274+0 records in / 274+0 records out / 1122304 bytes copied
```

Read back from the device and hashed against the host file:

| | SHA-256 |
|---|---|
| device, first 1,122,304 bytes of `boot` | `374555a5…bdbdde` |
| `head -c 1122304 work/uefi/Mu-Silicium/Mu-gauguin.img` | `374555a5…bdbdde` |

Identical. And the whole part changes hash (`50ef59be…` → `6915a6ac…`), so the
write landed where it was aimed rather than being silently dropped.

### Before blaming the payload: what is ruled out

A silent failure invites guessing at the payload, so the first thing done after
the observation was to check the things that would make *any* image fail, and
to check the payload against its own claims rather than against memory.

**AVB does not gate `boot` on this device.** `part-vbmeta.img` parses as a
well-formed AVB image whose header carries `flags = 0x2`, which is
`AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED`. The two halves of that:

```
vbmeta         libavb 1.0  alg=SHA256_RSA4096  flags=0x2  release=avbtool 1.1.0
vbmeta_system  libavb 1.0  alg=SHA256_RSA2048  flags=0x0
fingerprint    Redmi/edgeration_gauguin/gauguin:11/RQ2A.210505.003/…:userdebug/test-keys
```

`userdebug` + `test-keys` + verification disabled is a device that does not
authenticate `boot`. (`vbmeta_product`, `vbmeta_vendor` and `vbmeta_odm` are
allocated but entirely zero.) So a modified `boot` is not being refused by
verified boot — and the previously-considered "ABL reported
`Failed to load/authenticate boot image`" reading is not supported either: that
string exists in ABL, but nothing here is asking it to authenticate.

**The appended DTB is the right board's.** Decompiling the DTB out of our image
gives `qcom,msm-id = <0x1b2 0x10000 0x1cb 0x10000>` and
`qcom,board-id = <0x23 0x00>`. Parsing the stock `dtbo` (19 entries) shows
entry **13** is

```
13  249086 bytes  msm=<0x1b2 0x10000 0x1cb 0x10000>  board=<0x23 0x00>
    "Qualcomm Technologies, Inc. Gauguin"
```

The only board-id `0x23` in the whole table, and it is gauguin's. This is a
claim that had been recorded earlier from a summary; it is now checked against
the partition rather than trusted.

### Corrected: entry 13 is an *overlay*, and the base tree comes from `boot`

The paragraph above stopped one step short. Entry 13 is not a device tree — it
is an **overlay**, 40-odd `fragment@N { target = <phandle>; __overlay__ {...} }`
nodes plus a `model`/`msm-id`/`board-id` header so ABL can identify it. It has
no `chosen`, no `memory`, no `reserved-memory` of its own. So there must be a
**base** tree for it to be applied to, and the only other place a device tree
lives is the `boot` image's own declared DTB region.

That region is not empty, and it explains a string that looked like a copy-paste
mistake:

```
$ dtc -I dtb -O dts stock-boot.img@0x2BFC000
  model = "Qualcomm Technologies, Inc. APQ 8016 SBC";
  chosen { stdout-path = "serial0"; };
  reserved-memory { ramoops@bff00000 { reg = <0 0xbff00000 0 0x100000>; }; mpss@86800000 {...}; };
  soc { interrupt-controller@b000000 {...}; sdhci@7824000 {...}; mdss@1a00000 {...}; };
```

`APQ 8016 SBC` is a DragonBoard 410c, so the *model string* is boilerplate — but
the **contents are not**: `sdhci@7824000`, `mdss@1a00000`, `usb@78d9000` and
`mpss@86800000` are this SoC family's addresses. It is Qualcomm's generic base
tree, and Android boots with it *plus* the entry-13 overlay applied on top.

Two consequences that matter:

1. **The boot image's DTB region is a DTB slot, not a vestigial field.** It is not
   one tree but **twelve, concatenated**, and `GetSocDtb` walks them in order and
   picks one by `qcom,msm-id`:

   ```
    0  59,279  Qualcomm Technologies, Inc. APQ 8016 SBC      no msm-id  (the ramoops node above)
    1           Qualcomm Technologies, Inc. DB820c
    2           Qualcomm Technologies, Inc. IPQ8074-HK01
    3           Qualcomm Technologies, Inc. MSM 8916 MTP
    4           LG Nexus 5X
    5           Huawei Nexus 6P
    6           Qualcomm Technologies, Inc. MSM 8996 MTP
    7           Qualcomm Technologies, Inc. SDM845 MTP
    8 418,772  Qualcomm Technologies, Inc. Lagoon SoC      <-- msm-id 434/459
    9          Qualcomm Technologies, Inc. Lito v2 SoC
   10          Qualcomm Technologies, Inc. Lito SoC
   11          Qualcomm Technologies, Inc. Orchid SoC
   ```

   Tree 0 is the "APQ 8016 SBC" one, and it is not selectable: no `qcom,msm-id`,
   so `GetPlatformMatchDtb` leaves `DtMatchVal` at `NONE_MATCH` and tree 8 wins.
   The running phone confirms it from the other end —
   `/proc/device-tree/chosen/bootargs` carries `androidboot.dtb_idx=8` and
   `androidboot.dtbo_idx=13`, i.e. ABL wrote down which two trees it used. **Our
   own tree put in this slot becomes the base**, and tree 0 becomes irrelevant:
   nothing selects it, which is why its `ramoops` was never this device's.

2. **`ramoops@bff00000` is in tree 0, and it is not in this phone's RAM anyway.**
   Two independent reasons not to copy it, and the second is the one that would
   have cost a device cycle: ABL writes the RAM partition table into `/memory`
   before the jump, and per the running phone that is

   ```
   0x80000000 + 0x3bb00000   ends 0xbbb00000     (bank 0)
   0xc0000000 + 0xc0000000                       (bank 1)
   0x180000000 + 0x100000000                     (bank 2)
   ```

   so `0xbff00000` sits in the 69 MB hole between bank 0 and bank 1 — outside
   every partition memblock is given. It is the top of *that board's* RAM, which
   is the kind of thing that looks like a fact when it is copied and stops being
   one when the board changes. See the log-channel section below.

### What ABL does with a device tree, from its own strings

ABL's string table describes the algorithm precisely, and it was read out rather
than guessed:

```
Single appended DTB found / Not the single appended DTB
DTB offset is incorrect, kernel image does not have appended DTB
DTB offset goes beyond kernel size / Dtb offset goes beyond the image size
Best match DTB tags %u/%08x/0x%08x/%x/%x/%x/%x/%x/(offset)0x%08x/(size)0x%08x
Exact DTB match found. DTBO search is not required
Error: Board Dtbo blob not found / Error: Device Tree blob not found
ApplyOverlay: After overlay DTB size exceeded than supported
Error: Dtb overlay failed / Device Tree update failed Status:%r
Override DTB: GetBlkIOHandles failed loading user_dtbo!
```

So the order is: take the base tree (appended inside the kernel image if there
is one, otherwise the boot image's DTB region), and if its match value has every
bit of `ALL_BITS_SET` set, stop — the DTBO partition is not searched and no
overlay is applied (`DtboNeed = FALSE`). Only otherwise does it pick the "best
match DTB tags" out of `dtbo` and overlay it. There is also an override through a
`user_dtbo` partition.

**"Exact" is much stricter than `msm-id` and `board-id`, and our tree does not
meet it.** `ALL_BITS_SET` is a six-field set, from `LocateDeviceTree.h:142`:

| field | where it comes from | our tree |
|---|---|---|
| `SOC_MATCH` | `qcom,msm-id` | `<434 0x10000>, <459 0x10000>` — set |
| `VARIANT_MATCH` | `qcom,board-id` cell 0 vs. the CDT | `<0x23 0>` — set |
| `SUBTYPE_EXACT_MATCH` | `qcom,platform-subtype` | **declares neither** this nor the next two |
| `FOUNDRYID_EXACT_MATCH` | `qcom,foundry-id` | — |
| `PMIC_MATCH_EXACT_MODEL_IDX0…F` | `qcom,pmic-id`, all **sixteen** entries matched against the PMICs' model *and* revision | — |
| `SOFTSKU_EXACT_MATCH` | `qcom,softsku-id` | — |

The two set rows were checked against the built DTB rather than assumed: its root
node carries exactly `model`, `compatible`, `chassis-type`, `interrupt-parent`,
`#address-cells`, `#size-cells`, `qcom,msm-id` and `qcom,board-id`, and nothing
else. An earlier version of this file read `Exact DTB match found. DTBO search is
not required` as being about `msm-id`/`board-id` alone. It is not: it wants the
PMIC list too, and `CheckAllBitsSet()` cannot pass for a tree that declares no
`qcom,pmic-id` at all. **So the overlay is applied to our tree every time** —
`CheckAllBitsSet` is the *only* thing that can clear `DtboNeed`, and
`BootLinux.c:556` reads it back as `DtboCheckNeeded`.

That is the fact the `__symbols__` work below follows from: a payload whose DTB
slot holds our tree is *not* the case where ABL uses it untouched, and a tree of
ours built without `/__symbols__` is one ABL refuses outright, before the kernel's
first instruction. The overlay does get appended, and where it lands depends on
what our symbols say.

### Every payload was being refused for a missing `/__symbols__`

This is the defect that made the first payloads unreadable rather than wrong, and
it is the reason ABL's silence was never evidence that our code had run.

Entry 13 is a dtc `-@` overlay, which means its 79 fragments do not carry
addresses: each one says `target = <0xffffffff>` and a `__fixups__` table maps
that placeholder back to a *symbol name* — `tlmm`, `mdss_mdp`, `CPU0`,
`thermal_zones`, and 154 more. Resolving one happens in
`ufdt_overlay_do_fixups()` (`LibUfdt/ufdt_overlay.c:226`), which for each symbol
does **three** things in a row, and only the first two have an error path:

| step | code | if it fails |
|---|---|---|
| the base tree's `/__symbols__` must exist | `:231`, `:234` | `-1` — `"Bad main_symbols in ufdt_overlay_do_fixups"` |
| the symbol name must be in it | `:256`, `:259` | `-1` — `"Couldn't find '%s' symbol in main dtb"` |
| that value must resolve to a node | `:264`, `:267` | `-1` — `"Couldn't find '%s' path in main dtb"` |
| that node's phandle is read | `:271` → `ufdt_node.c:145` | **nothing** — it returns `0` |

Any of the three `-1`s reaches `ApplyOverlay()` as

```
ApplyOverlay: ufdt apply overlay failed
```

and returns `EFI_NOT_FOUND` (`BootLinux.c:385`), before the kernel's first
instruction. Nothing on the screen, no `oem fbreason` that distinguishes it,
nothing in pstore — the same shape as a payload that never ran.

Our tree is built by `scripts/Makefile.dtbs`, which adds `-@` to `base-dtb-y`
only, so a board file built on its own carries **no `/__symbols__` at all**, and
every payload we had built was refused this way. That is the first row of the
table, and it fires on the overlay's *first* symbol — the overlay's content never
even matters.

The fourth row is the one that does not announce itself. A symbol whose path
resolves to a node that has no `phandle` on it (and no `linux,phandle` either)
returns phandle `0`, which is not a valid phandle; `ufdt_apply_fragment()`
(`:324`) then fails to find target 0, reports `OVERLAY_RESULT_TARGET_INVALID`
(`:339`), and `ufdt_overlay_apply_fragments()` (`:377`) aborts on
`OVERLAY_RESULT_MERGE_FAIL` **alone** (`:387`) — so the fragment is dropped and
the boot continues. ABL succeeds with the vendor's content silently missing. Two
silences on the same overlay path — `ufdt_overlay_apply()` calls both — and they
mean opposite things.

`tools/make_dtbo_sinks.py` reads the dtbo, takes the 158 symbol names entry 13's
fixups ask for, and generates one empty node per name plus a `/__symbols__`
pointing at them — 217 symbols, phandles `0x8000`–`0x80d7`, none shared with a
node of ours. Two properties make that safe rather than a hack:

- **Nothing binds to them.** `/__sink__` has no `compatible`, and
  `of_platform_bus_create()` opens with `if (strict && !of_get_property(bus,
  "compatible", NULL)) return 0;` (`drivers/of/platform.c`, and
  `of_platform_populate()` passes `strict = true`) — it returns before
  `of_device_alloc`, so the node's subtree is never even walked. The vendor
  overlay's content is merged and then never populated as a device on the Linux
  side. It is a place for 249 KB of overlay to land that is not a real node.
- **The phandles are disjoint from every real node's.** `ufdt_overlay_apply_fragment()`
  merges into the node *whose phandle the symbol resolved to*, looked up in a table
  built from the whole tree, so a symbol that collided with a real node would send
  the overlay somewhere else entirely. `tools/abl-boot-check.py` fails a tree with
  any phandle that has two owners, and fails one whose symbols name paths with no
  phandle at them — distinguishing the two rows of the table above, because
  "ABL refused" and "ABL booted without the vendor tree" call for different next
  steps.

**The merge is now performed rather than replayed.** `abl-boot-check.py` checks
the fixups in Python *and* hands the overlay and the selected tree to
`fdtoverlay` — libfdt's implementation, where libufdt is Google's
reimplementation of the same format — and requires the real merge to succeed. Two
implementations agreeing is evidence in a way that one of them being careful is
not, and it is the check that would have caught the payloads that shipped with no
`/__symbols__` at all.

They are not redundant, and the fourth row is where they part company: libfdt's
`overlay_fixup_phandle()` treats a missing phandle as an error, so `fdtoverlay`
*refuses* the phandle-less tree that libufdt would have accepted and booted with
fragments missing. The stricter implementation is the merge; the Python replay is
the only one that can say which of the two ABL behaviours a given tree gets.

Measured on the built payloads: the overlay applies cleanly, the merged tree is
296,928 bytes against our 87,594, and `/reserved-memory/ramoops@d0000000` and
`/chosen/framebuffer@a0000000` are both still in it — which is checked in the
merged tree rather than the input, because that is what the kernel is handed and
an overlay that clobbered either node would leave a boot that runs and cannot be
read. Three negatives were built and all three are refused, each with its own
diagnosis: `/__symbols__` deleted ("this tree has no /__symbols__ at all, and the
board overlay … arrives with 158 `__fixups__`"), the sink nodes deleted but the
symbols left pointing at them ("158 of the overlay's symbols name paths this tree
has no node at"), and the sink nodes kept but their phandles stripped ("113 of
the overlay's symbols name nodes this tree has no phandle on … every one of those
fragments is dropped in silence").

### BootShim preserves `x0`, so it does not choose the device tree

Worth writing down because it is easy to assume otherwise. BootShim is 112 bytes
and uses only `x1`–`x6`:

```
_Head:  adr x1, _Payload ; b _Start
_Start: mov x4, x1 ; ldr x5,=FD_BASE ; ... copy loop ... ; br x5
```

`x0` — the DTB pointer on the arm64 boot path — is passed through untouched. So
the DTB appended **inside `kernel_size`** in Mu-Silicium's images is there for
ABL's benefit (it is what `Single appended DTB found` looks at), not because
BootShim reads it.

### P1's payload as built could not print anything, and now can

This was found by reading P1's kernel `.config` against its command line, and it
is the reason step 4a of the runbook has been rewritten rather than re-run.

| what | state | consequence |
|---|---|---|
| `console=tty0` | in the cmdline | the console is the framebuffer, so it needs one |
| `CONFIG_FRAMEBUFFER_CONSOLE=y` | set | fbcon exists |
| `CONFIG_DRM_SIMPLEDRM=y`, `DRM_FBDEV_EMULATION=y` | set | a `simple-framebuffer` **node in the DT** is drawn on |
| `CONFIG_DRM_MSM=m` | module | the real panel driver is not built in |
| `CONFIG_ARM64_APPENDED_DTB` | **does not exist on arm64** | a Linux payload can only get a DT from ABL |

Bluntly: with the vendor tree (no `chosen`, no `simple-framebuffer`) the kernel
prints to a dummy console and the screen stays black **even on a successful
boot**. A black screen would have been indistinguishable from a payload that
never ran, which is exactly the ambiguity that has cost this project its device
cycles. Two things change that:

**Our tree goes in the DTB slot, and it declares a framebuffer.** The tree's
`/chosen` carries

```
stdout-path = "serial0:115200n8";
framebuffer@a0000000 { compatible = "simple-framebuffer";
    reg = <0 0xa0000000 0 0x9e3400>;  width = 0x438;  height = 0x960;
    stride = 0x10e0;  format = "a8r8g8b8"; };
```

`0x438 x 0x960 x 4 = 0x9E3400` is exactly the reserved size, so the geometry is
self-consistent with the panel (1080x2400).

The address is stronger than "copied from Fairphone", which is what an earlier
version of this file said. `0xa0000000 + 0x2300000` is `cont_splash_memory` in
`sm6350.dtsi`, marked `no-map` — the SoC family's continuous-splash carveout, the
region the bootloader paints the logo into and hands over as a live scanout
buffer. The *geometry* in our `/chosen` node is gauguin's own, not Fairphone's
(the Fairphone 4 is 1080x2340). What is still unverified is the direction of
travel: that ABL leaves that buffer live rather than turning the panel off before
jumping to the kernel. `CONFIG_DRM_SIMPLEDRM=y`, `CONFIG_FRAMEBUFFER_CONSOLE=y`
and `CONFIG_DRM_FBDEV_EMULATION=y` are all set, so if the buffer is live, fbcon
draws on it.

Worth recording what is *not* needed, since it looks like it should be:
`CONFIG_SYSFB_SIMPLEFB` is deliberately unset. On a device-tree system
`of_platform_default_populate_init()` finds `/chosen`'s `simple-framebuffer`
child itself, creates the platform device, and calls `sysfb_disable()` so sysfb
will not register a second one; `simpledrm` then binds by its own
`of_device_id` match. sysfb is the x86/UEFI path.

If the screen stays black while `oem fbreason` says the boot was attempted, the
first thing to check is not another address but whether the panel is still being
scanned out at all — the framebuffer above is the bootloader's, and this payload
never re-initialises DSI (`&mdss` is `disabled`, and `msm` is a module). The
address itself is settled: `/proc/device-tree/reserved-memory` on the running
phone has both `cont_splash_region` and `disp_rdump_region@0xa0000000` at
`0xa0000000+0x2300000`, the bootloader's logo is in it, and `0x9e3400` bytes of
1080x2400 fit with room to spare.

An earlier version of this section proposed `0xac000000` as a fallback and
argued that if it worked then "the overlay was applied and our tree was not used
at all". That test does not exist: the overlay is applied every time (see
"Exact" above), and `disp_rdump_region@ac000000` is a debug dump region the
overlay *creates*, not a scanout buffer anyone has ever drawn to.

**A log channel that needs no cable at all.** The kernel is now built with:

```
CONFIG_PSTORE=y  CONFIG_PSTORE_RAM=y  CONFIG_PSTORE_CONSOLE=y
```

and P1's command line carries

```
ramoops.mem_address=0xd0000000 ramoops.mem_size=0x100000
ramoops.record_size=0x20000 ramoops.console_size=0x80000 ramoops.ftrace_size=0x20000
```

**The address is the part that has been wrong twice, and both times the same
way.** A DT region has to be in DRAM *and* it has to be free, and neither check
is one ABL makes:

| address | inside a RAM partition? | free? |
|---|---|---|
| `0xbff00000` (copied from tree 0, "APQ 8016 SBC") | **no** — in the 69 MB hole between bank 0 (`0x80000000+0x3bb00000`, ends `0xbbb00000`) and bank 1 (`0xc0000000`) | — |
| `0xc4000000` (our first fix) | yes, `0xc0000000+0xc0000000` | **no** — inside `removed_region@c0000000`, `0xc0000000+0x7b00000`, `compatible = "removed-dma-pool"`, `no-map` |
| `0xd0000000` (this) | yes, `0x1500000` past that removed region and 2.75 GB inside bank 1 | yes |

Both faults land in the same place: `ramoops_init` is a `postcore_initcall` and
`ramoops.mem_address=` on the command line is the copy that wins over the DT
node, so the first `ioremap()` and the first ring-buffer write happen before
there is any console to print a fault on — indistinguishable from a payload that
never ran, at the price of a device cycle. The second row is the one that is easy
to argue for: it *is* in DRAM, and the reason it is also in memory that belongs
to someone else is that the mainline tree (`sm6350.dtsi`) and the phone's own
tree disagree about how much of bank 1 the bootloader gives away — `0x3900000`
against `0x7b00000`. `removed-dma-pool` is the name Qualcomm uses for DRAM the
bootloader has handed to a subsystem, and on this phone that subsystem is the
modem, the ADSP and the CDSP.

`tools/gauguin.py` now carries both halves as measurements — `DRAM` and
`PHONE_RESERVED`, the latter read out of the running phone's
`/proc/device-tree/reserved-memory` — and `tools/abl-boot-check.py` fails any
payload whose log region is outside the first or inside the second. Against the
two old payloads it prints exactly the two reasons above.

**The geometry, on the other hand, is ours to choose**, and an earlier version of
this file got that wrong in the opposite direction by treating the vendor tree's
`record_size`/`console_size`/`ftrace_size` as a contract with Android. There is
no such contract on this device: the vendor kernel has no `/sys/fs/pstore`, and
its own panic log goes through `mtdoops` to a raw partition (`block2mtd` in its
cmdline points at `/dev/block/sda15` with `mtdoops.record_size=2097152`). The
only reader is our own kernel, so `console_size` is set to `0x80000` because a
long console record is what an initramfs bring-up wants, and the region is
written the same way by both the DT node and the cmdline.

That reader is now real rather than hypothetical: the initramfs prints the tail
of `/sys/fs/pstore/console-ramoops-0` and `dmesg-ramoops-0` in its report
(`tools/initramfs/init.c`, `report_pstore()`). So a payload that dies comes back
up — `panic=10` resets the phone, and `reboot=panic_warm` makes that reset warm,
which keeps DRAM — and the second boot prints the first boot's last 3 KB on the
panel. Before that function existed the carveout was write-only.

The same values are in the device tree as a reserved-memory carveout,
`ramoops@d0000000` with `no-map`, replacing the `ramoops@ffc00000` inherited from
`sm6350.dtsi` (Fairphone's, different address *and* different geometry). The DTS
version carries a comment saying why; the build checks for it
(`tools/build-p1-payloads.sh` refuses to build a DTB that has anything other than
exactly one `ramoops@d0000000`).

Having both a DT node and cmdline parameters is deliberate, and it does not
matter which of them wins, because they now say the same thing. It is worth
knowing which one *does* win, though, because "two sources of truth" is the kind
of thing that bites later: `ramoops_init` is a `postcore_initcall` and registers
the cmdline's dummy platform device before `platform_driver_register`, while the
DT nodes are not populated until `of_platform_default_populate_init` at
`arch_initcall_sync` — one initcall level later. `ramoops_probe` opens with
"only a single ramoops area allowed at a time, so fail extra probes", so the
**cmdline wins and the DT node is the one that gets refused.** That is the
documented cmdline method working as designed, not a bug.

`no-map` means the kernel never hands the region to the page allocator. That
matters more than it sounds: a payload that only set `ramoops.mem_address` would
be pointing pstore at 1 MB of ordinary System RAM. On arm64
`request_standard_resources()` never runs, so System RAM is not claimed in
`iomem_resource` and `ramoops`'s `request_mem_region` **succeeds anyway** — the
region would look fine and be allocatable, and the log would be corrupted by
whatever landed on it. An earlier version of this file said the failure would be
a visible `EBUSY`; it would not have been.

Finally, the cmdline now carries `reboot=panic_warm`. Mainline parses the
`panic_` prefix in `reboot_setup()` into a separate `panic_reboot_mode`, and
`psci_sys_reset()` turns `REBOOT_WARM` into `SYSTEM_RESET2` with
`SYSTEM_WARM_RESET` instead of a plain `SYSTEM_RESET`. A warm reset keeps DRAM,
so the log survives the reboot `panic=10` triggers. Without it, whether the
log survives is up to whatever the platform's plain reset happens to do.

With all of that, `PSTORE_CONSOLE` writes every `printk` into the region as it
is produced — `pstore_console_write` calls the backend directly per chunk and
registers with `CON_PRINTBUFFER`, so the pre-ramoops boot log is replayed into it
too; no crash is needed to flush. The sequence "boot our payload → it dies or
panics (`panic=10`) → the phone reboots itself warm → our payload boots again →
the initramfs prints the previous boot's tail on the panel" therefore produces
the kernel log of a payload that had no UART, no screen driver, and nothing else
to say. Warm reboots keep RAM; a power cycle does not, so read the log before
pulling the battery or holding the power button on the phone.

Both are reproducible, and reproducible as one command rather than as a recipe
to retype:

```sh
tools/build-initramfs.sh                # the report + heartbeat init
tools/build-p1-payloads.sh              # all six variants
tools/check-payload.py work/out/boot-pstore-*.img   # refuse a structurally wrong one
```

The script builds the DTB as well as the kernels — the tree is an **input we
edit** now, so leaving it out of the reproducible path is how the ramoops
carveout would quietly go missing. It refuses to proceed unless the built tree
has exactly one `ramoops` node and that node is `ramoops@d0000000`, and it ends
by running `tools/abl-boot-check.py` over every image it built, which is the gate
that now covers the two address faults above. Its one expensive step, the second
full kernel build with `CONFIG_EFI=n`, is reused from `work/out/Image-noefi` when
it already exists; `--rebuild-noefi` forces it after a kernel-source or config
change. The config it toggles is restored on every exit path, so a failed run
cannot leave `CONFIG_EFI=n` behind for the next build.

`check-payload.py` enforces the image *layout* against the constants the phone's
own image fixes — header version 2, page size 0x1000, the kernel/ramdisk/DTB
geometry, the DTB magic at the offset the header declares — and against the
image itself, since declared regions that do not add up to the file size, or an
AVB footer, are faults no external reference is needed to see. It used to take
the stock image as a `--stock` reference as well, and no longer does: the only
thing that reference bought was the ramoops comparison below, and it was the one
part of the check whose premise was wrong. Its ramoops-geometry check —
comparing our address and record sizes against the vendor base tree's — has been
**removed**: the premise was that Android reads the region, and it does not
(there is no `/sys/fs/pstore` in the vendor kernel; its panic log goes to
`mtdoops` on a raw partition), and the node it was compared against is in a tree
nothing selects. A check against a reader that does not exist can only produce
false failures. What survives there is the part that is self-contained: one
ramoops node, and a device tree and a command line that name the same region.
Where the region has to *be* is checked against the *device* instead, by
`abl-boot-check.py`, which is the check that can actually be right or wrong.

The tree itself is versioned, because the kernel tree is not: the board file
lives in `dts/sm7225-xiaomi-gauguin.dts` and is copied into the build tree by
`tools/build-p1-payloads.sh`. It was first produced from the Fairphone 4 file by
a one-shot generator (`tools/make_gauguin_dts.py`), which has since been deleted:
it rewrote the whole board file from hard-coded blocks, so re-running it would
have put the old `0xbff00000` carveout back, and its output — the tracked file —
is now hand-edited far past what it emits. Git history has it.
`build-p1-payloads.sh` re-copies the tracked file in before building the DTB and
deletes the stale `.dtb` first, which is the guard from the other side: a build
tree whose copy has drifted now fails instead of producing a payload whose log
goes to 0xffc00000.

One consequence worth knowing before it looks like a mystery: the P2 UEFI image
already written to `boot` was built from an earlier DTB, so a rebuild now differs
from it by exactly this one carveout. The difference is inert for P2 — nothing in
ABL's image check or in the UEFI memory map depends on a 1 MiB `no-map`
reservation — but the two files are no longer identical, and the `gauguin.dtb`
that a fresh Mu build installs into `Resources/DTBs/` will carry the node.

#### The console and the log were being claimed, not configured

The device tree half of the two channels above is versioned and checked. The
kernel half was not, and the way it failed is worth a subsection because nothing
about it is visible from either end.

`tools/build-kernel.sh` builds the `.config` from a list of `scripts/config`
calls. Two independent bugs meant that list was a statement of intent rather than
a description of the kernel:

| # | bug | what it did |
|---|---|---|
| 1 | the whole list sat inside `if [ ! -f .config ]` | write-once: it ran when the tree was first configured and never again, so a line added later never reached the kernel |
| 2 | `scripts/config` upper-cases symbol names unless `--keep-case` is passed | `--enable FONT_TER16x32` wrote `CONFIG_FONT_TER16X32=y`, which matches no Kconfig entry, so Kconfig dropped it in silence |

Both were live at once, on the same block. The console-font lines were added
after the tree had already been configured (bug 1 meant they never ran), and both
of them have a lowercase letter in the name (`FONT_8x16`, `FONT_TER16x32`, bug 2
would have mangled them if they had). The measured result:

```
$ strings vmlinux | grep -x TER16x32      # before
$ ls lib/fonts/font_ter16x32.o            # before
ls: cannot access 'lib/fonts/font_ter16x32.o': No such file or directory
```

while the command line in every payload said `fbcon=font:TER16x32`. fbcon's
handler for that option is `strscpy(fontname, options + 5, ...)` followed by
`if (!fontname[0] || !(font = find_font(fontname))) *font = get_default_font(...)`
— it falls back to the default font **without printing anything**, so the request
is unverifiable from the phone. Measured against the kernel those payloads were
actually built from:

```
$ strings -a vmlinux | grep -x TER16x32
$ nm vmlinux | grep -ci ter16x32
0
```

— no font, in a kernel whose `fbcon` was told to use one. A payload built this
way draws 8x16 text: 135 columns of 8-pixel glyphs on a 1080-wide panel, which is
a photograph the runbook cannot read, on a device whose only diagnostic interface
is that photograph. After the fix the same two commands find the font, and
`lib/fonts/font_ter16x32.o` is built.

The same audit turned up the pstore options in the same state — `PSTORE`,
`PSTORE_RAM` and `PSTORE_CONSOLE` were set in the `.config` on disk but appeared
nowhere in the build script, so they came from a hand edit. **Every payload in
`work/out/` was correct by accident**: the tree it was built from carried a
config that no run of the build script would have produced, and a fresh clone
would have built a kernel with neither console nor log. That is the same shape as
the versioned-DTS problem above, one layer down.

Three changes close it, all in `tools/build-kernel.sh`:

* the option list is applied unconditionally, not only when `.config` is absent —
  `scripts/config` writes nothing when a value is unchanged, so the build stays
  incremental;
* every call goes through a `cfg()` wrapper that passes `--keep-case`, because an
  all-capital name is unaffected and a lowercase one is otherwise destroyed;
* after `olddefconfig`, the script reads back the fifteen options whose absence
  would make a device session unreadable — the console chain, the UFS driver, and
  the pstore trio — and **exits non-zero** rather than building a kernel that
  cannot report why it failed.

`build-p1-payloads.sh` gained the matching device-tree guard: it reads
`compatible`, `width`, `height`, `stride` and `format` straight out of
`/chosen/framebuffer@a0000000` with `fdtget` and refuses to build a payload
without them. The two guards are deliberately at the two ends of the same claim —
the DTB half says "the framebuffer is described", the config half says "and there
is a driver to draw on it" — because a payload that satisfies one and not the
other is indistinguishable from a payload that works, on a dead screen.

#### Both channels assume the kernel is still running when someone reads them

The two guards above make the payload *able* to speak. Neither makes it *willing*,
and the audit that found the font bug turned up a third gap on the same theme: a
payload that fails in either of the two ways this bring-up is most likely to fail
does not panic, and a kernel that does not panic never restarts itself — which is
the only way the pstore half of the log is ever read.

The chain was checked in the tree rather than assumed, because the runbook leans
on it. `reboot_setup()` (`kernel/reboot.c:1012`) takes the `panic_` prefix off the
argument and assigns the rest to `panic_reboot_mode`; `panic()`
(`kernel/panic.c:441`) copies that into `reboot_mode` before calling
`emergency_restart()`; arm64 uses `asm-generic`'s, so that is `machine_restart()`
→ `do_kernel_restart()` → the notifier chain → `psci_sys_reset()`
(`drivers/firmware/psci/psci.c:309`), which for `REBOOT_WARM` invokes
`SYSTEM_RESET2` with reset type 0 (`SYSTEM_WARM_RESET`) instead of the cold
`SYSTEM_RESET`. So `panic=10 reboot=panic_warm` really is a warm reset and really
does keep DRAM — in the kernel. What the *device* does with it is a separate
question, below.

Two things were missing at the front of that chain:

| gap | what happened instead |
|---|---|
| no `PANIC_ON_OOPS` | an oops — a wrong property in our DTB causing a NULL dereference in a probe — does not panic. The kernel survives it, which is the worst outcome available here: half-initialised, no console (simpledrm binds late, long after early setup), and no reboot, so the ring is never read and the session learns nothing |
| no `SOFTLOCKUP_DETECTOR` / `BOOTPARAM_SOFTLOCKUP_PANIC` | a spin in a probe waiting for a clock or regulator that never comes ready — the classic Qualcomm bring-up failure — produces no panic at all. The detector notices it, but on its own it only prints a stack trace to a console that may not exist |

Both are set in `tools/build-kernel.sh` now, and both are in the read-back list,
because for a kernel whose entire diagnosis path is "did it say anything before it
stopped", a kernel that panics is strictly more useful than one that limps.

**The one thing that stays unverifiable, recorded because the tempting conclusion
is wrong.** `psci_init_system_reset2()` (`psci.c:517`) calls
`psci_features(SYSTEM_RESET2)` and **prints nothing either way**. So the boot log
cannot tell you whether this device honours a warm reset. If the ring comes back
empty after what should have been a panic, "SYSTEM_RESET2 is not supported here,
so the reset was cold and DRAM was lost" and "the payload never panicked" are the
*same observation*. The natural reading — no log, so it never ran — is not
supported by it, and on a device with no UART that misreading costs a session.

`build-p1-payloads.sh` also checks the font by name now, not just by config
symbol. The config read-back in `build-kernel.sh` proves `CONFIG_FONT_TER16x32=y`
survived `olddefconfig`; only the `font_desc`'s `.name` field connects that to the
`fbcon=font:TER16x32` on the command line, and nothing checked that the two still
agreed. The script extracts the name from the command line and requires it to be
in each kernel it packages — including the reused `Image-noefi`, since a stale one
is exactly how variant 1, the first thing the runbook flashes, ends up without it.
And it prints a note when the command line asks for no font at all, so that
"checked and fine" and "not checked" do not look the same in the log.

The first version of that guard was itself the bug it was written to catch, which
is why it is worth a sentence. It read
`found=$(strings -a "$1" | grep -cx "$FONTNAME")` under `set -eu` — and `grep -c`
**exits 1 when the count is zero**, so on the one input that matters the
assignment aborted the script before the error message was ever printed. A
missing font would have failed the build with no output at all: the same silent
failure, one layer up, in the code meant to detect it. `|| true` inside the
substitution is what makes the guard able to report.

**The boot image is laid out the way the framework intends.** Parsed field by
field, ours and the reference `Mu-surya.img` are byte-for-byte the same shape:

| field | ours | surya | stock |
|---|---|---|---|
| `header_version` | 1 | 1 | 2 |
| `page_size` | 0x800 | 0x800 | 0x1000 |
| `kernel_addr` | 0x10008000 | 0x10008000 | 0x8000 |
| `ramdisk_addr` | 0x11000000 | 0x11000000 | 0x1000000 |
| `tags_addr` | 0x10000100 | 0x10000100 | 0x100 |
| `ramdisk_size` | 5 (`"dummy"`) | 5 (`"dummy"`) | 0xe8624 |
| `os_version` | 0 | 0 | 0x16000155 |

Being identical to a working reference is the point: whatever ABL objects to,
it would object to on surya as well. And the regions are where the header says
they are — `kernel` at 0x800 holds the gzip stream, and the 71,714-byte DTB is
appended **inside** `kernel_size`, which is what ABL's
`DTB offset is incorrect, kernel image does not have appended DTB` is checking
for.

**BootShim decompresses and checks out.** The kernel region is
`gzip(BootShim.bin + SILICIUM_UEFI.fd) + DTB`. Decompressing it gives exactly
`0x300070` bytes = 112 + 0x300000, and the first 112 bytes are BootShim:

```
0x00  81 03 00 10 0f 00 00 14   adr x1, _Payload ; b _Start
0x08  00 00 c0 9f 00 00 00 00   _StackBase = 0x9fc00000   == FD_BASE
0x10  00 00 30 00 00 00 00 00   _StackSize = 0x300000     == FD_SIZE
0x38  41 52 4d 64               "ARMd" - the ARM64 header magic, 0x644d5241
```

(The magic is the u32 `0x644d5241`, whose little-endian bytes are `ARMd`. The
source's `.ascii "ARM\x64"` produces exactly those four bytes — `\x64` is a hex
escape for `d`, not three characters. Reading the doc's phrasing as
`ARM` + backslash + `x` + `64` would send you looking for a bug that is not
there; and the offset is right, because 8 bytes of instructions plus six
`.quad`s is 0x38.)

The FD that follows starts with `0e 2a 00 14`, the branch to the PEI core
entry, and its firmware volume header carries
`EFI_FIRMWARE_FILE_SYSTEM3_GUID` with `FvLength = 0x300000` — matching
`FD_SIZE` and the `UEFI FD` region `0x9FC00000/0x00300000` in this board's own
`uefiplat.cfg`.

So: format right, addresses right, board right, verification off. **Nothing in
the image is known to be wrong, and it still does not run.** That is a much
more useful position than "the payload might be broken", and it moves the
question to ABL's decision rather than the file's contents.

### `fastboot getvar kernel` returns `uefi`, and it means nothing

After rebooting, the device came up in **fastboot**, and `getvar all` reported:

```
(bootloader) product:gauguinpro
(bootloader) is-userspace:no
(bootloader) kernel:uefi
```

That last line is tempting to read as "ABL examined our image and recognised a
UEFI kernel". It is not. Decompressing ABL's own EFI volume (an LZMA stream at
offset `0x3078` of `part-abl.img`, props `0x5D`, yielding 917,704 bytes) shows
`kernel` and `uefi` as **adjacent entries in ABL's hardcoded fastboot variable
table**:

```
...  getvar:  download:  kernel  uefi  max-download-size  is-userspace ...
```

It is `fastboot_publish("kernel", "uefi")` — a constant of this build, present
whatever is in `boot`. `version-bootloader:` and `version-baseband:` are empty
in the same table, which is the same story: this ABL is a stripped release
build. **No fastboot variable on this device reports anything about the boot
attempt.**

### What ABL can tell us about why it entered fastboot

The same volume gives the list of ways in. ABL's fastboot entry points are:

- a BCB command in `misc` (`reboot-fastboot`, `boot-fastboot`) — `misc` was
  checked immediately before the reboot and was **all zeros**;
- a key combination held at power-on;
- `HandleActiveSlotUnbootable` — which **reboots** rather than serving fastboot;
- the `oem edl` / `oem poweroff` commands.

### Corrected: ABL *does* have a boot-failed-to-fastboot path, and this device takes it

An earlier version of this section said: "There is no 'boot failed, so enter
fastboot' path in the string table." **That was wrong.** It came from grepping
`strings` output for `fastboot` and reading the command list, which is not the
same as reading what the boot path does. Extracting ABL's AArch64 payload
properly (see the correction above) and mapping every string reference to the
RVA that uses it turns up, in `QcomModulePkg/Library/BootLib`:

```
0x0b1c68  No bootable slots found enter fastboot mode
0x0b1c95  Non Multi-slot: Unbootable entering fastboot mode
0x0ba75a  Slot %s is unbootable
0x0bb26c  GetActiveSlot: Slot attr: Priority %ld, Retry %ld, Active %ld, Success %ld, unboot %ld
0x0baa6d  Active Slot %s is bootable, retry count %ld
0x0baa9a  A/B retry count NOT decremented
0x0beb5b  slot-retry-count
0x0beb6c  slot-unbootable
0x0bf17f  CmdBoot: ClearUnbootable failed
```

The second line is the one that matters here. **This device reports no
`current-slot`** — `fastboot getvar current-slot` returns `GetVar Variable Not
found` — so it is the *non multi-slot* case, and if ABL judges its boot slot
unbootable it goes to fastboot. That is a mechanism that produces exactly what
was observed: a reboot that lands in fastboot and stays.

It also explains the shape of the observation better than "ABL refused the
image and fell through":

- `misc` was checked immediately before the write and was **all zeros**. If ABL
  sets an unbootable flag when a boot fails, it did so *during* the failed
  attempt, and the flag would not have been visible beforehand.
- Every subsequent reboot also went to fastboot. A one-shot refusal would not
  necessarily do that; a persisted flag would.

**This is a hypothesis, not a finding, and it is cheap to test** — the
variables are named in ABL's own fastboot handler:

```
fastboot getvar slot-unbootable
fastboot getvar slot-retry-count
fastboot oem device-info
```

and, independently, reading `misc` again *now* and comparing it with the
all-zeros that was there before the write. If ABL wrote a BCB or a flag, it is
in one of those. The BCB commands ABL recognises are short enough to grep for
by name — they sit in a table at RVA `0x0be34d`:

```
0x0be34d  boot-recovery
0x0be35d  boot-fastboot
0x0be36d  boot-bootloader
```

so the check is `strings` on the first block of `misc` for those three. (The
`reboot-recovery` / `reboot-fastboot` / `reboot-bootloader` names immediately
before them are the *fastboot command* spellings of the same three things, which
is a good reminder that a name being present in the string table says nothing
about which path uses it.) `tools/fastboot-capture.sh` asks for the first two; the `misc`
comparison needs TWRP (Power + Volume Up), which does not go through ABL.

If the flag is set, that is also the explanation for the run of failed
reboots — and clearing it is a documented ABL action (`CmdBoot:
ClearUnbootable`, and `fastboot` publishes `slot-unbootable`), not a mystery.

### Checked, and ruled out: the slot metadata is not in the GPT

ABL prints `Slot suffix %s Part Attr 0x%lx` and contains a full GPT writer
(`Updating GPT partition`, `Failed to write Gpt partition`, `Error writing
partition entries array for Primary Table`), so the obvious next guess is that
the unbootable state lives in the `boot` partition's GPT attribute bits — the
Android `bootloader_control` layout (priority / tries / successful) is exactly
that shape. **It does not, on this device, and the backup settles it.**

Parsing every GPT entry in `LUN-sde.img` and grouping by the Attributes field:

| attribute | count | which partitions |
|---|---|---|
| `0x0000000000000000` | 19 | `qupfw`, `apdp`, `devcfg`, `aopbak`, `uefisecapp`, `tzbak`, `hyp`, **`boot`** |
| `0x0000000000000001` | 2 | `imagefv`, `imagefvbak` |
| `0x1000000000000000` | 41 | `multiimgoem`, `sec`, `limits`, `vbmeta*`, `aop`, `uefivarstore`, `storsec`, … |

Bit 60 only, on firmware that is read-only, which is the Qualcomm "read-only"
convention rather than A/B metadata — `docs/02` already records the same bit
meaning read-only for `super`. **`boot` is attribute 0**: no priority, no tries,
no unbootable bit, nothing. There is no slot metadata in this GPT to consult,
which is consistent with `current-slot` not existing, and it kills any theory of
the form "the failed boot set a flag in the partition attributes". If a flag was
set, it is in `misc`.

### One enumeration, then nothing: ABL never handed off and reset back

The observation itself, from the host side:

```
21:54:43  adb reboot
21:54:49  usb 3-1: new high-speed USB device number 2
21:54:49  ENUMERATED  18d1:d00d   (iProduct "Android", iInterface "fastboot")
```

Six seconds, then ABL's fastboot descriptor. **No further USB event followed** —
no disconnect, no re-enumeration. So ABL did not hand control to anything and
then reset back; it enumerated as fastboot once and stayed there. Whether it
attempted the boot at all is not established by this, and is the open question.

### The next attempt is scripted, because device time is the scarce resource

Answering that question costs one physical reset per attempt — a stranded
fastboot needs the power button, and the phone has no UART to log to. So the
next round is a single script rather than a sequence of questions:

```
tools/fastboot-capture.sh
```

It probes ABL first and stops with a clear message if ABL is not answering,
then spends the responsive window on the three log-dump commands ABL carries
for boards like this one — `oem uefilog`, `oem lkmsg`, `oem lpmsg` — plus
`oem device-info` and `getvar all`, every one of them redirected to a file.

The reading is unambiguous either way:

- `BootStats: ID-n: Kernel Load Start` with no matching `Kernel Load Done`
  means ABL began loading `boot` and stopped. The failure is in the image, and
  the next line in its log names the check it failed.
- No `BootStats` at all means ABL never reached its boot path, and the payload
  is not implicated — something before it (a BCB, a key, a slot decision)
  routed to fastboot.

`is-userspace:no` in the same capture confirms it is ABL's own fastboot rather
than `fastbootd`; the `flash:`/`erase:`/`oem unlock` command table found in
`abl`'s volume says the same thing, so this is a check and not a hope.

### `oem fbreason` was in the command list all along

This one is worth its own heading because of how it was missed. The very first
`fastboot getvar all` dump showed the command table, and in it, among
`oem lkmsg`, `oem lpmsg`, `oem uefilog` and the rest:

```
0x0be2e4  oem fbreason
```

It was read past. Disassembling the payload properly and dumping the string
block around it shows what it prints — ABL's complete set of reasons for being
in fastboot:

```
0x0bf375  Reason:Down Key Press
0x0bf38b  Reason:Reboot Bootloader
0x0bf3a4  Reason:LoadImageAndAuth Fail
0x0bf3c1  Reason:BootLinux Fail
0x0bf3d7  Reason:Unknown
0x0bf3e6  Powerup Reason: %x
```

Those five strings discriminate **exactly** the cases that have been
indistinguishable for the whole of P2:

- `LoadImageAndAuth Fail` — ABL *tried* to load `boot` and could not. The
  payload was reached, and the failure is in the image.
- `BootLinux Fail` — it loaded, and failed after that. Also "the payload was
  reached", further along.
- `Down Key Press` — a button was held, and nothing about our work is implicated.
- `Reboot Bootloader` — something deliberately asked for fastboot, which with
  `misc` verified all-zeros points at a control or a command rather than a
  failure.
- `Unknown` / `Powerup Reason: 0x...` — inconclusive, and says so.

**This is the feedback channel that was declared not to exist.** The document
already says, correctly, that ABL's boot-path failures go to a UART this phone
lacks — but the *decision* to enter fastboot, and whether the boot path was
entered at all, is separately recorded and separately readable, and it was
available from the first session. `tools/fastboot-capture.sh` now asks for it
first, before anything else.

The five strings are not chosen by a chain of `if`s. Disassembling the handler
shows one 64-bit pointer table at **VA 0xc01b0**, indexed by a small integer:

```
code 0 -> 0x0bf375  "Reason:Down Key Press"
code 1 -> 0x0bf38b  "Reason:Reboot Bootloader"
code 2 -> 0x0bf3a4  "Reason:LoadImageAndAuth Fail"
code 3 -> 0x0bf3c1  "Reason:BootLinux Fail"
code >3 -> 0x0bf3d7 "Reason:Unknown"
```

and the value being indexed is a **global at VA 0xc3000** whose initial contents
are `4`. Three things follow, and the third is the one that matters:

- The reason is stored in ordinary ABL memory, not in the partition or in the
  UART. It is written when ABL decides to enter fastboot and is still there when
  the command is served in that same session, which is why asking after the fact
  works at all.
- The complete set of answers is those five. There is no sixth.
- **`Reason:Unknown` is not "we could not tell".** It is the *default* — the
  value is 4 when nothing has touched it. So an answer of `Unknown` means no
  fastboot-reason path was taken, which is itself informative rather than a
  dead end.

Searching the image for writers of that global by ADRP+store finds exactly one:
a store of `0` (`Down Key Press`) guarded by the power-on reason being 2 or 8.
So **`Down Key Press` is worth distrusting as evidence** — it is set from the
power-on reason, and a reset done by holding the power button could produce it
regardless of what our firmware did. That is the argument for powering on
normally, with no keys held, before asking: otherwise the answer may be an
artefact of how the phone was restarted.

Codes 1–3 are set somewhere this search did not reach (a narrower store width,
or through a pointer), so their exact trigger conditions are not pinned down.
That does not affect the reading — the table above is the complete answer set,
and `LoadImageAndAuth Fail` or `BootLinux Fail` mean what they say.

### The way back is confirmed to work, not just intended to

The A/B control — flash the stock `boot` back and see whether the phone boots
Android — is only a control if the file is good, so it was verified rather than
assumed. `~/backup/gauguin/images/part-boot.img` still hashes to
`50ef59be…8ef3`, the value read off the device before the write, and the carve
pass does not overwrite an existing `part-boot.img`. So the restore is one
128 MB write of a file that is byte-identical to what this phone booted with
two hours earlier.

`tools/restore-stock-boot.sh` performs it, and refuses to write anything until
that hash matches — a restore script that will write whatever it is pointed at
is not a safety net. It takes either route, because they fail independently:
`fastboot flash boot`, or `adb push` + `dd` from TWRP, which does not go
through ABL's fastboot at all. Both are one command, and it says which answer
means what:

- stock boots Android → the firmware is the problem, and the device is healthy;
- stock does not boot → something other than the image changed state, and the
  payload is not implicated.

### The transfer-abort hazard, again — and it is worth a rule

`fastboot getvar all` was piped into `head -45`. `head` exits once it has its
lines, fastboot is killed with `SIGPIPE` partway through reading the response,
and ABL is left mid-reply. Every fastboot command after that hung until the
device was reset, while `fastboot devices` still worked — because that only
reads the USB descriptor and never talks to ABL at all, which is exactly what
makes it a misleading "the device is fine" signal.

This is the second time an aborted transfer has stranded ABL. The rule is
narrower than "do not abort a transfer" and worth stating precisely:

> **Never put a `fastboot` command in a pipeline that can close early.** Redirect
> to a file and read the file. `fastboot devices` succeeding says nothing about
> whether ABL is responsive.

### But that is not what the phone is stuck in

The device has been silent since, and it turns out neither recorded wedge
describes it. `tools/unwedge-fastboot.py` reads the endpoints directly and
separates the states: the truncation wedge leaves a reply unread on the IN
endpoint, the aborted-transfer wedge leaves ABL parked in a bulk OUT read that
still accepts data, and this device does **neither**. EP0 answers (descriptors,
`GET_STATUS`, even `SET_CONFIGURATION`) while both bulk endpoints are unarmed.

That combination says ABL's USB stack is running and the thread that owns the
fastboot command loop is not — which is possible because the stack is
interrupt-driven and answers from interrupt context. It also means the timeouts
seen since are not evidence of a truncation wedge, and that no host-side reset
will help: an endpoint nothing has armed cannot be reached by resetting the link
above it, which is why all three resets in `docs/08` step 0 re-enumerated the
device and changed nothing.

There is a second-hand benefit. ABL's descriptors come back, so ABL is still
resident: had a payload reached the point of taking the CPU, EP0 would have gone
with it and the device would not be presenting itself as fastboot at all. A
silent `fastboot` therefore reads as *ABL is stuck*, not as *our image ran* —
which is the opposite of the obvious interpretation.


### The P2 payload could not have executed, and one of the two reasons first given for that was wrong

Two payloads are involved and they are not the same payload, which matters because
the first write-up of this treated them as one. **Written to `boot`** was
`Mu-Silicium/Mu-gauguin.img`, 1,122,304 bytes, hash `374555a5…` — what
Mu-Silicium's own builder produces, and the subject of "The image is written to
`boot`" above. The **pair in `work/out/p2-variants/`** was built for the next device
session and has never been flashed.

Both would have been refused before any of our code ran. There was **one** cause,
not two, and it is not the header version:

| | `Mu-gauguin.img` (flashed) | `p2-variants/*.img` (built, not flashed) |
|---|---|---|
| header | v1, page 0x800 | v2, page 0x1000 |
| DTB | appended to the gzip stream | its own declared region |
| the tree inside it | 71,737 bytes, **0** `/__symbols__`, `ramoops@ffc00000` | the same stale tree |
| the DTB offset came from | GZipPkgCheck's decompressor: `0xff73b` | the header's page counts: `0x102000` / `0x303000` |
| refused at | `ApplyOverlay` | `ApplyOverlay` |

That table is not how it was found. It was found the way the project's rules say
to find it — by replaying ABL's decision path over a built image before letting one
near the phone — and the tool that does that, `tools/abl-boot-check.py`, had been
written by then and was being run only on the P1 images. On the pair:

```
ApplyOverlay: ufdt apply overlay failed: this tree has no /__symbols__ at all,
and the board overlay (dtbo entry 13, Qualcomm Technologies, Inc. Gauguin)
arrives with 158 __fixups__ it has to resolve against them
```

The flashed image was then reported as failing earlier, at
`DTBImgCheckAndAppendDT: Dtb offset goes beyond the image size` — and **that was
the replay being wrong, not the image.** It modelled only the v2 branch of the DTB
stage and ran it before the stage that supplies the offset for a v1 header, so a v1
image could only ever get a v2 diagnosis. With both branches modelled it gives the
message above instead: the same `ApplyOverlay` refusal, for the same reason.
`tools/check-payload.py` reported all of them `ok` throughout, and it is not wrong:
it checks the wrapper's structure, which was fine. What it cannot see is ABL's
*decision path*, which is what the other checker replays — and the replay has to be
right before anything can be concluded from it either.

**Reason A, retracted: `header_version = 1` is a defect, but it is not what refused
the flashed image.** What was claimed here first was that at v1 ABL locates no DTB
by any route: `DTBImgCheckAndAppendDT` enters its DTB-locating branch only
`if (HeaderVersion > BOOT_HEADER_VERSION_ONE)` (`BootLinux.c:454`;
`BOOT_HEADER_VERSION_ONE` is 1), this device's dtbo partition validates, so ABL
takes the overlay branch, calls `GetSocDtb (ImageBuffer, ImageSize, DtbOffset, ...)`
with `DtbOffset` still at the `0` the `BootParamlist` was initialised with, and
returns `NULL` on the function's first check — `if (!DtbOffset)`
(`LocateDeviceTree.c:1005`) → `"DTB offset is NULL"` → `"Error: Appended Soc Device
Tree blob not found"` → `EFI_NOT_FOUND`. Every line of that is in the source. The
conclusion does not follow from it, because that branch is not the only writer of
`DtbOffset`.

`GZipPkgCheck` runs **before** the DTB stage — `BootLinux.c:976` against `:1022` —
and for a gzip package kernel it is ABL's own decompressor that writes the field:

```c
    if (decompress (
        (UINT8 *)(BootParamlistPtr->ImageBuffer + BootParamlistPtr->PageSize),
        BootParamlistPtr->KernelSize, BootParamlistPtr->KernelLoadAddr,
        (UINT32)OutAvaiLen,
        &BootParamlistPtr->DtbOffset, &OutLen)) {
```

and what it writes is `pos = stream->next_in - in_buf + 8` — the offset, inside the
compressed blob, of the end of the gzip member plus its trailer, which is exactly
where a DTB appended to the stream begins. On a v0/v1 header that value is then used
*unchanged*, because the block that would overwrite it is the block that is skipped.
So the outcome depends on the compression, which is not something a header version
would suggest:

- **v1 + a gzip package kernel locates its DTB, and `Mu-gauguin.img` is that case.**
  Its kernel is a gzip member with `FNAME = "SILICIUM_UEFI.fd-bootshim"` and the tree
  appended behind it; replaying the vendor's decompress — skip the 10-byte header,
  skip `FNAME`, inflate raw, `pos = consumed + 10 + fname + 8` — lands on `0xff73b`,
  where the next four bytes are `d0 0d fe ed`. The DTB stage accepted that, and
  `GetSocDtb` returned the tree it found there — the stale one. That is how the
  refusal got as far as `ApplyOverlay` at all.
- **v1 + a raw kernel cannot.** Nothing writes `DtbOffset` on that path, it stays 0,
  and `GetSocDtb` refuses it on its first line. `tools/make_boot_image.py --profile
  silicon --compression none` builds exactly that image, and the corrected replay
  says so in ABL's own words:

```
FAIL Mu-silicon-none.img
     DTBImgCheckAndAppendDT: GetSocDtb: "DTB offset is NULL" -> "Error: Appended
     Soc Device Tree blob not found" -> EFI_NOT_FOUND. header_version is 1, and ABL
     computes no DTB offset from a v0/v1 header at all - "DT size doesn't apply to
     header versions 0 and 1" (BootLinux.c:1239) - so the offset can only have come
     from the decompressor or a patched kernel header, and nothing before
     DTBImgCheckAndAppendDT writes it, so it is still 0
```

while the same build with `--compression gzip` passes every check ABL makes before
it hands control over, at the same header version and the same page size.

So `gauguin.toml`'s `header_version = 1` with `append_dtb = true` is still a real
defect — it makes the builder's output depend for its DTB location on a property of
the compression, which is not something anyone would guess, and it puts the
*appended* tree out of reach on the branch that is skipped while the offset the
decompressor supplies is used as-is on the branch that runs. It is just not the
defect that was observed, and "v1 cannot locate a DTB by any route" was wrong;
what is true is that v1 computes no offset of its own, so it depends on something
else having written one.

Retracting it mattered for more than tidiness. "Refused twice over" is a stronger
claim than the evidence supported, and it hid the real single cause — a device tree
rebuilt for P1 and never rebuilt for P2 — behind a header-version theory that
happened to be about the same `toml`.

**The one cause: the tree inside it was stale by two changes, and both payloads
carried it.** It is 71,737 bytes with **0** `/__symbols__` entries and the inherited
`ramoops@ffc00000` — the address that was already known to be wrong — where the
current tree is 87,594 bytes with 217 symbols and `ramoops@d0000000`. The replay
names it in both images: the flashed one's copy sits at `0xff73b` in the kernel
blob, and the node it reports there is `reserved-memory/ramoops@ffc00000`. The
`/__symbols__` half is the same defect the P1 payloads were fixed for (`docs/07`,
above): this device's dtbo validates, so the vendor overlay is applied to our tree
on every boot, and `ufdt_overlay_do_fixups()` returns `-1` on the first symbol it
cannot resolve — here the very first one, `tlmm`, because there is no `/__symbols__`
node for it to be resolved against at all.

The P1 build had that fix. The P2 build had been done by hand against whatever
`.dtb` was lying in the kernel tree at the time, and nothing rebuilt it when the
tree changed — which is why the fix is structural rather than a corrected constant
(see below), and why the same stale tree appears in two payloads built by two
different scripts.

**The replay was fixed first, because everything else here is read off it.**
`tools/abl-boot-check.py` now models both branches of `DTBImgCheckAndAppendDT` —
the v2 branch that computes the offset from the page counts, and the v0/v1 branch
that consumes whatever `DtbOffset` already holds — and it runs the stages in ABL's
order (`GZipPkgCheck` before the DTB stage), which is what makes the v0/v1 branch
reachable at all. The gzip half is not taken on trust: the emulated `pos` is checked
against the fdt magic it is supposed to point at, and reported as an emulation
failure when that is not there. The two branches disagree about their reference
point as well — v2 offsets are from the start of the file, v0/v1 offsets from the
point ABL calls `ImageBuffer` — so the reference is printed with the number rather
than left implicit.

**Then the cause, so that it cannot recur.** The tree has one builder,
`tools/build-device-tree.sh`, used by both payload scripts, and it validates the
built blob on four properties — `/__symbols__` count, exactly one `ramoops` node,
the address, and the four `/chosen/framebuffer` properties — on both its fresh
and its `--reuse` path. The two UEFI payloads are built by
`tools/build-p2-payloads.sh`, which calls both checkers at the end and exits
nonzero on any failure, the same gate `tools/build-p1-payloads.sh` has. And
`gauguin.toml` is off v1 and off `append_dtb`, so a stray
`./build_uefi.py -d gauguin` fails for a reason that is true rather than one that
misnames the problem. Measured, after the fix:

```
work/out/p2-variants/Mu-gauguin-stock-none.img     3,248,128  v2  page 0x1000  raw
work/out/p2-variants/Mu-gauguin-stock-gzip.img     1,146,880  v2  page 0x1000  gzip
work/out/p2-variants/Mu-gauguin-silicon-gzip.img   1,138,688  v1  page 0x800   gzip
```

The first two are the pair. The third sits beside them and is deliberately not
part of it — it varies the header version, the page size and where the tree lives
all at once, so a difference between it and the others cannot be attributed — but
it is built and gated all the same, because the corrected replay shows that shape
is admissible when it carries the current tree, which was not known when the shape
was written off. All three pass `check-payload.py` and `abl-boot-check.py`,
including the real `fdtoverlay` merge of dtbo entry 13 onto the tree they carry
(296,928 bytes merged, the `ramoops@d0000000` and `framebuffer@a0000000` nodes
still in it). `check-payload.py` reads the shape off each image rather than
asserting one, so the third is not failed for the ways it is deliberately unlike
stock.

**This is worth separating from the wedge.** The phone went silent after the
write to `boot`, and it is tempting to read that silence as "the UEFI port does
not run". It is not evidence of that, and could not have been: the image was
refused at `ApplyOverlay`, before any of our code was reached, and the fastboot
state the phone is actually stuck in (`docs/07`, "But that is not what the phone
is stuck in") has ABL resident and its command loop stalled — which is ABL's own
failure, not ours. The P2 gate is still open, and the next attempt tests the port
for the first time rather than re-testing it.


### ABL keeps a log of every boot, and it needs neither the screen nor the payload

The two channels the project has been leaning on both fail at exactly the moment
they are needed. The screen needs the payload to have got as far as a console, and
pstore needs it to have got as far as a *panic* and a warm reboot. Neither can
answer the question a refused payload raises, which is not "what did our code do"
but "**what did ABL do with what we gave it**" — and ABL writes that down every
boot, to a partition, in plain text, whether or not anything of ours ever runs.

`logfs` is a FAT12 volume holding a ring of five 32 KiB files, `UEFILOG0.TXT`
through `UEFILOG4.TXT`. ABL writes the current slot as it shuts its boot services
down. Measured on this device, from the P0 dump
(`~/backup/gauguin/images/part-logfs.img`, taken before anything was ever
flashed):

```
5 live entries, 250 deleted
4096-byte sectors, 1 sector/cluster, 1 reserved, 2 FATs, 512 root entries
UEFILOG0.TXT  32,768 bytes   Start EBS   pureason = 0x40081   312 lines
UEFILOG1.TXT  32,768 bytes   Start EBS   pureason = 0x80040   286 lines
UEFILOG2.TXT  32,768 bytes   Start EBS   pureason = 0x80080   306 lines
UEFILOG3.TXT  32,768 bytes   Start EBS   pureason = 0x40001   284 lines
UEFILOG4.TXT  32,768 bytes   Start EBS   pureason = 0x40081   304 lines
```

(Line counts are non-blank lines, which is what `tools/read-logfs.py` reports.)

**Which slot is newest is read off the directory, not assumed.** The live entries
sit at directory indices 2, 4, 6, 8 and 10 in the order 4, 3, 2, 1, 0, and the 250
deleted entries beside them repeat that same descending cycle. Directory entries
append in write order, so the name written *last* is `UEFILOG0.TXT` — which is why
0 is the newest, and why `tools/read-logfs.py` prints it first. The 250 deleted
entries are 50 complete cycles of the five names, so the ring has already been
overwritten about fifty times and still holds five boots. **Every one of the five
reached `Start EBS`**, i.e. completed handover — which is what makes it a
baseline. The question about a new session is always "does its slot look like
these, and if not, where does it stop", and the first line that differs is the
answer.

The log covers ABL's whole life, in order, and each stage answers a different
question:

| line in the log | what it settles |
|---|---|
| `UEFI Ver : 5.0.210418.BOOT.XF.3.3-00285-BITRALAZ-4` | which ABL this is — the build string the whole ABL analysis in this document is against |
| `DisplayDxe: MDPPLATFORM_PANEL_J17_TIANMA_NT36672C_LCD_DSC_VIDEO` | the panel, so "the screen stayed black" can be separated from "the backlight was never told" |
| `PON Reason is 129 cold_boot:1`, `KeyPress:0, BootReason:32` | which kind of boot this was — the number that makes a power-button reset distinguishable from a clean one |
| `Load Image vbmeta` / `boot` / `dtbo total time: N ms` | the boot image path was **entered**, and all three partitions were read |
| `Apply Overlay total time: N ms` | the vendor overlay was merged into **the tree we shipped** — the stage that refused every P2 image built so far |
| `Cmdline: …` | the command line ABL actually composed, not the one we asked for |
| `pureason = 0x…` | which boot this slot *is* — a power-on reason, not a verdict (below) |
| `Shutting Down UEFI Boot Services: N ms` → `Start EBS` | handover happened |

Two lines deserve a warning attached, because both look like better evidence than
they are.

**`Load Image boot total time` does not tell you which image was in `boot`.** It
looks like it should: Android's `boot` is ~64 MB and contains ~1 MB. But the
number comes from `AvbReadFromPartition` (`avb_ops.c:308`), and the size it reads
is decided *before* the transfer — `image_size = hash_desc.image_size`, except
that `if (allow_verification_error)` it is overwritten with
`get_size_of_partition()` (`avb_slot_verify.c:173-175`, with the comment saying
so: *"just load the entire partition"*, because `fastboot flash boot` with a
bigger image is a normal workflow). This phone boots in **orange** state with
verification errors allowed — the log says so two lines later, `boot state is:
orange(1)`, and `fatal error is not set` — so the read is always the whole
partition and always costs about the same. All five baseline slots: 256, 256, 255,
256, 255 ms. It is a fixed cost, not a signature.

**`pureason` identifies the boot; it does not grade it.** The five baseline slots
carry 0x40081, 0x80040, 0x80080, 0x40001 and 0x40081, and the shape of each is
the `PON Reason is N` printed earlier in the same slot plus high bits (`PON
Reason is 129` with `pureason = 0x40081`; `PON Reason is 64` with 0x80040). So it
is a power-on reason recorded through to the end of the log — useful for matching
a slot to a physical session and for noticing that two boots were the same kind —
and **not** a statement about whether the boot succeeded, which is what the stage
table is for. It is also **not only a log line**: the phone's ABL string table
(`work/abl_pe.bin`, at `0xb814b`) has `"pureason = 0x%x"` immediately followed by
`"pureason"` and `"ERROR: Cannot update chosen node [pureason] ..."`, next to the
same pair for `linux,initrd-end`. So ABL writes it into the device tree as well,
under `/chosen` — which means a payload that can print anything at all can read
the reason recorded for *this* boot from `/proc/device-tree/chosen/pureason`,
without a host, without adb, and without the log partition.
(`UpdateDeviceTree.c:775-843` in the vendored source shows the same mechanism for
`bootargs`, `rng-seed`, `kaslr-seed` and `linux,initrd-*`; `pureason` is not in
that copy — see below — but the mechanism is the same one.)

**The refusal itself lands in this log.** `Apply Overlay`
(`0xb6bca`), `DTB offset is NULL` (`0xb6e4f`) and `Appended Soc Device Tree`
(`0xb69bf`) are all strings in the phone's ABL PE, so the failure mode that
refused both P2 payloads (`docs/07`, above) is written down at the time it
happens. That is the whole point: a refused payload produces a **silent phone and
a loud log**, and until now the project had no way to read the second one.

Three ways in, and the tools pick whichever answers:

```sh
tools/pull-bootloader-log.sh          # tries all three in order, then reads it
tools/read-logfs.py work/bllog-.../logfs.img -o work/bllog-.../slots
tools/read-logfs.py work/bllog-.../slots/UEFILOG0.TXT --full
```

| route | needs | reaches |
|---|---|---|
| `fastboot oem uefilog` | ABL's fastboot answering | **unexercised on this phone** — see below |
| `dd if=/dev/block/by-name/logfs` | TWRP (no root needed) or Android with `su` | the partition — the last boot that completed a shutdown. **This is the route that has been used** |
| the P0 dump | nothing | the baseline, and it is the only route that always works |

The command name is confirmed from the phone's ABL, not guessed: `oem uefilog`
sits at `0xbe309` in a NUL-separated table with `oem fbreason`, `oem lkmsg`,
`oem lpmsg`, `oem mtdoops`, `oem edl`, `oem uart-enable` and `oem poweroff`, and
the backing protocol is visible too — `gLogFsProtocol save logfs files failed:%d`
next to `gLogFsProtocol get logfs files failed:%d`, and `Failed to save logfs
files` next to `Failed to get logfs files`. There is no usage string anywhere in
the PE, so what arguments the command takes is **not knowable from the binary**;
the tool calls it bare, which is the only form that can be justified from what is
there.

**What that route returns is not knowable from the binary either, and it has
never run.** `save` and `get` being separate calls is consistent with a live
in-memory buffer that `oem uefilog` dumps — which would be the only way to read a
boot that never reached the shutdown that writes the file — and equally consistent
with `save` having already put the text on the partition and `get` reading it
back out. `work/fb-uefilog.txt` is **0 bytes**: it was created by a session where
ABL had already stopped answering, so the command has been attempted exactly once
and returned nothing. Recorded because a route described here as the fastest one
would otherwise be read as one that works, and it is the only one of the three
with no evidence behind it.

**The vendored ABL is a reduced copy, and that bounds what can be asked of a
Mu-Silicium build.** `work/ref/mu_qcommodulepkg` has 66 `.c` files and its
`FastbootLib/FastbootCmds.c` registers six commands — `enable-charger-screen`,
`disable-charger-screen`, `off-mode-charge`, `select-display-panel`,
`device-info`, `display-cmdline`. The phone's own ABL registers all of those
*plus* `fbreason`, `lkmsg`, `lpmsg`, `uefilog`, `mtdoops`, `edl`, `uart-enable`
and `poweroff`. So `oem fbreason` and `oem uefilog` are commands the **stock** ABL
answers and a Mu-Silicium-built one would not, which is worth knowing before
reading a missing answer as a wedged transport — and it is a second, independent
reason the logfs partition route matters: it does not go through ABL at all.

Which is the property that makes this channel the one to reach for. `docs/08`
step 4.5 reads the *payload's* log and needs the payload to have panicked and
rebooted itself; `docs/08` step 4.6 reads *ABL's* log and needs only a block
device.

## P3 groundwork: which SoC's ACPI tables gauguin can use, decided on the GICC geometry

The P2 platform ships no ACPI tables on purpose (see "The ACPI tables are absent
by choice" above), so P3 has to produce them, and the first question is which of
`Silicium-ACPI`'s 18 Qualcomm table sets is close enough to be worth adapting.
`Platforms/Realme/bitra/AcpiTables.inf` answers the question the obvious way — it
is the only Bitra-family platform file in the tree and it references the **Kona**
set — and that answer is wrong for this device. The measurement below is why.

**What the APIC table has to get right is the GICC geometry.** Every GICC subtable
carries the PPI INTIDs for the PMU and the virtual timer and the base address of
that core's redistributor frame, and a wrong one is not a degraded boot but an
interrupt controller the OS cannot bring up. Gauguin's own device tree states its
three values, and they are not negotiable:

| | gauguin (`Resources/DTBs/gauguin.dts`) | as INTID |
|---|---|---|
| `pmu { interrupts = <0x01 0x05 0x08> }` | `0x05` | **21** (0x15) — PPI 5, `16 + 5` |
| `interrupt-controller@17a00000 { interrupts = <0x01 0x08 0x04> }` | `0x08` | **24** (0x18) — PPI 8, `16 + 8` |
| `interrupt-controller@17a00000` … `reg = <… 0x00 0x17a60000 0x00 0x100000>` | `0x17a60000` | GICR base |

The second row was wrong on the first pass and the error is worth naming, because
the two candidates are one node apart and only one of them is a MADT field. The
value 24 is the **VGIC maintenance interrupt** — field `[064h] Virtual GIC
Interrupt` in the disassembly, `VGIC Maintenance Interrupt` in the ACPI spec — and
gauguin states it on the **GIC node itself**, `interrupts = <0x01 0x08 0x04>`. The
`timer` node is not its source: its four PPIs (`0x01 0x02 0xff08` and friends) are
the architectural timers, they land in `GTDT`, not in the MADT. Reading 24 off the
timer node gets the right number from the wrong table, which is the kind of thing
that stays hidden until the interrupt is wrong on hardware that is not this one.

**The DT's own arithmetic, stated once so it is not re-derived wrongly.** A DT
`interrupts` triple is `<type number flags>` and the INTID is not the number: for
PPIs (type 1) it is `16 + number`, for SPIs (type 0) it is `32 + number`. Every
value in this section uses that, and getting it backwards is what produced the
wrong UFS conclusion corrected further down.

with `uefiplat.cfg:81-84` confirming the same map from the other direction (GICD
`0x17A00000` len `0x170000`, GICR `0x17A60000` len `0x100000`, QTIMER `0x17C00000`
len `0x110000`) and `BitraPkg.dsc.inc:45-52` pinning them as PCDs
(`PcdGicDistributorBase|0x17A00000`, `PcdGicRedistributorsBase|0x17A60000`,
`PcdArmArchTimerSecIntrNum|17`, `PcdArmArchTimerIntrNum|18`).

**All 18 sets, read out of `Decompiled/APIC.dsl`.** Columns are the GICC subtable's
length, and the per-core PMU INTID, virtual-timer INTID and redistributor base;
`stride` is the step between consecutive cores:

| SoC set | GICC subtables | len | PMU | VGIC maint | GICR base | stride |
|---|---|---|---|---|---|---|
| Blackbolt | 8 | 82 | 22 | 25 | 0x17B00000 | 0x20000 |
| Cedros | 8 | 80 | 23 | 25 | — | — |
| Divar | 8 | 82 | 22 | 25 | 0x0F300000 | 0x20000 |
| Hana | 8 | 80 | **21** | **24** | — | — |
| Kailua | 8 | 80 | 23 | 25 | 0x17180000 | 0x40000 |
| Kamorta | 8 | 82 | 22 | 25 | 0x0F300000 | 0x20000 |
| Kodiak | 8 | 82 | 23 | 25 | 0x17A60000 | 0x20000 |
| **Kona** | 8 | 80 | 23 | 25 | — | — |
| Lahaina | 8 | 80 | 23 | 25 | — | — |
| **Moorea** | 8 | 82 | **21** | **24** | **0x17A60000** | **0x20000** |
| **Napali** | 8 | 82 | **21** | **24** | **0x17A60000** | **0x20000** |
| Nazgul | 8 | 82 | 22 | 25 | 0x17B00000 | 0x20000 |
| Nicobar | 8 | 82 | 22 | 25 | 0x0F300000 | 0x20000 |
| Palawan | 8 | 82 | 23 | 25 | 0x17180000 | 0x40000 |
| Palima | 8 | 82 | 23 | 25 | 0x17180000 | 0x40000 |
| **Rennell** | 8 | 82 | **21** | **24** | **0x17A60000** | **0x20000** |
| Starlord | 8 | 82 | 22 | 25 | 0x17B00000 | 0x20000 |
| Waipio | 8 | 82 | 23 | 25 | 0x17180000 | 0x40000 |

**Exactly three sets reproduce gauguin — Moorea, Napali and Rennell — and every
other one fails on at least one field.** Hana gets the two INTIDs right and carries
no GICR base at all; Kodiak has the right GICR base and the wrong INTIDs; the rest
miss both. **Kona, the set the only Bitra-family platform file uses, matches
nothing**: it carries no redistributor base (`GICR base —`) and INTIDs 23/25 where
gauguin needs 21/24. A Kona APIC is therefore not a starting point that needs
correcting, it is a different interrupt layout.

**There is a trap in reading these dumps and it caught this pass.** Every value in
a `Decompiled/*.dsl` is printed **in hex**, per that file's own header line
(`FieldName : FieldValue (in hex)`), but the small ones have no `0x` and no letters
to give it away: Moorea's GICC header reads `Length : 52`, which is 0x52 — **82
bytes**, which is what the raw `Raw Table Data:` hexdump confirms at every subtable
boundary (`0B 52`). Read as decimal 52 it looks like a table one third smaller than
it is, and the eight entries then appear to end 240 bytes before the GICD subtable
starts.

**Moorea is the one to take, and there is a second, independent reason.** Moorea is
the set used by `Platforms/Xiaomi/surya` — and surya is this project's own reference
platform: `tools/make_uefi_platform.py` rewrites `suryaPkg/Include/APRIORI.inc` to
point at `Binaries/gauguin/`, and it is also the source of seven of the eight MDP
stream IDs this port hands `ArmSmmuDetach` (see "The MDP stream IDs … are **not**
verified" above). So the tables come from the platform this port is already built
on rather than from a stranger.

**Moorea's two other geometry tables were then checked against gauguin's device
tree, and both are drop-ins — which the APIC argument alone did not establish.**
The APIC decides the interrupt controller; `GTDT` decides the timers, and a wrong
one is a machine with no working clock:

| | gauguin (`Resources/DTBs/gauguin.dts`) | Moorea `GTDT` |
|---|---|---|
| `timer { interrupts = <…> }` | PPI 1, 2, 3, 0 → INTID **17, 18, 19, 16** | `0x11, 0x12, 0x13, 0x10` |
| `timer@17c20000` | block `0x17C20000`, frame 0 `0x17C21000` + `0x17C22000` | `Block Address 0x17C20000`, `Base 0x17C21000`, `EL0 Base 0x17C22000` |
| `frame@17c21000 { interrupts = <0x00 0x08 0x04 0x00 0x06 0x04> }` | SPI 8, SPI 6 → INTID **40, 38** | `Timer Interrupt 0x28`, `Virtual Timer Interrupt 0x26` |

All four architectural-timer INTIDs, the platform timer block, its frame address
and both of that frame's interrupts agree — eight values from two sources that were
never derived from one another. `FACP` is the same: `PSCI Compliant : 1` with
`Must use HVC : 0` is gauguin's `psci { compatible = "arm,psci-1.0"; method = "smc" }`,
it is a hardware-reduced table (`Hardware Reduced : 1`, `PM Profile : 08 [Tablet]`)
with every legacy PM block left at zero, and its `Reset Register` is inert because
`Reset Register Supported` is clear, so the reset path is PSCI in both. **So the
three tables that come from Moorea are verified against gauguin's own tree rather
than assumed transferable.** The other seven in the bundle are a different matter
and are dealt with below.

**What the SoC directory supplies is a bundle, not
a fixed list** — Moorea's holds ten tables (`APIC`, `CSRT`, `DBG2`, `FACP`, `FACS`,
`GTDT`, `IORT`, `MCFG`, `PPTT`, `DSDT_Minimal`) and each platform's
`AcpiTables.inf` picks its own subset: `Platforms/Realme/bitra` took three of
Kona's, `Platforms/Xiaomi/surya` takes nine of Moorea's plus its own `DSDT.aml`.
So choosing Moorea decides where the values come from, not which tables exist, and
`gauguin/AcpiTables.inf` is where the subset is chosen. **The subset a shipped
platform picks is small, and the two Moorea users disagree about it**:
`Platforms/Lenovo/j706f` — a Snapdragon 7150 device, Moorea's own SoC — takes
exactly `APIC`, `FACP` and `GTDT` and nothing else, while `Platforms/Xiaomi/surya`
takes all nine. That gap is the whole question, and it is decided by what each of
the remaining seven describes:

| table | what it pins down | verdict for gauguin |
|---|---|---|
| `MCFG` | PCIe ECAM segments | **wrong shape** — gauguin's device tree has **no `pcie` node at all** |
| `DBG2` | the debug UART's address | Moorea's address; gauguin's console is the framebuffer |
| `IORT` | SMMU topology and stream IDs | Moorea's devices and stream IDs — gauguin's differ |
| `PPTT` | cache hierarchy and package topology | Moorea is A76/A55, gauguin is A77/A55 |
| `CSRT` | non-ACPI device resources | Moorea's device list |
| `FACS` | the firmware-wake handshake block | generic; carries no device data |
| `DSDT_Minimal` | eight `ACPI0007` CPU devices | a skeleton gauguin's own DSDT supersedes |

**`j706f`'s three is the set to take**, and the reason is not austerity: APIC,
FACP and GTDT are the three tables whose content is *SoC geometry* — INTIDs, GICR
frames, timer INTIDs — and those are the three that have now been checked value by
value against gauguin's own device tree and agree exactly. The other four are
device inventories, and each would have to be re-derived from gauguin's tree before
it could be trusted; a wrong `PPTT` or `IORT` is not a missing feature, it is the
OS told something false about hardware it will then touch. `FACS` is generic and
costs nothing, so it comes along. `DSDT_Minimal` is superseded by gauguin's DSDT,
which carries the same CPU devices.

**The set decides APIC/FACP/GTDT. It does not decide the DSDT, and the DSDT is
where gauguin actually differs.** `Platforms/Realme/bitra` ships its own
`DSDT.aml` alongside the borrowed Kona set, and reading it revises what was
expected: bitra's `Device (UFS0)` declares its interrupt as

```
0x00000129,        /* bitra DSDT, _CRS */
```

and gauguin's device tree declares the same controller as

```
interrupts = <0x00 0x109 0x04>;    /* gauguin dts, ufshc */
```

`0x129` is 297. Gauguin's `0x109` is SPI 265, and a DT SPI number is an index, not
an INTID: the INTID is `32 + 265` = **297 = 0x129**. The two files agree, and the
earlier reading of this pair — that bitra was 32 too high, and that gauguin's DSDT
needed a correction there — was wrong, because it compared a DT index against an
ACPI GSI. **An ACPI `Interrupt ()` resource carries the INTID directly**, so
gauguin's UFS interrupt is 297 and bitra's value is already gauguin's.

The same read-through produces the other half of the answer, which is that the
DSDT's device nodes are mostly *in*herited rather than *authored*, because the
SDM7xx/SM8250 Qualcomm reference they both descend from put the controllers where
gauguin's device tree says they are:

| | bitra `DSDT.aml` | gauguin device tree | |
|---|---|---|---|
| UFS0 window | `0x01D84000` + `0x00014000` | `ufs@1d84000` std `0x3000` + ice at `0x1D90000` | covered |
| UFS0 interrupt | GSI `0x129` = 297 | SPI 265 → INTID 297 | **identical** |
| UFS0 `_HID` | `QCOM24A5`, `_ADR 0x08` | — | the Qualcomm UFS driver's IDs |
| URS0 window | `0x0A600000` + `0x000FFFFF` | `usb@a600000` (dwc3 core) `0xcd00` | covered |
| URS0 interrupt 1 | GSI `0x000000A5` = 165 | `usb@a600000 { interrupts = <0x00 0x85 …> }` → SPI 133 → INTID **165** | **identical** |
| URS0 interrupt 2 | GSI `0x000000A3` = 163 | wrapper `hs_phy_irq` SPI 131 → INTID **163** | **identical** |

So three of the DSDT's load-bearing values are verified equal, and what is left to
author is small: the CPU devices (`ACPI0007`, `_UID` 0–7, which
`Silicon/Qualcomm/Moorea/DSDT_Minimal.asl` already has as source), the PMIC-sourced
`dp_hs_phy_irq` / `dm_hs_phy_irq` / `ss_phy_irq` GSIs from gauguin's `PM7250B`, and
the header's `OEM Table ID`. The UFS and USB blocks are copied from bitra with their
numbers checked against the device tree, not assumed. *(All of those are now in the
file; the three wake GSIs were the last of them, on 2026-09-25, and the note directly
below is how they were settled. The PMIC family — `SPMI`, `PMIC` and `PM01` — followed
in Step 4.63, and the `_HID`-family section further down is what made it possible.)*

### USB PHY wake interrupts

**The last of the PMIC-sourced GSIs above, and the only one this file left out for
a reason.** `tools/acpi/gauguin.asl` carried only three of bitra's five USB0
interrupts — `A5`, `A2`, `A3` — and omitted the three PHY wake lines, with a header
comment saying why: two encodings were both consistent with the evidence, `512 +
pin` and the INTID the PDC maps the pin to, and they differ. It was the same
judgement the `_HID` discussion records more loudly: a wrong GSI in a wake resource
is worse than an absent one, so it stayed out until it could be measured.

**It was measured on 2026-09-25 (Step 4.62) and the answer is `512 + pin`.** The
device tree states it in one property. `interrupt-controller@b220000` — the PDC, the
`interrupt-parent` phandle `0x62` the wake lines point at — carries

```
qcom,pdc-ranges = <0x00 0x1E0 0x5E  0x5E 0x261 0x1F  0x7D 0x3F 0x01
                   0x7E 0x28F 0x0C  0x8A 0x8B 0x0F>;
```

which is the kernel binding's `<first pin, GIC SPI, count>` triples, so the first
entry reads *pins 0–93 map to SPI 480–573*. The three wake lines are on pins 14, 15
and 17 — all inside that first triple — and `usb@a6f8800` names them:

```
interrupts-extended = <0x01 0x00 0x82 0x04  0x01 0x00 0x83 0x04
                       0x62 0x0e 0x03  0x62 0x0f 0x03  0x62 0x11 0x04>;
interrupt-names     = "pwr_event", "hs_phy_irq", "dp_hs_phy_irq",
                      "dm_hs_phy_irq", "ss_phy_irq";
```

so `dp_hs_phy_irq` is PDC pin 14, `dm_hs_phy_irq` pin 15 and `ss_phy_irq` pin 17.
`INTID = 32 + SPI` gives **526, 527 and 529**. The `480 + pin` reading — 494, 495,
497 — had stopped one step short: `480 + pin` is a GIC *SPI* number and an ACPI
`Interrupt ()` resource carries the *INTID*. That is exactly the slip the UFS pair
above documents, on a different number.

Two more sources agree, and all three are independent of one another:

| source | what it says |
|---|---|
| the PDC's own `qcom,pdc-ranges` | the device's pin-to-SPI map, not a transcription — pins 14/15/17 → SPI 494/495/497 |
| `Platforms/Realme/bitra` USB0 `_CRS` | carries exactly `0x20E`, `0x20F` and `0x211`, in that order, with Edge, Edge and Level triggers — matching the dts type cells 3, 3 and 4 |
| every `PM0x` node in the 66-table corpus | 21 of the 66 tables have one at all; **all 21** carry `0x201` = 513, which is the same arithmetic on the SPMI arbiter's own PDC pin 1 — and gauguin's `spmi@c440000` says `interrupts-extended = <0x62 0x01 0x04>` |

The three are now in `USB0`'s `_CRS`, in bitra's order and with bitra's trigger
types. **The DSDT grew from 1,520 to 1,547 bytes**, which is the first ASL change in
this port that moves the AML: the earlier comment-only drift recorded further down
did not. Everything after the table in the volume therefore shifted by 28 bytes —
`APIC` `0x54dabc`→`0x54dad8`, `FACP` `0x54dd94`→`0x54ddb0`, `FACS`
`0x54deac`→`0x54dec8`, `GTDT` `0x54def0`→`0x54df0c`; the offsets in the table below
are the ones the previous build had. The payload built from it is
`work/out/p2-phywake/` (Step 4.62 in `docs/08-device-session.md`).

**One thing this pass turned up that is not a correction.** gauguin's `USB0` carries
`0xA2` (`pwr_event`) and bitra's does not, and that had gone unmentioned in every
earlier audit of this file even though the table above lists it. It is right:
gauguin's own dts gives `pwr_event` as SPI 130 → INTID 162, which is bitra's *other*
`A2`-shaped hole filled from this device rather than inherited. It stays.

### The `_HID` is patterned by SoC family, and that retires the "names are free" reading

**Every earlier note in this file about the `_HID` question treated the choice as open.
It is not, and Step 4.63 measured the pattern.** A Qualcomm `_HID` is `QCOM` plus two
hex pairs, and the pairs are not independent: the **low** pair is a block index shared
by a whole generation of the table generator, and the **high** pair is the SoC family.
Joining by *name* rather than by address — because a reference `_CRS` is often much
coarser than the block it declares, which is the correction this file already records
about the address join — `tools/acpi-hid-census.py --functions` gives five blocks whose
index is constant across families:

| block | 02 | 05 | 08 | 09 | **0A** | 0C | 14 | 1A | 25 | 60 |
|---|---|---|---|---|---|---|---|---|---|---|
| `GIO0` (TLMM) | 17 | 0D | 0D | 0C | **0C** | 0C | 0D | 0C | 0C | 16 |
| `SPMI` | 16 | 0C | 0C | 0B | **0B** | 0B | 0C | 0B | 0B | — |
| `MMU0` | 12 | 09 | 09 | 09 | **09** | 09 | 09 | 09 | 09 | — |
| `QDSS` | 8C | 5A | 5A | 56 | **56** | 56 | 5A | 56 | 56 | — |
| `RFS0` | 35 | 17 | 17 | 15 | **15** | 15 | 17 | 15 | 15 | — |

`{09, 0A, 0C, 1A, 25}` are one generation — arbiter `0B`, GPIO controller `0C`, MMU
`09`, QDSS `56`, RFS `15` — and `{05, 08, 14}` are another. **So copying an id from a
table on another family is predictably wrong, and the failure is silent:** a device node
whose `_HID` no driver claims does not warn, fail or fall back, it is simply absent
from Device Manager. That is worse than a missing node, which at least reads as
missing.

The family byte for gauguin is **`0A`**, and it is measured three ways, by three
sources that do not share a method:

| source | what it says |
|---|---|
| the name join above | `GIO0` = `QCOM0C` and `SPMI` = `QCOM0B` on lisa, a52sxq, renoir, Cedros, Kailua, Waipio, venus, vili, lemonade and Lahaina — twelve tables — and gauguin's `pinctrl@f100000` is `0x0F100000 + 0x300000`, which is lisa's window exactly |
| the 21 PMIC-GPIO nodes in the 66-table corpus | nine distinct `_HID`s whose middle byte is a family and nothing else — `QCOM0269`/02, `QCOM0530`/05, `QCOM0830`/08, `QCOM092D`/09, `QCOM0A2D`/**0A**, `QCOM0C2D`/0C, `QCOM1430`/14, `QCOM1A2D`/1A, `QCOM252D`/25. gauguin is `QCOM0A2D` |
| the SC7280/Kodiak Windows driver set | 112 `.inf`, 158 ids. Under `0A` it claims 4 of the 5 distinct index ids — `0B` qcspmi7280, `0C` qcgpio7280, `10` qci2c7280, `16` qcuart7280 — covering **8 of gauguin's 10 indexable blocks**. Under each of the other eight candidate bytes it claims **0 of 5**, covering 0 of 10 |

The two blocks `0A` does not cover are `SE0` and `SE6` (both index `0E`), which no
`.inf` in the set names — a real gap in that set, not a doubt about the byte. Step 4.70
made that gap a measurement rather than a reading of one package: the same search run over
all five driver trees on this host (`inf-7280`, `qrd`, `qrd/7280_CLS`,
`windows_silicon_qcom_kodiak`, `windows_silicon_qcom_rennell`) finds `QCOM0A0E` in none of
them, and those four carry no `.inf` at all. So no SPI engine on this SoC can be driven by
anything on this machine, which is why the two live SPI engines are measured and not
declared. And the
set states `QCOM0A2D` and `QCOM0A0C` itself in `qcpmicgpio7280.inf` and
`qcgpio7280.inf`, independently of anything the census computed.

**The outstanding TLMM node is `QCOM0A0C`.** That is the one decision this settles
that was still open in this file's P3 list, and it was previously held to be a choice.

### What exists and what is missing, so the next session starts from the right
place.** Present: the table sets above, `iasl` at `/usr/bin/iasl`, the ASL source
for the CPU skeleton at `Silicon/Qualcomm/Moorea/DSDT_Minimal.asl`, and 20 platform
`AcpiTables.inf` files to copy the shape from — all of them
`FILE_GUID = 7E374E25-8E01-4FEE-87F2-390C23C606CD`,
`MODULE_TYPE = USER_DEFINED`, `INF_VERSION = 0x00010005`. Missing, all four of them:

1. An `AcpiTables.inf` for gauguin, listing `DSDT.aml`, `Common/SSDT.aml`,
   `Moorea/APIC.aml`, `Moorea/FACP.aml`, `Moorea/FACS.aml`, `Moorea/GTDT.aml` —
   `Platforms/Lenovo/j706f`'s list plus `FACS` — and the `gauguin.dsl`/`.asl` it
   compiles. The DSDT is the only genuinely new content.
2. The `INF RuleOverride = ACPITABLE …` line at
   `gauguin.fdf:73`, which is present and commented out.
3. A `[Components]` section in `gauguin.dsc` — **the file has none at all**, so
   adding the table module is not a one-line insert.
4. `BitraPkg/Library/AcpiTableUpdateLib/AcpiTableUpdate.c`, whose `UpdateAcpiTables ()`
   is the deliberate no-op with the P3 TODO. Nothing needs correcting in the three
   borrowed tables as they stand, so this stays a no-op until something does.

> **Superseded 2026-09-25. Items 1–3 were done the same day this list was written,
> and the fourth is still open.** The list above is kept because it is what the
> port's state was when the table choice was made, but nothing in it should be
> acted on. What replaced it:
>
> - **1.** `Silicium-ACPI/Platforms/Xiaomi/gauguin/AcpiTables.inf` exists — six
>   `ASL|` entries, exactly the set argued for above, plus `Common/SSDT.aml`. It
>   resolves as `gauguin/AcpiTables.inf` because `Silicium-ACPI/Platforms/Xiaomi`
>   is on `PackagesPath`, the way surya's does. The DSDT has no separate
>   `gauguin.dsl`: `tools/acpi/gauguin.asl` is the source and
>   `tools/sync-uefi-platform.sh` compiles it to the 1,520-byte `DSDT.aml` beside
>   the `.inf`.
>
>   The byte figure for that source moved and the copies did not, so the chain is
>   worth writing down. `make_uefi_platform.py:1246` copies
>   `tools/acpi/gauguin.asl` → `uefi/Silicium-ACPI/Platforms/Xiaomi/gauguin/gauguin.asl`
>   (`$GEN`), and `sync-uefi-platform.sh:116` copies that → the same path under
>   `work/uefi/Mu-Silicium` (`$MU`) and runs `iasl` there. As of this edit the
>   source is **23,575 bytes** while both installed copies are **21,615**, because
>   the difference is entirely a comment block added to the source after the last
>   run of the generator. The compiled `DSDT.aml` is therefore byte-identical and
>   the built payload is unaffected — which is exactly why it is written down
>   rather than fixed: propagating it means re-running `make_uefi_platform.py`
>   over the whole generated tree, which is not worth doing while a payload hash
>   is what P2 is waiting on.
>
>   **That call was wrong, and Step 4.64 is where it was paid for.** The same
>   staleness recurred with a change that *was* load-bearing — the `GIO0` node —
>   and `sync-uefi-platform.sh` installed the previous table while exiting 0 and
>   printing a plausible size, which reads exactly like "the change did nothing".
>   The script now compares its source against `$GEN` before installing and dies
>   with "re-run `tools/make_uefi_platform.py`" if they differ, so the cost of
>   propagating is no longer optional and no longer silent.
> - **2.** `gauguin.fdf:73` is **live**, not commented out.
> - **3.** `gauguin.dsc` has a `[Components]` section (line 91) whose only member
>   is `gauguin/AcpiTables.inf`.
> - **4.** `AcpiTableUpdateLib` is unchanged, and with the tables verified correct
>   on this board (below) it should stay a no-op — but not because the platform
>   works that way. See "The volume is not short of room, and `AcpiTableUpdate` is
>   a no-op in ours alone" above: all 13 sibling packages implement it, and the two
>   nearest SoCs patch 32 named DSDT fields and reinstall the table.
>
> **And the tables are not merely declared — they are built into the payload, and
> each one was read back out of it.** In the firmware volume of the build behind
> the staged `p2-freewhy-g` image (`FVMAIN.Fv`, 7,352,320 bytes, `0x703000`,
> built 2026-09-24 23:30), the six tables sit between `0x54d484` and `0x54df8c`:
>
> | table | offset | length | header |
> |---|---|---|---|
> | `SSDT` | `0x54d484` | 61 | `MSFT`, checksum valid |
> | `DSDT` | `0x54d4c8` | **1,520** | `QCOMM `/`SM7225 `, OEM rev 3, creator `INTL` (iasl), **checksum valid** |
> | `APIC` | `0x54dabc` | 724 | `QCOM`/`QCOMEDK2`, rev 5 |
> | `FACP` | `0x54dd94` | 276 | `QCOM`/`QCOMEDK2`, rev 6 |
> | `FACS` | `0x54deac` | 64 | (no OEM fields — all zero, as `FACS` has none) |
> | `GTDT` | `0x54def0` | 156 | `QCOM`/`QCOMEDK2`, rev 2 |
>
> **`docs/08` step 4.53 has since re-derived this table by walking the
> `AcpiTables` FFS file rather than by scanning for signatures, and every offset
> above came out exactly right for the volume the paragraph names.** The two
> things step 4.53 corrects are about the method and about scope, not about the
> numbers: these offsets are right for the **payload of record** and stale for the
> `Build/` tree, which now holds the `xhci-host` volume (7,524,352 bytes — the
> payload plus exactly the three USB-host blobs' 172,032 — and 126 files, built
> 2026-09-25 01:19), where the same six tables are at `0x57764c`…`0x578158`; and
> "take each hit whose length field is sane" is not what selects them, because
> the raw scan returns six `DSDT` hits and five `FACS` hits, the extras being
> debug strings inside `AcpiTableDxe.efi` — one of which carries a length field of
> `0x0000000A`, which passes any plausible sanity bound.
>
> **A third build has since moved them again, and this time for a reason that is not
> cosmetic.** The Step 4.62 PHY-wake GSIs grow the DSDT by 27 bytes, so the
> `Build/` tree rebuilt on 2026-09-25 05:29 holds the **1,547**-byte table and the
> tables after it shift by 28 (4-byte alignment): the six now sit at
> `0x54d484`…`0x54df68`, with `DSDT` still at `0x54d4c8`, `APIC` `0x54dad8`, `FACP`
> `0x54ddb0`, `FACS` `0x54dec8`, `GTDT` `0x54df0c`. The `SSDT` offset is unchanged
> because the `SSDT` precedes the `DSDT`. Read back out of the payload itself
> (`work/out/p2-phywake/Mu-gauguin-silicon-gzip.img`) the `DSDT` is at the same
> `0x54d4c8` with length 1,547 and a valid checksum, against the control's 1,520 at
> the same offset — and the control and this build differ in exactly that one place,
> which is what makes the pair a comparison rather than two builds.
>
> **A fourth build moved them again on 2026-09-25 05:48, by much more.** Step 4.63
> adds the PMIC family — `SPMI`, `PMIC` and `PM01` — and the DSDT goes **1,547 →
> 2,017 bytes**. `DSDT` is *still* at `0x54d4c8` (the `AcpiTables` FFS file is padded
> and the `SSDT` precedes it), and the tables after it shift by 470 + 2 pad: `APIC`
> `0x54dcb0`, `FACP` `0x54df88`, `FACS` `0x54e0a0`, `GTDT` `0x54e0e4`. Read back out
> of the payload (`work/out/p2-pmic/Mu-gauguin-silicon-gzip.img`), the `AcpiTables`
> FFS file is 3,378 bytes against the previous build's 2,906.
>
> **And this time the table was disassembled and recompiled, not just measured.**
> Lengths are not tables; `iasl -d` on the extracted `DSDT` followed by `iasl -p` on
> the result produces a **byte-identical 2,017-byte** table, so the AML in the payload
> is exactly what `tools/acpi/gauguin.asl` says, through the generator, the sync script
> and the build. The three new nodes survive with every value intact. That is now the
> standard for an ASL change in this port: a length check says a table is there, a
> round trip says it is the right table.
>
> **`FVMAIN` after this step is `7352064 used, 256 (0x100) free` of 7,352,320.** The
> percentage is a rounding artefact — and so is the total, but the *free count is not*,
> which is the half of this that Step 4.64 later spent. It reads as 99% full and was
> briefly recorded as the next node's budget; `[FV.FvMain]` declares `NumBlocks = 0`,
> so GenFv sizes the volume from its content, and a valid **11,536,368-byte `DSDT`**
> (`tools/acpi-pad.py`, which regenerates that exact table) builds with
> `FVMAIN [99%Full] 18886656 (0x1203000) total, 18886408 used, 248 free`
> and `PROGRESS - Success`. The volume that has a hard limit is the outer
> `FVMAIN_COMPACT` (`0x300000`), and it is the one that fails on 2 MiB of noise —
> `the required fv image size 0x311bf0 exceeds the set fv image size 0x300000` — while
> staying at 34% on the 11.5 MB table, because what is inside it compresses. The budget
> for the next ACPI node is therefore `FVMAIN_COMPACT`'s free **2,053,056 bytes**, after
> compression. `docs/08-device-session.md`, Step 4.63, has the measurements; the
> constraint on the node itself is still the driver set's id claims and this board's
> device tree.
>
> **A fifth build, on 2026-09-25, adds the TLMM controller `GIO0` and spends the last of
> that `FVMAIN` free space.** Step 4.64 puts `GIO0` in at `QCOM0A0C` — the family byte
> Step 4.63 established, plus the TLMM index `0C` that the name-join census gives, claimed
> by `qcgpio7280.inf` — and the DSDT goes **2,017 → 2,275 bytes**, so the `AcpiTables`
> FFS file goes 3,378 → 3,634 and the tables after the `DSDT` shift by 256: `APIC`
> `0x54ddb0`, `FACP` `0x54e088`, `FACS` `0x54e1a0`, `GTDT` `0x54e1e4`, read back out of
> `work/out/p2-gio0/Mu-gauguin-stock-gzip.img`. `DSDT` is again at `0x54d4c8`.
>
> **On `FVMAIN`, this build reads `100%Full 7352320 (0x703000) total, 7352320 used, 0
> free` — and that zero is real.** The total is not fixed (`NumBlocks = 0` does size the
> volume: recorded builds have totalled `0x702000`, `0x703000` and `0x72d000`), but it is
> always `align_up(content, 0x1000)`, so the *percentage* is a footline and the free count
> is not: it was `2,808`, then `760`, then `256`, and it is now `0`. The next byte added to
> `FVMAIN` costs one 4 KiB page. That is still not a gate — the volume with a limit is the
> outer `FVMAIN_COMPACT`, it is pinned at `0x300000` by `[FD.SILICIUM_UEFI]`'s `FD_SIZE`
> rather than by auto-sizing, and this build uses 1,092,784 of it with **2,052,944 free**
> — but "reads as full" and "is full" have stopped being the same statement, and this is
> the build where they parted.
>
> **This build's `FVMAIN.Fv` fingerprint is `80f30e19…6a845a65`**, against Step 4.63's
> `85f6f9fc…542b30`, both measured from the payload with `--dump-fvmain`. The `DSDT` was
> again disassembled out of the volume, and `GIO0` reads back with `_HID "QCOM0A0C"`,
> `_UID Zero`, the `0x0F100000+0x00300000` window, nine `Level/ActiveHigh/Shared`
> interrupts `0xF0`–`0xF8` and the `_DSM` values `0x03` and `Package (0x01) { 0x0100 }`.
> The node declares **none** of the corpus's per-pin interrupt catalogue — lisa has 54
> entries and a52sxq 55, but vili (24) and venus (77) are both SM8350, so the list is
> board data, and gauguin's device tree carries no property describing it. That omission
> is deliberate and its cost is stated in Step 4.64. `docs/08-device-session.md` carries
> the measurements and the three members the corpus supplied that were not ported.
>
> **A sixth build, also 2026-09-25, corrects the one `_HID` that had been inherited rather
> than derived, and makes the `GIO0` node answer the driver that binds to it.** Step 4.65
> changes `URS0` from `QCOM0497` — bitra's family-`04` id, which is what a copy of bitra's
> table puts there — to `QCOM0A8B`, the family-`0A` id both of gauguin's family-mates carry
> (`QCOM0497` is named by no file in the shipped 7280 driver set at all, and `QCOM0A8B` is
> what `QcXhciFilter7280.inf` and `QcUsbFnSsFilter7280.inf` bind as `URS\QCOM0A8B&HOST` and
> `URS\QCOM0A8B&FUNCTION`), and deletes the two dead members that made that node's id look
> plausible (`Method (URSI)` and `Name (QUFN)`, whose `QUFN == 0` branch returned the
> *board's own* family byte, so a copy of it can only ever agree with itself). It then adds
> `GIO0.OFNI`, which returns the TLMM's GPIO count, because that name — and not `_DSM` or
> `_AEI` — is the one ACPI method `qcgpio7280/qcgpio.sys` actually evaluates: `qcgpio7280.inf`
> binds `ACPI\QCOM0A0C`, this node's `_HID`; `OFNI` is the only string in that image that can
> be a method name; and the image's imports (`RtlInitializeBitMap`, `RtlSetBits`,
> `RtlFindSetBits`, `RtlNumberOfSetBits`) are a bitmap API, the shape of a driver sizing one
> bit per pin. `URS0`'s `USB0` loses `DPM0` and `HSEN`, bitra-only members that no shipped
> driver references in any file. **The DSDT goes 2,275 → 2,230 → 2,228 bytes** across the
> two halves of the step, the `FVMAIN` content 0x703000 → 0x702fd0, and the tables after
> the `AcpiTables` file shift down by 0x30: `APIC 0x54dd80`, `FACP 0x54e058`, `FACS
> 0x54e170`, `GTDT 0x54e1b4`, with `DSDT` unchanged at `0x54d4c8`, read back out of
> `work/out/p2-4.65/Mu-gauguin-silicon-gzip.img`. The last two bytes are absorbed by FFS
> padding, so the free count does not move for them: **48 bytes free**, against the previous
> build's zero, and the page the earlier note predicted the next node would cost was instead
> given back by the two deleted methods.
>
> **This build's `FVMAIN.Fv` fingerprint is `4b71843d…0f544802`**, against Step 4.64's
> `80f30e19…6a845a65`. The `DSDT` was again disassembled out of the volume rather than read
> in the source: `GIO0` carries `Method (OFNI)` returning `Buffer (0x02) { 0x9C, 0x00 }` =
> **156**, `URS0` reads `_HID "QCOM0A8B"`, and neither `Method (DPM0)` nor `Method (HSEN)`
> exists anywhere in the table. **`OFNI` was built once as 157 and then corrected**, which is
> why the fingerprint above has a superseded sibling: `0x9D` came from gauguin's own firmware
> device tree (`gpio-ranges = <&tlmm 0 0 0x9d>`, against 156 in the mainline
> `pinctrl-sm6350.c` descriptor list), and that is a reading of the wrong quantity. `OFNI` is
> the number of *gpios*, which the corpus answers where a corpus table, the mainline driver
> and the SoC dtsi can all be read: sm8150 175, sm8250 180, sm8350 203, sc7280 175 — the
> driver's `gpio_groups[]` size, not the dtsi's `gpio-ranges` (176/181/204/175) and not the
> driver's declared `.ngpios` (176/181/204/**176**). But the corpus is not unanimous, and this
> is the part the first write-up of this step got wrong: two SM8350 tables answer the chip
> width instead of the gpio count (venus and vili **204**, against lemonade and the Qualcomm
> reference MTP at **203** on the same silicon), so the value is a per-board choice and the
> corroboration has to come from the tables closest to gauguin rather than from a headcount —
> the two that carry `GIO0._HID = "QCOM0A0C"`, lisa and a52sxq, are both on the gpio-count
> side. An earlier draft of this passage called the 204 group "three Cape tables and Cedros",
> which is wrong twice: renoir and Cedros are SDM7350 boards, not Cape, and the 204 cluster is
> the counterexample rather than evidence of consistency. sm6350 is the chip where the
> distinction bites — its `gpio_groups[]` is 156 while its `.ngpios` and its dtsi range are
> both 157 — and its descriptor list runs to `PINCTRL_PIN(163)` of which `0..155` are gpios,
> `156` is `ufs_reset` (no gpio function, absent from `gpio_groups[]`, but still gpio line 156
> in Linux, which is why gauguin's board dts carries `reset-gpios = <&tlmm 156 …>` under
> `&ufs_mem_hc`), and `157..163` are the SDC lines. So both of the 157s count one pin that is
> not a gpio, and gauguin's vendor dtb carries the same quirk. So the answer is 156 — while no
> ACPI resource on the board names a pin above 155, which none does today; giving the UFS node
> its pad-156 reset line through ACPI would require `0x9D`. `docs/08-device-session.md`
> carries the derivation, the two-axis test that replaces the corpus-only test, and the
> `UCS0` node this step found missing.
>
> **And a payload hash from here on is a fingerprint of a build, not of the source.**
> `Silicon/Silicium/SiliciumPkg/Sec/Sec.c:48` compiles `__TIME__` and `__DATE__` into
> `Sec.efi`, which lives in the outer volume and not in `FVMAIN`; two consecutive `-c`
> clean builds of this tree give two `SILICIUM_UEFI.fd` hashes, two `Sec.efi` hashes,
> and **one** `FVMAIN.Fv` hash — `85f6f9fc…542b30`. So the reproducible fingerprint of
> what a build contains is `FVMAIN.Fv`, extractable from any payload with
> `tools/fv-inventory.py <img> --dump-fvmain PATH`.
>
> `FACP` and `FACS` do not checksum in *any* of these builds, and that is the
> un-patched state rather than a fault: `AcpiTableDxe` installs them at runtime and
> writes the `DSDT`/`FACS` addresses into `FACP` on the way (all four pointer fields
> — `FIRMWARE_CTRL`, `DSDT`, `X_FIRMWARE_CTRL`, `X_DSDT` — read `0x0` in the volume),
> which is what recomputes its checksum. `FACS` has no checksum field at all.
>
> The DSDT is gauguin's own and contains what it was supposed to: `ACPI0007`
> eight times (the eight CPU devices), `QCOM24A5` once (the UFS `_HID`), and
> `UFS0` and `URS0` device nodes, with `_HID`/`_ADR`/`_CRS`/`_DSM`/`_STA`/`_UID`
> all present.
>
> **The APIC is where a mistake would be fatal rather than cosmetic, so it was
> parsed subtable by subtable.** 44-byte MADT header, then eight `0x0B` subtables
> of `0x52` = 82 bytes each, then one `0x0C` (GICD) of 24 — 44 + 8×82 + 24 = 724,
> which is the whole table. GICC #1 and #8, read at the offsets ACPI 6.4 gives
> (SPE overflow interrupt at 78 is what makes the subtable 82 and not 80):
>
> | field | offset | GICC #1 (cpu 0) | GICC #8 (cpu 7) | gauguin's device tree |
> |---|---|---|---|---|
> | Performance Interrupt GSIV | 20 | `0x15` = **21** | `0x15` = **21** | `pmu interrupts = <1 5 8>` → PPI 5 → 21 ✔ |
> | VGIC Maintenance Interrupt | 56 | `0x18` = **24** | `0x18` = **24** | GIC node `interrupts = <1 8 4>` → PPI 8 → 24 ✔ |
> | GICR Base Address | 60 | **`0x17A60000`** | **`0x17B40000`** | `reg = <… 0x17a60000 0x100000>` ✔ |
>
> Eight cores at stride `0x20000` runs `0x17A60000`→`0x17B4FFFF`, inside the
> `0x100000` window the device tree declares. **So all three values the choice of
> Moorea rested on are in the built table, and they are the right ones** — which
> is what `docs/08` step 4.44's ordering rule asks for anyway: the reading is of
> the artifact, not of the source that was supposed to produce it.

> **A seventh build, also 2026-09-25, adds the Type-C controller `UCS0` — and this one
> costs the page the sixth said a node costs, to no consequence.** Step 4.66 puts `UCS0`
> in at `QCOM0AA4`, with a `_CRS` of one `GpioIo` on `GIO0` pin 35 and five one-line
> accessors into the `\_SB`-scope state names (`MUXC`, `CCST`, `DPPN`, `HPDS`, `HIRQ`) that
> were already in the table. The DSDT goes **2,228 → 2,369 bytes** and the `AcpiTables`
> FFS file 3,586 → 3,730, so `FVMAIN`'s content goes `0x702fd0` → `0x703060` and its total
> `0x703000` → `0x704000`: exactly the one 4 KiB page the fifth build predicted, and the
> reason the sixth's "48 bytes free" was never a budget. `FVMAIN_COMPACT` moved the other
> way by 32 bytes (1,092,712 → 1,092,680 of `0x300000`, so `2,053,048` free) — it is
> compressed, and a slightly different LZMA stream is not a larger one. The tables after
> `DSDT` move **up** by 0x90 this time — the sixth shrank the file and moved them down, this
> one grows it by exactly the DSDT's padded delta — so `APIC 0x54de10`, `FACP 0x54e0e8`,
> `FACS 0x54e200`, `GTDT 0x54e244`, with `DSDT` unchanged at **`0x54d4c8`, 2,369 bytes,
> checksum valid**, and `SSDT` at `0x54d484`. Read back out of
> `work/out/p2-4.66/Mu-gauguin-silicon-gzip.img`
> with `--acpi`, and the `DSDT` sliced out of the volume is byte-identical to the
> `Silicium-ACPI/Platforms/Xiaomi/gauguin/DSDT.aml` `iasl` produced
> (`c4e46e438eef1062fd410923ab0d8caf84aea9c21a7087985ecb65154ba2f0d2`), which is the check
> that the table in the payload is the table in the source.
>
> **The fingerprint is `FVMAIN.Fv` `04e1cabd…31f94727`**, against the sixth's
> `4b71843d…0f544802` — both the sha256 of the decompressed inner volume, as this page has
> quoted it since the fourth build. **Step 4.68 found the limit of that convention and it
> is worth reading before trusting any `FVMAIN.Fv` hash here on its own**: the inner volume
> contains `SmBiosTableDxe`, whose BIOS Information structure carries a `__DATE__`-shaped
> release date, so the hash moves at midnight even when nothing else does. Two Step 4.68
> builds whose ACPI input was byte-identical differ in exactly nine bytes — `09/24/2026`
> against `09/25/2026` — and a rebuild with no change at all reproduces the second
> exactly. Within a day the hash is reproducible and is a real check; across days the
> number that carries is the `DSDT.aml`'s size and sha256, which is why Step 4.68 quotes
> both. `tools/probe-fingerprint.py` reports the same
> ten-instrument ladder
> in this payload as in the sixth (`P2FreeWhy`, `P2Digest`, `P2Apri`, `P2Seq`, `P2Why`,
> `P2ErrRow`, `P2Bins`, `P2Retry`, `P2Key`, `P2Tick`), and `build-p2-payloads.sh` matched
> all three variants against GenFv's map at **123 offsets and GUIDs** with zero mismatches,
> so the node cost space in the table and nothing in the batch: `tools/apriori-order.py`
> reads the same 70-GUID Apriori file in the same order out of a 123-file volume, and
> `uefi/Platforms/Xiaomi/gauguinPkg` is unmodified in git after a full regenerate, which is
> the staleness guard agreeing.

**A trap in reading these files, because it produced a confident wrong answer
first.** The GICC subtable is labelled `Subtable Type : 0B [Generic Interrupt
Controller]`; a parser that looks for "GIC CPU Interface" — the name the ACPI spec
and most prose use — matches no subtable in any of the 18 files and reports *every*
SoC as having no GICC. The first pass of this comparison did exactly that, and the
table above is the corrected one.

| | |
|---|---|
| sets examined | 18 (`Silicium-ACPI/Silicon/Qualcomm/*/Decompiled/APIC.dsl`) |
| sets matching gauguin | **3** — Moorea, Napali, Rennell |
| set chosen | **Moorea**, and `APIC`/`FACP`/`GTDT` only — every value checked against gauguin's device tree |
| set `Platforms/Realme/bitra` uses | Kona — matches nothing |
| DSDT | authored for gauguin, but its UFS and USB values are verified equal to bitra's |
| next step | **done as of 2026-09-25** — `gauguin/AcpiTables.inf` and the DSDT exist, are wired into `gauguin.fdf`/`gauguin.dsc`, and are in the built payload with every GICC field read back correct (see the superseded note above). "Next step" here meant *the table set*, and that part is finished; what the DSDT still owes is a different list — `PEP0`, the two live SPI engines (which no driver on this host can bind), buttons and the 29 thermal zones — and outside ACPI, P3's items 3 and 4 (USB host, input) have not been started. Either way the next step on the glass is the same one: P2 handing off to BDS |
| where ACPI got to | the DSDT now carries UFS, USB (with the PHY wake lines), the eight CPUs, **the PMIC family** — `SPMI`, `PMIC` and `PM01`, Step 4.63 — **the TLMM pin controller `GIO0` at `QCOM0A0C`**, Step 4.64 — Step 4.65's **`GIO0.OFNI` (156 gpios) and the corrected `URS0` id `QCOM0A8B`** — and, Step 4.66, **the Type-C controller `UCS0` at `QCOM0AA4`**, which is the first node here added on the strength of a driver `.inf` naming the id rather than on a sibling table's, and the first to place a `GpioIo` on `GIO0` — pin 35, which is why `OFNI` stays 156 — and the best-attested node in the table besides: its 1,272 bytes are byte-identical in lisa, in a52sxq and in the SC7280 CRD's DSDT, the third of which is `MSFT`-compiled and was read out of a shipped Qualcomm UFS firmware capsule rather than out of `Silicium-ACPI`. The AML was **2,369 bytes** in the `work/out/p2-4.66/` payload, and Step 4.67 reproduced that volume byte-for-byte (`FVMAIN.Fv` sha256 `04e1cabd…31f94727`, `FVMAIN_COMPACT` `1,092,680 used, 2,053,048 free`) from a source that had only comments changed, which is the check that the change was comment-only. Step 4.68 then measured what Step 4.66 had written down and left half-done: `UCS0`'s five accessors were bound on three devices here and on one in the family — lisa and a52sxq each bind `CCVL` exactly once, inside `UCS0` — so the two copies on `URS0.USB0` and `URS0.UFN0` were removed, while `PHYC` stayed in both on the same evidence. The AML is now **2,345 bytes**, `692f728c…d456d387`, and that same step found the node at the root of everything the Type-C path still owes: `QCOM0A10`, the QUP I2C engine, claimed by `qci2c7280.inf` and carried by exactly two of the 66 corpus tables, lisa and a52sxq — the same pair that settled `URS0`'s and `UCS0`'s ids. `PEP0`'s `Field (\_SB.ABD.ROP1)`, the `_DEP` `UCS0` still cannot write, and the I2C addresses `PML0` needs all wait on it. Step 4.69 wrote that node, and in doing so found the question was two questions. The arithmetic one closes: the family's engine `_UID` follows its `_STR` by **`_UID = 8*wrapper + SE + 1`**, which fits all 23 engine nodes in the three tables carrying any — lisa, a52sxq and the SC7280 CRD — with no exception, and the device name is the same ladder in decimal (`"I2C"`+n to 9, `"IC"`+n from 10), which is every engine name in the corpus and nothing else. So `IC11` is the family's name for slot 11, not a bus with known contents, and gauguin's slot 11 is `i2c@988000`, the touch and NFC bus. The wiring question is gauguin's: the Type-C analog switch `fsa4480@42` and the whole charger cluster (`pm8008` ×2, `smb1396@34`, `bq25970@66`, `aw8624@5A`) are on wrapper 1 engine 4, `i2c@990000` — measured four ways that agree (stride, TLMM function `qup14`, `qcom,wrapper-core` phandle `0x193`, and the GSI ladder `0x181`–`0x185` which is the corpus's `0x181`–`0x186` slot for slot, wrapper 0's SE 0 matching the CRD's `I2C1` at `0x279` as well). **`IC13` at `QCOM0A10`, `_UID 0x0D`, `0x990000 + 0x4000`, INTID `0x185`**, is the node. `GIO0` declares its own nine level-`0xF0`–`0xF8` interrupts and *not* the corpus's per-pin catalogue, which is board data gauguin's device tree does not carry. `FVMAIN` is at 99% with 4,000 free because its total is `align_up(content, 0x1000)` and `NumBlocks = 0` lets it grow: these 141 bytes of AML moved it `0x703000` → `0x704000`, and the three USB host drivers took it to `0x72d000` earlier with nothing to say about it. **The volume with a real limit is the outer `FVMAIN_COMPACT`** — `2,052,976` bytes free after compression of a `0x300000` cap, and it did not shrink when this grew. Payload hashes are per-build (`__TIME__` in `Sec.efi`), and Step 4.68 showed `FVMAIN.Fv` is per-*day* as well (`__DATE__` in `SmBiosTableDxe`), so the fingerprint that crosses days is the `DSDT.aml`'s: **2,833 bytes, `a98c1a98…f5af5b`**, against Step 4.69's 2,465 and `b9e70ee6…`, Step 4.68's 2,345 and `692f728c…`, and Step 4.66's 2,369 and `c4e46e43…`. Step 4.70 wrote the rest of the engines the board leaves running, and moved a count this file had carried since 4.68: they are **six**, not four. Reading the `status` of every node at a QUP address in the tree dumped out of the running kernel gives `0x880000` `spi@880000` (SPI, `touch_spi@0`), `0x884000` `qcom,qup_uart@884000` (4-wire UART, the console), `0x984000` `i2c@984000` (`cs35l41` ×2), `0x988000` `i2c@988000` (`focaltech@38`, `nq@28`), `0x98c000` `spi@98c000` (`irled@0`) and `0x990000` `i2c@990000` (the charger cluster) — slots 1, 2, 10, 11, 12, 13 — with everything else at a QUP address `disabled`. The two that were missed were missed because **the payload's own tree disagrees about them**: `work/out/gauguin.dts` has `i2c@880000` where the board has `spi@880000` and `serial@98c000` (`qcom,geni-debug-uart`) where the board has `spi@98c000` with an IR blaster on it. The board wins, which closes the last open bullet of Step 4.69 but the first of its two. Three nodes were written — **`IC10` (`QCOM0A10`, `_UID 0x0A`, `0x984000`, INTID `0x182`), `IC11` (`QCOM0A10`, `_UID 0x0B`, `0x988000`, INTID `0x183`) and `UARD` (`QCOM0A16`, `_UID 0x02`, `0x884000`, INTID `0x27A`)** — and the two SPI engines were withheld, which is the one place this step answered a question by not writing a node: **no `.inf` on this host binds `QCOM0A0E`**, so `SP1` and `SP12` would register two unknown devices reserving `0x880000`, `0x98c000` and two GSIs against no driver. The step also moved the GSIs off the corpus and onto the board: a device tree interrupt specifier `<0 N 4>` names GIC INTID `N + 32`, which is ACPI's GSI, and all six `interrupts` values convert to exactly the corpus's ladder — including `0x259 + 32 = 0x279`, the CRD's `I2C1` — so the ladder is now measured twice, and the protocol of each engine is likewise on the board, in the TLMM group its `pinctrl-0` resolves to (`qupv3_se0_spi`, `se1_4uart`, `se2_i2c`/`se2_spi`, `se6_i2c`/`se6_spi`, `se7_2uart`/`se7_i2c`, `se8_i2c`, `se9_2uart`/`se9_spi`, `se10_i2c`) — where the `se` index is a fourth, different convention: `se0`–`se5` are wrapper 0 and wrapper 1 starts at `se6`, so `se7` is wrapper 1's engine 1 and not engine 7. `UARD` is named for its role and not its slot because the corpus shows the debug UART exempt from the ladder twice over: lisa's `UARD` is `_UID 6` and venus's is `_UID 4` while both carry the `,DBG` tag and neither name encodes the slot, where venus's `_UID` is still `8 * 0 + 3 + 1` for its `_STR "QUP_0_SE_3,DBG"`. And the touch moved to the bus the node names do not suggest: `spi@880000`'s child carries a `compatible`, a `reg` and a clock and nothing else, while `focaltech@38` on `i2c@988000` carries the interrupt, the reset, the supply and the panel phandles, and `qcom,i2c-touch-active = "focaltech,fts_ts"` names the I2C path active — so `IC11` is the touch's bus, and no `.inf` in the set drives a touch controller on either bus, which makes the node the prerequisite and not the feature. The AML is **2,833 bytes**, `a98c1a98095f77e2a1dde01cefe99b9a91ae6af1926f7fed5053353581f5af5b`, the delta is exactly the three nodes (disassembly diff: 72 added lines, 3 × 24, 0 changed, 0 removed; 19 devices to 22), `SSDT`/`APIC`/`FACP`/`FACS`/`GTDT` are byte-identical, and `tools/acpi-hid-census.py --asl` now finds 9 of the table's 22 declarations claimed by a driver in the set, up from 6, with the same two — `QCOM0A8B` and `QCOM24A5` — unclaimed. Step 4.71 then wrote the two controllers the engines above are not self-driving without: **`QGP0` and `QGP1`, `QCOM0A88`, `_UID Zero`/`One`, windows `0x00804000 + 0x50000` and `0x00904000 + 0x50000`, GSIs `0x114`/`0x115` and `0x2A5`/`0x2A6`**. Two independent sources name the pairing and they agree: the board's `dmas`, where each of the five live engines names its own wrapper's controller — `0x186` is `qcom,gpi-dma@800000`, `0x190` is `qcom,gpi-dma@900000`, none crosses over — and the corpus's `_DEP`, where three engines per table name a `QGP`. Each `dmas` specifier is six entries, `<phandle, tx/rx, SE index, code, 0x40, 0>`, and the SE index in the second cell is a further measurement of the numbering the `IC` nodes were built on, from a property that played no part in deriving it, with the third cell constant per protocol — 1 on the two SPI engines, 3 on the three I2C engines — as one more statement of which protocol sits at each slot, recorded and not decoded. The corpus also supplied the cleanest statement of the rule this table has run on since 4.70: lisa's `SP14` and a52sxq's `IC14` are the same engine — same address, same `_UID 0x0E`, same `_STR "QUP_1_SE_5"`, same `INTID 0x186`, same `_DEP` — and differ in exactly two lines, the `_HID` (`QCOM0A0E` against `QCOM0A10`) and the name, so **the slot identifies the engine and the board identifies the protocol**, which is also why the letter in an engine's name is the protocol rather than the slot, and why gauguin's two live SPI engines would be `SP1` and `SP12`. The id is broad rather than a pair for once: 20 of the 66 tables declare a `QGP` under nine distinct ids, the same block is indexed `88` in five families (09, 0A, 0C, 1A, 25), `93` in three and `F4` in one, so the index is a property of the generation and 0A sits in the `88` group — and `qcgpi7280.inf` claims it outright (`ACPI\QCOM0A88`). The resources are a derivation and not a copy: the corpus's `_CRS` is the board's region less its first `0x4000`, `0x50000` long, on lisa and on a52sxq alike, and both of gauguin's regions are `0x60000` under the reg-name `"gpi-top"` that says the stepped-over block is there. **The interrupts are the one place in this block where copying the corpus would have been wrong**: each board declares ten lines and `qcom,max-num-gpii = 10`, the family declares two, and at wrapper 0 those two are gauguin's first two to the digit — `0x114`, `0x115` — while at wrapper 1 the family's `0x137`, `0x138` sit 374 from gauguin's `0x2A5`, `0x2A6`; the family's count is kept and only its numbers are replaced, and neither number is explained. Neither node carries a `_DEP`, which is the family's own arrangement and also leaves no choice: all three of the family's shapes for an engine `_DEP` begin with `PEP0`, which this table does not yet have, and a one-entry `_DEP` is a shape no family table carries. The AML is now **3,027 bytes**, `fd760ef74f093d5d65d7959702f668af26f2f09daf9cb32af1f92cd6385939f3`, checksum `0xCC`; the delta is exactly the two nodes (disassembly diff against a 4.70 baseline recompiled from git: 3 changed header lines, 57 added, 0 removed, 22 devices to 24), `SSDT`/`APIC`/`FACP`/`FACS`/`GTDT` are byte-identical, the payload's `DSDT.aml` hashes the same as the build tree's, and the census now finds **11 of the table's 24** declarations claimed by a driver in the set, with the same two — `QCOM0A8B` (UFS) and `QCOM24A5` — still unclaimed. Step 4.72 then reversed the negative result Step 4.71 left behind. That step had recorded `MMU0` — the SMMU the GPI DMA controllers sit behind, and the node every family engine `_DEP` that reaches a `QGP` reaches beside it — as unbuildable, because the board has exactly two IOMMU blocks and neither had the corpus `MMU0`'s shape. Both are derivable, and both are now written: **`MMU0` (`QCOM0A09`, `_UID Zero`, `0x15000000 + 0x100000`, 81 interrupts in five runs — `0x61`; `0x7F`–`0x96`; `0xD5`–`0xE0`; `0x15B`–`0x179`; `0x1B1`–`0x1BD`) and `MMU1` (`QCOM0A09`, `_UID One`, `0x03D40000 + 0x00020000`, 10 interrupts — `0x105`, `0x107`, `0x18C`–`0x193`)**. The pair is the first in this table whose *form* — which resources go on which of two nodes sharing one id — came from a driver's record of two instances rather than from a sibling table's single one: `qcsmmu7280.inf` claims `ACPI\QCOM0A09` once and hangs two per-instance registry sets off it, `Parameters\0` with its context-bank page at `0x80` pages and `MDP`/`VFE`/`VIDEO` as its `PREFETCHDETAILS` clients, and `Parameters\1` with its CB page at `0x10` pages and **`GPU`** as its only client — the file's own comment calls the second "GFX MMU version specific settings". So instance 1 is the GPU's SMMU, and the board's two IOMMU blocks identify themselves three ways over: `apps-smmu@15000000` and `arm,smmu-kgsl@3d40000` in the vendor tree (`qcom,qsmmu-v500` / `qcom,smmu-v2` with the name `arm,smmu-kgsl`), the same two in the payload's kernel tree (`qcom,sm6350-smmu-500` / `qcom,sm6350-smmu-v2` with **`qcom,adreno-smmu`**), and the driver's client lists. The corpus agrees about the id and supplies neither: 20 of 66 tables carry the pair, all with the same `_UID Zero`/`One` under the same one id, and no table anywhere declares a second id for a second SMMU. `MMU0`'s window is the board's rather than the family's for once — `0x15000000` with `0x100000` is the one SMMU window in the family that does not move with the SoC (17 of the 20; the others are `0x7FFB8` and `0x186000` twice), and the driver's instance-0 layout has everything it names below `0x81000`, so here the corpus is a check and not a source. Its 81 interrupts are `#global-interrupts = 1` plus 80 context banks, the count is the board's and the ladder's *shape* is the family's without conflict — the runs opening at `0xD5` and `0x15B` are in all 20 tables, lisa's `0xD5`–`0xE0` and `0x15B`–`0x178` sit inside gauguin's to the digit, and lisa's own count is 65 against this board's 81, across a corpus that declares 43, 57, 58, 63, 65 or 71. `MMU1`'s base is the board's and its **length is the driver's**: the board's `0x10000` is a register footprint (`attach-impl-defs` reaches `0x6b68`, the page instance 1 calls implementation defined 1 at `0x06` pages) and the instance-1 context-bank page at `0x10` pages lands exactly where a `0x10000` window ends, so the node takes the `0x20000` the family gives the same node — also the largest power of two that stops short of the GPU's next region at `0x3d61000`. Eight of the 20 corpus `MMU1`s write `0x10000`, ten write `0x20000`, Kailua's two `0x40000`, and the split does not decide it; the driver's own offsets do, which is the first time a driver's page arithmetic rather than a sibling `_CRS` has settled a window length here. `MMU1`'s eight context-bank GSIs `0x18C`–`0x193` are another family's group — `QCOM0212`/`QCOM0809`/`QCOM1409`, and a52q, miatoll, surya — while gauguin's peers lisa and a52sxq put their ten at `0x2C6`–`0x2CF`, the same split the GPI DMA showed at wrapper 1, with the board's own ladder backing the eight: they lie in the gap between `MMU0`'s third run, which ends at `0x179`, and its fourth, which opens at `0x1B1`. **The trigger is the one place in this file where the corpus disagrees with the board**: all 1400 SMMU descriptors in the corpus are `Edge, ActiveHigh, Exclusive` and all 91 of gauguin's specifiers carry type cell 4, level, which is what the kernel programs those GIC lines with; the corpus is faithful elsewhere (its `UFS0` and `QGP0` are Level, matching the board's cells for the same blocks), so the disagreement is specific and is recorded rather than resolved. Three things every corpus SMMU carries are deliberately absent: the `_DEP {PEP0}` (40 of 40 nodes, and a one-entry `_DEP` is a shape no table has), `_STA` (absent from 19 of 20 `MMU0`s and 9 of 20 `MMU1`s; the ones that return `Zero` hide an SMMU rather than describe one, and this port means to drive it), and `Alias (\_SB.SVMJ, _HRV)` — `SVMJ` is a single `Name (SVMJ, 0xFFFF)` under `\_SB`, and this table has none, so adding it is its own measurement. The AML is now **3,997 bytes**, `47d7dc0acda977e79b48aa9c1d4efc64020704045302a6c048f3ea26bbf1545e`, checksum `0xBC`; the delta is exactly the two nodes (disassembly diff against the 4.71 payload's own DSDT extracted at the same FVMAIN offset: 2 changed header lines, 400 added — MMU0's 341, MMU1's 57, two blank separators — 0 removed; 24 devices to 26; 196/181 opcodes and named objects to 198/193), `SSDT`/`APIC`/`FACP`/`FACS`/`GTDT` are byte-identical a fourth time, the payload's `DSDT.aml` at `0x0054d4c8` hashes the same as the build tree's, and the census now finds **13 of the table's 26** declarations claimed by a driver in the set, with the same two — `QCOM0A8B` (UFS) and `QCOM24A5` — still unclaimed. `FVMAIN` is at 99% with 2,376 free of `0x704000`, and it is worth noting the outer volume went *down*: `FVMAIN_COMPACT` used 1,088,935 of its `0x300000` cap, 3,897 fewer bytes than 4.71 despite the larger DSDT. **Step 4.73** then wrote the first thirteen thermal zones and closed the join Steps 4.66 and 4.67 had left open, because the join turned out not to be between names: the corpus's `TZ<n>` labels and the board's `cpu-0-0-usr` names have no correspondence, and the identity that does exist is the **driver's id list**. `qcpep.wd7280.inf` — the only driver on this host that binds a thermal zone — accepts `0A17`, `0A37`–`0A51`, `0A57`–`0A64`, `0A91`, `0A92`, `0ABF`, `0AC8`–`0ACB`, `0AD4`, `0AD8`–`0AE0`, which is the family `Xiaomi/lisa` and `Samsung/a52sxq` write and no other table among the 66 does; the corpus's *same-SoC* table `Samsung/a52q` writes family `08` and **no `.inf` in any of the five driver trees on this host claims a single one of its ids**, so the table that had been the natural source in earlier steps is the wrong source here, for the same reason it was right then — an id family is a property of the driver set and not of the silicon (the GPI DMA in 4.71 and the SMMUs in 4.72 said the same thing). The written set is **`TZ0`–`TZ7`, `TZ9`–`TZ13`**: `QCOM0A58`/`0A59`/`0AD4` at `_UID Zero` and `_UID One` each (the one-id-two-instances pattern a third time), plus `QCOM0A91` (GPU), `0A92` (NPU), `0A51`, `0A4C`, `0ABF`, `0A4B` and `0A57`, every one of their ids claimed. `_PSV` is the one place this step deliberately departs from its source, and the departure is measurable: both sides encode temperature as `(C + 273) * 10`, so `0x0E60` = 95 C agrees with the board's `gpu-trip0` and `npu-trip0` on `TZ6` and `TZ10` and with lisa's own values, `0x0F28` = 115 C is simultaneously lisa's PMIC `_CRT` and gauguin's `reset-mon-cfg` (which is why `_CRT` is written on all thirteen where lisa writes it on the PMIC group only), and the three `_UID One` zones take `0x0EF6` = 110 C from the board's `cpuNN-config` rather than lisa's `0x0EC4` = 105 C, because the board's tree carries no 105 C trip at all and a passive trip is the design's decision. `QCOM0ABF` gets no `_PSV` for the same reason — lisa's `0x0EC4` would be a guess. `_TZD` is omitted (lisa's lists `\_SB.GPU0` and `\_SB.PEP0`, neither of which exists here) and so is `_DEP` (`{PEP0}` alone on all but the PMIC group; a one-entry `_DEP` is a shape no table in the family has). The step also caught a defect that compiled cleanly and reported nothing: the zones were first written as `Device (TZ0)`, which produced `0 Errors` and byte-for-byte the same AML size, opcode count and named-object count as the correct form, because ASL's `ThermalZone` is AML's `ThermalZoneOp` (`0x5B 0x85`) and not a synonym for `DeviceOp` (`0x5B 0x82`) — two opcodes of identical length, distinguishable in the artifact only by disassembling it. The AML is **5,210 bytes**, `ff492bef347825a846b03469a15fce2679ecdfe85003e1bf35f2845e79c1d639`, 237 opcodes and 326 named objects, 26 devices to 39, `SSDT`/`APIC`/`FACP`/`FACS`/`GTDT` byte-identical to 4.72's a fifth time, the claim count **23 of 25 distinct ids** with the same two — `QCOM0A8B` (UFS) and `QCOM24A5` — unclaimed, and the DSDT read back out of the payload at `0x54d4c8`, the same FVMAIN offset as 4.72's because the table grew inside its own FFS file. Four groups are withheld, each because it needs a device this table has not got: the PMIC group `0AC8`/`0AC9`/`0ACB` (two-entry `_DEP`, a `_DSM` on UUID `c2d42c4b-e25e-471c-8a4e-290aac3a29a3`, and a `GpioInt` `_CRS` on `\_SB.PM01` pin `0x00C0`), the ADC group `0A5F`/`0A61`/`0A63` (`_DEP` on `ADC1`), `TZ99` `0A5A` (a 13-entry `_TZD` naming five absent containers) and the modem's nine `QCOM04C0`–`QCOM04C8` under `qcthermalmdm7280.inf`. **The space looked like the binding constraint and is not.** This step finished by reading `FVMAIN`'s 1,160 bytes free of `0x704000` as a cap and concluding that the next zone group would not fit. It is not a cap: `[FV.FvMain]` declares `NumBlocks = 0` with `BlockSize = 0x1000`, so GenFv sizes the volume to content and rounds up to the next block, and the "free space" is the slack to that boundary. A throwaway probe of about 4 KB of extra AML - landing in the same `AcpiTables` file, so the same `+1,216` as the real change - built a volume of `0x705000`, one block larger with the same slack in front of it; removing the probe restored `FVMAIN.Fv` sha256 `e202996eab15b3fe215d7b04ee1691da17e057f2c0dc828075ab6ea8c7f8d33a`, the hash this step's payloads came from. The real cap is `FVMAIN_COMPACT`, a fixed `0x300000` region holding the compressed `FVMAIN`, at 1,089,206 used and **2,056,522 free**; this step measured the shrink ratio at 271 bytes of compact per 1,216 of `FVMAIN` (0.22), which puts the remaining headroom on the order of 9 MB of `FVMAIN` content - not a constraint this phase is near. The four withheld zone groups stay withheld for the reason given above and not for space |
