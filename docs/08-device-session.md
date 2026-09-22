# 08 — The device session runbook

Everything in this project needs the phone, and the phone is only reachable in
short windows: it has no UART, a wedged fastboot needs the power button, and the
Thunderbolt port drops its link roughly once a minute. **Device time is the
scarce resource**, and the sessions so far have spent it badly — one question at
a time, with a discovery in the middle of each one changing what the next
question should be.

This is the fixed sequence for the next session, written so that no thinking is
required while the phone is in hand. Each step says what to run, what each
outcome means, and where to go next.

---

## Before touching the phone

Nothing here writes to the device except step 5, which writes `boot` — and only
after step 4 has established that the phone is healthy. Confirm the pieces are
present first, because a missing file mid-session costs a whole cycle:

```sh
ls -la work/out/boot-pstore-*.img                     # P1's kernel, five shapes
ls -la work/out/p2-variants/Mu-gauguin-stock-*.img   # the two P2 variants
tools/restore-stock-boot.sh --check                  # must print "ok"
```

If `--check` fails, stop. Every write in this runbook depends on that file being
the byte-identical stock image.

Both of the payload's output channels live in the device tree that is embedded
in those images, and both have failed this way before, so check them in one
command rather than discovering it a session later. `tools/build-p1-payloads.sh`
refuses to build a DTB that is missing either; this is the check that the file
you are about to flash was built by it:

```sh
F=work/out/sm7225-xiaomi-gauguin.dtb
for p in width height stride format; do
    printf '%-8s ' "$p"; fdtget "$F" /chosen/framebuffer@a0000000 "$p" 2>&1; done
dtc -I dtb -O dts -o - "$F" 2>/dev/null | grep ramoops@ | sed 's/^ *//'
# and the font the command line names must be in the kernels, not just asked for
fn=$(tr ' ' '\n' < docs/p1-cmdline.txt | sed -n 's/^fbcon=font://p')
printf 'font %-4s %s\n' "$fn" "$( { strings -a work/out/Image-noefi
                                   zcat work/out/Image-pstore.gz | strings -a
                                 } | grep -cx "$fn")"
```

Expect `1080`, `2400`, `4320`, `a8r8g8b8`, exactly one `ramoops@bff00000`, and
`font TER16x32 2`. Stride `4320` is 1080x4: a different stride draws diagonal
text, and a missing node leaves the panel dark with nothing to say why. The font
count catches the other silent half — `fbcon=font:X` for a font the kernel does
not carry makes fbcon fall back to 8x16 without printing anything, which on a
1080-wide panel is 135 unreadable columns and a photograph nobody can read.

---

## Step 0 — Try the free recovery first, then classify what you are looking at

A stranded fastboot has so far been treated as needing the power button. Before
that, three host-side resets were tried and **none works** — recorded so nobody
spends time on them again:

| attempt | result |
|---|---|
| toggle `/sys/bus/usb/devices/3-1/authorized` | no re-enumeration |
| `USBDEVFS_RESET` ioctl on `/dev/bus/usb/003/002` | no re-enumeration |
| unbind + rebind `0000:6c:00.0` in `xhci_hcd` (full bus teardown) | bus rebuilt, device re-enumerated, **ABL still silent** |

The third is the interesting one: it forces a real USB reset on the wire and the
device re-enumerates, so the host side is provably clean — and `getvar product`
still times out. The stuck state is not in the host stack or the link.

(For reference, `fastboot devices` will keep working through all of this,
because it only reads the USB descriptor.)

### Which silence is it

`tools/fastboot-capture.sh` now runs `tools/unwedge-fastboot.py` when the probe
fails, because "wedged" is three different states and only two of them are worth
trying to recover from. It reads the endpoints directly with libusb and prints
which one this is. Measured on the phone as it stands (2026-09-23):

```
EP0 GET_DESCRIPTOR(device)  rc=18  (18 = firmware answers control requests)
EP0 GET_STATUS(IN  0x81)  rc=2 value=0 not halted
EP0 GET_STATUS(OUT 0x01)  rc=2 value=0 not halted
EP0 SET_CONFIGURATION(1)   rc=0  (>=0 = accepted by the firmware)
--- draining IN (0x81) for 2s, 2000 ms per read
   bulk_in rc=-7 after 0 bytes (timeout: nothing pending)
--- sent b'getvar:product' on OUT: rc=-7 wrote 0
--- response after 20.0s (0 bytes): b''
```

| state | IN drain | OUT write | recoverable |
|---|---|---|---|
| truncation wedge (client closed mid-response) | **returns the unread tail** | — | yes: the drain releases the thread |
| download wedge (aborted 128 MB transfer) | nothing | **accepted** | in principle; do not guess the reply |
| fastboot thread stuck outside its transport | nothing | **NAKs** | **no** — power button |

