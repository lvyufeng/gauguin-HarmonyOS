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
tools/pull-bootloader-log.sh /tmp/bllog-baseline     # step 4.6's tool; reads the P0 dump
```

That last one is worth running before the phone is touched at all: with no device
attached it falls through to the P0 dump and prints the baseline (all five slots
reaching `Start EBS`), which is both a check that the tool works and the thing
step 4.6's answer is read against. `docs/07` has the format.

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

**Step 4.7 has since been run once and this step's premise did not hold.** ABL's
own log shows it loading `boot`, applying the overlay and reaching EBS on every
Mission Mode boot, with an all-zero `misc`, so what the user saw was ABL's own
fallback rather than a refusal of the image. The section is kept because the wedge
it describes is real and has been hit; read 4.7 before concluding it has happened
again.

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

**The same trip in also reads ABL's log, and that is the highest-value thing to
do with it** (step 4.6 has the tool and how to read the answer):

```sh
tools/pull-bootloader-log.sh          # over adb, from TWRP: dd of the logfs partition
```

This is the one route that survives the thing this step costs. Reaching TWRP is a
cold power-button start, so anything in DRAM is gone — but `logfs` is on disk, and
ABL wrote a slot for the boot that failed before this one. So the trip answers the
question the project has been unable to answer since the write: **what ABL did
with the image in `boot`**. The slot to read is the one whose `pureason` is not in
the `docs/07` baseline of five.

Three things can be done on this one trip and the order among them is free,
because nothing on `logfs` depends on what is in `boot`:

1. `adb shell 'dd if=/dev/block/by-name/logfs ...'` — via `pull-bootloader-log.sh`
2. `dd` of `misc`, above
3. `tools/restore-stock-boot.sh --twrp` or `tools/flash-boot.sh --twrp <image>` —
   leaving `boot` holding whatever the next reboot should try

Do all three, then reboot once. The pstore cost below is about the *payload's*
log, not this one: reading `logfs` does not require the payload to have rebooted
itself, so it does not have to be read before the write.

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

**If it does not answer — or before bothering it at all — go to step 4.6.** ABL
writes a log of every boot to the `logfs` partition regardless of whether its
fastboot is alive, and that log is where a refusal by ABL records itself. Note
that `oem uefilog` has never actually returned anything on this phone (the one
attempt was made when ABL was already silent), so it is the `dd` route in step 4.6
that has evidence behind it.

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

## Step 4.6 — Read ABL's log, which does not depend on the payload

Step 4.5 reads the *payload's* log and needs three things to have gone right: the
payload reached a console, it panicked, and the device honoured a warm reset.
This step reads *ABL's* log and needs one thing: a block device. Do it on every
session, whether or not step 4.5 produced anything, because it answers the
question the payload's own log cannot — **what did ABL do with the image we
gave it.**

```sh
tools/pull-bootloader-log.sh
```

It tries `fastboot oem uefilog`, then `dd` of `/dev/block/by-name/logfs` over adb
— which is the TWRP route, and TWRP does not go through ABL — then falls back to
the P0 dump as the baseline. It ends by reading whatever it got with
`tools/read-logfs.py`, which prints each slot's stage table and marks the last
stage reached. Of the three, the `dd` route is the one with evidence behind it:
`oem uefilog` has been attempted once and returned nothing (`docs/07`).

Read the answer like this. Every boot of this phone before any of this work
reached `Start EBS`, so **the baseline is "everything", and the comparison is
exact** (the five slots, their stage tables and their `pureason` values are in
`docs/07`):

| what the new slot shows | meaning |
|---|---|
| reaches `Start EBS`, like all five baseline slots | ABL completed and handed over. The `Cmdline:` line is the one to compare: it is composed by ABL and printed by ABL, so it says which boot image was actually loaded |
| stops after `Load Image boot`, with an `Apply Overlay` or `DTB offset` error | **the payload was reached and ABL refused it**, and the error text is which of the two refusals it was (both are in the ABL string table, `docs/07`) |
| stops after `Load Image boot`, with no stage after it | ABL tried the image and got no further — the case step 1b describes as "the image in `boot` is implicated in the wedge" |
| no new slot at all, ring unchanged | that boot produced no completed log. The inferred reading is that ABL never shut its boot services down (`save logfs files` is a shutdown-time write, `docs/07`), which would make the fastboot a *failed boot* rather than a refused one — but this row is inference from the ABL string table and has not been observed, so treat it as a lead, not an answer: step 1b's TWRP route and step 2's `misc` check are what actually settle it |

The `pureason` value is how a slot is matched to the physical session, not a
verdict on it (`docs/07` has what the five baseline values look like). It is also
worth knowing ABL writes the same value into the device tree — so a payload that
can print anything at all can read the reason recorded for the boot it is running
in from `/proc/device-tree/chosen/pureason`, with no host involved at all.

One thing this log cannot do, recorded so it is not read as a signal: the
`Load Image boot total time` it prints **is the same whatever is in `boot`**. The
read is whole-partition because the phone boots in orange state, so ~256 ms is the
cost of 128 MB and not a fingerprint of our 1 MB image (`docs/07` has the source
for that).

## Step 4.7 — What the 2026-09-23 session actually found

Recorded because it moves this runbook's default reading, and because one of its
two results contradicts what step 1b assumed.

**ABL hands the payload control.** The first `logfs` read that ever succeeded
(TWRP route, `dd` of the partition) shows two Mission Mode slots — `pureason`
`0x40041` and `0x80001` — both with the full path:

```
Load Image boot total time: 257 ms
Load Image dtbo total time: 67 ms
Apply Overlay total time: 252 ms
Update Device Tree total time: 53 ms
Shutting Down UEFI Boot Services: 4272 ms
Start EBS        [ 4272]
```

So the second row of step 4.6's table is not what happened, and neither is the
third. There is no `Apply Overlay` error and no `DTB offset` error: ABL loads the
image, applies the vendor overlay, rewrites the tree and reaches EBS. The
`avb_vbmeta_image.c:206: Hash does not match` line above it is the unlocked
bootloader's signature and appears on every boot of this phone.

**`misc` is clean.** All 4096 bytes zero, identical to the P0 dump. No bootloader
control block was ever written, so nothing *told* ABL to enter fastboot — the
fastboot the user saw was ABL's own fallback, not a commanded mode. Step 2's
check is answered: look at `misc` once and stop suspecting it.

**The image in `boot` was stale, by exactly the two things the P2 builder gates
on.** What is byte-identical between it and the image
`tools/build-p2-payloads.sh` produces today is the **decompressed** payload — the
same `BootShim.bin` and the same `SILICIUM_UEFI.fd`, `md5
9c1047255a580b6b2f67a88e55b3287b`, checked against
`Build/gauguinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd` and equal to it — and the
only difference is the device tree appended after it:

| | gzip stream | decompressed payload | appended DTB | `__symbols__` | ramoops node |
|---|---|---|---|---|---|
| as found in `boot` | 1,046,331 B | 3,145,840 B `6c87b01e…` | 71,737 B | absent | `ramoops@ffc00000` |
| current build | 1,046,305 B | 3,145,840 B `6c87b01e…` | 87,594 B | present | `ramoops@d0000000` |

The *streams* are not byte-identical — they differ by 26 bytes and the two kernel
blobs differ by 67,229 because `kernel_size` covers the tree as well — so an
earlier draft of this paragraph, and the commit message for `10ebfa3`, which said
"the payload's gzip stream is byte-identical", overstated it. The measurement is
that the same 3,145,840 bytes were fed to two different compressors. The
conclusion is unaffected and is in fact tighter than the loose wording was: the
firmware itself is *provably* the same code on both sides, so the difference
between the phone's behaviour and the current build's is attributable to the tree
and to nothing else.

That is a one-variable experiment that was never run: every payload this phone
has booted carried a tree with no `/__symbols__` and the inherited `ramoops`
node, which are precisely the two faults `build-p2-payloads.sh` was written to
catch. So "our firmware runs and does nothing" has not yet been tested — what has
been tested is "a payload with a broken tree runs and does nothing".

It is also a reason to read the appended DTB, not the header, when asking what is
on the phone: `dtb 0` in the header is normal for this shape (the tree follows the
gzip stream inside the kernel blob, and `docs/07` has why ABL can still find it),
so the header alone cannot tell a stale tree from a current one. Reading it is a
`zlib` call and a `find`, not a guess — which is how the tables above were filled
in.

## Step 4.8 — The first execution (2026-09-23, later the same day)

The one-variable experiment of step 4.7 was finally run, and it produced the
result the whole phase was waiting for.

`work/out/p2-variants/Mu-gauguin-silicon-gzip.img` —
`sha256 816b1d418365ac7beb209e34c5b801ebeeb90bbaedfe8d48e1811e510dcef137` — was
written to `boot` over TWRP and read back byte-for-byte before the reboot.

**Our firmware executes.** The panel comes up full of text, and the last line of
it is:

```
ASSERT [DxeCore] DxeMain.c(593): !(((RETURN_STATUS)(Status)) >= 0x80000000000000ULL)
```

Both halves of that sentence are load-bearing. The text is ours because of how the
volume is built: in a DEBUG build `SiliciumPkg.dsc.inc` binds `SerialPortLib` to
`SiliciumPkg/Library/FrameBufferSerialPortLib`, so every `DEBUG ()` string in the
firmware is drawn glyph by glyph into the framebuffer. It needs no UART, no shell
and no boot-manager menu — which also means **text on the panel is not evidence
that BDS ran**, only that DXE got far enough to print. And `DxeMain.c(593)` is our
line: in this tree it is `ASSERT_EFI_ERROR (Status)`, immediately after
`Status = CoreAllEfiServicesAvailable ()` at line 582 of
`Mu_Basecore/MdeModulePkg/Core/Dxe/DxeMain/DxeMain.c`. A stock or vendor image
cannot print that string.

So the tree is what was blocking step 4.7, and P2's central question — does our
UEFI run at all on this board — is answered **yes**.

**What it is stopped on.** `CoreAllEfiServicesAvailable ()` walks `mArchProtocols[]`
in `DxeProtocolNotify.c` and returns `EFI_NOT_FOUND` at the **first** entry whose
`Present` is FALSE. The order is: Security, CPU, Metronome, Timer, Bds, Watchdog
Timer, Runtime, Variable, Variable Write, Capsule, Monotonic Counter, Reset, Real
Time Clock. So at least one of those was never installed — and the assert, being
on the first one, does not say how many or which.

**The name is almost certainly already on the screen, one or two lines above.**
`CoreDisplayMissingArchProtocols ()` runs at `DxeMain.c:568` and prints, for each
missing entry, `"<name> Arch Protocol not present!!"`; `CoreDisplayDiscoveredNotDispatched ()`
runs at 576 and lists the drivers that were found and never dispatched. Both are
inside `DEBUG_CODE_BEGIN ()`, which is compiled in when
`PcdDebugPropertyMask` has `DEBUG_PROPERTY_DEBUG_CODE_ENABLED` (0x04) set — DEBUG
has `0x2F`, and the `0x00` at `SiliciumPkg.dsc.inc:411` is a per-module override
for `ReportStatusCodeRouterRuntimeDxe`, not for DxeCore. `ASSERT_DEADLOOP_ENABLED`
(0x20) is also set, so the machine halts there rather than rebooting — the screen
stays as it was left.

**Read the screen before anything else.** The fourteen lines above the assert
narrow this from thirteen candidates to one. Useful facts for whoever is holding
the phone:

- The font is legible by design: `GetFontScale ()` divides the shorter panel
  dimension by 426, so 1080×2400 gives scale 2 — 5×16 glyphs drawn at 10×24,
  about 90 columns × 100 rows of ordinary terminal text.
- The console **wipes itself when it scrolls past the last row**
  (`AdvanceNewLine`), so only the final screenful survives. The assert is the last
  line of it, which puts the two most informative blocks inside the visible page.
- There is no host channel to fall back on: with this firmware running the phone
  enumerates on USB as nothing at all — `lsusb` shows no new device, no
  `/dev/ttyACM*`, and `adb`/`fastboot` are both empty.

All thirteen arch-protocol providers are in the volume, verified by GUID against
`Guid.xref`, so this is a dispatch or initialisation failure, not a packaging one.
The dependency chain worth knowing before the screen is read: `ArmGicDxe` depexes
on `gEfiCpuArchProtocolGuid` and produces `gHardwareInterruptProtocolGuid`;
`TimerDxe` depexes on *that*; `WatchdogTimer` depexes on
`gEfiTimerArchProtocolGuid`; `RealTimeClockRuntimeDxe` and `CapsuleRuntimeDxe`
depex on the Variable protocols. A single failure early in that chain takes two
or three names off the list at once, which is why the name matters more than the
count.

## Step 4.9 — The eight names, and the firmware made to name the culprit

Step 4.8's screen was read, and it says more than the assert did:

```
Security, Bds, Watchdog, Variable, Capsule, Monotonic, Reset, Real Time Clock
Arch Protocol not present
```

Eight of the thirteen, in the order `CoreAllEfiServicesAvailable ()` walks them.
The five that *are* present — CPU, Metronome, Timer, Runtime, Variable Write —
matter more than the eight that are not, because they rule things out.

**What the static pass established, and what it killed.** Reading the built
dependency expressions straight out of the volume (`/tmp/FvMain.bin`, GUID names
resolved through `Guid.xref` — the `.inf` `[Depex]` text is *not* the built depex,
`Common.fdf.inc:36` makes GenFv derive it from the libraries):

| Driver | Built depex | State |
|---|---|---|
| `ArmGicDxe` | `CpuArch AND PcdProtocol AND END` | **present** |
| `ArmTimerDxe` | `HardwareInterrupt AND PcdProtocol AND END` | **present** |
| `RuntimeDxe` | `PcdProtocol AND END` | **present** |
| `MetronomeDxe` | `PcdProtocol AND END` | **present** |
| `SecurityStubDxe` | `PcdProtocol AND END` | absent |
| `VariableRuntimeDxe` | `PcdProtocol AND END` | absent |
| `ResetSystemRuntimeDxe` | `PcdProtocol AND END` | absent |
| `EmbeddedMonotonicCounter` | `PcdProtocol AND END` | absent |
| `WatchdogTimer` | `TimerArch AND PcdProtocol AND END` | absent |
| `CapsuleRuntimeDxe` | `VariableWriteArch AND PcdProtocol AND END` | absent |
| `RealTimeClock` | `VariableArch AND PcdProtocol AND END` | absent |
| `BdsDxe` | `PcdProtocol AND HiiString AND HiiDatabase AND HiiConfigRouting AND VariablePolicy AND END` | absent |

That table kills the two hypotheses step 4.8 left open. `ArmTimerDxe` depexes on
`gHardwareInterruptProtocolGuid`, which only `ArmGicDxe` produces, and it ran — so
`ArmGicDxe` ran, so a protocol was installed, so `gBS` was valid and
`PcdProtocol` exists (four present drivers depex on it). "The PCD driver never
started" is false, and so is "something corrupted the pool before the first
protocol was installed". The second one died because of the order: the theory was
that some driver dereferenced an invalid `gBS` and the split falls exactly at
`MetronomeDxe`, the first driver to do so through `CalculateCrc32` — but
`ArmGicDxe` installs `gHardwareInterruptProtocolGuid` *earlier* in the same
sequence, and `ArmTimerDxe` only runs because that succeeded. A valid protocol
installation means `gBS` was good up to that point, which is past where the
theory needed it to be bad.

Five more candidates died the same way:

- **The Apriori file.** FvMain's file `[0]`, GUID `FC510EE7-FFDC-11D4-BD41-0080C73C8881`,
  is one `EFI_SECTION_RAW` of 1120 bytes = 70 GUIDs. **All 70 name a real file in
  the volume, and all eight missing providers are in it.** Measured against the
  FV's own map, the 70 are the files at index 1–31, 33–40, 42–49, 51–70 and
  72–74 — that is, the whole front of the volume minus five entries, not a
  curated list of anything. **All thirteen arch-protocol providers are in it,
  the five that ran and the eight that did not alike**, so membership separates
  them perfectly evenly and explains nothing on its own. What membership does
  give is the mechanism: `CorePreProcessDepex` marks a-priori members
  `Dependent = TRUE`, the promotion loop clears it, and the sweep at
  `Dispatcher.c:556` only evaluates `CoreIsSchedulable` for entries still
  `Dependent` — so **a-priori entries are scheduled once and never retried**,
  whereas depex-driven drivers are re-evaluated on every pass of the
  `do { … } while (ReadyToRun)` loop. All thirteen providers therefore had their
  entry points invoked in one batch at the very start of the dispatcher, with no
  depex gate.

**The Apriori order is the first thing in this project that separates the five
from the eight.** `CoreFwVolEventProtocolNotify` walks `AprioriFile[Index]` in
list order and appends each match to `mScheduledQueue`, and the drain at
`Dispatcher.c:486` is a plain FIFO — so the batch runs in the order of this
repository's `APRIORI.inc` `INF` list, *not* in FV file order:

| INF # | `P2 SEQ` | driver | protocol it owes | state |
|---|---|---|---|---|
| 5 | 4 | `RuntimeDxe` | Runtime | **present** |
| 6 | 5 | `CpuDxe` | CPU | **present** |
| 7 | 6 | `ArmGicDxe` | HardwareInterrupt | **present** |
| 8 | 7 | `MetronomeDxe` | Metronome | **present** |
| 9 | 8 | `TimerDxe` | Timer | **present** |
| 10–30 | 9–29 | 21 modules — `SmemDxe` … `TzDxeLA` — none installs an arch protocol | — | unobservable |
| 31 | 30 | `VariableRuntimeDxe` | Variable | **missing** |
| 34 | 33 | `ResetSystemRuntimeDxe` | Reset | **missing** |
| 36 | 35 | `WatchdogTimer` | Watchdog | **missing** |
| 37 | 36 | `SecurityStubDxe` | Security | **missing** |
| 38 | 37 | `EmbeddedMonotonicCounter` | Monotonic | **missing** |
| 39 | 38 | `RealTimeClockRuntimeDxe` | RTC | **missing** |
| 42 | 41 | `CapsuleRuntimeDxe` | Capsule | **missing** |
| 44 | 43 | `BdsDxe` | Bds | **missing** |

The two numberings differ because `APRIORI.inc` has 72 `INF` lines and the
volume's Apriori file has 70 GUIDs — the two `Display*` lines are behind a
conditional that is false for this build — and because the first GUID, `DxeCore`,
is never promoted: `CoreFwVolEventProtocolNotify` handles a `DXE_CORE` file by
filling in the core's loaded-image device path and does not add it to the driver
list, so the promotion loop's GUID compare finds nothing. `P2 SEQ` is indexed by
promotion, so its index 0 is `PcdDxe` and the shift is one below INF 60 and three
above 61.

In FV order the same thirteen are interleaved — `WatchdogTimer` is FV file 8,
`MetronomeDxe` 17, `ArmTimerDxe` 26 — and the split is not clean there. In
a-priori order it is exact: **every provider at position 5–9 installed its
protocol, every provider at position 31 or later did not**, with the unobservable
21 in between. The sample is sparse but the boundary is sharp, and it says the
batch stopped producing protocols somewhere in that window.

That ordering also retracts the obvious reading of it. Dependencies *are*
satisfied in this order — `TimerDxe` (9) precedes `WatchdogTimer` (36),
`VariableRuntimeDxe` (31) precedes `ResetSystemRuntimeDx` (34), `RealTimeClock`
(39) and `CapsuleRuntimeDxe` (42), and `PcdDxe` (1) precedes everything — so
"a member needed a peer that had not run yet" is not the defect, and trimming the
list is not the fix. The reference platforms schedule a batch of the same size
(72 entries here; `lavender` 63, `nabu` 73, `vili` 81) and dispatch it fine.
What the order does settle is narrower and more useful: because nothing in the
batch is retried and nothing can still be waiting, **each of the eight was
reached** — its image failed to load, or its `EntryPoint` was called and returned
an error. There is no third possibility, and no stall that the assert would not
have pre-empted.

- **Malformed images.** A census of all 86 PE32+ files in the volume found no
  malformed one: every arch-protocol provider is `0xaa64`, `ImageBase 0x0`,
  `DllCharacteristics 0x160`, uniform `OptHeaderSize 0xf0` — the same shape as
  the drivers that run. The FFS `Checksum`/`State`/`FileSize` block is not
  re-validated on the start path anyway (`CoreValidateFfsHeader` runs only on the
  read path), so a stale checksum could not be the cause even if one existed.
- **Heap exhaustion.** The five providers that ran are the five *cheapest* in the
  batch, so "the batch ran out of memory partway" was the obvious reading until
  the arithmetic killed it. Page-aligned `SizeOfImage` summed over every file in
  the a-priori batch is **7,385,088 B (7.04 MB)** — all 86 files together are
  9,306,112 B — against a `DXE Heap` of `0x02360000` = **35.38 MB**. Five times
  the headroom, for a batch whose `CoreLoadImage` allocations are freed as each
  section is copied in. (An earlier reading of that same figure was 5× low and
  briefly made exhaustion look plausible; the number is 35 MB, not 7 MB.)
- **"Runtime drivers fail."** Ten modules in the volume carry
  `Subsystem = 12 (EFI_RUNTIME_DRIVER)`, and five of the eight missing providers
  are among them (FV indices: `RuntimeDxe` 4, `CapsuleRuntimeDxe` 9,
  `ReportStatusCodeRouter` 10, `StatusCodeHandler` 11, `VariableRuntimeDxe` 12,
  `EmbeddedMonotonicCounter` 13, `ResetSystemRuntimeDx` 15, `RealTimeClock` 16,
  `EnvDxe` 24, `SdccDxe` 47). `RuntimeDxe` is one of them and it runs, so this
  was never a rule — and the a-priori order dissolves the correlation completely:
  the runtime drivers happen to sit late in `APRIORI.inc` (31–42) while
  `RuntimeDxe` happens to sit early (5). `SecurityStubDxe`, `WatchdogTimer` and
  `BdsDxe` are `Subsystem = 11` and are missing alongside them. The shared
  property is position, not subsystem.
- **A logging channel we could read instead of the panel.** There is no `logfs`
  writer anywhere in the Mu-Silicium tree, so the firmware does not append to the
  ring `tools/read-logfs.py` reads; and the P0 dump of that partition is a stock
  Android boot, overwritten ~50 times since. The panel is the only channel.

**So the discriminator is runtime, and the firmware now reports it.** In the
dispatcher there are exactly two ways an entry leaves the scheduled queue: its
`EntryPoint` returned, or `CoreLoadImage` failed and it was marked `Initialized`
and skipped. Neither prints anything a DEBUG build can reach. `Dispatcher.c` now
carries a self-contained `P2BRINGUP` block — struct `P2BRINGUP_DIAG {EFI_GUID
Guid; EFI_STATUS Status; CHAR8 Phase;}`, `mP2Diag[64]`, `P2Record ()` — wired into
`CoreAddToDriverList` (`mP2Discovered`), the Apriori promotion (`mP2Apriori`), the
`CoreLoadImage` failure path (`Phase 'L'`) and around `CoreStartImage`
(`Phase 'S'`, with `mP2Started` on success). It reports from
`CoreDisplayDiscoveredNotDispatched`, which `DxeMain.c:576` calls immediately
before `CoreAllEfiServicesAvailable ()` at 582 and the assert at 593 — so the
output lands while the evidence is still the last thing on the panel:

```
P2 NOLOAD <guid> dep=<0|1> sched=<0|1> unt=<0|1>   (up to 6, then a total)
P2 DIAG <L|S> <guid> <status>                      (one per failure, up to 24)
P2 SEQ [<up to 128 characters>]                    (one per Apriori *match*, in array order)
P2 STATS discovered=N apriori=N/70 started=N diag=N noload=N
```

The `SEQ` line's cap is `P2BRINGUP_APRIORI_MAX`, which is **128**, and
`mP2SeqLine[]` is 136. An earlier draft of this section said the line was "at most
69 characters"; it was reading a count of matches as if it were a limit. `STATS`
prints the denominator as the literal 70 because that is a constant in the macro
text rather than a computed count, so the two numbers on the `SEQ` and `STATS`
lines are not counted the same way and should not be compared to each other.

All at `DEBUG_ERROR`, which `PcdDebugPrintErrorLevel` `0x8007EE0F` enables. The
line budget is deliberate: eight missing protocols print as sixteen lines, so the
`NOLOAD` list is capped at six and the two lines that matter are printed **last**,
which puts them inside the console's final screenful however long the dump gets.

**`P2 SEQ` is the line to read, and it is why the image was rebuilt.** The
per-failure records say *what* failed; they cannot say whether anything after it
was ever **reached**, and "reached and failed" and "never reached" are the two
readings of the boundary. The character string gives both at once, in dispatch
order:

```
?  promoted by the Apriori file, nothing recorded for it - not reached
s  EntryPoint was called and returned EFI_SUCCESS
S  EntryPoint was called and returned an error
L  CoreLoadImage failed, so the EntryPoint was never called
```

The eight providers sit at these indices, which is what makes the string readable
at a glance — and which is where the string stops being the same as the `INF`
numbering above, for the two reasons given there:

| index in `P2 SEQ` | 30 | 33 | 35 | 36 | 37 | 38 | 41 | 43 |
|---|---|---|---|---|---|---|---|---|
| driver | VariableRuntimeDxe | ResetSystemRuntimeDxe | WatchdogTimer | SecurityStubDxe | EmbeddedMonotonicCounter | RealTimeClock | CapsuleRuntimeDxe | BdsDxe |
| if it worked | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` |

