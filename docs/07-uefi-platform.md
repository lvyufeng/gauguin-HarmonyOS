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
  numbers here trustworthy rather than plausible.
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
  `VibratorDxe`/`QcomChargerApp` are not needed for a shell, and
  `VerifiedBootDxe`/`SecRSADxe` are authentication paths this build does not
  use. None is a prerequisite of the console.

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

**Not verified — and this is the whole of the P2 gate:**

- **The firmware has never run.** It has never been loaded by a bootloader and
  it has never executed an instruction. Everything above is static inspection
  of a build product. Enumerating the volume proves the software is *in* the
  image; it says nothing about whether the image is reached.

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
  is a deliberate no-op with a P3 TODO. The P2 gate is the boot manager drawing
  on the panel and UFS enumerating, which does not need ACPI; Windows does, which
  is why that is P3.
- The MDP SIDs, as noted above.

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

(Those are the corrected sizes; the pair that was first built and written to
`boot` was 16,384 bytes smaller on both, because its device tree was stale — see
"The P2 payload could not have executed" below, which is also where the v1 header
that made it unreachable is dealt with.)

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


### The P2 payload could not have executed, for two independent reasons

The image written to `boot` was built by hand, and running the offline checker
over it — which had been written by then and was being run only on the P1 images
— says it would have been refused before any of our firmware ran. `tools/abl-boot-check.py`
on `work/out/p2-variants/Mu-gauguin-stock-{none,gzip}.img`:

```
ApplyOverlay: ufdt apply overlay failed: this tree has no /__symbols__ at all,
and the board overlay (dtbo entry 13, Qualcomm Technologies, Inc. Gauguin)
arrives with 158 __fixups__ it has to resolve against them
```

`tools/check-payload.py` had been reporting both images `ok` throughout. It is
not wrong: it checks the wrapper's structure, and the structure was fine. What it
cannot see is ABL's *decision path*, which is what the other checker replays.

**Reason A: `header_version` was 1, and at 1 ABL reads no DTB at all.**
`Resources/Configs/gauguin.toml` — generated by `tools/make_uefi_platform.py`, and
what Mu-Silicium's own builder used — set `header_version = 1` together with
`append_dtb = true`. `DTBImgCheckAndAppendDT` enters its DTB-locating branch only
`if (HeaderVersion > BOOT_HEADER_VERSION_ONE)` (`BootLinux.c:454`;
`BOOT_HEADER_VERSION_ONE` is 1), so at v1 `DtbOffset` is never computed and stays
at the `0` the `BootParamlist` was initialised with. This device's dtbo partition
validates, so ABL takes the overlay branch, calls
`GetSocDtb (ImageBuffer, ImageSize, 0, ...)`, and returns `NULL` on the function's
first check — `if (!DtbOffset)` (`LocateDeviceTree.c:1005`) → `"DTB offset is
NULL"` → `"Error: Appended Soc Device Tree blob not found"` → `EFI_NOT_FOUND`.
ABL's own comment says the same thing in words: *"DT size doesn't apply to header
versions 0 and 1"* (`BootLinux.c:1239`).

So `append_dtb = true` and `header_version = 1` are not merely a mismatched pair;
on a device whose dtbo partition is valid, v1 cannot locate a DTB by any route,
because the appended-DTB branch is on the *other* side of the same `if`. v1 is
unconditionally fatal here, and fatal with a message about the DTB.

**Reason B: the tree inside it was stale by two changes.** It was 71,737 bytes
with **0** `/__symbols__` entries and the inherited `ramoops@ffc00000` — the
address that was already known to be wrong — where the current tree is 87,594
bytes with 217 symbols and `ramoops@d0000000`. The `/__symbols__` half is the
same defect the P1 payloads were fixed for (`docs/07`, above): this device's dtbo
validates, so the vendor overlay is applied to our tree on every boot, and
`ufdt_overlay_do_fixups()` returns `-1` on the first symbol it cannot resolve.
The P1 build had that fix; the P2 build had been done by hand against whatever
`.dtb` was lying in the kernel tree at the time, and nothing rebuilt it when the
tree changed.

**What changed, so that it cannot recur.** The tree now has one builder,
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
work/out/p2-variants/Mu-gauguin-stock-none.img   3,248,128  kernel=raw
work/out/p2-variants/Mu-gauguin-stock-gzip.img   1,146,880  kernel=gzip
```

Both pass `check-payload.py` and `abl-boot-check.py`, including the real
`fdtoverlay` merge of dtbo entry 13 onto the tree they carry (296,928 bytes
merged, the `ramoops@d0000000` and `framebuffer@a0000000` nodes still in it).

**This is worth separating from the wedge.** The phone went silent after the
write to `boot`, and it is tempting to read that silence as "the UEFI port does
not run". It is not evidence of that, and could not have been: the image was
refused twice over before any of our code was reached, and the fastboot state the
phone is actually stuck in (`docs/07`, "But that is not what the phone is stuck
in") has ABL resident and its command loop stalled — which is ABL's own failure,
not ours. The P2 gate is still open, and the next attempt tests the port for the
first time rather than re-testing it.