The current state is the third row: EP0 answers and `SET_CONFIGURATION` is even
accepted, so ABL's USB stack is running, while the bulk endpoints are armed in
neither direction. That is a USB stack that has outlived its application — ABL's
fastboot is interrupt-driven, so the interrupt context keeps answering long after
the thread that owns the command loop has stopped. It also explains the two rows
above: **neither of the two recorded wedges is what the phone is in now**, and
`getvar product` timing out is not by itself evidence of the truncation wedge.

Worth knowing for interpreting a session, because it cuts both ways. A
non-answering fastboot looks like "something ran and took the CPU away", and it
is not that: the descriptors come back, so ABL's firmware is still resident and
answering. If our payload had reached the point of taking over, ABL would be gone
and EP0 with it, and the device would not be enumerating as `18d1:d00d` with
ABL's serial number at all. **So this state says ABL is still there and stuck —
not that our image executed.**

## Step 1 — Reset and power on cleanly

Hold **Power** ~20 s until the phone restarts. Then, from off, press **Power
once** and nothing else.

No volume keys. This matters: ABL's fastboot-reason code 0 (`Down Key Press`) is
written when the power-on reason is 2 or 8, so a reset performed with the power
button can produce that answer regardless of what the firmware did. Powering on
with no keys held is what makes the answer trustworthy.

Watch the screen. Note in one line what appears and whether it ever changes.

## Step 1b — If the reset does not give you an answering fastboot

It may not, and the reason matters: **`boot` currently holds the P2 UEFI image.**
A reset makes ABL try that image again, so if attempting it is what wedges ABL,
the reset reproduces the wedge — and repeating the reset is a loop, not a
retry. The capture script's classifier tells the two apart: it will say the
thread is stuck with EP0 alive, which is the same verdict as before, and that
means the image in `boot` is implicated in the wedge rather than merely being
refused by it.

If that is what happens, do not reset again. Go in through TWRP instead, which
does not need ABL's fastboot at all:

```
Power + Volume Up  →  TWRP
tools/restore-stock-boot.sh --twrp      # push + dd, from the TWRP-visible path
```

Then reboot and establish that Android comes back (that is the A/B control from
step 3, and it is worth having before any more payloads), and only then return to
step 4.

One cost to know about, because it is easy to lose the thing you came for:
reaching TWRP this way is a power-button path, and a pstore log does not survive
a cold start — the region is in RAM. So **the payload log is only readable via
the warm path**, which is the payload panicking on its own (`panic=10`), the
phone restarting itself (`reboot=panic_warm`), and Android coming up without a
hand on the power button. If that did not happen, there is no log to go and
recover; go straight to restoring the stock image.

## Step 2 — Ask why it is in fastboot

```sh
tools/fastboot-capture.sh
```

The first thing it prints is `oem fbreason`. This is the answer that has been
missing since the firmware was written, and there are exactly five possible
values (see `docs/07`):

| answer | meaning | next |
|---|---|---|
| `Reason:LoadImageAndAuth Fail` | ABL **tried to load `boot`** and could not | **step 4** |
| `Reason:BootLinux Fail` | it loaded, and failed after that | **step 4** |
| `Reason:Unknown` | nothing set it (this is the default, code 4) | **step 3** |
| `Reason:Reboot Bootloader` | something deliberately asked for fastboot | **step 3** |
| `Reason:Down Key Press` | suspect this one — see step 1 | **step 3** |

The script also dumps `oem uefilog` / `lkmsg` / `lpmsg`, `oem device-info`,
`getvar all`, and `slot-unbootable` / `slot-retry-count`. Read every file it
wrote before moving on; the whole point of capturing them together is that a
later command may not answer.

## Step 3 — Establish whether the phone can boot anything we produce

Only if step 2 said the boot path was not entered. This is the A/B control, and
it is what separates "our image is refused" from "this phone no longer boots":

```sh
tools/restore-stock-boot.sh          # auto-detects fastboot or TWRP
```

Reboot, and watch.

| outcome | meaning |
|---|---|
| Android boots | the phone is healthy, our images are the problem → **step 4** |
| Android does not boot | something other than our image changed state → stop; this is a different investigation (BCB in `misc`, unlock state, userdata) |

## Step 4 — Try the payloads, cheapest first

Each attempt: write, reboot, watch the screen, then run
`tools/fastboot-capture.sh` again and compare `oem fbreason` with step 2. The
*change* in the answer is the signal.

Order matters — cheapest and most informative first:

**4a. P1's mainline kernel — the closest match to what the phone boots today**

