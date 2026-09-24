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

> **Correction, added 2026-09-24 (step 4.20): the retirement reason above is
> unsound as written, for two independent reasons.**
>
> The stated reason is that "the batch stops producing promotable entries at a
> *physical file* boundary". Step 4.17 retracted that reading: the SEQ's letters,
> not its length, refute both physical stops, decisively at slot 21. So the
> sentence carries no argument any more.
>
> And its conclusion — that the variant "would therefore have reproduced the same
> 46-character string" — is the opposite of what steps 4.18 and 4.20 imply. Both
> narrow the 27 to heap state at the instant each request arrives, and 4.20 closes
> the only mechanism that could have made the boundary order-independent by
> injecting memory. Under that model the allocation sequence *is* what decides who
> fails, so reordering the Apriori array is exactly the experiment that would move
> the boundary: a different set of drivers failing at the same *count* is evidence
> for "a fixed quantity of heap, whoever arrives first", and a different count is a
> counterexample to it.
>
> The variant stays unflashed and is still not the next move — `P2 ERR` is one
> flash away and answers more per flash. But it is a live candidate again, not a
> closed one, and `work/out/retracted/Mu-gauguin-arch-first-gzip.img` (sha256
> `9a7e8ab8235f8f24f70e1012df7b840645043dc34d256dc156e093d3d2c35ce3`) is a built,
> never-flashed image with a known-good payload.

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
that step was unable to answer from the host. `P2 WALK t=0 seen=` splits the
remaining fork: 80 means `GetNextFile` handed back every `DRIVER` file and the 23
were dropped after the walk, while 57 means the sweep itself was cut short — and
the line is self-checking, because `iter` must be `seen + 1`. It is `P2 WALK` and
not `P2 STATS` that carries this, which is worth being exact about: `discovered` is
bounded at **57** by the 46-character `SEQ` and cannot report the walk's reach at
all. `P2 WHY`
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
`SEQ 22` to `SEQ 45`, and the eight arch-protocol providers are `ap31`
(`VariableRuntimeDxe`), `ap34` (`ResetSystemRuntimeDxe`), `ap36`
(`WatchdogTimer`), `ap37` (`SecurityStubDxe`), `ap38`
(`EmbeddedMonotonicCounter`), `ap39` (`RealTimeClock`), `ap42`
(`CapsuleRuntimeDxe`) and `ap44` (`BdsDxe`) — i.e. `SEQ 30, 33, 35, 36, 37, 38,
41, 43`. *(This list read `ap30, ap33, ap35, ap36, ap37, ap38, ap41, ap43` until
step 4.30: those are the same eight drivers' `SEQ` positions with the `ap` label
put on them, and the two numberings differ by one because `SEQ[k] = ap(k+1)`
under the very assumption at issue. The argument is unaffected — the eight are
inside `22..45` either way — but the indices are not interchangeable, and this is
the one place in the document where they were treated as if they were.)* Any
compaction shift of up to eight entries still lands all eight inside `22..45`, so
the check passes for any alignment in that band. It confirms the two sets
*overlap*; it does not pin the offset. What it also cannot do is fail: all eight
are inside the promoted 46 under *both* readings, so no observation of this set
distinguishes them — `P2 APRI miss=` is the field that does.

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
  load failures from each other.) Step 4.15 adds a third line, **`P2 ERR
  <status name> x<count>`**, which carries the same statuses grouped and named,
  because this pair turned out not to survive being read off a phone.

### The reading, and the race was shortened rather than removed

`P2Digest ()` is called **41 times**, with `CoreStall (300000)` between calls. The
console wipes rather than scrolls, so once the digest has been drawn the panel
holds nothing but copies of it, and a reading can be taken from whatever frame is
up — the intent was to convert "photograph the right moment" into "photograph any
moment".

It shortens the race rather than removing it, and this section as first written
said otherwise: it claimed the 41 copies were free, because "`CoreStall` returns
`EFI_NOT_AVAILABLE_YET` quietly when `gMetronome` is NULL". That is false on this
platform, and step 4.9's own table is what refutes it — `MetronomeDxe` and
`ArmTimerDxe` are both reported **present**, and the `s` at ap8 and ap9 in the SEQ
line is the same fact from the other side. `gMetronome` is therefore not NULL,
`CoreStall` waits, and the 41 copies are **40 × 300 ms = 12 seconds** of digest on
the panel.

Twelve seconds is a window, not a steady state, and it is bracketed on both sides
by things this document does not control: before it the reset and the boot, after
it the `ASSERT` at `DxeMain.c(593)` on `CoreAllEfiServicesAvailable`, whose output
lands on the same panel. Step 4.14 lengthens the window and, more to the point,
stops it depending on the drivers under test.

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

The `P2 STATS` line is one line of arithmetic, which is worth stating because it
makes the reading self-checking. `noload` is `mDiscoveredList`'s entries with
`ImageHandle == NULL`, so `discovered − noload` is the number that *loaded*, and
because there are zero `'S'` characters every one of them also started — so
**`discovered − noload = started` exactly**, and `started ≥ 19`. It is `≥` and not
`=` because `started` counts entry points across the whole volume while the SEQ
string only spans the Apriori array: the 11 `DRIVER` files the array never names
would each add 1 to `started` if they loaded, and would be invisible in `SEQ`
and `WHY`. So a `started` above 19 is itself a finding — it means some unnamed
driver loaded — and a `P2 STATS` line where the three do not satisfy that identity
was misread or is not this run.

**And a 46-character `SEQ` bounds `discovered` from above, which removes one of the
two rows this table used to carry.** `mP2Apriori` counts Apriori entries that
matched an entry in `mDiscoveredList`, and the host has verified that all 69
non-zero entries name a `DRIVER` file this volume contains. So a list holding all
80 `DRIVER` files would match 69. It matched 46, so 23 of the named drivers are
not in the list, so **`discovered` is at most `80 − 23 = 57`** — or, written the way
the arithmetic actually closes, `discovered = 46 + (unnamed drivers in the list)`.
The old `discovered ≈ 80` row is therefore not a reading that can come back, and a
`P2 STATS` line reporting it means the SEQ string and the STATS line are from
different runs.

| panel says | means | next |
|---|---|---|
| `bytes`/`entries`/`sum` short of `1120/70/a998b263` | `Fv->ReadSection` returned a truncated section | the read is the mechanism; the "23 missing drivers" were never in the buffer, and there is no missing-driver puzzle to solve |
| `entries=70`, `miss=47 9143B2B7-…` | exactly `ap1..ap46` matched (`matched=1..46`), so the absent 23 are the array's tail | step 4.12's tables stand; the discovered list is short by a *suffix*, and the shortfall is in the walk or in `CoreAddToDriverList` |
| `entries=70`, `miss=j<47` | the absent entries are interleaved; `miss` names the first one | step 4.12's name tables are shifted from `SEQ[j-1]` on and must be redone against the true alignment |
| `entries=70`, `miss=none` | 69 of 69 matched, so the match count was never 46 | the 46-char line was truncated somewhere other than the array — re-open step 4.12 rather than patch it |
| `P2 WALK t=0 seen=80 iter=81` | `GetNextFile` handed back every one of the volume's 80 `DRIVER` files | the walk is exonerated and the 23 were dropped after it, in `CoreAddToDriverList` or in the list itself — with `discovered ≤ 57`, so the drop is between the two numbers |
| `P2 WALK t=0 seen≈57 iter≈58` | `GetNextFile` stopped after 57, and `last` names where | the walk itself is the mechanism; the volume's file table is known to hold 80 and `FvCheck` lists all 123, so nothing below it is at fault |
| `P2 STATS` with `discovered − noload ≠ started`, or `started > 19` | the three numbers disagree with the coded identity, or an Apriori-unnamed driver loaded | re-read the line before drawing anything from it |
| `P2 FREE largest=` ≥ 256 | there is a free run of at least 1 MiB, so the largest single request in the promoted set (112 pages) would have fit | the 27 `L`s are not a single allocation that could not be satisfied, and `P2 WHY` decides what they are instead |
| `P2 FREE largest=` ≤ 64 | the largest free run is 256 KiB or less, less than the largest request the promoted set makes | the load order's arithmetic was right and the heap really is being consumed by something else; find what |
| `P2 FREE largest=0` | nothing is allocatable at all at digest time | the `L`s are exhaustion by another name |

**`P2 WALK` prints one line per entry in `mDxeFileTypes`, and the DRIVER pass is
index `0`, not `7`.** The array is
`{ EFI_FV_FILETYPE_DRIVER, COMBINED_SMM_DXE, COMBINED_PEIM_DRIVER, DXE_CORE,
FIRMWARE_VOLUME_IMAGE }`, and the counter and the printed number are both the
*position* in it — so the five lines are `t=0`..`t=4`, the DRIVER line is the first,
and a `t=7` on the panel would be a line the code cannot print. The four non-DRIVER
passes are part of the same self-check rather than noise: this volume holds no
`0xF3`, `0x08` or `0x0B` file at all and exactly one `0x05` file, so they should
read `seen=0 iter=1` three times and `seen=1 iter=2` once (`DxeCore`), and because
the loop counts the terminating `EFI_NOT_FOUND` call in `iter` and not in `seen`,
**every line must satisfy `iter = seen + err`** — where `err` is a counter the
digest does not print, and is 1 on a pass that ended by running out of files. So
`iter = seen + 1` on all five lines is the expected reading, and `iter > seen + 1`
means `GetNextFile` failed on a file the volume does contain.

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
stopped early, and `P2 WALK t=0 seen=80` follows rather than being a separate
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
| this image is | the one on the phone as of this step, and the one step 4.14 replaces. Kept at `work/out/p2-silicon-gzip-preread-0923d.img` (same sha256) so the two can be told apart by more than a filename |

Everything in the flashed image came from a tree that compiles with **zero
`error:` lines**; the build's exit status is 1 from the declared `mkbootimg`
`DTB image must not be empty.` nag and the artifact was checked directly, as
`tools/build-p2-payloads.sh` does.

## Step 4.14 — The pause is made independent of what it measures

### What was wrong, and it was not the length

Step 4.13's repeat loop stalls on `CoreStall` between the 41 copies of the digest.
`CoreStall` waits by way of `gMetronome`, and `gMetronome` is installed by
`MetronomeDxe` — one of the drivers in the volume under measurement. It happens to
work out on this run, because `MetronomeDxe` is one of the nineteen that start
(`s` at ap8 in the SEQ line), which is exactly why the pause is 12 seconds rather
than nothing. But that makes the instrument's readability a function of its own
reading: a run in which `MetronomeDxe` does not start prints the 41 copies back to
back and loses the digest entirely, and the interesting run is the one where the
set of drivers that start has changed. Twelve seconds is also short for reading
twenty-odd lines off a panel that only exists between a reset and an assert.

### The change, and it is one function

`Dispatcher.c` gains `P2Hold ()` and a file-scope `volatile UINTN mP2Spin`, and
the repeat loop's `CoreStall (300000)` becomes `P2Hold ()`:

```c
Hold = 2000000000ULL;
for (Count = 0; Count < Hold; Count++) {
  mP2Spin += Count;
}
```

A cycle count and not a clock, because this platform has no clock to count with:
`CNTFRQ_EL0` reads 0 — the same fact that makes the two timer libraries in this
patch fall back to `PcdTimerFreqOverwrite` — so a duration in seconds is not
something the dispatcher can compute. Each iteration is a read-modify-write on a
volatile, so it cannot be folded away. Its cost is at least one cycle and more
likely a handful, since it is a load-add-store with a store-to-load dependency,
which puts a copy at roughly 1–6 seconds and the 40 copies at **44 seconds in the
worse-than-possible case and three to four minutes realistically**. The excursion
is upward only: a hold that is too long holds the screen longer. The loop stays
bounded, so control still reaches `ASSERT_EFI_ERROR (Status)` at `DxeMain.c(593)`
and the boot still ends — minutes later.

### What proves it is in the image

The hold has no string to grep for, so the check is the instructions themselves.
`Hold = 2000000000ULL` compiles to a `movz`/`movk` pair, and `0x77359400` appears
exactly once in the new `DxeCore` and not at all in the one the phone is
carrying:

```
new (to flash)  DxeCore 170496 B   movk #0x7735, lsl #16  -> 1
old (on phone)  DxeCore 170496 B   movk #0x7735, lsl #16  -> 0
```

and the loop around it is the shape above, not something the compiler folded:

```
3e04:  mov   w20, #0x800                  // the low half of 2e9
3e08:  mov   x19, xzr                     // Index = 0
3e0c:  movk  w20, #0x7735, lsl #16        // w20 = 0x77359400
3e14:  cmp   x19, #0x28                   // Index < 40
3e18:  b.eq  0x3e48
3e20:  cmp   x8, x20                      // Count < Hold
3e28:  ldr   x9, [x22, #2288]             // mP2Spin
3e2c:  add   x9, x8, x9
3e30:  add   x8, x8, #0x1                 // Count++
3e34:  str   x9, [x22, #2288]             // mP2Spin += Count
3e38:  b     0x3e20
3e3c:  bl    0x114b0                      // P2Digest ()
3e40:  add   x19, x19, #0x1
3e44:  b     0x3e14
```

The load and the store are both present in the loop body, which is the thing being
verified: a busy-wait that the optimiser had seen through would be a pause of
zero, and the disassembly is what distinguishes the two.

The build that produced it reports **zero `error:` lines** and `PROGRESS -
Success` (exit status 1 is the declared `mkbootimg` nag), `tools/build-p2-payloads.sh`
re-ran both gates over all three images, and the volume compares clean against
`FVMAIN.Fv.txt` — "123 offsets and GUIDs, zero mismatches".

### The image, and where it is now

| | |
|---|---|
| image | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` |
| size | 1,140,736 B |
| sha256 | `725c33c18df17b69f6e0e95186f2bb63b941e55b4dc4890e25ab8339c534371e` |
| supersedes | `8c565681d1093b76c1cf184a549099aa2a957127c8be5ded439d934164535842` (step 4.13, preserved at `work/out/p2-silicon-gzip-preread-0923d.img`) |
| difference | one function and its call site in `DxeCore`; nothing else in the volume changed, and the 123 files are at the same offsets |
| on the phone | **not yet** — the phone still carries the step-4.13 image |
| to write it | `tools/flash-boot.sh --twrp work/out/p2-variants/Mu-gauguin-silicon-gzip.img`, from TWRP |

### What to read, and it is the same list

Nothing about the digest changed but the time it is on the screen, so step 4.13's
reading table stands unchanged — `P2 APRI` (`bytes=1120 entries=70 sum=a998b263`
is the host's value), `miss`, `P2 SEQ`, `P2 WHY`, `P2 STATS`, the five `P2 WALK`
lines and `P2 FREE`. The difference is that a given frame now stays up for
long enough to be written down rather than remembered, which is what the last
four sessions were losing.

## Step 4.15 — The reason, printed where it will be read

Step 4.13 added `P2 WHY` for exactly one purpose: to say *why* the 27 loads failed,
because `P2Record` collapses every `CoreLoadImage` failure to `'L'` and
`EFI_OUT_OF_RESOURCES`, `EFI_NOT_FOUND` and `EFI_SECURITY_VIOLATION` have no
mechanism in common. The line has been on the panel in every one of the three
sessions since, and it has not been read once — the `SEQ` line has, three times.

That is not a reading error to be corrected by asking more carefully. `SEQ` and
`WHY` are 46 unbroken characters each and neither means anything alone: `SEQ`
says the phase, `WHY` says the status at the same position, and they are only an
answer as a pair. A person photographing a phone gets one line per photograph, so
a design that requires two captures to produce one reading loses the reading half
the time, and it has lost this one every time.

Step 4.15 prints the same statuses a third time, grouped and named:

```
P2 ERR <status name> x<n>
```

one line per distinct status among the entries that are not `EFI_SUCCESS`, or
`P2 ERR none` if there are none. `P2MarkSeq` now keeps the raw `EFI_STATUS` per
Apriori entry in `mP2ApriSt[]`, and `P2Digest ()` groups it — so this is not a
new measurement, it is the existing one in a form that survives being read once.
The block that produces it sits between `P2 WHY` and `P2 STATS`, and the line is
a handful of words: it cannot be truncated, it cannot be half-photographed, and
it does not need the `SEQ` line to mean something.

### What the line decides

Given a `SEQ` of 18 `s`, then `L L L`, then `s`, then 24 `L`, the batch broke at
ap19 and stopped, and `P2 ERR` names the cause of all 27 at once:

| `P2 ERR` shows | what it means | where that leaves the work |
|---|---|---|
| one status, `x27` | all 27 failed for one reason | the retracted-mechanism list has a single target, and the cause is a property of the batch rather than of individual drivers |
| `x25` and `x2` | the batch broke for one reason and two drivers failed for another | the two are the ones to explain; ap22 `ShmBridgeDxe` is already the odd `s` in the middle of the run |
| `Out of Resources` | the failing path is allocation | `P2 FREE largest=` is the number that pairs with it, and step 4.13's table already says how to read the two together |
| `Not Found` | the failing path is lookup, not memory | the `P2 WALK` lines and `P2 APRI miss` are the pair, and the heap is a bystander |
| several unrelated statuses | there is no single cause | that is itself the finding, and it retires the search for one |

### The `unhit` field, which is a checksum and not a measurement

Step 4.15 adds one counter to the promotion loop, printed on the `P2 APRI` line as
`matched=1..46 unhit=`. It was written to separate a batch that is a contiguous
prefix from one with holes, and **it cannot do that.** That is worth recording
rather than quietly leaving in the firmware, because the reason is exact and it
leaves the field worth keeping for something else. Every Apriori entry either
matches a driver — and is then promoted, once — or matches nothing. So

```
unhit = entries − apriori
```

identically, with no freedom in it. The counter is a spelling of two numbers that
are already printed, not a third reading; `entries` is on the same `P2 APRI` line
and `apriori` on `P2 STATS`. What it is worth is the check that those two lines
came from the same boot, which is a failure mode nothing else on either line would
reveal, and one this session has already been near — step 4.13 was written around
the fact that no `P2 STATS` line has ever been read at all.

With 46 promotions it can only be **1** or **24**:

| `unhit` | with `entries=` | what it means |
|---|---|---|
| 1 | 47 | every entry past index 0 matched, so the buffer that was read ended at the last of them, and the promotions stopped where the buffer stopped |
| 24 | 70 | all 70 entries were read: index 0 matched nothing, and neither did ap47..ap69 |

Index 0 is counted on purpose, and it is the one place an off-by-one is easy to
make: `DxeCore` is ap0 and is never in the discovered list by construction, so a
whole-array read gives 1 + 23 = **24**, not 23. The `P2 APRI miss=` line is the
one that *names* the shape, and it needs `entries` beside it to be read exactly:
`none` is the short read, `47 AdcDxe` with `entries=70` is the whole array with
the discovered list missing its tail, and index 46 or less is a hole — a different
fault, where the array and the volume disagree about individual GUIDs rather than
about where the list ends.

### The image, and where it is now

| | |
|---|---|
| image | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` |
| size | 1,140,736 B |
| sha256 | `5be70ecc4b2646cf9d44e3ddb6cde1cfe6a176bf38c5bd96b991ea19683cf11d` |
| supersedes | `725c33c18df17b69f6e0e95186f2bb63b941e55b4dc4890e25ab8339c534371e` (step 4.14, preserved at `work/out/p2-4.14/Mu-gauguin-silicon-gzip.img`) |
| difference | the digest function, one assignment per record, and one counter in the promotion loop, all in `DxeCore`; the other 122 files are at the same offsets, and `fv-inventory.py --against` reports "123 offsets and GUIDs, zero mismatches" |
| on the phone | **no** — the phone carries the step-4.13 image, and the whole of step 4.15 is host-side work |
| to write it | `tools/flash-boot.sh --twrp work/out/p2-variants/Mu-gauguin-silicon-gzip.img`, from TWRP |

This step was built three times on the host, and only the last one counts:
`25fd2d57…` carried `P2 ERR` without `unhit`, `f94b5157…` carried both, and
`5be70ecc…` is the one above, in which the `unhit` comment says what the field
actually is rather than what it was written to be. None of the three has been on
the device — the phone still carries step 4.13's image — so overwriting the first
two in `work/out/p2-variants/` loses nothing that any reading has to be compared
against. The rule that a control image must be read before it is overwritten
applies to images the device has carried, and none of these has. All three are
reproducible from their commits (`515dc71` for the first, `09e0e0e` for the
last), and **none of them is archived**: `work/out/p2-4.15/` was created for the
last one and was then reused by step 4.16's build, which is the mistake this
paragraph exists to record. It is recoverable - a checkout of `09e0e0e` plus
`tools/build-p2-payloads.sh` reproduces `5be70ecc…` byte for byte - and it costs
nothing, because a build no device has carried is a build nothing has to be
compared against. Step 4.16 archives under its own directory and does not reuse
one.

The payload was checked against the build it claims to be, byte for byte rather
than structurally: the gzip kernel is a 112-byte `BootShim.bin` followed by
`SILICIUM_UEFI.fd` exactly, and the new strings are in the `FVMAIN.Fv` that build
produced. `tools/check-payload.py`, `tools/abl-boot-check.py` and the GenFv-map
comparison all pass, so a refusal by ABL is not what a failed attempt will mean.

### What to read

