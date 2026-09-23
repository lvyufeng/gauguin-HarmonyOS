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

Nothing here writes to the device except step 4, which writes `boot` — and only
after step 3 has established that the phone is healthy. Confirm the pieces are
present first, because a missing file mid-session costs a whole cycle:

```sh
ls -la work/out/boot-pstore-*.img                     # P1's kernel, six shapes
ls -la work/out/p2-variants/Mu-gauguin-stock-*.img   # the two P2 variants
tools/restore-stock-boot.sh --check                  # must print "ok"
tools/flash-boot.sh                                  # usage line, exits 1 - it is executable
```

`ls` proves a file is there, not that it is current. Both sets are rebuilt in one
command each — `tools/build-p1-payloads.sh` and `tools/build-p2-payloads.sh` — and
both end by refusing to exit 0 if any image fails the offline checkers. Run the
one whose payload is about to be flashed rather than trusting the timestamps: the
P2 pair that was written to `boot` had been built by hand and was stale in two
ways that made it unbootable, neither of which `ls` or `tools/check-payload.py`
can see (`docs/07`).

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

Expect `1080`, `2400`, `4320`, `a8r8g8b8`, exactly one `ramoops@d0000000`, and
`font TER16x32 2`. Stride `4320` is 1080x4: a different stride draws diagonal
text, and a missing node leaves the panel dark with nothing to say why. The
ramoops address is ours to place rather than the phone's, and the build script
checks it against the phone's DRAM map and its `no-map` carveouts
(`tools/abl-boot-check.py`) — but a tree that lost the node entirely still boots
and still leaves no log, which is what this line catches. The font
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
step 4 — where the write is `tools/flash-boot.sh --twrp <image>`, the same single
command as the fastboot route. Step 4a names it; the `--twrp` flag exists so that
step 4 is still runnable when this step is the one that got the phone back.

**TWRP is also the only route that can answer the bootloader-side question.**
`fastboot-capture.sh` cannot read `misc` through ABL — it can only ask ABL what
ABL thinks, and a missing answer is exactly the condition being investigated.
From TWRP, with ABL out of the path entirely:

```sh
adb shell 'dd if=/dev/block/by-name/misc bs=4096 count=1' | od -c | head
```

`misc` was all zeros before the P2 write. A bootloader command block appearing in
it means **ABL marked the boot unbootable after a failed attempt** — there are no
A/B slots here, so this is the non-multislot flag `fastboot-capture.sh` reports as
`Non Multi-slot: Unbootable`, and it means the fastboot is a *consequence* of our
image being tried, not a refusal of it. That is a materially different verdict
from "the image was rejected", and it is the one reading that would say P2 was
reached. Worth doing on the same trip in, since getting here costs a power-button
sequence and the pstore log is the thing that does not survive it.

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

Six builds of the same kernel, differing only in the properties that
distinguish our images from the one that boots (see the table in `docs/07`).
Each one removes exactly one difference, so the *change* between attempts is the
signal.

| # | image | kernel | arm64 header | `text_offset` | `image_size` | size |
|---|---|---|---|---|---|---|
| 1 | `boot-pstore-raw-noefi.img` | raw | **no EFI stub** | 0x80000 | 0x2c50000 | 46,092,288 |
| 2 | `boot-pstore-gz-noefi.img` | **gzip** | no EFI stub | 0x80000 | 0x2c50000 | 14,766,080 |
| 3 | `boot-pstore-raw-txt.img` | raw | EFI stub | 0x80000 | 0x2d90000 | 47,271,936 |
| 4 | `boot-pstore-raw.img` | raw | EFI stub | 0 | 0x2d90000 | 47,271,936 |
| 5 | `boot-pstore-gz-fixedsz.img` | gzip | EFI stub | 0 | **0x2cb8200** | 15,282,176 |
| 6 | `boot-pstore.img` | gzip | EFI stub | 0 | 0x2d90000 | 15,278,080 |

The size column is the one that drifts: every rebuild moves it a little, and a
size that no longer matches reads as "I flashed the wrong file" when the file is
right. `tools/check-payload.py`, which the step below requires before flashing
anything, is the authority on all six columns; the sizes are here to be glanced at,
not compared.

**1 against 2 is the experiment.** Same kernel, built the same way, packaged with
the same `text_offset` and the same declared `image_size` — the only difference
between them is that 2's kernel is a gzip stream. Compression is the last
property that can be blamed for the pattern actually observed (every compressed
image refused, every raw one booting), and this pair changes nothing else, so the
difference between the two attempts has one explanation instead of three.
**Flash 1, then 2**, and read the pair rather than either one.

The rest of the table is context, and two of its columns are now known to be
**dead**:

- `text_offset` is bootloader metadata that the kernel never reads back. Rows 1
  and 3 set 0x80000 only to match the phone's own image on a field that has no
  behaviour behind it, and 4 does not; the 1-vs-4 difference is therefore not a
  candidate.
- `image_size` is read by exactly one ABL check, which the headroom has never
  come close to failing — 131,305,472 bytes under this device's memory map
  against a largest declared `image_size` of 47,775,744, and the other check in
  the same function compares the decompressed length against the size of a
  pointer (`docs/07`). Row 5's lowered `image_size` was built to test a check that
  does not exist.

So 3, 4 and 6 exist to keep the older comparisons intact; 5 is kept because it is
built, not because it is a candidate. The one thing worth trying after 1 and 2 is
4b/4c below, which is the same experiment one layer up.

all under `work/out/`. Every one is stock-shaped v2 with our own DTB in the
declared DTB slot. It is *not* true that ABL uses that tree as-is: our tree
carries gauguin's exact `msm-id` and `board-id`, but `CheckAllBitsSet` also wants
`qcom,pmic-id`, `qcom,softsku-id`, `qcom,platform-subtype` and `qcom,foundry-id`,
and a tree that declares none of them leaves `DtboNeed` TRUE (`docs/07`). ABL
therefore applies the Gauguin overlay to our tree on every boot, which is why
the built tree carries a `/__symbols__` naming the 158 fixup symbols that
overlay asks for, and why `tools/build-p1-payloads.sh` refuses to ship a tree
without it — with the merge then performed for real by `fdtoverlay` and not just
replayed in Python, because the two readers disagree on a tree whose symbols
point at phandle-less nodes (`docs/07`). All six carry the same cmdline
(`docs/p1-cmdline.txt`), whose pstore
parameters place the log at an address **we choose** — the phone declares no
ramoops region of its own, and the address is checked against the phone's DRAM
partitions and its `no-map` carveouts rather than against another tree's opinion
(`docs/07`, `tools/abl-boot-check.py`).

Start at the top of the table, and treat the first two rows as one step.
**1 is the one most likely to work**: it is the only build that matches the
phone's own kernel on the raw property, the EFI-stub property *and* `text_offset`
at the same time. **2 is 1 with its kernel gzipped and nothing else changed**, so
the pair answers the one question that is still open.

**5 is now known to be testing nothing**: the ABL message it was built for,
`Decompress kernel size is smaller than image header size`, compares the
*decompressed length* against `sizeof(struct kernel64_hdr *)` — the size of a
pointer. A 46 MB kernel cannot fail it, and BootShim's image was passing it by
accident of arithmetic rather than by construction. The other size check in the
same function reads `ImageSize` out of the kernel and compares it against the
headroom between the kernel's load address and the device tree's, which is 125 MB
here against a largest declared `image_size` of 47,775,744 — so lowering
`image_size` changes nothing either. 5 stays in the table because it is built,
not because it is a candidate. `docs/07` has the source.

Before flashing anything, run

```sh
tools/check-payload.py work/out/boot-pstore-*.img
```