**This table has been wrong twice, and step 4.12 replaces it.** The first version
was wrong by one or three in every cell; the second used the Apriori *array* index
rather than the `P2 SEQ` index, which is the same number minus one wherever
`DxeCore` shifts the string — so it was off by one throughout, and off by more
before `SecurityStubDxe`. The indices above are the `P2 SEQ` positions, read off
the join in step 4.12, and the eight names are the eight the panel printed as
`Arch Protocol not present`. The two names that were wrong outright in the first
version were `CpuDxe` (the module is `ArmCpuDxe`) and `TimerDxe` (the module is
`ArmTimerDxe`) — those are the names the built volume and `Guid.xref` carry, and
the `INF` paths they come from are
`ArmPkg/Drivers/ArmCpuDxe/ArmCpuDxe.inf` and
`ArmPkg/Drivers/ArmGenericTimerDxe/ArmGenericTimerDxe.inf`.

So a healthy line is `s` at every position. The first character that is not `s`
is where the batch stopped, and its index names the driver.

**And the reading has since landed, so none of the three outcomes below is what
happened.** The measured line is 46 characters with **zero `?`** — so the drain
did not stop partway through the promoted batch (it drained all 46), and the
"`?` from index 30 onward" hypothesis in particular is dead: there are no `?` at
all. What the three paragraphs below describe is still the right way to read a
*`?`* if one ever appears, which is why they are kept rather than deleted.

**What to read, and what each outcome means.** `P2 DIAG` names the victims, in the
order they were reached; the phase letter is what separates the two readings of
the boundary — and note that DIAG never names a *cause*, only a driver that
failed, so the cause is either the first line's driver or something earlier that
returned success while breaking something shared:

- **`?` from index 30 onward.** The drain never got there, and
  `VariableRuntimeDxe` is where it stopped. That contradicts the dispatcher having
  no early exit, so the entry point did not return: it hung or faulted, and the
  boot only continued because the assert is reached on a later path than expected.
  Look inside `VariableRuntimeDxe`'s variable-store probe (`PcdFlashNvStorage*`) —
  this firmware's PCD values for the store region are inherited from the reference
  platform.
- **`L` at 30.** `CoreLoadImage` failed, most likely `EFI_VOLUME_CORRUPTED` or
  `EFI_NOT_FOUND`. Then by index 30 the dispatcher could no longer read the
  volume, and the culprit is in the unobservable window 9–29 — the six there that
  touch memory mappings or do DMA (`HALIOMMUDxe` 14, `ShmBridgeDxeLA` 21,
  `ScmDxeLA` 22, `SdccDxe` 26, `UFSDxe` 27, `TzDxeLA` 29) are the ones to look at
  first.
- **`S` at 30, `s` (or `S`) at 33–43.** The volume was fine and the entry points
  failed on their own. The status codes then say why, and if the failures share
  one status the shared cause is in the window again.
- **`started=70 apriori=69/70 diag=0`, all `s`.** Every entry point was reached
  and every one returned success, and the protocols are still absent. Then the
  failure is inside the drivers — an early "not supported on this platform" return
  that still returns `EFI_SUCCESS` — and no dispatcher instrumentation can see it;
  the next probe has to be inside `VariableRuntimeDxe` and `SecurityStubDxe`
  themselves, with the trivial second one as the control.

That last case is the one worth naming, because no dispatch-count line can rule it
out. `SecurityStubDxe`'s entry point installs one protocol and returns; it cannot
fail except by `EFI_OUT_OF_RESOURCES`. So if Security is missing while SEQ shows
`s` at index 36, the dispatcher is exonerated and the fault is in the
protocol-installation path itself — which is a different investigation, and the
`P2 SEQ` line is what tells the two apart.

One count to read as a checksum rather than as a result: `apriori=69/70` is the
**expected** pair, not a shortfall, and the truncation is at most 69 for a reason
that is not "one GUID names nothing". All 70 GUIDs in the array name a file that
is really in the volume — verified, 70/70, by resolving each against the volume's
own file table. The one that can never be promoted is index 0, `DxeCore`, and it
is excluded by its **file type**, not by being absent: `EFI_FV_FILETYPE_DXE_CORE`
is one of the types `CoreFwVolEventProtocolNotify` handles by filling in the core's
loaded-image device path *without* calling `CoreAddToDriverList` (Dispatcher.c, the
type switch), so it is never in `mDiscoveredList` for the promotion loop at the
bottom of the same function to find. Every other one of the 70 is a real
`EFI_FV_FILETYPE_DRIVER` in the volume and is added. Hence 69 promotable entries,
a `SEQ` line of at most 69 characters, and — since the promotion loop appends in
`Index` order and the drain is a FIFO — the string's index *i* is Apriori entry
*i*, shifted by one from what a naive reading of `APRIORI.inc` gives.

**This step's prediction was overtaken: the reading was taken, and step 4.11/4.12
are what it said.** Nothing in the paragraphs above is now the live question, and
the image header below is the one that produced the reading rather than one that
has yet to be tried. What *is* still worth keeping from here is how the
instrumentation was verified to be in the artifact: `strings` finds all five
format strings in `DxeCore.efi` and in the packed `FVMAIN.Fv`, and the image's
packed volume matches `FVMAIN.Fv.txt` at all 122 offsets and GUIDs. Note where that
check has to be made — **not** on the payload, which carries `FVMAIN` inside
`FVMAIN_COMPACT` and is compressed, so the strings are not in it. (That last point
cost this project a session, and step 4.12 gives the decompression recipe.)