Five builds of the same kernel, differing only in the properties that
distinguish our images from the one that boots (see the table in `docs/07`).
Each one removes exactly one difference, so the *change* between attempts is the
signal.

| # | image | kernel | arm64 header | `text_offset` | `image_size` | size |
|---|---|---|---|---|---|---|
| 1 | `boot-pstore-raw-noefi.img` | raw | **no EFI stub** | 0x80000 | 0x2c50000 | 46,075,904 |
| 2 | `boot-pstore-raw-txt.img` | raw | EFI stub | 0x80000 | 0x2d90000 | 47,255,552 |
| 3 | `boot-pstore-raw.img` | raw | EFI stub | 0 | 0x2d90000 | 47,255,552 |
| 4 | `boot-pstore-gz-fixedsz.img` | gzip | EFI stub | 0 | **0x2cb8200** | 15,265,792 |
| 5 | `boot-pstore.img` | gzip | EFI stub | 0 | 0x2d90000 | 15,261,696 |

The size column is the one that drifts: every rebuild moves it a little, and a
size that no longer matches reads as "I flashed the wrong file" when the file is
right. `tools/check-payload.py`, which the step below requires before flashing
anything, is the authority on all six columns; the sizes are here to be glanced at,
not compared.

all under `work/out/`. Every one is stock-shaped v2 with our own DTB in the
declared DTB slot — our tree carries gauguin's exact `msm-id`/`board-id`, which
is the one case where ABL uses it as-is instead of overlaying the vendor's on
top — and all five carry the same cmdline (`docs/p1-cmdline.txt`), whose pstore
parameters are the phone's own geometry so that step 4.5's log is readable
(`docs/07`).

Start at the top of the table. **1 is the one most likely to work**: it is the
only build that matches the phone's own kernel on the raw property, the
EFI-stub property *and* `text_offset` at the same time.

4 is worth understanding rather than skipping. ABL contains

```
Decompress kernel size is smaller than image header size
```

and BootShim's image is written so that it *passes* — `image_size` 0x300000,
decompressed length 0x300070. A Linux `Image.gz` fails it, because `image_size`
covers BSS and is therefore larger than anything the gzip stream can contain.
4 declares the real decompressed length instead. That check is the closest thing
found so far to an explanation of why every compressed image has been refused
while every raw one boots, so 4 is the attempt that tests it — but it costs a
cycle, so it goes after 1.

Before flashing anything, run

```sh
tools/check-payload.py --stock ~/backup/gauguin/images/part-boot.img work/out/boot-pstore-*.img
```

which prints these properties next to the phone's own image and fails loudly on
a bad magic, a DTB not at its declared offset, or an AVB footer. The variants
differ only in things `ls -la` cannot show, and mixing two up mid-session reads
as "that variable does not matter" when the wrong file was flashed.

Worth noting before spending the cycle: `fastboot boot` — the RAM chain-load path
— was what refused the earlier gzip image, and that is **not the same code path
as booting from the `boot` partition**, so its failure does not predict anything
here.

**The screen is the console, so read it before anything else.** This payload does
not print to a dummy console: the board device tree carries a
`simple-framebuffer` node at `0xa0000000` (1080x2400, stride 1080x4, `a8r8g8b8`)
describing exactly the framebuffer ABL has just drawn the logo on, the kernel has
`DRM_SIMPLEDRM` + `DRM_FBDEV_EMULATION` + `FRAMEBUFFER_CONSOLE`, and the command
line ends with `console=tty0` — last, so `/dev/console`, and therefore the boot
log *and* init's output, land on the panel. `fbcon=font:TER16x32` is in there so
that 1080 pixels carry 67 legible columns rather than 135 unreadable ones.

So the screen is a graded signal, not a pass/fail:

| what the screen does | what it means |
|---|---|
| the logo never changes | nothing of ours executed, or it died before `simpledrm` bound — the bootloader's logo is still the framebuffer's contents either way |
| the logo is replaced by text | the kernel is running, and **the last line is where it stopped** |
| the text keeps changing (`alive: N s uptime, heartbeat M`) | init reached userspace and the hardware report above it is complete — that is P1's gate |
| text, then the phone restarts by itself after ~10 s | a panic, and the warm reset that makes the ring readable — **step 4.5 now, not another payload** |
| the screen goes dark and stays dark | something took the display over and drew nothing; the log is still in pstore, step 4.5 |

Two things the screen can tell you that nothing else can, for free. The **font
size** says whether you flashed a current payload: 67 columns means TER16x32 is
in the kernel, and a tiny 135-column wall of text means it is not. And the
**photograph** is the only record that exists if the phone then wedges, so take
one at each attempt.