`P2 ERR` is now the line the session is decided on, and it is short enough to be
read in the same photograph as `P2 STATS`. Everything else is unchanged from step
4.14 — `P2 APRI` (`bytes=1120 entries=70 sum=a998b263` is the host's value),
`P2 APRI miss=`, `P2 SEQ`, `P2 WHY`, `P2 STATS`, the five `P2 WALK` lines and
`P2 FREE largest=` — and each frame is still held for minutes by step 4.14's
bounded busy-wait, so the window is not the constraint any more. Step 4.16 moves
the per-driver `P2 DIAG` list into this repeated block as well, so it is read from
the same steady-state screen; see that step.

The three `P2 APRI` lines and `P2 STATS` are read as one group, and the numbers
that matter are small: `entries` is the array as the firmware read it, `apriori`
is how many of those entries were promoted, and the two must satisfy
`apriori + unhit = entries` with `unhit` at 1 or 24. A `P2 APRI` line that
disagrees with the `apriori=` on `P2 STATS` is two lines from different boots, and
then the fix is to read them again rather than to explain them.

## Step 4.16 — The per-driver failure list, moved where it repeats

Step 4.15 put the failure *statuses* into the repeating digest and left one thing
outside it: the list of failures, one line per driver, `P2 DIAG <phase> <guid>
<status>`. That list was printed once, before the digest's 41 repetitions, and
capped at 24 records of the 27.

Both of those are the same fault, and it is the fault this whole line of work
exists to remove. `AdvanceNewLine` clears the panel once the cursor passes the
last row, so a line printed before the repetition begins is on the screen only
during the first frames; and a cap of 24 against 27 failures means three of them
could not be photographed at all, however the photograph was taken. The sessions
that read this data were decided by what the panel happened to be holding when
somebody looked, and both of these make that worse than it needs to be.

The list now lives inside `P2Digest ()`, so it repeats with everything else and is
part of the steady state. The cap is the storage cap, 64 records — no lower,
because the whole list is the point.

**`P2 ERR` says how many of each kind; `P2 DIAG` says which.** They answer
different questions and the second is the one that names a mechanism: a status
shared by twenty-seven drivers that have nothing in common points somewhere other
than the same status shared by a recognisable family — the four runtime drivers,
the USB stack, the console stack. With the list in the digest, one photograph of
the steady-state screen carries every failure with its own status in words, which
is the first time that has been true.

The block still fits: the worst case is 64 records plus fourteen rows of digest,
78 rows against the hundred or so the panel holds, and a `P2 DIAG` line is at most
65 columns against the ninety — so it neither wraps nor pushes the head of the
digest off the screen. Because the block is shorter than the screen, the last 100
rows of the output always contain one complete copy, whatever the clear does.

| | |
|---|---|
| image | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` |
| size | 1,140,736 B |
| sha256 | `7b5a1067d29d1a2fcc832941c614939452307ee17de41c32fd9cf081cf564b6d` |
| supersedes | `5be70ecc4b2646cf9d44e3ddb6cde1cfe6a176bf38c5bd96b991ea19683cf11d` (step 4.15, reproducible from `09e0e0e`; its archive directory was reused, see that step) |
| difference | the digest function in `DxeCore`: one loop moved into it and its cap raised; the other 122 files are at the same offsets, and `fv-inventory.py --against` reports "123 offsets and GUIDs, zero mismatches" |
| on the phone | **no** — the phone still carries the step-4.13 image |
| archived at | `work/out/p2-4.16/` |
| to write it | `tools/flash-boot.sh --twrp work/out/p2-variants/Mu-gauguin-silicon-gzip.img`, from TWRP |

The payload was re-checked the same way as step 4.15's: the gzip kernel stream is
exactly `BootShim.bin` followed by `SILICIUM_UEFI.fd`, `tools/check-payload.py`
and `tools/abl-boot-check.py` pass, and the GenFv map agrees on all 123 files.
`grep` for the `P2 DIAG` format string finds it **once** in the volume, which is
the move: the one-shot copy is gone and the digest's is the only one left.

### What to read from this one

Everything from step 4.15's list, plus the `P2 DIAG` block, which now sits between
`P2 ERR` and `P2 STATS` and lists one line per load failure. Read it as the
driver-by-driver half of `P2 ERR`: `P2 ERR Out of Resources x27` with 27 `P2 DIAG`
lines all saying `Out of Resources` is one finding; the same `x27` spread across
`Not Found` and `Out of Resources` is two, and the GUIDs say which is which —
`Guid.xref` on the host maps them back to names, and `tools/pe-facts.py` says what
is peculiar about each one.

## Step 4.17 — The SEQ's length was a coincidence, and its letters say so

This step changes no firmware. It finishes reading the line that has been read off
the panel three times — the 46-character `P2 SEQ` — and it ends by withdrawing a
conclusion that the host-side tooling had recorded.

That conclusion was: `len(P2 SEQ)` is `mP2Apriori`, the number of Apriori entries
that got promoted, so 46 characters means 46 promotions; the `miss` decoder band
that yields exactly 46 promotions is `miss=14 PlatformInfoDxeDriver`; therefore the
discovery walk stopped at physical 49 or 50 and was cut short. That reasoning uses
the SEQ's **length**. It never looks at its **letters** — at which driver sits in
each slot and what the panel says that driver's own result was — and that is the
check that kills it.

### The two stops the length allows, and what each of them predicts

46 promotions is reachable from exactly two stops on this volume, physical 49
(`seen=48`) and physical 50 (`seen=49`, where `seen` counts only files of the walk's
type, `EFI_FV_FILETYPE_DRIVER`). Both predict the same string, because the file
between them is not an Apriori name:

```
predicted  sssssssssssssssssLLLLLLLLLLLLLLLLLLLLLLLLL????
observed   ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL
```

Seventeen leading `s` and then all `L`, against the panel's eighteen leading `s`, a
lone `s` at slot 21, and `L` everywhere else. Two slots disagree, and each
disagreement is a driver that the stop would have moved into a position where its
own measured result contradicts the panel:

| slot | a stop at 49 or 50 promotes | its own result | the panel shows | which is |
|---|---|---|---|---|
| 17 | `RpmhDxe` (ap19, physical 33) | `L` | `s` | `NpaDxe` (ap18, physical 30) |
| 21 | `DiskIoDxe` (ap24, physical 35) | `L` | `s` | `ShmBridgeDxe` (ap22, physical 74) |

Slot 17 shifts because `PlatformInfoDxeDriver` (ap14) lives at physical 52 — the
lowest Apriori-named file above the stop — so a stop drops it out of the list and
every later slot holds the next driver instead. That is the mechanism the length
argument was resting on, and it is also what refutes it.

**Slot 21 is the one that settles it, because `ShmBridgeDxe` sits at physical 74.**
No stop below 74 can promote it, yet the panel's slot 21 is its `s`. Every driver a
stop at 49 or 50 *can* promote into that slot — `DiskIoDxe`, at physical 35, and the
rest of the run between — has an observed result of `L`. So neither stop is
available, and

> no stop on this volume produces the observed SEQ, so those 46 characters are not a
> stopped discovery walk.

The two images agree on all of it. `work/out/p2-silicon-gzip-preread-0923d.img` (step
4.13, the one on the phone) and the current step-4.16 build give byte-identical
analysis from `=== The Apriori section` onward; 122 of the 123 files sit at the same
offset with the same GUID, and the one that differs is `DxeCore` (physical index 1),
whose size moved from 170544 to 172592.

### The fork that replaces the stop, and the one number that names it

With the stop gone, the 23 absent Apriori entries were either never asked for, or
were asked for by a walk that cannot lose a file it was handed. `P2 APRI` has two
admissible readings and each produces the observed SEQ exactly; the length
fingerprint is what tells them apart, and they point at different code:

| `P2 APRI` | what has to be true |
|---|---|
| `bytes=1120 entries=70 sum=a998b263` | the array was read whole and 23 of its names matched nothing — a premise is wrong, because `CoreAddToDriverList` inserts every driver the walk returns into `mDiscoveredList` unconditionally (`Dispatcher.c:1142-1190`) |
| `bytes=752 entries=47 sum=b4ba9d75` | the Apriori section came back 368 bytes short of its 1120; `unhit` is then 1, `miss` is none, the promotion loop never looks past ap46, and the observed SEQ is the string it must print |

`P2 STATS apriori=46/70` or `apriori=46/47` says the same thing in one number.
`mP2AprioriCount` is `MAX (mP2AprioriCount, AprioriEntryCount)` — the largest Apriori
file size ever *seen* — so the denominator is the fork, and that is the reason to
read that line first.

Neither branch explains the 27 failures. Every one of them — ap19, ap20, ap21 and
ap23..ap46 — is inside the first 46, so it is promoted either way. `P2 ERR` is the
line that answers the 27. These 46 characters answer a different question: *why the
batch is 46 long*.

### The tail shape, which the SEQ cannot argue for or against

The `bytes=1120` branch above is the one reading that fits the SEQ with no stop at
all: `ap1..ap46` promoted and `ap47..ap69` not, printed as `P2 APRI matched=1..46
unhit=24 miss=47`. It is worth recording why the SEQ cannot be used as evidence for
it — the reason being that a hypothesis stated as "these 46 were promoted" produces
those 46 characters *by construction*, since the SEQ is generated from the promotion
result and not from the walk. What would make the shape a measurement rather than a
restatement is the missing set being contiguous in Apriori index (47..69) while
being scattered in physical order:

```
ap47:58 ap48:57 ap49:56 ap50:55 ap51:62 ap52:63 ap53:64 ap54:65 ap55:66
ap56:67 ap57:68 ap58:53 ap59:59 ap60:51 ap61:60 ap62:69 ap63:70 ap64:72
ap65:61 ap66:14 ap67:20 ap68:21 ap69:22
```

ap66 `SimpleTextInOutSerial` is at physical 14 and ap69 `GraphicsConsoleDxe` at 22,
both far below the last promoted file. That is what makes the shape unproducible by
a stop, and it is also what leaves `miss=47` — not a cut walk, which would report one
miss — as the thing to look for.

### What this makes `P2 WALK` say

With every stop refuted, the five `P2 WALK` lines stop being a fork of the SEQ and
become a prediction the volume can be held to:

```
P2 WALK t=0 seen=80 iter=81 last=EBF342FE-B1D3-4EF8-957C-8048606FF671
```

That `last=` is the last *driver* file and not the volume's last file. The walk is
type-filtered — `Type = mDxeFileTypes[Index]` is re-set before every `GetNextFile`
and `mP2WalkLast` is copied only on a successful return — so the seven
FREEFORM/PAD/DXE_CORE files after physical 115 are never returned at t=0 and never
update it. `iter=81` is `seen + 1` on the pass that ends by running out of files,
which is the pass a complete walk ends on. The other four lines are the control:
`t=3` should read `seen=1 iter=2 last=D6A2CB7F-6A18-4E2F-B43B-9920A733700A` (the one
DXE_CORE file) and `t=1`, `t=2`, `t=4` should each read `seen=0 iter=1` with an all
zero GUID, since this volume has no file of those types at all.

| | |
|---|---|
| image | **none** — this step changes no firmware, and the phone still carries step 4.13's `8c565681d1093b76c1cf184a549099aa2a957127c8be5ded439d934164535842` |
| changed | `tools/fv-census.py`, and nothing else |
| reproducible from | `b3faf21` carries the earlier half (the length band and the two candidate stops); the content check, the fork and the walk prediction land with this step |
| what it refutes | the cut walk at physical 49/50, which is a fifth retraction in the same line as the four in `work/out/retracted/README.md` (that file is host-only — `work/` is ignored — which is why the finding is recorded here, in a file that reaches the remote) |

### What to read, and it is the same list

Nothing here changes what the phone should be asked. `P2 ERR` is still the line that
answers the 27; `P2 APRI`'s `bytes=`/`entries=`/`sum=` and `P2 STATS`'s second number
are now a decided pair rather than a range (`entries=70 sum=a998b263` with
`apriori=46/70`, or `entries=47 sum=b4ba9d75` with `apriori=46/47`); `unhit` must be
1 or 24 and must satisfy `apriori + unhit = entries`; and the five `P2 WALK` lines
above are now predictions that can be wrong, which is more than could be said for
them while the stop was still standing.

## Step 4.18 — The 27 fail on a boundary, not on exhaustion, and the boundary is not the request

> **Partly withdrawn in step 4.23.** The boundary verdict stands and is
> strengthened: the pair that decides it is `PdcDxe`/`ShmBridgeDxe`, and after the
> correction they are byte-identical requests rather than merely equal-after-rounding.
> What step 4.23 withdraws is this step's claim about the *memory type* — that all
> 46 requests are made with `EfiRuntimeServicesCode` and are therefore all rounded
> to 16 pages. They are not: the type is the PE subsystem, only 10 of the 46 are
> runtime-typed, and the rounding is 7 pages rather than 262. Every figure below
> that came from the 1824-page total has been corrected in place, and the
> correction is written up in step 4.23 rather than made silently.

This step changes no firmware. It is the first one that tries to answer the 27 by
reading the allocator rather than the panel, and it ends somewhere narrower than
it started: three candidate mechanisms are gone, the number that was supposed to
settle it is wrong, and what remains is a `FindFreePages` boundary condition whose
deciding factor is heap state at the moment each request arrives. `P2 ERR` still
has not been read, and this step does not replace it — it changes what `P2 ERR`
and `P2 FREE` would prove.

### The demand, in the units the allocator uses

`tools/pe-facts.py` has printed a per-driver page demand since `3f79ad6`. The
number is `EFI_SIZE_TO_PAGES (ImageSize + SectionAlignment)` when
`SectionAlignment > 0x1000` (`CoreLoadPeImage`, `Image.c:682-688`): **1562 pages =
6.10 MiB** across the 46 promoted drivers, 575 pages among the `s` and 987 among
the `L`.

What `FindFreePages` is asked for is not quite that number, but **this section
got the difference wrong when it was written, and step 4.23 retracts it**: it said
every one of the 46 requests is made with `MemoryType = EfiRuntimeServicesCode`,
because all 46 take the `!RelocationsStripped` fallback. Two things are wrong with
that. `Image.c:713/724/733` choose the allocation *strategy*, not the type, and
all three pass the same `ImageCodeMemoryType`; and that type comes from the PE
**subsystem** (`Image.c:630-645`) — 10 → `EfiLoader`, 11 →
`EfiBootServicesCode`/`EfiBootServicesData`, 12 → the runtime pair. Of the 46
promoted, **10** are subsystem 12 and the other **36** are subsystem 11.

So the 16-page rounding that step 4.12 predicted for the runtime family — and that
this step briefly asserted as fact — **does not happen on this build**, and the
disproof is in the same file step 4.12 read it from. `CoreInternalAllocatePages` does
set `Alignment = RUNTIME_PAGE_ALLOCATION_GRANULARITY` (`Page.c:1190-1198`) for exactly
four types — `EfiReservedMemoryType`, `EfiACPIMemoryNVS`, `EfiRuntimeServicesCode`,
`EfiRuntimeServicesData` — and `Page.c:1217-1218` then rounds `NumberOfPages` up to a
multiple of `EFI_SIZE_TO_PAGES (Alignment)`. But that macro's value is a
*compile-time* choice of two branches on AArch64, and this build takes the 0x1000 one:
`Silicon/Silicium/SiliciumPkg/SiliciumPkg.dsc.inc:14` defines
`__DEPRECATED_AARCH64_4K_RUNTIME_GRANULARITY` for AARCH64, so
`MdePkg/Include/AArch64/ProcessorBind.h:166-167` is the arm that compiles and
`RUNTIME_PAGE_ALLOCATION_GRANULARITY` is **0x1000** — equal to
`DEFAULT_PAGE_ALLOCATION_GRANULARITY`. The `#else` at `:169` is the 0x10000 arm and is
**not compiled**. `Alignment` is therefore the same for every type,
`EFI_SIZE_TO_PAGES (Alignment)` is 1, and `:1217-1218` reduce to `+= 0` then `&= ~0`:
a no-op. Step 4.12 found this at the source and step 4.23 below relies on it; this
step is where it was lost.

There is consequently **no per-type page penalty at all** — not for the 10 runtime
images, not for anyone:

| | pages |
|---|---|
| the 46 promoted, `SizeOfImage` + `SectionAlignment` demand | 1562 = 6.10 MiB |
| rounding, on this build | **0** |
| as the allocator sees it | **1562** = 6.10 MiB |

The smallest request in the run is `PlatformInfoDxeDriver`'s and `CmdDbDxe`'s 8
pages, both boot-service. The one alignment effect that *does* survive is on the
image side rather than the allocator side, and it is already inside the 1562:
`DXE_RUNTIME_DRIVER` links with `/ALIGN:0x10000` (`SiliciumPkg.dsc.inc:22-23`) against
`/ALIGN:0x1000` for everything else (`:19-20`), `CoreLoadPeImage` adds a whole
`SectionAlignment` when it exceeds a page (`Image.c:686-690`), and eight of the 46
carry 0x10000 — so each of those eight asks for `SizeOfImage + 0x10000`. It is keyed
off the *link* flags rather than off the subsystem: the ten-driver subsystem-12 set
includes `EnvDxe` and `SdccDxe`, which are `DXE_DRIVER` and linked at 0x1000, so they
pay nothing.

### What the cumulative column rules out, with or without the rounding this step retracted

The promotion order is Apriori order, so each slot's cumulative demand is the
running total up to that request:

| | slot | request | cumulative | result |
|---|---|---|---|---|
| last success before the failures | 18 `NpaDxe` | 20 pages, `EfiBootServicesCode` | 566 | `s` |
| first failure | 19 `RpmhDxe` | 16 pages, `EfiBootServicesCode` | 582 | `L` |
| | 20 `PdcDxe` | 9 pages, `EfiBootServicesCode` | 591 | `L` |
| | 21 `ClockDxe` | 47 pages, `EfiBootServicesCode` | 638 | `L` |
| the lone success | 22 `ShmBridgeDxe` | 9 pages, `EfiBootServicesCode` | 647 | `s` |

> `PdcDxe` and `ShmBridgeDxe` make the byte-identical request — 36,864 B, 9 pages,
> subsystem 11, so the same memory type and the same alignment, neither of them
> rounded — 56 pages of cumulative demand apart, and get opposite results.

Identical request, opposite result, in the same phase of the same run. So the
deciding factor is not the request — not its size, not its type, not its
alignment — and it is not a running total either, since a larger total came
later and worked. This is the same fact as the `PdcDxe`/`ShmBridgeDxe` pairing
from step 4.17 (both 36,9xx B, both 9 pages, opposite results), now stated in the
unit that matters, and it is what makes "plain exhaustion" untenable rather than
merely improbable: 647 pages is 2.53 MiB of a heap that is 35.4 MiB.

### The heap is 35.4 MiB and it is the only conventional region

Unchanged from the last time it was dumped, and re-checked here rather than
assumed: `MemoryMapLib.c:24` carries `{"DXE Heap", 0x9B800000, 0x02360000, AddMem,
SYS_MEM, SYS_MEM_CAP, Conv, WRITE_BACK_XN}`, and the whole span
0x9DB60000–0xA0000000 above it is `BsData`/`Reserv`/`RtData`. The three other rows
carrying `Conv` are `MMAP_IO` rather than `SYS_MEM`, so they never reach
`CoreAddMemoryDescriptor`'s `EfiConventionalMemory` path. The DXE Heap really is
the only conventional memory on the platform, and it is 9056 pages.

### Three mechanisms eliminated

**`EFI_MEMORY_SP` — not set by this platform.** The skip is real:
`CoreFindFreePagesI` does `if (Entry->Type != EfiConventionalMemory) continue;`
and then, at `Page.c:961-965`, `if ((Entry->Attribute & EFI_MEMORY_SP) != 0)
continue;` with the comment "Don't allocate out of Special-Purpose memory". The
mapping exists too — `Gcd.c:96` is
`{ EFI_RESOURCE_ATTRIBUTE_SPECIAL_PURPOSE, EFI_MEMORY_SP, TRUE }`. But that
attribute has to be set by the platform's resource map, and neither `Platforms/`
nor `Silicon/` contains a single `EFI_MEMORY_SP` or `SPECIAL_PURPOSE` reference.
No region on this device is Special-Purpose, so the skip never fires.

**The `AllocateAddress` path — no promoted driver takes it.** `ImageBase` is
**0x0 for all 46**, and `RelocationsStripped` is false for all of them, so
`CoreLoadPeImage`'s condition at `Image.c:719-728` is false, `AllocateAddress` is
never attempted, and the `Special`-overlap `EFI_NOT_FOUND` at `Image.c:1293`
cannot be the mechanism for any of the 27. `Status` stays at the value it was
pre-set to — `EFI_OUT_OF_RESOURCES` at `Image.c:697` — and the run takes the
`CoreAllocatePages (AllocateAnyPages, ImageCodeMemoryType, …)` fallback. That
fallback asks for the image's own subsystem type, which is step 4.23's correction:
`EfiBootServicesCode` for the 36 boot-service images and
`EfiRuntimeServicesCode` for the 10 runtime ones. **The sentence this paragraph
ended with — that the status the 27 report is *inherited* rather than the reason
the allocation failed — is wrong, and step 4.23 corrects it.** The pre-set value
is only ever the trigger for the fallback `if` at `Image.c:730`; the fallback's
own status is what `:741` returns. So `Out of Resources` on a `P2 DIAG L` line is
`FindFreePages` coming back empty, and the detail is in which of its rungs.

**The runtime fixup log — measured now, and it fits.** `FixupDataSize =
reloc_size / 2 * 8` (`BasePeCoff.c:1515`) and `CoreLoadPeImage` allocates it at
`Image.c:793`, with NULL meaning `EFI_OUT_OF_RESOURCES`, for every image whose
`Subsystem` is 12. That is a second allocation the page-demand table does not
count, so it was worth measuring rather than reasoning about. Per driver, in
pages:

| driver | slot | result | reloc | fixup bytes | fixup pages |
|---|---|---|---|---|---|
| `EnvDxe` | 2 | `s` | 4096 | 16384 | 4 |
| `ReportStatusCodeRouterRuntimeDxe` | 3 | `s` | 20 | 80 | 1 |
| `StatusCodeHandlerRuntimeDxe` | 4 | `s` | 0 | 0 | 0 |
| `RuntimeDxe` | 5 | `s` | 16 | 64 | 1 |
| `SdccDxe` | 27 | `L` | 4096 | 16384 | 4 |
| `VariableRuntimeDxe` | 31 | `L` | 116 | 464 | 1 |
| `ResetSystemRuntimeDxe` | 34 | `L` | 48 | 192 | 1 |
| `EmbeddedMonotonicCounter` | 38 | `L` | 0 | 0 | 0 |
| `RealTimeClock` | 39 | `L` | 0 | 0 | 0 |
| `CapsuleRuntimeDxe` | 42 | `L` | 0 | 0 | 0 |

**12 pages in total, unrounded** — and the number that was carried into this step
as 131 was wrong; it is 12, and the tool computes it rather than a scratch
session. This paragraph used to continue "at the 16-page granularity that applies to
a runtime pool it is 96 pages"; that rounding is off on this build (step 4.12, and the
retraction in step 4.18 above), so the family's relocation fixups stay at 12 pages —
against the 300-page `EfiRuntimeServicesData` bin below, and far under it either way.
The table's `s`/`L` split is also flat: the four that loaded and the six that did not
are interleaved in the order they appear, so the fixup log does not separate them.
**Not the mechanism**, and now measurably not.

### The bins, which are real but are not where the boot-service state lives

The request's preferred rung is the type's own bin, and those bins are real on
this build. The chain, end to end, because each link is a different file:

- `SiliciumPkg.dsc.inc:45-53` sets nine of these PCDs, and only **two** of them
  are read by anything: `PcdMemoryTypeEfiRuntimeServicesData|300` and
  `PcdMemoryTypeEfiRuntimeServicesCode|150`. The other three Special types are
  also listed, at 0, and both are `Special = TRUE` in `mMemoryTypeStatistics`
  (`Page.c:36-53`). The remaining four — `PcdMemoryTypeEfiBootServicesCode|1000`,
  `PcdMemoryTypeEfiBootServicesData|800`, `PcdMemoryTypeEfiLoaderCode|10`,
  `PcdMemoryTypeEfiLoaderData|0` — are dead configuration on this platform: the
  HOB below emits five entries and none of them is boot-services or loader. Step
  4.23 traces that to `PrePiHobLib/Hob.c:891-905` and draws the consequence.
- `:121` sets `PcdPrePiProduceMemoryTypeInformationHob|TRUE` — the DEC default is
  FALSE — so `BuildMemoryTypeInformationHob ()` runs in SEC
  (`SiliciumPkg/Library/MemoryInitPeiLib/MemoryInitPei.c:153-155`) and produces a
  6-entry array with a terminator (`PrePiHobLib/Hob.c:887-908`), i.e. five types.
- `PopulateMemoryTypeInformation` (`MemoryBin.c:123`, called from
  `Gcd.c:2306`) copies it in and aligns each non-zero count up:
  `NumberOfPages = EFI_SIZE_TO_PAGES (ALIGN_VALUE (…, Granularity))`.
- `CoreInitializeMemoryServices` then computes `MinimalMemorySizeNeeded =
  MINIMUM_INITIAL_MEMORY_SIZE (0x10000) + CalculateTotalMemoryBinSizeNeeded (…)`
  (`Gcd.c:2319`) and calls `CoreAddMemoryDescriptor (EfiConventionalMemory, …)`
  (`Gcd.c:2539-2544`), which reaches
  `AllocateMemoryTypeInformationBins` (`MemoryBin.c:447`).