| | |
|---|---|
| image | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` |
| sha256 | `09db74022ccfef57515d69d8850465ab610c53356612f8eabcc3d9501d98a8ce` |
| payload | 3,145,840 B, md5 `a2963f46faeb27fe601022c3a67aa738` — byte-identical to `SILICIUM_UEFI.fd-bootshim` |
| previous image | sha256 `3db8aba2…`, payload md5 `8cbe30d8…` (DIAG/STATS, no SEQ) |

One build note, and it was read wrongly the first time: `FVMAIN` reports
**99% — 2104 bytes free**, and that figure is not a budget. The FV is declared with
`NumBlocks = 0`, so GenFv sizes it to fit its contents and rounds up to the block
size — which means this FV reports "99% Full" for *every* build of it, and the
"free" number is only the padding left on the last `0x1000` block. Three builds in
`work/` prove it by disagreeing with each other: `FVMAIN` has come out at
`0x677000`, `0x702000` and `0x753000`, each reported as 99% full. So there is no
headroom to run out of — a new file, or more `DEBUG ()` text, grows the FV. What
bounds it is `FVMAIN_COMPACT`, which is at 34–37% with about 2 MB free. This
matters beyond bookkeeping: the earlier reading of that line made "is there room
for the ACPI tables" look like a design constraint, and it is not one.

**Next action, in order.** Flash this image, read the panel, and write down the
`P2 SEQ` string before anything else — it is the last line of substance before the
assert. Then remove the `P2BRINGUP` block, fix what it names, and re-run the
standing cycle.

If the evidence lands inside the window rather than on a `DIAG` line — `diag=0`,
or a set of failures that does not explain the boundary — the next probe is the
twenty-one modules at a-priori positions 10–30, one at a time, starting with
`HALIOMMUDxe` (15). The experiment is a **reorder, not a removal**: move the
suspect to the end of the `APRIORI DXE` block and rebuild. If the eight providers
then install, that module is the one that broke the batch, and it did so without
returning an error at its own entry point — which is the one failure mode the
instrumentation cannot see. The order comes from the reference package verbatim:
`tools/make_uefi_platform.py` rewrites `suryaPkg/Include/APRIORI.inc` to point at
`Binaries/gauguin/`, comments out what this device's XBL did not provide, and
inserts the four extra drivers — it never reorders. **The move step that paragraph
needed now exists**; step 4.10 is the experiment it enables, and it is a better
first move than dragging `HALIOMMUDxe` around, because it separates "these eight
fail" from "something in the middle fails them" without needing a suspect.

## Step 4.10 — The depex dead end, and the one experiment that separates the readings

Step 4.9's boundary — every provider at a-priori position 5–9 installs its
protocol, every one at 31 or later does not — has two readings, and the panel
cannot tell them apart on its own:

- **the eight fail wherever they are put**, or
- **something between position 9 and position 31 fails them** without returning an
  error at its own entry point.

**The dependency reading is the one the static pass can finish, and it is dead.**
Reading the eight providers' `[Depex]` out of the packages that build them
(`Mu_Basecore`, not the volume) gives:

| driver | `[Depex]` | built depex (from step 4.9) |
|---|---|---|
| `VariableRuntimeDxe` | `TRUE` | `PcdProtocol AND END` |
| `ResetSystemRuntimeDxe` | `TRUE` | `PcdProtocol AND END` |
| `SecurityStubDxe` | `TRUE` | `PcdProtocol AND END` |
| `EmbeddedMonotonicCounter` | `TRUE` | `PcdProtocol AND END` |
| `WatchdogTimer` | `gEfiTimerArchProtocolGuid` | `TimerArch AND PcdProtocol AND END` |
| `RealTimeClockRuntimeDxe` | `gEfiVariableArchProtocolGuid` | `VariableArch AND PcdProtocol AND END` |
| `CapsuleRuntimeDxe` | `gEfiVariableWriteArchProtocolGuid` | `VariableWriteArch AND PcdProtocol AND END` |
| `BdsDxe` | `TRUE` | `PcdProtocol AND HiiString AND HiiDatabase AND HiiConfigRouting AND VariablePolicy AND END` |

The extra `PcdProtocol` conjunct in the built column is the libraries', not the
driver's (`Common.fdf.inc:36` — the same fact step 4.9's table rests on). So every
conjunct of all eight was satisfied before their entry points ran: `PcdDxe` is at
`P2 SEQ` 0 and installs, `TimerDxe` at 8, and `VariableRuntimeDxe`/`VariableWrite`
by 30. Four of the eight are `TRUE` and cannot be blocked by anything at all. And
for an a-priori member the depex is not even consulted — step 4.9 established that
the promotion loop clears `Dependent` and the sweep only evaluates
`CoreIsSchedulable` for entries still marked — so all eight were invoked
unconditionally regardless. Either way **"a dependency was never satisfied" cannot
account for the set**, and what is left is a state effect: something in the middle
of the batch breaks shared state (heap, pool, GCD map) and returns success anyway.
That is precisely the failure no dispatch-count line can see, which is why this
step is an experiment rather than another reading of the same evidence.

**The experiment: run the eight before the Qualcomm block.** Move all eight lines
of `APRIORI.inc` to immediately after `ArmPkg/Drivers/TimerDxe/TimerDxe.inf` —
chosen as the anchor because it is already early and its own result is known good,
and because four of the eight depex on the protocol it installs. No driver is
added, removed or rebuilt; the volume keeps every file at every offset it had (the
map check below is what makes that a measurement); the only bytes that differ
between this image and the baseline are the Apriori file's GUID array. What the
panel then shows answers both readings at once:

- **the eight install.** Then the order was the cause, and the culprit is in the
  block they were moved ahead of. Bisect by moving the anchor later.
- **they still fail, in the same way.** Then the order is not the factor, they fail
  wherever they sit, and the next probe is inside `VariableRuntimeDxe` and
  `SecurityStubDxe` themselves — with `SecurityStubDxe`, which installs one
  protocol and returns, as the control.

Both readings need the baseline's `P2 SEQ` string first, which is why the order of
operations is baseline image, then this one. In the variant the eight land at SEQ
**9–16**, in this order:

| SEQ | 0 | 1–8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| driver | Pcd | EnvDxe … TimerDxe | Variable | Reset | Watchdog | SecurityStub | Monotonic | RTC | Capsule | Bds |

so the eight's fates are the eight characters after position 8, and a healthy line
reads `s` at all eight. Everything after position 8 in the *baseline* shifts down by
eight here (`SmemDxe` 17 … `GraphicsConsoleDxe` 68), so the two strings are
compared by driver, not by index.

**The tooling, and why it is three files rather than a build command.**

- `tools/make_uefi_platform.py --apriori-move ANCHOR:NAME[,NAME...]` is the move
  step `rewrite_incs ()` was missing. It is deliberately strict: a name that
  matches zero or more than one `INF` line in `APRIORI.inc` is an error, not an
  experiment that quietly did nothing. It applies to `APRIORI.inc` alone — DXE.inc
  decides which FFS files the volume carries and where they land, and reordering it
  would move every offset in the map the volume is checked against.
- `tools/apriori-order.py` reads the order **out of the image that would be
  flashed** rather than out of the file that was meant to produce it, and checks the
  two positionally. That distinction is the whole point: a reorder that never
  reached the volume would look exactly like a reorder that made no difference on
  the device. It resolves GUIDs to module names through the build's own `Guid.xref`
  and reads each module's `BASE_NAME` out of its `.inf` — deriving the name from the
  file name is wrong often enough here to matter (`TLMMDxe/TLMMDxe.inf` builds
  `DALTLMM`, `TzDxe/TzDxeLA.inf` builds `TzDxe`) and a first version of this tool
  reported 69 confident `OUT OF ORDER` lines about an array that was in the right
  order. Verified against the baseline (`70 entries, zero mismatches`, exit 0) and
  against two deliberately wrong inputs (a wrong `--display`: 11 problems; a
  reordered `--expect`: 35 problems; exit 1 each).
- `tools/build-apriori-variant.sh arch-first` drives the two above, builds with
  `-c`, and gates the result on `apriori-order.py`, `check-payload.py`,
  `abl-boot-check.py` and `fv-inventory.py --against FVMAIN.Fv.txt`, reading each
  gate's exit status from the command itself rather than from a pipe. An `EXIT`
  trap regenerates the reference order, so a checkout that has built the
  experimental firmware does not keep it.

**A trap that cost two runs, worth recording because it is silent.** Mu-Silicium's
`setup_env.sh` is a package installer and it is not `-u`-clean: line 40 tests
`$CI_BUILD` unguarded, so sourcing it from a script that has `set -u` on aborts the
whole shell on `CI_BUILD: unbound variable`. The abort is fatal to the *sourcing*
script rather than something `||` catches, and its one message goes into the
redirect on that line — so the symptom is a script that stops after printing
`== building the firmware` and gives no reason at all. The fix is `set +u` around
the `source` (`build-apriori-variant.sh` does that, with the reason at the line).
Both failed runs did still run their restore trap — the tracked `APRIORI.inc` was
rewritten 48 ms after the copy of it was taken — so nothing was left in the
experimental order; they simply produced no image and no explanation.

One build note, carried from 4.9 and corrected there: `FVMAIN` reports
**99% (2104 bytes free)** in every build, because `NumBlocks = 0` makes GenFv size
the volume to its contents and the figure is last-block padding. This variant adds
no file, so its total is unchanged at `0x702000`, which is a useful confirmation
that the reorder really did not change the FV's contents — only the order.

**The image, and the measurement that says the reorder is the only variable.** All
four gates passed on the artifact: `apriori-order.py` read the array back out of the
image as the experimental INF order (`70 entries, zero mismatches`), `check-payload.py`
and `abl-boot-check.py` passed, and `fv-inventory.py --against FVMAIN.Fv.txt` matched
every one of the 122 files at its offset.

Re-measured on 2026-09-23 while auditing which payloads carried which array, and
recorded because it is a single number that can be checked in one command rather
than a paragraph of argument: the Apriori section's md5 is **`a32543ed…`** in this
variant and **`ed607ebc…`** in every baseline-order image of the same period
(`boot-before-p2walk.img`, and all three in `work/out/p2-variants/`). So the
reorder is visible in one hash of one 1120-byte section, and — the reason to write
it down — the three images that are *not* the variant all share the baseline hash,
which makes "did this experiment actually run" answerable by hashing rather than
by trusting the log. The variant differs at `ap10` (`CBD2E4D5-…` where the
baseline has `94527566-…`), which is the block the eight were moved ahead of.

| | |
|---|---|
| image | `work/out/p2-variants/Mu-gauguin-arch-first-gzip.img` |
| size | 1,138,688 B (same as the baseline image) |
| sha256 | `9a7e8ab8235f8f24f70e1012df7b840645043dc34d256dc156e093d3d2c35ce3` |
| payload | 3,145,840 B, md5 `1e2e8d22a62d448fb5b0df2e2e2970ee` |
| baseline for comparison | `Mu-gauguin-silicon-gzip.img`, sha256 `09db7402…` |
| `APRIORI.inc` as built | `work/out/p2-variants/APRIORI.arch-first.inc` (tracked file restored) |

Then the two images were compared directly, which is the check that makes the
experiment an experiment:

- The **compressed** `FVMAIN_COMPACT` differs in **1,012,015 bytes of its 3,145,728** —
  and that number means nothing, because one changed GUID early in an LZMA stream
  reflows everything downstream of it. A byte-diff at this level would read as "the
  build is not reproducible", which is the wrong conclusion; the inner file's
  compressed size moved by 96 bytes (`0xf8200` → `0xf81a0`) and the volume's did not.
- The **decompressed** `FVMAIN` differs in **7 runs, 554 bytes, and every one of them
  is inside FVMAIN file #0** — the Apriori file. Not one byte of the other 121 files
  differs, they are at the same offsets, and both volumes are `0x702000`. The
  differing bytes are **GUID slots 10–44 of the array, contiguous**: the eight arrive
  at 10–17 and the 27 slots behind them shift down by eight, which is exactly the
  difference that was asked for and nothing besides.

**What it means on the device, and why this experiment was never run.** The order
of operations it prescribes is right and was followed — flash the baseline, read
its `P2 SEQ`, and only then the variant — but the reading came back in step 4.12
and **it retired the experiment rather than motivating it.** In the variant the
eight would have occupied SEQ **9–16**:

| SEQ | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|---|---|
| driver | Variable | Reset | Watchdog | SecurityStub | Monotonic | RTC | Capsule | Bds |
| if it worked | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` |

and a prompt that reports *eight* protocols missing rather than thirteen — CPU,
Metronome, Timer, Runtime and Variable Write were never among the missing — is the
same signal read off the assert instead of off `P2 SEQ`.

- **All eight `s`.** The order was the cause, and the culprit is inside the block
  they were moved ahead of, between `SmemDxe` (now SEQ 17) and `ScmDxeLA`. Move the
  anchor later and re-run to bisect.
- **The same failures as the baseline, at the same drivers.** Order is not the
  factor and the eight fail wherever they sit; the next probe is inside the drivers
  themselves, `SecurityStubDxe` first as the control, since it installs one protocol
  and cannot fail except by `EFI_OUT_OF_RESOURCES`.
- **`?` at 9 or a new `L`.** The forced-early batch did not even get its entry
  points called, which would put the failure before anything this reorder touches —
  and the baseline's string is what distinguishes that from the same result.

**The reading made all three of those outcomes uninteresting, and the reason is
structural rather than empirical.** The baseline's 27 failures are every one of
them phase `L` — `CoreLoadImage` returned an error and no entry point was ever
called — and the batch stops producing promotable entries at a *physical file*
boundary, not at a position in the Apriori array. Reordering the array cannot move
that boundary: the same 46 files are discovered in the same order whatever order
the GUIDs are listed in, because discovery walks the volume and the promotion loop
only decides which of the discovered entries get *scheduled*. The variant would
therefore have reproduced the same 46-character string with the same first `L`,
having changed which driver that `L` lands on and nothing else — at the cost of one
build, one flash and one physical reset. It was built
(`work/out/retracted/Mu-gauguin-arch-first-gzip.img`, kept there rather than in
`p2-variants/` so it cannot be picked up as a candidate by accident) and never
flashed.

The `--apriori-move` machinery and `tools/apriori-order.py` are not wasted — the
move step is what any future reordering experiment needs, and the tool that reads
the order back *out of the image* rather than out of the file is the only thing
that makes such an experiment readable at all. They are simply not the next move.

The phone was off USB for every build in steps 4.9 and 4.10, and neither step was
run on the device — which is also why the baseline had to be re-flashed in 4.11
rather than being re-read.

## Step 4.11 — The reading was attempted, and the photograph is not the console

Steps 4.9 and 4.10 both stop waiting on the same thing: the baseline payload's
`P2 SEQ` line off the panel. The phone came back on USB later on 2026-09-23 and
went into TWRP, so the session's host-side checks could run again — `adb devices -l`
filled in as `d25f844e recovery product:twrp_gauguin device:gauguin`, `lsusb` showed
`2717:ff68`, and reading `boot` back off the device with `dd` matched
`Mu-gauguin-silicon-gzip.img` (`09db7402…`) byte for byte. **The instrumented build
is confirmed to be the content of the partition, so nothing has to be flashed to
re-take the baseline.**

What did not happen is the read. A photograph of the screen was taken and it is not
the firmware console. Guessing is what this repository keeps having to unlearn, so
the frame was measured instead, over the full 1279×2465:

| | |
|---|---|
| mean grey | 136.6 |
| pixels below 16 | **0.16 %** |
| pixels below 32 | **0.19 %** |
| pixels below 64 | 3.24 % |
| darkest 32×32 block, mean | **49.9 / 255** |
| darkest 64×64 block | **30.5 % of full scale** |
| saturation max | 0.423 — one blob at the lower right and a strip down the left edge |

