# 02 — Partition map and backup

Every LUN on this device has been dumped to `~/backup/gauguin/images/`. The Smartisan R2
ROM currently installed is a `user/dev-keys` build with **no public image**, so these files
are the only copy that exists. Losing `super` or the small firmware LUNs means an
unrecoverable device.

All UFS LUNs here use **4096-byte logical sectors**, and several carry their GPT at a
non-zero byte offset within the LUN. Do not assume 512-byte sectors when parsing or
restoring them.

## What is in the backup

| Path (under `~/backup/gauguin/images/`) | Contents |
|---|---|
| `LUN-sdb.img` … `LUN-sdf.img` | whole-LUN dumps of the five small firmware LUNs. They include the GPT itself, so they are directly restorable. |
| `GPT-sda.bin` | the primary GPT of the big LUN (`sda`) |
| `part-<name>.img` | each `sda` partition except `userdata`, dumped by name |

`userdata` (107 GB of the 118 GB LUN) is deliberately **not** backed up — see the warning
at the end.

## `sda` — 118 GB, the OS LUN

| # | name | size | notes |
|---|------|------|-------|
| 0 | `switch` | 8 KB | bootloader switch |
| 1 | `ssd` | 32 KB |  |
| 2 | `dbg` | 32 KB |  |
| 3 | `bk01` | 32 KB |  |
| 4 | `bk02` | 128 KB |  |
| 5 | `bk03` | 256 KB |  |
| 6 | `bk04` | 512 KB |  |
| 7 | `keystore` | 512 KB | Android keystore |
| 8 | `frp` | 512 KB | factory reset protection |
| 9 | `bk05` | 2 MB |  |
| 10 | `misc` | 4 MB | bootloader control block (BCB) |
| 11 | `bk06` | 8 MB |  |
| 12 | `logfs` | 8 MB |  |
| 13 | `bk07` | 8 MB |  |
| 14 | `oops` | 16 MB |  |
| 15 | `devinfo` | 16 MB | bootloader unlock state |
| 16 | `oem_misc1` | 1 MB |  |
| 17 | `metadata` | 16 MB | Android metadata encryption |
| 18 | `bk08` | 15 MB |  |
| 19 | `bk19` | 32 MB |  |
| 20 | `splash` | 32 MB |  |
| 21 | `bk09` | 32 MB |  |
| 22 | `persist` | 64 MB | sensors / calibration data — device-specific, irreplaceable |
| 23 | `persistbak` | 64 MB | backup copy of persist |
| 24 | `logdump` | 64 MB |  |
| 25 | `rawdump` | 128 MB |  |
| 26 | `minidump` | 96 MB |  |
| 27 | `mtdblk` | 32 MB |  |
| 28 | `recovery` | 128 MB |  |
| 29 | `cache` | 384 MB |  |
| 30 | `exaid` | 384 MB |  |
| 31 | `cust` | 1.0 GB |  |
| 32 | `super` | 8.5 GB | Android dynamic partitions (system/vendor/product/odm/system_ext) — attr bit 60 = read-only |
| 33 | `oops` | 16 MB |  |
| 34 | `userdata` | 107.0 GB | **107 GB — deliberately not backed up** |

35 partitions.

### `sdb` — 16,777,216 bytes, sector size 4096

| name | size | byte offset in LUN | notes |
|------|------|--------------------|-------|
| `xbl` | 7 MB | 24,576 | primary bootloader — contains the whole signed DXE driver set |
| `xbl_config` | 512 KB | 7,352,320 | XBL configuration |

### `sdc` — 16,777,216 bytes, sector size 4096

| name | size | byte offset in LUN | notes |
|------|------|--------------------|-------|
| `xblbak` | 7 MB | 24,576 |  |
| `xbl_configbak` | 512 KB | 7,352,320 |  |

### `sdd` — 33,554,432 bytes, sector size 4096

| name | size | byte offset in LUN | notes |
|------|------|--------------------|-------|
| `ALIGN_TO_128K_1` | 104 KB | 24,576 |  |
| `cdt` | 896 KB | 131,072 | board configuration data |
| `ddr` | 2 MB | 1,048,576 |  |