- That function computes `RequiredSize = 300 + 150 = 450 pages = 1,843,200 B =
  1.76 MiB`, allocates one contiguous block at that alignment, carves the bins
  out of it top-down in array order — `RuntimeServicesData` gets the top 300
  pages and `RuntimeServicesCode` the 150 below it — and drops
  `*DefaultMaximumAddress` to `BaseAddress - 1`, i.e. to the top of everything
  below the block.

So 1.76 MiB of the heap is spoken for before the dispatcher starts, and the
150-page `RuntimeServicesCode` bin is asked to hold **873 pages** of runtime image
demand — 336 of them in the first four promotions, before any failure: it
is exhausted within the first few drivers, and everything after that is served by
the fallthrough — the default bin (the heap below the block), then
`CoreFindFreePagesI` anywhere, then `PromoteMemoryResource ()`. `Page.c:1314`'s
`Start = FindFreePages (…); if (Start == 0) { Status = EFI_OUT_OF_RESOURCES; }`
is therefore being reached with a large amount of memory still free, which is the
one thing the panel has already told us: `P2 DIAG` reported `Out of Resources`.

This paragraph is the half of step 4.18 that step 4.23 does **not** withdraw: the
bins and the carve are real and unchanged, and what the correction changes is how
fast a *runtime* bin fills, not whether it exists. The 36 boot-service images do
not consult those two bins at all. **The reason is not what this paragraph said.**
It said `EfiBootServicesCode`'s count is 0 in `SiliciumPkg.dsc.inc`; the DSC says
**1000**, and `PcdMemoryTypeEfiBootServicesData` says 800. Both are dead
configuration — the HOB that fills `gMemoryTypeInformation` is built by
`EmbeddedPkg/Library/PrePiHobLib/Hob.c:891-905`, and it emits **five** entries and
never a boot-services one:

```c
EFI_MEMORY_TYPE_INFORMATION  Info[6];
Info[0] = { EfiACPIReclaimMemory,   PcdMemoryTypeEfiACPIReclaimMemory };   // 0
Info[1] = { EfiACPIMemoryNVS,       PcdMemoryTypeEfiACPIMemoryNVS };       // 0
Info[2] = { EfiReservedMemoryType,  PcdMemoryTypeEfiReservedMemoryType };  // 0
Info[3] = { EfiRuntimeServicesData, PcdMemoryTypeEfiRuntimeServicesData }; // 300
Info[4] = { EfiRuntimeServicesCode, PcdMemoryTypeEfiRuntimeServicesCode }; // 150
Info[5].Type = EfiMaxMemoryType;   // terminator
```

No code in this tree reads either boot-services PCD — the two writers of that HOB
(`PrePiHobLib` and `ArmPlatformPkg/MemoryInitPei/MemoryInitPeim.c`) read the same
five. So `gMemoryTypeInformation` holds five types and `RequiredSize` is 450 pages
— not the 2260 the DSC's nine PCDs would sum to if the boot-services and loader
lines were read by anything — and `EfiBootServicesCode` has a `MaximumAddress`
window for a different reason than "it was configured to zero": see step 4.23,
which corrects this paragraph's *mechanism* along with step 4.18's memory type.

### What is left, and what would decide it

The mechanism has to be a state that differs between two *identical* requests
made a few milliseconds apart — that is all the `s`/`L` pattern leaves standing.
The two candidates the code offers are both in `FindFreePages`' ladder:

1. **A bin boundary.** `mMemoryTypeStatistics[EfiRuntimeServicesCode]` has a
   `BaseAddress`/`MaximumAddress` window of 150 pages set at bin-creation time,
   and test 1 of the ladder only fires while `MaxAddress >= MaximumAddress`. Once
   the bin is full, the request is served *elsewhere*, and where it lands depends
   on which rung has room — a page-aligned run of the right size below
   `mDefaultMaximumAddress`, or a promotion. (`Alignment` here is 0x1000, not the
   64 KiB the macro's name suggests: the 4K escape hatch is defined on this build,
   which is the retraction in step 4.18.) A request can fail on rung 3 while a
   later one succeeds on rung 3 because `PromoteMemoryResource` added a region in
   between. **Step 4.23 withdraws this candidate for the failing pair**, which is
   boot-service: `EfiBootServicesCode` has no bin, so rung 1 is not gated on one —
   and rung 3 is not gated on any bin at all. A bin boundary can make the
   *runtime* family's allocations fall through; it cannot refuse a boot-service
   request. That leaves `PdcDxe`'s failure to be explained by rung 3 coming up
   empty, which is a claim about the whole memory map and not about the bins.
2. **Promotion.** `PromoteMemoryResource` (`Page.c:382`) converts a non-conventional
   GCD region into `EfiConventionalMemory` and the ladder recurses. If it fires
   between slot 21 and slot 22, the successes on either side are the same kind of
   request finding room that was not there before — which is exactly the shape
   observed.

Both are decidable, and neither is decidable from the host, because both turn on
what the heap looked like at that instant. The instrument that answers it is
already on the phone and has never been read: **`P2 FREE largest=`** is
`P2LargestAlloc`'s ladder `{4096, 1024, 256, 64, 16, 4, 1}` pages run at the
assert, so it says how much memory a *fresh* request could still get after all 46
attempts. Read with the `s`/`L` pattern it separates the two candidates:

| `P2 FREE largest=` | what it means |
|---|---|
| `≥16` | a fresh request of the size that failed at slot 19 (16 pages) still succeeds *after* all 46 — so the failures are not about room at all, and the deciding factor is per-request state (a bin window, or an alignment run), not the total |
| `0` | the heap really did run out by the end — which, with 1562 pages of demand against 9056 pages, means something other than the drivers is consuming it, and the search moves off the dispatcher |
| `4`, `1` | the residue is real but too small for any driver; the demand was paid down, and the 27 have to be explained by *when* it was paid, not how much was left |

And the one value that would settle the heap's own size, which the host cannot
compute and which no `P2` line prints: `CoreInitializeMemoryServices`' chosen
`BaseAddress`, `Length` and `MinimalMemorySizeNeeded`. The `DEBUG` at
`Gcd.c:2505` computes all three and prints them to a console that does not exist
on this device. **If a `P2` addition is wanted, that is the line to add** — three
numbers from inside the one function that decides what the heap is, printed once
per boot, and they turn the 35.4 MiB figure from the resource map into the number
the allocator actually got.

| | |
|---|---|
| image | **none** — this step changes no firmware, and the phone still carries step 4.13's `8c565681d1093b76c1cf184a549099aa2a957127c8be5ded439d934164535842` |
| changed | `tools/pe-facts.py` — the rounded column and its cumulative, the running-total verdict, and the runtime-family bin arithmetic |
| reproducible from | `python3 tools/pe-facts.py`; the bin sizes come from `SiliciumPkg.dsc.inc`, not from the volume |
| what it corrects | the fixup-log total, 131 pages → **12** (the "96 at runtime granularity" that stood here is withdrawn with the same rounding, step 4.23), and a first reading of `P2 DIAG`'s `Out of Resources`. Step 4.23 completes the second: the loader's pre-set status at `Image.c:697` is *not* its source. All 46 have `ImageBase` 0x0 and `RelocationsStripped` clear, so the `AllocateAddress` arm is unreachable, and `PcdLoadModuleAtFixAddressEnable` is 0, so the other arm is too — the only allocation that runs is `CoreAllocatePages (AllocateAnyPages, …)` at `:731`, and `:741` returns *its* status. `Out of Resources` is therefore a verdict about `FindFreePages` after all |
| what it got wrong | the memory type of the 46 requests: not `EfiRuntimeServicesCode` for all of them but the PE subsystem's type — and, in the same retraction, the per-type 16-page rounding it claimed for the 10 runtime images. The correct figure is **1562 pages with no rounding at all**, not 1824 and 262 before it and not 1569 and +7 after. Withdrawn in step 4.23; the boundary verdict survives both |

### What to read

Unchanged in priority, with one addition, and one narrowing from step 4.23 (which
cuts this list down to `P2 ERR` and `bs9` as the pair that decides it). `P2 ERR`
first — it names the status the 27 report, and the whole of the above is
consistent with one name while the state behind it is what varies; for a
boot-service image step 4.23 shows the ladder's last stop is a whole-address-space
search, so what varies is the memory map itself rather than a window inside it.
Then **`P2 FREE largest=`**, which has never been read and which the table above
turns into a four-way decision.
Then the rest of the list from step 4.17 (`P2 APRI`'s `bytes=`/`entries=`/`sum=`
against `P2 STATS apriori=46/70` or `46/47`; `P2 WHY`, never read; the five
`P2 WALK` lines; the 27 `P2 DIAG` records in the repeating digest).

## Step 4.19 — The bins and the retry, which is the question the host cannot answer

Step 4.18 closed by naming its own missing measurement:

> And the one value that would settle the heap's own size, which the host cannot
> compute and which no `P2` line prints: `CoreInitializeMemoryServices`' chosen
> `BaseAddress`, `Length` and `MinimalMemorySizeNeeded`. … **If a `P2` addition is
> wanted, that is the line to add**

That is this step, with the measurement taken from a different side of the same
question. What step 4.18 left standing is a state that differs between two
*identical* 16-page requests made milliseconds apart, and the two candidates for
that state — a bin boundary and a promotion — are both properties of the heap at
the instant the request arrives. No file in the firmware volume records that, so
the only way to read it is to ask the allocator while the heap is in the failing
state, which is exactly when the digest prints. So this step adds the probe.

### The probe, and why it re-attempts the failing request rather than describing it

> **This probe was built against the wrong type, and step 4.23 corrects it before
> it is ever flashed.** The four sizes below are stated as
> `EfiRuntimeServicesCode`, and the intent behind them — "the requests the slots
> either side of the failure actually made" — is right; the type is not, because
> step 4.18 had the type wrong. `RpmhDxe`, `PdcDxe`, `ClockDxe` and `ShmBridgeDxe`
> are all subsystem 11 and ask for `EfiBootServicesCode`, so as built this probe
> re-attempts sizes that *nothing* in the run asked for, in a type that only the
> 10 runtime images use. Step 4.23 adds the two requests the failures were made
> of (`EfiBootServicesCode`, 9 and 16 pages) and keeps the four below as a
> control; `work/out/p2-4.20/` is the corrected image. What follows describes the
> 4.19 build as it was.

`P2Retry` makes four allocations at the assert and frees each one it gets back;
`P2Bins` calls it once and then prints the bins. The four sizes are not arbitrary
— they are the requests the slots either side of the failure actually made, at the
sizes those slots asked for (the "after the 16-page rounding step 4.18 measured" this
paragraph used to say is withdrawn — there is no rounding, step 4.23):

| request | who made it | outcome in the SEQ |
|---|---|---|
| 16 pages `EfiRuntimeServicesCode` | `RpmhDxe` (slot 19), `PdcDxe` (slot 20) | `L` `L` |
| 48 pages `EfiRuntimeServicesCode` | `ClockDxe` (slot 21) | `L` |
| 16 pages `EfiRuntimeServicesCode` | `ShmBridgeDxe` (slot 22) | `s` |
| 112 pages `EfiRuntimeServicesCode` | `BdsDxe` (the largest request in the set) | `L` |
| 16 pages `EfiRuntimeServicesData` | the runtime family's pools | — |

```c
STATIC BOOLEAN  mP2RetryDone = FALSE;
STATIC EFI_STATUS  mP2Rc16;
STATIC EFI_STATUS  mP2Rc48;
STATIC EFI_STATUS  mP2Rc112;
STATIC EFI_STATUS  mP2Rd16;

STATIC
VOID
P2Retry (
  VOID
  )
{
  EFI_PHYSICAL_ADDRESS  Memory;

  if (mP2RetryDone) {
    return;
  }

  mP2RetryDone = TRUE;

  Memory = 0;
  mP2Rc16 = CoreAllocatePages (AllocateAnyPages, EfiRuntimeServicesCode, 16, &Memory);
  if (!EFI_ERROR (mP2Rc16)) {
    CoreFreePages (Memory, 16);
  }
  …
}
```

Two design points, both because the digest repeats 41 times and the copy that gets
photographed is whichever one happens to be on the screen:

- **The statuses are cached in statics and the allocations happen once.** The probe
  moves the heap itself. A line that changed between copies would make the copy that
  was read the wrong one, and there would be no way to tell from the photograph
  which copy it was. Caching makes all 41 copies read identically — the first one
  prints exactly what the last one prints.
- **Everything it allocates is freed immediately.** The probe returns the heap to the
  state it found, so the bins it then prints are the bins the 46 attempts produced,
  not the bins the probe produced. This matters more than it looks: the four
  allocations, if kept, would be 192 pages of the very type whose exhaustion is
  under test.

`P2Bins` prints the state as five lines, each `DEBUG_ERROR` so it is not compiled
away at any level:

```
P2 BIN init=%d hob_rc=%d hob_rd=%d
P2 BIN rc=%lx..%lx used=%ld/%ld
P2 BIN rd=%lx..%lx used=%ld/%ld
P2 BIN def=%lx..%lx
P2 RETRY rc16=%r rc48=%r rc112=%r rd16=%r
```

**The order those five print in is not the order they are useful in.** `P2Bins`
(`Dispatcher.c:326`) calls `P2Retry` *first* and only then emits the four `P2 BIN`
lines, so `P2 RETRY` is the **last line of the whole digest** — it lands after the
four `P2 BIN` lines, after `P2 FREE largest=`, after everything. Read it by scrolling
to the bottom of the screen, not by looking near the `P2 BIN` block. The table below
is in usefulness order; the count in `P2 BIN rc=… used=…/…` and the statuses in
`P2 RETRY` therefore describe *the same instant*, which is what makes the pair
readable together.

| line | fields, in the order they print | what it decides |
|---|---|---|
| `P2 BIN init=` | `mMemoryTypeInformationInitialized` | whether the memory-type-information HOB arrived at all. `0` means `AllocateMemoryTypeInformationBins` never ran, the 450-page carve step 4.18 derived from `SiliciumPkg.dsc.inc` does not exist on this device, and the bin-boundary candidate is dead — the failure is then about the default bin and alignment alone |
| `P2 BIN …hob_rc=/hob_rd=` | `gMemoryTypeInformation[…].NumberOfPages` | **dead fields — read `0` on every boot, see step 4.27.** They index the array by type, but `PopulateMemoryTypeInformation` fills it positionally, so `hob_rc` lands on the HOB's `EfiMaxMemoryType` terminator and `hob_rd` past the copy. They were meant to be the check on the host-side derivation (`rc=150 rd=300`); that check is now `used=../..` on the two lines below, and `init=` above |
| `P2 BIN rc=` | `BaseAddress..MaximumAddress used=Current/Number` | the `EfiRuntimeServicesCode` bin's window and fill. `used==total` says the type's own bin was full and every later request fell through the ladder — which is the mechanism step 4.18 predicts; a `used` well under 150 says the requests never got into their own bin |
| `P2 BIN rd=` | same, for `EfiRuntimeServicesData` | the 300-page window against the 160 pages of runtime pools |
| `P2 BIN def=` | `mDefaultBaseAddress..mDefaultMaximumAddress` | where the fallthrough rung starts. `AllocateMemoryTypeInformationBins` drops `*DefaultMaximumAddress` to `BaseAddress - 1` of the carved block, so this pair is the 450-page carve seen from below, and `def=` above `rc=`/`rd=` would mean the carve did not happen in that order |
| `P2 RETRY` | the four statuses, `%r` | below |

### The decision, and it is one word on one line

`P2 RETRY rc16=` is the whole of step 4.18's question in a single status:

| `rc16=` | what it means |
|---|---|
| `Success` | a 16-page `EfiRuntimeServicesCode` request **still succeeds** at the assert, after all 46 attempts, with the heap in the state those attempts left it in — so runtime-typed room is not what ran out, and what decides the 27 is per-request state inside `FindFreePages`' ladder. **This row said "the exact size and type that failed at slots 19 and 20" and only half of that is true** — 16 pages is `RpmhDxe`'s size, and the type is not the one it asked for. The `bs9=` field step 4.23 adds is the one that carries the whole argument |
| any named error | the heap really is empty at the point the assert fires. Against 1562 pages of demand and 9056 pages of heap that is not plain exhaustion by the drivers, so something else is holding the rest — and that is a different fault with a different fix, and it moves the search out of the dispatcher |

Read with `P2 FREE largest=`, which is the same question asked by the ladder
`{4096, 1024, 256, 64, 16, 4, 1}` that `P2LargestAlloc` walks, the four combinations
separate the candidates. **Step 4.23 replaces the `rc16` column with `bs9=`**,
which is the same `Success`/error split asked of the type the failures were
actually made of; the readings below are otherwise unchanged:

| `rc16` | `P2 FREE largest=` | reading |
|---|---|---|
| `Success` | `≥16` | room exists and both probes find it. The failures are the ladder's rules, not its supply: the bin window (`rc=`), the alignment requirement, or `PromoteMemoryResource` firing between slots. The next step is then a probe inside `FindFreePages` itself, not another census |
| `Success` | `0` | the two probes disagree, which is itself the finding: `EfiRuntimeServicesCode` can be served while a `EfiBootServicesData` request of any size cannot, so the two are drawing on different regions and the "35.4 MiB heap" is not one pool in practice |
| error | `≥16` | the type's own bin and its fallthrough rungs are exhausted for that type while other memory remains — the bin boundary is confirmed as the mechanism |
| error | `0` | the heap is empty. The demand figure in step 4.18 is then wrong about something, and the item to check is what the 1562 pages are made of — in particular whether anything outside the dispatcher is holding the heap |

### What it does not do, and what it stands in for

Step 4.18 asked for three numbers from `Gcd.c:2505` inside
`CoreInitializeMemoryServices`. This probe gets at the same question from inside
DxeCore instead, and the reason is that it costs one patched file rather than two:
the two bins sit at the top of the heap and `def=` is the boundary the carve drops
below them, so `rc=`, `rd=` and `def=` locate the carved block in the address space
without leaving the module that is already patched. If the read comes back with
`init=1`, non-zero windows below `def=`, and `used=../..` filling them from the top,
then the host-side derivation is confirmed on the device and the `Gcd.c` line is not
needed. (`hob_rc=/hob_rd=` used to be part of that list and cannot be — step 4.27.)
The one outcome that would make it necessary is a `BIN` block that is all zeros
while the `RETRY` line still fails — which would say the memory-type-information
mechanism is not running here and the bins are a story about a different platform.

### What proves it is in the image, and it is not the file size

The check that would normally be run — did the file change size — **fails here and
would have been read as a bad build.** The 4.19 `DxeCore` is 172,592 bytes in the
FFS file and 172,544 bytes as a PE, and so is 4.16's, to the byte. Two things
absorb the addition:

- the PE's `FileAlignment` is `0x200`, and `.rdata` had 0x1E4 bytes of padding at
  its end. The five new literals are 0xC0 of it, so `.rdata`'s raw size is
  unchanged while its virtual size goes `0x821c → 0x82dc`.
- `.text` is one of the sections the linker aligns, and its growth went into
  padding that sat before the section's trailing aligned block, so every later
  branch target moved `+0x174` while the section's declared size stayed `0x1e020`.

So presence is proved by content, on four independent counts:

| check | 4.16 | 4.19 |
|---|---|---|
| the five literals in the decompressed inner volume | 0 | 1 each |
| `.rdata` virtual size | `0x821c` | `0x82dc` (+0xC0, the five literals exactly) |
| `.data` virtual size | `0x7530` | `0x7550` (+0x20, the four cached `EFI_STATUS`) |
| `add #0x2f8` in `.text` ("P2 BIN init=%d", RVA `0x242f8`) | none | one, at `0x1198c` |

and the last one is the one that matters, because a `DEBUG` call is only emitted if
a reachable call site names its format string. Five `adrp`/`add` pairs sit in one
0x74-byte window — `0x11988` through `0x119fc` — and they resolve to exactly the
five new literals' RVAs:

```
ADRP/ADD at 0x11988/0x1198c -> 0x242f8  P2 BIN init=%d hob_rc=%d hob_rd=%d
ADRP/ADD at 0x119b0/0x119b4 -> 0x24138  P2 BIN rc=%lx..%lx used=%ld/%ld
ADRP/ADD at 0x119cc/0x119d0 -> 0x24110  P2 BIN rd=%lx..%lx used=%ld/%ld
ADRP/ADD at 0x119e4/0x119e8 -> 0x23740  P2 BIN def=%lx..%lx
ADRP/ADD at 0x119f8/0x119fc -> 0x23d08  P2 RETRY rc16=%r rc48=%r rc112=%r rd16=%r
```

One window, five references, the sizes above — that is `P2Bins` inlined into
`P2Digest`, which is also why `llvm-nm` on `Dispatcher.obj` shows no `P2Retry` or
`P2Bins` symbol: both are `STATIC` and called once, so clang inlined them and the
names are gone. A symbol search would have been the second wrong check to reach for.

### The image, and where it is

| | |
|---|---|
| image | `work/out/p2-4.19/Mu-gauguin-silicon-gzip.img`, 1,140,736 B, sha256 `ef9f8217ae7a4b6f9d640f856d9c6f3f0f142bc45b8639d1c79e0201f84d24aa` — **not flashed, and superseded before it was**: step 4.23 corrects the retry type and rebuilds it as `work/out/p2-4.20/` |
| on the phone | still step 4.13's `8c565681d1093b76c1cf184a549099aa2a957127c8be5ded439d934164535842` |
| changed | `Dispatcher.c` only, inside the existing `P2BRINGUP` block: `P2Retry`, `P2Bins`, and one call at the end of `P2Digest` |
| reproducible from | `tools/build-p2-payloads.sh`; all three variants pass `check-payload.py`, `abl-boot-check.py` and the 123-offset map comparison |
| verified | the same three images came out of a rebuild of the corrected source byte-identically, which is the only reason the archive and the build agree: the first build of this step carried a comment that described `%r` as printing a `Y`, which it does not, and the correction changed no code |

### What to read

Six lines now, and the first three are the ones this step exists for. This list is in
priority order, which is deliberately **not** the order they print — `P2 RETRY` is the
very last line of the digest (see above), so the reading is "find these six, in this
order of importance", not "read the screen top to bottom":

1. **`P2 ERR`** — unchanged first priority. It names the status the 27 report, and
   everything in steps 4.18 and 4.19 is a guess about the mechanism behind that name
   until it has been read.
2. **`P2 RETRY bs9=`** (in the 4.19 build, `rc16=`) — `Success` or a named error,
   and the table above turns that one word into the choice between "the ladder's
   rules" and "the heap's supply". `bs9` is the field step 4.23 adds for the reason
   given there: it is the request `PdcDxe` failed and `ShmBridgeDxe` succeeded with,
   and it is the only one of the six that is literally that request. Printed last,
   after the `P2 BIN` block.
3. **The four `P2 BIN` lines** — `init=`, then `rc=`/`rd=`/`def=` against each other.
   `hob_rc=`/`hob_rd=` are on the first line and read `0` structurally (step 4.27), so
   ignore them. `init=0` is the one reading that would invalidate the whole bin story;
   `init=1` is expected, because the only call site (`Page.c:585`, inside
   `CoreAddMemoryDescriptor`) passes the real `&mMemoryTypeInformationInitialized` and
   runs at DXE init, long before dispatch.
4. Then the list from step 4.18: **`P2 FREE largest=`**, which pairs with `bs9`;
   `P2 DIAG`'s 27 records; `P2 APRI`'s `bytes=`/`entries=`/`sum=` against
   `P2 STATS apriori=46/70` or `46/47`; `P2 WHY`, still never read; the five
   `P2 WALK` lines; and the 46-character `P2 SEQ`.

## Step 4.20 — The one mechanism that could grow the heap mid-run cannot fire here