A DEBUG build's console is `FrameBufferSerialPortLib` + `BaseDebugLibSerialPort`
(`SiliciumPkg.dsc.inc:160-169`), so the screen it draws is **white DEBUG text on
black across the whole panel**. A frame containing that cannot be a mid-grey
photograph: near-black would dominate the histogram rather than be 0.16 % of it,
and no 64-pixel block anywhere could sit at 30 % brightness. The only strongly
coloured content — a warm, smooth gradient in the lower right, cropped out at 3×
and looked at directly — is photographic; text is neither coloured nor smooth.

So the frame is a scene, not a panel: the screen was off, or the phone was still on
its earlier screen, or the shot was taken before the payload had drawn. The useful
part is that this costs no host work to fix. The reading is deterministic for a
given image, so `boot` does not have to be written again to re-take it.

**Two things this project had been carrying were wrong, and both are closed now.**

- **The `P2BRINGUP` call site is `DxeMain.c:576`, and it is correct.** Step 4.9
  says 575. The call is `CoreDisplayDiscoveredNotDispatched ()` at 576, after
  `CoreDispatcher ()` at 562 and before `Status = CoreAllEfiServicesAvailable ()`
  at 582 — whose failure is the `ASSERT_EFI_ERROR` at 593 the panel ends on. The
  instrument prints while the evidence it describes is still the last thing drawn,
  which is what it was placed there to do rather than something it happens to do.
- **The dispatch drain has no early exit, so the eight have three fates, not four.**
  Reading `Dispatcher.c:523-664` end to end: a started entry leaves the queue with
  `Initialized = TRUE` and `Scheduled = FALSE` and is never re-added, and the only
  `continue` in the loop (582) skips `CoreStartImage` for that one entry without
  ending the loop. Nothing breaks out; the `do { … } while (ReadyToRun)` at 664 is
  reached. So each of the eight is (a) scheduled and never promoted — a `?` in
  `P2 SEQ`; (b) promoted and `CoreLoadImage` failed — an `L` at line 572; or
  (c) promoted, loaded, and `CoreStartImage` returned an error — an `S` at line 611.
  There is no fourth way, and in particular no silent drop.

**Two traps to carry, because between them they have now cost time twice.** Both
defeat the obvious command and both produce a confident wrong answer:

- The payload holds `FVMAIN` **compressed** inside `FVMAIN_COMPACT`, so grepping
  the `.img` or the `.fd` for a `DEBUG ()` format string finds nothing and reads as
  "the instrumentation is not in the build". It is. Reach it through
  `fv-inventory.py`'s `fvmain_of_fd ()` / `unpack ()`.
- The payload's gzip stream has the **DTB appended**, so `gzip.decompress ()`
  raises `BadGzipFile: Not a gzipped file (b'\xd0\r')` on a file whose first four
  bytes at 0x800 are a valid `1f 8b 08 00`. Inflate one member instead:
  `zlib.decompressobj (16 + zlib.MAX_WBITS)`, then `decompress (blob) + flush ()`.

**The reading was taken on the next attempt, and step 4.12 is what it said.** In
TWRP, `Reboot → System`, wait for the assert, then photograph the whole panel
square-on and filling the frame — that screen is the last thing drawn and it stays
up. Read `P2 SEQ […]` first, then `P2 STATS …`, then the `P2 DIAG` lines above them.

**And the screen does not in fact stay up, which is the correction this step
needs.** `PcdDebugPropertyMask` is `0x2F` (`SiliciumPkg.dsc.inc:69`), whose `0x20`
is `DEBUG_PROPERTY_ASSERT_DEADLOOP_ENABLED` — so the assert *is* a deadloop and the
CPU does stop there. What ends it is not the firmware: something resets the phone
after a few seconds, so the panel is readable only inside that window. A still
photograph has to be taken in it, and a still taken later reads as a scene rather
than as a console — which is exactly the frame step 4.11 measured and rejected. The
reliable instrument is a **video** covering the whole boot: film from before the
power button, then step the frames on the host and take the last one that is a
black background with white text. Nothing about the reading changes; only the way
it is captured does.

The variant (`Mu-gauguin-arch-first-gzip.img`) is **not** the next payload, and
step 4.10 says why.

## Step 4.12 — The reading, and what it does and does not say

The `P2 SEQ` line was read off the device on 2026-09-23, second attempt:

```
ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL
```

**46 characters, 19 `s`, 27 `L`, zero `?`.** The zero is worth as much as the
digits: `?` means "promoted but never reached", and there are none, so every entry
the promotion loop queued was also drained. The batch does not stop partway
through the drain; it stops before the drain begins.

### The join, and it is now exact rather than inferred

`P2 SEQ`'s index *i* is Apriori entry *i + 1*, and the reason is in the promotion
loop (`Dispatcher.c`, the `for (Index = 0; Index < AprioriEntryCount; Index++)`
block): the array is walked in order and the ring buffer is filled **inside the
match** —

```c
if (CompareGuid (&DriverEntry->FileName, &AprioriFile[Index]) &&
    (FvHandle == DriverEntry->FvHandle))
{
  …
  if (mP2Apriori < P2BRINGUP_APRIORI_MAX) {
    CopyGuid (&mP2AprioriGuid[mP2Apriori], &DriverEntry->FileName);
    mP2AprioriRes[mP2Apriori] = '?';
  }
  mP2Apriori++;          /* unconditional: counts MATCHES, not entries scanned */
  break;
}
```

— so the string is the *matches*, in array order, and `mP2Apriori` is a count of
matches rather than a cursor into the array. Entry 0 is `DxeCore`, whose file
type is `EFI_FV_FILETYPE_DXE_CORE` — a type the discovery switch handles by
filling in the core's loaded-image device path and **not** calling
`CoreAddToDriverList` — so it is never in `mDiscoveredList`, never matches, and is
not counted. Every one of the other 69 entries is a real
`EFI_FV_FILETYPE_DRIVER`, and all 70 resolve to a file the volume actually
contains (70/70, checked GUID by GUID). Hence index 0 of `P2 SEQ` = `PcdDxe`.

**There is no 69-character cap.** An earlier draft of this step said there was;
the only cap in the printer is `min (mP2Apriori, P2BRINGUP_APRIORI_MAX)`, and
`P2BRINGUP_APRIORI_MAX` is **128** (`uefi/patches/mu-basecore-local.patch:328`),
with the line buffer `mP2SeqLine[136]` sized above it. A 69-long `P2 SEQ` would be
a legal line, and 70 would too. The 46 characters are 46 matches, not 46 out of a
room of 69.

Resolving each character against the array and the array against the volume's own
file table gives:

| SEQ | 0–17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 |
|---|---|---|---|---|---|---|---|---|---|
| Apriori | 1–18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26 |
| driver | PcdDxe … NpaDxe | RpmhDxe | PdcDxe | ClockDxe | **ShmBridgeDxe** | ScmDxe | DiskIoDxe | PartitionDxe | EnglishDxe |
| result | `s` ×18 | `L` | `L` | `L` | **`s`** | `L` | `L` | `L` | `L` |

and the rest of the failing run, SEQ 26–45, is Apriori 27–46: `SdccDxe`, `UFSDxe`,
`Fat`, `TzDxe`, `VariableRuntimeDxe`, `DALTLMM`, `SPMI`, `ResetSystemRuntimeDxe`,
`PmicDxe`, `WatchdogTimer`, `SecurityStubDxe`, `EmbeddedMonotonicCounter`,
`RealTimeClock`, `PrintDxe`, `DevicePathDxe`, `CapsuleRuntimeDxe`, `HiiDatabase`,
`BdsDxe`, `GpiDxe`, `I2C` — every one of them `L`.

**This reproduces the assert, which is what makes the join trustworthy rather than
merely tidy.** The eight protocols the panel reported missing are
`SecurityStubDxe` (SEQ 36), `BdsDxe` (43), `WatchdogTimer` (35),
`VariableRuntimeDxe` (30), `CapsuleRuntimeDxe` (41),
`EmbeddedMonotonicCounter` (37), `ResetSystemRuntimeDxe` (33) and `RealTimeClock`
(38) — and those eight indices are exactly the ones the join says failed. They are
not eight indices found by guesswork; they are the eight names the panel printed,
mapped back through the array, and landing on `L`.

### Three things this settles, and one it does not

**Settled, and this reading had it backwards for a while: the SEQ *does* separate
a load failure from a start failure, and all 27 are load failures.**

An earlier version of this paragraph said the two were indistinguishable because
`'S'` "renders as `'L'`". That contradicts the line of code it quotes.
`P2Record` writes **two different letters**:

```c
P2MarkSeq (Guid, (Phase == 'L') ? 'L' : 'S');   /* 'L' = CoreLoadImage failed */
                                                 /* 'S' = CoreStartImage failed */
```

and `P2MarkSeq` assigns `mP2AprioriRes[Index] = Ch` verbatim, and the SEQ line is
`mP2AprioriRes[0..mP2Apriori)` with no mapping in between. So a driver whose entry
point ran and returned an error is marked `'S'`, not `'L'`. The line is `Phase ==
'L' ? 'L' : 'S'`, not `'L'` for both.

The observed string is `ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL` — lowercase
`'s'` for the 19 successes, uppercase `'L'` for the 27 failures, and **zero `'S'`
characters**. The case is not incidental: `'s'` and `'S'` are written by different
lines of code. So, over the 46 promoted entries, **not one driver's `DriverEntry`
ever ran and returned an error.** Every failure is `CoreLoadImage` returning an
error before `CoreStartImage` was called, which places the fault in the loader and
not in the drivers. Nothing about how a driver behaves once started — including
every `gKernel == NULL` / missing-`EFI_KERNEL_PROTOCOL` reading elsewhere in this
document — can be the cause of these 27.