The other evidence, in the order it costs you something:

1. Does it come back to fastboot at all? If the phone sits there dark and does
   *not* return to ABL's fastboot, something executed.
2. `oem fbreason` after the next boot (the table in step 2).
3. The pstore log — step 4.5. It outlives the screen and is the one channel that
   survives a boot that died before `simpledrm` bound.

```sh
fastboot flash boot work/out/boot-pstore-raw-noefi.img    # or the next one down
```

(or, from TWRP, `adb push` + `dd`, which is what `restore-stock-boot.sh --twrp`
does and which does not depend on ABL's fastboot answering at all — use that if
the fastboot route has just stranded itself.)

**4b/4c. The two UEFI variants** — `Mu-gauguin-stock-gzip.img` (stock-shaped,
compressed kernel, dummy ramdisk) and `Mu-gauguin-stock-none.img` (the same but
uncompressed). These test whether P2's silence is the same problem as P1's
refusal: if 4a changes nothing at all — same `fbreason`, same screen — then the
two builds fail for different reasons, and the UEFI side needs its own
investigation rather than more payload variants.

After each: if `oem fbreason` changes to `LoadImageAndAuth Fail`, the payload is
being reached and the difference between this attempt and the last one is the
variable that matters.

## Step 4.5 — Read the log back, before touching the power button

If step 4a ran, the kernel may have left a log in the pstore ring even though it
could not print anything. **It survives a warm reboot but not a power cycle.**

`reboot=panic_warm` is in the cmdline for exactly this reason: mainline turns it
into a PSCI `SYSTEM_RESET2` warm reset, which keeps DRAM (the chain is verified
one call at a time in `docs/07`). So the sequence to aim for is `panic=10`
firing, the phone restarting **by itself**, and then Android coming up — with no
hands on the phone. Holding the power button, or a `fastboot reboot` that happens
to take the cold path, throws the log away.

The kernel is built so that this is the *expected* shape of a failed attempt
rather than a lucky one. The two failures this bring-up is most likely to hit —
an oops (a wrong property in our DTB dereferencing NULL in a probe) and a spin in
a probe waiting on a clock or regulator that never comes ready — both end in a
panic now, and a panic is the only thing that makes the phone restart itself and
flush the ring. So **the phone rebooting on its own roughly ten seconds after the
logo is a result, not a misfire**: let it come up, then go and read the ring.

If the ring is empty, read the ambiguity before concluding anything. `psci_init_system_reset2()`
asks the device whether it supports `SYSTEM_RESET2` and prints nothing either
way, so an empty ring is equally consistent with "the payload never panicked" and
with "the device answered no, the reset was cold, and DRAM went with it". A
payload that hung *without* panicking does leave nothing at all; a black screen
and an empty pstore together mean "it never got far enough to say" — but they do
not distinguish the two causes, and "no log, so it never ran" is one reading too
many.

From Android (root) or from TWRP:

```sh
adb shell 'ls -la /sys/fs/pstore/'
adb shell 'cat /sys/fs/pstore/console-ramoops-0' | tail -200
```

Our kernel writes the same geometry the vendor kernel reads (`docs/07`), so
`console-ramoops-0` is the file to read, and `dmesg-ramoops-0` … `-5` hold the
dmesg ring.

**Check whose log it is before reading anything into it.** Android also writes
this region on every ordinary boot, so a file that exists is not evidence ours
ran. The first line settles it:

```sh
adb shell 'head -1 /sys/fs/pstore/console-ramoops-0'
```

`Linux version 6.6…` is ours — mainline. The vendor kernel is `4.19`, so a
`4.19` first line means you are reading Android's own previous boot and our
payload left nothing. Then read for `UFS`, `ufshcd`, `geni`,
`simple-framebuffer`, `ramoops`, and the last line before it stops. That output
is what P1's gate is actually asking for, and it is also the input to P2.

## Step 5 — Leave it bootable

Whatever the outcome, end the session with the stock image back on `boot`:

```sh
tools/restore-stock-boot.sh
```

Do not leave an experimental image on the phone. The next session should start
from a known state, and the phone is someone's daily driver in between.

---

## What to record

For each attempt, one line — the phone is at a distance and memory is not a
channel:

```
attempt  image                          fbreason                     screen            pstore
-------  -----------------------------  ---------------------------  ----------------  ------
1        (as found)                     LoadImageAndAuth Fail        Redmi logo, then fastboot   n/a
2        work/out/boot-pstore-raw-*.img ...
```

That table is the entire output of a session. Everything else is in the files
`fastboot-capture.sh` wrote, and — if step 4a ran — in the pstore ring, which is
gone the moment the power button is held.