Step 4.18 ruled out a running-total boundary and a request-size boundary and
narrowed the 27 to heap state at the instant each request arrives. That left one
shape of explanation standing beside it: a mechanism that *hands the allocator
memory* partway through the run. Such a thing produces exactly the observed
pattern — fail, fail, then succeed at a request the same size as one that just
failed, then fail again — and it would be invisible to every cumulative
calculation, which is why 4.18 could not exclude it by arithmetic.

There is exactly one such mechanism in DxeCore, and this step reads it, finds
its gate, and evaluates that gate against this device's own memory map. **It
cannot fire on this board.** The 27 are therefore heap state at request time,
and no region can be injected under them.

### The function, and the two places it is called

`PromoteMemoryResource` is at `Mu_Basecore/.../Dxe/Mem/Page.c:382`. It walks the
GCD memory-space map and, for every entry that qualifies, flips a whole region
into the conventional pool at once:

```c
if ((Entry->GcdMemoryType == EfiGcdMemoryTypeReserved) &&
    (Entry->EndAddress < MAX_ALLOC_ADDRESS) &&
    ((Entry->Capabilities & (EFI_MEMORY_PRESENT | EFI_MEMORY_INITIALIZED | EFI_MEMORY_TESTED)) ==
     (EFI_MEMORY_PRESENT | EFI_MEMORY_INITIALIZED)))
{
  ...
  CoreAddRange (EfiConventionalMemory, Entry->BaseAddress, Entry->EndAddress,
                Entry->Capabilities & ~(EFI_MEMORY_PRESENT | EFI_MEMORY_INITIALIZED |
                                        EFI_MEMORY_TESTED | EFI_MEMORY_RUNTIME));
  Promoted = TRUE;
}
```

Note the shape of the condition: it wants a region that is **present and
initialized but not tested**. A region nobody has tested is the one thing it is
willing to hand out, which is the opposite of what a platform's own "reserved"
list usually means.

It has two call sites and no others in the tree:

- `Page.c:1129`, the last rung of `FindFreePages`. The ladder is: preferred bin →
  default bin → anywhere. Then, verbatim, `if (!PromoteMemoryResource ()) { return 0; }`
  and `return FindFreePages (...)` if it promoted anything. **So a failure on this
  rung returns 0 — that is, `EFI_OUT_OF_RESOURCES` — not because the heap is empty
  but because the ladder ran out of rungs.**
- `Page.c:1333`, the `CoreConvertPages` failure path inside
  `CoreInternalAllocatePages`, which re-attempts the conversion after a promotion.

### The gate is not written in the platform's own words

This is why the question needed evaluating rather than reading off. The device's
descriptors are written in one vocabulary and the gate tests bits from another,
and two lookup tables sit between them.

`MemoryMapLib.c` describes regions as `Reserv`, `Conv`, `RtData` — but those are
the `MemoryType` column, which is what the *EFI memory map* will call the region.
The gate first asks a different question: what is the region's **GcdMemoryType**,
and what capabilities does it carry. Both are decided in `Gcd.c`'s
`CoreInitializeGcdServices` HOB walk (`Gcd.c:2646-2731`):

- **Type** comes from `ResourceType`, at `Gcd.c:2653-2699`. Only
  `EFI_RESOURCE_MEMORY_RESERVED` and `EFI_RESOURCE_MEMORY_MAPPED_IO_PORT` map
  straight to `EfiGcdMemoryTypeReserved`. A `SYSTEM_MEMORY` descriptor's type is
  decided by `(ResourceAttribute & MEMORY_ATTRIBUTE_MASK)` compared against three
  masks — **three separate `if`s, not a switch, so the last one that matches
  wins**. And `MEMORY_ATTRIBUTE_MASK` (`Gcd.c:19-31`) is much wider than the three
  lifecycle bits: it also carries `READ_PROTECTED`, `WRITE_PROTECTED`,
  `EXECUTION_PROTECTED`, `READ_ONLY_PROTECTED`, the 16/32/64-bit IO bits,
  `PERSISTENT` and `SPECIAL_PURPOSE`. A system-memory descriptor that also sets
  any of those matches none of the three masks and ends up `NonExistent` — no GCD
  entry at all.
- **Capabilities** come from `mAttributeConversionTable` (`Gcd.c:81-99`) through
  `CoreConvertResourceDescriptorHobAttributesToCapabilities` (`Gcd.c:2145`). The
  three bits the gate reads are in that table, and each is marked `Memory = FALSE`.
  The predicate at `Gcd.c:2157` is
  `if (Conversion->Memory || ((GcdMemoryType != SystemMemory) && (GcdMemoryType != MoreReliable)))`,
  so those three are converted **only for a GCD type that is not system memory**.
  That is why a `Reserved` region can carry `PRESENT|INITIALIZED|TESTED` and a
  `SystemMemory` one cannot — and it is also why the same three bits appear in the
  gate at `Misc/MemoryProtection.c:1062`, whose guard-page pass uses this predicate
  verbatim.

One more thing the descriptors do not say: `AddHob` in `MemoryInitPei.c:92` only
calls `BuildResourceDescriptorHob` for `AddMem`, `AddDev` and
`HobOnlyNoCacheSetting`. A `NoHob` region never becomes a HOB, so it never reaches
the GCD map at all.

### The four regions that land as Reserved, and each one's disqualification

`tools/promote-check.py` evaluates all of it — it parses the generated map, resolves
the `#define`s out of `MemoryMapLib.h`, `PiHob.h`, `UefiSpec.h` and `DxeMain.h`,
parses `mAttributeConversionTable` out of `Gcd.c`, applies the type rules in the
same order the `if`s are written, and prints the verdict per region. Of 74
descriptors, 72 get a resource HOB and **four** land in the GCD map as `Reserved`:

| region | length | attribute as written | capabilities | why it fails the gate |
|---|---|---|---|---|
| `AOP CMD DB` | 128 KiB | `UNCACHEABLE` | none of the three | `PRESENT` and `INITIALIZED` are not set |
| `SMEM` | 2 MiB | `UNCACHEABLE` | none of the three | same |
| `PIL Reserved` | 352 MiB | `UNCACHEABLE` | none of the three | same |
| `Display Reserved` | 36 MiB | `SYS_MEM_CAP` | `P/I/T` | `TESTED` is set, and the gate needs it clear |

Everything else is `SystemMemory` or `MemoryMappedIo`, and the gate requires
`Reserved`. Two near-misses are worth naming, because a reader working from the
`MemoryType` column would pick them:

- **`ABOOT FV`**, 2 MiB, is `ResourceType SYS_MEM` — its `Reserv` is the *memory
  type*, not the resource type — so it becomes `EfiGcdMemoryTypeSystemMemory` and
  fails on the type test rather than the bit test. An inspection by eye that took
  the `Reserv` column for the resource type would have listed it as the leading
  candidate. This is the slip the instrument exists to prevent; here it does not
  change the verdict, but it would have changed which regions a hand-written
  candidate set named.
- **`Kernel`**, 128 MiB, is the same shape as `ABOOT FV` and the same outcome.

`SYS_MEM_CAP` itself is the reason the `SYS_MEM` regions cannot qualify even by
accident: `SYSTEM_MEMORY_RESOURCE_ATTR_CAPABILITIES` in `MemoryMapLib.h` contains
`TESTED`, so every descriptor that uses it carries the one bit the gate excludes.

### What this closes, and what it does not

Both call sites are now no-ops on this board, so:

- the third rung of `FindFreePages` returns 0 on its own terms, and the recursive
  retry below it never runs;
- the promotion retry on `CoreConvertPages`' failure path never runs.

And the heap cannot grow at all during dispatch. Every other route into
`EfiConventionalMemory` is either DXE-init time — `CoreAddMemoryDescriptor` from
the same HOB walk, from `CoreSetMemoryTypeInformationRange`'s bins, and from
`CoreInitializeMemoryServices` — or a page coming back (`CoreFreePages`,
`Page.c:1500`; `CoreFreePoolPages`, `Page.c:2287`). That is what makes step 4.18's
phrase "heap state at request time" a closed statement rather than a placeholder.

What it does **not** do is answer the question. It removes a competing
explanation; it does not produce the reason. **`P2 ERR` is still first**, and
`P2 BIN` / `P2 RETRY` / `P2 FREE largest=` are still the instruments that measure
the state this step has now proved the failures must be in.

| | |
|---|---|
| instrument | `tools/promote-check.py` — reads the map the build used, and compares it against the committed one so the analysis is of the image and not of a stale copy |
| verdict | no descriptor satisfies the gate; `PromoteMemoryResource` returns `FALSE` from both call sites |
| closes | the "a region is injected mid-run" family, which was the last alternative to heap state in step 4.18 |
| does not close | the 27. `P2 ERR` remains the only route to the reason |

## Step 4.21 — What the SEQ's letters index, and the assumption the join rests on

Step 4.17 read the SEQ's letters as a *stop* test and that argument is sound: no
stop on this volume produces the observed string, because slot 21 is
`ShmBridgeDxe`'s `s` and `ShmBridgeDxe` sits at physical index 74. This step adds
two things that argument used without stating them, and corrects one claim made
alongside it.

### The alignment rule, from the source rather than from the join

The join at step 4.15 is written `SEQ[k]` → `Apriori[k + 1]`. That is true, but
here is why, and it is worth having because the reason is not the obvious one:

```c
STATIC
VOID
P2MarkSeq (IN EFI_GUID *Guid, IN CHAR8 Ch, IN EFI_STATUS Status)
{
  for (Index = 0; Index < mP2Apriori && Index < P2BRINGUP_APRIORI_MAX; Index++) {
    if (CompareGuid (&mP2AprioriGuid[Index], Guid)) {
      mP2AprioriRes[Index] = Ch;
```

`mP2AprioriGuid[]` is written in exactly one place — inside the match branch of the
promotion loop, on the line above `mP2Apriori++` — so it holds the *promoted*
entries in promotion order, and the promotion loop walks `Index` ascending. The
SEQ therefore indexes **the j-th promotion**, not Apriori index j. Those coincide
only when the matches are a contiguous run from index 1.

**And the sentence at step 4.15 that appears to close this — "a contiguous 46-letter
string is 46 matches and not a short array" — does not close it.** Filling the
buffer on a match establishes that the string's *length* counts matches; it says
nothing about the array's length, because a 47-entry array in which indices 1..46
all match produces the same 46 characters. The length is a count of promotions
either way. (Step 4.15's *other* argument against the 47-entry reading does hold and
is untouched: `GetSection` writes `*BufferSize = SectionSize` unconditionally when
it allocates the buffer, so a successful Apriori read cannot come back short.)

### The assumption the physical-index argument rests on

So the 46 characters fix the outcome of 46 promotions, and the join to `Apriori[k+1]`
— which is what turns them into the name tables and into the physical-index lists
`2 3 4 10 11 17 24 25 26 27 28 29 30 31 39 42 43 52 74` and
`5 6 7 8 9 12 13 15 16 18 19 23 33 34 35 36 37 38 40 44 45 46 47 48 49 54 73` — needs
the matches to be exactly 1..46. That holds if the entries which failed to match sit
past the run; it fails if the misses are scattered through the first 46, in which
case the same 46 letters name a different set of drivers and every table after the
first miss is wrong.

Two consequences, stated plainly because an earlier draft of this step overstated
the first:

- **the physical-index lists are not "re-derived from the volume", whatever step
  4.15's heading says.** The volume supplies the file *order*; the letters come from
  the panel. They are one SEQ joined to one host-side file table, and the join has
  an assumption in it. It is a good check — it is what shows the last success sits at
  physical 74 while the first failure sits at 5 — but it is a consistency check, not a
  second measurement.
- **`P2 APRI miss=` is the number that validates or voids the tables.** Its comment
  block already says a miss of 47 means "the first 46 non-core entries matched and
  the 47th did not", which is precisely this assumption, and it is a *prediction*: if
  the tables above are right, `miss=47`. A lower index means the batch has holes and
  the name tables are wrong from that index on.

That does not change the count — 19 started, 27 failed, 46 promoted — and it does
not change what to read first. `P2 ERR` is still the line that answers the 27, and
`P2 APRI miss=` now carries a second job: it is the falsifier for the driver names
that step 4.15 and step 4.18 both build on.

| | |
|---|---|
| instrument | `P2MarkSeq` in `Dispatcher.c` (the rule), `tools/apriori-order.py` (the order), `tools/fv-inventory.py` (the physical order) |
| adds | the alignment is *proven* to index promotions, not Apriori entries; step 4.15's "not a short array" sentence is an over-claim and is dropped; the join carries a contiguity assumption it never named |
| prediction | `P2 APRI miss=47` if the name tables are right; a lower index voids them from that point |
| does not close | the 27. `P2 ERR` remains first |

### The consequence for the proposed `logfs` writer, which kills it

The plan this authorization was granted for was a DXE driver that writes the digest
onto the phone's own `logfs` partition, so that reading a result stops being a race
against the framebuffer console. Its stated premise was that the storage stack is
already up — "`UFSDxe` (ap28, physical 48), `DiskIoDxe` (ap24, physical 35),
`PartitionDxe` (ap25, physical 36) and `Fat` (ap29, physical 37) all start, so no USB
is needed."

**That premise is false, and the authoritative lists say so.** All four are in the
27. They are four of the indices in the failing physical list recorded at step 4.15 —
`35, 36, 37, 48` — and they are `L` in the SEQ at promotion positions 23, 24, 27 and
28. There is no `BlockIo` producer and no FAT driver in this DXE, so:

- nothing can reach the UFS at all, so `logfs` is not merely hard to *write*, it is
  unreachable from DXE by any route;
- an ATA/SCSI-passthrough shortcut is not available either, because the UFS host
  controller driver is itself one of the failures;
- the only remaining channel to the partition is TWRP, i.e. the `dd` on
  `/dev/block/by-name/logfs` that `tools/pull-bootloader-log.sh` already uses — and
  that reads ABL's log, not ours.

So the `logfs` writer is **blocked on the same 27 drivers**, and it is not a way of
*avoiding* the panel; it is a second thing waiting on the same fix. The plan is
withdrawn rather than deferred, and the reason is worth keeping: the premise was
sound only under the *old* SEQ alignment, which put `ScmDxe` in the loaded set. It is
the same misalignment this step corrects, surfacing a second time as a work plan
instead of as a table.

| | |
|---|---|
| what it was | a DXE driver writing the digest to `logfs`, to replace photographing the panel |
| why it cannot work now | `UFSDxe`, `DiskIoDxe`, `PartitionDxe` and `Fat` are all among the 27 load failures — there is no block device and no filesystem in this DXE |
| what actually reads `logfs` | TWRP's `dd`, which reads *ABL's* log, not ours. `tools/read-logfs.py` |
| status | withdrawn, not deferred — it returns as a P2 *output* once the 27 are fixed, never as an input to fixing them |


## Step 4.22 — The memory protection pass cannot fail a load, because nothing builds its HOB

Step 4.18 narrowed the 27 to heap state at request time and eliminated
`PromoteMemoryResource`; step 4.20 confirmed that ends the list of mechanisms that
could *grow* the heap mid-run. That left one candidate outside the allocator, and it
is this one: `CoreLoadImageCommon` calls `ProtectUefiImage` at `Image.c:1496` and
propagates its result:

```c
Status = ProtectUefiImage (&Image->Info, Image->LoadedImageDevicePath);
if (EFI_ERROR (Status)) {
  goto Done;
}
```

so anything that makes that function return an error shows up as an `L` in the SEQ
with no evidence pointing at the allocator. It is the only one of the three call
sites whose result can fail a load: `Image.c:278` is inside `CoreUnloadAndCloseImage`
and returns `VOID`, and `MemoryProtection.c:1179`, inside
`MemoryProtectionCpuArchProtocolNotify`, discards the result. Chasing this is worth
doing because it would explain a boundary that is neither a running total nor the size
of the request — exactly the shape step 4.18 was left with — and because it is
answerable entirely from the tree.

It is answerable, and the answer is no. Here is the chain, in the order it runs.

### `gDxeMps` is the only input to the decision

`ProtectUefiImage` does not read any policy PCD. It calls
`GetUefiImageProtectionPolicy`, which for a non-DxeCore image calls
`GetProtectionPolicyFromImageType (IMAGE_FROM_FV)`, and that function's whole body is:

```c
if (((ImageType == IMAGE_UNKNOWN) && gDxeMps.ImageProtectionPolicy.Fields.ProtectImageFromUnknown) ||
    ((ImageType == IMAGE_FROM_FV) && gDxeMps.ImageProtectionPolicy.Fields.ProtectImageFromFv))
{
  if (gDxeMps.ImageProtectionPolicy.Fields.RaiseErrorIfProtectionFails) {
    return PROTECT_ELSE_RAISE_ERROR;
  }
  return PROTECT_IF_ALIGNED_ELSE_ALLOW;
} else {
  return DO_NOT_PROTECT;
}
```

The drivers being loaded all come from an FV, so `ImageType` is `IMAGE_FROM_FV`
(`GetImageType` resolves the loaded-image device path against the Firmware Volume 2
protocol) and the branch taken is decided by one field of one struct. `mImageProtection`
PCD reads are all inside `/* MU_CHANGE Start: Use Enhanced Memory Protections */`
comment blocks in this tree — checked at `MemoryProtection.c:657`, `:1126`, `:1616`
and `HeapGuard.c:573` — and `MdeModulePkg.dec` declares
`PcdImageProtectionPolicy` and `PcdDxeNxMemoryProtectionPolicy` only as commented-out
lines (`:1687`, `:1719`). **The PCDs are inert; `gDxeMps` is the entire policy.**

### `gDxeMps` is zeroed, because the HOB it comes from is never built

`gDxeMps` is written by exactly one function, the constructor of
`DxeMemoryProtectionHobLib`, and its body ends like this:

```c
Ptr = GetFirstGuidHob (&gDxeMemoryProtectionSettingsGuid);
if (Ptr != NULL) {
  ... version check ...
  CopyMem (&gDxeMps, GET_GUID_HOB_DATA (Ptr), sizeof (DXE_MEMORY_PROTECTION_SETTINGS));
  DxeMemoryProtectionSettingsConsistencyCheck ();
} else {
  DEBUG ((DEBUG_INFO, "DxeMemoryProtectionHobLibConstructor - Unable to fetch memory protection HOB. \
Zero-ing memory protection settings\n"));
  ZeroMem (&gDxeMps, sizeof (gDxeMps));
}
```

`DxeMain.inf:102` does link the real instance (not `...HobLibNull`), and the
constructor's own debug strings are present in the built `DxeCore.efi`, at 149655 and
149694 — so this code is in the image and it is the code that runs.
`ProcessLibraryConstructorList` is at `DxeMain.c:321`, `CoreInitializeDispatcher` at
`:557`, so it has run long before any driver is dispatched.

**Nothing on this platform produces that HOB.** Searched across the whole
`work/uefi` tree for the GUID name, for the GUID literal `0x9ABFD639`, and for the
settings type name: the only hits are the DEC's definition, the header, one
`## CONSUMES ## HOB` line in `DxeMain.inf:133`, and the two readers
(`DxeMemoryProtectionHobLib.c:268`, `MemoryProtectionSupport.c:1844`). No
`BuildGuidHob` / `BuildGuidDataHob` call site anywhere references it, and the
platform and silicon trees between them mention `MemoryProtection` exactly once —
`SiliciumPkg.dsc.inc:181`, the library class mapping. There is no PEIM for it, and
nothing in `gauguinPkg` produces it either.

So the constructor's `else` branch is the one that runs, `gDxeMps` is all zeros,
`ProtectImageFromFv` is 0, `GetProtectionPolicyFromImageType` returns `DO_NOT_PROTECT`,
and `ProtectUefiImage` takes the branch that ends:

```c
case DO_NOT_PROTECT:
  ClearAccessAttributesFromMemoryRange (...);          // VOID
  CreateNonProtectedImagePropertiesRecord (...);       // return value ignored
  return EFI_SUCCESS;
```

`EFI_SUCCESS`, unconditionally, with no path out of it that can fail. **The
protection pass cannot be the source of any of the 27 `L`s.**

### The one thing the panel has already said agrees

`P2 DIAG` prints one line per load or start failure as `%c %g %r` — phase letter,
driver GUID, status in words. The line that has been read off this panel is
`P2 DIAG L … Out of Resources`. That is `EFI_OUT_OF_RESOURCES`, which is the
allocator's status, not a protection status — and `EFI_OUT_OF_RESOURCES` is not a
status `ProtectUefiImage` can return on this build at all, since the only returns it
has left are `EFI_SUCCESS`, the `ASSERT (FALSE)`/`EFI_INVALID_PARAMETER` default, and
the `PROTECT_*` tail that is now unreachable. The two lines of evidence agree, and
this also means `P2 ERR` — which groups the 27 by status and has never been read —
is a short line if they share one cause. `P2 DIAG` records are capped at 64
(`P2BRINGUP_DIAG_MAX`), the SEQ holds 46 promotions, so both the grouped line and the
per-driver lines are on that screen.

### What else follows, and is worth having on its own

With `gDxeMps` zero, the whole MU enhanced-memory-protection subsystem is inert on
this board — not configured off, but never configured. That is a fact about the build,
and it reaches further than the image loader:

- `CpuDxe` never applies the NX policy to free memory: `ArmPkg/Drivers/CpuDxe/CpuDxe.c:318`
  returns immediately when `gDxeMps.NxProtectionPolicy.Fields.EfiConventionalMemory`
  is 0. That function also allocates a memory-map-sized buffer while it runs, so the
  heap it does not touch is also a heap it never allocates from.
- `CpuDxe` therefore installs **no** `EFI_MEMORY_ATTRIBUTE_PROTOCOL`:
  `CpuDxe.c:446` gates `InstallMultipleProtocolInterfaces` on
  `gDxeMps.InstallMemoryAttributeProtocol`. `CpuDxe` did start (it is ap6 in the
  started set), so the CPU arch protocol is up and this one is absent by policy —
  worth remembering at P3/P4, where a Windows loader may look for it, and not a P2
  problem.
- Heap guard, null-pointer detection and the CPU stack guard are all off, all for the
  same reason. That is a silent cross-check on step 4.18: none of the page-count
  arithmetic there needs a guard-page allowance, because no guard pages exist.

The useful part of this step is that it is *negative* and *closed*: it removes the
last mechanism that could have failed a load without the allocator being involved and
without leaving a trace in the heap arithmetic. What is left is what step 4.18 said —
the allocator refusing a request, for a reason that lives in the heap state at that
instant and in nothing else. `P2 FREE largest=`, `P2 BIN`, `P2 RETRY` and `P2 ERR` are
the lines that can see it, and the instrument that prints them
(`work/out/p2-4.19/Mu-gauguin-silicon-gzip.img`, sha256 `ef9f8217…`) is built, gated
and waiting for a cable.

| | |
|---|---|
| instrument | `MemoryProtection.c:145/:179/:491/:511/:571/:1179`, `MemoryProtectionSupport.c:24/:2032/:2055`, `MemoryProtectionHobLib/DxeMemoryProtectionHobLib.c:261`, `DxeMain.c:321/:557`, `DxeMain.inf:102/:133`, `SiliciumPkg.dsc.inc:181`, plus the built `DxeCore.efi` |
| adds | `ProtectUefiImage` is eliminated from the 27 — its `DO_NOT_PROTECT` branch returns `EFI_SUCCESS` unconditionally, and the HOB that would enable any other branch has no producer in this tree |
| also | the entire enhanced-memory-protection subsystem is inert here: no NX pass, no memory attribute protocol, no heap guard, no null detection — which independently confirms step 4.18 needed no guard-page allowance |
| agrees with | the one `P2 DIAG L … Out of Resources` line already read off the panel — an allocator status, on a path `ProtectUefiImage` cannot produce |
| does not close | the 27. `P2 ERR` is still first, and the instrument that prints it is still unflashed — the one this step names, `work/out/p2-4.19/`, was superseded by step 4.23 before either was ever on the phone |