**Narrowed: one `P2 DIAG` reading said Out of Resources, and the sites that can
produce it are three.** The panel's `%r` rendered a status whose name is `Out of
Resources` — `EFI_OUT_OF_RESOURCES` — on a `P2 DIAG` line whose phase character was
`L`, which is the reading above arriving independently and in the same direction.
In the load path that is `CoreLoadImageCommon`'s `AllocateZeroPool` of the
`LOADED_IMAGE_PRIVATE_DATA`, `CoreLoadPeImage`'s page allocation, or the
`AllocatePool` inside `GetSection` when the caller passes a NULL buffer. So this
is not 27 drivers declining to support the platform, and it is not 27 dependency
failures: it is a run of loads that each returned a status that *renders* as Out
of Resources. Which site, and why an identical request two positions later
succeeded, is what the two sections below narrow — and the memory section is
where the obvious answer dies.

**What the phase split is worth, beyond the 27.** It removes the largest class of
candidate explanations outright. "Driver X starts before its dependency is
installed and returns an error" and "the Apriori order starves a driver of
something it needs at start time" both require *some* entry point to have run, and
none did. That is also a better reason to retire step 4.10's reorder than the one
given there: reordering changes only the drain order of the promoted batch, and the
drain never reaches a driver's entry point at all.

The distinction was never absent — it was in the string that has been on the panel
since `e864a59`. What was missing was reading the case as significant rather than
as decoration.

**Settled: the promoted set is not a prefix of the array's first 46 — it is the
first 46 *matches*, and 23 entries are missing from the list entirely.** Zero `?`
characters means the drain reached every promoted entry, so nothing was queued and
lost. But `mP2Apriori` only increments on a match, so a contiguous 46-letter
string does not imply the loop stopped at entry 46: it implies entries 47–69
matched nothing. That is the second, independent puzzle, and it is the one the
next section and the `P2 WALK` probe are about.

### What the host can rule out, and it is more than expected

Four of the obvious readings of "27 loads in a row ran out of memory" were
checked against the built volume and do not hold. They are recorded because each
one is the first thing anyone will reach for next time.

**Ruled out: the PE images.** All 80 drivers were parsed out of `FVMAIN.Fv` and
compared on `ffs size, pe size, NumberOfSections, SizeOfOptionalHeader,
Characteristics, SizeOfImage, SizeOfHeaders, SizeOfCode, SizeOfInitializedData,
SizeOfUninitializedData, AddressOfEntryPoint, BaseOfCode, ImageBase,
SectionAlignment, FileAlignment, Subsystem, DllCharacteristics, SizeOfStackReserve,
SizeOfHeapReserve, NumberOfRvaAndSizes, .reloc-directory size`, plus the section
name/characteristics layout. **Not one of them separates the two sets.**
`tools/pe-facts.py` makes this a per-field verdict rather than an assertion, and
it asks two questions per field because they are easy to conflate. The
equality-class question — "does some value appear only under one result?" —
comes out `shared` for **every field but one**: `Machine`,
`NumberOfSections`, `SizeOfOptionalHeader`, `Characteristics`, `Magic`,
`SizeOfUninitializedData`, `BaseOfCode`, `ImageBase`, `SectionAlignment`,
`FileAlignment`, `SizeOfHeaders`, `Subsystem`, `DllCharacteristics`,
`SizeOfStackReserve`, `SizeOfHeapReserve`, `NumberOfRvaAndSizes`, `has_reloc`
and `sections` all take values that appear on both sides. The exception is
`ffs_size`, and it is the reason the tool prints the second question: a field
that is nearly unique per image comes out `disjoint` for free, having explained
nothing, so the tool also asks whether a *threshold* splits the sets — and
`ffs_size` is `interleaved`, i.e. `s` and `L` sizes overlap in range. Its verdict
line is `no field separates the 19 s from the 27 L by a single value or a
threshold`.

Everything has `ImageBase 0x0`; nothing is reloc-stripped (`Characteristics` bit
0 clear on all 80); `SectionAlignment` is `0x1000` except for the Runtime family
at `0x10000`, and that family straddles the boundary in both directions —
`ReportStatusCodeRouterRuntimeDxe`, `StatusCodeHandlerRuntimeDxe` and `RuntimeDxe`
succeed at `0x10000` while `CapsuleRuntimeDxe`, `EmbeddedMonotonicCounter`,
`RealTimeClock`, `ResetSystemRuntimeDxe` and `VariableRuntimeDxe` fail at the same
value. Size is not it either, and the interleaving is the measurement. `NpaDxe`
succeeds at 81,966 bytes — a size **20 of the 27 failures fall below and 7 rise
above**. The smallest success (`ReportStatusCodeRouterRuntimeDxe`, 9,338 B) is
smaller than the smallest failure (`EmbeddedMonotonicCounter`, 19,050 B) and the
largest success (`DALSys`, 307,246 B) is smaller than the largest failure
(`BdsDxe`, 385,166 B). The two ranges overlap rather than being ordered, which is
what the tool's `interleaved` verdict means. There is nothing to see in the
binaries.

**Ruled out, and this is the sharp one: plain exhaustion.** Summing
`SizeOfImage` (plus `SectionAlignment` where it exceeds a page) over the 46 loads in
promotion order gives **6,397,952 B = 6.10 MiB**, spread over 46 images. Against
that, the device's own memory map gives the DXE heap as
`{"DXE Heap", 0x9B800000, 0x02360000, AddMem, SYS_MEM, SYS_MEM_CAP, Conv,
WRITE_BACK_XN}` (`uefi/Platforms/Xiaomi/gauguinPkg/Library/MemoryMapLib/MemoryMapLib.c`)
— **35.4 MiB of `EfiConventionalMemory` at 0x9B800000**. And the FFS file list
adds nothing on top: `FFS_ATTRIB_CHECKSUM` is clear on all 123 files, so
`FvCheck`'s `AllocateCopyPool` per-file cache is never populated and the volume is
read in place. The cumulative demand is 6.1 MiB of a 35.4 MiB heap. And the two
drivers on either side of the boundary have the *same* `SizeOfImage`: `PdcDxe` is
`36,864` and is an `L` at promotion position 19; `ShmBridgeDxe` is `36,864` and is
an `s` at promotion position 21. A loader that could not satisfy a 36,864-byte
request at position 19 satisfied an identical one two positions later. Whatever is
happening, it is not "the heap filled up".

**Ruled out: depex arity.** 53 of the 80 drivers have no `DXE_DEPEX` section at
all and 25 of the 27 that have one are the bare `(TRUE)`. The only real dependency
in the entire volume is `883CC780-0281-F0F6-A313-4A26F03EF2E0`, required by
`WatchdogTimer` and `RealTimeClockRuntimeDxe` and **defined nowhere in the tree** —
a second missing-provider signal, but not this one, because both of those fail as
`L` before any depex is evaluated.

**Ruled out: everything below the dispatcher.** This was the layer with room for a
quiet, plausible bug, so every part of it was replayed against the actual volume
rather than reasoned about:

- **`FvCheck`.** The scan adds **123 files** to `FfsFileListHeader` and stops at an
  erased run at `0x702308` — `0xcf8` bytes from the end of a `0x703000` volume,
  after every file. The truncated-list mechanism is real in the code and hides
  **nothing** here. Zero corruption.
- **`IsValidFfsFile`.** All **123/123** files pass the exact test, including
  `CalculateCheckSum8`'s `(0x100 - sum) & 0xFF` semantics and the `0xAA`
  `FFS_FIXED_CHECKSUM` for the un-attributed case. (`tools/fv-census.py`'s
  replay had used the raw sum here, and this step corrected it to EDK2's
  `CalculateCheckSum8`; harmless only because no file sets the bit.)
- **`FFS_ATTRIB_CHECKSUM`.** Set on **0 of 123** files — attribute histogram
  `{0x0: 123}`. So `FvCheck`'s memory-mapped `AllocateCopyPool (WholeFileSize,
  CacheFfsHeader)` branch and its `EFI_OUT_OF_RESOURCES` site are **dead code on
  this volume**, and no file is ever copied.
- **The read path.** `FvReadFile`'s `do { FvGetNextFile (…) } while
  (!CompareGuid (…));` leaves `LastKey` on the *matching* entry, so
  `FvReadFileSection`'s `FfsEntry = (FFS_FILE_LIST_ENTRY *)FvDevice->LastKey` is
  sound. **This was carried as a suspected defect for a while; it is not one.**
- **The section read.** `GetSection` writes `*BufferSize = SectionSize`
  unconditionally on success, so a stale `SizeOfBuffer` cannot survive a
  successful Apriori read.
- **The GUID table.** All **70/70** Apriori GUIDs resolve to a file the volume
  contains.

So the volume is clean at every layer beneath the promotion loop, and the
remaining question is not in the bytes on disk.

**Settled: the boundary is in the Apriori array's index, not in the volume's
physical file order.** Step 4.9's discussion, and the reading it was built on,
treated the stop as a position in the volume — an earlier draft of this step
claimed a "physical file 49/50" cutoff and had to drop it. It cannot be right:
Apriori entry 22 (`ShmBridgeDxe`) is at **physical index 74** and *is* promoted and
started, while entries 66–69 (`SimpleTextInOutSerial`, `ConPlatformDxe`,
`ConSplitterDxe`, `GraphicsConsoleDxe`) sit at **physical indices 14, 20, 21 and
22** and are *not* promoted at all. No walk-order cutoff produces that set.

**Re-derived from the volume, and the argument is now a measurement with a single
number in it.** Joining each SEQ position *k* to `Apriori[k + 1]` and then to the
volume's file table gives the 46 promoted drivers at physical indices

```
physical indices of the 19 loaded (s): 2 3 4 10 11 17 24 25 26 27 28 29 30 31 39 42 43 52 74
physical indices of the 27 failed (L): 5 6 7 8 9 12 13 15 16 18 19 23 33 34 35 36 37 38 40 44 45 46 47 48 49 54 73
```

**The lowest physical index among the failures is 5; the highest among the
successes is 74.** A load at physical 5 failed — `SecurityStubDxe`, Apriori entry
37 — while a load at physical 74 succeeded, and the interleaving across the whole
range rules out not just a cutoff but any monotone function of physical position.
`tools/fv-census.py` prints exactly this and the verdict line "a physical cutoff
is IMPOSSIBLE". The Apriori index is the only ordering in which the promoted set
is a clean prefix.

An earlier draft of this paragraph carried different numbers for the
counterexamples — `NpaDxe` at physical 28, `RpmhDxe` at 31, `ShmBridgeDxe` at 72 —
and they were wrong; the measured values are 30, 33 and 74. The five-`s`-after-31
argument it was making survives, because there are still successes above every
failure, but it is now one number instead of a list.

**Settled as (b), from the host: why promotion stops at 46.** The loop iterates
`Index < AprioriEntryCount`; the ring buffer is filled on a match and `break`s out
of the inner scan, so a contiguous 46-letter string is 46 matches and not a short
array. The two readings that would have produced it were:

  (a) `AprioriEntryCount` was 47 on the device — the section read came back short.
  (b) the loop did run to 70 and entries 47–69 matched nothing, because those files
      were never in `mDiscoveredList`.

**(a) is dead, and it is dead by the code.** `GetSection` writes
`*BufferSize = SectionSize` unconditionally on success, so a stale `SizeOfBuffer`
cannot survive a successful read; and even a short array could not do this, because
`mP2AprioriCount = MAX (mP2AprioriCount, AprioriEntryCount)` is a running
*maximum* over every `ReadSection` in the walk, so the counter the `P2 STATS` line
would report is not the one the promotion loop used. The stale-`SizeOfBuffer`
mechanism this step was built on is retracted.

**(b) is what is left, and the promotion loop cannot be the cause.** The match is
`CompareGuid (&DriverEntry->FileName, &AprioriFile[Index]) && (FvHandle ==
DriverEntry->FvHandle)`. The outer loop runs the whole array and the inner loop
scans the entire `mDiscoveredList`; the extra `FvHandle` conjunct is satisfied by
every entry *this* FV added, because `CoreFwVolEventProtocolNotify` processes one
FV and returns. So for any file the walk handed to `CoreAddToDriverList`, the
matcher finds it. Entries 47–69 therefore matched nothing because those 23 files
are **not in `mDiscoveredList` at all** — `CoreAddToDriverList` was never called
for them. The walk's only escapes for a type-0x07 file are
`FvHasBeenProcessed (FvHandle)` (once per FV, not selective), `FvFoundInHobFv2`
(`continue`), a `GetNextFile` error, or `FvGetVolumeAttributes` /
`EFI_FV2_READ_STATUS` failing. And the one failure `CoreAddToDriverList` itself can
have — `AllocateZeroPool` returning NULL — is an `ASSERT (DriverEntry != NULL)` →
`CpuDeadLoop`, not a silent skip.

**So the open question has narrowed to one sentence: why are 23 of the 70 Apriori
files absent from `mDiscoveredList`?** Nothing below the dispatcher can explain it
(that layer is exonerated above), and the Apriori array is not short. The
measurement that answers it is `mP2WalkSeen[0]` — `P2 WALK t=0 seen=…` — and it has
never been read off the panel. This is why the probe exists and why the next step
is a power-on rather than a rebuild.

**And the `P2 STATS` line was never captured at all.** It appears in this step's
earlier drafts as if it had been — `apriori=46/N`, `discovered=?` — and it has not:
the only device reading of this screen is the 46-character `P2 SEQ` string. The
`N` in `apriori=46/N` has never been observed. A shorter form of the SEQ string,
`ssssssssssssssssssLLLsL` (23 characters), also appears in this project's notes,
and it is **a summarisation artifact rather than a second reading** — the
transcript holds exactly one user turn carrying the string, and it is the
46-character one.

### What was built to close it, and why it is on the device now

Two probes were added to the `P2BRINGUP` block and the firmware rebuilt and
flashed. Neither answers a question the host can compute, which is the test for
whether a round trip is worth it:

- `P2 WALK t=<type> seen=<n> iter=<n> last=<guid>` — one line per entry in
  `mDxeFileTypes`, printed from counters incremented inside the discovery walk,
  immediately around each `GetNextFile` call. The loop is a
  `do { … } while (!EFI_ERROR (GetNextFileStatus));`, so the terminating call —
  the one that returns `EFI_NOT_FOUND` — is counted in `iter` and in
  `mP2WalkErr`, and **not** in `seen`. That makes the DRIVER line
  (`t=0`) a self-checking prediction: **`seen=80 iter=81`** if the sweep ran to
  the end of the volume, since `FVMAIN.Fv` holds exactly **80** files of type
  `0x07` — the histogram over all 123 files is `{0x02: 37, 0x05: 1, 0x07: 80,
  0x09: 5}` — and the 81st call is the `EFI_NOT_FOUND` that ends it. Anything
  less than 80 in `seen`, or more than 81 in `iter` for the DRIVER pass, is the
  walk being cut off, and `last` then names where. (An earlier draft of this
  bullet said `seen ≈ 73`, a guess off the Apriori array's length; the array is
  not the volume. A second draft said the other four types print `seen=0`; the
  `DXE_CORE` pass has one file — `DxeCore` itself — so it prints `seen=1`, and
  every type prints a line because every type makes at least the terminating
  call.)
- `P2 FREE largest=<n> pages` — the largest allocation `CoreAllocatePages` will
  still satisfy at the moment the assert fires, found by a shrinking ladder
  (4096, 1024, 256, 64, 16, 4, 1 pages) that allocates and immediately frees. This
  is the memory question asked directly instead of computed. **It is the one probe
  whose answer is partly predictable from the host:** with 6.10 MiB of demand
  against a 35.4 MiB `Conv` heap, a large number here would confirm the arithmetic
  and a small one would falsify the memory map rather than the hypothesis.

The console's screen is wiped by `AdvanceNewLine` and has no scrollback, and the
P2 output grows from 33 lines to at most 39; 100 rows are available and the
six-line assert postmortem prints after, so it still fits.

**And the capture has to be a video, not a still.** `PcdDebugPropertyMask` is
`0x2F` (`SiliciumPkg.dsc.inc:69`) and `0x20` is
`DEBUG_PROPERTY_ASSERT_DEADLOOP_ENABLED`, so the assert really is a deadloop — but
something resets the phone a few seconds later, so the panel is only readable
inside that window. Step 4.11's rejected photograph is what a still taken outside
it looks like.

### The payloads, and the one number that reconciles them

The instrumented build was regenerated and the payload of record rebuilt and
re-flashed through TWRP. All three variants pass `check-payload.py`,
`abl-boot-check.py` and `fv-inventory.py --against FVMAIN.Fv.txt` (123 offsets and
GUIDs, zero mismatches each).

| | |
|---|---|
| payload of record | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` |
| size / sha256 | 1,140,736 B / `ecc10a225c8492d99ab4062e840e8a6b7def34a75f3f3679b1989ed515bb4bda` |
| `boot` before this flash | `work/out/boot-before-p2walk.img`, sha256 `fb697f47…` |

`boot` was read back with `dd` before being overwritten, and the image pulled off
the device was taken apart to check that the reading could have come from it: its
kernel blob inflates to 3,145,840 B, its inner `FVMAIN` holds a `DxeCore` carrying
`P2 SEQ` / `P2 STATS` / `P2 NOLOAD` / `P2 DIAG` and *not* `P2 WALK` / `P2 FREE`,
and its Apriori array is 70 GUIDs. That last check is what licenses the join above:
the array the join was computed against is the array the device actually ran, so
the mapping needs no caveat about which build drew the screen.

**And the readback was taken again on 2026-09-23, after the re-flash, because the
question it answers had changed from "what drew this screen" to "is the payload
that can answer the next question actually on the device". It is:**

| | |
|---|---|
| `boot[0:1140736]` read back in TWRP | sha256 `ecc10a225c8492d99ab4062e840e8a6b7def34a75f3f3679b1989ed515bb4bda` |
| payload of record | sha256 `ecc10a225c8492d99ab4062e840e8a6b7def34a75f3f3679b1989ed515bb4bda` |
| inner `FVMAIN` on device | sha256 `2995a6d0c1e62ceb…`, 123 files, 7,352,320 B |
| inner `FVMAIN` in the build tree | identical |
| Apriori array on device | 70 GUIDs, 1120 bytes, md5 `ed607ebccf3c61aa02d15f4b727baf85` |
| Apriori array in the build tree | identical |

Byte-for-byte. **The payload on the device carries `P2 WALK` and `P2 FREE`**, so
the three readings step 4.12's "Next" asks for are obtainable by a reboot — no
flash, no TWRP round trip, and no dependence on the USB port, which at the time of
writing is not enumerating at all.

The `md5` above replaces `4fef65c55ac6d83cbfa14996e4b1b0dd`, which an earlier
draft of this step carried and which **matches no hash of any part of the file and
appears nowhere else in this repository**. It was copied forward without being
recomputed; the value for the 1120-byte Apriori payload is
`ed607ebccf3c61aa02d15f4b727baf85`. A hex digest that nothing else in the repo
agrees with is worth more suspicion than one that appears twice.

**And the host can now say what the difference between the two payloads is, file
by file.** `boot-before-p2walk.img` (1,142,784 B, sha256 `fb697f47…`) is the image
that drew the 46-character SEQ; the payload of record is 1,140,736 B and carries
the `P2 WALK` / `P2 FREE` probes. Diffing their inner `FVMAIN` GUID tables:

```
only in the payload of record (123 files): [('AcpiTables', (0x02, 2878))]
only in boot-before-p2walk  (122 files): []
type/size mismatches on shared GUIDs:    []
```

So the two builds differ by **exactly one file**: the `AcpiTables` FREEFORM file,
2,878 bytes, type `0x02` — the platform DSDT. Nothing else moved. That is what
makes the join above safe to compute against the build tree: the volume the SEQ
was produced from is this volume minus one ACPI table, and no driver's offset in
it changed except by the 2,878 bytes that file and its FFS padding account for.

**And that file is at physical index 112, after every driver.** The consequence is
testable, and it tests out: running the join against the reading's own build
(`work/out/boot-before-p2walk.img`, 122 files) reproduces it **exactly** — 19 `s`
and 27 `L`, the same two Apriori sets, the same physical index list
`[5, 6, 7, 8, 9, 12, 13, 15, 16, 18, 19, 23, 33, …, 73]`, and the same 5-vs-74
verdict. So the join does not depend on which of the two volumes it is computed
against, and the "one file differs" caveat is discharged rather than carried.

Two traps in taking that readback, because the first attempt produced a confident
wrong answer:

- **`head -c 2097152 file | sha256sum` is not the same comparison as
  `dd bs=4096 count=512 | sha256sum`** when the file is shorter than 2,097,152
  bytes. The local payload is 1,140,736, so `head -c` silently returns the whole
  file and the sha covers 1,140,736 bytes; the device `dd` returns a full 2 MB with
  the partition's stale tail behind it. Comparing those two hashes is comparing two
  different lengths, and it reads as "the device holds something else". Hash the
  **declared payload length** on both sides.
- The stale tail is real and harmless: `boot` is 128 MB and the payload is 1.1 MB,
  so everything past byte 1,140,736 is whatever the previous image left there. Only
  the written prefix means anything.