### `sde` — 1,073,741,824 bytes, sector size 4096

| name | size | byte offset in LUN | notes |
|------|------|--------------------|-------|
| `multiimgoem` | 32 KB | 24,576 |  |
| `multiimgoembak` | 32 KB | 57,344 |  |
| `multiimgqti` | 32 KB | 90,112 |  |
| `multiimgqtibak` | 32 KB | 122,880 |  |
| `qupfw` | 128 KB | 155,648 |  |
| `qupfwbak` | 128 KB | 286,720 |  |
| `apdp` | 256 KB | 417,792 |  |
| `msadp` | 256 KB | 679,936 |  |
| `sec` | 32 KB | 942,080 |  |
| `secdata` | 32 KB | 974,848 |  |
| `limits` | 32 KB | 1,007,616 |  |
| `limits-cdsp` | 32 KB | 1,040,384 |  |
| `featenabler` | 128 KB | 1,073,152 |  |
| `featenablerbak` | 128 KB | 1,204,224 |  |
| `vbmeta` | 128 KB | 1,335,296 |  |
| `vbmeta_system` | 128 KB | 1,466,368 |  |
| `storsec` | 128 KB | 1,597,440 |  |
| `devcfg` | 256 KB | 1,728,512 | device configuration (fuse/lock bits) |
| `devcfgbak` | 256 KB | 1,990,656 |  |
| `aop` | 512 KB | 2,252,800 | Always-On Processor firmware |
| `aopbak` | 512 KB | 2,777,088 |  |
| `uefivarstore` | 512 KB | 3,301,376 | UEFI variable store — needed by Windows |
| `vbmeta_product` | 128 KB | 3,825,664 |  |
| `vbmeta_vendor` | 128 KB | 3,956,736 |  |
| `vbmeta_odm` | 128 KB | 4,087,808 |  |
| `bk40` | 4 MB | 4,218,880 |  |
| `cmnlib` | 1 MB | 8,019,968 |  |
| `cmnlibbak` | 1 MB | 9,068,544 |  |
| `cmnlib64` | 1 MB | 10,117,120 |  |
| `cmnlib64bak` | 1 MB | 11,165,696 |  |
| `keymaster` | 1 MB | 12,214,272 |  |
| `keymasterbak` | 1 MB | 13,262,848 |  |
| `bluetooth` | 1 MB | 14,311,424 | Bluetooth firmware (wcn3990) |
| `dip` | 1 MB | 15,360,000 |  |
| `uefisecapp` | 2 MB | 16,408,576 | UEFI secure application |
| `uefisecappbak` | 2 MB | 18,505,728 |  |
| `abl` | 2 MB | 20,602,880 | Android Bootloader (UEFI application) |
| `ablbak` | 2 MB | 22,700,032 |  |
| `tz` | 4 MB | 24,797,184 | TrustZone image |
| `tzbak` | 4 MB | 28,991,488 |  |
| `spunvm` | 32 MB | 33,185,792 |  |
| `hyp` | 512 KB | 66,740,224 | hypervisor image |
| `hypbak` | 512 KB | 67,264,512 |  |
| `gsort` | 16 MB | 67,788,800 |  |
| `dtbo` | 32 MB | 84,566,016 | device tree overlay |
| `logo` | 64 MB | 118,120,448 | boot logo |
| `dsp` | 64 MB | 185,229,312 | hexagon DSP firmware |
| `modem` | 320 MB | 252,338,176 | modem firmware |
| `mdtp` | 32 MB | 587,882,496 |  |
| `mdtpbak` | 32 MB | 621,436,928 |  |
| `mdtpsecapp` | 4 MB | 654,991,360 |  |
| `mdtpsecappbak` | 4 MB | 659,185,664 |  |
| `imagefv` | 2 MB | 663,379,968 | UEFI image firmware volume |
| `imagefvbak` | 2 MB | 665,477,120 |  |
| `boot` | 128 MB | 667,574,272 | Android boot image (kernel + ramdisk) |
| `vm-linux` | 32 MB | 801,792,000 |  |
| `core_nhlos` | 170 MB | 835,346,432 |  |
| `questdatafv` | 16 MB | 1,013,604,352 |  |
| `catefv` | 1 MB | 1,030,381,568 |  |
| `catecontentfv` | 1 MB | 1,031,430,144 |  |
| `toolsfv` | 2 MB | 1,032,478,720 |  |
| `cateloader` | 2 MB | 1,034,575,872 |  |