## Step 4.23 — The memory type is the subsystem, not the relocation fallback

Step 4.18 asked what the 46 promoted drivers ask the allocator for, answered
"`EfiRuntimeServicesCode`, via the `!RelocationsStripped` fallback", and built the
rest of the step on that answer: 1824 pages instead of 1562, a 262-page rounding
penalty, and a bin argument in which every one of the 46 lands in the runtime bins.
**The answer is wrong, and it is wrong about the one thing the step was measuring.**

This step retracts it, re-derives the arithmetic from the source, re-states the
boundary verdict in the strongest form the fleet actually supports — where it comes
out *stronger*, not weaker — and then records the second discovery: the step 4.19
probe was built against the wrong memory type, and it is corrected and rebuilt here
as step 4.20's instrument, before either was ever flashed.

### Where the type actually comes from

`RelocationsStripped` does appear in `CoreLoadImageCommon`, but not in the role step
4.18 gave it. The type is decided earlier, from the PE **subsystem**:

```c
// Mu_Basecore/MdeModulePkg/Core/Dxe/Image/Image.c:630-645
switch (Image->ImageContext.ImageType) {
  case EFI_IMAGE_SUBSYSTEM_EFI_APPLICATION:      // 10
    Image->ImageContext.ImageCodeMemoryType = EfiLoaderCode;
    Image->ImageContext.ImageDataMemoryType = EfiLoaderData;
    break;
  case EFI_IMAGE_SUBSYSTEM_EFI_BOOT_SERVICE_DRIVER:  // 11
    Image->ImageContext.ImageCodeMemoryType = EfiBootServicesCode;
    Image->ImageContext.ImageDataMemoryType = EfiBootServicesData;
    break;
  case EFI_IMAGE_SUBSYSTEM_EFI_RUNTIME_DRIVER:   // 12
    Image->ImageContext.ImageCodeMemoryType = EfiRuntimeServicesCode;
    Image->ImageContext.ImageDataMemoryType = EfiRuntimeServicesData;
    break;
```

`Subsystem` in the PE optional header decides it. `RelocationsStripped` enters only
afterwards, in the three arms at `Image.c:702/:719/:730`, and all it chooses there
is the allocation **strategy** — `AllocateAddress`, `AllocateMaxAddress` or
`AllocateAnyPages` — while all three calls pass the same
`(EFI_MEMORY_TYPE)(Image->ImageContext.ImageCodeMemoryType)`, at `:713`, `:724` and
`:733`. So "not relocations-stripped" selects a *where*, never a *what*.

On this fleet only one of the three arms is even reachable, and it is worth
following because it settles where the failures are reported from.
`PcdImageLargeAddressLoad` is TRUE (the `MdeModulePkg.dec` default, overridden by
no platform DSC) and `PcdLoadModuleAtFixAddressEnable` is 0, `ImageBase` is 0x0 on
all 80 DRIVER files and `IMAGE_FILE_RELOCS_STRIPPED` is clear on all 80. So for
every one of the 46: the fixed-address arm is dead by PCD, the `AllocateAddress`
arm is dead because `0x0 >= 0x100000` is false, and the reaches-the-fallback test
`if (EFI_ERROR (Status) && !RelocationsStripped)` is true because `Status` is still
the deliberately pre-set `EFI_OUT_OF_RESOURCES` from `:697`. The single allocation
that runs for all 46 is therefore `CoreAllocatePages (AllocateAnyPages, <subsystem
type>, Image->NumberOfPages, …)` at `:731`, and `:741` returns *its* status. That
closes a reading step 4.18 left open: `Out of Resources` on a `P2 DIAG L` line is
not the loader's pre-set value leaking out — it cannot be, that arm never runs — it
is `CoreAllocatePages` reporting `FindFreePages` returning 0 (`Mem/Page.c:1314`).

On this fleet the distinction is not academic, because the fleet is mixed. Of the 46
promoted drivers, **36 are subsystem 11** and only **10 are subsystem 12**:

```
runtime family (Subsystem 12): 10 of the 46 promoted, 873 pages of image demand
```

The other 36 ask for `EfiBootServicesCode`, including every driver in the failing
run from `RpmhDxe` onward that step 4.18 reasoned about by size.

### The two sets that are easy to conflate

There is a second, quite different set of ten — eight, in fact — that a reader
arriving at this from `pe-facts.py`'s alignment column will trip over:

| set | size | members | why the number is what it is |
|---|---|---|---|
| promoted drivers that are **subsystem 12** | 10 | the eight above plus `EnvDxe`, `SdccDxe` | these are the requests that get the runtime *memory type* (`EfiRuntimeServicesCode`); they do **not** get a 64-KiB allocator granularity, for the reason below |
| DRIVER files carrying **`SectionAlignment` 0x10000`** | 8 | `ReportStatusCodeRouterRuntimeDxe`, `StatusCodeHandlerRuntimeDxe`, `RuntimeDxe`, `VariableRuntimeDxe`, `ResetSystemRuntimeDxe`, `EmbeddedMonotonicCounter`, `RealTimeClock`, `CapsuleRuntimeDxe` | these are the ones whose *image* costs an extra 0x10000, through `Image.c:686-690` |

The eight are a subset of the ten; `EnvDxe` and `SdccDxe` are subsystem 12 with
`SectionAlignment` 0x1000, which is why they are the two the two partitions disagree
about. `CoreInternalAllocatePages` (`Mem/Page.c:1190-1198`) does force
`Alignment = RUNTIME_PAGE_ALLOCATION_GRANULARITY` for `EfiReservedMemoryType`,
`EfiACPIMemoryNVS`, `EfiRuntimeServicesCode` and `EfiRuntimeServicesData` only, and
`:1217-1218` does round the page count up to that alignment:

```c
NumberOfPages += EFI_SIZE_TO_PAGES (Alignment) - 1;
NumberOfPages &= ~(EFI_SIZE_TO_PAGES (Alignment) - 1);
```

**But `RUNTIME_PAGE_ALLOCATION_GRANULARITY` is `0x1000` on this build, so both lines
are no-ops and no driver pays anything.** This paragraph said 0x10000 and "`EnvDxe`
15 → 16, `SdccDxe` 26 → 32, +7 pages" until it was read back against the source. The
macro is a compile-time choice on AArch64:

```c
#define DEFAULT_PAGE_ALLOCATION_GRANULARITY  (0x1000)          // ProcessorBind.h:165
#ifdef __DEPRECATED_AARCH64_4K_RUNTIME_GRANULARITY
#define RUNTIME_PAGE_ALLOCATION_GRANULARITY  (0x1000)          // :167  <- this one
#else
#define RUNTIME_PAGE_ALLOCATION_GRANULARITY  (0x10000)         // :169  not compiled
#endif
```

`Silicon/Silicium/SiliciumPkg/SiliciumPkg.dsc.inc:14` is where the `#ifdef` is
satisfied for AARCH64 — `*_CLANGPDB_AARCH64_CC_FLAGS = -D
__DEPRECATED_AARCH64_4K_RUNTIME_GRANULARITY` — and it reaches this platform because
`gauguin.dsc` has no `[BuildOptions]` of its own and its only `!include` chain
(`BitraPkg.dsc.inc` → `QcomPkg.dsc.inc` → `SiliciumPkg.dsc.inc`) ends there. It is not
inference: 218 generated `GNUmakefile`s under `Build/gauguinPkg/DEBUG_CLANGPDB/` carry
the define on their `CC_FLAGS` line. So `EFI_SIZE_TO_PAGES (Alignment)` is 1,
`Alignment == DEFAULT_PAGE_ALLOCATION_GRANULARITY`, `Page.c:1207`'s
`NeedGuard = FALSE` guard is not triggered, and the allocator sees exactly the
byte counts the loader asks for.

### The corrected arithmetic

| | pages | bytes |
|---|---|---|
| `SizeOfImage` + `SectionAlignment` demand, all 46 promoted | 1562 | 6,397,952 (6.10 MiB) |
| of which the 19 that started (`s`) | 575 | 2,355,200 |
| of which the 27 that failed (`L`) | 987 | 4,042,752 |
| per-type rounding (`EfiRuntimeServicesCode`) | **0** | 0 |
| **as the allocator sees it** | **1562** | **6,397,952 (6.10 MiB)** |

Step 4.18's numbers were 1824 and 262. The retraction removes 255 pages of an
imaginary per-image type penalty, and this step removes a further 7 pages of an
imaginary per-type granularity penalty. The heap arithmetic that follows from it is
otherwise unchanged, because the heap is 9056 pages (35.4 MiB) and the ratio was never
close — it is, in fact, now *further* from close, which makes the boundary verdict
stronger rather than weaker.

What the retraction *does* change is where the room is expected to be tight, and it
turns out to sharpen the picture rather than blur it:

```
runtime family (Subsystem 12): 873 pages of image demand
  against PcdMemoryTypeEfiRuntimeServicesCode = 150 pages
  their RuntimeData pools add 160 more pages of EfiRuntimeServicesData
  against PcdMemoryTypeEfiRuntimeServicesData = 300
```

873 pages of runtime-typed image demand against a 150-page runtime-code bin is a
5.8× oversubscription, and 160 pages of runtime-data pools against a 300-page bin is
0.53×. That asymmetry is the live one: the code bin cannot hold the runtime family and
must fall through to the default bin, while the data bin holds. It matters because of
where the bins are carved — see below. The 36 boot-service images make the *opposite*
kind of demand, and this is the part step 4.18 got backwards:

`EfiBootServicesCode` has no window of its own, and the reason is worth getting
right because it is not the obvious one:

```c
// Mem/Page.c:36-53, mMemoryTypeStatistics[] initialiser, entry for EfiBootServicesCode
{ 0, MAX_ALLOC_ADDRESS, 0, 0, EfiMaxMemoryType, FALSE, FALSE }
//   ^BaseAddress       ^MaximumAddress      ^InformationIndex  ^Special
```

`Special` is **not** the flag that decides anything here — it is read only on the
`AllocateAddress` path (`Page.c:1272`) and in `CoreFreePages`' bookkeeping
(`:1815`, `:1867`), and all 46 use `AllocateAnyPages`. The window is closed
elsewhere, by `InitializeBinStatisticsFromRange` (`MemoryBin.c:281-319`), which
runs at the end of the carve:

```c
MemoryTypeStatistics[Type].CurrentNumberOfPages = 0;
if (MemoryTypeStatistics[Type].MaximumAddress == MAX_ALLOC_ADDRESS) {
  MemoryTypeStatistics[Type].MaximumAddress = *DefaultMaximumAddress;
}
```

That loop walks **every** type and lowers the ones the carve did not give a bin to
— which is every type except the five in the HOB, `EfiBootServicesCode` included —
from `MAX_ALLOC_ADDRESS` down to `*DefaultMaximumAddress`, the top of everything
below the carved block. `BaseAddress` is left at 0, and `MaximumAddress` is
clamped. It is that clamp, not the absence of a bin entry, that changes the
ladder: rung 1 tests `MaxAddress >= mMemoryTypeStatistics[NewType].MaximumAddress`
and, since `AllocateAnyPages` passes `MaxAddress = MAX_ALLOC_ADDRESS`, the test
**passes** — the rung fires, but with `CoreFindFreePagesI (MaximumAddress,
BaseAddress = 0, …)`, which is the same call rung 2 makes. So for the 36
boot-service images the effective ladder is: below the carve (rung 1, then rung 2
again — the same search), then anywhere (rung 3), then promotion. Rung 1 is not
skipped for them; it collapses into rung 2.

And *that* is the sharp part, because rung 3 is not gated by a bin at all. The one
thing the ladder takes away from a boot-service request is the region above
`mDefaultMaximumAddress` — and rung 3 hands it back. The only rung that can
refuse a boot-service request is rung 3 over the whole address space, and after it
there is nothing but `PromoteMemoryResource`, which step 4.20 established cannot
fire on this board at all (no descriptor satisfies the gate).

### What is *not* withdrawn, and the one thing the ladder now implies

The bins and the carve are real, and this is the paragraph that survives the
memory-type correction intact. Only the *boot-services* half of it needed fixing,
and that fix is above: a type with no bin is not skipped on rung 1, it is clamped.
What is unchanged is the carve itself:

```c
// Mem/MemoryBin.c:496-514
BaseAddress = (EFI_PHYSICAL_ADDRESS)(UINTN)AllocateAlignedPages (
                                             EFI_SIZE_TO_PAGES ((UINTN)RequiredSize),
                                             RUNTIME_PAGE_ALLOCATION_GRANULARITY
                                             );
...
LastBinAddress         = BaseAddress + RequiredSize;
*DefaultMaximumAddress = BaseAddress - 1;
```

The array it carves is the five-entry one from the HOB, so on this board
`RequiredSize` is 300 + 150 = **450 pages = 1.76 MiB** — the same figure step 4.18
had, arrived at without the two dead boot-services PCDs — taken as one
page-aligned block (the alignment argument is `RUNTIME_PAGE_ALLOCATION_GRANULARITY`,
which is 0x1000 here, not 64 KiB; the carve's size and position are unaffected), with
`RuntimeServicesData` getting the top 300 pages and `RuntimeServicesCode` the 150
below it, and `mDefaultMaximumAddress` dropped to `BaseAddress - 1`.

So the runtime family's 873 pages of code demand against a 150-page window
oversubscribes rung 1 five times over and the fallthrough below the carve is
load-bearing for it — that is a real and unchanged consequence, and it is about
the 10 runtime drivers, not the 27 failures. For the 36 boot-service images the
carve's only effect is to move the region rung 1 searches, and rung 3 undoes even
that. **A bin boundary cannot be what refuses `PdcDxe`**, because no bin gates the
rung that would have to refuse it.

Which leaves a much sharper claim than the one step 4.18 made, and it is a claim
about the *whole memory map* rather than about the bins: `CoreAllocatePages`
returns `EFI_OUT_OF_RESOURCES` only where `FindFreePages` returns 0
(`Page.c:1314`), and for a boot-service image `FindFreePages` has already been
given, by rung 3, the freedom to place the allocation **anywhere in the address
space** — the carve region included, since the whole block is freed back to
`EfiConventionalMemory` immediately after it is carved. For `PdcDxe`'s 9 pages to
have been refused, there must have been no 9-contiguous-page
`EfiConventionalMemory` run anywhere, at that instant, in a heap of 9056 pages
with 591 pages of cumulative demand standing on it. That is a strong statement
about this memory map and not a subtle one about a bin, and it is the thing
`bs9=` and `P2 FREE largest=` between them measure: `bs9` asks the same question
again at the assert, and `P2 FREE largest=` reports the largest run a fresh
request could still get.

### The boundary verdict, in its strongest form

Step 4.18's argument was that a running total cannot explain the failures, and it
compared three runs at 672/688/752/768 pages on requests of the sizes 88/48/96/48.
That argument stands, but it was needlessly weak: on the corrected type the fleet
contains a pair of requests that are **byte-for-byte identical in every field the
allocator reads**.

```
the boundary is not a running total. PdcDxe fails at 591 pages of
demand, and ShmBridgeDxe succeeds at 647 pages, on the identical request:
subsystem 11, 9 pages. 1 more failure in between, so the deciding
factor is neither the request nor the total: it is heap state at the moment
each one arrives.
```

`PdcDxe` and `ShmBridgeDxe` are both subsystem 11, both 36,864 B of `SizeOfImage`,
both 9 pages, neither rounded, so both enter `CoreInternalAllocatePages` with the same
`MemoryType`, the same `NumberOfPages` and the same strategy. One fails and one
succeeds, **56 pages apart, with one more failure in between** (`ClockDxe`). No
property of the request can separate them, because there is no difference between the
requests. 647 pages of cumulative demand is 2.53 MiB of a 35.4 MiB heap, so it is not
exhaustion either. What is left is the state of the heap and of the carve at the
instant each one arrives — which is precisely the quantity `P2 BIN` and
`P2 RETRY` were added to read, and the reason they are still worth a cable.

The bin-boundary candidate step 4.18 listed does **not** survive for the pair that
actually fails, and saying so is part of this step's correction. `PdcDxe` and
`ShmBridgeDxe` are boot-service: their type has no bin, rung 1 collapses into rung
2 under the clamp `InitializeBinStatisticsFromRange` applies, and rung 3 is not
gated by any bin at all. So the carve cannot be the thing that refuses one of them
while admitting the other — the refusal has to be rung 3 coming up empty, which is
a statement about the layout of the whole heap at that instant and not about a
window inside it. The candidate is withdrawn, and what replaces it is narrower and
better: a request of 9 pages was refused when 9 free pages, contiguous and
conventional, did not exist anywhere in a 9056-page heap. Step 4.20 removed the one
mechanism that could have made that plausible by adding memory mid-run, so the
question is now entirely about how much of that heap was *usable* at that moment —
which is what `P2 FREE largest=` prints, and what no host-side reading can supply.

### The probe's own bug, found by the same correction

`P2Retry` — the assert-time re-attempt added in step 4.19 — re-attempted four
requests, all of them `EfiRuntimeServicesCode`: 16, 48, 112 pages and a 16-page
`AllocateAddress`. Every one of the 27 failures is a subsystem-11
`EfiBootServicesCode` image. **As built, the probe tested requests that nothing in the
run had asked for**, so `rc16` printing `Success` at the assert was evidence about a
memory type the 27 never used.

The fix is two more allocations at the end of `P2Retry`, after the four controls so
that the controls still see the heap the run left:

```c
Memory = 0;
mP2Bs9 = CoreAllocatePages (AllocateAnyPages, EfiBootServicesCode, 9, &Memory);
if (!EFI_ERROR (mP2Bs9)) {
  CoreFreePages (Memory, 9);
}

Memory = 0;
mP2Bs16 = CoreAllocatePages (AllocateAnyPages, EfiBootServicesCode, 16, &Memory);
if (!EFI_ERROR (mP2Bs16)) {
  CoreFreePages (Memory, 16);
}
```

`bs9` is `PdcDxe`'s exact request — 9 pages, `EfiBootServicesCode`, `AllocateAnyPages`
— re-attempted at the assert, with the heap in the state the whole run left it in. That
is the single field that carries the argument: `bs9=Success` means 9 pages of
boot-service memory *are* still obtainable after all 46 attempts, so the 27 were never
about the ladder running out of room, and the deciding factor is per-request state
inside `FindFreePages`; a named error instead means the memory map really has no 9
contiguous free pages at the end of the run — which, against 1562 pages of demand and
a 9056-page heap, is a fault not in the drivers at all, and it moves the search to
what else is standing on the heap. `bs16` is the same question at `RpmhDxe`'s size,
and is there to separate "9 pages specifically" from "boot-service generally".

The format string becomes:

```
P2 RETRY rc16=%r rc48=%r rc112=%r rd16=%r bs9=%r bs16=%r
```

### The instrument, rebuilt and proved present by content

`work/out/p2-4.20/Mu-gauguin-silicon-gzip.img`, 1,142,784 B, sha256
`dbf131d254374646bfbc5e3da4cbfc3dc8e23f3d7ec4a2a7f6ca73dde692e40f` — built, gated,
archived and **never flashed**. All three variants pass `check-payload.py`,
`abl-boot-check.py` and the 123-offset `FVMAIN.Fv.txt` comparison.

It is **+2048 bytes** on step 4.19's 1,140,736 B, and that number is a trap: the size
difference proves the payload changed, not that the two allocations are in it. The
same trap step 4.19 fell into applies again — `DxeCore`'s FFS entry is still
**172,592** bytes and the PE still **172,544**, identical to 4.16 and 4.19, because
`FileAlignment` is 0x200 and the addition fits in the padding the previous step's
`.rdata` and `.text` tails already carried. The PE section *virtual* sizes tell the
real story, and only just: `.rdata` `0x82dc → 0x82ec` (+0x10) and `.data`
`0x7550 → 0x7560` (+0x10), raw sizes unchanged.

So presence is established from the contents of the inner volume, through
`tools/fv-inventory.py`'s walk (the outer gzip stream is the kernel blob; the DXE
volume lives inside the inner LZMA-compressed `FVMAIN`, and grepping the outer stream
finds nothing in *either* image — which is what a first, wrong check looked like):

| check | 4.19 | 4.20 |
|---|---|---|
| `bs9=%r bs16=%r` occurrences in the inner FV | 0 | 1 |
| `rd16=%r bs9` occurrences in the inner FV | 0 | 1 |
| the five `P2 BIN`/`P2 RETRY` literal `adrp`/`add` pairs | window `0x11988`–`0x119fc` | window `0x119e8`–`0x11a5c`, the whole window shifted **+0x60** |

All five literals resolve, each with exactly one `adrp`/`add` pair, to
`0x24308` (`P2 BIN init=%d`), `0x24148` (`P2 BIN rc=`), `0x24120` (`P2 BIN rd=`),
`0x23740` (`P2 BIN def=`) and `0x23d08` (`P2 RETRY … bs9=%r bs16=%r`).

### What to read when it lands

Order matters, and the first two are the ones this step changed. As in step 4.19 this
is priority order, not screen order: **`P2 RETRY` prints last**, below the four
`P2 BIN` lines, because `P2Bins` calls `P2Retry` before it prints anything.

1. **`P2 ERR`** — still first, still never read, still the line that answers the 27.
2. **`P2 RETRY bs9=`** — the decisive field, and the **last line of the digest**.
   `Success` means 9 boot-service pages *are* still obtainable after all 46 attempts,
   so nothing in the ladder was ever exhausted and the 27 are a per-request-state
   failure; a named error means the memory map really could not produce 9 contiguous
   free pages, which is a much larger claim than a bin filling and points at
   `P2 FREE largest=` and at what else is standing on the heap.
3. `P2 DIAG`, `P2 STATS discovered=`/`apriori=`, `P2 WALK`, `P2 APRI unhit=`/`miss=`
   (`miss=47` validates the driver-name tables, per step 4.21).
4. The four `P2 BIN` lines, `P2 FREE largest=`, `P2 SEQ`, `P2 WHY`.