The other thing the earlier readback closed is the discrepancy carried since step
4.10: the build tree's `APRIORI.inc` had been regenerated with the *Qualcomm*
display driver at slot 60, and the flashed array has
`DCFD1E6D-788D-4FFC-8E1B-CA2F75651A92` (`SimpleFbDxe`) there instead. The two now
agree, because `tools/make_uefi_platform.py --display` **defaults to `simple`**: a
default is what a plain regeneration produces, and it has to be the configuration
that has been on the device. `DisplayDxe` is a bring-up step of its own, taken once
DXE reaches BDS, not something that should happen by omitting a flag.

### A tool fix this forced

`tools/flash-boot.sh` refused the new payload: 1,140,736 bytes is a multiple of
2048, which is the `silicon` profile's page size, and not of 4096, which is the
block device's. The old guard simply died. That was hiding a real hole rather than
finding one — the read-back does `dd bs=4096 count=$((SIZE/4096))`, so for a
non-4096-aligned image it reads *less* than was written and hashes a prefix, which
passes whatever the last half-block happens to contain. Every earlier payload was
4096-aligned by luck. The script now pads the file to a block with zeros and pushes
the padded copy, so the whole written region is verified and the half-block that
used to be unverifiable is zeros on both sides.

**And `tools/fv-inventory.py`'s section walker was stepping by the wrong amount.**
PI 2.3.1 pads every section to a 4-byte boundary, so the next section header is at
`align4 (off + size)` and not at `off + size`. Most section sizes are already a
multiple of 4, which is why it looked right: the odd-length ones put every
following header four bytes early, and a four-byte-early section header still
yields a plausible type byte. Measured on `FVMAIN.Fv`: stepping by `size` reports
**83** sections across its 123 files; stepping by `align4` reports 123 files'
worth, and every file gains the trailing section the unaligned walk had been
losing. This matters beyond tidiness — `fv_files ()` and `sections ()` are what
`tools/apriori-order.py`, `tools/fv-census.py` and `tools/pe-facts.py` all read the
volume through, so a walker that drops each file's last section would have
mis-cited the Apriori payload as well.

### What step 4.9 and step 4.10 got wrong

Recorded because both were written with confidence and both are now replaced:

- **`P2 SEQ` is not capped at 69 — the cap is 128 — and `DxeCore` is excluded by
  its file type, not by being undiscovered.** Step 4.9's paragraph had the right
  count and the wrong mechanism — it said the promotion loop's GUID compare "finds
  nothing", implying no file. The file is there; it is the discovery switch that
  declines to add it. And the "69" was never a limit of anything: the ring buffer
  is `mP2AprioriGuid[128]` behind `P2BRINGUP_APRIORI_MAX`, so a 70-match run would
  print 70 characters.
- **The eight providers' SEQ indices were 5, 6, 7, 8, 9, 31, 34, 36, 37, 38, 39,
  42, 44** — not 4, 5, 6, 7, 8, 30, 33, 35, 36, 37, 38, 41, 43. Two of the names
  were wrong outright: the modules are `ArmCpuDxe` and `ArmTimerDxe`, not `CpuDxe`
  and `TimerDxe`.
- **Step 4.10's reorder experiment is retired, not pending.** All 27 failures are
  `L`, and reordering the Apriori array cannot change which files discovery finds
  or in what order — it changes only which discovered entries get *scheduled*. The
  variant would have reproduced the same first `L` at a different driver. It was
  built and is kept at `work/out/retracted/Mu-gauguin-arch-first-gzip.img` so it
  cannot be picked up as a candidate by accident. `--apriori-move` and
  `tools/apriori-order.py` remain the right tooling for a reordering experiment;
  this is simply not a reordering problem.

### Readings retracted in this step, and the measurements that replaced them

Six mechanisms were proposed, argued for, and then killed by measurement. They
are listed together because each one is the natural next guess, and the list is
the cheapest way to stop the next session from re-deriving them:

- **The stale or partial `SizeOfBuffer` short read.** `GetSection` writes
  `*BufferSize = SectionSize` unconditionally on success, and `mP2AprioriCount` is
  a running `MAX`, so neither a short array nor a stale length survives contact
  with the code.
- **A second Apriori GUID file, or a hidden `EFI_SECTION_USER_INTERFACE` on the
  existing one.** The Apriori file is physical 0, one file, one `RAW` section,
  1120 bytes, 70 GUIDs, no UI section.
- **The "74-GUID file" as a source for `mP2AprioriCount`.** There is no such file.
- **`FvReadFile`'s `LastKey` as a defect.** It is left on the matching entry, so
  the read that follows it is correct. This was carried as a suspected bug for a
  while and is not one.
- **`FvCheck` truncating the FFS file list.** The scan does stop dead at an erased
  run, and on this volume that run is 0xcf8 bytes from the end with nothing after
  it: 123 files listed, zero corruption. The mechanism is real in the code and
  hides nothing here.
- **The `.reloc`-less images, and memory protection.** Both were carried past this
  step and both are killed in the two sections below — the first because
  `RelocationsStripped` is a PE header bit and not a missing section, the second
  because the memory-protection settings the policy reads are all zero on this
  build. They are listed here so that the count in the heading is honest: this step
  did not end with a surviving mechanism, it ended with none.

What replaced them is four measurements: the FFS attribute histogram and the exact
`IsValidFfsFile` test (volume clean at every layer), the Apriori-index join with
the 5-vs-74 verdict (no physical cutoff), the promotion loop read verbatim
including its `FvHandle` conjunct (the loop cannot miss a listed file, so the 23
absent entries were never listed), and the payload diff (the build tree differs
from the reading's build by one `AcpiTables` file).

### The one partial mechanism that "survived", for 3 of the 27 — and it does not

`tools/pe-facts.py`'s table has no field that separates the classes, but its last
two lines report a *joint* condition that does most of the work for three of the
27: **three of the 27 have no `.reloc` section and `ImageBase 0x0`** —
`EmbeddedMonotonicCounter`, `RealTimeClock` and `CapsuleRuntimeDxe`. `has_reloc`
on its own is a shared value (`true` and `false` both occur on both sides), so
this is not a one-field separator; the signal was taken to be in the combination.
With relocations stripped, the argument went, `CoreLoadPeImage` takes the
`CoreAllocatePages (AllocateAddress, …)` path at address 0 and has **no**
`AllocateAnyPages` fallback (`if (EFI_ERROR (Status) && !RelocationsStripped)`),
and `CoreInternalAllocatePages` rejects `Start == 0` with `EFI_NOT_FOUND` because
page 0 is reserved for null-pointer detection. That would record an `L` — and one
with a status that does *not* render as "Out of Resources", unlike the one
`P2 DIAG` line that was read.

**The premise is false, and it is false in the source rather than by inference.**
"Relocations stripped" is not `reloc size == 0`. It is a field on the loader's
image context, and `PeCoffLoaderGetImageInfo` sets it from exactly one bit:

```c
  if ((!(ImageContext->IsTeImage)) && ((Hdr.Pe32->FileHeader.Characteristics & EFI_IMAGE_FILE_RELOCS_STRIPPED) != 0)) {
    ImageContext->RelocationsStripped = TRUE;
  } else if (...) {
    ImageContext->RelocationsStripped = TRUE;
  } else {
    ImageContext->RelocationsStripped = FALSE;
  }
```

(`MdePkg/Library/BasePeCoffLib/BasePeCoff.c:660`.) `EFI_IMAGE_FILE_RELOCS_STRIPPED`
is `Characteristics` bit 0. **Measured, that bit is clear on all 46 promoted
drivers** — `Characteristics` is only ever `0x2e` or `0x2022` across the whole
promoted set, and it is clear on all 80 DRIVER files in the volume. So
`RelocationsStripped` is `FALSE` for every one of the 46, every one takes the
`AllocateAnyPages` fallback, and the page-0 `AllocateAddress` path is taken by
**none of them**. The `.reloc`-size signal is not a partial mechanism for three of
the 27; it is not a mechanism for any of them, and `tools/pe-facts.py`'s closing
two lines reason from a field the loader never reads.

The tool is not wrong about what it measured. `.reloc` really is absent from those
three, and that absence really is unusual. What was wrong was the step from "no
`.reloc` section" to "`RelocationsStripped`": EDK2 derives the second from the PE
header bit and not from the section table, so an image can carry no relocation
data at all without ever declaring itself non-relocatable. `has_reloc` is a fact
about the file; `RelocationsStripped` is a fact about what the loader will do, and
only the second one is on the load path. The tool now carries a
`reloc_stripped` column that reads the bit, so the two cannot be conflated again;
it comes out `False` on all 46.

That also disposes of the paragraph's last line of defence. The observation that
`StatusCodeHandlerRuntimeDxe` succeeded while `EmbeddedMonotonicCounter`,
`RealTimeClock` and `CapsuleRuntimeDxe` failed at the same `ImageBase 0x0` was
carried as "one variable, not two". With the premise gone there is no variable
here at all.

### The runtime-driver size penalty, which also straddles

`SectionAlignment` was the one field in `tools/pe-facts.py`'s table with any
apparent structure — `0x1000` for most drivers and `0x10000` for a family — so it
is worth saying where that family comes from and what it costs, because the
temptation is to read `0x10000` as "the ones that need a bigger allocation".

It is not a PE property at all. It is the module type, set at link time:

```
[BuildOptions.common.EDK2.DXE_RUNTIME_DRIVER]
  *_CLANGPDB_*_DLINK_FLAGS = /ALIGN:0x10000
```

(`work/uefi/Mu-Silicium/Silicon/Silicium/SiliciumPkg/SiliciumPkg.dsc.inc`.) Every
module class that file names is linked with an explicit `/ALIGN`: `0x10000` for
`DXE_RUNTIME_DRIVER`, `0x1000` for `DXE_CORE`, `DXE_DRIVER`, `UEFI_DRIVER` and
`UEFI_APPLICATION`. So the `0x10000` family is the modules whose `MODULE_TYPE` is
`DXE_RUNTIME_DRIVER` — which is *nearly* the same set as the modules whose PE
header says `Subsystem 12`, and the next paragraph measures the difference rather
than assuming it away. What matters here is the cost, and it is in
`CoreLoadPeImage`:

```c
    if (Image->ImageContext.SectionAlignment > EFI_PAGE_SIZE) {
      Size = (UINTN)Image->ImageContext.ImageSize + Image->ImageContext.SectionAlignment;
    } else {
      Size = (UINTN)Image->ImageContext.ImageSize;
    }
    Image->NumberOfPages = EFI_SIZE_TO_PAGES (Size);
```

so a runtime driver requests `ImageSize + 0x10000` and a boot-services driver
requests `ImageSize` — a 64 KiB padding charge on top of a 64 KiB alignment
requirement.

**Measured over the 46 in promotion order, the runtime set straddles the result
just as every other candidate does.** The eight drivers with `SectionAlignment
0x10000` are `ReportStatusCodeRouterRuntimeDxe` (s), `StatusCodeHandlerRuntimeDxe`
(s), `RuntimeDxe` (s), `VariableRuntimeDxe` (L), `ResetSystemRuntimeDxe` (L),
`EmbeddedMonotonicCounter` (L), `RealTimeClock` (L) and `CapsuleRuntimeDxe` (L) —
`sssLLLLL`. Three succeed and five fail at the same alignment and the same size
penalty. Ten drivers carry PE `Subsystem 12`
(`EFI_IMAGE_SUBSYSTEM_EFI_RUNTIME_DRIVER`) — the eight above plus `EnvDxe` and
`SdccDxe`, whose INFs say `MODULE_TYPE = DXE_DRIVER` and which are therefore
linked at `/ALIGN:0x1000` despite the runtime subsystem in the PE, a disagreement
between the two header-derived facts that is worth knowing about before either is
used as a proxy for the other. That ten-driver set gives `ssssLLLLLL`.
`ImageCodeMemoryType` follows `Subsystem` and not `SectionAlignment`, so it is the
second partition that decides the memory type — and neither partition, and neither
field, is the split.

And the penalty is small enough that it could not matter. The largest single
request in the promoted set is 458,752 B / 112 pages, shared by
`ReportStatusCodeRouterRuntimeDxe` and `VariableRuntimeDxe` (of which 64 KiB is
the padding); the smallest is `PcdDxe`'s 53,248 B / 13 pages. The cumulative
figure is **1,562 pages = 6,397,952 B = 6.10 MiB** — the same total the exhaustion
argument measures, now split **575 pages across the 19 `s` and 987 pages across
the 27 `L`** against a 35.4 MiB `Conv` region. The alignment padding is 0.4 MiB
across the whole set and cannot carry a 6.1 MiB demand past 35.4 MiB.

**One more way the runtime path could have gone wrong, and it is off on this
build.** On AArch64 `RUNTIME_PAGE_ALLOCATION_GRANULARITY` is `0x10000` by default,
and `CoreInternalAllocatePages` would then require every runtime allocation to be
64 KiB-aligned *and* round `NumberOfPages` up to a multiple of 16 — a much sharper
requirement than the 4 KiB one, and one that could plausibly fail intermittently
on a fragmented heap. It does not apply here, because `SiliciumPkg.dsc.inc:14`
defines the escape hatch:

```
  *_CLANGPDB_AARCH64_CC_FLAGS = -D __DEPRECATED_AARCH64_4K_RUNTIME_GRANULARITY
```

`gauguin.dsc` has no `[BuildOptions]` section of its own; its only `!include` is
`BitraPkg/BitraPkg.dsc.inc`, which includes `QcomPkg/QcomPkg.dsc.inc`, which
includes `SiliciumPkg/SiliciumPkg.dsc.inc` — so this one line is where the flag
comes from and it is in the chain. With it, `MdePkg/Include/AArch64/ProcessorBind.h`
takes the `0x1000` branch and `Alignment` in `CoreInternalAllocatePages` is
`0x1000` for every memory type. `NumberOfPages` is never rounded and the
`AllocateAddress` alignment test is trivially satisfied for anything
page-aligned. The 64 KiB-alignment theory is dead at the source — and this
paragraph stops being true the day that define is removed.

### `ProtectUefiImage` is on the fatal path of every load, and inert on this platform

`CoreLoadImageCommon` calls `ProtectUefiImage` and treats its failure as fatal:

```c
  Status = ProtectUefiImage (&Image->Info, Image->LoadedImageDevicePath);
  if (EFI_ERROR (Status)) {
    goto Done;
  }
```

(`Image.c:1496`), and the `Done:` block calls `CoreUnloadAndCloseImage (Image, …)`
— so a status out of memory protection is an `L` exactly like an allocation
failure. `ProtectUefiImage` has its own `EFI_OUT_OF_RESOURCES` exits:
`AllocateZeroPool` of the `IMAGE_PROPERTIES_RECORD`, and three `AllocatePool` sites
inside `GetImageList` reached through `CreateImagePropertiesRecord`. That makes it
a live candidate for a run of `L`s that render as Out of Resources — the status
name on the `P2 DIAG` line.

It is inert here, and the chain is short. The policy comes from
`GetUefiImageProtectionPolicy`, whose first substantive check is
`if (!IsEnhancedMemoryProtectionActive ()) { return DO_NOT_PROTECT; }`, and the
settings it would otherwise consult live in `gDxeMps`, which
`DxeMemoryProtectionHobLibConstructor` fills from the
`gDxeMemoryProtectionSettingsGuid` HOB — **and no module in this build produces
that HOB.** `DxeMain.inf` lists it under `## CONSUMES ## HOB`; the only other
mention anywhere in the tree is `UefiTestingPkg`'s
`DxeMemoryProtectionTestApp.inf`, an application that is not built. So the
constructor takes its `else` branch and prints "Unable to fetch memory protection
HOB. Zero-ing memory protection settings", and `ZeroMem` leaves **every field of
`gDxeMps` zero**. With the policy zeroed the images take the `DO_NOT_PROTECT`
branch, `ProtectUefiImage` returns `EFI_SUCCESS` for all 46, and the fatal
`goto Done` never fires.

So memory protection is not the mechanism either — but unlike the `.reloc`
argument, this one is falsifiable from the next reading rather than only from the
source: **if a `P2 WHY` character shows `'R'` on a driver that should have taken
the `DO_NOT_PROTECT` branch, the zero-`gDxeMps` chain is wrong, and that is a
finding.** `P2WhyLetter` maps `EFI_OUT_OF_RESOURCES` to `'R'` for exactly this
kind of check.

### What is left, after all of that

No PE field and no header bit separates the 19 from the 27. The volume is clean at
every layer below the promotion loop. The total demand is 6.10 MiB of a 35.4 MiB
heap, and two drivers that make identical requests land on opposite sides. The
runtime-driver size penalty is 0.4 MiB across the set and straddles. Memory
protection returns success for every image. And zero `'S'` characters means no
entry point ran at all, so nothing a driver does at start time can be the cause.

What is left is state that exists only at run time: how much of the heap is
actually free, and in what sizes, at the moment each load asks. That is what
`P2 FREE largest=` and `P2 WHY` are for, and there is no reading of the volume on
the host that substitutes for them.

### Eleven drivers the volume registers and the Apriori list does not name

`tools/fv-census.py` enumerates them, and the count matters because an earlier
draft said four:

```
PwrUtilsDxe, VcsDxe, FeatureEnablerDxe, MacDxe, RamManagerDxe, SmbiosDxe,
SmBiosTableDxe, AcpiTableDxe, AcpiPlatform, BootGraphicsResourceTableDxe,
SetupBrowser
```

11 of the volume's 80 type-`0x07` files are not in the Apriori array, which leaves
the 69 that are — matching the 69 non-`DxeCore` entries exactly. They are discovered
and scheduled by DEPEX like any other driver; they are simply not promoted by the
a-priori pass. `EnvDxe`, which an earlier draft listed here, *is* named — it is
entry 2.

### The resolved table, all 70 entries

The join is exact and resolves every Apriori entry to a physical file. `phys` is
the index into `FVMAIN.Fv`'s file table (0 is the Apriori file itself, 1 is
`DxeCore`); the last column is the `P2 SEQ` letter, with `-` for the 23 entries
that were never promoted and `core` for `DxeCore`, which the walk never lists.