### `sdf` — 29,360,128 bytes, sector size 4096

| name | size | byte offset in LUN | notes |
|------|------|--------------------|-------|
| `ALIGN_TO_128K_2` | 104 KB | 24,576 |  |
| `modemst1` | 8 MB | 131,072 | modem persistent state |
| `modemst2` | 8 MB | 8,519,680 | modem persistent state |
| `fsg` | 8 MB | 16,908,288 | modem filesystem golden copy |
| `fsc` | 1 MB | 25,296,896 | modem file system cache |

## Restoring

The small-LUN dumps are byte-exact images including their partition tables, so a raw write
puts everything back:

```sh
fastboot flash xbl     ~/backup/gauguin/images/LUN-sdb.img
fastboot flash xblbak  ~/backup/gauguin/images/LUN-sdc.img
# sdd and sdf each hold several partitions; write them back as whole LUNs from
# fastbootd with dd, or extract individual partitions at the offsets listed above.
```

For `sda`, each partition was dumped individually, so:

```sh
fastboot flash <name> ~/backup/gauguin/images/part-<name>.img
```

**Do not repartition anything until a restore of at least one small partition has been
tested end to end.**

### The gap that was there, and the fix

The original backup dumped `sda` partition by partition and the five small LUNs whole. That
was a mistake for `sde`: it is a 1 GB LUN holding **62 partitions, including `boot`** — and
nothing carved `boot` out of it. So `boot` existed only as "somewhere inside `LUN-sde.img`
at LBA 162982", which is not a restore path anyone wants to compute by hand with a phone
sitting bricked.

`tools/carve-partitions.py` closes it. It parses the GPT inside each whole-LUN dump and
writes the same `part-<name>.img` files the per-partition dumps produced, so **every**
partition now has one obvious restore path:

```sh
python3 tools/carve-partitions.py --images ~/backup/gauguin/images
```

It re-derives the sector size rather than assuming it, and validates the guess against the
GPT header's own `MyLBA` field — necessary because a 4096-byte-sector LUN has a valid header
at byte 4096, which is also LBA 8 of a 512-byte-sector disk, and only one of those readings
checks out.

74 partitions carved from `sde` and `sdf`, all with their signatures verified:

| | |
|---|---|
| `boot` | 128 MB, `ANDROID!` — the stock boot image |
| `abl`, `ablbak` | XBL itself 
| `xbl`, `xblbak`, `xbl_config`, `xbl_configbak` | bootloader and its config |
| `tz`, `tzbak` | TrustZone |
| `vbmeta`, `vbmeta_system`, `vbmeta_product`, `vbmeta_vendor`, `vbmeta_odm` | all five AVB tables |
| `modem`, `dsp`, `bluetooth`, `aop`, `hyp`, `cmnlib*`, `keymaster*`, `uefisecapp*` | the rest of the firmware |

`abl` is the one that matters most: it is XBL, the thing that runs `fastboot` and decides
whether the phone boots at all. Before this it was reachable only through the whole-LUN
image.

### Note on `sde`'s partition count

The GPT inside `LUN-sde.img` lists **62** entries, but `docs/04`'s table of the same LUN has
fewer, and the earlier `uefiplat.cfg` work assumed the size. The carve is authoritative — it
reads the table rather than transcribing it. The count differs from what an earlier note in
this project recorded, which is worth knowing before trusting any hand-written table here.

## Before P4: save the user's data

`userdata` holds everything stored on the phone. Installing Windows means repartitioning
that space. Copy anything that matters off the device first — this backup does not
include it, and no public image of the installed ROM exists to fall back on.