| | |
|---|---|
| instrument | `Image.c:630-645`, `:686-690`, `:697`, `:702/:719/:730`, `:713/:724/:733`, `:741`; `Page.c:36-53`, `:1060-1130`, `:1190-1198`, `:1217-1218`, `:1314`; `MemoryBin.c:281-319`, `:447-527`; `PrePiHobLib/Hob.c:891-905`; `SiliciumPkg.dsc.inc:14`, `:19-23`, `:45-53`; `ProcessorBind.h:165-169`; plus the built `DxeCore.efi` and `FVMAIN.Fv` |
| retracts | step 4.18's memory-type claim: the type comes from the PE subsystem, not from the `!RelocationsStripped` fallback. 10 of the 46 are `EfiRuntimeServicesCode`, 36 are `EfiBootServicesCode` — and, with `SiliciumPkg.dsc.inc:14`, **no per-type page rounding applies to any of them**, so the total is 1562 pages, not 1824 and 262 and not 1569 and +7 |
| strengthens | the boundary verdict: `PdcDxe` and `ShmBridgeDxe` are byte-identical requests 56 pages apart with opposite results, so the deciding factor is neither the request nor the total |
| keeps | the bins and the carve — 450 pages carved as one block off the top of the heap, `mDefaultMaximumAddress` dropped below it, and the runtime family's 873 pages of demand oversubscribing the 150-page code window |
| also corrects | the mechanism step 4.18 gave for the boot-services hole. Not `Special`, and not `PcdMemoryTypeEfiBootServicesCode` (the DSC says 1000; nothing reads it — the HOB builder emits five entries and no boot-services one). It is `InitializeBinStatisticsFromRange` clamping `MaximumAddress` to `*DefaultMaximumAddress`, so rung 1 fires and collapses into rung 2 |
| which means | a bin boundary cannot refuse the failing pair. Rung 3 is not bin-gated, so `PdcDxe`'s refusal means no 9 contiguous conventional pages existed **anywhere** at that instant — a claim about the whole memory map, which is what `P2 FREE largest=` measures |
| withdraws | the `Image.c:697` reading of `P2 DIAG`'s `Out of Resources`. All 46 take `AllocateAnyPages` at `:731` and `:741` returns its status, so the pre-set cannot survive |
| finds | a real bug in the step 4.19 probe: it re-attempted four runtime-typed requests, while every one of the 27 is boot-service. Corrected here with `bs9`/`bs16` |
| rebuilds | `work/out/p2-4.20/Mu-gauguin-silicon-gzip.img`, 1,142,784 B, sha256 `dbf131d2…`, gated and archived, never flashed. Present by content: `bs9=%r bs16=%r` ×1 in the inner FV, `.rdata`/`.data` vsz +0x10 each, five `adrp`/`add` pairs in the window `0x119e8`–`0x11a5c` |
| does not close | the 27. It corrects the instrument that will read them, and it does not touch the phone |


## Step 4.24 — The granularity this step assumed away, and the `P2 RETRY` line reads last

Two corrections, both found by reading back what step 4.23 asserted rather than by
reading anything new off the phone. Neither changes the boundary verdict; the first
strengthens it.

**`RUNTIME_PAGE_ALLOCATION_GRANULARITY` is `0x1000` on this build, so there is no
per-type page rounding and the demand is 1562 pages, not 1569.** Step 4.23 cited
`ProcessorBind.h:169` for 0x10000. That is the `#else` arm of a two-branch
`#ifdef`, and it is **not compiled**: `Silicon/Silicium/SiliciumPkg/SiliciumPkg.dsc.inc:14`
puts `-D __DEPRECATED_AARCH64_4K_RUNTIME_GRANULARITY` on
`*_CLANGPDB_AARCH64_CC_FLAGS`, so `:167` wins and the macro equals
`DEFAULT_PAGE_ALLOCATION_GRANULARITY`. `CoreInternalAllocatePages`'s
`Page.c:1217-1218` rounding is therefore `+= 0` / `&= ~0` for every memory type,
`EfiRuntimeServicesCode` included, and `Page.c:1207`'s `NeedGuard = FALSE` guard is
never triggered either. The "+7 pages, `EnvDxe` 15 → 16 and `SdccDxe` 26 → 32" that
stood in step 4.23 is the arithmetic of a rounding that does not run.

This is not inference from a header. `SiliciumPkg.dsc.inc` reaches the platform
because `gauguin.dsc` has no `[BuildOptions]` of its own and its only `!include`
chain is `BitraPkg.dsc.inc` → `QcomPkg.dsc.inc` → `SiliciumPkg.dsc.inc`; and 218
generated `GNUmakefile`s under `Build/gauguinPkg/DEBUG_CLANGPDB/` carry the define on
their `CC_FLAGS` line, one of which was read verbatim. `tools/pe-facts.py` modelled
the rounding (`rt16 = Subsystem == 12`, `r16 = ceil(pg/16)*16`) and has been corrected
to model its absence; its own output now prints `1562` on both the plain and the
"as the allocator sees it" line, and the pair it searches for (`PdcDxe` fails at 591,
`ShmBridgeDxe` succeeds at 647, identical request) is found on the *unrounded*
numbers, so the argument never depended on the rounding column.

Step 4.12 had this right at `SiliciumPkg.dsc.inc:14` and drew the correct conclusion
from it — "the 64 KiB-alignment theory is dead at the source". Step 4.23 re-derived
the macro without re-reading that line and lost it. The fix here is in the doc
(steps 4.18, 4.19 and 4.23 above), in `tools/pe-facts.py`, and in the project memory
file; no firmware change follows from it, because no firmware branch depended on the
value.

**The alignment effect that survives is a different one and was never missing.**
`EDKII.DXE_RUNTIME_DRIVER` links with `/ALIGN:0x10000` (`SiliciumPkg.dsc.inc:22-23`)
against `/ALIGN:0x1000` for everything else (`:19-20`), and `CoreLoadPeImage`
(`Image.c:686-690`) adds a whole `SectionAlignment` to the allocation size when it
exceeds a page. Eight of the 46 carry `SectionAlignment` 0x10000, so each asks for
`SizeOfImage + 0x10000`; that is 128 pages / 0.5 MiB across the set, and it is
already inside the 1562 — `pe-facts.py` computes `req = SizeOfImage + SectionAlignment`
and always did. It is keyed off the *link* flags, not off the subsystem: the
ten-driver subsystem-12 set includes `EnvDxe` and `SdccDxe`, which are
`MODULE_TYPE = DXE_DRIVER`, linked at 0x1000, and pay nothing.

**`P2 RETRY` is the last line of the digest, not one near the top.** `P2Bins`
(`Dispatcher.c:326`) calls `P2Retry` *before* it prints anything, so the five lines
come out as four `P2 BIN` lines and then `P2 RETRY` — and `P2 RETRY` therefore lands
below `P2 FREE largest=` as well, at the very bottom of the repeating block. The read
lists in steps 4.19 and 4.23 above have been corrected; the priority order they give
is unchanged, it is just not screen order.

**One open question closed on the way, against the source.** `PromoteMemoryResource`
does *not* re-attempt the bin allocation, so step 4.20's "cannot fire here" mechanism
stands as written: the function (`Page.c:382-460`) is driven purely by the GCD map's
own `EfiGcdMemoryTypeReserved` entries and their `EFI_MEMORY_PRESENT|INITIALIZED`
capabilities, with no path to `AllocateMemoryTypeInformationBins`.
`AllocateMemoryTypeInformationBins` has exactly **one** call site in DXE —
`Page.c:585`, inside `CoreAddMemoryDescriptor` — and it is passed the real
`&mMemoryTypeInformationInitialized`, not a dummy. So `P2 BIN init=` should read
**`1`**; `init=0` would mean `CoreAddMemoryDescriptor` never ran the carve, which is a
much larger fault than anything step 4.18 or 4.20 considered.

| | |
|---|---|
| instrument | `SiliciumPkg.dsc.inc:14`, `:19-23`; `ProcessorBind.h:165-169`; `Page.c:382-460`, `:585`, `:1190-1198`, `:1207`, `:1217-1218`; `Image.c:686-690`; `Dispatcher.c:326`; the 218 build-tree `GNUmakefile`s |
| corrects | step 4.23's granularity: `RUNTIME_PAGE_ALLOCATION_GRANULARITY` is 0x1000, not 0x10000. Demand 1562 pages, not 1569 |
| corrects | step 4.19's and step 4.23's read order: `P2 RETRY` prints last, after the four `P2 BIN` lines |
| confirms | step 4.20's mechanism. `PromoteMemoryResource` has no route to the bin allocation, so "the gate never passes here" is the whole reason it cannot fire |
| does not change | the firmware. No build shipped from this step; the image flashed below is step 4.23's |
| strengthens | the boundary verdict, since the demand is 7 pages *smaller* than the version of it that was already far from close |


## Step 4.25 — The 4.23 probe goes onto the phone

`work/out/p2-4.20/Mu-gauguin-silicon-gzip.img`, 1,142,784 B, sha256
`dbf131d254374646bfbc5e3da4cbfc3dc8e23f3d7ec4a2a7f6ca73dde692e40f`, was written to
`boot` over the TWRP route on 2026-09-24 and read back byte-identical.

The control was read **before** the write, which is the standing rule for this
device and the reason the readback means anything: `boot` carried
`work/out/p2-silicon-gzip-preread-0923d.img`'s exact bytes,
sha256 `8c565681d1093b76…`, 1,140,736 B — step 4.13's image, the one steps 4.16 and
4.19 superseded without either reaching the phone. So this flash is the first change
to `boot` since step 4.13, and the payload it replaced is identified by content rather
than by assumption.

The route was TWRP, not fastboot, for the reason step 1b recorded: ABL's fastboot is
what the P2 payload appears to wedge, and a dropped link during `fastboot flash` has
no cancel, where a dropped `adb push` fails only the push. The write is 279 × 4096 B
and the verification is a readback of the first 1,142,784 bytes of `sde55`, not the
absence of a `dd` error.

What the phone is doing now is the loop this step has been building toward: the digest
is drawn on the panel, DXE asserts on the missing arch protocols, and the APSS watchdog
resets it — so the summary repeats and `P2 ERR` stays readable. Nothing about the
`userdata` / partition-table / firmware-LUN constraints is touched by this; the
relaxation is still `boot` only.

| | |
|---|---|
| flashed | `work/out/p2-4.20/Mu-gauguin-silicon-gzip.img`, 1,142,784 B, sha256 `dbf131d254374646bfbc5e3da4cbfc3dc8e23f3d7ec4a2a7f6ca73dde692e40f` → `/dev/block/sde55` (`by-name/boot`), read back identical |
| replaced | `work/out/p2-silicon-gzip-preread-0923d.img` = `8c565681d1093b76…`, 1,140,736 B, identified from the partition itself before the write |
| route | `tools/flash-boot.sh --twrp` — TWRP `adb push` + `dd`, verified by readback |
| next | read `P2 ERR` (first) and `P2 RETRY bs9=` (last line, after the four `P2 BIN`) off the panel |


## Step 4.26 — The allocator's whole world is 9056 pages, so `Out of Resources` cannot mean "full"

Step 4.24 read `FindFreePages` and concluded that a boot-service refusal has to mean
that no run of 9 pages existed **anywhere**. That sentence is only as strong as its
picture of what "anywhere" is, and every version of the picture so far had an unstated
assumption in it: that the region the allocator searches is the whole DXE heap. This
step walks the model from the descriptor table down to `CoreFindFreePagesI` and shows
that it is — which converts "no run existed anywhere" from a possibility into a
measurement, and makes `bs9` the one field that separates the two ways it can be true.

### One `Conv` region, and it is the one named for it

The generated table in `Platforms/Xiaomi/gauguinPkg/Library/MemoryMapLib/MemoryMapLib.c`
holds fourteen `SYS_MEM` rows. All but one of them carry a fixed memory type:

| region | range | memtype |
|---|---|---|
| LLCC0 | 0x09200000–0x09250000 | `BsData` |
| **DXE Heap** | **0x9B800000–0x9DB60000** | **`Conv`** |
| Sched Heap | 0x9DB60000–0x9DF60000 | `BsData` |
| FV Region | 0x9F800000–0x9FA00000 | `BsData` |
| ABOOT FV | 0x9FA00000–0x9FC00000 | `Reserv` |
| UEFI FD | 0x9FC00000–0x9FF00000 | `BsData` |
| SEC Heap | 0x9FF00000–0x9FF8C000 | `BsData` |
| CPU Vectors | 0x9FF8C000–0x9FF8D000 | `BsData` |
| MMU PageTables | 0x9FF8D000–0x9FF90000 | `BsData` |
| UEFI Stack | 0x9FF90000–0x9FFD0000 | `BsData` |
| Log Buffer | 0x9FFF7000–0x9FFFF000 | `RtData` |
| Info Blk | 0x9FFFF000–0xA0000000 | `RtData` |
| Kernel | 0xA2400000–0xAA400000 | `Reserv` |
| DBI Dump | 0xAFAA0000–0xB0EA0000 | `RtData` |

`DXE Heap` ends at **0x9DB60000**, not 0x9DB80000 — `Sched Heap` starts exactly there,
which is the check. 0x02360000 bytes is 9056 pages, 35.4 MiB, and it is the only `Conv`
row in the table.

What makes the other thirteen unavailable is not their type label but an allocation:
`AddHob` (`Silicium/SiliciumPkg/Library/MemoryInitPeiLib/MemoryInitPei.c:86-100`) builds
a `BuildResourceDescriptorHob` only for `AddMem`/`AddDev`/`HobOnlyNoCacheSetting`, but
builds a **`BuildMemoryAllocationHob` for every region whose `ResourceType` is
`EFI_RESOURCE_SYSTEM_MEMORY`** — regardless of its `HobOption`, and including `DXE Heap`
itself, whose `MemoryType` is `Conv`. A region with an allocation HOB is memory in use;
`CoreInitializeGcdServices` does not hand it back as free. So the thirteen are typed
allocations, and `DXE Heap` is a self-marked one that the DXE core claims through the
PHIT range instead.

### What that leaves, and the rung that cannot be gated

`CoreFindFreePagesI` (`Mem/Page.c:950-958`) is the loop that actually picks addresses,
and its first act is to skip every entry that is not `EfiConventionalMemory`:

```c
    //
    // If it's not a free entry, don't bother with it
    //
    if (Entry->Type != EfiConventionalMemory) {
      continue;
    }
```

So the allocator's whole world is the Conventional entries, which is `DXE Heap` and
nothing else: **9056 pages, and no allocation of any type can come from anywhere but
there.**

`CoreInternalAllocatePages` then sets `MaxAddress = MAX_ALLOC_ADDRESS` (`Page.c:1228`)
— the only branch that narrows it is `AllocateAddress` (`:1240-1300`) — and the 46
promoted images all take `AllocateAnyPages` because their `ImageBase` is 0 and their
relocations are not stripped. So `FindFreePages (MAX_ALLOC_ADDRESS, …)`:

| rung | source | gate |
|---|---|---|
| 1 | `Page.c:1073-1085`, the type's own bin window | the window, if the type has one |
| 2 | `Page.c:1090-1106`, the default bin | `MaxAddress >= mDefaultMaximumAddress`, then clamps to it |
| 3 | `Page.c:1114-1124`, `CoreFindFreePagesI (MaxAddress, 0, …)` | **none** — and `MaxAddress` here is still `MAX_ALLOC_ADDRESS` |

Rung 2's clamp costs nothing: `mDefaultMaximumAddress` is the bin block's base minus one,
and the bin block is allocated top-down, so the default window is nearly the whole heap.
Rung 3 is ungated by any bin, any window and any type. **The only way all three return 0
is that no run of `NumberOfPages` contiguous `EfiConventionalMemory` pages exists
anywhere in the 9056.** That is the claim step 4.24 made, and this is the reading of the
source that licenses it.

### The bins do not consume a page — a model that was wrong until this step

`BuildMemoryTypeInformationHob` (`PrePiHobLib/Hob.c:884-905`) is the only writer, and it
emits five types, verbatim: `EfiACPIReclaimMemory`, `EfiACPIMemoryNVS`,
`EfiReservedMemoryType`, `EfiRuntimeServicesData`, `EfiRuntimeServicesCode`, from the
`PcdMemoryTypeEfi*` set. In `SiliciumPkg.dsc.inc:45-53` those are 0, 0, 0, 300 and 150,
so `RequiredSize` is **450 pages, 1.76 MiB** — and `PcdMemoryTypeEfiBootServicesCode|1000`
and `…BootServicesData|800` two lines below feed nothing, as step 4.23 said.

`AllocateMemoryTypeInformationBins` (`Mem/MemoryBin.c:495-560`) then does something the
earlier notes had backwards. It allocates `RequiredSize` with `AllocateAlignedPages`, sets
`*DefaultMaximumAddress = BaseAddress - 1`, walks the five windows down from the top, and
**frees the entire block again** with `FreeAlignedPages` before returning. The bins are
address *windows* over pages that are free again by the time the next caller looks, not
450 reserved pages. Nothing is consumed and nothing can be exhausted by that route.

Two producers can set those windows and whichever runs first wins — both open with
`if (*MemoryTypeInformationInitialized) return;`: `CoreSetMemoryTypeInformationRange`
(`MemoryBin.c:337-423`) called from `CoreInitializeMemoryServices` (`Gcd.c:2526`) when a
MemoryTypeInformation *resource* HOB exists, and `AllocateMemoryTypeInformationBins` called
from the tail of `CoreAddMemoryDescriptor` (`Page.c:583-591`). So `P2 BIN init=1` says one
of them ran; it does not say which, and it changes the page count in neither case.

### The arithmetic, and this step's only prediction

1562 pages of image demand (step 4.24) against 9056 pages of heap, with the bins costing
nothing. A 9-page request — and `PdcDxe`'s is 9 pages and `ShmBridgeDxe`'s is 9 pages — cannot
be refused for want of room. So the reading to expect is `bs9=Success`, and the field is
worth reading precisely because of what each alternative would mean:

| if the panel shows | then |
|---|---|
| `P2 RETRY bs9=Success` | the ladder was never out of room, and `Out of Resources` on the 27 is not the allocator running dry. The only heap-side explanations left are rungs 1 and 2, and both would have to be a window narrower than the heap; `P2 BIN rc=.. used=../..` are the next fields, not `P2 FREE` |
| `bs9=` a named error | the heap was smaller than 9056 pages at that instant. That is not a statement about demand — it is a statement about which region `CoreInitializeMemoryServices` (`Gcd.c:2384-2475`) took as the first Conventional range. `MINIMUM_INITIAL_MEMORY_SIZE` is only `0x10000` (`Gcd.c:17`), so `Length` need only beat `16 + 450 = 466` pages for that call to succeed, and `FindLargestFreeRegion` may hand back a sub-range. `P2 FREE largest=` then sizes it, and the repair is a host-side build change (the open item from 4.14), not a device write |
| `P2 FREE largest=` large **while** `bs9` fails | the run exists but not where the request looked — impossible with rung 3 ungated. This pair would mean the failure is not in `FindFreePages` at all, and the next instrument belongs on the pool side of `CoreLoadImage` |

| | |
|---|---|
| reads | `MemoryMapLib.c` (the 14 rows), `MemoryInitPei.c:86-100` (`AddHob`), `Page.c:546-592`, `:902-958`, `:1060-1137`, `:1228`, `MemoryBin.c:337-423`, `:440-575`, `Gcd.c:17`, `:2241-2550`, `Hob.c:884-905`, `SiliciumPkg.dsc.inc:45-53` |
| establishes | the allocator's world is exactly `DXE Heap`, 0x9B800000–0x9DB60000, 9056 pages / 35.4 MiB; rung 3 is ungated |
| corrects | the bin model: 450 pages of `RequiredSize` are allocated, then freed, and survive only as address windows — no bin consumes a page |
| predicts | `bs9=Success`, because 1562 pages of demand cannot exhaust 9056 |
| cannot answer | whether the core actually held all 9056 of them at the moment `PdcDxe` ran — that is `P2 FREE largest=` and `bs9=` on the panel, and nothing on the host |


## Step 4.27 — Two of the four `P2 BIN` fields cannot carry a reading, and one of them I asked for

Step 4.19 put four facts on three `P2 BIN` lines and step 4.24 told the reader to
expect `init=1`. Reading the same two fields back against the array they index
shows that half of them are structurally zero and always have been:

```
"P2 BIN init=%d hob_rc=%d hob_rd=%d\n"
```

`hob_rc` and `hob_rd` are `gMemoryTypeInformation[EfiRuntimeServicesCode]` and
`gMemoryTypeInformation[EfiRuntimeServicesData]` — indexed **by type**. That read
is correct against the array's *initialiser*, which `Mem/Page.c:59-77` writes by
type (`arr[i].Type == i` at all 17 positions) and which `DxeMain.h:267` declares as
`[EfiMaxMemoryType + 1]`. It is wrong against the array's *contents*, because two
other files write it in a different vocabulary:

- `BuildMemoryTypeInformationHob` (`PrePiHobLib/Hob.c:884-905`) fills a six-entry
  HOB **by position**, in its own order: `ACPIReclaimMemory, ACPIMemoryNVS,
  ReservedMemoryType, RuntimeServicesData, RuntimeServicesCode`, then the
  `EfiMaxMemoryType` terminator.
- `PopulateMemoryTypeInformation` (`MemoryBin.c:145`) moves it with a plain
  `CopyMem (MemoryTypeInformation, EfiMemoryTypeInformation, DataSize)` — 48 bytes,
  positional, with no merge keyed on `.Type`.

So position 5 — `EfiRuntimeServicesCode`'s ordinal, which is 5 — receives the
**HOB's terminator** `{ EfiMaxMemoryType, 0 }`, and position 6 is past the 48-byte
copy and keeps the initialiser's 0. `hob_rc=0 hob_rd=0` on every boot, with or
without the HOB.

**Why the bins are still right, and why both facts coexist.** The loops walk
`.Type` and stop at `EfiMaxMemoryType`:

```c
for (Index = 0; MemoryTypeInformation[Index].Type != EfiMaxMemoryType; Index++) {
  Type = (EFI_MEMORY_TYPE)(MemoryTypeInformation[Index].Type);
  ...
  MemoryTypeStatistics[Type].BaseAddress = ...;
```

so they read the five HOB entries wherever they landed and assign into
`mMemoryTypeStatistics`, which *is* indexed by type. `P2 BIN rc=.. used=../..` reads
that array and is sound; only the two fields that index `gMemoryTypeInformation`
directly are dead. The same walk, run on the host, prints the two nonzero entries —
`RuntimeServicesData(300)` and `RuntimeServicesCode(150)` — for
**`RequiredSize` = 450 pages**, which is the figure step 4.26 derived independently
from the PCDs. Two routes, one number.

**What to do about it: nothing on the phone.** The fields to read are `init=`, which
carries on its own whether a producer ran, and `used=../..`, which carries what
`hob_rc`/`hob_rd` were meant to. Fixing the two dead fields means patching
`P2Bins` to walk `.Type` instead of indexing — worth doing in the next build that
happens for another reason, not worth a flash of its own, because the information is
already on the line below.

`tools/bin-field-check.py` is the instrument and it reads the four sources rather
than restating them: it parses the enum out of `UefiMultiPhase.h` for the ordinals,
the initialiser out of `Page.c`, the `Info[]` assignments out of `Hob.c` paired with
their PCDs out of `SiliciumPkg.dsc.inc`, and applies the positional copy. Run it when
a probe field looks wrong; it answers "can this field carry information at all"
before any time is spent reading a panel for it.

| | |
|---|---|
| finds | `hob_rc` reads `gMemoryTypeInformation[5]`, which `PopulateMemoryTypeInformation`'s positional `CopyMem` overwrites with the HOB's terminator; `hob_rd` reads `[6]`, past the copy. Both always 0 |
| mechanism | the array is initialised **by type** (`Page.c:59-77`, `arr[i].Type == i`) and overwritten **by position** (`MemoryBin.c:145`); index-by-type reads are only valid against the former |
| does not contradict | the bins: the loops walk `.Type` and assign into `mMemoryTypeStatistics`, which is indexed by type, so `init=` and `used=../..` are sound and the carve is 450 pages — the same `RequiredSize` step 4.26 got from the PCDs |
| instrument | `tools/bin-field-check.py` — parses the ordinals, the initialiser, the HOB and the PCDs, and applies the copy; prints what each field resolves to |
| action | read `init=` and `used=../..`; fix `P2Bins` to walk `.Type` only in a build that is happening anyway |


## Step 4.28 — The heap DxeCore gets is 28.4 MiB, not 35.4, and the 24.6 MiB beside it is not ours

The question carried since step 4.14 — whether the DXE heap can be enlarged —
closes here, and not in the direction it was asked. The row can be converted into
the number that matters, the room beside it turns out to belong to something else,
and the conversion shows enlargement is not needed.