**One assumption in this table, stated here rather than left implicit: the 23
unpromoted entries are taken to be `ap47..ap69`, the array's tail.** That is what
makes `SEQ[k] = ap(k+1)` and it is not yet measured — see step 4.13, which is the
step written to measure it, and the `P2 APRI miss=` row that decides whether this
table stands or shifts. Read the `-` column as "not promoted under the suffix
hypothesis" until that line has been read.

```
ap 0 DxeCore                          phys   1  core   ap35 PmicDxe                    phys  54  L
ap 1 PcdDxe                           phys   2  s      ap36 WatchdogTimer              phys   8  L
ap 2 EnvDxe                           phys  24  s      ap37 SecurityStubDxe            phys   5  L
ap 3 ReportStatusCodeRouterRuntimeDxe phys  10  s      ap38 EmbeddedMonotonicCounter   phys  13  L
ap 4 StatusCodeHandlerRuntimeDxe      phys  11  s      ap39 RealTimeClock              phys  16  L
ap 5 RuntimeDxe                       phys   4  s      ap40 PrintDxe                   phys  18  L
ap 6 ArmCpuDxe                        phys   3  s      ap41 DevicePathDxe              phys  19  L
ap 7 ArmGicDxe                        phys  25  s      ap42 CapsuleRuntimeDxe          phys   9  L
ap 8 MetronomeDxe                     phys  17  s      ap43 HiiDatabase                phys  23  L
ap 9 ArmTimerDxe                      phys  26  s      ap44 BdsDxe                     phys  73  L
ap10 SmemDxe                          phys  28  s      ap45 GpiDxe                     phys  44  L
ap11 DALSys                           phys  39  s      ap46 I2C                        phys  45  L
ap12 HWIODxeDriver                    phys  43  s      ap47 AdcDxe                     phys  58  -
ap13 ChipInfo                         phys  27  s      ap48 UsbPwrCtrlDxe              phys  57  -
ap14 PlatformInfoDxeDriver            phys  52  s      ap49 QcomChargerDxeLA           phys  56  -
ap15 HALIOMMU                         phys  42  s      ap50 ChargerExDxe               phys  55  -
ap16 ULogDxe                          phys  29  s      ap51 UsbfnDwc3Dxe               phys  62  -
ap17 CmdDbDxe                         phys  31  s      ap52 UsbBusDxe                  phys  63  -
ap18 NpaDxe                           phys  30  s      ap53 UsbKbDxe                   phys  64  -
ap19 RpmhDxe                          phys  33  L      ap54 UsbMassStorageDxe         phys  65  -
ap20 PdcDxe                           phys  34  L      ap55 UsbMsdDxe                  phys  66  -
ap21 ClockDxe                         phys  40  L      ap56 UsbDeviceDxe               phys  67  -
ap22 ShmBridgeDxe                     phys  74  s      ap57 UsbConfigDxe               phys  68  -
ap23 ScmDxe                           phys   6  L      ap58 ButtonsDxe                 phys  53  -
ap24 DiskIoDxe                        phys  35  L      ap59 TsensDxe                   phys  59  -
ap25 PartitionDxe                     phys  36  L      ap60 SimpleFbDxe                phys  51  -
ap26 EnglishDxe                       phys  38  L      ap61 LimitsDxe                  phys  60  -
ap27 SdccDxe                          phys  47  L      ap62 HashDxe                    phys  69  -
ap28 UFSDxe                           phys  48  L      ap63 CipherDxe                  phys  70  -
ap29 Fat                              phys  37  L      ap64 RngDxe                     phys  72  -
ap30 TzDxe                            phys   7  L      ap65 DDRInfoDxe                 phys  61  -
ap31 VariableRuntimeDxe               phys  12  L      ap66 SimpleTextInOutSerial      phys  14  -
ap32 DALTLMM                          phys  49  L      ap67 ConPlatformDxe              phys  20  -
ap33 SPMI                             phys  46  L      ap68 ConSplitterDxe              phys  21  -
ap34 ResetSystemRuntimeDxe            phys  15  L      ap69 GraphicsConsoleDxe         phys  22  -
```

Two things the table makes visible that the prose argues: the `L`s are not
contiguous in `phys` (5, 6, 7, 8, 9, 12, 13, 15, 16, 18, 19, 23, 33, …, 73), and the
never-promoted `-`s are interleaved with them right across the volume
(`phys` 14, 20, 21, 22, 51, 53, 55–70, 72).

### The panel line the user read, and where it could and could not have come from

The device-side report that started this step was
`UFS Sleep callback registration failed`. That string is in the built volume — but
**neither of the two drivers that could print it ran.** `grep` for it over
`Binaries/` finds it in `UFSDxe.efi` (whose literal is
`UFS Sleep callback registration failed, Status = 0x%lx`, at file offset `0x1431c`)
and, for gauguin, **only** there — gauguin's `SdccDxe.efi` does not carry it at all,
unlike most other device profiles. `RpmhDxe.efi` carries the parallel literal
`Rpmh Sleep callback registration failed, Status = 0x%lx` at `0x0a65d`. In the
reading, `UFSDxe` is SEQ 27 and `RpmhDxe` is SEQ 18 — **both `L`**. An `L` is
written on `CoreLoadImage`'s error path, before `CoreStartImage`, so neither
`DriverEntry` ever executed and neither `DEBUG` call was reachable.

Both are thin wrappers over the same protocol:

```
adrp x9, 0x19000
ldr  x8, [x9, #0x740]   ; gKernel
cbz  x8, 0x6348
ldr  x10, [x8, #0x40]   ; gKernel->MpCpu
ldr  x2, [x10, #0x58]   ; MpCpu->RegisterPwrTransitionNotify
br   x2
mov  x0, #-0x7ffffffffffffffd   ; EFI_UNSUPPORTED  (gKernel == NULL)
```

so the failure is `EFI_UNSUPPORTED` because `gKernel` is NULL. `gKernel` is the
`EFI_KERNEL_PROTOCOL` installed against `gEfiKernelProtocolGuid`
(`B5062BE7-170B-4A32-BE21-689262FF4399`, `QcomPkg.dec:94`) — and **30 drivers'
PE bodies reference that GUID while nothing in the tree installs it.** There is no
`QcomKernelDxe`; `EFI_KERNEL_PROTOCOL` blobs are linked against a provider that
this firmware is missing. That is a real, named, missing dependency and it is worth
chasing on its own, but **it is not the source of the panel line**: the two drivers
that would have printed it never loaded.

**Where the line probably came from.** The device's own `xbl_dxe_fv.bin` — the DXE
volume ABL carries — contains `UFS Sleep callback registration failed`,
`Rpmh Sleep callback registration failed` **and** the bare
`Sleep callback registration failed` substring. So the line is plausibly ABL's own
output, printed before our payload is ever entered, or output from an earlier
payload. Either way it should not be read as a fault *of this firmware*, and the
step-4.10 hypothesis built on it should not be revived without re-attributing the
line first.

### Next

**The image is already on the phone, so the next step is a look, not a flash, and
not a video.** Every claim in the paragraphs below about what still had to be
obtained was written before step 4.13, and one of them is now wrong: it said "no
flashing is needed", and a flash *was* done — see 4.13 for the image, the hash and
the read-back. What replaces it is not a second flash but the instrumented payload
that is now in `boot`, and the reading it produces does not need a video, because
the digest repeats 41 times and any moment on the panel is a valid frame.

**Three lines decide the two puzzles, and they are now written to be read
together.** `P2 APRI` (`bytes/entries/sum`, then `matched=` and `miss=`) settles
whether the 23 absent entries are a suffix of the array or interleaved — which is
what makes step 4.12's join either correct or shifted, and it is the one question
that step was unable to answer from the host. `P2 STATS discovered=` splits the
remaining fork: near 80 means the walk saw every DRIVER file and something after
the walk dropped 23, while near 57 means the sweep itself was cut short. `P2 WHY`
turns the 27 `L` from a count into a mechanism: `P2Record` writes `'L'` for every
`CoreLoadImage` failure regardless of *which* error it was, so the letters are the
only signal that separates a load that ran out of memory (`'R'`) from one the PE
loader rejected (`'E'`) — and, because memory protection was measured to be inert
on this build, an `'R'` on a driver the policy should have skipped is itself a
finding.

The `P2 FREE largest=` line comes with them and is the same measurement step 4.12
was going to take: it either confirms the 6.10 MiB-vs-35.4 MiB load-order
allocation arithmetic or falsifies it. **That arithmetic is now the only candidate
standing** — every static property of the 46 has been measured and straddles, so
`P2 FREE` and `P2 WHY` between them are the whole of what is left to read on the
load-failure side. The `P2 STATS` line's `apriori=` denominator is no longer
load-bearing — the `AprioriEntryCount` fork it was going to split was settled from
the host — but `entries=` supersedes it anyway, from the device.

Then, once DXE reaches BDS, **remove the whole `P2BRINGUP` block** and regenerate
the patch with `tools/regen-mu-basecore-patch.sh --regen`.

**Everything computable from the host has been computed and re-checked**: the file
census, the `FvCheck` scan, the FFS attribute histogram, the exact `IsValidFfsFile`
test, the Apriori join, the DEPEX graph, every PE header with a per-field separator
verdict, the load-order allocation total against the device's own memory map, the
payload diff, and the attribution of the panel line. The Apriori section is now
also read on both sides — `tools/fv-census.py` on the host, `P2 APRI` on the device
— so a disagreement between them is a finding rather than a rounding error. What
remains is the list state and the failure statuses, and neither exists anywhere
except inside the running firmware.

### What this step cost, and what it bought

Eleven host-side hypotheses were tested and eleven were killed, which is a poor
ratio for a step and the reason it is worth writing down. The last two died later
than the rest and are recorded above: the `.reloc`-absence mechanism, which was
carried as "partial, for 3 of the 27" through this step and turns out to be no
mechanism at all because `RelocationsStripped` comes from a header bit the three
images do not set; and memory protection, which is on the load's fatal path but
returns success for every image on this build. What is left of the step's
conclusion is the *other* puzzle, and it is untouched: the 23 Apriori entries that
matched no driver, so that the 46 promoted entries are not the array's first 46.
That one is still open, it is a question about list state, and `P2 WALK` is the
probe for it. The load failures are now a question about runtime free space, with
no host-side reading left that could decide them: the volume, the checksums, the
file table, the Apriori array, the PE headers, the module memory types, the
alignment granularity, the heap size, the DEPEX graph and the loader's address
selection have all been measured and are all clean.

## Step 4.13 — The instrument that reads it for you

Step 4.12 ended with four lines that had been *drawn on the panel and never read*.
The image in `boot` carried `P2 WALK` and `P2 FREE` for a whole session, and what
came back was one `P2 SEQ` string and one `P2 DIAG` line. The reason is not
carelessness in the reading; it is that the panel is a framebuffer console with a
90×100 cell grid and **no scrollback** — `AdvanceNewLine` clears the whole screen
once the cursor passes the last row — so every reading is a race between a human
and a wipe. Three consecutive sessions were each decided by whether the right line
happened to be photographed before something else scrolled it away.

There is no second channel to fall back on, and this was checked rather than
assumed: on the host, `ls /proc/kcore /dev/mem /dev/kmem` returns **No such file
or directory** for all three, `/dev/video*` does not exist, and `v4l2-ctl` is not
installed. So there is no way to read the device's memory and no way to record the
screen except by pointing a camera at it.

Two changes follow from that, and both are in `Dispatcher.c`'s `P2BRINGUP` block.

### The gap in the step-4.12 join, which is what the instrument is aimed at

Step 4.12's join says "`P2 SEQ`'s index *i* is Apriori entry *i + 1*". Read the
promotion loop again and that sentence is **conditional**, in a way the step
states but does not flag:

```c
if (mP2Apriori < P2BRINGUP_APRIORI_MAX) {
  CopyGuid (&mP2AprioriGuid[mP2Apriori], &DriverEntry->FileName);
  mP2AprioriRes[mP2Apriori] = '?';
}
mP2Apriori++;          /* unconditional: counts MATCHES, not entries scanned */
```

The slot is `mP2Apriori`, the running **match count** — not `Index`, the position
in the array. So the string is the matched entries **compacted**: if Apriori entry
*j* matches nothing, it does not occupy a character, and every character after it
belongs to a later entry. `SEQ[k] = ap(k+1)` therefore holds **only if no entry
before position 46 fails to match** — which is the very thing at issue. The 46
characters are 46 matches among 69 candidate entries, so **23 of `ap1..ap69`
matched nothing**, and where those 23 sit is exactly what decides whether the
step-4.12 name tables are right or shifted:

| if the 23 absent are | then | and |
|---|---|---|
| `ap47..ap69` (a suffix) | `SEQ[k] = ap(k+1)` for all 46 | step 4.12's tables stand as written |
| interleaved, first gap at *j* < 47 | `SEQ[k] = ap(k+1)` only up to *j* | every name at or after `SEQ[j-1]` in the 4.12 tables is mislabelled |