which prints each image's own properties and fails loudly on a bad magic, a DTB
not at its declared offset, declared regions that do not add up to the file size,
an AVB footer, or a ramoops node that the cmdline contradicts. The variants
differ only in things `ls -la` cannot show, and mixing two up mid-session reads
as "that variable does not matter" when the wrong file was flashed. It is also
run for you: `tools/build-p1-payloads.sh` ends by checking every image it built
with this and then replaying ABL's own decision path over them with
`tools/abl-boot-check.py`, so a structurally wrong or unbootable payload fails
the build rather than costing a device cycle.

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
tools/flash-boot.sh work/out/boot-pstore-raw-noefi.img    # or the next one down
```

Not `fastboot flash boot` directly, for two reasons that both cost a session
when they bite. It **reads the partition back and compares it** to the file, so
"the write landed" is established rather than assumed — `fastboot flash` verifies
its own transfer, but a wrong file or a partial write after a USB drop is
indistinguishable from success at the prompt. And it takes the TWRP route
(`--twrp`) as easily as the fastboot one, which matters because TWRP does not go
through ABL's fastboot at all. If step 1b was reached — the image in `boot`
wedging ABL, so every reset reproduces it — then TWRP is not the fallback, it is
the only route, and a runbook whose step 4 says `fastboot flash` has no way to run.

There is a counter-intuitive consequence of the port dropping its link roughly
once a minute, worth stating because the usual advice is the opposite: **for a
large payload, prefer TWRP.** `fastboot flash` has no cancel — killing it
mid-download strands the bootloader until a physical reset, which is how the first
wedge happened. A link drop during `adb push` fails the push and nothing else, and
the push can simply be retried.

**4b/4c. The two UEFI variants** — `Mu-gauguin-stock-gzip.img` (stock-shaped,
compressed kernel, dummy ramdisk) and `Mu-gauguin-stock-none.img` (the same but
uncompressed). Their value is that each pairs with a P1 variant on the one
property that is easy to blame: `-gzip` against 2/5/6, `-none` against 1/3/4. Read
the pair, not the attempt. With 1 and 2 already run, this pair is the same
experiment one layer up: it asks whether compression acts on the kernel or on
ABL's path to it, and only the pair can say.

**The inference that used to be drawn here was not available, and this is the
correction worth carrying into the next session.** The section used to argue that
eight images sharing a v2 header, our DTB in the declared DTB slot and ABL's
load-and-authenticate path, all producing the same result, pointed at ABL rather
than at any kernel property. The P2 half of that was never true: the image that
was written to `boot` could not execute, and the reason is a property of the file
rather than of UEFI (`docs/07`) — a device tree with no `/__symbols__`, so ABL
refuses the overlay at `ApplyOverlay`. A second reason was given at the time
(`header_version = 1`, at which ABL was said to locate no DTB by any route) and it
has since been retracted: the image's kernel is a gzip package, so ABL's
decompressor supplies the DTB offset and the header version never decides it.
ABL refusing a payload says nothing about the payload's own code, and the
outcomes were not the same outcome either: P1's images were refused on the RAM
chain-load path, and the P2 image wedged a bootloader that never reached it. So
**4b/4c has not been run**, and the next attempt is the port's first execution
rather than a repetition of a negative result.

What survives is the pairwise design, which is unchanged and still the reason to
read 4b/4c as a pair:

| what 4b/4c do relative to 4a | what it means |
|---|---|
| `-gzip` matches 2/5/6 and `-none` matches 1/3/4 | the property that differs *between* those groups is the live variable; the UEFI code is not implicated yet |
| `-gzip` and `-none` agree with each other but **not** with their P1 partners | compression is not the variable; what differs is the payload, so P2 has its own problem and the UEFI side needs its own investigation |
| 4b/4c differ from each other, and follow their P1 partners | compression is the variable, and it acts on ABL's path rather than on the kernel — which is a statement about *where* to look, not about which check: the `Decompress kernel size` message that used to be the candidate here compares the decompressed length against the size of a pointer and cannot fire (`docs/07`) |

There is a fourth outcome that the table did not have a row for and that the
failed attempt shows is the one to expect first: **nothing changes at all.** The
screen stays as it was and `oem fbreason` answers the same as step 2. That is
`docs/07`'s "One enumeration, then nothing" — which has an important corollary
this runbook relies on. ABL's USB descriptors still answer in that state, and
they can only answer from ABL, so **a silent fastboot means ABL is stuck, not
that our image ran.** Reading it the other way is how a payload that never
executed gets recorded as a payload that failed.

After each attempt, the rule is the same as 4a: a change in `oem fbreason` is the
signal, and the difference between this attempt and the one it is paired with is
the variable that matters.

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

The primary reader is **our own initramfs**, and it needs no host at all: on every
boot it lists `/sys/fs/pstore` and prints the tail of `console-ramoops-0` on the
panel, above the hardware report. So the designed shape is `panic=10` fires → the
phone warm-reboots by itself → ABL boots our payload again → the panel shows the
*previous* boot's log. Photograph it. `docs/07` has the sequence; the point here
is that a payload which dies can report why without anyone touching the phone.

If the payload instead comes up and stays up, the same text is on the screen from
that first report, and a host read is only a convenience:

```sh
adb shell 'ls -la /sys/fs/pstore/'
adb shell 'cat /sys/fs/pstore/console-ramoops-0' | tail -200
```

Either route reads `console-ramoops-0` (the printk stream) and `dmesg-ramoops-0`
(the older ring the same stream spills into).

**Whose log it is is not a question on this device.** Android cannot have written
it: the vendor kernel has no `/sys/fs/pstore` at all, and logs its panics through
`mtdoops` onto `/dev/block/sda15` instead (`docs/07`). So a file in the region is
ours — mainline — whenever it was written by a payload boot rather than by
Android. A `head -1` is still worth a glance for the version string, since our
own previous boot is the one thing that can also be in there:

```sh
adb shell 'head -1 /sys/fs/pstore/console-ramoops-0'
```

`Linux version 6.6…` is mainline; anything `4.19` means the region holds an older
build of ours from an earlier attempt, not this one. Then read for `UFS`,
`ufshcd`, `geni`, `simple-framebuffer`, `ramoops`, and the last line before it
stops. That output is what P1's gate is actually asking for, and it is also the
input to P2.

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
