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
they are — `kernel` at 0x800 holds the gzip stream, and the 71,737-byte DTB is
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

There is no "boot failed, so enter fastboot" path in the string table. ABL's
banner is `FastBoot Mode`, and the failures on the boot path (`Failed to
load/authenticate boot image: %r`, and the ten validation messages above) are
reported to the UART this phone does not have.

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