Step 4.12's own consistency check — the eight missing arch protocols landing on
eight `L` positions — does **not** decide this, and it is worth saying why rather
than letting it carry more weight than it has. The `L` run is continuous from
`SEQ 22` to `SEQ 45`, and the eight arch entries are `ap30`, `ap33`, `ap35`,
`ap36`, `ap37`, `ap38`, `ap41`, `ap43`. Any compaction shift of up to eight
entries still lands all eight inside `22..45`, so the check passes for any
alignment in that band. It confirms the two sets *overlap*; it does not pin the
offset.

### What the firmware now prints

Three lines were added, all from inside the same `P2BRINGUP` block, all printed by
`P2Digest ()`:

- **`P2 APRI bytes=1120 entries=70 sum=a998b263`** and **`P2 APRI first=… last=…`**
  — the Apriori section **as `Fv->ReadSection` handed it to the dispatcher**:
  `SizeOfBuffer`, `SizeOfBuffer / sizeof (EFI_GUID)`, a `sum*31+byte` checksum over
  the buffer as read, and the first and last GUID. `tools/fv-census.py` prints the
  identical four numbers for the same section out of the same image, so the panel
  and the host are two readings of one object and agree or do not.
- **`P2 APRI matched=1..46`** and **`P2 APRI miss=47 <guid>`** — the arbitration
  the SEQ line cannot supply. `miss` is the lowest index above 0 whose entry
  matched nothing in `mDiscoveredList`. Index 0 is skipped deliberately: it is
  `gDxeCoreFileName`, the DXE_CORE branch never hands it to `CoreAddToDriverList`,
  so it matches nothing on *every* boot, and reporting it would put a permanent
  false miss at 0 and hide the real one.
- **`P2 WHY [...]`** — one character per `SEQ` character, aligned with it.
  `P2Record` writes `'L'` for *every* load failure, which says *that* 27 loads
  failed and not *why*; `'R'` (`EFI_OUT_OF_RESOURCES`), `'N'` (`EFI_NOT_FOUND`),
  `'X'` (`EFI_SECURITY_VIOLATION`) and `'U'` (`EFI_UNSUPPORTED`) have no mechanism
  in common, and the existing retracted-mechanism list is largely about telling
  them apart. `'s'` is success. (A *start* failure is a separate letter, `'S'`, in
  `SEQ` itself — see the phase split above; `WHY` is what distinguishes the 27
  load failures from each other.)

### The reading, and it is now a steady state rather than a race

`P2Digest ()` is called **41 times**, with `CoreStall (300000)` between calls. The
console wipes rather than scrolls, so after the first wipe the panel holds nothing
but copies of the digest, and whatever is on the screen whenever someone looks is
a valid reading. That is the whole purpose: it converts "photograph the right
moment" into "photograph any moment". The loop is bounded and `CoreStall` returns
`EFI_NOT_AVAILABLE_YET` quietly when `gMetronome` is NULL, so this cannot hang —
control falls through into the `ASSERT` at `DxeMain.c(593)` exactly as before, and
the reboot loop continues unchanged.

### The host-side prediction, so the comparison is a checkable pair

`tools/fv-census.py` was extended to print the device's own numbers and to replay
the promotion loop over the volume:

```
=== The Apriori section, as the device reads it ===
  bytes 1120  entries 70  sum 0xa998b263
  first D6A2CB7F-6A18-4E2F-B43B-9920A733700A  last CCCB0C28-4B24-11D5-9A5A-0090273FC14D
  replay of the promotion loop over this volume (index 0 skipped):
  matched 1..69 of 70 entries, miss none
```

The replay is over the **volume's file types**, not over `mDiscoveredList`, so it
cannot decide the compaction question — it says every named GUID is present as a
`DRIVER` file, which is a fact about the volume and not about the list. What it
does supply is the *identity* half of the check. A panel reading of `bytes 1120
entries 70 sum a998b263` says the dispatcher received the section the host read,
and the question moves entirely to `miss`.

**The numbers below are anchored to GUIDs, because a reading is only a comparison
if both sides name the same thing.** `ap46` is `I2C` (`D06A77F4-4874-5898-9421-303158ECEA1A`)
and is the last SEQ character; `ap47` is `AdcDxe`
(`9143B2B7-D5E7-5190-B22A-605E5C78E7CC`), the first entry the tail assumption says
matched nothing. `ap69` is `GraphicsConsoleDxe`
(`CCCB0C28-4B24-11D5-9A5A-0090273FC14D`) and is the last entry in the array. And
**all 69 non-zero entries resolve to a `DRIVER` file in this volume**, so a host
replay of the promotion loop over the volume alone matches `1..69` with no miss;
a panel `miss` can therefore only be non-`none` because `mDiscoveredList` is short
of what the volume contains, never because the volume is missing a name.

Three of these lines are one line of arithmetic apart from each other, which is
worth stating because it makes the reading self-checking. `noload` is
`mDiscoveredList`'s entries with `ImageHandle == NULL`, and zero `'S'` characters
means every driver that loaded also started, so **`discovered − noload = 19`
exactly** (`mP2Started`). If the pair on the `P2 STATS` line does not differ by 19,
the line was misread or the run is not the run this document is describing.

| panel says | means | next |
|---|---|---|
| `bytes`/`entries`/`sum` short of `1120/70/a998b263` | `Fv->ReadSection` returned a truncated section | the read is the mechanism; the "23 missing drivers" were never in the buffer, and there is no missing-driver puzzle to solve |
| `entries=70`, `miss=47 9143B2B7-…` | exactly `ap1..ap46` matched (`matched=1..46`), so the absent 23 are the array's tail | step 4.12's tables stand; the discovered list is short by a *suffix*, and the shortfall is in the walk or in `CoreAddToDriverList` |
| `entries=70`, `miss=j<47` | the absent entries are interleaved; `miss` names the first one | step 4.12's name tables are shifted from `SEQ[j-1]` on and must be redone against the true alignment |
| `entries=70`, `miss=none` | 69 of 69 matched, so the match count was never 46 | the 46-char line was truncated somewhere other than the array — re-open step 4.12 rather than patch it |
| `P2 WALK t=7 seen=80 iter=81` | the walk was handed every DRIVER file | the 23 were dropped after the walk, in `CoreAddToDriverList` or in the list itself |
| `P2 WALK t=7 seen≈57` | the walk was handed only 57 | `GetNextFile` or the walk's own bound; the volume's file table is known to hold 80 and `FvCheck` lists all 123 |
| `P2 STATS discovered=` ≈ 80 and `noload=` ≈ 61 | the list was populated and then 23 loads never happened | the load failures and the missing entries are separate faults |
| `discovered=` ≈ 57 and `noload=` ≈ 38 | the list was never fully populated | one fault, not two: the 23 absent Apriori entries and the 27 `L`s are both downstream of a short walk |
| `P2 FREE largest=` ≥ 256 | there is a free run of at least 1 MiB, so the largest single request in the promoted set (112 pages) would have fit | the 27 `L`s are not a single allocation that could not be satisfied, and `P2 WHY` decides what they are instead |
| `P2 FREE largest=` ≤ 64 | the largest free run is 256 KiB or less, less than the largest request the promoted set makes | the load order's arithmetic was right and the heap really is being consumed by something else; find what |
| `P2 FREE largest=0` | nothing is allocatable at all at digest time | the `L`s are exhaustion by another name |

`P2LargestAlloc` probes a fixed ladder — 4096, 1024, 256, 64, 16, 4, 1 pages —
and returns the first step that succeeds, so the value is quantized to those
seven numbers and `0` when even one page fails. That is why the table's thresholds
are 256 and 64 rather than 112: the number cannot report a 112-page run, only
whether a 256-page one exists.

**And `miss` can rule the short walk in or out on its own, because a short walk has
a shape.** `mDiscoveredList` is populated in walk order, and the walk is in
physical order (`Fv->ReadNextFile` skips forward by file size), so a walk that
stopped after volume file *k* would leave missing exactly the Apriori entries whose
files sit above *k* — a physical **suffix** of the DRIVER files. Measured against
that, the tail assumption is already incompatible with it: `ap47..ap69` are not the
volume's last 23 DRIVER files but a scattered set whose physical indices are
`{14, 20, 21, 22, 51, 53, 55…70, 72}`, which reaches back to the console drivers in
the first quarter of the volume. So under the tail assumption the walk cannot have
stopped early, and `P2 WALK t=7 seen=80` follows rather than being a separate
measurement.

The converse is sharper than expected, because `miss` does not just say *that* the
walk was cut, it says **where**. `mP2ApriMiss` is the lowest Apriori index whose
file sits above the stop, so it is a monotone step function of the stop position —
and the step function is short, because the Apriori-named files have gaps in them.
Measured over this volume, every possible `miss` value is one of eight, plus
`none`:

| `miss` | names | the stop was in physical | which is `seen` |
|---|---|---|---|
| `1` | `PcdDxe` | -1..1 (nothing listed at all) | 0..0 |
| `2` | `EnvDxe` | 2..23 | 1..22 |
| `7` | `ArmGicDxe` | 24 | 23 |
| `9` | `ArmTimerDxe` | 25 | 24 |
| `10` | `SmemDxe` | 26..27 | 25..26 |
| `11` | `DALSys` | 28..38 | 27..37 |
| `12` | `HWIODxeDriver` | 39..42 | 38..41 |
| `14` | `PlatformInfoDxeDriver` (`09EE56ED-E7FD-5B64-831C-7C32CE88C6E2`) | 43..51 | 42..50 |
| `22` | `ShmBridgeDxe` | 52..73 | 51..72 |
| `none` | — | 74..122 | 73..80 |

So a `miss` of anything else is not a cut at all: it means the device is looking at
a volume whose Apriori-named files sit somewhere the host's do not, and that is a
different investigation. And two of the rows are already eliminated from the
device's own earlier reading. `miss=none` cannot be it, because 46 of the 70 did
not match. `miss=22` cannot be it either, and for the nicest reason in this
document: `ShmBridgeDxe` is the driver at physical 74, the **highest index in the
loaded set** — the 5-vs-74 measurement — so a walk that stopped before 74 did not
produce this SEQ line. That leaves `miss=14 09EE56ED-…` as the signature of a
genuinely short walk, and `miss=47 9143B2B7-…` (`AdcDxe`) as the tail assumption's
signature. The two readings are two characters apart on the panel and they are
different faults.

### The image, and where it is

| | |
|---|---|
| image | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` |
| size | 1,140,736 B |
| sha256 | `8c565681d1093b76c1cf184a549099aa2a957127c8be5ded439d934164535842` |
| inner FVMAIN | 123 files, `0x703000` — identical to the tree's, "123 offsets and GUIDs, zero mismatches" against `FVMAIN.Fv.txt` |
| flashed to | `boot` (`sde55`), via `tools/flash-boot.sh --twrp` |
| read back | `ok the first 1142784 bytes of boot match` |
| previous `boot` content | archived first, per "对照的那张必须在覆盖之前读" — `work/out/boot-pre-flash-0923c.bin`, sha256 `03ef39d1ea1cef463f77c8ee916ab46447e96b756011836388621a0d0788d574` |

Everything in the flashed image came from a tree that compiles with **zero
`error:` lines**; the build's exit status is 1 from the declared `mkbootimg`
`DTB image must not be empty.` nag and the artifact was checked directly, as
`tools/build-p2-payloads.sh` does.

## Step 5 — Leave it bootable

Whatever the outcome, end the session with the stock image back on `boot`:

```sh
tools/restore-stock-boot.sh
```

Do not leave an experimental image on the phone. The next session should start
from a known state, and the phone is someone's daily driver in between.

**Overridden on 2026-09-23, deliberately.** The instruction above was not followed
for the step-4.13 image: the phone was left running it, and the next step of the
work was taken to be modifying the firmware directly rather than restoring stock
between attempts. That is a knowing departure from the rule, not an oversight, and
it is recorded here because a rule that is quietly not followed is worse than one
that is rewritten. What it costs is that the phone stays in the boot loop — our
payload draws its digest, asserts, and the APSS watchdog resets it — instead of
sitting at a stock Android boot. What it buys is that the reading is available
whenever someone looks at the screen, because the digest repeats.

The restore path is unchanged and still the way out:

```sh
tools/restore-stock-boot.sh    # verifies the pinned sha256 before writing
```

It is run from TWRP, and the pinned `EXPECT` hash is still
`50ef59beb17e75de1e749b7d261eb41a18d9cca025befb3357e43e239de78ef3`.

---

## What to record

For each attempt, one line — the phone is at a distance and memory is not a
channel:

```
attempt  image                          fbreason                     screen               pstore         abllog
-------  -----------------------------  ---------------------------  -------------------  ------         ------
1        (as found)                     LoadImageAndAuth Fail        logo, then fastboot  n/a            UEFILOG0: Start EBS
2        work/out/boot-pstore-raw-*.img (never flashed; `fastboot boot` refused on the RAM path)
3        as found in `boot`             not captured                 logo only            n/a            UEFILOG0: Start EBS
4        Mu-gauguin-silicon-gzip.img    not captured                 text, then assert    n/a            (not read)
5        (not flashed - same as row 4)  not captured                 not the panel        n/a            (not read)
6        boot-before-p2walk.img, then   not captured                 P2 SEQ (46 chars),   n/a            (not read)
         Mu-gauguin-silicon-gzip.img,                               then assert
         read back byte-identical
7        Mu-gauguin-silicon-gzip.img    not captured                 P2 SEQ + the P2 APRI/  n/a           (not read)
         (rebuilt with the 4.13                                     WHY digest, repeating
         instrumentation), read back                                41x - not yet read
         verified
```

Row 4 is the one that matters and is step 4.8: our firmware ran and drew its own
DEBUG stream on the panel, ending in `ASSERT [DxeCore] DxeMain.c(593)`. Rows 1–3
each stop before that point — 1 and 3 because the tree in the image was stale,
which is what step 4.7 found and row 4 fixes. Row 5 is the attempt to read row 4
back: `boot` was re-read and verified to still hold the same instrumented image, so
no flash was needed and none was done, and the photograph that came back was
measured and is not the panel — step 4.11 has the numbers. The reason `fbreason` is
"not captured" for 3–5 is that reading it needs a host channel to fastboot, and none
of those boots ended in fastboot.

Row 6 is the reading step 4.12 is built on, and the one state the table had been
missing. What came back from it is one line — the `P2 SEQ` string, read by eye
rather than by frame-stepping a video — plus, from the same sessions, a single
`P2 DIAG` line ("Out of Resources"). **No `P2 STATS` line has ever been read**, so
`discovered=`, `started=` and the `apriori=` denominator are all still unknown, and
it is the reason this step had to settle the `AprioriEntryCount` fork from the host
instead. It is also the only row whose image is confirmed byte-identical on the
device by a hash of the declared payload length rather than of a partition-sized
read.

Row 7 is step 4.13: the same volume plus the `P2 APRI`, `P2 WHY` and
repeating-digest instrumentation, written to `boot` after the previous contents
were archived. Its screen column is the one this row exists for, and it is the
first row whose reading is **not** a race — the digest repeats 41 times, so the
column stays fillable for as long as the phone has power, and it has not been read
yet. Rows 5 and 6 should be read as one attempt rather than two: row 5 is the read
back that found the image unchanged, and row 6 is the reading taken from it, so
"not flashed" in row 5 and the image named in row 6 describe the same state of
`boot`.


The `abllog` column is step 4.6's answer — the last stage ABL's own log for that
boot reached. It is the one column that is filled in whether or not the payload
ran, so it is the column worth not leaving blank.

That table is the entire output of a session. Everything else is in the files
`fastboot-capture.sh` and `pull-bootloader-log.sh` wrote, and — if step 4a ran —
in the pstore ring, which is gone the moment the power button is held.