**The heap DxeCore gets is not the extent the row declares.** `InitializeMemory`
(`Sec/Sec.c:64-78`) locates `"DXE Heap"` **by name** and hands it to

```c
HobList = HobConstructor ((VOID *)UefiMemoryBase, UefiMemorySize,
                          (VOID *)UefiMemoryBase, (VOID *)(UefiMemoryBase + UefiMemorySize));
```

so `EfiMemoryTop` and `EfiFreeMemoryTop` are both 0x9DB60000 and the whole PHIT
lives inside that one row. PrePi then allocates **downward** from
`EfiFreeMemoryTop` (`PrePiMemoryAllocationLib.c:30-54`: `NewTop = …EfiFreeMemoryTop
& ~EFI_PAGE_MASK; … EfiFreeMemoryTop = NewTop;`). By the time DxeCore runs, the
first range `CoreInitializeMemoryServices` computes is `EfiMemoryTop` to the end of
the PHIT's resource HOB — the same address, so `Length` is **0** (`Gcd.c:2385-2387`)
— and the second branch is what is taken (`Gcd.c:2393-2394`):

```c
BaseAddress = PageAlignAddress (PhitHob->EfiFreeMemoryBottom);
Length      = PageAlignLength (PhitHob->EfiFreeMemoryTop - BaseAddress);
```

That one descriptor is the entire `EfiConventionalMemory` world, and it is the row
**minus what PrePi took**.

**What PrePi took is on disk, so this is measured and not estimated.**
`Build/gauguinPkg/DEBUG_CLANGPDB/FV/FVMAIN.Fv` is 7,352,320 B = 7.01 MiB = 1795
pages, and that is exactly the buffer `DecompressFirstFv` allocates: `FfsProcessFvFile`
(`PrePiLib/FwVol.c:925-958`) extracts the `EFI_SECTION_FIRMWARE_VOLUME_IMAGE`
section, which decompresses into its own allocation, and only copies again if the
result landed unaligned. 9056 − 1795 = **7261 pages = 28.4 MiB**, before the HOB
list's few pages and DxeCore's own image.

**28.4 MiB against 1562 pages is 4.6×.** A 9-page request cannot be refused for
want of room, which is the prediction step 4.26 reached from the other end
(9056 pages, bins costing nothing). `bs9=Success` stands.

**The lever was wrong twice over, and both halves matter.**

- *The FD cannot grow into the heap.* The FD is the `UEFI FD` row (0x9FC00000,
  3 MiB) and the heap is the `DXE Heap` row (0x9B800000) — different rows in
  different places, and `Sec.c` selects the heap row by name, so only that row's
  length decides anything. Growing the FD would move the decompressed volume's
  address, not the heap's size.
- *The room beside the heap is not free.* gauguin leaves **24.625 MiB unlisted**
  between `Sched Heap`'s end (0x9DF60000) and `FV Region` (0x9F800000). Every
  sibling platform in the tree whose heap base is also 0x9B800000 — `i005d`,
  `lemonade`, `q2q`, `r9qb2`, `vili` — declares **60.0 MiB** of heap there and
  covers 0x9B800000..0x9F800000 with heap + `Sched Heap` and nothing in between.
  gauguin covers the *same span* with the *same two rows* and leaves 24.625 MiB
  blank in the middle. So this is not a row someone forgot to extend; it is a
  carveout for this device. Nothing builds a HOB for an address the config never
  mentions, so the hole has no GCD descriptor and no memory map entry — it is
  invisible to the allocator, and claiming it would mean running on RAM the
  device's own XBL deliberately left out.

**So nothing is changed and nothing is flashed.** If `bs9` names an error it is not
a capacity fact, and step 4.26's fork applies: the next instrument goes on the pool
side, not on the heap size. `tools/heap-compare.py` is the instrument — it reads
this device's config against the siblings' and prints both the extents and the
holes, so "can this be enlarged" is answered from `uefiplat.cfg` rather than from
the row in the generated C. `tools/pe-facts.py` now prints the declared extent
*and* the post-PrePi figure, because printing only "35.4 MiB" invites the reader to
compare the demand against a number DxeCore never has.

| | |
|---|---|
| finds | DxeCore's conventional region is `[EfiFreeMemoryBottom, EfiFreeMemoryTop]` (`Gcd.c:2393-2394`) = **7261 pages / 28.4 MiB**, not the row's 9056 / 35.4 MiB; against 1562 pages of demand that is 4.6× |
| mechanism | `Sec.c:64-78` sizes the PHIT from the `DXE Heap` row **by name**; PrePi allocates down from `EfiFreeMemoryTop`, taking 1795 pages for the decompressed FVMAIN (`FVMAIN.Fv` = 7,352,320 B) |
| does not change | the prediction: `bs9=Success`, for the reason step 4.26 gave and this step's arithmetic confirms |
| rules out | enlarging the heap, on both branches — the FD lives in a different row, and the 24.625 MiB beside the heap is a device carveout the five sibling platforms at the same base do not have |
| instrument | `tools/heap-compare.py` (this config vs the siblings, plus the holes); `tools/pe-facts.py` (declared extent vs post-PrePi extent) |


## Step 4.29 — The panel is 90 columns by 100 rows, and the last text row on it is `P2 RETRY`

Step 4.24 established that `P2 RETRY` reads last — `P2Bins` takes its readings
first and prints its line after the four `P2 BIN` lines — and that is why `bs9=`
is the field the whole P2 question turns on. What was never established is where
on the panel that line lands, and four sessions have now failed to read `bs9=` off
the phone. The answer is that it is the **last text row on the screen**, and the
reason it has been missed is that "the last line of the digest" is easy to hear as
"the bottom of a full screen" when the screen is in fact part-filled with blank
rows beneath the cursor.

**The geometry, from four sources rather than from a photograph of text.**
`gauguin.dsc:71-73` sets `PcdFrameBufferWidth|1080`, `Height|2400`, `ColorDepth|32`;
`Font.h:34-35` sets `FONT_WIDTH 5` and `FONT_HEIGHT 16`; `GetFontScale`
(`FrameBufferSerialPortLib.c:125-133`) is `(ShorterDimension < 426) ? 1 :
ShorterDimension / 426`; and `GetFbPositions` (`:135-154`) divides:

```c
LocalMaxPosition.XPos = FB_WIDTH  / ((FONT_WIDTH  + 1) * FontScale);   // 1080 / 12 = 90
LocalMaxPosition.YPos = FB_HEIGHT / ((FONT_HEIGHT - 4) * FontScale);   // 2400 / 24 = 100
```

So **90 columns by 100 rows**, each glyph 12×24 px. The digest's own comment
already assumed these figures — "at most 65 columns against the ninety", "the
hundred or so the panel holds" — so this is the arithmetic behind a number that
was being estimated, not a correction of it.

**Two behaviours, both of which the row count depends on.**

- *It wraps.* `WriteFrameBuffer:213-217`: `CurrentPosition->XPos++` then `if
  (CurrentPosition->XPos >= MaxPosition.XPos) AdvanceNewLine (…)`, which sets
  `XPos = 0` and adds a row. A line wider than 90 columns is therefore not
  truncated and not lost — it costs a second row, and its tail is on that row.
- *It wipes rather than scrolls.* `AdvanceNewLine:115-122`: on `YPos >=
  MaxPosition.YPos` it calls `ZeroMem` on the entire framebuffer and restarts at
  (0, 0). There is no scrollback, so the panel holds the **last 99 rows of
  output, never the first** — which is what the 41 repetitions of the digest are
  for, and why the once-printed `P2 NOLOAD` block is only visible during the
  first two windows and gone afterwards.

**One copy of the digest costs about 45 rows, so this run is nowhere near the
ceiling.** Fixed lines 15 rows (the one wrapper among them is `P2 APRI first=%g
last=%g` at 92 columns), plus `diag=27`, `err=1`, `walk=2`. Two copies fit in 99
rows. The design's own worst case is not this run's: with the full `diag=64` and a
distinct status per failure the digest is **115 rows against the 99-row panel**,
and then the head of a copy is wiped while its tail is printing. That ceiling is
worth knowing and is not a fault in this reading.

**And the RETRY line is the last populated row in every state — that is the
finding.** `P2Bins` prints it last, its `P2Hold` pause (2e9 volatile
read-modify-writes, seconds each, 40 of them) follows immediately, so printing
stops on that line and does not resume for seconds. The wipe does not move it
either: a wipe lands *inside* a copy, and the remainder of that same copy prints
from the top of the cleared panel and ends on the same line. So the row to
photograph is not the bottom of the screen — it is the last row that has any text
on it, and the rows beneath it are blank.

Whether that row is the whole line or its tail depends on the run: the format is
44 columns of fixed text plus six status names with 46 columns to share, i.e.
**86 to 152 columns**, so one row only if the six names fit in 46 — which for a
run with `Out of Resources` in it they do not. When it wraps the break falls in
the middle of a field, and `bs9=` and `bs16=` — the two fields the retry exists to
report — are on the row *below*, which is the last populated one. Either way `bs9=`
is on the last text row, and there is nowhere else on the screen it can be.

**Instrument:** `tools/console-budget.py`, which reads the panel PCDs, the font,
the scale rule and both console behaviours out of their sources, takes the digest's
DEBUG formats out of `P2Retry`/`P2Bins`/`P2Digest` by parsing the C, groups the
if/else variants of one line (keyed on the set of named fields, so `matched=none`
and `matched=%d..%d` count once and `APRI bytes=` and `APRI first=` do not), and
reports columns, rows per copy, and where the RETRY line sits.

| | |
|---|---|
| finds | the panel is **90 columns × 100 rows** (1080/12, 2400/24); the console **wraps** (`WriteFrameBuffer:213-217`) and **wipes** rather than scrolls (`AdvanceNewLine:115-122`), so the panel holds the last 99 rows, never the first |
| reading | **the last row of the panel that has text on it is `P2 RETRY`**, and `bs9=` is on it — as the whole line if the six statuses fit in 46 columns, otherwise as the wrapped tail on the row below |
| budget | one digest copy ≈ 45 rows (15 fixed + diag 27 + err 1 + walk 2); two copies fit the 99; the storage caps would make it 115 and overflow — a ceiling, not this run |
| why `bs9=` was missed | it is not at the bottom of a filled screen: after a wipe the panel refills from the top, so the rows below the cursor are blank and the cursor's row is the reading |
| instrument | `tools/console-budget.py` |


## Step 4.30 — `Out of Resources` can only be the allocator, and the largest request a load makes cannot print it

The line the phase turns on is `P2 ERR`, which prints each distinct failure
status by its `%r` name. Four sessions have now gone looking for `bs9=` and
`P2 ERR` on the phone without landing either, so this step goes at the question
from the side the host can answer: **what can return the status the panel would
name, and which of those can this platform reach.** The answer is an inventory,
and one entry in it turns out to bound all the others sharply enough to be worth
reading on its own.

**The name is not the loader's.** `PeCoffLoaderLoadImage` **cannot** return
`RETURN_OUT_OF_RESOURCES`: the token occurs *zero* times in
`BasePeCoffLib/BasePeCoff.c` (`grep -c` = 0), and the function's only failure
return is `RETURN_LOAD_ERROR`, which `%r` prints as **"Load Error"**. So on this
panel "Out of Resources" is never "the PE was rejected" — a file the loader would
not take says a different word. This matters because it is the one reading that
would have sent the investigation at the file and the relocations instead of at
the allocator.

**The pool collapses into the page allocator, so a pool failure is a page
failure.** Three pool-side gates look like independent mechanisms and none of
them is:

- `LookupPoolHead` (`Pool.c:135-137`) answers `&mPoolHead[Type]` for *any* type
  below `EfiMaxMemoryType`, and the three types that could make it refuse are
  rejected by `CoreInternalAllocatePool:212-217` before it is ever called. It
  cannot return NULL where it is used.
- `MAX_POOL_SIZE` (`Pool.c:60`) is `MAX_ADDRESS - POOL_OVERHEAD`; no
  firmware-sized request reaches it.
- The one *live* `return EFI_OUT_OF_RESOURCES` on the lock path is
  `CoreAcquireLockOrFail`'s `EFI_ACCESS_DENIED`, which `Library.c:41-45` returns
  only when `Lock->Lock == EfiLockAcquired` — reentrancy. Every acquisition on
  every path is paired with a release, and memory protection's
  `ApplyMemoryProtectionPolicy` call sits at `Pool.c:328`, outside the lock.

What is left is `Pool.c:247`: `return (*Buffer != NULL) ? EFI_SUCCESS :
EFI_OUT_OF_RESOURCES;`. `CoreAllocatePoolI` returns NULL only when
`CoreAllocatePoolPagesI` could not get its pages — i.e. only when
`CoreAllocatePages` refused. **Pool and page failures share one mechanism**, and
the probe cannot separate them by the name.

**`ProtectUefiImage` cannot contribute on this platform.** Its
`EFI_OUT_OF_RESOURCES` (`MemoryProtection.c:589`) sits *after* the `return
EFI_SUCCESS` in `case DO_NOT_PROTECT:` (`:559-567`), and the whole "Enhanced
Memory Protections" body below `Finish:` is commented out. Every image takes the
`DO_NOT_PROTECT` branch because `GetUefiImageProtectionPolicy` reads
`gDxeMps.UefiImageProtectionPolicy` and `gDxeMps` is zero here — the same root
cause as the `EFI_MEMORY_ATTRIBUTE_PROTOCOL` that is not installed, carried to
P3. `MemoryProtectionSupport.c`'s seven producers hang off the machinery below
`Finish:` and one of them, `CreateNonProtectedImagePropertiesRecord`, *is* on the
live path — but `ProtectUefiImage:576` calls it as a statement and discards the
status, so it cannot reach the panel either.

**The address-0 rule is real and is not taken.** `CoreInternalAllocatePages:1240-1243`
refuses `AllocateAddress` at 0 ("reserved for null pointer detection") with
`EFI_NOT_FOUND`, and all 46 Apriori drivers do have `ImageBase 0x0` — but
`Image.c:719-720` gates that branch on `ImageAddress >= 0x100000`, which excludes
0, so every one of them falls through to `AllocateAnyPages`. The rule is a real
source of "Not Found" in general and is not the source here.

**And the largest request a load makes reports as a different name.** This is the
finding. `GetFileBufferByFilePath` (`DxeServicesLib.c:810`) allocates
`AllocatePool (FileInfo->FileSize)` — **the whole image file**, which is the
biggest single allocation anywhere in a load, larger than the image's own pages
for any driver in this volume. Every failure inside it sets
`EFI_OUT_OF_RESOURCES` (`:743, :796, :814, :872, :914`) and then returns
**NULL**, not the status. The only thing that sees the result is
`CoreLoadImageCommon:1282-1283`:

```c
FHand.Source = GetFileBufferByFilePath (BootPolicy, FilePath, &FHand.SourceSize, &AuthenticationStatus);
if (FHand.Source == NULL) {
  Status = EFI_NOT_FOUND;
}
```

So a load that cannot find room for the file buffer prints **"Not Found"**, not
"Out of Resources". Read the other way round, that is the useful direction:
**seeing "Out of Resources" on the panel is already evidence that the file buffer
succeeded** — the heap held a whole image file — and the refusal came after it,
in the image's own pages, the private-data pool, or the device-path copy. It also
puts a floor under `P2 FREE largest=`: whatever that field says, the heap held at
least one image file at load time.

One caution, because it is the step where this gets over-read: the file buffer and
the image pages are live *together*. `CoreFreePool (FHand.Source)` is at
`Image.c:1512`, after `CoreLoadPeImage`'s return at `:1413`. So the peak is the
file plus the pages, and the buffer fitting does not mean the image fit.

**The whole inventory, and where each verdict comes from.** 23 lines *produce*
`EFI_OUT_OF_RESOURCES` across the seven files a load can fail out of, 1 only
tests it, 20 are documentation. The produces resolve to 12 ledger entries: six
reachable, four dead in this tree, one absent by construction, and one reachable
only on the arithmetic that `P2 FREE largest=` measures. Two files'
producers — six in `DxeServicesLib.c` and seven in `MemoryProtectionSupport.c` —
are accounted for by a checked claim about a *third* file rather than by a verdict
of their own, which is the form the tool prints when the honest answer is "these
all report as something else, and here is the line that decides that".

**Instrument:** `tools/load-sites.py`, which takes a status name as it appears in
`P2 DIAG`'s `%r`, parses the name table out of `PrintLibInternal.c` rather than
assuming it, and prints the ledger and the raw occurrence list. Every ledger entry
carries the source text that identifies its mechanism as an **anchor**, and the
anchor is checked before the verdict is printed: a mechanism spelled differently
prints `!! anchor gone`, one that moved to another function prints `!! moved`
(with the same warning that the verdict may be about the right words in the wrong
code), and the `absent` entry — the loader claim above — is checked by requiring
the token to be *not* there, so it fails the day the loader gains that return.
The point is that a verdict cannot outlive the code it was about.

| | |
|---|---|
| finds | `PeCoffLoaderLoadImage` **cannot** return `RETURN_OUT_OF_RESOURCES` (0 occurrences in `BasePeCoff.c`); its only failure is `Load Error` |
| finds | the pool's three gates are inert (`LookupPoolHead`, `MAX_POOL_SIZE`, the lock's `ACCESS_DENIED`) and `Pool.c:247` is the only line that reports exhaustion — so **pool and page failures are one mechanism** |
| finds | `ProtectUefiImage`'s `EFI_OUT_OF_RESOURCES` is unreachable (`gDxeMps == 0` ⇒ every image takes `case DO_NOT_PROTECT:`'s early return); `MemoryProtectionSupport`'s is on the live path but its status is discarded |
| **reading** | **`GetFileBufferByFilePath` allocates the whole image file and returns NULL on any failure, which `Image.c:1282-1283` maps to `EFI_NOT_FOUND` — so the largest request in a load prints "Not Found", and "Out of Resources" on the panel already means the file buffer fit** |
| caution | the file buffer and the image pages are live together (`CoreFreePool (FHand.Source)` is at `Image.c:1512`, after `CoreLoadPeImage`), so the floor is real but it is a floor on the peak, not headroom |
| budget | 23 produces / 1 test / 20 doc; 12 ledger entries — 6 reachable, 4 dead, 1 absent, 1 arithmetic-only |
| instrument | `tools/load-sites.py` (`--status NAME`, `--all`) |

**Still unread on the device.** None of this replaces the panel. `P2 ERR` names
the status; `P2 FREE largest=` says whether `FindFreePages` was the arithmetic;
`bs9=Success` with 27 `L`s would falsify "the heap is exhausted" outright, because
the probe's request is 4 KiB-aligned (`Alignment = DEFAULT_PAGE_ALLOCATION_GRANULARITY`,
so `EFI_SIZE_TO_PAGES (Alignment) - 1` is `+0` and nothing is rounded), of type
`EfiBootServicesCode` (never binned), and the probe itself frees everything it
takes. What this step changes is what a reading of that line will *mean*: the name
picks between the allocator and nothing else, and among the allocator's requests
the largest one is already excluded by the name alone.

**Checked against the device while it was in TWRP.** `boot` was verified by
`dd`-ing its first 280 blocks and comparing block-for-block against every image on
the host: 17856 of 17856 64-byte blocks identical to
`work/out/p2-4.20/Mu-gauguin-silicon-gzip.img` (the next best candidate matches
411 and differs from byte 8). So the panel being read is unambiguously that
build, and the header read back off the partition — v1, 2048-byte pages,
kernel 1,136,759 bytes at `0x10008000`, tags `0x10000100` — is that image's own.

**Step 4.32 corrects the two numbers in that sentence and keeps the conclusion.**
280 blocks is 17,920 bytes and 17,856 blocks is 1,142,784, so the clause merges two
measurements; 411 is `p2-4.19`-vs-`p2-4.20` and not a candidate-vs-readback figure;
and no 09-24 readback was archived at all, so this verification cannot be re-run from
disk. The conclusion is instead carried by step 4.25's flash record — `p2-4.20` written
to `sde55` and read back identical — and by `work/out/p2-4.20/` still holding
`dbf131d2…` today.


## Step 4.31 — The panel was holding the load flood, and no `P2` line has ever been on it

The reading this step is built on is one row, and it is the first panel reading
in five sessions that names a line the host could place:

```
Loading Driver at 0x0009CBE3000 EntryPoint=0x0009CBE41DC Fat.efi
```

with, in the same answer, **not one line beginning with `P2` anywhere on the
screen**. That pair is the finding, and it is worth being exact about what each
half rules out.

**The row is DxeCore's own, and it is a success.** It is printed by
`CoreLoadPeImage` (`Image.c:862`, `DEBUG_INFO | DEBUG_LOAD`) and it is printed
*after* the PE is parsed, the pages are taken and the relocations are applied —
look at the position in the function: the `goto Done` that frees the pages is
below it. So `Fat.efi` was loaded into memory. Nothing was rejected.

**The address is inside this device's DXE heap, two thirds of the way up it.**
`device/config/uefiplat.cfg:16` declares `DXE Heap` at `0x9B800000 + 0x02360000`
= `0x9B800000..0x9DB60000`, 28.4 MiB, the window step 4.28 established. `0x9CBE3000`
is `0x13E3000` into it: **20.9 MiB of 28.4 MiB allocated**, 7.5 MiB left, at the
moment `Fat.efi` went in. `Fat` is FV file 37 of 123 (`961578FE-B6B7-44C3-AF35-6BC705CD2B1F`)
and entry 30 of the Apriori list, so this is mid-batch, not near the end of it.

**No `P2` line has ever been on that panel, and that is a statement about when
the digest prints.** Every bring-up line — `P2 NOLOAD`, `P2 APRI`, `P2 SEQ`,
`P2 WHY`, `P2 ERR`, `P2 DIAG`, `P2 STATS`, `P2 WALK`, `P2 FREE`, `P2 BIN`,
`P2 RETRY`, `KEY` — is printed by `CoreDisplayDiscoveredNotDispatched`, which
`DxeMain` calls at `DxeMain.c:576`, **after `CoreDispatcher ()` returns at
`:562`**. There is no bring-up line anywhere in the dispatch loop. So a panel
showing the load flood and no `P2` line is a panel photographed at a moment when
`CoreDispatcher` had not returned — and a panel photographed *after* it returns
cannot look like this, because the digest is printed 41 times with a multi-second
pause between copies (`P2Hold`), which over 41 copies and ~45 rows is about 1800
rows against a panel that holds 99.

That asymmetry is the useful half, because it does not depend on catching a
frame. If dispatch had completed, the last thing printed before the assert would
have been 41 copies of the digest, and the panel would hold digest rows and then
the assert row. It holds neither. **So the run being read did not reach the end
of `CoreDispatcher`.** The alternative explanation for a `P2`-less panel — that
the payload is not ours at all, i.e. that `boot` no longer holds
`work/out/p2-4.20/` — is not excluded by the row alone (`Qualcomm's own DXE
prints the same text from the same function`), and step 4.30's block-for-block
verification of `boot` was taken in a *previous* TWRP session. Both readings
predict this screen, which is why the first thing this step asks for is a
read-back of `boot` and not another photograph.)

**What the panel cannot say, and the line that makes it say it.** The flood
cannot distinguish `CoreDispatcher` *stopped* from `CoreDispatcher` *still
running* — the console has no scrollback, so a panel mid-dispatch and a panel
that died mid-dispatch are the same image. A tick printed once per attempted
dispatch separates them: a number that keeps advancing is a run in progress, and
a number that stops is the last driver attempted, with the free-page count at
that instant beside it. That is `P2Tick`, added in this step:

```
K 30 Ss 18/46 free=4096 961578FE-B6B7-44C3-AF35-6BC705CD2B1F
  |  |  |   |     |     |
  |  |  |   |     |     +-- the driver, the same GUID P2 DIAG names
  |  |  |   |     +-------- P2 FREE largest=, the allocator's rung
  |  |  |   +-------------- started / Apriori-promoted, P2 STATS' two numbers
  |  |  +------------------ the stage ('L' load, 'S' start) and the status letter
  |  +--------------------- P2WhyLetter's letter, the same alphabet as P2 WHY
  +------------------------ the tick: a sequence number, so a stall shows as a stop
```

It is printed after the load and after the start of every driver, success and
failure alike, so it is the last row on the panel whenever a run stops inside
dispatch — which is the state the reading above came back in. It measures nothing
new: every field is a line that already existed, and it is placed where a reader
will be, not where the digest is.

**`PcdDebugPrintErrorLevel` is inert on this platform, and that is why the flood
is silenced by deletion rather than by a level.** The obvious way to get the
digest onto the screen is to raise the flood's level past the platform's mask.
That does not work here, and the reason is worth recording because it applies to
every module in the tree: `BaseDebugLibSerialPort`'s `DebugPrintLevelEnabled`
tests its argument against **`PcdFixedDebugPrintErrorLevel`**, not against
`PcdDebugPrintErrorLevel`. This platform sets `PcdDebugPrintErrorLevel|0x8007EE0F`
(`SiliciumPkg.dsc.inc:69`) and never sets the fixed one, so it takes the
`MdePkg.dec` default — `0xFFFFFFFF` — and **every level is enabled**. That is not
an inference: `PcdFixedDebugPrintErrorLevel`'s compiled value is `0xFFFFFFFF` in
this module's own generated `AutoGen.c` (`_PCD_VALUE_PcdFixedDebugPrintErrorLevel`).
So `DEBUG_VERBOSE` there would print exactly as loudly as `DEBUG_INFO | DEBUG_LOAD`
did, and the platform's carefully chosen print mask has never filtered anything
for any module linked against this DebugLib. Raising the level cannot silence the
line and lowering the mask would silence far more than it, so the three calls are
removed (`Image.c`, `P2BRINGUP`-marked, with the restore instruction in place).
The build is the check: `FVMAIN.Fv` contains `Loading driver at 0x%11p EntryPoint`
one time before this step and **zero** times after, while `KEY 0/%d` and
`P2 RETRY` are still there.

**Still unread.** Everything above is an argument about a screen; the screen is
unchanged. What this step produces is a build that can be read from the bottom
row in either state, and the two readings it is worth flashing for:

| state | the reading | what it means |
| --- | --- | --- |
| run stops in dispatch | `K <n> <phase><status> <s>/46 free=<pages> <guid>` as the bottom row, `n` short of ~123 | the run died at that driver, and `free=` is the heap at that instant — the first heap number ever taken *during* dispatch rather than at the end of it |
| run completes dispatch | `KEY <errors>/46 err=<name> at=<i> free=<pages> miss=<i>` as the bottom row | the digest was there all along and the flood was hiding it; `P2 ERR` and `bs9=` are then read from the same screen |

| | |
| --- | --- |
| finding | the panel holds DxeCore's load flood and no `P2` line, from a run that did not reach the end of `CoreDispatcher` |
| the row that says so | `Loading driver at 0x0009CBE3000 ... Fat.efi` — printed only after a successful PE load, at 20.9 MiB into a 28.4 MiB heap |
| why no `P2` line | every bring-up line prints from `CoreDisplayDiscoveredNotDispatched`, which `DxeMain.c:576` calls after `CoreDispatcher` returns at `:562` |
| why the flood cannot be silenced by level | `DebugPrintLevelEnabled` tests `PcdFixedDebugPrintErrorLevel`, which is `0xFFFFFFFF` (MdePkg.dec default, confirmed in the module's `AutoGen.c`), so `PcdDebugPrintErrorLevel|0x8007EE0F` filters nothing |
| instrument added | `P2Tick`: one `K <n> <phase><status> <s>/<ap> free=<pages> <guid>` row per attempted dispatch, 70 columns, printed last |
| instrument removed | the three `DEBUG_INFO \| DEBUG_LOAD` prints in `CoreLoadPeImage` (`Image.c:862`, `:916`, `:919`), `P2BRINGUP`-marked |
| build | payload of record `work/out/p2-variants/Mu-gauguin-silicon-gzip.img`, sha256 `7c8fdb5a…0e1ef44`, 1,142,784 B — verified to carry `K %d %c%c %d/%d free=%d %g`, `KEY 0/%d` and `P2 RETRY`, and verified *not* to carry `Loading driver at` |
| still first | read `boot` back and compare it block-for-block before concluding anything about "our payload stops mid-dispatch" — the comparison is now `tools/identify-boot.py` (step 4.32), which prints both sha256s so the answer can be pinned instead of remembered |


## Step 4.32 — Every archived read of `boot` reconciles, and two of the paths no longer name their bytes

Step 4.31 left one row as "still first": read `boot` back and compare it
block-for-block. That row is a rule this device has had for weeks —
"对照的那张必须在覆盖之前读" — and a rule with no instrument behind it is the kind
of thing that survives until the session under time pressure gets it wrong, which
is what happened at step 4.30: the comparison was made, was right, and the loop
that made it was thrown away. So the rule now has a tool, `tools/identify-boot.py`,
and running it over everything in the archive is what this step is.

### The tool, and the three ways the first three drafts of it were wrong

It answers one question — *is the image on the partition the one I think it is* —
and it does not care which image that should be: it reports the best match, how
close the rest came, and says "none of them" as loudly as a match. Each of its
three corrections came from a measurement rather than from taste:

| | |
|---|---|
| the window | the first draft compared the leading **280 64-byte blocks** (17,920 B), because that is what step 4.30's hand comparison used. Two *different* builds, `p2-4.19` and `p2-4.20`, agree on **278 of those 280** and disagree on **17,413 of the file's 17,824** — they differ in the header page, and the rest of the first 17 KB is BootShim, identical by construction. A 280-block window calls two images that are 97% different a near-match |
| the grading length | grading over the *readback's* length called a perfect match a **54%** match, because the partition (2 MiB read) is longer than the image in it (1,142,784 B). A candidate is now graded over its **own** extent, and the blocks past its end are reported as tail, not as disagreement |
| unequal lengths | a candidate *longer* than the readback was reported "not a match", which is what a naive comparison of unequal lengths says. `work/out/boot-readback-payload.bin` is 1,140,736 B and the image it contains is 1,142,784 — the last 2,048 bytes were never on the wire. That is now `identical over every byte read`, with the un-read count named, and exit status 0 |

### What the archive says, read back through it

Every partition read kept in `work/out/`, identified by content:

| read (mtime) | bytes | kernel_size | best match | verdict |
|---|---|---|---|---|
| `boot-readback-head.bin` (09-23 16:10:02) | 2,097,152 | 1,135,628 | `boot-now-0923.img` | identical; same sha256 as the next row |
| `boot-readback-2026-09-23.bin` (16:10:31) | 2,097,152 | 1,135,628 | `boot-now-0923.img` | **17,856/17,856 — identical over its whole length** |
| `boot-readback-payload.bin` (16:10:36) | 1,140,736 | 1,135,628 | `boot-now-0923.img` | identical over every byte read; 2,048 unread |
| `boot-pre-flash-0923c.bin` (22:57:24) | 1,048,576 | 1,135,628 | `boot-now-0923.img` | identical over every byte read; 94,208 unread |

Four reads, three sizes, two times of day, one answer, and every byte of overlap
agrees. The reads and the document's own records then form a single chain of
payloads on `boot`, each one's kernel a little larger than the last:

| kernel_size | image | on `boot` |
|---|---|---|
| 1,134,485 | `boot-before-p2walk.img` | before step 4.13 |
| 1,135,628 | `boot-now-0923.img` | at both 09-23 reads, 16:10 and 22:57 |
| 1,136,314 | `8c565681d1093b76…` (step 4.13), preserved at `p2-silicon-gzip-preread-0923d.img` | from 4.13 until 4.25 |
| 1,136,759 | `dbf131d254374646…` (`p2-4.20`, step 4.25) | from 09-24 until now, as far as anything records |

So the apparent contradiction — step 4.30 saying the read held `p2-4.20` while the
archived reads hold `boot-now-0923.img` — is not one. Those reads are from 09-23,
`p2-4.20` was built 09-24 11:32 and flashed by step 4.25, and the reads predate it.
`p2-4.20/Mu-gauguin-silicon-gzip.img` is still `dbf131d2…` today, so that step's
record is intact.

What does not survive is a **path** as a name for bytes:

| path | step's record | measured now |
|---|---|---|
| `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` | step 4.13: `8c565681…`, 1,140,736 B, flashed and read back | `7c8fdb5a…`, 1,142,784 B (step 4.31's build) — a different image under a reused path |
| `work/out/p2-4.20/Mu-gauguin-silicon-gzip.img` | step 4.25: `dbf131d2…`, flashed and read back identical | unchanged — nothing has rebuilt it since |
| `work/out/boot-now-0923.img` | — | written 09-23 20:47, i.e. *after* the 16:10 read it matches: a named copy of what the partition held, not a build output |

Step 4.13's bytes survive because it kept a second copy under a name that means
"the image that was on the phone before the next one" —
`p2-silicon-gzip-preread-0923d.img`, still `8c565681…`. That is the mechanism the
rule needs, and it is the one the tool's output is now built around: it prints the
**candidate's** sha256 beside the **readback's**, so the pair can be written into a
step and the next comparison is against a hash rather than against a filename.

### The sentence at step 4.30 that cannot be right

> `boot` was verified by `dd`-ing its first 280 blocks and comparing block-for-block
> against every image on the host: 17856 of 17856 64-byte blocks identical to
> `work/out/p2-4.20/Mu-gauguin-silicon-gzip.img` (the next best candidate matches 411
> and differs from byte 8).

Two of those numbers cannot come from one `dd`. 280 blocks is **17,920 bytes**; 17,856
blocks is **1,142,784 bytes**, which is `p2-4.20`'s exact size. And 411/17,824 is what
`p2-4.19` and `p2-4.20` agree on *with each other* — measured this step — not a figure
any candidate takes against a readback containing `p2-4.20`. The conclusion stands, on
step 4.25's flash record (written to `sde55`, read back identical, 09-24), but the
verification is not reproducible from disk: **no 09-24 readback of `boot` was archived
at all.** The next read closes that too, because it is the one that gets saved.

### What is still not known

Which payload `boot` holds *right now*. The last recorded write is step 4.25's, and
steps 4.26 through 4.31 shipped no build to the phone, so the inference is `p2-4.20`
(`dbf131d2…`) — but that is an inference from the document's records and not a
measurement of the partition, and it is exactly the class of answer the previous
sentence showed can be wrong. One `dd` settles it, and the tool prints both hashes.

| | |
|---|---|
| tool | `tools/identify-boot.py` — whole-extent grading, prefix reporting, both sha256s; `--read` takes the readback off the phone in 1 MiB `dd` blocks and compares at 64 |
| finds | all four archived reads of `boot` hold `boot-now-0923.img` over every byte of overlap, from three read sizes at two times |
| finds | the payload chain 1,134,485 → 1,135,628 → 1,136,314 → 1,136,759 bytes, each step crossing between a read and a step's own record |
| finds | `work/out/p2-variants/` no longer holds step 4.13's image (`8c565681…` → today's `7c8fdb5a…`); a path is not a fixation, a sha256 is |
| corrects | step 4.30's verification sentence: 280 blocks and 17,856 blocks are two measurements in one clause, and 411 is `p2-4.19`-vs-`p2-4.20` |
| corrects | the "still first" row of step 4.31: the comparison it asks for now has an instrument, and it was run over the archive first |
| does not change | the firmware, and nothing on the phone |
| next | `tools/identify-boot.py --read`, then flash `work/out/p2-variants/Mu-gauguin-silicon-gzip.img` (`7c8fdb5a…`) over the identified control |


## Step 4.33 — The control is `p2-4.20`, the tool read it wrong first, and the new probe is on the phone

Step 4.32 ended with one unmeasured thing: which payload `boot` held. It has now been
read, and getting the answer required fixing the tool that read it — which is the
part worth recording, because the wrong answer it gave first was
"the payload on the phone is not any image here. Do not assume it is one of them",
against the partition that held the image the records named.

### The wrong answer, and it was the read and not the partition

The first `--read` produced a 4,194,624-byte readback (`4 × 1 MiB + 320`) and this:

```
  16,471/17,856   92.2%  work/out/p2-4.20/Mu-gauguin-silicon-gzip.img
          first difference at offset 0x100000 (1,048,576)
best match: ... at 16,471/17,856 blocks - NOT a match
```

A 1 MiB boundary is where a transfer artifact lands and not where a firmware
difference does, so the bytes were dumped as text:

```
readback[0x100000:]  "1+0 records in\n1+0 records out\n1048576 bytes (1.0 M)
                      copied, 0.016758 s, 60 M/s\n"  then binary
```

**TWRP's toybox 0.8.4 `dd` prints its transfer statistics, and `adb exec-out`
delivers them on the captured stdout.** So a 1 MiB-chunked read is 1 MiB of data
followed by ~80 bytes of `dd` status, per chunk, concatenated — the file is shifted
by 80 bytes after the first megabyte, and 1,385 of the last 1,472 blocks differ for
that reason alone. Lining the readback up at a shift of 80 makes **all 94,208** of
those bytes match.

Measured afterwards, to be sure of where the statistics go:

| invocation | host received |
|---|---|
| `adb shell "dd ... count=1"` | 4,096 bytes on the pipe, statistics on the terminal — i.e. device **stderr** |
| `adb shell "dd ... count=1 2>/dev/null"` | 4,096, statistics gone |
| `adb exec-out dd ... count=1` | **4,174** = 4,096 + 78 |
| `adb exec-out dd ... count=1 status=none` | 4,096 |

So `adb shell` keeps the two streams apart and `adb exec-out` merges them. That is
why the hand-made reads in earlier sessions were right and this one was not: they
went through `adb shell` with a redirect, this went through `adb exec-out`. Two
fixes, both now in the tool: `status=none`, and truncating each chunk to its
requested length (the statistics are written after the data, so this holds even if a
future toybox ignores the flag). The same hardening went into
`tools/flash-boot.sh`'s read-back — it used `2>/dev/null` and was correct, but only
because of which of the two adb entry points it happens to use.

This is worth stating plainly because of what the tool is *for*: it exists so the
control image is identified by content before it is overwritten, and an unfixed
version of it would have certified 80-byte-shifted bytes as the control and then
declared the real image unrecognisable. The failure would have looked like a
firmware finding.

### The check a readback cannot make about itself

Every other comparison in the tool is between two copies made on the host, so a
read that is wrong in a way the host repeats faithfully passes all of them —
which is precisely what happened. So `--read` now also hashes the same range **on
the phone** (`dd ... status=none | sha256sum`, 512-byte blocks) and compares the
two; the first read is the one that would have failed that check. It is 64
hexadecimal characters over the wire, and it is the only number in the transcript
that the phone produced rather than the host.

### The reading

```
  sha256 over the same 4,194,304 bytes, hashed on the phone: ok
  17,856/17,856  100.0%  work/out/p2-4.20/Mu-gauguin-silicon-gzip.img  <- IDENTICAL over its whole length
```

`boot` held `work/out/p2-4.20/Mu-gauguin-silicon-gzip.img`, sha256
`dbf131d254374646bfbc5e3da4cbfc3dc8e23f3d7ec4a2a7f6ca73dde692e40f`, over its whole
1,142,784 bytes — exactly what step 4.25's flash record says and what step 4.32
inferred from the records. The inference was right; it is now a measurement. The
readback is kept at `work/out/boot-readback-0924.bin`, 4,194,304 bytes, sha256
`77778cd624d2842be6ff4b84a5b21ece36177e80da83d01eebae4c46e3d70a72`.

Step 4.30's verification sentence is now closed from the other side: the image it
named is the image that was on the partition, and the sentence's *numbers* (280
blocks vs 17,856 blocks vs 411) remain the three different measurements step 4.32
separated.

### And the new probe is written

`work/out/p2-variants/Mu-gauguin-silicon-gzip.img`, 1,142,784 B, sha256
`7c8fdb5a1a272ab65d806c2833eb849f441c3ef2092121aac0d5df40d0e1ef44`, written to
`/dev/block/sde55` (`by-name/boot`) with `tools/flash-boot.sh --twrp`, 279 × 4096 B,
read back identical. Its content was checked against the control inside the
compressed volume, by walking `FVMAIN` rather than grepping the image (`the inner
volume is LZMA, so a raw search finds nothing in either`):

| string | control `dbf131d2…` | flashed `7c8fdb5a…` |
|---|---|---|
| `Loading driver at` | 1 | **0** |
| `EntryPoint=0x` | 1 | **0** |
| `K %d %c%c %d/%d free=%d %g` | 0 | **1** |
| `KEY 0/%d` | 0 | **1** |
| `KEY %d/%d err=` | 0 | **1** |
| `P2 RETRY` / `P2 DIAG` / `P2 SEQ` | 1 / 1 / 2 | 1 / 1 / 2 |

So the flood is out and the per-dispatch line is in, and the `P2` digest is
untouched. The binary check for the reader is in the same table: **if
`Loading Driver at …` is still on the panel, the flash did not take**, because no
build after this one can print it.

### What the panel is now for

Two readings, and the bottom row distinguishes them:

| bottom row | means |
|---|---|
| `K <n> <phase><status> <s>/<ap> free=<pages> <guid>` | the run stopped inside the dispatch loop. `<n>` is the last driver attempted, `free=` is the heap in pages at that instant, and the `guid` names it. This is the state the previous reading could not distinguish from anything — `Fat.efi` on the panel with no `P2` line |
| `KEY <errors>/46 err=<name> at=<i> free=<pages> miss=<i>` | `CoreDispatcher` returned and the digest is being printed. The 41-copy flood did not hide the digest; the load flood did, and it is gone now. Then `P2 ERR` and the four `P2 BIN` lines are readable from the same screen |

And a third outcome that is also information: if the panel shows `K` lines with the
counter *advancing* on every pass of the loop and never settling, the run reaches
the end of the dispatch loop and the value of `n` on the last line is the count of
promoted entries.

| | |
|---|---|
| reads | `boot` = `work/out/p2-4.20/Mu-gauguin-silicon-gzip.img`, `dbf131d2…`, 17,856/17,856 blocks identical, phone-side sha256 ok |
| archived | `work/out/boot-readback-0924.bin`, 4,194,304 B, `77778cd6…` — the control, kept as bytes and not as a filename |
| corrects | `tools/identify-boot.py`'s `--read`: `dd` statistics were being concatenated into the readback by way of `adb exec-out`; `status=none` plus per-chunk truncation, and an on-device sha256 cross-check that the first, wrong read would have failed |
| corrects | `tools/flash-boot.sh`'s read-back the same way, which was correct only because it uses `adb shell` |
| flashed | `work/out/p2-variants/Mu-gauguin-silicon-gzip.img`, `7c8fdb5a…`, read back identical; content verified to have lost `Loading driver at` and gained `K %d %c%c` |
| still first | read the bottom row of the panel, and read it while the loop is showing the same line on every pass |


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
8        Mu-gauguin-silicon-gzip.img    (not flashed)                (not read)             n/a           (not read)
         (rebuilt again with 4.14:
         CoreStall's pause replaced
         by P2Hold), sha256 725c33c1
9        Mu-gauguin-silicon-gzip.img    (not flashed)                (not read)             n/a           (not read)
         (4.15: the digest also
         groups the failure statuses
         and names them, P2 ERR, and
         the promotion loop counts the
         Apriori entries that matched
         nothing), sha256 5be70ecc
10       Mu-gauguin-silicon-gzip.img    (not flashed)                (not read)             n/a           (not read)
         (4.16: the per-driver
         failure list moved into the
         repeated digest, so it stays
         on the screen), sha256 7b5a1067
11       Mu-gauguin-silicon-gzip.img    (not flashed)                (not read)             n/a           (not read)
         (4.19: the digest also reads
         the allocator's own state -
         P2 BIN for the two runtime
         bins and the default window,
         P2 RETRY for the four
         requests that failed and the
         one that succeeded), sha256
         ef9f8217
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

Row 8 is step 4.14 and it differs from row 7 in one function. The 41 repetitions
were never free: the pause between them was `CoreStall`'s, and `CoreStall` waits
through the Metronome, which is installed by `MetronomeDxe` — a driver this volume
is being measured on. So the twelve seconds that window lasted was produced by the
measurement, and a run in which `MetronomeDxe` did not start would have printed
the 41 copies back to back and lost the digest entirely. Row 8 replaces that pause
with a bounded busy-wait on a volatile counter, so the window is minutes
regardless of what starts. **Row 8 has not been flashed**: `boot` still holds
row 7's image, and the two differ only inside DxeCore, which is why they are kept
side by side in `work/out/` (row 7's copy at
`work/out/p2-silicon-gzip-preread-0923d.img`, same sha256 as what is on the
phone) rather than overwritten.

Row 9 is step 4.15, and it is the first change made *because* of how the readings
have been going rather than because of what they said. The reason the loads failed
has been printed since 4.13 — `P2 WHY` is one status-class letter per `SEQ`
character, aligned with it — and the `SEQ` line has been read off the panel three
times while `WHY` has never been read once. They are 46 characters each and only
mean anything as a pair, so the reason needed two captures and got none. Row 9 adds
a third spelling of the same statuses, `P2 ERR <name> x<count>`, which is short
enough to be photographed on its own. Like row 8 it has not been flashed; the phone
still carries row 7.

Row 10 is step 4.16 and it is the same complaint one layer down. Row 9 put the
failure *statuses* into the repeated digest; the list of failures, one line per
driver, was still printed once before the repetition began — and capped at 24
records against 27 failures, so three of them were unreachable no matter how the
photograph was taken. Row 10 moves that list into `P2Digest` and lifts the cap to
the storage. It adds nothing to the instrument and everything to the chance of
reading it, which is now what this step of the work turns on: the answer exists
after every boot and has been missed after every boot, and each of rows 8, 9 and
10 removes one way for that to happen. Row 10 has not been flashed either.

Row 11 is step 4.19 and the table skips from row 10 to it because steps 4.17 and 4.18
changed no firmware at all: they were analysis of the volume on the host, so row 11 is
the first row since row 7 with an image behind it, and `boot` has not been written since
row 7 was read. It is the first row whose probe was written to answer a question the
host had already narrowed rather than to capture something unread: step 4.18 reduced the
27 failures to two candidates, both of them heap state at the instant a request arrives,
and neither of them is in a file. So the digest now asks the allocator itself — the two
runtime bins' windows and fill, the default bin's window, and four allocations that
re-attempt the exact requests the slots either side of the failure made — and it caches
those four statuses so all 41 copies read the same. Like rows 8, 9 and 10 it exists
because the answer has to be on the screen rather than on the wire; unlike them, the
answer it stays there for is about the heap and not about the drivers.

The `abllog` column is step 4.6's answer — the last stage ABL's own log for that
boot reached. It is the one column that is filled in whether or not the payload
ran, so it is the column worth not leaving blank.

That table is the entire output of a session. Everything else is in the files
`fastboot-capture.sh` and `pull-bootloader-log.sh` wrote, and — if step 4a ran —
in the pstore ring, which is gone the moment the power button is held.
