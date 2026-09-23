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
P2 SEQ [<70 characters>]                           (one per Apriori entry, in dispatch order)
P2 STATS discovered=N apriori=N/70 started=N diag=N noload=N
```

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

| index in `P2 SEQ` | 4 | 5 | 6 | 7 | 8 | 30 | 33 | 35 | 36 | 37 | 38 | 41 | 43 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| driver | RuntimeDxe | CpuDxe | ArmGicDxe | MetronomeDxe | TimerDxe | Variable | Reset | Watchdog | SecurityStub | Monotonic | RTC | Capsule | Bds |
| if it worked | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` | `s` |

So a healthy line is `s` at every position. The first character that is not `s`
is where the batch stopped, and its index names the driver.

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
**expected** pair, not a shortfall. 70 is the Apriori file's GUID count and 69 is
how many of them name a discovered driver — `DxeCore` is the one that does not, so
the `SEQ` string is 69 characters long and ends with `GraphicsConsoleDxe`.

**This is unverified.** Nothing here has been run on hardware — the phone was not
on USB when the image was built. What *is* verified is that the instrumentation is
in the artifact: `strings` finds all five format strings in `DxeCore.efi` and in
the packed `FVMAIN.Fv`, and the image's packed volume matches `FVMAIN.Fv.txt` at
all 122 offsets and GUIDs. Note where that check has to be made — **not** on the
payload, which carries `FVMAIN` inside `FVMAIN_COMPACT` and is compressed, so the
strings are not in it.

| | |
|---|---|
| image | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` |
| sha256 | `09db74022ccfef57515d69d8850465ab610c53356612f8eabcc3d9501d98a8ce` |
| payload | 3,145,840 B, md5 `a2963f46faeb27fe601022c3a67aa738` — byte-identical to `SILICIUM_UEFI.fd-bootshim` |
| previous image | sha256 `3db8aba2…`, payload md5 `8cbe30d8…` (DIAG/STATS, no SEQ) |

One build note to carry forward: `FVMAIN` is at **99% — 2104 bytes free**, and it
read the same before and after this rebuild because the FV's file records are
padded to alignment. The instrumentation has consumed nearly all the headroom, so
any further `DEBUG ()` text added to DXE may overflow the FV rather than simply
fail to display.

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

One build note, carried from 4.9 and unchanged by this experiment: `FVMAIN` sits at
**99% (2104 bytes free)**, and this variant adds no file, so the reorder does not
consume headroom.

**The image, and the measurement that says the reorder is the only variable.** All
four gates passed on the artifact: `apriori-order.py` read the array back out of the
image as the experimental INF order (`70 entries, zero mismatches`), `check-payload.py`
and `abl-boot-check.py` passed, and `fv-inventory.py --against FVMAIN.Fv.txt` matched
every one of the 122 files at its offset.

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

**What it means on the device, and the order to do it in.** Flash the baseline
first: its `P2 SEQ` string is what the variant is read against, and it is the one
piece of evidence that cannot be recovered afterwards. In the variant the eight
occupying SEQ **9–16** are, in order:

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

This is still unverified on hardware. The phone was off USB for every build in
steps 4.9 and 4.10, and nothing in either step has been run on the device.

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

**The order of operations is unchanged and the baseline is still first.** In TWRP,
`Reboot → System`, wait for the assert, then photograph the whole panel square-on
and filling the frame — that screen is the last thing drawn and it stays up. Read
`P2 SEQ […]` first, then `P2 STATS …`, then the `P2 DIAG` lines above them. Only
after that string is written down does `Mu-gauguin-arch-first-gzip.img` go on, and
its three lines are read the same way. **The baseline is the control and it cannot
be recovered once the variant has overwritten `boot`.**

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
attempt  image                          fbreason                     screen               pstore         abllog
-------  -----------------------------  ---------------------------  -------------------  ------         ------
1        (as found)                     LoadImageAndAuth Fail        logo, then fastboot  n/a            UEFILOG0: Start EBS
2        work/out/boot-pstore-raw-*.img (never flashed; `fastboot boot` refused on the RAM path)
3        as found in `boot`             not captured                 logo only            n/a            UEFILOG0: Start EBS
4        Mu-gauguin-silicon-gzip.img    not captured                 text, then assert    n/a            (not read)
5        (not flashed - same as row 4)  not captured                 not the panel        n/a            (not read)
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

The `abllog` column is step 4.6's answer — the last stage ABL's own log for that
boot reached. It is the one column that is filled in whether or not the payload
ran, so it is the column worth not leaving blank.

That table is the entire output of a session. Everything else is in the files
`fastboot-capture.sh` and `pull-bootloader-log.sh` wrote, and — if step 4a ran —
in the pstore ring, which is gone the moment the power button is held.
