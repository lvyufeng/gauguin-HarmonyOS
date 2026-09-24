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
  `Dispatcher.c:1204` only evaluates `CoreIsSchedulable` for entries still
  `Dependent` — so **a-priori entries are scheduled once and never retried**,
  whereas depex-driven drivers are re-evaluated on every pass of the
  `do { … } while (ReadyToRun)` loop. All thirteen providers therefore had their
  entry points invoked in one batch at the very start of the dispatcher, with no
  depex gate.

**The Apriori order is the first thing in this project that separates the five
from the eight.** `CoreFwVolEventProtocolNotify` walks `AprioriFile[Index]` in
list order and appends each match to `mScheduledQueue`, and the drain at
`Dispatcher.c:1066` is a plain FIFO — it takes the head each time
(`mScheduledQueue.ForwardLink`, `:1067-1072`) and every append is an
`InsertTailList` (`:2113` for the Apriori walk, `:1276` for the depex sweep) — so
the batch runs in the order of this repository's `APRIORI.inc` `INF` list, *not*
in FV file order:

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
  Reading the drain end to end (`Dispatcher.c:1062-1216`, the `do { … } while
  (ReadyToRun)` block): a started entry leaves the queue with `Initialized = TRUE`
  (`:1134`) and `Scheduled = FALSE` (`:1133`) and is never re-added, and the only
  `continue` in the loop (`:1127`) skips `CoreStartImage` for that one entry without
  ending the loop. Nothing breaks out; the `} while (ReadyToRun)` at `:1216` is
  reached. So each of the eight is (a) scheduled and never promoted — a `?` in
  `P2 SEQ`; (b) promoted and `CoreLoadImage` failed — an `L` at `:1111`; or
  (c) promoted, loaded, and `CoreStartImage` returned an error — an `S` at `:1156`.
  There is no fourth way, and in particular no silent drop.
  `Initialized` is not what removes it — the flag is written at `:1134` and `:1108`
  and read nowhere in the DXE core. What keeps it off the queue for the rest of the
  boot is `Dependent`, which was already FALSE when the driver was put on the queue:
  cleared at the promotion itself, at `:1274` in
  `CoreInsertOnScheduledQueueWhileProcessingBeforeAndAfter` and at `:2111` in the
  Apriori walk, and the re-evaluation sweep only ever offers `Dependent` entries
  (`:1203`). So the decision that a dispatched driver is done was made when it was
  scheduled, and the failure path records a flag that nothing consults.

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

> **Step 4.47 is what makes that paragraph hold without a condition attached.** The
> bound needs `it matched 46` to mean 46 out of 70, and the 47-entry reading of the
> array would have supplied an alternative: 46 matched out of 47, every one it
> looked at, with a full discovered list of 80 underneath. That alternative is now
> gone — the section declares 1124 and `GetSection` reports the declared size, so
> the array is read whole on every boot — and with it the only way `discovered`
> could have been near 80. The paragraph above was written as an entitlement;
> it is one now.

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

> **The first row was struck in step 4.47, and the third row is now unreachable
> with it.** `bytes=752` needs the Apriori section's *declared* size to be 756; it is
> 1124, and `GetSection` reports the declared size rather than the bytes the caller
> had room for, so no run can print a short `bytes=`. `entries=70` is a property of
> the volume and not of the run, and `miss=none` would now mean the device is not
> reading this volume at all. Row 2 is the reading the run has, and `miss` — 47 or
> lower — is the only part of it still open.

**The third and fourth rows above are now answered off the volume, without the
panel.** Step 4.37 measured every promoted entry's PE `SizeOfImage` and found that
no size threshold classifies the 46 — the best one is wrong about 17 of them — and
that `PdcDxe` (failed) and `ShmBridgeDxe` (loaded) ask for the same 36,864 bytes two
dispatch slots apart. So "the `L`s are not a single allocation that could not be
satisfied" is established rather than conditional, and `P2 FREE largest=` no longer
has to be read to get there. The row that still has to be read is `P2 WHY`'s.

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

> **The fork was closed in step 4.47, against the second row of the table below.**
> `bytes=752` requires the Apriori section to declare 756 where it declares 1124,
> and `GetSection` hands back the declared size, so the second row is not a reading
> that can come back from the device. What the step leaves standing from this
> section is the first row, and with it the *other* half of the question — the 23
> drivers the run's own replay says are all present. The paragraph below the table
> that reads the denominator of `P2 STATS` as the fork now reads it as an identity
> check instead.

With the stop gone, the 23 absent Apriori entries were either never asked for, or
were asked for by a walk that cannot lose a file it was handed. `P2 APRI` has two
admissible readings and each produces the observed SEQ exactly; the length
fingerprint is what tells them apart, and they point at different code:

| `P2 APRI` | what has to be true |
|---|---|
| `bytes=1120 entries=70 sum=a998b263` | the array was read whole and 23 of its names matched nothing — a premise is wrong, because `CoreAddToDriverList` inserts every driver the walk returns into `mDiscoveredList` unconditionally (`Dispatcher.c:1142-1190`) |
| `bytes=752 entries=47 sum=b4ba9d75` | the Apriori section came back 368 bytes short of its 1120; `unhit` is then 1, `miss` is none, the promotion loop never looks past ap46, and the observed SEQ is the string it must print |

`P2 STATS apriori=46/70` says the same thing in one number. `mP2AprioriCount` is
`MAX (mP2AprioriCount, AprioriEntryCount)` — the largest Apriori file size ever
*seen* — so the denominator was going to be the fork. With the fork closed the
denominator is fixed at 70 by the volume, and what the line buys is an identity
check instead: `apriori=46/47` is not a reading this image can produce, so it names
a different image. That is still a reason to read the line, one line earlier than
it used to be.

Neither branch explains the 27 failures. Every one of them — ap19, ap20, ap21 and
ap23..ap46 — is inside the first 46, so it is promoted either way. `P2 ERR` is the
line that answers the 27. These 46 characters answer a different question: *why the
batch is 46 long*.

### The tail shape, which the SEQ cannot argue for or against

The `bytes=1120` branch above is the reading that fits the SEQ with no stop at
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

> **Corrected in step 4.47.** "Rather than a range" was right and the range was
> already down to one: `entries=70` and `apriori=46/70` are fixed by the volume, so
> the parenthesised alternative is a statement that the running image is not this
> image. `unhit` is 24, not 1-or-24. The line that is left to read is `miss`, and
> the five `P2 WALK` lines are unchanged predictions.

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
calls `P2Retry` at `Dispatcher.c:462` and only then emits the four `P2 BIN` lines
(`:464-492`); the `P2 RETRY rc16=…` line itself is printed after them, at
`:493-502`, so `P2 RETRY` is the **last line of the whole digest** — it lands after the
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
calls `P2Retry` at `Dispatcher.c:462`, before any of its own prints, so the five lines
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
| instrument | `SiliciumPkg.dsc.inc:14`, `:19-23`; `ProcessorBind.h:165-169`; `Page.c:382-460`, `:585`, `:1190-1198`, `:1207`, `:1217-1218`; `Image.c:686-690`; `Dispatcher.c:462`; the 218 build-tree `GNUmakefile`s |
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

**This paragraph has since been corrected three times over — see "Step 4.52 — the
one heap address the panel ever gave was read from the wrong end" below.** The
allocator carves free memory from the **top** of the run downward
(`Page.c:1008`, `:1025`, `:1033`), so the address is a distance *down* from the
ceiling, and the pair belongs the other way round: **≤8.5 MiB used, ≥19.9 MiB
free**. The sentence above also calls the `0x9B800000 + 0x02360000` **row** 28.4
MiB, which is 35.4 MiB — 28.4 MiB is what survives PrePi (step 4.28), and
conflating the two is the specific mistake step 4.28 added `tools/pe-facts.py`
to prevent. And `0x13E3000` is 19.89 MiB, not 20.9: that figure is the byte count
read in decimal megabytes and labelled MiB.

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
| the row that says so | `Loading driver at 0x0009CBE3000 ... Fat.efi` — printed only after a successful PE load, at 20.9 MiB into a 28.4 MiB heap (*the direction and the arithmetic are both wrong — **step 4.52**: ≤8.5 MiB used, ≥19.9 MiB free*) |
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

## Step 4.34 — Nothing on this board can be promoted, and the heap is one line of a file in this repo

Step 4.33 left an instrument on the phone and one question under it: the two
requests step 4.18 reduced the 27 failures to — `PdcDxe`'s 9 pages refused at one
slot and `ShmBridgeDxe`'s byte-identical 9 pages granted two slots later — have
two possible shapes. Either the allocator had nowhere to put the first one, or it
had somewhere and did not look. One of those is answerable without the phone, and
it turns out to be answerable completely: not "the heap is small", but **the last
rung of the allocator's ladder cannot be reached on this board at all, so the only
memory the allocator will ever see is a single line of a config file — and that
line is in this repository.**

### What `free=` is, and what it can and cannot say

The field the flashed line prints is `P2LargestAlloc` (`Dispatcher/Dispatcher.c:195-219`):
the ladder `{ 4096, 1024, 256, 64, 16, 4, 1 }` (`:199`), each rung asked for with
`CoreAllocatePages (AllocateAnyPages, EfiBootServicesData, Ladder[Index], &Memory)`
(`:208`, which is where the type comes from as well as the size) and freed again on
success (`:210`), the first that succeeds being printed by `P2Tick` (`:683`). So the
field is one of
eight numbers, and it is a statement about the largest *contiguous run* and not
about a total:

| `free=` | 4096 | 1024 | 256 | 64 | 16 | 4 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|
| bytes it proved | 16 MiB | 4 MiB | 1 MiB | 256 KiB | 64 KiB | 16 KiB | 4 KiB | none |

Two things follow, and they are why the field is worth the row it costs. A refused
load of N pages, on a line whose `free=` is at least 4·N pages, was not refused for
want of room. And `free=0` is unambiguous: not one 4 KiB page of the default type
could be found at that instant, which is exhaustion and not an artefact of the
type's bin — by the time `FindFreePages` returns 0 it has already tried the
request's own bin, then the default bin, then anywhere (`Mem/Page.c:1088-1116`).

### The promote-and-retry rung is dead here

Both places that re-attempt an allocation call `PromoteMemoryResource` and retry
**only if it promoted something**: `FindFreePages`' final step
(`Mem/Page.c:1129-1134`) and `CoreInternalAllocatePages`' failure path
(`:1333-1341`). `PromoteMemoryResource` (`:382-460`) promotes one kind of entry
and no other — a GCD range that is `EfiGcdMemoryTypeReserved` whose capabilities
are `PRESENT | INITIALIZED` and *not* `TESTED` (`:402-405`).

Those three bits are set in exactly one place in this whole tree: the
`mAttributeConversionTable` at `Gcd/Gcd.c:91-93`, driven by the resource HOB's
attribute bits. Nothing in `Gcd.c` ORs them in afterwards — those three table rows
are the only mentions of the three capabilities in the file. And this board's own
table gives every `Reserv` row one of two attributes, both of which fail the test:

  * **`UNCACHEABLE`** → the capabilities come out as `EFI_MEMORY_UC` alone, with
    neither `PRESENT` nor `INITIALIZED`, so the equality at `Page.c:402` is false.
    That is `AOP CMD DB`, `SMEM` and `PIL Reserved` (`device/config/uefiplat.cfg:8-10`).
  * **`SYS_MEM_CAP`** → the capabilities *include* `TESTED`, so the left side of
    the comparison is `PRESENT|INITIALIZED|TESTED` and can never equal
    `PRESENT|INITIALIZED`. That is `Display Reserved` (`uefiplat.cfg:27`).

The other two `Reserv` rows, `ABOOT FV` and `Kernel`, are not even GCD-Reserved:
the switch at `Gcd.c:2658-2688` maps a `SYS_MEM` resource HOB whose attribute mask
is exactly `TESTED` — which is what `SYS_MEM_CAP` is — to
`EfiGcdMemoryTypeSystemMemory`, and the allocation HOB then marks the range
reserved *inside* system memory (`MemoryInitPeiLib.c:98`).

So `PromoteMemoryResource` returns FALSE on its first call and every call after
it, and the retry rung it guards is unreachable on this board. **No refused request
has a hidden region behind it.** Step 4.18's reading — that the deciding factor is
heap state at the instant a request arrives — keeps its force, but narrows: it is
not "the allocator could not find a region that a retry would have freed", because
there is no such region to free.

Worth saying in the same breath, because it is why this must not be "fixed": if
those attributes were made promotable, the first refused allocation would hand the
allocator the 344 MiB `PIL Reserved` region, which is where XBL put the firmware
this payload is running beside. The accident is load-bearing.

### The whole supply is one row, and the row is ours

`AddHob` (`MemoryInitPeiLib.c:86-99`) turns each `AddMem` row into a resource
descriptor HOB and, for `SYS_MEM` rows, a memory allocation HOB carrying that
row's own `MemoryType`; `CoreAddMemoryDescriptor` (`Page.c:546-597`) adds the type
to the memory map; and `CoreFindFreePagesI` looks at a map entry only if
`Entry->Type == EfiConventionalMemory` (`Page.c:956`) and its attribute has no
`EFI_MEMORY_SP` (`Page.c:963`). Exactly one row of this board's map carries
`Conv`:

```
0x9B800000, 0x02360000, "DXE Heap",  AddMem, SYS_MEM, SYS_MEM_CAP, Conv, WRITE_BACK_XN
```

`device/config/uefiplat.cfg:16` — 9056 pages, and the 450-page runtime-bin block
comes off the top of it (`Page.c:585-592`). Everything else that is not a register
range is an *allocated* range of its own type and is not allocatable memory at all:
the `BsData` rows are the ones DxeCore itself runs on and out of.

That row is this repository's, and not a number inherited from XBL.
`gauguinPkg/Library/MemoryMapLib/MemoryMapLib.c` is generated from
`device/config/uefiplat.cfg` by `tools/make_uefi_platform.py` — it says so in its
own header comment — and it is compiled into our FV and linked into PrePi, whose
`MemoryPeim` is what builds the HOBs. `tools/heap-compare.py` records the other
half: every sibling platform at this base declares 60.0 MiB, so 35.4 MiB is this
board's own figure, and the 24.6 MiB between `Sched Heap` and `FV Region` is a
carveout this config never mentions, which extending the row would be claiming.

None of this says the row is too small. It says that if the reading comes back
"room", the row is the only place room could come from — and it is a rebuild of
this repository rather than an XBL flash. What is *not* re-opened by that is where
the extra pages would come from: step 4.28 measured that the 24.6 MiB above the row
is a carveout this config never mentions, so growing the row is the mechanism and
not yet an available supply.

### The two boot-services PCDs are decorations

`BuildMemoryTypeInformationHob` (`EmbeddedPkg/Library/PrePiHobLib/Hob.c:887-908`)
is what turns the platform's memory-type figures into the HOB the bins are built
from, and it emits five entries: `EfiACPIReclaimMemory`, `EfiACPIMemoryNVS`,
`EfiReservedMemoryType`, `EfiRuntimeServicesData`, `EfiRuntimeServicesCode`
(`:893-903`). `SiliciumPkg.dsc.inc:45-53` also sets

```
gEmbeddedTokenSpaceGuid.PcdMemoryTypeEfiBootServicesCode|1000
gEmbeddedTokenSpaceGuid.PcdMemoryTypeEfiBootServicesData|800
```

and nothing reads either token. `PcdMemoryTypeEfiBootServicesCode` occurs in two
files in the tree: the DSC line that sets it, and `EmbeddedPkg.dsc`, which sets it
to 0. There is no `Info[]` slot for a boot-services type in the builder, so no
such bin is ever created.

Two consequences, and the second is the one that saves a session. The bins are
only the runtime types — 300 + 150 = 450 pages, which is the block the digest's
`hob_rc`/`hob_rd` check against the device's own HOB — so 450 pages against 9056
is not the fault. And those two PCD lines are not a lever for it, which matters
precisely because they are the first thing in the DSC that looks like one: a
session that read `1000` and `800` as the boot-services bins would be tuning
something that does not exist.

### What this does not decide, and what the reading now means

All of the above is a statement about the allocator's *shape*, read off sources.
It does not say which of step 4.18's two shapes the run is in; that is what the
probe on the phone is for, and the decode is now bounded rather than open:

  * **Bottom row `K <n> <phase><why> <started>/<apriori> free=<pages> <guid>`** —
    `free=` is a number from the table above. `free=0` means the heap was empty at
    that instant, whatever it started with. A large `free=` on a line whose
    `<why>` letter is the `Out of Resources` letter means the refusal was *not*
    about room, and the next reading is the `P2 BINS` census, not a bigger heap.
  * **Bottom row `KEY <errors>/46 err=<name> at=<i> free=<pages> miss=<i>`** —
    `CoreDispatcher` returned and the digest is printing, so the whole funnel is
    on the panel, `bs9=` included.
  * **`Loading Driver at ...` still on the panel** — the flash did not take. The
    three prints that make that row are silenced in the tree
    (`Image/Image.c:890-937`), so the payload in `work/out/p2-variants/` and every
    build after it cannot print it; only the `p2-4.20`-class images can. Step 4.35
    is what that now buys, and it is more than a yes/no.

## Step 4.35 — Every payload of this phase is 1,142,784 bytes, and only one of them can say where a run stopped

Step 4.34 left a decode that turns on the bottom row of the panel. Before waiting
on that row it is worth checking something about the payload that produced it,
because this project has already lost a session to a control image that "stopped
being the bytes it named without the name changing" (step 4.30) — and the same
trap is sitting under this phase, larger.

### Three builds, one size, two of them with the same filename

```
work/out/boot-now-0923.img                        1,142,784  sha256 3547fd0487d33387...
work/out/p2-4.20/Mu-gauguin-silicon-gzip.img      1,142,784  sha256 dbf131d254374646...
work/out/p2-variants/Mu-gauguin-silicon-gzip.img  1,142,784  sha256 7c8fdb5a1a272ab6...
```

Every payload built for this phase is the same size to the byte, and two of the
three are called `Mu-gauguin-silicon-gzip.img`. They are not near-duplicates
though: they differ in 17,414 of 17,856 64-byte blocks, from offset 0. What
separates them is which bring-up instruments they carry, and that is decided
inside the compressed firmware volume, where a grep of the `.img` finds nothing.

`tools/probe-fingerprint.py` walks the volume the way `tools/fv-inventory.py`
does — it imports that walk rather than repeating the padding rules — and reports
which instruments are present. The markers are **read out of `Dispatcher.c`**, per
named function, rather than typed into the tool; that is not tidiness. The first
draft of this tool carried a hand-typed `err=%a at=%d` for `P2Key`, the real
format is `err=%r at=%d` (`Dispatcher.c:286`), and the tool cheerfully reported
`P2Key` absent from an image that has it. A tool whose job is to say which
instrument is in an image cannot invent its own fingerprints.

```
                       P2Digest  P2Apri  P2Bins  P2Retry  P2Key  P2Tick
boot-now-0923            yes       no      no       no      no      no
p2-4.20                  yes      yes     yes      yes      no      no
p2-variants              yes      yes     yes      yes     yes     yes
```

A ladder: each build added a rung, and the two rows that matter are the last two
columns. `P2Tick` is the one that makes a stopped run legible — one row per
attempted dispatch — and `P2Key` is the one that makes the *bottom row*
legible.

### `P2Key` is in exactly one build, and that makes the bottom row categorical

`P2Digest` ends `P2Bins ();` then `P2Key ();` (`Dispatcher.c:2464`, `:2470`, with
the comment at `:2466` saying "Last, so that it is the last populated row of the
panel"). `P2Key` is a `for` and an `if/else` that prints one of two lines
(`:264`, `:275`) — it cannot print nothing. So on the `p2-variants` build the last
populated row of the panel is the `KEY` line in *every* state where
`CoreDispatcher` returned, including after a wipe.

On `p2-4.20` there is no `P2Key`, so the last populated row is `P2 RETRY`. **That
is step 4.29's reading**, and it is not a contradiction of
`tools/console-budget.py`, which computes the same thing and gets `KEY` — the two
are describing two different builds. What it does mean is that the bottom row is
now a build detector that needs no readback and no host:

> **A `P2 RETRY` row as the last populated row of the panel means the payload on
> the phone is the `p2-4.20`-class build, i.e. the flash did not take the newest
> one.** On the newest build `P2Key` follows that row with nothing in between, so
> it cannot be the last row of a run that got that far.

And the mirror of it, for the row step 4.34 was written around: `Loading Driver
at ...` on the panel is the same detector pointing the other way, because those
three prints are commented out in the tree (`Image/Image.c:890-895`, `:933` for
the PDB name, `:936` for the newline) and no build from `p2-variants` onward can
emit them.

### The driver's name is gone from the dispatch rows, so the GUID is the reading

The reason those three prints were silenced is in the comment beside them: the
load address and the driver name printed once per image is the whole of what the
panel holds during dispatch, and it is what buried the digest across four
sessions. But silencing them has a cost that this phase now has to pay, and it is
worth stating plainly because the natural assumption is the opposite:

> **On the current build, a dispatch row identifies a driver by GUID and by
> nothing else.** There is no row above it naming the same driver in English. The
> `%g` in `K <n> <phase><why> <i>/<j> free=<pages> <guid>`
> (`Dispatcher.c:677`, the format string in `P2Tick`) is `DriverEntry->FileName`,
> handed in at the two call sites (`:1122` for a failed load, `:1167` for a start),
> the FFS file's own GUID,
> and the mapping from that GUID to a name exists in the firmware volume and not
> on the screen.

So the volume has to supply it, which is what `fv-inventory.py --roster` and
`--name` now do. `--roster` prints every FFS file as `GUID type size name`, and
`--name` resolves a transcribed GUID against that list, case- and
hyphen-insensitively and *ranked rather than matched*: a wrong digit yields a
near neighbour rather than a confident miss, and the number of differing digits is
printed, so a one-digit answer reads as the guess it is. Measured against the
`p2-variants` roster:

```
1FA1F39E-FEFF-4AAE-BD7B-38A070A3B608 (one digit changed by hand)
  1 digit(s) differ  1FA1F39E-FEFF-4AAE-BD7B-38A070A3B609  type 0x07  PartitionDxe
 25 digit(s) differ  D6A2CB7F-6A18-4E2F-B43B-9920A733700A  type 0x05  DxeCore
```

The roster is 123 files and the numbers in it check out against an observation
made on the glass rather than on the host: the listing's own order puts `Fat` at
FFS index **37** (the listing prints a one-line header first, so `Fat` is its
38th line) — which is exactly what the panel said in the step 4.33 session,
`Fat.efi` as file 37 of 123. The name `Fat` and not `Fat.efi` is the same string
minus the extension the old print appended from the PDB name.

### The decode, as the panel will now present it

For the newest build, bottom row first: **`K`** means the run stopped inside
dispatch, **`KEY`** means `CoreDispatcher` returned, and **`P2 RETRY`** means
neither build is on the phone — it is the older one. Within the `K` case the
phase letter is the discriminator, from the two call sites
(`Dispatcher.c:1111`, `:1156`, the two `P2Record` calls that set it):

  * **`L`** — `CoreLoadImage` (`:1081`) failed for that driver, so its entry point
    never ran and no driver code executed between this row and the previous one. Its
    `<i>/<j>` is position in the Apriori order, and its `free=` was measured by
    `P2Tick` at `:1122`, after `CoreReleaseDispatcherLock` at `:1116`, so it is the
    allocator's state as the next driver will find it.
  * **`S`** — the entry point ran and returned an error. The GUID names the
    driver, and any rows between this one and the previous `K` row are that
    driver's own output.
  * **`S` with a `Success` spelling** — the driver started cleanly, which is what
    the nineteen in step 4.9's table are.

A bottom row that is a `K` row therefore fixes both *which* driver and *how far*
the run got, and `tools/fv-inventory.py IMG --name <guid>` turns the first of
those into a name. `tools/probe-fingerprint.py --expect P2Key IMG` is the
pre-flight for the other direction: it is the check that the payload being
written is one whose screen can be read at all, and it is the check that a size
comparison passes while being blind to.

---

## Step 4.36 — The ring is rebuilt every boot, so `logfs` cannot carry a dead payload's words

`tools/read-logfs.py` arrived in step 4.6 and its five slots have been read more
than once, but always for their *stages* — the table of
`Start EBS`, `Shutting Down UEFI Boot Services` and so on that tells a reader how
far a boot got. This step read them for a different question: **are they five
different boots, or one log repeated?** The answer decides whether physical
memory is worth mining for the payload's output, so it had to be settled before
any of that work was worth doing.

### The five slots are five consecutive bootloader runs, and the difference between them is nothing

Measured on `work/bllog-20260924-logfs/txt/`, slot 0 being the newest:

```
UEFILOG0.TXT  13,232 bytes   286 lines   Shutting Down UEFI Boot Services: 4391 ms   Start EBS [ 4391]
UEFILOG1.TXT  13,289 bytes   288 lines   Shutting Down UEFI Boot Services: 4130 ms   Start EBS [ 4131]
UEFILOG2.TXT  13,291 bytes   288 lines   Shutting Down UEFI Boot Services: 4126 ms   Start EBS [ 4126]
UEFILOG3.TXT  13,291 bytes   288 lines   Shutting Down UEFI Boot Services: 4134 ms   Start EBS [ 4134]
UEFILOG4.TXT  13,289 bytes   288 lines   Shutting Down UEFI Boot Services: 4145 ms   Start EBS [ 4145]
```

Each file is a fresh ring, beginning with the three-line legend and then SBL's own
statistics — the file on the phone and not a rendering of a persisted buffer:

```
Format: Log Type - Time(microsec) - Message - Optional Info
S - QC_IMAGE_VERSION_STRING=BOOT.XF.3.3-00285-BITRALAZ-4
S - IMAGE_VARIANT_STRING=BitraPkgLAA
S - OEM_IMAGE_VERSION_STRING=c4-miui-ota-bd105.bj
S - Serial Number @ 0x00786134 = 0xd695efb1
S - Core 0 Frequency, 1459 MHz
S -     82913 - PBL, End
S - DDR Frequency, 1555 MHz
```

and each ends at the handoff. Raw, slot 0 against slot 1 is **354 differing
lines**, because every line that carries a time carries a different one; masking
the digits collapses that to **53**, essentially all of them the whitespace of the
time column — `D -         N - boot_flash_init` against `D -        N -
boot_flash_init`. So the substantive difference between the five is small, and the
correction below is about what the small difference actually is.

Two readings come out of that, and the second is the one that matters:

  * **The ring is per-boot and the renderer emits only its own boot's entries.**
    There is no preserved tail from the boot before, which is what a persisted
    ring would show at the top of every file after the first. Whatever the
    `-*-LOG_OVERLAP-*-BOOT=` string in `ULogDxe.efi` is for, it is not firing:
    `grep -ac LOG_OVERLAP UEFILOG*.TXT` is 0 in all five.
  * **The payload's death is invisible in `logfs`.** Five boots, and every one of
    them reached `Start EBS`, spread over 265 ms (4126 to 4391 ms of bootloader
    time). ABL stops logging at the handoff and the reset that follows is not
    written by anything, because the only thing running when it happens is the
    payload that cannot write. So even if every slot were readable perfectly,
    the bootloader's own account of a failed run is one line that says the
    handoff happened.

### Correction: the five slots are not five iterations of the loop, and slot 0 is the recovery boot

The paragraph above first went into this document saying the five files were "one
bootloader run written five times, not five different events", on the strength of
the 53 masked diff lines. That is wrong, and the lines that make it wrong are two
that the digit-masking hid because they contain no digits — plus a third that is
identical in all five and therefore says more by its sameness than by its value.
Comparing all five properly:

```
                        slot 0   slots 1-4
KeyPress:1, BootReason:0     1        0      <- the two lines the masking hid
Fastboot=0, Recovery:1       1        0
PM: HARD RESET by PS_HOLD   yes      yes      <- identical in all five
PON Reason is 1 cold_boot:1 yes      yes
```

**Slot 0 is the boot into recovery** — `Recovery:1` and `KeyPress:1`, which is the
key the user held to take this very dump. So it is not an iteration of anything;
it is the intervention that produced the file. And **slots 1–4 are four boots, each
one entered by a `PS_HOLD` hard reset** — the PMIC's own record of what made the
reset that preceded the boot, read by SBL1 at init. `PS_HOLD` is the power button,
so those four boots were entered by the user's own twenty-second hold, not by the
payload. **Not one of the five slots is a watchdog-triggered boot.**

That does not weaken the per-boot conclusion; it strengthens it, because a slot is
written with content specific to the boot that wrote it, and slot 0 differs from
the other four in exactly the two lines a recovery boot would differ in. A ring
that persisted across boots would still be visible, and is not.

What it does add is the one thing `logfs` turns out to hold that this document did
not know it held: **the cause of the reset that preceded each boot.** It is not a
record of *why* a payload died — `BootReason:0` and `PON Reason is 1 cold_boot:1`
are identical in all five and do not distinguish a power-hold from anything else
here, and ABL's own last act is still the handoff. Whether a watchdog-triggered
reset is named differently from `PS_HOLD` is untested. Testing it costs a boot loop
plus a `logfs` read and yields only the datum that the payload died, which is
known already, so it is recorded here as a capability and not proposed as a next
step.

### A `grep` that answered "nothing" for the wrong reason

The first pass over these files reported no hits at all — no `LOG_OVERLAP`, no
`boot`, no `reset`, no `watchdog` — and that looked like a clean negative result.
It was not. Each file carries 800 NUL bytes, so `grep` classifies them as binary:
`file` says `data`, and a plain `grep -l`/`grep -c` against them produced silence
rather than a count, which is not the same thing as zero matches. Adding `-a`
made the `S - ` lines appear immediately.

This is the third instance in this phase of a tool reporting absence for a reason
that has nothing to do with the thing being absent, and it is the same mistake
each time: the 80-byte toybox `dd` statistics shift, the hand-typed fingerprint
marker that read `err=%a` where the source says `err=%r at=%d`, and now a text
search over a file the search decided was not text. **Everything read out of
`logfs` needs `-a`**, and the negative result above was re-taken with it.

### Two things the platform map got corroborated against

Neither of these was the object of the step, and both are cheap enough to record.

  * **`MaxMemoryRegions = 74` is the row count of its own table.** The
    `[Config]` key was previously noted as having no consumer anywhere in this
    repo, which is true of a grep for the *string*; the `[MemoryMap]` table in
    the same file has exactly 74 rows, as `tools/read-dram.py`'s parser now reads
    it. So the number is XBL's count of the rows in this file, not a Mu-Silicium
    parameter, and the earlier note was about the wrong question.
  * **The device tree keeps Linux out of the framebuffer.** `reserved-memory` has
    `memory@a0000000 { reg = <0x00 0xa0000000 0x00 0x2300000>; }` and
    `memory@a2300000 { reg = <0x00 0xa2300000 0x00 0x100000>; }`, which together
    are `0xa0000000..0xa2400000` — **exactly** the map's
    `Display Reserved 0xA0000000 + 0x02400000` row, to the byte. Two independent
    descriptions of the same region agreeing is worth something. It is not worth
    much, though, and the caveat has to travel with it: the DT's outer
    `memory@80000000` node ships with size 0 and is patched by XBL at runtime, so
    the DT readable here is not the DT the kernel is given.

### What this kills, and what it leaves

**It kills the idea that the payload's output can be mined out of DRAM from
TWRP.** The reasoning is now two-legged rather than one: the ring is rebuilt on
every boot, so a payload's writing into it would be superseded before anyone
could read it; and `0x9FFF7000` has no `reserved-memory` node covering it, so the
Linux kernel behind TWRP owns that page and has been running for minutes by the
time `adb` answers. Either leg alone would be enough. **The panel therefore
remains the only channel for the run that just failed**, and the pending reading is
unchanged by everything in this step.

What it leaves is `tools/read-dram.py`, and one honest use rather than four
speculative ones: `--probe` answers a question this project has never once asked,
which is whether physical memory is readable on this phone at all. `/dev/mem`
requires `CONFIG_DEVMEM` and not `CONFIG_STRICT_DEVMEM`, and TWRP's kernel is not
this project's to choose. Until it is tried, every instrument that would sit on
top of a memory reader is built on an assumption. The other three subcommands
(`--iomem`, `--ulog`, `--fb`) are here because they cost a line each and because
each is a second, independent statement about a map this project has been reading
as ground truth for several sessions.

The tool is read-only, and it inherits the two rules this phase has already paid
for — `status=none` **with** a per-chunk truncation, because TWRP's toybox 0.8.4
writes `dd`'s statistics to stdout where `adb exec-out` collects them; and an
on-device `sha256sum` as a second opinion that is not the host, because every
other check compares two copies one program made. It adds a third: `bs=4096` with
`skip=addr/4096` and never a large block with a proportional skip, because a skip
that is not a whole number of blocks silently reads from somewhere else and a
memory reader that is 4 KiB out returns plausible bytes.

It was exercised against a synthetic 64 KiB "memory" carrying two markers, with a
fake `dd` that appends toybox's statistics tail exactly as the real one does. Four
assertions pass: the truncation (the reconstruction is byte-exact), the two hits
reported at `0x2000` and `0x9000` with their surrounding text, an absent marker
exiting 1 with a reading rather than a crash, and a dead link stopping at 0 bytes
instead of silently zero-filling the rest.

What it does **not** answer is whether `/dev/mem` is readable on this TWRP at all.
That is one `--probe` away and it needs the phone.

## Step 4.37 — The twenty-seven that would not load are not the big ones, and two of them asked for exactly the same room

Step 4.12 read 46 characters of `P2 SEQ` off the panel and could say only which
*positions* had failed, because a position could not be turned into a driver name
without transcribing a 36-character GUID off a neighbouring `K` row. That is now
fixed on the host side: **`tools/apriori-index.py`** prints the Apriori index, the
GUID and the driver's own name for every position, reads its letter table out of
`P2WhyLetter()` rather than carrying a hand-typed copy, and asserts its own
position mapping against two measurements that came from the volume and not from
the string. The 46 characters decode to this:

     pos  ap    letter  driver                              phase
    ----  --    ------  ----------------------------------  ------------------------
       0   1    s       PcdDxe                              EntryPoint returned success
       1   2    s       EnvDxe                              ...
       ...
      17  18    s       NpaDxe                              ...
      18  19    L       RpmhDxe                             CoreLoadImage failed
      19  20    L       PdcDxe                              CoreLoadImage failed
      20  21    L       ClockDxe                            CoreLoadImage failed
      21  22    s       ShmBridgeDxe                        EntryPoint returned success
      22  23    L       ScmDxe                              CoreLoadImage failed
      23  24    L       DiskIoDxe                           CoreLoadImage failed
      ...
      45  46    L       I2C                                 CoreLoadImage failed

`P2 SEQ`'s alphabet is four characters, not ten, and the tool now keeps the two
tables apart instead of decoding both with `P2WhyLetter`'s. Three facts fall out of
the string that do not need the panel to be re-read, because they are properties of
the characters already recorded:

  * **no `?` anywhere** — `?` is what the promotion loop stamps into
    `mP2AprioriRes` before anything runs, so its absence means the drain reached
    every one of the 46 promoted entries. The batch ran to the end of the array;
    nothing was skipped.
  * **no `S` anywhere** — `S` is `CoreStartImage` returning an error. Its absence
    means every driver that loaded also started, and **all 27 failures are at
    `CoreLoadImage`, so no driver in this array ran and failed.** The exception is
    hypothetical, not observed: `P2 ERR` would spell an `S` row in words.
  * **one contiguous failing block, positions 18 through 45, with a single
    survivor at position 21.** `sx18 (0-17), Lx3 (18-20), sx1 (21), Lx24 (22-45)`.
    A monotone cause — a resource that ran out at position 18 and stayed out — has
    to explain why position 21 succeeded in the middle of it.

### The cause is not room, and the host can say so without the panel

> **Read this subsection as a re-derivation, not a discovery.** Everything in it
> except the threshold count was already established earlier in this same document,
> and is only being re-measured here through a second walk. The
> `PdcDxe`/`ShmBridgeDxe` pairing first appears in step 4.12's "What the host can
> rule out, and it is more than expected" (`PdcDxe` … `36,864` and an `L` at
> position 19; `ShmBridgeDxe` … `36,864`), becomes the boundary argument in step
> 4.17 and step 4.18 ("Identical request, opposite result"), survives step 4.23's
> corrected table in the unit that matters (`PdcDxe` 9 pages, `ShmBridgeDxe` 9
> pages, 56 pages of cumulative demand apart), and reaches its final form in
> `tools/pe-facts.py`,
> whose closing note says *"the deciding factor is neither the request nor the
> total: it is heap state at the moment each one arrives"* and whose verdict on
> the field-by-field question is *"no field separates the 19 s from the 27 L by a
> single value or a threshold."* Writing it here as a finding was a slip. The tool
> this step adds is a **decoder**; its `--sizes` mode walks ground
> `tools/pe-facts.py` had already walked, and the docstring now says so. The two
> agree to the byte, which is the useful part of the repetition and the only reason
> to keep it.
>
> **The two size tables in this document are on different columns and must not be
> read as contradicting each other.** The prose in step 4.12's "Ruled out: the PE
> images" quotes `ffs_size` — 9,338 B for `ReportStatusCodeRouterRuntimeDxe`,
> 19,050 B for `EmbeddedMonotonicCounter`, 307,246 B for `DALSys`, 385,166 B for
> `BdsDxe` — while this subsection quotes `SizeOfImage`. On the same 46 entries the
> `ffs_size` ranges are 9,338..307,246 (`s`) and 19,050..385,166 (`L`), so on that
> column the smallest success really is smaller than the smallest failure in
> `ReportStatusCodeRouterRuntimeDxe` vs `EmbeddedMonotonicCounter` — the sentences
> are both true and about two different numbers. `SizeOfImage` is the one to prefer
> when the question is what the allocator sees, because it is the field
> `CoreLoadPeImage` acts on; the two columns also rank the candidate cuts
> differently, and `SizeOfImage` cuts better (17 wrong against 18).
>
> What this step actually adds on the size axis is the count, not the verdict:
> **the best `SizeOfImage` threshold is 17 wrong of 46**, where `pe-facts.py` had
> stated the verdict qualitatively. The decoder, the corrected anchor pair and the
> 46-versus-69 closure below are new; this is not.

This is the part worth having before anyone photographs a screen, because it
retires the move the phase has been circling: **the failures are not the big
ones.** Measured out of the payload itself, using `fv-inventory.py`'s section walk
to the `EFI_SECTION_PE32` and the PE optional header's `SizeOfImage` (which is what
`CoreLoadPeImage` actually allocates, not the FFS file size, which is padded and
carries a UI section):

  * **`PdcDxe` and `ShmBridgeDxe` are a matched pair.** PE32 length 36,864 bytes
    and `SizeOfImage` 36,864 bytes — *identical* to the byte — and they are two
    dispatch slots apart in the same run (`PdcDxe` at position 19, `ShmBridgeDxe`
    at 21, with only the failed `ClockDxe` between them). `PdcDxe` failed to load;
    `ShmBridgeDxe` loaded and started. **No account of these 27 failures as an
    allocation failing fits this pair:** if the allocator had room for one it had
    room for the other, and if it did not then neither should have loaded. This is
    not a hand-picked pair — the tool finds all entries with equal `SizeOfImage`
    on both sides and prints the one closest together in dispatch order, and this
    is it.
  * **No size threshold classifies this run.** Sweeping every `SizeOfImage` as a
    candidate cut and asking how many of the 46 entries "refuse anything this big
    or bigger" would get wrong, the best rule is *"refuse anything at or above
    36,864 bytes"*, and it is **right about 29 of 46 and wrong about 17**. A rule
    that is wrong about a third of the entries is not the rule. The failures and
    the successes overlap across almost the whole width of the size range —
    successes 32,768..393,216 (`PlatformInfoDxeDriver` and `CmdDbDxe` at the
    bottom, `RuntimeDxe` and `ReportStatusCodeRouterRuntimeDxe` at the top, median
    49,152), failures 36,864..397,312 (`PdcDxe`, `DALTLMM`, `WatchdogTimer` and
    `EnglishDxe` sharing the bottom, `BdsDxe` the top, median 73,728) — and **only
    `BdsDxe` is strictly above the largest success** (397,312 against 393,216),
    with `VariableRuntimeDxe` and `ResetSystemRuntimeDxe` sitting exactly on it at
    393,216. Three are at or above that value; one is above it. The strict reading
    is the one to keep, because the at-or-above pair is a tie and not a separation.
  * Physical position does not separate them either, and this is a re-confirmation
    rather than a discovery: the failures sit at physical file indices 5,6,7,8,9,
    12,13,15,16,18,19,23,33..38,40,44..49,54,73 and the successes at
    2,3,4,10,11,17,24..31,39,42,43,52,74 — interleaved throughout. `fv-census.py`
    had already ruled a physical cutoff out from the 5-versus-74 pair; this is the
    same fact seen from the Apriori side.
  * Every Apriori entry in the volume — all 70, not only the 46 that ran — is
    `PE32+` (`0x20B` optional-header magic) with machine type `0xAA64`, so this is
    not a wrong-architecture load and not a 32-bit image the loader refuses.

So **a larger DXE heap, a larger reserved region, or a bigger `P2LargestAlloc`
ladder cannot be the fix**, and the `free=` value in a `K` row is very unlikely to
be the explanation. That is not a statement that memory is fine — it is a statement
that these 27 refusals were not decisions about how much room a driver needed.

### The prediction this makes, which the panel can kill

Because the cause is not room, the `K` rows' `free=` should be **flat and large
across the whole failing block** — the same ladder value at position 18 as at
position 45. If a reading shows that, size is out for good and the only remaining
datum is `P2 WHY`'s letters, which is the row that has never been read. If instead
`free=` collapses at position 18 and stays collapsed, then something *is* being
consumed and the matched `PdcDxe`/`ShmBridgeDxe` pair needs an explanation that is
not about size — an allocation that only the failing images make, before the one
that matters. Either reading is decisive, which is what makes this worth writing
down rather than leaving as an expectation.

And the shape of `P2 WHY` is itself the next discriminator, before any name is
looked up: **if all 27 letters are the same, the cause is one thing affecting
27 drivers; if they differ, the cause is per-driver** and the ones that are `R`
(`EFI_OUT_OF_RESOURCES`) have to be separated from the ones that are `N`, `X`, `E`
or `U`, because those four have no mechanism in common with `R`.

### Two things reconciled while building the decoder

  * **Position 45 is `I2C`, not `AdcDxe`.** The first draft of the tool asserted
    `AdcDxe` there, from misreading the decode table's `miss=47 9143B2B7-...` row.
    `9143B2B7` is Apriori file index 46 — `AdcDxe` — and position is file index
    minus one, so `AdcDxe` is position **46** and position 45 is `I2C`. The
    corrected anchors are the two `fv-census.py` measured: position 21 is
    `ShmBridgeDxe` and position 36 is `SecurityStubDxe`, both of which the table
    satisfies and both of which the tool now asserts on every run.
  * **The 69-versus-46 question is closed, and the answer is that it was never a
    contradiction.** `tools/apriori-index.py` reports 69 promotable entries in the
    volume while the panel shows 46 characters, and the two can be reconciled
    without deciding anything, because **both open readings of `P2 APRI` produce
    exactly 46:**
    - the array read whole (`entries=70`, then `miss=47`) promotes `ap1..ap46` and
      does not promote `ap47..ap69`;
    - the array read 368 bytes short (`entries=47`, `unhit=1`) promotes `ap1..ap46`
      and `ap47` does not exist.

    Both give a 46-character `SEQ` and both name the same 46 drivers. So **the
    string that has been read three times cannot decide the fork**, which is worth
    stating plainly because it looked like it might: the row that decides it is
    `P2 APRI` itself. `mP2ApriSum` is taken over `SizeOfBuffer`, so a short read
    prints the hash of a prefix and `sum=` names its own length — `b4ba9d75` is 47
    entries, `a998b263` is all 70 — and `KEY`'s `unhit=`/`miss=` corroborates it
    (`unhit=1, miss=none` against `unhit=24, miss=47`).

> **Corrected in step 4.47.** The bullet above is right that the string cannot decide
> the fork, and wrong that a fork is what is left: the second reading needs a
> declared section size of 756, the volume declares 1124, and `GetSection` reports
> what is declared — so `sum=b4ba9d75` and `unhit=1, miss=none` are not readings this
> image can produce. They are now the signature of a *different* image, which is a
> more useful thing for them to be: `sum=a998b263` with `apriori=46/70` is what this
> payload hashes to, so the line identifies the build. `miss` remains the one open
> number.

The decoder is a host tool and its input is a string; it changes nothing about what
the phone is doing. **The pending reading is unchanged and is still the bottom of
the panel** — the `KEY` row and the `P2 WHY` row under it. What has changed is that
neither of them now requires anyone to write down a GUID.

## Step 4.38 — The one loader failure that memory cannot explain is also not happening, and it is measured over the bytes

Step 4.37 left the 27 `L`s with a verdict — not room — and no mechanism. There is
exactly one failure inside `CoreLoadPeImage` that is neither memory nor the image's
own headers, and it was the last candidate standing on the host's side of the
panel, so it is worth closing by measurement rather than by argument.

**The mechanism, from the source.** `PeCoffLoaderRelocateImage` walks each base
relocation record and switches on the top four bits of the word
(`BasePeCoff.c`, `switch ((*Reloc) >> 12)`). Five codes have their own `case`:
`0` `ABSOLUTE`, `1` `HIGH`, `2` `LOW`, `3` `HIGHLOW`, `10` `DIR64` — and **every
other code falls to `default:`**, which calls `PeCoffLoaderRelocateImageEx` and
returns its status when it fails. On AArch64 that function is
`MdePkg/Library/BasePeCoffLib/PeCoffLoaderEx.c`'s first entry, and its whole body is
`return RETURN_UNSUPPORTED;`. `BasePeCoffLib.inf` binds that file for
`Sources.AARCH64`, so on this platform it is the function that is compiled in. One
relocation of any other type therefore fails the *entire* load with
`EFI_UNSUPPORTED` — letter `U` in `P2 WHY` — before `CoreAllocatePages` is ever
reached. It is a mechanism that would produce any number of contemporaneous
failures, it needs no allocator state at all, and it is invisible to every
instrument in this project that watches memory. That is what made it worth
chasing: `free=` cannot see it.

**It is not happening.** The relocation blocks are bytes, so this is answerable from
the image on the host. `tools/pe-facts.py` now walks them: for each promoted
DRIVER it finds the `IMAGE_DIRECTORY_ENTRY_BASERELOC` directory (data directory 5),
walks its blocks by their own `SizeOfBlock` fields, and collects the type code of
every entry.

- **measured over all 69 DRIVER entries of `work/out/p2-variants/Mu-gauguin-silicon-gzip.img`
  (every entry of the Apriori array that resolves to a DRIVER), the set of
  relocation types present is `{0, 10}`** — `ABSOLUTE` and `DIR64`, two of the five
  the loader handles itself. **The unhandled set is empty.**
- **over the 46 promoted entries alone it is the same `{0, 10}`, empty unhandled,
  on both sides of the `s`/`L` split.**

So the `default:` arm is reached by nothing in this volume. The AArch64 stub is
compiled in and correct to exist, and no image in the run exercises it.

**A correction, stated as one.** An earlier pass over this same question reported the
type set as `{0, 1, 3, 10}`. That was wrong, and the reason is worth keeping: the
earlier walk read the relocation words at an offset taken from the section order
rather than by resolving the directory RVA through the section table. `ABSOLUTE`
entries (`0`) are padding — the loader skips them and never touch a byte — and the
`1`/`3` values came from words that were not relocation entries at all. The walk
that is in the tool now bounds itself three ways: the directory must have a non-zero
size, its RVA must resolve inside a section, and every block is stepped by its own
`SizeOfBlock` and abandoned if that field would run past the directory. It is a
strictly narrower set, and it is the set the loader would see. The exoneration does
not depend on which reading is right — both leave the unhandled set empty — but the
tool should print the number the loader acts on.

**Four promoted images have no relocation directory at all**:
`StatusCodeHandlerRuntimeDxe` (position 3), `EmbeddedMonotonicCounter` (37),
`RealTimeClock` (38), `CapsuleRuntimeDxe` (41). This is worth naming because it was
on the list of suspects for a different reason: a file with no `.reloc` cannot be
rebased, and `CoreLoadPeImage` has a branch for exactly that. It is not the branch
being taken. `IMAGE_FILE_RELOCS_STRIPPED` — bit 0 of `Characteristics`, the bit the
loader actually branches on — is **clear on every one of the 80 DRIVER files**, and
the tool prints both facts separately (`has_reloc` from the section table,
`reloc_stripped` from the header) precisely so they stop being conflated. All four
of these take `AllocateAnyPages` like everything else.

**What this leaves.** The loader's failure surface is now enumerated rather than
searched: `EFI_INVALID_PARAMETER` (a NULL or too-short file path),
`EFI_UNSUPPORTED` (machine type, subsystem, or an unhandled relocation — the last now
measured away), `EFI_NOT_FOUND` (`GetFileBufferByFilePath`), the security protocols
(which are not installed here, because `SecurityStubDxe` is itself one of the 27),
`EFI_OUT_OF_RESOURCES` (three allocation sites), and `RETURN_LOAD_ERROR` from the
relocation guards. `EFI_DEVICE_ERROR` is produced nowhere in this path, and
`RETURN_VOLUME_CORRUPTED` is returned by no PE/COFF loader function at all. The
remaining live candidate is `EFI_OUT_OF_RESOURCES`, and **the panel is what
distinguishes it from the rest**: `'R'` in `P2 WHY` is that status and nothing else,
and it is one character wide.

## Step 4.39 — The three `P2 SEQ` readings came from a build that cannot print a status, and that is a fact about the payload

Step 4.29's ladder has six rungs and it is missing two, and the two it is missing
are the two that decide whether a panel reading can answer anything at all. Both
were found the same way: by inflating each payload's `FVMAIN` (through
`tools/fv-inventory.py`'s walk, because the strings live inside the LZMA GUIDed
section) and searching for literals that `tools/probe-fingerprint.py` resolves out
of `Dispatcher.c`.

```
                      P2Digest  P2 SEQ  P2 WHY  P2 ERR  P2 APRI  P2 BIN  P2 RETRY  P2 KEY  P2 TICK
boot-now-0923            yes      yes     no      no       no       no      no        no      no
p2-4.20                  yes      yes    yes     yes      yes      yes     yes        no      no
p2-variants              yes      yes    yes     yes      yes      yes     yes       yes     yes
```

**`boot-now-0923` prints `P2 SEQ` and nothing else in that block.** No `WHY` beside
it, no `ERR` below it. That is because `P2WhyLetter`/`P2MarkSeq` and the grouped
`P2 ERR %r x%d` were added together, in a later build than the payload named
`boot-now-0923`. Reading the literal set straight off the three images:

| literal | boot-now-0923 | p2-4.20 | p2-variants |
| --- | --- | --- | --- |
| `P2 SEQ [%a]` | present | present | present |
| `P2 WHY [%a]` | **absent** | present | present |
| `P2 ERR %r x%d` / `P2 ERR none` | **absent** | present | present |
| `P2 APRI bytes=%d entries=%d sum=%x` | absent | present | present |
| `KEY 0/%d err=none free=%d miss=%d` | absent | absent | present |
| `K %d %c%c %d/%d free=%d %g` | absent | absent | present |

**And `P2 SEQ` has been read off this panel three times while `P2 WHY` has been read
zero times.** The source comment at the `P2 ERR` block already records that
asymmetry — it says, in the line written when that block was added, "SEQ has been
read off this panel three times and WHY has never been read once". What is new here
is that the asymmetry has a *build* explanation and not only a reader explanation,
and the two are distinguishable from one photograph:

> **A panel showing `P2 SEQ` with no `P2 ERR` anywhere on it is running
> `boot-now-0923`, and nothing on that screen can name a status.** `SEQ` is a string
> of `s` and `L`; `L` means the load failed and says nothing about why. The
> readable spelling of the same datum is `P2 ERR`, which exists only in the two
> later builds.

That turns a guess into a gate. `tools/probe-fingerprint.py --expect P2ErrRow IMG`
exits 1 on `boot-now-0923` and 0 on `p2-variants`, and it is the precondition for
the next flash: a payload whose screen cannot print the reason is not worth
flashing, however new it is.

### `P2 ERR` is the row to photograph, and it is short

The block was written for exactly this. For each distinct failing status it prints
one line **once, at its first occurrence**, with a count taken over the whole
batch (`Dispatcher.c`, the `Prior`/`Same` pair inside `P2Digest`):

```
P2 ERR %r x%d
```

So the answer to "did the batch fail for one reason or twenty-seven" is one line or
a few. `P2 ERR Out of Resources x27` means one global cause; three `P2 ERR` lines
mean three, and the split between them is the finding, because a status that is
`OUT_OF_RESOURCES` at one index and `NOT_FOUND` at another has no single cause. It
is the same reading as `P2 WHY`, in words, grouped, and it does not require the
reader to keep 46 unbroken characters straight. `P2 DIAG %c %g %r` below it names
all 27 individually — driver, phase, status — for the question `P2 ERR` deliberately
collapses.

### Nothing about the *room* changed: the bins are a preference, and the numbers say so

Step 4.37 concluded the 27 are not a large-request problem. Two more candidates on
the allocator side were open and both are now closed, one of them by arithmetic
over the same volume.

**The memory-type bins have PCD-limited sizes and the loads walk past them.** The
platform builds the HOB and sets them
(`Silicon/Silicium/SiliciumPkg/SiliciumPkg.dsc.inc:44-53`, with
`PcdPrePiProduceMemoryTypeInformationHob|TRUE` at `:121`):

| bin | pages | the run's cumulative demand, in promotion order |
| --- | --- | --- |
| `EfiBootServicesCode` | 1000 | peaks at **689** pages, at the last entry |
| `EfiRuntimeServicesCode` | 150 | crosses 150 **between positions 2 and 3**, peaks at 873 |
| `EfiRuntimeServicesData` | 300 | 160 pages of runtime data pools |
| `EfiLoaderCode` / `EfiLoaderData` | 10 / 0 | unused: no promoted image is subsystem 10 |

`EfiRuntimeServicesCode` is over its bin from position 3 onward — the cumulative
runtime-code demand passes 150 pages there and reaches 873 by the end, and
**sixteen further loads succeed after that point**, the last of them at position
21, every one of them with the preferred bin already full. So a bin being exceeded
does not stop a load, which is what
`FindFreePages` says it should be: the preferred bin is tried
(`Mem/Page.c:1073`), then the default bin (`:1090`), then anywhere inside
`MaxAddress` (`:1119`), and only then `PromoteMemoryResource` and a retry — and
`AllocateAnyPages` passes `MAX_ALLOC_ADDRESS`, so the third attempt sees the whole
map. A bin is a *preference and a promotion source*, never a limit. The one thing
this leaves open is that `PromoteMemoryResource` is unreachable on this board, so
the third attempt is the last one; but the third attempt is not type-limited, and
`P2LargestAlloc` measures it.

**And the heap is barely touched when the failures begin.** Cumulative demand over
the entries that actually loaded — positions 0 through 17, then 21, since
positions 18 to 20 are `L` and consumed nothing — is **575 pages**. The DXE heap is
9056 pages declared and about 7261 after PrePi takes the FVMAIN off the top. So the
run has consumed **8% of the heap** when a 9-page allocation stops working. That is
not a fragmentation story either — there is nothing to fragment.

### And every byte-level guard the loader has is clean, over all 46

`PeCoffLoaderGetImageInfo` and `PeCoffLoaderRelocateImage` have guards that are
properties of the image's bytes rather than of memory, so they are checkable on the
host, and they were checked: for each of the 46 promoted entries, is any section's
`VirtualAddress` or `VirtualAddress + VirtualSize - 1` at or beyond `SizeOfImage`; is
any section's raw range past the end of the FFS file's PE32 section; is the
relocation directory itself inside `SizeOfImage`; is any relocation *target* at or
beyond `SizeOfImage`; is `AddressOfEntryPoint` at or beyond it.

**All clean, on all 46, on both sides of the `s`/`L` split.** The walk checks the
target of every non-`ABSOLUTE` relocation entry, which is the one that
`PeCoffLoaderImageAddress` would return NULL for and turn into
`IMAGE_ERROR_FAILED_RELOCATION` / `RETURN_LOAD_ERROR` — letter `E`. It fires for
none of them.

So the failure surface of `CoreLoadImage` is now enumerated and each entry is
either measured away or measured to be reachable-but-unproven: `EFI_INVALID_PARAMETER`
(a NULL or too-short file path), `EFI_UNSUPPORTED` (machine type, subsystem — both
accepted; an unhandled relocation — none in the volume), `EFI_NOT_FOUND`
(`GetFileBufferByFilePath`), the security protocols (not installed: `SecurityStubDxe`
is one of the 27), `EFI_OUT_OF_RESOURCES` (three allocation sites, all of them
pool-or-page), and `RETURN_LOAD_ERROR` from the loader's own guards (all clean).
`EFI_DEVICE_ERROR` is produced nowhere in the path. **The live candidates are
`EFI_OUT_OF_RESOURCES` and `EFI_NOT_FOUND`, and `P2 ERR` distinguishes them in one
line.**

## Step 4.40 — The reading stops being a transcription, and the decoder is honest about what it cannot do

Every `P2 …` reading this phase has produced came from a person looking at the panel
and typing back a row of characters. Steps 4.29, 4.31, 4.33 and 4.39 each cost a
session that way, and 4.39 found why it kept costing them: the row a person *can*
transcribe and the row that *answers the question* are different rows, and the build
on the phone printed only the transcriber's row. A reader who mis-transcribes one `L`
in a run of 46 `s` and `L` has no way to know, and neither does anyone reading the
transcription afterwards.

`tools/panel-text.py` removes the transcription. Its input is a photograph of the
panel and its output is the text, and **everything it needs comes out of the sources
rather than out of anyone's memory**: the 96 glyphs are parsed from `Font.h`, the
cell size is computed from the same `PcdFrameBuffer*` and `FONT_WIDTH`/`FONT_HEIGHT`
the firmware computes it from, and the drawing rules are transcribed from
`FrameBufferSerialPortLib.c`. A font change or a panel change moves the numbers
instead of silently invalidating them.

### The font, decoded, because none of it is visible in the header

`GlyphFont[]` is 96 `UINT64` constants and nothing in `Font.h` says how they are
laid out. From `DrawGlyph` and `DrawRow`:

  * `DrawGlyph` splits each constant into `TopGlyph = (UINT32)(Glyph >> 32)` and
    `BottomGlyph = (UINT32)Glyph`. Rows **0–5** are `TopGlyph`, rows **6–11** are
    `BottomGlyph`, five bits per row, `RowData >>= 5` between rows.
  * `DrawRow` takes the **low** bit first and advances the cursor right, so within
    each 5-bit group **bit 0 is the leftmost pixel**. Checked against the glyphs that
    are not mirror-symmetric — `1`, `[`, `]`, `/`, `B`, `b` — because `0`, `A` and
    `X` read the same either way and cannot settle it.
  * The top half is 6 rows × 5 bits = 30 bits of a 32-bit dword, so **the top two
    bits of `TopGlyph` are unused.** Read that as a fact before reading it as a bug.

Geometry, computed rather than assumed: panel 1080×2400 → `FontScale` 2 → **90
columns × 100 rows**, **12×24 px per cell**, of which the left **10 px** carry ink
(the 12th column of each cell is the inter-character gap, and `DrawGlyph`'s stride
arithmetic is what puts it there). Three console rules decide how a reading is taken,
and all three are read off `WriteFrameBuffer` and `AdvanceNewLine`:

  * **It wraps at 90 columns.** A line longer than the panel is not truncated and not
    lost — it costs a second row. The `P2 RETRY` line is the only digest line that can
    exceed 90, which is why `tools/console-budget.py` says to read the row below it.
  * **It wipes rather than scrolls.** `AdvanceNewLine` past the last row calls
    `ZeroMem` on the whole framebuffer, so the panel holds the *last* 100 rows of
    output and never the first.
  * **A leading space is skipped.** `if (CurrentPosition->XPos == 0 && Character ==
    ' ') return;` — so a line that begins with a space is drawn one cell to the left of
    where its source string puts it. This is why the decode is compared against
    `--render` output and not against the format string.

### Five wrong readings, each of which looked like a right one

`--selftest` renders text through the console model, places it in a larger black
scene the way a phone appears in a photograph, applies the degradations a hand-held
photograph has, and requires the text back exactly — each degradation alone and then
all of them together. It went **0 of 11 → 11 of 11** over the two sessions it took to
build, and the distance is the useful part, because every failure in it was a
plausible wrong answer rather than a crash:

| symptom | what was actually wrong | measurement |
| --- | --- | --- |
| `P` and `2` decoded as `\|--l … }\|` while the report said `dx=0` | `score_rows` returned the scores of the *last* `dx` tried, not the winner's — it sampled `x0+3` and reported `dx=0`, and a 3-px shift lands inside the next cell | `dx` searched `x0±3`; glyph boundaries are 12 px apart |
| alignment impossible to move, answer frozen at the estimate | `align` initialised `best` to a score measured at magnification **1.0** labelled as `mag0`; when the estimate was wrong the answer was compared against a number it could never beat | returned `mag0` four decimals deep on a case whose truth was 1.00 |
| `P2 SEQ` decoded as `P7 ##W [####…]` | `normalise` used the *mean* of the above-threshold pixels as white; on a blurred picture most of those are half-lit edges | white read 0.29 against a true 1.0, saturating the strokes **and** the gaps |
| sampling preferred the space beside the text | the objective `sum_k [ t·c + (1-t)·(1-c) ]` has a constant 60 per cell and the term that varies is 4% of the total; it rewards sampling *more* ink, so row 1 of a tilted screen chose an empty band while rows 2–8 decoded exactly | fixed by expanding the squared distance: per-cell value `max_g(2·sum_ink c − popcount_g) − sum(c²)` |
| an empty panel row read as populated; the crop became the whole scene | `ink > 0.5` fires on sensor noise and on the faint edge of a stroke; on the combined scene the crop read **2679×1277** — the entire photograph — and every alignment score fell under 0.07 | replaced by a 3×3 box mean (`lit_mask`) |

Two more came out of the same bench and are worth separating from the decoder,
because neither was a decoder bug:

  * **The last character of a line was being dropped.** The columns to decode were
    `np.arange(90)` — the panel's width — anchored at the grid's phase. The crop puts
    one cell of margin around the text and the warp then re-centres whatever it was
    given, so the text starts at an arbitrary cell: measured, a 60-character `KEY` /
    `K 19 Ss` row landed in cells 30–89 on a clean screen and in cells **31–90** once
    the alignment came out one pixel over, and cell 90 is not in `arange(90)`. The
    row came back 59 characters long. **The columns now come from where the ink is**,
    and the last character of a `KEY` line is the most important one on the panel.
  * **The scaffold was testing a resolution no photograph of this phone will have.**
    It rendered the panel at one photograph pixel per panel pixel, where a glyph
    stroke is two pixels wide and a blur of radius 2 deletes it before the decoder
    runs — which is why `lens blur r=2` decoded to `''` with the alignment *correct*
    to 0.04° and 1.000×. A photograph has the panel at two to three pixels per panel
    pixel; `make_photo(scale=2)` is that, and it turned three failures into passes
    without the decoder changing at all.

**11 of 11, exactly, in 2m06 on this host.** The last degradation to fall was
`all of it` — blur, a 5% brightness ramp, sensor noise at σ=0.2, a 1.5° tilt and an
offset together — and it fell to the white-level rule, not to the alignment: with
σ=0.2 the above-threshold class is *mostly noise*, Otsu split the noise at 0.131, the
90th percentile of that class read 0.472 against a true white of 1.0, dividing by it
inflated the noise 2.1×, and the background then read as 0.7 of white everywhere. The
white level is now the **mean of the brightest 1% of the above-threshold pixels** —
the robust maximum of that class — which measures 1.000 on the clean scene, 0.813 at
blur radius 2 and 0.842 on the combined scene, and decodes all three. The top 0.1% is
too high (0.866 at blur 2, which then loses the first row), so it is deliberately the
top percent and not the top of the distribution.

### What the tool says when it is not sure, which is the point of it

On a deliberately hard synthetic photograph — blur 1.6, a 15% ramp, noise, a 1.5°
tilt, an offset — the CLI returns all eight rows correctly **and** flags 57 of their
411 characters as below `--min-margin`, with the position of each within the line:

```
    7 |KEY 27/46 err=Out of Resources at=18 free=1024 miss=18|   <- 9 weak: chars [9, 19, 22, 25, 35, 36, 43, 53, 54]
    8 |K 19 Ss 19/46 free=1024 6D6F6475-6C65-0000-0000-000000000000|   <- 8 weak: chars [3, 9, 13, 20, 25, 27, 34, 36]
```

and exits 1. The margin is the gap between the best glyph and the runner-up, so a
flagged position is one where two glyphs explain the cell almost equally well; the
tool's own instruction is to read those positions against `--render` rather than off
the decode. **A tool that returned the text without saying this would be worse than
no tool**, because the failure it is there to prevent is exactly a confident wrong
reading.

The one thing it does *not* claim: the alignment's own estimate of the cell phase can
be one pixel off on a blurred picture (measured, the blur-2 case reads phase 0 where
the clean case reads 11), and the decode is insensitive to that because the ink
plateau is 10 of the 12 pixels of a cell. `--grid-check` is what to look at when a
decode is wrong in a way this does not explain.

### The device-side reading is unchanged, and it is still the bottom of the panel

Nothing here changes what the phone is doing, and the two requests are the same two:

```
tools/probe-fingerprint.py --read        # in TWRP: which payload is actually on `boot`
tools/panel-text.py --decode PHOTO.jpg   # boot it, photograph the bottom of the panel
```

  * **`P2 SEQ` present with no `P2 ERR` anywhere on the panel** ⇒ the payload is
    `boot-now-0923`, and no reading of that screen can produce a status.
  * **`P2 RETRY` as the last populated row**, or any `Loading driver at …` row ⇒ the
    `p2-4.20`-class build, and the newest flash did not take.
  * **`P2 ERR <name> x<count>`** is the row that decides one global cause against
    twenty-seven, and `KEY <errors>/<promoted> err=<name> at=<i> free=<pages>
    miss=<i>` is the bottom row on `p2-variants` in every state.
  * **Read the panel before flashing anything else.** The ring holds one boot.

## Step 4.41 — Rung three is the whole map, so the probe goes inside `FindFreePages`

Step 4.18 concluded that "the next step is then a probe inside `FindFreePages` itself,
not another census" (line 3437). Five sessions went by without one, because each of
them measured the failure from outside the allocator: the digest after the run,
`P2 RETRY` re-asking the same requests from the assert, the bins, the HOB, the Apriori
census. Every one of those describes the heap the run **left behind**. The question —
why a request byte for byte identical to one that succeeded a few milliseconds later
was refused — is about the heap **at the failure**, and there is exactly one line of
code where that state exists. This step reads the code that decides failure, and the
reading changes what the probe has to be.

### The chain, each link read rather than recalled

  * `CoreLoadPeImage` makes exactly one allocation whose status reaches the caller:
    `CoreAllocatePages (AllocateAnyPages, ImageCodeMemoryType, NumberOfPages,
    &ImageAddress)` at `Image.c:700-741`. `PcdLoadModuleAtFixAddressEnable` is 0 and
    `RelocationsStripped` is clear on all 46, so neither the fixed-address branch nor
    the relocation fallback runs, and the other `EFI_OUT_OF_RESOURCES` in
    `CoreLoadImageCommon` is an `AllocateZeroPool` of about 200 bytes.
  * `CoreInternalAllocatePages` sets `MaxAddress = MAX_ALLOC_ADDRESS` for
    `AllocateAnyPages` (`Page.c:1305`) and rounds `NumberOfPages` up to the type's
    granularity — 16 pages for the four runtime types, one page for everything else.
  * `FindFreePages` has four rungs, and **the third is `CoreFindFreePagesI
    (MaxAddress, 0, …)`: every descriptor in `gMemoryMap`, no floor, no window.** Rungs
    one and two come first and are *preferences* — rung 1 searches the type's own bin
    (`mMemoryTypeStatistics[NewType].MaximumAddress` down to its `BaseAddress`), rung 2
    the default bin — and both are skipped entirely when `MaxAddress` is below their
    ceilings.
  * `CoreFindFreePagesI` returns 0 exactly when no `EfiConventionalMemory` descriptor
    at or below `MaxAddress` is `NumberOfPages` long.

**So a terminal failure is not a statement about a bin window.** The bins decide *where*
an allocation lands; whether it succeeds is rung 3's business, and rung 3 is the whole
map. Step 4.24's crux — "a bin boundary … test 1 of the ladder only fires while
`MaxAddress >= MaximumAddress`" — is a correct statement about test 1 and cannot be the
cause of the 27. That does not make the `P2 BIN` lines worthless; it makes them a
reading about placement, which is what they always were.

Two details of that function are worth writing down, because both were misread here on
the way to this step:

  * **The `Target & EFI_PAGE_MASK` test at `:1033` is not a second filter.** `Target` is
    `DescEnd - (NumberOfBytes - 1)`, and `DescEnd + 1` is a multiple of the alignment
    and therefore of `EFI_PAGE_SIZE`, so for a 4-KiB or a 64-KiB alignment `Target` is
    always a multiple of 4096 and the test can only fire in the case where no
    descriptor matched at all — where `Target` wraps to `-NumberOfBytes + 1`. It is the
    "found nothing" test, and "found nothing" is what reaches `EFI_OUT_OF_RESOURCES`
    at `:1313`.
  * **`NeedGuard` is FALSE for every runtime request**, because
    `if (Alignment != EFI_PAGE_SIZE) { NeedGuard = FALSE; }` (`Page.c:1210`). The guard
    arithmetic inside the loop is dead on this platform, and so is the alignment
    mismatch it exists to prevent.

### The probe records at the one place failure becomes terminal

`P2FreeWhy` is called from `FindFreePages` at `if (!PromoteMemoryResource ())` — not at
the two earlier `return 0`s, and not on the recursive retry, because on the retry path
promotion succeeded and the enlarged map was searched and was still too small. That is
the same statement with the promoted regions already counted in, and it is the only
state the retry's own rung 3 saw.

The census walks `gMemoryMap` and mirrors `CoreFindFreePagesI` exactly — the same
`EfiConventionalMemory` filter, the same `EFI_MEMORY_SP` skip, the same `MaxAddress`
canonicalisation, the same alignment clip — and keeps four numbers:

| field | what it is | what it decides |
| --- | --- | --- |
| `big` | the largest run the same search would have accepted, in pages | `big < np` is a statement about the map; `big >= np` is a statement about this census |
| `raw` | the largest conventional descriptor before the clip | `raw > big` says the alignment clip was the cost, and by how much |
| `free` | every conventional page in the map | `free` large beside a small `big` is fragmentation; `free` near zero is a full heap |
| `c` | the descriptor count | makes "fragmented" a measurement rather than an adjective |

Because rung 3 is the whole map, **`big < np` is a prediction the probe is guaranteed
to confirm, and `big >= np` would falsify the census rather than the allocator.** That
is deliberate: an instrument whose interesting outcome is a contradiction is worth more
than one whose answer is already known, and the two possible worlds behind `big < np`
have completely different fixes. `free` in the thousands beside `big` in the tens is
fragmentation, which no promotion fixes and which no bin window causes — the answer
would then be about the heap's size or the number of modules, not about the allocator's
rules.

The other line is the cheap falsification. `P2 FWTY bc= bd= rt= oth= n=` counts the
terminal failures by memory type, so the belief that all 27 are
`EfiBootServicesCode` — the type comes from the PE subsystem, not from the relocation
fallback (`Image.c:629-646`), and only 10 of the 46 promoted images are subsystem-12 —
prints as a count instead of resting on a reading of the source. `n` carries the rest
without costing a row: **`n=0` is no terminal `FindFreePages` failure in the whole run**,
which puts the fault in `CoreLoadPeImage`'s own `AllocateRuntimePool` and outside this
file entirely; `n` above zero with no second line is terminal failures that were all
smaller than four pages; and a 4-page floor is what keeps the first record from being
some pool chunk rather than an image.

### The row budget decided the shape of the lines, twice

`tools/console-budget.py` is the arbiter, and it was extended this step to read
`P2FreeWhyReport` out of `Mem/Page.c` alongside the four Dispatcher functions that emit
the digest. The first draft was four lines — `none`, `FWTY`, `FWHY`, `FWMAP` — and took
a copy of the digest from **47 rows to 51**, which against a 99-row panel is the
difference between *two* copies fitting and *one*. The second draft merged the request
and the census onto one line and folded "no failure at all" into `n=0`, which is **49
rows** and two copies again. The final shape is two lines, 52 and 71 columns, both well
inside the panel's 90:

```
P2 FWTY bc=%d bd=%d rt=%d oth=%d n=%d
P2 FWHY t=%d np=%ld a=%d big=%ld raw=%ld free=%ld c=%d
```

The second is omitted rather than spelled when there is nothing to describe. A third
line would have been affordable only at the cost of the second copy of the digest on
the panel, which is the redundancy the wipe needs.

### The new payload differs from the one on the phone in one file, by 282 bytes

`work/out/p2-freewhy/Mu-gauguin-silicon-gzip.img`, sha256 `eb1601ab98fe97d2…`. Three
independent checks, run because a firmware that is one file different from the control
is the only kind of difference this phase can afford:

  * **`tools/pe-facts.py` over both payloads differs in exactly one line**: the FFS
    file `9E21FD93-9C72-4C15-8C4B-E77F1DB2D792` — DxeCore — grows from `0xf8a4b` to
    `0xf8b65`. **282 bytes.** Every other GUID, offset and size in the volume is
    identical.
  * **The printable strings of the two decompressed FVMAINs differ by exactly the two
    new format literals** (`P2 FWTY …` and `P2 FWHY …`), plus two 6-character artifacts
    of the compression stream's tail. The decompressed sizes are equal to the byte:
    7,352,320.
  * **`tools/probe-fingerprint.py --expect P2FreeWhy` passes**, and the image carries
    **10 of 10** instruments — the full ladder, with the tenth rung read out of
    `Mem/Page.c` rather than out of the Dispatcher.

The volume is very nearly full, and this is the first time that has been measured:
GenFv's own map says `EFI_FV_TOTAL_SIZE = 0x703000` against `EFI_FV_TAKEN_SIZE =
0x702d08`, so **760 bytes of free space** — 1,042 before this probe. That is the probe
budget for the rest of P2, and it is why the plan to delete the whole `P2BRINGUP` block
before BDS work is now also a space requirement and not only a tidiness one.

The payload is the **fourth** built for this phase at exactly 1,142,784 bytes, which is
by now the expected result and the reason `probe-fingerprint.py` exists at all.

`tools/build-p2-payloads.sh` grew a `P2DIR` override for this. The rule it implements is
the one `tools/restore-stock-boot.sh` pins a sha256 for and `identify-boot.py`
reconciles archived readbacks against: **the payload on the phone is the control, and a
build that overwrites it destroys the comparison it exists for.** `work/out/p2-variants`
is byte for byte where it was — sha256 `7c8fdb5a1a272ab6…` — and the new probe sits
beside it.

### Nothing is flashed, and the reading is still owed

The ordering rule from step 4.36 is unchanged and it is the reason this step stops here:
**先读屏，再刷下一次**, because the print ring holds one boot's worth and the payload now
on `boot` is the one whose screen is the only evidence of what its own run did. So:

```
tools/probe-fingerprint.py --read        # in TWRP: which payload is on `boot`
tools/panel-text.py --decode PHOTO.jpg   # boot it, photograph the bottom of the panel
python3 tools/probe-fingerprint.py --expect P2FreeWhy \
    work/out/p2-freewhy/Mu-gauguin-silicon-gzip.img    # before that payload is flashed
```

and the new probe is worth a flash only after `p2-variants` has been read, because if
the reading says `bs16=Success` the answer is already the per-request state this probe
describes, and if it says `Out of Resources` the census is what turns that into either
a full heap or a fragmented one. Either way the next measurement is the same one, which
is why it is built and staged rather than built on demand.

## Step 4.42 — The ladder was counting itself, and the volume is where the fix costs nothing

Step 4.41 ended owing one thing it could not finish: the host-side cross-check of the
recorded 46-character `P2 SEQ` against `tools/pe-facts.py`'s per-entry PE facts. Doing
it found the falsified premise it was for, and then something the write-up had not been
about at all — the probe staged in that step was going to count the instrument as part
of the run, and could have described a ladder rung in place of the run's first failure.
This step is that reading, the source corrections it forced, and the payload rebuilt with
them. Nothing is flashed; the device-side reading is still the one that is owed.

### The volume is memory-mapped, so every file it reads is a second allocation

`FVMAIN.Fv`'s own header, read out of the built volume rather than recalled:

```
FvLength 0x703000   Attributes 0x0003feff   HeaderLength 72   Revision 2
  bit  9  EFI_FVB2_MEMORY_MAPPED   1
  bit 10  ERASE_POLARITY           1
  bits 16..20  ALIGNMENT           3   -> EFI_FVB2_ALIGNMENT_8 (PiFirmwareVolume.h:54)
```

Bit 9 is the one that matters. `FwVol.c:346-347` sets `FvDevice->IsMemoryMapped = TRUE`
from it, and that turns on the per-file copy in `FvReadFile`:

```c
if (FvDevice->IsMemoryMapped) {
  if (!FvDevice->LastKey->FileCached) {
    WholeFileSize = IS_FFS_FILE2 (FfsHeader) ? FFS_FILE2_SIZE (FfsHeader) : FFS_FILE_SIZE (FfsHeader);
    FfsHeader     = AllocateCopyPool (WholeFileSize, FfsHeader);
    if (FfsHeader == NULL) {
      return EFI_OUT_OF_RESOURCES;
    }
    FvDevice->LastKey->FfsHeader  = FfsHeader;
    FvDevice->LastKey->FileCached = TRUE;
  }
}
```

`FileCached` (`FwVolRead.c:322-332`) is a latch on the file's key and nothing on the
load path resets it. The only `CoreFreePool` calls near it — `FwVol.c:276` and `:279` —
are in `DestroyFvDevice`-class teardown. So **every promoted module's whole FFS file is
copied into the pool once and stays there for the entire load window**, beside its own
image. That is a second heap consumer in the same window as the image allocations, and
it had never been measured. `tools/pe-facts.py` now prints it, because it is a property
of the volume the tool was already walking:

| over the 46 promoted entries | bytes | pages |
| --- | --- | --- |
| Σ whole-file size — what `AllocateCopyPool` is asked for | 2,947,366 | **759** |
| Σ image request — `SizeOfImage + (SectionAlignment if > 0x1000)` | 6,397,952 | **1,562** |
| together | | **2,321 = 9.07 MiB** |

759 is not 2,947,366 rounded up once (719.6, which is the 720 the first pass at this
arithmetic produced before the per-file rounding was separated out): each file is copied
by its own `AllocateCopyPool`, so each one rounds up on its own. The largest single cache
entry is `BdsDxe` at 385,166 B = **95 pages**, and `DALSys` is next at 76.

This sharpens the 4.18 crux without changing it. The run does not ask the heap for 1,562
pages and then fail on the 1,563rd; by slot 46 it asks for 1,562 image pages **and** 759
cache pages, against the ~7,261 pages `pe-facts.py` derives for `DxeCore`'s conventional
region. The margin is 3.1x rather than the 4.6x the same tool printed for images alone —
still far too wide to be a full-heap story, which is exactly why the question stays where
4.18 left it: state at the instant, not room in total.

### The staging measurement, which is the strongest form of the crux so far

`pe-facts.py`'s cumulative column, joining the recorded `SEQ` to each entry's PE header:

| slot | entry | request | cum demand | result |
| --- | --- | --- | --- | --- |
| 18 | `NpaDxe` | 20 | 566 | `s` — the last success before the run |
| 19 | `RpmhDxe` | 16 | 582 | `L` — the first failure |
| 20 | `PdcDxe` | 9 | 591 | `L` |
| 21 | `ClockDxe` | 47 | 638 | `L` |
| 22 | `ShmBridgeDxe` | 9 | 647 | `s` — the run's last success |

**Of the 81 pages of demand between the last clean success and the run's last success, 72
were refused** — 89%. And `ClockDxe`'s 47 pages, the largest of the three, is not a
request anyone would call large: it is a 192,562-byte file whose `SizeOfImage` is
192,512, so its request is the image rounded up and nothing else — its
`SectionAlignment` is 0x1000, which is why the runtime link line's 64 KiB does not apply
to it — and the requests that succeed on either side of it are 20 pages and 9. A window
in which 20 succeeds, 16 fails, 9 fails, 47 fails and 9 succeeds is not a window running
out of a room-shaped resource, and then every request for the next 24 slots fails.

### The premise in `P2Retry`'s comment was false, and the correction had a second falsification in it

The comment beside `P2Retry` asserted that every one of the 27 failures asks for
`EfiBootServicesCode`, on the grounds that the memory type comes from the PE subsystem
(`Image.c:631-641`) and only 10 of the 46 promoted images are subsystem-12. The join says
otherwise, and it says it without the phone: **21 subsystem-11 against 6 subsystem-12** —
`SdccDxe`, `VariableRuntimeDxe`, `ResetSystemRuntimeDxe`, `EmbeddedMonotonicCounter`,
`RealTimeClock`, `CapsuleRuntimeDxe`. The four subsystem-12 images that succeed are
`EnvDxe`, `ReportStatusCodeRouterRuntimeDxe`, `StatusCodeHandlerRuntimeDxe` and
`RuntimeDxe`, and they ask for 15, 112, 96 and 112 pages — **335 pages against the
150-page `PcdMemoryTypeEfiRuntimeServicesCode` bin** (`SiliciumPkg.dsc.inc:49`), so that
bin is empty by slot 5, fourteen slots before the first failure. After that the runtime
requests fall to the same default window the boot-service ones use, because boot services
have no bin at all: `PrePiHobLib/Hob.c:887-905` builds the type-information HOB from five
entries and none of them is a boot-service type, even though
`PcdMemoryTypeEfiBootServicesCode` (1000) and `PcdMemoryTypeEfiBootServicesData` (800)
exist at `SiliciumPkg.dsc.inc:50-51` and are never read.
`AllocateMemoryTypeInformationBins` (`MemoryBin.c:447`) carves one contiguous block off
the top of the heap and lowers the default window's ceiling to just below it (`:515`), so
the type is a real difference that does not survive to the failure.

The correction I wrote into the comment then contained a claim of its own that is false,
and the same join falsifies it: *"they ask for 96 to 112 pages against the 9 to 17 the
other 21 ask for."* The 21 subsystem-11 failures ask for **9 to 97** pages — `BdsDxe` is
97, `PmicDxe` 34, `UFSDxe` 28, `GpiDxe` 22 — and the six subsystem-12 ones for **26 to
112** (`SdccDxe` is 26: it is subsystem-12 with `SectionAlignment` 0x1000, so it carries
no 64-KiB penalty at all). The ranges overlap at both ends, and `bs9`/`bs16`-versus-
`ShmBridgeDxe` already made size a non-explanation: **size is not what decides these
either.** What `SectionAlignment` 0x10000 does explain is only why five of the six ask
for a 64-KiB-rounded request: `EDKII.DXE_RUNTIME_DRIVER` is linked with `/ALIGN:0x10000`
(`SiliciumPkg.dsc.inc:22-23`) where every other module type gets `/ALIGN:0x1000`
(`:19-20`), and `Image.c:682-688` turns that into `SizeOfImage + SectionAlignment`. That
is a property of the DSC, not of the allocator.

Both of those sentences are corrected in the sources this step, and so is `Page.c`'s
census comment for the same reason: it claimed the runtime types are clipped by 16 pages
where `EfiBootServicesCode` is clipped by one, which is the `#else` arm of
`ProcessorBind.h:163-170` that `SiliciumPkg.dsc.inc:14` removes from the build.
`RUNTIME_PAGE_ALLOCATION_GRANULARITY` is 0x1000, so every one of the 46 is clipped by the
same single page and `raw == big` on every line the device will print. The field stays —
it is what would make `raw > big` readable the day the granularity changes — but the
sentence that said otherwise is gone.

### The probe defect: `P2LargestAlloc` runs inside the dispatch loop, and its rungs are terminal failures

`P2LargestAlloc` (`Dispatcher.c:195-219`) walks a ladder — `{4096, 1024, 256, 64, 16, 4, 1}`
pages — of `CoreAllocatePages (AllocateAnyPages, EfiBootServicesData, N, …)` and returns
the first rung that succeeds. It has three call sites: `P2Key`'s two `free=` readings
(`:280`, `:291`), and `P2Tick` (`:683`).

`P2Tick` is called after **every attempted dispatch** — the load-failure path
(`:1122`) and the start path (`:1167`) — so the ladder runs once per tick from the first
tick onward, and the first tick is `PcdDxe` starting at slot 1, eighteen slots before the
first load failure at slot 19. Every rung that fails is a terminal page failure at
`FindFreePages`'s last rung, which is precisely the condition `P2FreeWhy` exists to
record. And `P2FreeWhy` counts before it judges (`Page.c:1182-1187`): the tally takes
every arrival, and the *description* is taken by the first arrival of four pages or more.

Three consequences, in ascending order of how much they mattered:

  * `P2 FWTY`'s counts included the instrument. Every rung is `EfiBootServicesData`, and
    no promoted image asks for `EfiBootServicesData` at all — the images are
    `EfiBootServicesCode` (21 failures) or `EfiRuntimeServicesCode` (6) — so the `bd`
    field in the pre-guard build was going to be **a count of failed ladder rungs and
    nothing else**. A field built to measure a category of failure would have measured
    the instrument instead. The `n` total was inflated with it; `bc` and `rt` were not,
    because no rung ever asks for either of those types.
  * **The described record could have been a rung.** A rung of 16, 64, 256, 1024 or 4096
    pages is describe-eligible, and the first one to fail before slot 19 would have set
    `mP2FwSeen` — after which `P2 FWHY` describes *that* instead of the run's first
    image-sized refusal, with `a=1` and `np=` one of those powers of four. The 4096-page
    rung is 16 MiB against a 28.4 MiB heap, which makes it the rung most likely to be
    the first to fail; whether the description was actually lost depends on which rung
    first found nothing, which is the point — an instrument whose output depends on an
    unmeasured coincidence cannot be read.
  * The 4.39 argument about how many probes run inside dispatch was counting the
    instrument as one of them.

The fix is a flag set and cleared **inside `P2LargestAlloc`** and not at its call sites,
because there are four of them and because the ladder's own report walks the same ladder
before the digest is printed. `mP2FwProbe` (`Dispatcher.c:191`, set at `:204`, cleared at
`:211` and `:216`) is declared in `DxeMain.h` beside `P2FreeWhyReport` and read at the
top of `P2FreeWhy` (`Page.c:1167-1170`), which returns before the tally. The count is not
thrown away: `mP2FwSkip` (`Page.c:1132`) is printed as a new **`g=%d`** field, and it is
the only field on either line that measures the instrument — one increment per rung that
found nothing, so `free=1024` beside `g=1` on a tick is the 4096-page rung failing and
the 1024-page one succeeding. It is also what distinguishes this build's `P2 FWHY` row
from the previous build's, which was the same row without the field.

Nothing on the load path got more expensive: the guard is inside a function that is only
entered on a terminal failure, and `P2LargestAlloc` gained two stores.

### The rebuilt payload differs from the control by one literal, and costs no volume

`work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img`, sha256
`cbe5a13114fc4a0465677e480a29a76fa2836cf2ae00fb9e9838c490e0102132`, still 1,142,784
bytes. (An earlier rebuild of this same step — the `g=` guard alone, before the two
`Mem/Page.c` self-references in the line-number section below were corrected — hashed
`4c7456d01d9c626f…`. The comment edit is a source edit, so it made a new payload; the hash
in this document is the one on disk.) The same three checks 4.41 ran, run again against
`p2-freewhy` — the payload built before the guard, sha256 `eb1601ab98fe97d2…` and
unchanged, so the control is intact:

  * **All 123 FVFF entries are identical** in GUID, type, size, state and offset. Not
    "one file grew": nothing moved, and the walk agrees with GenFv's own map of the volume
    — 123 offsets and GUIDs, zero mismatches, for all three payloads. The only size that
    changed anywhere in the payload is the LZMA section in `FVMAIN_COMPACT`
    (`9E21FD93-…`), 0xf8b65 → **0xf8b78**, nineteen bytes *larger* — and that section is
    the compressed container, not a file in the volume. (4.41's own write-up called that
    GUID "the FFS file DxeCore"; it is the section, and the 282-byte growth it recorded is
    that section's. The number stands, the name was off by one level of nesting.)
  * **The decompressed inner FVs are 7,352,320 bytes each, and every byte outside
    `DxeCore`'s FFS file is identical.** `DxeCore`'s header is at the same offset and the
    file is the same size — `0x4f8`, type `0x05`, `0x2a230` — and all **99,277** differing
    bytes lie inside it, from `0x6c4` to `0x2a240` against the file's `0x4f8`…`0x2a728`.
    A comment-only source edit moving 99 KB of a DEBUG PE is the line-number table, which
    is the largest thing in a DEBUG image that tracks a comment. The volume's free space is
    **760 bytes** (`0x703000` − `0x702d08`), unchanged.
  * **The strings of the two differ by exactly one literal**: `P2 FWHY … c=%d` is gone
    and `P2 FWHY … c=%d g=%d` is in its place. One caveat, because it is the kind of thing
    that gets written up as a clean result and is not: a 6-byte-printable extractor also
    reports `R*eK9+` present in the old image and absent in the new. It is four AArch64
    instructions that happen to be printable (`2a 65 4b 39` is an `ldrb`) sitting where the
    line tables shifted by a byte or two — code misread as text, not a literal. The
    literals differ by one.

`tools/probe-fingerprint.py --expect P2FreeWhy` passes on the new payload — exit code 0,
measured without a pipe this time, because an earlier run of the same check read `rc` after
`tail` and so measured `tail` — and
the image carries 10 of 10 instruments. The marker caveat is worth writing down, because
it is the one thing that check cannot do: `P2FreeWhy` is a *function name* that the tool
resolves out of `Mem/Page.c`, so it is present in the previous build too and is no longer
a discriminator between them. The discriminator is `g=`, and the way to check it is the
strings diff above. `tools/console-budget.py` re-run says the two lines are 52 and 79
columns and a digest copy is **49 rows** against a 99-row panel, so two copies still fit:
`g=%d` cost nothing on the panel either.

### The tool could not see the previous build, which is the build the sentence above is about

That caveat was checked rather than asserted, and the check failed. `probe-fingerprint.py`
resolves each instrument's marker out of the *current* sources and then searches the image
for that literal — so the moment `P2 FWHY … c=%d` became `… c=%d g=%d`, the tool stopped
recognising the build that carries the older spelling:

```
work/out/p2-freewhy/Mu-gauguin-silicon-gzip.img       P2FreeWhy  ABSENT   -> 9/10
work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img     P2FreeWhy  present  -> 10/10
```

The first line is false. `p2-freewhy` is where `P2 FWTY`/`P2 FWHY` were introduced, and it
is the build whose screen the whole `g=` argument is about. A tool that says its tenth
instrument is missing from it would send the next session looking for a payload that is
already on disk — and the module's own docstring names this failure mode, one direction
over: *"a stale marker reports an instrument absent that is present."* It arrived through
the marker rather than through the search, because every edit to a format string orphans
every image built before it.

The fix is a second, weaker marker: the **head** of each literal, everything before its
first `%`, tried only when the whole literal is not in the image, and reported as such —

```
work/out/p2-freewhy/Mu-gauguin-silicon-gzip.img    P2FreeWhy  present  in DxeCore (2 lines, head-matched)
```

— because "this image has the instrument in an older spelling" is a different statement
from "present", and `g=` is what separates them.

**The first version of that fix invented two instruments, and the mistake is worth the
lines.** `P2Tick`'s literal is `K %d %c%c %d/%d free=%d %g`, so its head is the two bytes
`K `, which appear in some body of almost every image. With `heads` unguarded, `p2-4.20`
and its readback went from 7 of 10 to 8 of 10 — reporting `P2Tick` present in a build that
has no `P2Tick` at all, and silently contradicting the tool's own ladder table two
hundred lines above. The guard is that a head is kept only when it is **strictly longer
than the token** it was resolved from, so a head match always states more than a token
match would; where the guard rejects it, the literal stands in and that instrument stays
exact-only. Re-measured over the five payloads of record and the archived readback, the
ladder reads:

| image | before this fix | after | what it is |
| --- | --- | --- | --- |
| `work/out/boot-now-0923.img` | 2/10 | 2/10 | `P2 FREE` + `P2 SEQ`, nothing else |
| `work/out/p2-4.20/…` | 7/10 | 7/10 | step 4.25's build, on the phone until 15:04 |
| `work/out/p2-variants/…` | 9/10 | 9/10 | **on the phone now** — flashed 15:04, read back identical |
| `work/out/p2-freewhy/…` | **9/10** | **10/10** | 4.41's build; the ` ABSENT` was the bug |
| `work/out/p2-freewhy-g/…` | 10/10 | 10/10 | the payload this step stages |
| `work/out/boot-readback-0924.bin` | 7/10 | 7/10 | the 15:02 control read, identical to `p2-4.20` |

Three gates, run to make sure the weakening did not go the other way: `--expect P2FreeWhy`
passes on `p2-freewhy-g` at exit 0 and **fails** on `p2-variants` at exit 1, and
`--expect P2Tick` fails on `p2-4.20` at exit 1.

Which also settles what is on the phone, from the archive and without it: step 4.33's
commit records the readback at 15:02 as `p2-4.20` over its whole length and then `p2-variants`
flashed to `sde55` at 15:04, read back identical. Every readback taken since — the empty
`work/out/boot-readback.bin` at 21:43 is the only one — came back at zero bytes. So the
payload whose screen is owed is `p2-variants`, and `--read` in TWRP should say so; if it
says `p2-4.20`, the 15:04 flash did not take, and that is itself the finding.

### The line numbers this step's edits moved

The doc's citations were stale by the probes' own growth, and this step's edits moved them
again. Re-measured, so the next step can cite these instead:

| file | symbol | now | before this step |
| --- | --- | --- | --- |
| `Mem/Page.c` | `CoreFindFreePagesI` | 903 | 903 |
| `Mem/Page.c` | `P2FreeWhy` | 1136 | 1129 |
| `Mem/Page.c` | `P2FreeWhyReport` | 1266 | 1259 |
| `Mem/Page.c` | `FindFreePages` | 1313 | 1306 |
| `Mem/Page.c` | `CoreInternalAllocatePages` | 1421 | 1414 |
| `Mem/Page.c` | `CoreAllocatePages` | 1637 | 1630 |
| `Mem/Page.c` | `CoreFreePages` | 1782 | 1775 |
| `Dispatcher/Dispatcher.c` | `P2Tick` | 659 | 651 |
| `Dispatcher/Dispatcher.c` | `P2Tick ('L')` / `('S')` | 1122 / 1167 | 1114 / 1159 |
| `Dispatcher/Dispatcher.c` | `P2Digest` | 2221 | 2213 |
| `Dispatcher/Dispatcher.c` | `P2LargestAlloc` | 195-219 | — |

Inside `CoreInternalAllocatePages`, which is where the next reading will be spent:

| statement | now | 4.41 cited |
| --- | --- | --- |
| `Alignment = DEFAULT_PAGE_ALLOCATION_GRANULARITY` | 1451 | — |
| the `RUNTIME_PAGE_ALLOCATION_GRANULARITY` override | 1456-1458 | — |
| `if (Alignment != EFI_PAGE_SIZE) { NeedGuard = FALSE; }` | 1468-1470 | `:1210` — 258 lines short |
| `NumberOfPages += EFI_SIZE_TO_PAGES (Alignment) - 1` | 1478 | — |
| `MaxAddress = MAX_ALLOC_ADDRESS` | 1489 | `:1305` — 184 lines short |
| `Status = EFI_OUT_OF_RESOURCES` | 1575 | `:1313` — 262 lines short |
| `if (Start == 0)` — its only predecessor | 1574 | — |

The three `4.41 cited` figures are the ones that step wrote for statements inside this one
function, and all three were short — by 184, 258 and 262 lines, which is roughly the length
of the probe block that sits above them. The likely mechanism is that 4.41 numbered the
file as it was before the probes were added and did not re-measure after; the effect is
that a reader following `:1305` lands in `PromoteMemoryResource`, not in the allocator. The
`Target & EFI_PAGE_MASK` test is the fourth: 4.41 and the `P2FreeWhy` header comment both
called it `:1033`, and it is **1038** now. Two of those four numbers were fixed in the
source this step — `Mem/Page.c`'s `:1305` and `:1033` self-references — which is a comment
edit and therefore another payload hash; the numbers in 4.41's prose are left standing,
because a step that rewrites an earlier step's measurements to agree with a later one
destroys the only record of what was measured when. This table is the correction.

The `PromoteMemoryResource` / `CoreAddMemoryDescriptor` / `CoreConvertPages` half of the
file, everything above the probes, is unmoved.

### Still nothing flashed, and the reading is still owed

The ordering rule is unchanged and it is still the reason this step stops here:
**先读屏，再刷下一次**, because the print ring holds one boot's worth and the payload now on
`boot` is the one whose screen is the only evidence of what its own run did.

```
tools/probe-fingerprint.py --read        # in TWRP: which payload is on `boot`
tools/panel-text.py --decode PHOTO.jpg   # boot it, photograph the bottom of the panel
python3 tools/probe-fingerprint.py --expect P2FreeWhy \
    work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img   # passes; do this before flashing
```

The new probe is worth a flash only after `p2-variants` has been read, for the reason
4.41 gave: if the reading says the retried request succeeds at the assert, the answer is
per-request state inside `FindFreePages` and the `P2 FWHY` census is what turns it into
either a full heap or a fragmented one. This step does not change that order. It changes
what the census means when it arrives: it is now guaranteed to describe a real request,
and the `g=` beside it says how much of the run's own instrument it took to know.


## Step 4.43 — the census has a predicted number in every field, and `t=` plus `np` name the allocation

Host side only. Nothing was flashed and the device was not attached.

4.42 made the census describe a real request instead of the instrument. This step reads the load
path that request comes from, and ends with two results that change what the next reading is for.
Every field of both `P2 FWTY` and `P2 FWHY` now has a predicted value, and `t=` beside `np=`
identifies which allocation failed, because this build has exactly four memory types on that path
and one of them can only come from one call site. The second result is that the census decides a
premise the phase has been standing on since 4.19 — that the 27 failures are 27
`EFI_OUT_OF_RESOURCES` — and the two answers are mutually exclusive.

### `tools/promote-check.py`, run for the first time since 4.20 wrote it

74 descriptors, 72 with a resource HOB, 4 landing in the GCD map as `Reserved`, and each fails a
different clause of the gate at `Mem/Page.c:402-405`:

| region | base | why the gate rejects it |
| --- | --- | --- |
| `AOP CMD DB` | `0x0000020000` | no `EFI_MEMORY_PRESENT`, no `INITIALIZED` |
| `SMEM` | `0x0000200000` | no `EFI_MEMORY_PRESENT`, no `INITIALIZED` |
| `PIL Reserved` | `0x0015800000` | no `EFI_MEMORY_PRESENT`, no `INITIALIZED` |
| `Display Reserved` | `0x0002400000` | `EFI_MEMORY_TESTED` set — the one bit that must be clear |

`PromoteMemoryResource` therefore returns FALSE from every call site on this board, and the control
flow at the call site on the load path is why the census is readable:

```
if (!PromoteMemoryResource ()) {          //  Page.c:1382
  P2FreeWhy (MaxAddress, NoPages, NewType, Alignment);   //  :1390
  return 0;                               //  :1391
}
return FindFreePages (MaxAddress, NoPages, NewType, Alignment, NeedGuard);  // :1397
```

* **The recursion at `:1397` is unreachable here**, so `P2FreeWhy` is called **exactly once per
  failed request** and not twice on a promotion path. There is no factor of two in the census.
* `g` is therefore the exact number of ladder rungs that found no run of their size: 47 walks of at
  most 7 rungs, `0 <= g <= 329`, and **`g = 0` is a real answer** — it says a 4096-page allocation
  succeeded at every instant the ladder was walked, including at the end of the run.
* The heap cannot grow mid-run, so `free=` and `big=` at the first refusal describe the same map for
  every request after it. The snapshot is not a state that has passed; it is the ceiling the rest of
  the run fails under.

### The ladder runs 47 times, and both comments about it say three

`P2LargestAlloc` has **four** call *sites*, not three — `Dispatcher.c:280`, `:291`, `:683`,
`:2462` — and `Page.c:1159`'s "there are three of them" is wrong the same way the
`Dispatcher.c:453-455` comment is; both are recorded in the table below. The two in
`P2Key` are the arms of an `if`/`else`, so only one of them is ever evaluated; the four textual
sites are three live paths, and those three paths do not *run* three times. `P2Tick` is called once
per attempted driver, at `:1122` for each load that failed and `:1167` for each that started, which
this run's 46-character SEQ fixes at 27 + 19 = 46 walks; `P2 FREE largest=` (`:2462`) walks the
ladder once more before `P2Bins` (`:2464`) prints the row. **47 walks behind the `g=` this row
prints, and every rung all of them failed is in `g`.** `P2Key`'s own walk is the 48th, reached
from the `P2Key ()` call at `:2470` after that row is printed, so it is in no field on the panel.

| where | says | should say |
| --- | --- | --- |
| `Dispatcher.c:453-455` | "P2LargestAlloc runs three times before this - once per P2Tick inside the dispatch loop, and once for `P2 FREE largest=` above" (two items, total three) | 46 walks inside the dispatch loop plus one for `P2 FREE largest=`, so 47 walks behind the `g=` this row prints; four call sites, two of them arms of the same `if`/`else` |
| `Mem/Page.c:1159` | "there are three of them" (call sites of `P2LargestAlloc`) | four: `Dispatcher.c:280`, `:291`, `:683`, `:2462` — three live paths, since `P2Key`'s two are mutually exclusive |
| `Mem/Page.c:1250-1254` | "CoreLoadPeImage's other EFI_OUT_OF_RESOURCES is a 200-byte AllocateRuntimePool … so `n=0` beside 27 recorded `L`s names the pool" | the pool it means is 48 bytes and its failure path returns `EFI_SUCCESS`; the only `EFI_OUT_OF_RESOURCES` that function can return is multi-page — see the next section but one |
| `Mem/Page.c:1258` | "the second line … is now guaranteed to be a real request" | true, and weaker than "an image request": nothing filters on type at `:1185`; the type is read at `:1172-1180` only to choose a tally slot, and the ladder is excluded by the flag at `:1167-1170`, not by its arguments |

### The per-tick `free=` is not a reading, and cannot be

Every `K` line carries a `free=`, so the row set looks like a 46-sample fragmentation trace of the
whole run. It is unreadable, and not because of the photography: `P2Digest` prints once and then 40
more times (`Dispatcher.c:2552-2557`), a copy costs about 49 rows against a 99-row panel
(`tools/console-budget.py`), and `AdvanceNewLine` wipes rather than scrolls — so after the first wipe
the panel holds nothing but copies of the digest, and every `K` line is printed inside the dispatch
loop, before the first copy. `:2507-2515`'s conclusion is right and stronger than it is written
there: the repetition does not merely let the digest win the race, it erases the per-tick rows from
every reachable state.

The same measurement survives in three places that *are* on the panel — `P2 FREE largest=` after the
run, and `free=`/`big=` on the census — and those are the reading.

### What the sources say the two lines will read

Both lines print pages, not bytes: `free` is already pages (`Page.c:1218`), and `raw`/`big` are
shifted down by `EFI_PAGE_SHIFT` at `:1290-1291` before printing. So the quantities are comparable
and the relations are `big <= raw <= free`.

* **The heap.** The PHIT row is `DXE Heap` `0x9B800000 + 0x02360000` = 9056 pages = 35.4 MiB, the
  only `Conv` row in the device's own memory map (`tools/heap-compare.py`); every sibling platform at
  that base declares 15360. `Sec.c:64-65` looks the row up by name to size the PHIT, and 4.27
  established that `DxeCore`'s own conventional region is the 9056 minus the 1795 pages PrePi takes
  off the top for the decompressed FVMAIN (`Gcd.c:2393-2394`) = **7261 pages**, not 9056.
* **The bins cost nothing of it.** 4.26's reading stands: `AllocateMemoryTypeInformationBins`
  allocates the 450-page `RequiredSize` and frees it, so the bins leave address windows and consume
  no page.
* **The demand.** `tools/pe-facts.py` prints two consumers per slot, and both are live for the whole
  window: the image request (`SizeOfImage + SectionAlignment` when that is 0x10000) and the FV
  driver's `AllocateCopyPool` of the entire FFS file, which `FileCached` (`FwVolRead.c:322-332`)
  latches and nothing on the load path releases. Cumulative to the last success before the run, slot
  18: 566 image pages + 252 cache pages = 818.

| instant | cumulative demand | `free=` expected |
| --- | --- | --- |
| slot 19 refuses, `RpmhDxe` | 818 (nothing taken by the failed request) | **~6,443** |
| end of the run, all 46 attempted | 1,562 image + 759 cache = 2,321 | **~4,940** |

So `free=` at the first refusal should read in the **thousands** — near 6,400, less whatever else
the started drivers hold in pools — and `big=` should be within a few hundred of it, because
everything the run allocates is taken off one end of the region and the free area is what is left
behind it. `big == free` exactly is not expected; `big` a small fraction of `free` is the shredded
case. `c >= 1` whenever `free > 0`, since `c` counts the `EfiConventionalMemory` descriptors that
survived the `EFI_MEMORY_SP` test (`Page.c:1207-1218`). And at the end of the run `P2 FREE
largest=` should read **4096**: the predicted surviving run is about 4,940 pages, so the ladder's
first rung succeeds. `1024` in that field is one grade down and still says room was never the
problem; `16` or below would say the whole region is in pieces of 64 KiB or less.

`a=1` is not a prediction, it is a consequence. `CoreInternalAllocatePages` takes `Alignment` from
the memory type (`Page.c:1451`, `:1458`), and on this build `RUNTIME_PAGE_ALLOCATION_GRANULARITY` is
0x1000 like the default, because `SiliciumPkg.dsc.inc:14` compiles with
`__DEPRECATED_AARCH64_4K_RUNTIME_GRANULARITY` and `ProcessorBind.h:164-170` makes the runtime
granularity 0x10000 only in the `#else` arm. So every request on the load path has a one-page
alignment, and the rounding at `:1478-1479` is a no-op — which is also why `np=` is exactly the
caller's page count.

### `t=` is the field that names the allocation, because there are only four types on this path

| `t` | type | what asks for it on this path |
| --- | --- | --- |
| 3 | `EfiBootServicesCode` | a boot-service driver's own image pages — `Image.c:731` passes `ImageCodeMemoryType`, and `:636-638` sets that from the PE subsystem |
| 4 | `EfiBootServicesData` | `AllocatePool` — the FV file cache, and DxeCore's own bookkeeping |
| 5 | `EfiRuntimeServicesCode` | a runtime driver's own image pages (`:640-642`) |
| 6 | `EfiRuntimeServicesData` | `AllocateRuntimePool` — of which `CoreLoadPeImage` has exactly two, and neither can be a tens-of-pages request |

That last row is the new one, and it is arithmetic rather than argument.
`Image->ImageContext.FixupDataSize` is `DirectoryEntry->Size / sizeof (UINT16) * sizeof (UINT64)`
(`BasePeCoff.c:1515`) — four bytes per two bytes of base relocation — and `AllocateRuntimePool`
rounds through `Size = ALIGN_VARIABLE (Size) + POOL_OVERHEAD` to `EFI_SIZE_TO_PAGES`
(`Pool.c:409-431`). Measured over the 46 promoted images: the largest `FixupData` is 16,384 bytes
for a 4,096-byte relocation directory, which is **5 pages**; everything else is 1 page or is served
from an existing pool page and never reaches the page allocator at all. Of the whole promoted set
only two images have a 4,096-byte directory — `EnvDxe`, which starts, and `SdccDxe`, which does not.
The other `AllocateRuntimePool` in `CoreLoadPeImage` is `sizeof (EFI_RUNTIME_IMAGE_ENTRY)` = 48
bytes (`Image.c:837`, `Protocol/Runtime.h:39-61`: four 8-byte fields and a 16-byte `LIST_ENTRY`),
which is filtered out of the census by the `NumberOfPages < 4` test at all times.

So the census is decidable from the two fields together. If the failure is an image's own pages, `np`
must be one of these, and `t` says which family:

| slot | entry | `np` | `t` | | slot | entry | `np` | `t` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 19 | `RpmhDxe` | 16 | 3 | | 33 | `SPMI` | 11 | 3 |
| 20 | `PdcDxe` | 9 | 3 | | 34 | `ResetSystemRuntimeDxe` | **112** | 5 |
| 21 | `ClockDxe` | 47 | 3 | | 35 | `PmicDxe` | 34 | 3 |
| 23 | `ScmDxe` | 12 | 3 | | 36 | `WatchdogTimer` | 9 | 3 |
| 24 | `DiskIoDxe` | 12 | 3 | | 37 | `SecurityStubDxe` | 14 | 3 |
| 25 | `PartitionDxe` | 13 | 3 | | 38 | `EmbeddedMonotonicCounter` | 96 | 5 |
| 26 | `EnglishDxe` | 9 | 3 | | 39 | `RealTimeClock` | 96 | 5 |
| 27 | `SdccDxe` | 26 | 5 | | 40 | `PrintDxe` | 10 | 3 |
| 28 | `UFSDxe` | 28 | 3 | | 41 | `DevicePathDxe` | 19 | 3 |
| 29 | `Fat` | 18 | 3 | | 42 | `CapsuleRuntimeDxe` | 96 | 5 |
| 30 | `TzDxe` | 15 | 3 | | 43 | `HiiDatabase` | 35 | 3 |
| 31 | `VariableRuntimeDxe` | 112 | 5 | | 44 | `BdsDxe` | 97 | 3 |
| 32 | `DALTLMM` | 9 | 3 | | 45 | `GpiDxe` | 22 | 3 |
| | | | | | 46 | `I2C` | 10 | 3 |

21 of the 27 are subsystem 11, so `t=3`; 6 are subsystem 12, so `t=5`. A `t=6` row means the
`FixupData` pool, and it can then only be `np=5` — `SdccDxe`'s load, at slot 27, whose image pages
were allocated and then freed at `Done:` (`Image.c:946-950`) on the way out. A `t=4` row means the
failure was a pool chunk and not an image load at all.

> **Corrected in step 4.46.** Slot 34's `np` read 96 here and is 112. `Image.c:682-688` makes
> `SizeOfImage` the whole story only when `SectionAlignment` is `0x1000`; above that the request is
> `EFI_SIZE_TO_PAGES (SizeOfImage + SectionAlignment)`, and `ResetSystemRuntimeDxe` is a
> `0x10000`-alignment module with `SizeOfImage` 393216, so `(393216 + 65536) / 4096 = 112`. The row
> directly above it, slot 31 `VariableRuntimeDxe`, has the identical pair of values and was already
> 112, which is what makes this a transcription slip rather than a formula error in the table. It
> costs a distinction the table exists to draw: `t=5` at 96 names one of `EmbeddedMonotonicCounter`,
> `RealTimeClock` or `CapsuleRuntimeDxe`, `t=5` at 112 names `VariableRuntimeDxe` or
> `ResetSystemRuntimeDxe`, and 96 for slot 34 collapsed that pair into the wrong group. `tools/pe-facts.py`
> prints the two inputs and this cell is the only one of the 27 whose arithmetic disagrees with them.

`n` counts every failed page search of any size, because the tally at `Page.c:1182-1183` runs before
the size filter at `:1185`; the ladder's probes are excluded from it by the guard at `:1167-1170`,
which returns before the tally. `bc`/`bd`/`rt`/`oth` are the same count split by type — and `rt`
merges the two runtime types, so the tally alone cannot separate a runtime image's pages from a
`FixupData` pool. That is what `t=` is for.

### The load path, resolved to one live call

`CoreLoadPeImage`'s allocation block (`Image.c:676-742`) is three `CoreAllocatePages` calls of which
two are dead on this platform, and it is worth writing down which, because the first is the one that
could have made every load a fixed-address request and therefore invisible to the whole census:

* `:702` `if (PcdGet64 (PcdLoadModuleAtFixAddressEnable) != 0)` — 0. `MdeModulePkg.dec:1154` sets the
  default and no platform DSC, INC or INF overrides it, which is also why `CoreLoadingFixedAddressHook`
  (`Page.c:579-581`) never runs. **Dead.**
* `:719-720` `if ((PcdGetBool (PcdImageLargeAddressLoad) && (Image->ImageContext.ImageAddress >=
  0x100000)) || Image->ImageContext.RelocationsStripped)` — `PcdImageLargeAddressLoad` is TRUE
  (`MdeModulePkg.dec:1379`, consumed at `DxeMain.inf:206`, never overridden) but the address is 0:
  `ImageContext.ImageAddress` is `OptionalHeader.ImageBase` and nothing else (`BasePeCoff.c:624`,
  `:629`), and `tools/pe-facts.py` measures `ImageBase 0x0` on all 80 `DRIVER` files with
  `IMAGE_FILE_RELOCS_STRIPPED` clear on all of them. Both arms FALSE, so the `AllocateAddress` call
  at `:722-727` is **skipped**.
* `:730-735` `if (EFI_ERROR (Status) && !RelocationsStripped)` — `Status` is the
  `EFI_OUT_OF_RESOURCES` preset at `:697`, so this is TRUE by construction and its `AllocateAnyPages`
  at `:731` is **the one live allocation** for all 46 promoted loads, with
  `ImageCodeMemoryType` as the type.

This is also the only place on the load path that can return `EFI_OUT_OF_RESOURCES` while the page
allocator never ran, and it cannot: `:740-742` returns the status of the allocation, and the preset
at `:697` is always overwritten before that: `IMAGE_FILE_RELOCS_STRIPPED` is clear on all 80
promoted images, so the second arm of `:730`'s test is always TRUE and the call at `:731` always
runs. The preset is unreachable, and the live call's status is what the dispatcher sees.

### The contradiction the census resolves

`CoreLoadPeImage` has exactly two `EFI_OUT_OF_RESOURCES` (`Image.c:697`, `:795`) and one more in the
runtime pool beside them:

* `:697` — the preset above. Unreachable.
* `:795` — `Image->ImageContext.FixupData = AllocateRuntimePool (...)`, reached only when the
  attribute requests runtime registration *and* `ImageType == EFI_IMAGE_SUBSYSTEM_EFI_RUNTIME_DRIVER`
  (`:791-793`). Real, multi-page, and **runtime-driver-only — so it can account for at most the 6
  subsystem-12 failures and never the other 21.**
* `:837-840` — the 48-byte `EFI_RUNTIME_IMAGE_ENTRY` pool, whose failure path is `if
  (Image->RuntimeData == NULL) { goto Done; }` with `Status` left at the `EFI_SUCCESS`
  `PeCoffLoaderRelocateImage` set at `:804` and checked at `:805`. **It returns `EFI_SUCCESS` from
  `CoreLoadPeImage` with `RuntimeData` NULL.** So it cannot be the source of an `EFI_OUT_OF_RESOURCES`
  from anywhere, and it is not a tens-of-pages request in any case: the largest `FixupData` in the
  promoted set is 5 pages and this one is 48 bytes. The note at `Page.c:1250-1254` names "a 200-byte
  AllocateRuntimePool" for exactly this role, and the only allocation that can be is this one — the
  other candidate, the preset at `:697`, is not an allocation at all.

And the pool path is closed too: every reachable `EFI_OUT_OF_RESOURCES` out of `CoreAllocatePool`
comes from `CoreAllocatePoolPages` failing (`Pool.c:242`, `:247`), except the `Size > MAX_POOL_SIZE`
gate at `Pool.c:231`, whose threshold is `MAX_ADDRESS - POOL_OVERHEAD`. So on this platform:

**`EFI_OUT_OF_RESOURCES` out of an image load requires `FindFreePages` to have returned 0, which
requires `P2FreeWhy` to have been called, which means `n >= 1`.**

The other possible source, a `CoreConvertPages` failure after a successful search, has no way to
fire either. `CoreConvertPagesEx`'s three failure returns are all `EFI_NOT_FOUND` (`Page.c:664`,
`:686`, and the single-entry rule at `:672-676`), and the single-entry rule is the one that could
have been live: it fires only when the range being converted is not covered by one memory-map
descriptor, and the range the search returns is inside one by construction — `CoreFindFreePagesI`
clips `DescEnd` to `Entry->End` and then takes `Target = DescEnd - (NumberOfBytes - 1)`
(`Page.c:984-1040`), so `Start + bytes - 1 <= Entry->End` always. The memory lock is held across both
(`:1561`), so the map cannot change in between.

Put together: **`n = 0` and "27 × `EFI_OUT_OF_RESOURCES`" cannot both be true.** One of the two
premises is wrong, and the premise that is weaker than it looks is the second one. "The 27 are all
`EFI_OUT_OF_RESOURCES`" rests on a single status name read off a screen in an earlier session — the
answer to "屏幕上 Out of source 指的是哪个?" was "是状态名 Out of Resources" — and on no census of the
other 26. The alphabet at `Dispatcher.c:166-170` exists precisely because
`EFI_OUT_OF_RESOURCES`, `EFI_NOT_FOUND` and `EFI_SECURITY_VIOLATION` have no mechanism in common.

The good news is that the same panel decides it, on two rows that are already there. `P2 ERR %r x%d`
(`:2375`) prints one line per *distinct* status with how many failures share it, so
`Out of Resources x27` and `Device Error x4` + `Not Found x23` are different readings of one line.
And `P2 DIAG %c %g %r` (`:2391-2398`) prints one line per failure with the driver's GUID beside the
status in words, up to 64 records (`:78`, `:86`) — which is why the per-failure list moved into the
repeated digest, and why "printed once, three of the twenty-seven were unreachable" (`:2496-2504`).

### The tree the next reading walks

Read `P2 ERR` first; it is one line and it settles the premise.

1. **`P2 ERR` shows more than one status** → the 27 were never one problem. The memory-type-bin
   change is aimed at the wrong thing, and `P2 DIAG`'s per-driver GOUs are the new work list.
2. **`P2 ERR` shows one status and it is not `Out of Resources`** → the same, and cheaper: the
   mechanism is named on the line.
3. **`P2 ERR` shows `Out of Resources x27` and there is no `P2 FWHY` line (`n = 0`)** → the reading
   above says this combination is impossible, so the fault is in the instrument: the census's tally
   is being reset, or the row is being printed before the failures it counts. `P2 FWTY` alone, with
   `n=0` and `bc/bd/rt/oth` all zero, is the evidence for that, and it is a host-side fault.
4. **`P2 FWHY` present, with `t=6 np=5`** → `SdccDxe`'s `FixupData` pool, i.e. the one runtime
   allocation on this path that cannot be served from the bin. Its image pages were allocated and
   freed, so the same heap was large enough 26 pages earlier and this is a `RuntimeServicesData`
   window question, not a size question.
5. **`P2 FWHY` present with `t=3` or `5` and `np` equal to one of the rows in the table above** →
   the failure is that image's own pages, and `big=` against `np=` decides whether the region was
   too small or the search was wrong: `big < np` with `free` in the thousands is shredding, `big >=
   np` is the map refusing a request it could satisfy.
6. **`P2 FWHY` present with `np` that matches no image** → the failure is a pool chunk (`t=4`), which
   means the heap was short for an allocation no driver's size explains.

That is a decision procedure rather than a set of hypotheses, which is what 4.42 was aiming at, and
it needs one photograph of the bottom of the panel.

### Two comment defects, recorded and left in place

The whole `P2BRINGUP` block is scheduled for deletion when DXE reaches BDS, which is the same step
that has to happen for the 760 bytes of volume it costs, and a comment edit here would move the hash
of the payload that identifies `p2-freewhy-g` — staged, gated and waiting to be flashed. So the two
defects are recorded in the table above and not fixed, under 4.42's rule: a source edit invalidates
the hash of an artifact that was measured, and re-measuring is only worth what it costs when the
artifact is the one about to be flashed.

> **One of the two was fixed in step 4.46, and the rule was read rather than broken.** The rule's
> reason is the hash of the staged payload. A comment is not in the binary, so a comment-only edit
> that does not rebuild leaves that hash alone, and step 4.46 checked both ends rather than assuming
> it: `sha256` on `work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img` is still
> `cbe5a131…e0102132`, and `tools/probe-fingerprint.py --expect P2FreeWhy` on it still exits 0 with
> all ten instruments present. No build was run and nothing was re-staged. What 4.43's sentence
> conflates is *editing* with *rebuilding* — a rebuild moves the FV header's timestamp whatever the
> edit was, so the rule is about rebuilds and this was not one. Which of the two defects this is, and
> why 4.43's own diagnosis of it was wrong, is in step 4.46.

### The bin question, now bounded

While reading `t=6` back to its source, one thing about the pending memory-type-bin change became
checkable, and it is worth writing down before the change is designed rather than after.
`BuildMemoryTypeInformationHob` — the one this platform links, `EmbeddedPkg`'s, at
`PrePiHobLib/Hob.c:887-908`, because `SiliciumPkg.dsc.inc:345` binds `HobLib` to
`PrePiHobLib.inf` for `SEC` — builds a five-entry HOB and reads five PCDs: `ACPIReclaimMemory`,
`ACPIMemoryNVS`, `EfiReservedMemoryType`, `RuntimeServicesData`, `RuntimeServicesCode`. The DSC sets
nine of them (`SiliciumPkg.dsc.inc:45-53`), and the four it sets that nothing on this path reads are
`PcdMemoryTypeEfiBootServicesCode|1000` (`:50`), `PcdMemoryTypeEfiBootServicesData|800` (`:51`),
`PcdMemoryTypeEfiLoaderCode|10` (`:52`) and `PcdMemoryTypeEfiLoaderData|0` (`:53`).
That is the whole of why the bins are 450 pages and not 2,250, and it is a measurement now rather
than an inference: a grep over the tree finds those four PCDs in the DSC, in `EmbeddedPkg.dec`'s
declaration and in `EmbeddedPkg.dsc`, and in no `.c` file at all. The one PCD in the group that *is*
read elsewhere, `PcdMemoryTypeEfiACPIReclaimMemory`, is read by
`ArmPlatformPkg/MemoryInitPei/MemoryInitPeim.c:43` — the PEI path, which this platform does not
link for its DXE bins (it sets that PCD to 0, so the entry is a no-op either way).

What the change should be is not this step's to decide. It alters firmware behaviour on a run whose
screen has not been read, and the ordering rule is unchanged: **先读屏，再刷下一次**.

### The reading is still owed, and this step is what makes it decisive

```
tools/probe-fingerprint.py --read        # in TWRP: must report p2-variants
tools/panel-text.py --decode PHOTO.jpg   # boot it, photograph the bottom of the panel
python3 tools/probe-fingerprint.py --expect P2FreeWhy \
    work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img   # passes; do this before flashing
```

Nothing here was flashed and nothing in the firmware changed. What changed is that the census now
has a predicted number in every field, that `t=` beside `np=` names the allocation that failed
rather than merely describing a request, and that the row can no longer be read as "the page
allocator ran out" without also being read as "the 27 failures were not all the same status" — the
two readings cannot both be right, and one photograph of the bottom of the panel says which.

## Step 4.44 — The bin change cannot widen the search, because rung three does not consult the bins

Step 4.43 ended with the bin question bounded: the linked HOB builder reads five PCDs, and the four
memory-type PCDs `SiliciumPkg.dsc.inc:50-53` sets for boot services and the loader types are read by
no `.c` file in the tree. That made it worth asking the prior question, the one that decides whether
the pending edit belongs on the critical path at all: **if `EfiBootServicesCode` and
`EfiBootServicesData` had bins, could any of the 27 requests that fail today succeed?** The answer is
no, and the reason is a property of `FindFreePages` rather than of the DSC.

### `FindFreePages` has four rungs, and only two of them are bins

`FindFreePages` (`Page.c:1312-1398`) is the single funnel. Both callers that matter here go through
it, and neither passes a type-dependent bound:

| caller | where | `MaxAddress` |
| --- | --- | --- |
| `CoreLoadPeImage`'s live `AllocateAnyPages` | `Image.c:731` → `CoreAllocatePages` `Page.c:1566-1577` | `MAX_ALLOC_ADDRESS` (`:1489`, set for every type but `AllocateMaxAddress`, which image loads never use — `:1557-1558`) |
| `CoreAllocatePoolPages` | `Page.c:2511-2517` | `MAX_ALLOC_ADDRESS`, hard-coded |

The four rungs, in order, each returning only on success:

```
//  rung 1 — the preferred bin for NewType                    Page.c:1326-1338
if (((UINT32)NewType < EfiMaxMemoryType) && (MaxAddress >= mMemoryTypeStatistics[NewType].MaximumAddress)) {
  Start = CoreFindFreePagesI (mMemoryTypeStatistics[NewType].MaximumAddress,
                              mMemoryTypeStatistics[NewType].BaseAddress, NoPages, NewType, Alignment, NeedGuard);
  if (Start != 0) { return Start; }
}

//  rung 2 — the default allocation bin                        Page.c:1343-1359
if (MaxAddress >= mDefaultMaximumAddress) {
  Start = CoreFindFreePagesI (mDefaultMaximumAddress, 0, NoPages, NewType, Alignment, NeedGuard);
  if (Start != 0) { return Start; }
}

//  rung 3 — the whole map, unconditionally                    Page.c:1367-1377
Start = CoreFindFreePagesI (MaxAddress, 0, NoPages, NewType, Alignment, NeedGuard);
if (Start != 0) { return Start; }

//  rung 4 — promote, or give up                               Page.c:1382-1397
if (!PromoteMemoryResource ()) { P2FreeWhy (...); return 0; }
return FindFreePages (MaxAddress, NoPages, NewType, Alignment, NeedGuard);
```

**Rung 3 is `CoreFindFreePagesI (MaxAddress, 0, ...)` with `MaxAddress = MAX_ALLOC_ADDRESS` and
`BaseAddress = 0` — the entire memory map, with no reference to `mMemoryTypeStatistics` at all.**
Rungs 1 and 2 can only ever be *narrower* than rung 3, because they are the same call with a tighter
`MaxAddress` and a non-zero `BaseAddress`. A bin configuration is therefore a **restriction**
mechanism: it buys a preferred range, and it can never buy reach.

The consequence for the pending edit is immediate. `FindFreePages` returns 0 only if rung 3 failed,
and rung 3's inputs do not depend on any PCD, any `EFI_MEMORY_TYPE_INFORMATION` entry, or any bin
range. **So no edit to `SiliciumPkg.dsc.inc:45-53`, and no extension of
`PrePiHobLib/Hob.c:887-908`, can turn a request that fails at rung 3 into one that succeeds.**

### For the two types on this path, rungs 1 and 2 are the same range anyway

That argument would be enough on its own, but the state of the two boot-service types makes it
sharper, and it is worth writing down because it was previously only half-stated.

`mMemoryTypeStatistics` is initialised at `Page.c:36-53` in the field order `{ BaseAddress,
MaximumAddress, CurrentNumberOfPages, NumberOfPages, InformationIndex, Special, Runtime }`
(`MemoryBin.h:16-24`). Every entry starts as `{ 0, MAX_ALLOC_ADDRESS, 0, 0, EfiMaxMemoryType, … }` —
so `BaseAddress = 0` and `MaximumAddress = MAX_ALLOC_ADDRESS` for all sixteen types, including
`EfiBootServicesCode` (`:40`) and `EfiBootServicesData` (`:41`). Then
`InitializeBinStatisticsFromRange` (`MemoryBin.c:302-313`) walks *every* type, not only the ones the
HOB named, and clamps:

```c
    MemoryTypeStatistics[Type].CurrentNumberOfPages = 0;
    if (MemoryTypeStatistics[Type].MaximumAddress == MAX_ALLOC_ADDRESS) {
      MemoryTypeStatistics[Type].MaximumAddress = *DefaultMaximumAddress;   //  MemoryBin.c:310-311
    }
```

`*DefaultMaximumAddress` is `BaseAddress - 1` (`MemoryBin.c:515`), where `BaseAddress` is the start
of the one contiguous block `CoreAddMemoryDescriptor` reserved for all the bins
(`MemoryBin.c:492-497`, `RequiredSize` from `CalculateTotalMemoryBinSizeNeeded` at `:482`). So a type
with **no** HOB entry does not end up with a degenerate range that fails instantly — it ends up with
`[0, DefaultMaximumAddress]`, and rung 1 for `EfiBootServicesCode` issues
`CoreFindFreePagesI (DefaultMaximumAddress, 0, ...)`.

And that is byte-for-byte rung 2's call, because `mDefaultMaximumAddress` in `Page.c` is the same
`BaseAddress - 1`. **For the two boot-service types, rung 1 and rung 2 search the identical range, and
rung 3 searches a strict superset of it.** The "boot-services hole" the earlier steps argued about is
real but it is not a hole in *reachability*: boot-service allocations are unconstrained below the
reserved block, they are simply unpinned. What a bin would add is the pinning — a fixed block so
these pages stop being scattered through the map — and that is a fragmentation effect, never a
capacity one.

### What this does and does not settle

It settles the direction of the change. The bins cannot be the fix for the 27, and the reason is
structural rather than a matter of choosing the right numbers.

It does **not** prove the bin edit is worthless, and the difference matters, because there is a real
mechanism left open. Rung 3 needs *contiguous* pages. If boot-service allocations have been sprinkled
through the region and split it, a 4096-page run can fail to exist even with 6,000 pages free, and
pinning boot services into a bin is exactly the read-modify-write that would stop that. That
hypothesis is measurable and the census already measures it: **`free=` large with `largest=` small is
fragmentation; `free=` small with `largest=` small is capacity.** Step 4.43 predicts `free= ≈6,443`
and `largest= 4096`, which is neither — a 4096-page run is a healthy largest block, and if it is
really 4096 then no request on this path (`np <= 97` for an image, `np = 5` for the largest
`FixupData`) can fail at rung 3 for want of contiguity. So the two fields read together decide whether
the bin change is a real fix, a robustness measure, or neither — and that reading is still owed.

### The contradiction, restated with rung 3 in it

Step 4.43 showed that `EFI_OUT_OF_RESOURCES` out of an image load requires `n >= 1`, and that the
status on this path is set at exactly one place, `Page.c:1574-1577`:

```c
    Start = FindFreePages (MaxAddress, NumberOfPages, MemoryType, Alignment, NeedGuard);
    if (Start == 0) {
      Status = EFI_OUT_OF_RESOURCES;
      goto Done;
    }
```

Rung 3 makes that equivalence tight in both directions: `Start == 0` means the **whole map** could not
hold `NumberOfPages` contiguous pages at the forced alignment. So each of the 27, if it really is
`EFI_OUT_OF_RESOURCES`, is a statement that a request of at most 97 pages could not be placed anywhere
in the map. Twenty-seven of those, on a board with gigabytes free, in the same run the census prints a
`largest=` — that is the thing to look at, and it is why `P2 ERR` (one line, one status name and its
count) is the first thing to read rather than the census.

Nothing was flashed and nothing in the firmware changed. The change this step makes is to the *status*
of the pending bin edit: it moves off the critical path and becomes conditional on a measurement, and
the measurement is already in the payload that is waiting to be flashed.

## Step 4.45 — The ACPI tables are already in the payload, and the docs said they were missing

Host-side, and it started as a check on the flash budget rather than on ACPI. Step 4.44 ended by
noting that a source edit invalidates the hash of a staged artifact, and that the volume has 760 bytes
free — so the question "what exactly is in the payload that is waiting to be flashed" is worth being
able to answer from the artifact rather than from the build tree. Answering it turned up something the
documentation had wrong.

### What the payload is, end to end

The staged image is an Android boot image whose header is version 1 — so the kernel starts at
`page_size` = 2048, not 4096, and reading `raw[4096:]` sees `7cf18e4c…` and looks like a corrupt gzip.
The kernel is one gzip member; `gzip.decompress` rejects it because of the 87,594 bytes of padding
after it, and `zlib.decompressobj(16 + MAX_WBITS)` takes it:

```
work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img        1,142,784 bytes
  header        ANDROID! v1, page_size 2048, kernel_size 1,137,214, kernel_addr 0x10008000
  kernel        gzip -> 3,145,840 bytes   (= SILICIUM_UEFI.fd-bootshim, 3,145,840 on disk)
    FVMAIN_COMPACT   one FV header, its _FVH at 0x98
      GUID section  0x11088, size 0xf8b60, DataOffset 24, Attributes 0x1 (PROCESSING_REQUIRED)
        GUID        EE4E5898-3914-4259-9D6E-DC7BD79403CF   = LzmaCustomDecompress
        -> FVMAIN.Fv  7,352,320 bytes = 0x703000
```

`FVMAIN.Fv` is written to disk uncompressed beside the `.fd`, which is the shortcut that matters: the
reversing above was only needed to establish that the artifact and the build tree agree, and they do.
`Build/gauguinPkg/DEBUG_CLANGPDB/FV/FVMAIN_COMPACT.Fv`, `FVMAIN.Fv`, `SILICIUM_UEFI.fd` and
`SILICIUM_UEFI.fd-bootshim` are all timestamped **2026-09-24 23:30**, the same minute as the staged
`Mu-gauguin-silicon-gzip.img`. So reading the build tree is reading the payload.

**The volume figure in step 4.42 is confirmed exactly, from the artifact rather than from a
calculation.** `FVMAIN.Fv` is 7,352,320 bytes and its last non-`0xFF` byte is at `0x702d07`:
`EFI_FV_TAKEN_SIZE = 0x702d08`, **760 bytes free**. The `FVMAIN.Fv.txt` map's last line is
`0x006C5800`, 251,904 bytes short of the end — which is not free space, it is the last module, and
taking it for free space is the easy way to misread this file.

### The tables are there, and they were read back one by one

Searching `FVMAIN.Fv` for the six signatures and taking each hit whose length field is sane gives all
six between `0x54d484` and `0x54df8c`:

| table | offset | length | header |
|---|---|---|---|
| `SSDT` | `0x54d484` | 61 | `MSFT`, checksum valid |
| `DSDT` | `0x54d4c8` | 1,520 | `QCOMM `/`SM7225 `, OEM rev 3, creator `INTL`, **checksum valid** |
| `APIC` | `0x54dabc` | 724 | `QCOM`/`QCOMEDK2`, rev 5 |
| `FACP` | `0x54dd94` | 276 | `QCOM`/`QCOMEDK2`, rev 6 |
| `FACS` | `0x54deac` | 64 | OEM fields all zero, which is what `FACS` has |
| `GTDT` | `0x54def0` | 156 | `QCOM`/`QCOMEDK2`, rev 2 |

**Step 4.53 re-derived this table by walking the `AcpiTables` FFS file instead of by
scanning, and every offset above is exactly right** — the zeros this session got from
reading those offsets came from the `Build/` tree, not from the artifact, because that
tree had been rebuilt into the `xhci-host` volume by then. Two corrections to the
paragraph above, both about method and scope rather than about the numbers: the
sentence "taking each hit whose length field is sane" does not describe what selects
these six, because the raw scan returns six `DSDT` hits and five `FACS` hits, the extras
being debug strings inside `AcpiTableDxe.efi` — one of them carrying a length field of
`0x0000000A`, which passes any plausible sanity bound; and the volume this table
describes is the payload of record, while `Build/gauguinPkg/DEBUG_CLANGPDB/FV/FVMAIN.Fv`
is now 7,524,352 bytes (the payload plus exactly the three USB-host blobs' 172,032) and
126 files, built **2026-09-25 01:19**, with the same six tables at
`0x57764c`…`0x578158`. So "reading the build tree is reading the payload" above held on
2026-09-24 and stopped holding when the host stack was added.

The DSDT is gauguin's own, 1,520 bytes compiled by `iasl` from `tools/acpi/gauguin.asl` (21,615
bytes), and it contains `ACPI0007` eight times, `QCOM24A5` once, and `UFS0` and `URS0` device nodes,
with `_HID`, `_ADR`, `_CRS`, `_DSM`, `_STA` and `_UID` all present.

### The APIC, which is the one that has to be right

An ACPI mistake is not always a degraded boot. A wrong GICR base or a wrong PPI INTID is an interrupt
controller the OS cannot bring up, and it fails on hardware that is not this one — so this was worth
parsing rather than eyeballing. 44-byte MADT header, then eight `0x0B` subtables of `0x52` = 82 bytes,
then one `0x0C` (GICD) of 24: 44 + 8×82 + 24 = **724**, the whole table, with nothing left over. The
subtable length of 82 rather than the textbook 80 is the SPE overflow interrupt ACPI 6.4 appended at
offset 78, and getting that wrong shifts every field after 76 — which is the field this table exists
for.

| field | offset | GICC #1 | GICC #8 | gauguin's device tree |
|---|---|---|---|---|
| Performance Interrupt GSIV | 20 | `0x15` = **21** | `0x15` = **21** | `pmu { interrupts = <1 5 8> }` → PPI 5 → 21 |
| VGIC Maintenance Interrupt | 56 | `0x18` = **24** | `0x18` = **24** | GIC node `interrupts = <1 8 4>` → PPI 8 → 24 |
| GICR Base Address | 60 | **`0x17A60000`** | **`0x17B40000`** | `reg = <… 0x17a60000 0x100000>` |

Eight cores at stride `0x20000` run `0x17A60000` → `0x17B4FFFF`, inside the `0x100000` window. **All
three values `docs/07` chose Moorea on are in the built table and are the right ones.**

A false positive worth naming, because it cost a detour: `fv.find(b'DSDT')` returns an occurrence
inside a debug string — `"… not found"`, whose last four characters are `ound`, and the six bytes
before it are ` not f` — so the header read off the first hit is garbage (`len = 1650553888`, OEM ID
` not f`). The tables have to be found by a hit whose length field is consistent with the volume, not
by the first hit.

### What this changes

`docs/07`'s P3 groundwork section ended with a four-item "what is missing" list and the row
`next step | gauguin/AcpiTables.inf + DSDT, then 1-4 above`. **Items 1, 2 and 3 were done the same day
that list was written**, and the list has been misleading ever since: `AcpiTables.inf` exists at
`Silicium-ACPI/Platforms/Xiaomi/gauguin/`, `gauguin.fdf:73` is a live `INF RuleOverride = ACPITABLE`
line, and `gauguin.dsc:91` has a `[Components]` section whose only member is the table module. Item 4
(`AcpiTableUpdateLib`) is still the deliberate no-op and should stay one now that the tables check
out. All of that is annotated in place in `docs/07` rather than rewritten, and P3 in `docs/00-plan.md`
now carries a status line.

**So P3's ACPI half is done, and P3's driver half cannot start.** The payload has 760 bytes free and
the P2BRINGUP instrumentation is what occupies the rest; DisplayDxe, UsbBusDxe and ButtonsDxe do not
fit alongside it. Which puts the whole of P3 behind the same reading P2 has been behind since step
4.30 — the panel, one photograph, `P2 ERR` first.

> **Corrected by Step 4.57.** "P3's ACPI half is done" was true of the tables in the payload, and is
> not true of P3 item 1, which asks for I2C, SPI, GPIO, buttons and thermal as well. Those nodes
> cannot be written from the reference corpus — `_HID` is a declared string and no two SoCs declare
> the same one — so item 1's remainder is behind the Windows driver set, not behind the 760 bytes.
> The 760-byte figure itself is right: it is `0x703000` − `0x702d08`, the build's own
> `EFI_FV_TAKEN_SIZE`, and not the 1,265-byte sum of file headers and data that a naive walk of the
> volume gives.

Nothing was flashed and nothing in the firmware changed. This step read an artifact that was already
built and corrected two documents that described it wrongly.

## Step 4.46 — `has_reloc=False` is not `RelocationsStripped`, and one cell of the decision table is 16 pages light

### The arm that would have made the loads invisible, measured over the whole payload

The line worth doubting is `Image.c:719-720`:

```c
if ((PcdGetBool (PcdImageLargeAddressLoad) && (Image->ImageContext.ImageAddress) >= 0x100000)) ||
    Image->ImageContext.RelocationsStripped)
```

If either arm is true the load's page request becomes `AllocateAddress` at the image's *linked* base
(`:722-727`), `FindFreePages` is never entered for it, `P2FreeWhy` is never called, and the census
is blind to it. `PcdImageLargeAddressLoad` is TRUE here and not overridable — `AutoGen.h:214` in the
generated build is `_PCD_VALUE_PcdImageLargeAddressLoad 1U`, and `:166` is
`_PCD_VALUE_PcdLoadModuleAtFixAddressEnable 0ULL`, so the fixed-address branch at `:702` is dead and
the `else` arm at `:719-736` is the live one. That leaves the address and the stripped flag as the whole
question, and step 4.43 answered them for the 80 DRIVER files. This step answered them for
everything the dispatcher can load:

* **145** `.efi` under `Build/gauguinPkg/DEBUG_CLANGPDB/AARCH64` — `ImageBase 0x0` on every one,
  `RELOCS_STRIPPED` clear on every one.
* **55** blobs under `Binaries/gauguin` — the same, on every one.

200 files, no exception. Mu-Silicium relinks the XBL blobs to base zero and rebuilds their
relocation directories, which is what that directory is for, so the `>= 0x100000` arm is not merely
false for the promoted 46 — **it is unreachable for any image this firmware will ever load**, and
`PcdImageLargeAddressLoad` is a dead knob in both positions on this platform.

### `RelocationsStripped` is a header bit, and `has_reloc` is a different field

The second arm is the one that nearly got through, and it is the reason this step exists.
`tools/pe-facts.py` prints a column called `has_reloc`, and it is `False` for four of the 46
promoted entries: `StatusCodeHandlerRuntimeDxe` (slot 2, which starts), `EmbeddedMonotonicCounter`
(38), `RealTimeClock` (39) and `CapsuleRuntimeDxe` (42) — three of the four among the 27 failures.
An image with no base relocations *and* `RelocationsStripped` set would take the `AllocateAddress`
arm at address 0, with no fallback to `AllocateAnyPages` (`:730`'s guard needs
`!RelocationsStripped`). Read that way, `has_reloc=False` tracks the failure set and looks like the
mechanism the whole P2 investigation has been looking for.

It is not. `BasePeCoff.c:660-666` is the only place the field is assigned, and for a PE image it
reads one file-header bit:

```c
if ((!(ImageContext->IsTeImage)) && ((Hdr.Pe32->FileHeader.Characteristics & EFI_IMAGE_FILE_RELOCS_STRIPPED) != 0)) {
  ImageContext->RelocationsStripped = TRUE;
} else if ((ImageContext->IsTeImage) && (Hdr.Te->DataDirectory[0].Size == 0) && ...) {
  ImageContext->RelocationsStripped = TRUE;
} else {
  ImageContext->RelocationsStripped = FALSE;
}
```

The second branch is TE images only, and nothing in this payload is a TE image. So for all 46 the
question is `Characteristics & 0x0001`, and the tool's own `chars` column answers it: `0x2022` for
`StatusCodeHandlerRuntimeDxe`, `EmbeddedMonotonicCounter`, `RealTimeClock` and `CapsuleRuntimeDxe`
— executable, large-address-aware, debug-stripped, **bit 0 clear**. The comment above that code
names this exact case: *"Image has no base relocs, RELOCS_STRIPPED==0 => Image is relocatable but
has no base relocs to apply."* That is what `has_reloc=False` means on these four, and it is the
opposite of stripped.

The consequence is one field further on. `FixupDataSize = DirectoryEntry->Size / sizeof (UINT16) *
sizeof (UINT64)` (`BasePeCoff.c:1515`, and `:1521` for the TE case), so a zero-sized relocation
directory gives `FixupDataSize = 0`, and `Image.c:793`'s `AllocateRuntimePool (0)` is a 40-byte pool
chunk — a live allocation, but not a page request, and one that cannot return `EFI_OUT_OF_RESOURCES`
unless the pool head is empty. So the `n = 0` closure step 4.43 proved is not disturbed by these
four entries; they fail somewhere else.

`has_reloc` is a true statement about the `.reloc` section and a false hint about
`RelocationsStripped`; the two are different fields of different structures with different meanings,
and the tool prints the one whose name suggests the other. Worth carrying into the reading on its
own account: `pe-facts.py`'s per-field verdict table reports `shared` for every field it examines
except `ffs_size`, so **no PE header field separates the 19 from the 27** — which is the host's own
statement that the split is not a property of the files. The `chars` column is the one that would
have, and it does not.

### The one cell, and what a 16-page slip costs

Writing the `np` values out from the tool's own two inputs found an error in 4.43's decision table.
`Image.c:682-688` is:

```c
if (Image->ImageContext.SectionAlignment > EFI_PAGE_SIZE) {
  Size = (UINTN)Image->ImageContext.ImageSize + Image->ImageContext.SectionAlignment;
} else {
  Size = (UINTN)Image->ImageContext.ImageSize;
}
Image->NumberOfPages = EFI_SIZE_TO_PAGES (Size);
```

so `SectionAlignment` joins the request only above `0x1000`. Six of the 27 are subsystem-12 runtime
drivers, built at `0x10000` alignment:

| slot | entry | `SizeOfImage` | `SectionAlignment` | 4.43 said | correct |
| --- | --- | --- | --- | --- | --- |
| 27 | `SdccDxe` | 106496 | `0x1000` | 26 | 26 |
| 31 | `VariableRuntimeDxe` | 393216 | `0x10000` | 112 | 112 |
| 34 | `ResetSystemRuntimeDxe` | 393216 | `0x10000` | 96 | **112** |
| 38 | `EmbeddedMonotonicCounter` | 327680 | `0x10000` | 96 | 96 |
| 39 | `RealTimeClock` | 327680 | `0x10000` | 96 | 96 |
| 42 | `CapsuleRuntimeDxe` | 327680 | `0x10000` | 96 | 96 |

`SdccDxe` is `0x1000`-aligned, so its `np` is `106496 / 4096` and the rule does not reach it. The
other five are `SizeOfImage / 4096 + 16`: `393216 / 4096 + 16 = 112` and `327680 / 4096 + 16 = 96`.
Slot 34 is the only one that disagrees, and the row directly above it — slot 31, with the identical
pair of inputs and the identical expected output — was already 112. That is a transcription slip and
not a second formula, and the table is corrected in place above rather than left for the reader.

What it costs is the distinction the table exists to draw. A `t=5` row at 96 names one of
`EmbeddedMonotonicCounter`, `RealTimeClock` or `CapsuleRuntimeDxe`; at 112 it names
`VariableRuntimeDxe` or `ResetSystemRuntimeDxe`. With `ResetSystemRuntimeDxe` listed at 96, a panel
reading of `t=5 np=112` would have excluded it, and a reading of `t=5 np=96` would have included it
wrongly — in the one column that gets compared against a number printed on the screen.

### The comment, fixed, and 4.43's diagnosis of it corrected

4.43 recorded two comment defects and left both, on the ground that a source edit moves the hash of
the payload staged as `p2-freewhy-g`. One of the two — the note above `P2FreeWhyReport` at
`Mem/Page.c:1250-1256` — is now fixed, because it is part of how the panel gets read and because
the ground does not apply: **nothing was rebuilt.** `sha256` on
`work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img` is still
`cbe5a13114fc4a0465677e480a29a76fa2836cf2ae00fb9e9838c490e0102132`, and
`tools/probe-fingerprint.py --expect P2FreeWhy` on it still exits 0 with all ten instruments present.
Neither was assumed; both were run. What 4.43's sentence conflates is *editing* with *rebuilding* —
a rebuild moves the FV header's timestamp whatever the edit was, so the rule is about rebuilds, and
this was a comment. The correction is folded into `uefi/patches/mu-basecore-local.patch` by
`tools/regen-mu-basecore-patch.sh`, which reverse-applies the result against the tree it came from.

4.43's diagnosis of that defect was also wrong, which is worth recording because the wrong diagnosis
is the reason it was left. 4.43 read the sentence as naming `CoreLoadPeImage`'s 48-byte
`EFI_RUNTIME_IMAGE_ENTRY` pool at `Image.c:837`. The comment's "200-byte" is not that pool. It is
`CoreLoadImageCommon`'s `AllocateZeroPool (sizeof (LOADED_IMAGE_PRIVATE_DATA))` at `Image.c:1393` —
a different function from the one the sentence names, and a different pool type, `EfiBootServicesData`,
so `t=4` where the runtime-entry pool would have been `t=6`. And the sentence omits the allocation
that *is* `CoreLoadPeImage`'s other `EFI_OUT_OF_RESOURCES` on this path: `Image.c:793`'s
`AllocateRuntimePool (FixupDataSize)`, whose status lands at `:795`, `t=6`. It is reachable, which 4.43 established, and the
attribute that opens it is `Image.c:1629`:

```c
EFI_LOAD_PE_IMAGE_ATTRIBUTE_RUNTIME_REGISTRATION | EFI_LOAD_PE_IMAGE_ATTRIBUTE_DEBUG_IMAGE_INFO_TABLE_REGISTRATION
```

The comment now names both sites, both pool types, and the `t=` each one shows as.

`FixupDataSize` for the two images that matter is also exact now rather than "the largest is 5
pages". A 4,096-byte relocation directory is `4096 / 2 * 8 = 16384` bytes of fixup log;
`CoreAllocatePoolI` then does `ALIGN_VARIABLE (Size) + POOL_OVERHEAD` (`Pool.c:409-411`), where
`POOL_OVERHEAD = SIZE_OF_POOL_HEAD + sizeof (POOL_TAIL) = 24 + 16 = 40` (`Pool.c:24-32`, `:35-41`),
giving 16424, and `EFI_SIZE_TO_PAGES (16424)` is 5. So **`t=6` means `np=5`, and the driver is
`EnvDxe` (slot 0, which starts) or `SdccDxe` (slot 27)** — subsystem 12 is the gate at `Image.c:792`
and no other subsystem-12 image in the payload has a relocation directory above 116 bytes. 4.43's conclusion,
with the arithmetic shown instead of asserted.

### What this step did not change

`n = 0` still means no `EFI_OUT_OF_RESOURCES` out of an image load. Nothing here reopened it. The
`AllocateAddress` arm is dead for all 200 images; the `RelocationsStripped` arm is dead for all 46
promoted entries even though four of them have no `.reloc` section; `Image.c:697`'s preset is
overwritten on every branch through `:697-742`; and the two pool sites fail only through
`CoreAllocatePoolPages`, which is `FindFreePages`. Step 4.43 closed those by argument. This step
went back to the two that were closed only by assertion — the address on every image, and the
stripped flag on the four that look stripped — and both held, one of them after nearly going the
other way.

Nothing was flashed and no build was run. `P2 ERR` is still the first thing to read:

```
tools/probe-fingerprint.py --read        # in TWRP: must report p2-variants
tools/panel-text.py --decode PHOTO.jpg   # boot it, photograph the bottom of the panel
python3 tools/probe-fingerprint.py --expect P2FreeWhy \
    work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img   # passes; do this before flashing
```

The decision procedure above is unchanged. One of its 27 numbers is not.

## Step 4.47 — The section header closes the fork, and `sum=` stops being a length

### The reading that was left open, and the four bytes that end it

Step 4.16 left the Apriori read as a fork between two readings that both produce the
observed 46-character `SEQ`, on the ground that `mP2ApriSum` is taken over
`SizeOfBuffer` and so a short read prints the hash of a prefix. Step 4.15 had already
argued that a short read cannot happen; both were kept, which left the repo asserting
a reading it had also refuted.

The four bytes at `0x90` of the volume finish it. The Apriori file is the first FFS
file, at offset `0x78`, `gAprioriGuid`, FFS size 1148, FFS header 24 bytes, type
`0x02` and state `0xf8`. Its one section starts at `0x90` and reads `64 04 00 19`:

| field | value |
|---|---|
| declared size (24-bit LE) | `0x000464` = 1124 |
| type | `0x19` = `EFI_SECTION_RAW` |
| body | 1120 bytes = 70 GUIDs, first `D6A2CB7F` (DxeCore), last `CCCB0C28` (GraphicsConsoleDxe) |
| after the 70th GUID | `FFFFFFFF` — the file's erased tail — then the next file's header at `0x4f8` |

1148 − 24 − 4 = 1120, so the FFS size, the section header and the payload agree in
three places, and no size field anywhere on the path is stale.

### Why the declared size is the size the code reports

`Fv->ReadSection` is `FvReadFileSection` (`FwVol.c:432`); it reads the file and hands
the caller's pointer and size to `GetSection`
(`MdeModulePkg/Core/Dxe/SectionExtraction/CoreSectionExtraction.c:1245`). `GetSection`
computes

```c
  CopySize   = SECTION_SIZE (Section) - sizeof (EFI_COMMON_SECTION_HEADER);
  SectionSize = CopySize;
  if (*Buffer != NULL) {
    if (*BufferSize < CopySize) { Status = EFI_WARN_BUFFER_TOO_SMALL; CopySize = *BufferSize; }
  } else {
    *Buffer = AllocatePool (CopySize);
    ...
  }
  CopyMem (*Buffer, CopyBuffer, CopySize);
  *BufferSize = SectionSize;
```

`SectionSize` is `CopySize` **before** the clamp, and `*BufferSize = SectionSize` is the
last write on the success path. So the size the dispatcher receives is the section's
*declared* size and never the bytes the caller had room for, and
`CoreFwVolEventProtocolNotify`'s `SizeOfBuffer` (`Dispatcher.c:2069`) is 1120 —
`AprioriFile` is set to `NULL` at `:2062`, so the callee-allocated branch is the one
taken and there is not even a caller buffer to be too small.

`AprioriEntryCount = SizeOfBuffer / sizeof (EFI_GUID)` is therefore 70, from the
volume, on every boot. It is not a property of the run.

### What that does to the fork

The `bytes=752` reading needed `SizeOfBuffer` to be 752, i.e. a declared section size
of 756. The volume declares 1124. So of the two readings step 4.16 left open, the
second is not reachable, and `b4ba9d75` / `unhit=1` / `miss=none` are not readings this
image can print — they are the signature of a different image, which is the more
useful thing for them to be. What survives is the first reading and its own open end:

- `unhit = 24` — index 0, DxeCore, which is never in `mDiscoveredList` by
  construction, and the 23 names at `ap47..ap69`.
- `miss` — **the one number the step promotes to load-bearing**, and the job step 4.16
  gave it. It was already the interesting one and this step does not narrow it: the
  decoder's legal set for a *cut walk* is `1, 2, 7, 9, 10, 11, 12, 14, 22, none`, and
  `47` is not in it, because a cut leaves a physical suffix missing and `ap47..ap69`
  include files at physical 14, 20, 21 and 22. So `miss=47` is what step 4.12's tail
  assumption wants and would itself say the shortfall is *not* a walk cut, and any
  value the decoder does not list means the running image's Apriori-named files do not
  sit where this volume's do. Which of those it is, is what `P2 APRI` is read for.
- `bytes=1120`, `entries=70`, `sum=a998b263`, `first=D6A2CB7F…`,
  `last=CCCB0C28-4B24-11D5-9A5A-0090273FC14D` are now **predictions**, and the triple
  is read as a build identity check rather than as an arbitration: this payload's
  Apriori section hashes to `a998b263`, so a panel `sum=` that differs says the image
  running on the phone is not the image on the host. `last=` says the same thing more
  cheaply — the whole array ends at `CCCB0C28` (GraphicsConsoleDxe); `D06A77F4-…`
  (I2C, ap46) would mean the array in the running image stops at 46.

### The 23 names, and the one thing they explain for free

With the tail reading now the run's reading rather than a hypothetical, the absent 23
are the volume's own answer: `AdcDxe`, `UsbPwrCtrlDxe`, `QcomChargerDxeLA`,
`ChargerExDxe`, `UsbfnDwc3Dxe`, `UsbBusDxe`, `UsbKbDxe`, `UsbMassStorageDxe`,
`UsbMsdDxe`, `UsbDeviceDxe`, `UsbConfigDxe`, `ButtonsDxe`, `TsensDxe`, `SimpleFbDxe`,
`LimitsDxe`, `HashDxe`, `CipherDxe`, `RngDxe`, `DDRInfoDxe`, `SimpleTextInOutSerial`,
`ConPlatformDxe`, `ConSplitterDxe`, `GraphicsConsoleDxe`.

That set is exactly the P3 gate and nothing to do with the P2 assert: **every USB
driver in the volume is in it** (`UsbfnDwc3Dxe`, `UsbBusDxe`, `UsbKbDxe`,
`UsbMassStorageDxe`, `UsbMsdDxe`, `UsbDeviceDxe`, `UsbConfigDxe`, `UsbPwrCtrlDxe`), as
are all four console drivers and `SimpleFbDxe`, and the charger pair
(`QcomChargerDxeLA`, `ChargerExDxe`). The eight arch protocols the assert names are a
different and much tighter set, all well inside the 46: `ap31` Variable, `ap34` Reset,
`ap36` Watchdog, `ap37` Security, `ap38` Monotonic, `ap39` Real Time Clock, `ap42`
Capsule, `ap44` Bds.

It also makes an already-recorded fact concrete. No display or console driver is among
the 46 promoted drivers, and the panel shows the `P2` lines anyway — which is
`SerialPortLib` bound to `FrameBufferSerialPortLib` in a `DEBUG` build
(`SiliciumPkg.dsc.inc:163`), writing glyphs straight into the framebuffer and needing
no console at all. Two consequences follow, and the second is new here:

1. text on the panel is not evidence that BDS ran (`docs/00-plan.md` already says so);
2. **the `USE_CUSTOM_DISPLAY_DRIVER = 0` switch — the `SimpleFbDxe` build — cannot be
   read off the P2 panel at all.** `SimpleFbDxe` is `ap60`, in the absent 23, so it is
   not one of the drivers the Apriori walk promotes; whatever it would draw comes
   after the `P2` lines, from the ordinary FV walk if it comes at all. The panel's text
   is the serial binding, so it looks the same whichever way that switch is set. That
   switch can only be judged at BDS.

### What this step did not change

The 27. `P2 ERR` is still the first line to read, and every one of the 27 — `ap19`,
`ap20`, `ap21` and `ap23..ap46` — is inside the 46 and is promoted under either
reading, so nothing in this step touches them. The memory findings of steps 4.43–4.46
are untouched. And nothing was rebuilt or flashed: the change is one tool's output and
its docstring, four notes in this log, and the payload of record is byte-for-byte what
it was.

| | |
|---|---|
| instrument | the section header at `0x90` of `FVMAIN.Fv`, read from the staged payload (`tools/fv-census.py` now prints it and the FFS-size agreement beside it); `GetSection` at `CoreSectionExtraction.c:1245` |
| adds | `entries=70` is forced by the volume, so the 47-entry reading is dead and `sum=` becomes a build identity check; the absent 23 are named and are the whole of USB plus the whole console; `miss` is the one open number |
| prediction | `P2 APRI bytes=1120 entries=70 sum=a998b263 last=CCCB0C28-…`, `P2 STATS apriori=46/70`, `unhit=24` |
| does not close | the 27, and `miss` — 47 or lower is still what decides whether step 4.12's name tables stand |

## Step 4.48 — The SEQ length is the match count, and the instrument's own comment said it was the scan count

### What was read, and why it is the origin of the fork

`P2Digest` opens with this:

```c
  // The Apriori file as the firmware read it. Every line below is a consequence
  // of these three numbers, and none of them had been measured on the device:
  // `entries` is AprioriEntryCount, and the SEQ line is exactly `entries`
  // characters long, so a SEQ of 46 means either 46 entries were scanned and all
  // matched, or 70 were scanned and 46 matched.
```

The second clause is false, and the third is the arithmetic that makes it false. The
SEQ line is built out of `mP2AprioriRes[0..SeqLen)` and `SeqLen` is `mP2Apriori`
(`Dispatcher.c:2333-2336`):

```c
  SeqLen = mP2Apriori;
  if (SeqLen > P2BRINGUP_APRIORI_MAX) {
    SeqLen = P2BRINGUP_APRIORI_MAX;
  }
```

and `mP2Apriori` is incremented inside the **match** branch of the promotion loop,
after the entry has gone on the scheduled queue (`Dispatcher.c:2115-2123`):

```c
          if (mP2Apriori < P2BRINGUP_APRIORI_MAX) {
            CopyGuid (&mP2AprioriGuid[mP2Apriori], &DriverEntry->FileName);
            mP2AprioriRes[mP2Apriori] = '?';
          }

          mP2Apriori++;
          ...
          break;
```

So the length of the SEQ line is the number of entries that **matched**, never the
number scanned. "A SEQ of 46 means 46 entries were scanned and all matched" describes
a string that cannot exist: `mP2Apriori` cannot exceed `AprioriEntryCount`, and it
equals it only when every entry matched — which no boot of this volume can do, because
index 0 is DxeCore and the DXE_CORE branch of the walk fills in
`gDxeCoreLoadedImage->FilePath` instead of calling `CoreAddToDriverList`.

This is worth a step because **the fork that steps 4.13 through 4.47 spend their length
closing was generated by this comment.** It declares `entries` and the SEQ length to be
two spellings of one quantity, so a 46-character line looked like it needed either a
46-entry array or a 70-entry array with a 24-entry tail, and the question "which of the
two readings is it?" followed from that. Separated, they are measurements of different
things — `bytes`/`entries`/`sum` of what `ReadSection` handed back, the SEQ length of
what `mDiscoveredList` let through — and neither constrains the other. Which is the
conclusion step 4.47 reached from the section header, without noticing that the
premise it was arguing against was written into the instrument.

Two more claims in the same block were wrong for the same reason and went with it:

- `// The batch, one character per Apriori entry, in the order it was dispatched.` The
  characters are one per **promoted** entry, and their order is the order the Apriori
  file names them — the order `for (Index = 0; Index < AprioriEntryCount; Index++)`
  walks — not the order the volume holds them in, and not the order they dispatch in.
- `// ... and the discovered list is the one missing its tail, which is what the
  volume's own layout predicts.` The volume's layout predicts the opposite.
  `ap47..ap69` sit at physical 14, 20, 21, 22, 51, 53, 55..70 and 72, interleaved with
  the 46 that were promoted, and a walk that gives up drops a *physical suffix*. No
  stop drops those 23 and nothing else. `tools/fv-census.py` enumerates every stop this
  volume allows and prints the `miss` each one produces; `47` is not among them.

### What was checked and is not a defect

Two things in the same region were re-derived rather than assumed, and both hold:

- **The `miss` decoder's `stop in phys` column is sound.** A `miss` of *k* means entry
  *k* is the first non-zero index whose GUID matched nothing, and a walk that stops at
  physical *P* leaves undiscovered exactly the files above *P*, so `miss` is a
  well-defined function of *P* — the decoder is not claiming that a stop shortens the
  Apriori array, which it cannot. The two are checked against each other on this
  volume: stops at 43..51 give `miss=14 PlatformInfoDxeDriver` (physical 52, the lowest
  Apriori-named file above them), stops at 52..73 give `miss=22 ShmBridgeDxe` (physical
  74), and the SEQ lengths those stops allow (40..47 and 48..68) contain the observed
  46 only at stops 49 and 50 — which the slot-content check already refutes.
- **`Index 0` is skipped deliberately** (`if ((Index > 0) && (mP2ApriMiss == (UINTN)-1))`,
  `Dispatcher.c:2185`), and the comment beside it says why correctly. So `miss=0` is not
  a reading the device can produce, and `unhit >= 1` on every boot.

### The one number still open, and it is already on the payload

With the walk refuted as the mechanism on the SEQ's *content* and the stops refuted on
its *length*, what is left is that `ap47..ap69` were never in `mDiscoveredList` by some
path that is not `FvGetNextFile`. There is exactly one deployed field that separates
"the walk never handed them over" from "the promotion loop dropped them", and it is
`P2 STATS discovered=` — `mP2Discovered`, which `CoreAddToDriverList` bumps once per
successful `InsertTailList` (`Dispatcher.c:1546`), so it is `|mDiscoveredList|` and
nothing else.

On this volume `discovered` is forced: one add per DRIVER file, 0 from
`COMBINED_SMM_DXE`, 0 from `COMBINED_PEIM_DRIVER`, 0 from the DXE_CORE branch, and 0
from `FIRMWARE_VOLUME_IMAGE` because the t=4 walk finds no file in this volume. So it
must equal `seen` at t=0. `tools/fv-census.py` now prints that as a prediction beside
the `P2 WALK` lines, and the three readings are:

| `seen` at t=0 | `discovered` | SEQ length | reading |
|---|---|---|---|
| 80 | 80 | 69 | walk and list both whole — the 23 were handed over, so the promotion loop's own `CompareGuid`/`FvHandle` test is what let them go |
| 80 | < 80 | < 69 | the walk handed them over and the adds failed; in a DEBUG build the `ASSERT` in `CoreAddToDriverList` fires before the add can fail silently |
| < 80 | ≤ `seen` | < 69 | the walk stopped — but then the missing set is a physical suffix, which `ap47..ap69` are not |

The first row is the one the host analysis implies and the one that would move the
investigation off the walk entirely. It is also the row that is cheapest to falsify:
`P2 WALK` and `P2 STATS` are both on the staged payload, and `P2 WALK t=0 seen=80`
with a 46-character SEQ is a contradiction that no reading of the promotion loop
survives.

### The line numbers in this log, and the one convention they do not share

Editing `Dispatcher.c` moves every line below the edit, so this step re-checked the
`Dispatcher.c:` citations in this log against the tree rather than against the
paragraphs that carry them. Five were wrong and are now right:

| cited | actual | what it names |
|---|---|---|
| `:556` | `:1204` | the `CoreIsSchedulable (DriverEntry)` sweep in `CoreDispatcher` |
| `:2376`, `:2382`, `:2378` | `:2464`, `:2470`, `:2466` | `P2Bins ()`, `P2Key ()`, and the "Last, so that it is the last populated row" comment between them |
| `:2524-2529` | `:2552-2557` | `P2Digest ()` and its 40-copy repeat |
| `:263`, `:271` | `:264`, `:275` | `P2Key`'s `for` and its `if (Errors == 0)` |
| `:271` | `:286` | the `"KEY %d/%d err=%r at=%d free=%d miss=%d\n"` format string |

The first row is the interesting one, because it is not drift — it is a second
convention. `:556` is where that sweep sits in **upstream Mu**, and `:1204` is where it
sits once `uefi/patches/mu-basecore-local.patch` is applied. The local patch is
additive-only in this file, so the two numbering systems are related by a
piecewise-constant offset, and the patch's own hunk headers give every piece exactly:

| upstream | current | offset |
|---|---|---|
| 60 | 60 | 0 |
| 483 | 1108 | +625 |
| 519 | 1152 | +633 |
| 896 | 1543 | +647 |
| 1309 | 1958 | +649 |
| 1412 | 2071 | +659 |
| 1433 | 2112 | +679 |
| 1470 | 2225 | +755 |
| 1486 | 2486 | +1000 |
| 1497 and below | 2559 and below | +1062 |

which is what makes a single stale number possible in the first place: `:556` is the
right line for that sweep in the unpatched tree and 648 lines away from it in this one.
The rest of this log's citations are against the patched tree, and the
ones re-checked here — `:166-170`, `:191`, `:195-219`, `:280`, `:291`, `:683`, `:1546`,
`:2069` — are correct under that convention. `:486` and `:1062`/`:1107` were not
re-checked to a verified target in this step and were left as its remaining debt, to be
read at the lines they name before being trusted again. Step 4.49 did that read: they
are `:1066`, `:1111` and `:1156`, and this paragraph's own `:185-204` was wrong too, as is
`Mem/Page.c:1159`'s "there are three of them".

### A tool that could not run at all, on the one path step 4.37 rests on

Re-running the decode against the staged payload — a cheap check, since this step is
about which numbers in this log are trustworthy — found that

```
tools/apriori-index.py work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img \
    --seq ssssssssssssssssssLLLsLLLLLLLLLLLLLLLLLLLLLLLL
```

died with `NameError: name 'entry_at' is not defined` at `tools/apriori-index.py:438`,
on the line that pairs each character of the SEQ with its file. `entry_at` was
introduced in step 4.37 (`ebe2bb0`) and never defined; `bed9cc1`, the correction step
three days later, rewrote the surrounding subsection and did not reach it. So the
`--seq` path has been broken since 4.37 while 4.37's verdict — *the 27 load failures
are not the big ones* — has been standing in this log on the strength of what that
path prints.

It is fixed by defining the function it names: `entry_at (entries, pos, i)` is the
inverse of `positions`, returning the entry whose promoted position is `i`. That is
worth a definition of its own rather than an inline `entries[i]`, because `positions`
drops entries listed in `skips`, so position `i` is `entries[i]` only when nothing was
skipped before it. With it, the command exits 0 and reproduces the 4.37 split from the
staged payload's own tables:

```
    loaded and started: 19 files, 9,338 .. 307,246 bytes, median 36,924
    CoreLoadImage failed: 27 files, 19,050 .. 385,166 bytes, median 45,098
    failing files smaller than the largest succeeding one: RpmhDxe, PdcDxe, ...
```

which is the refutation the step claims — 26 of the 27 are smaller than the largest
file that loaded — now reproducible with one command instead of resting on a run that
could not happen.

### What this step did not change

No semantics. Three comments in `Dispatcher.c`, one prediction block in
`tools/fv-census.py`, one missing function in `tools/apriori-index.py`, and this section. Nothing was rebuilt and nothing was flashed:
the payload of record is still `cbe5a13114fc4a0465677e480a29a76fa2836cf2ae00fb9e9838c490e0102132`,
`tools/probe-fingerprint.py --expect P2FreeWhy` still exits 0, and
`uefi/patches/mu-basecore-local.patch` was regenerated from the tree and verified with
`--check` (`13 files changed, 1452 insertions(+), 23 deletions(-)`). The 27 `CoreLoadImage`
failures and the memory findings of 4.43–4.46 are untouched.

| | |
|---|---|
| instrument | `P2Digest`'s own comment block (`Dispatcher.c:2260-2275`) against `SeqLen = mP2Apriori` (`:2333`) and `mP2Apriori++` in the match branch (`:2120`) |
| adds | the SEQ length is the promotion count and never the scan count, so `entries` and the SEQ are independent and the 4.13–4.47 fork does not arise from them; `P2 STATS discovered=` becomes the field that decides where the 23 went, and the census now predicts it |
| prediction | `P2 STATS discovered=80 apriori=46/70`, `P2 WALK t=0 seen=80 iter=81 last=EBF342FE-B1D3-4EF8-957C-8048606FF671` |
| does not close | the 27 — `P2 ERR` is still the first line to read — and `discovered`, which needs the panel |
| citations | the `Dispatcher.c:` line numbers in this log are against the **patched** tree; `:556` was an upstream number and is now `:1204`, and the upstream→current offset for every hunk is in the table above |
| broken tool | `tools/apriori-index.py --seq` raised `NameError` since step 4.37 and is the path step 4.37's verdict is printed from; `entry_at` is now defined and the command exits 0 |

## Step 4.49 — every stale number was exact when it was written, and two live comments still count the ladder wrong

### The method, because it is the reusable part

Step 4.48 ended with a debt it could not pay from the tree alone. `Dispatcher.c:486` and
`:1062`/`:1107` were quoted for a property of the scheduled queue rather than for a symbol,
so checking them meant reading a line the current tree says is something else; and the same
step found a *second* convention in its own citations, because `:556` is the right number
for that sweep in **upstream Mu** and 648 lines away from it here. Both facts point at the
same question — how many other numbers in this log are claims about a tree that no longer
exists — and neither can be answered by reasoning about drift.

It can be answered by measurement. The local edits reach this repository as a patch against
a pristine upstream checkout, so **any revision's patched tree is reconstructible exactly**:

```
git -C work/uefi/Mu-Silicium/Mu_Basecore archive HEAD | tar -x -C /tmp/recon-<rev>
git -C /home/lvyufeng/Project/gauguin-HarmonyOS show <rev>:uefi/patches/mu-basecore-local.patch \
    | patch -p1 -d /tmp/recon-<rev>
```

All sixteen revisions of `uefi/patches/mu-basecore-local.patch` were reconstructed this way.
The newest reconstructs byte-identically to the working tree apart from line endings, which
is the check that makes everything below a measurement. Then a citation is checkable as what
it actually is — *a claim about the revision that was current when it was written*: `git log
--follow -S '<the number>'` gives the doc commit that introduced it, the patch revision whose
commit time precedes that one is the tree it was written against, and reading the named line
in that revision's reconstruction says whether the number was right at the time.

The patch is not quite additive in `Dispatcher.c` — it removes two lines, both in the last
hunk (`@@ -1486,11 +2486,73 @@`, inside `CoreDisplayDiscoveredNotDispatched`). That
does not affect the method or the offset table: each row there comes from a hunk header,
which carries its own net delta.

### Sixteen numbers, fifteen of them exact when written

Every stale `Dispatcher.c:` number this step found was **the right line in the revision that
was current when it was written**, except one. That is worth stating plainly because the
opposite — numbers that were never right, invented along the way — is the more natural thing
to assume about a log this long, and it is not what happened.

| cited | was right in | and named there | is now |
|---|---|---|---|
| `:523`, `:664` | `e864a59` | `do {` and `} while (ReadyToRun);` — the drain end to end | `:1062`, `:1216` |
| `:572` | `e864a59` | `P2Record (…, 'L', Status)` | `:1111` |
| `:582` | `e864a59` | the loop's only `continue;` | `:1127` |
| `:611` | `e864a59` | `P2Record (…, 'S', Status)` | `:1156` |
| `:326` | `755d58c` | `P2Retry ();` | `:462` |
| `:185-204` | `7f3cee9` | the whole of `P2LargestAlloc`, `(` through `}` | `:195-219` |
| `:189` | `7f3cee9` | `Ladder[]` | `:199` |
| `:196` | `7f3cee9` | the `CoreAllocatePages` rung | `:208` |
| `:599-628` | `7f3cee9` | all but the last line of `P2Tick` — signature at 599, `}` at 626 | `:677` |
| `:617` | `7f3cee9` | the `P2Tick (…)` calls | `:683` |
| `:1062` | `7f3cee9` | `P2Tick ('L', …)` | `:1111` |
| `:1107` | `7f3cee9` | `P2Tick ('S', …)` | `:1156` |
| `:2434` | `981d738` | `P2 FREE largest=%d pages` | `:2462` |
| `:2436` | `981d738` | `P2Bins ();` | `:2464` |
| `:486` | **never** | the FIFO loop is at `:487` in `b5a3e45`, the revision current when this number was written; `:486` is the `//` above it, and in upstream it is `DriverEntry->Scheduled = FALSE;` | `:1066` |

The first column of that table is history and not a citation. A checker that resolves it
against the working tree lands on unrelated code — `:486` is `));` and `:523` is a comment
about the busy-wait — which is precisely the defect this step removes from the rest of the
log. Only the last column is against the tree as it stands.

The exception is `:486`, and it fails in the way the other fifteen do not. It is off by
**one**: the `while (!IsListEmpty (&mScheduledQueue))` it names sat at 487 in `b5a3e45`, and
has sat at 487, 527, 563, 650, 699, 711, 712, 838, 875, 1006, 1024 and 1066 since, never at
486. It is the only number in this set that was wrong on the day it was typed; the other
fifteen were read where they pointed when they were written. `:556` — step 4.48's finding,
and not in the table because it is not one of these — is the other failure mode: not off by a
little, but written in the upstream numbering system, where it is exact.

The offset is not a constant, which is the reason a careful reader still could not have
caught these by arithmetic: the five `e864a59` numbers moved by +539 (`523`→`1062`) and +552
(`664`→`1216`) within the same function, because the instrumentation inserted between them is
not uniformly spaced. A piecewise-constant table is the only thing that reproduces it, and
step 4.48's table is that.

### `Initialized` is written and never read

This is the one finding here that is not about line numbers. The 4.10-era bullet argued that
a dispatched driver never returns to `mScheduledQueue` because the drain leaves it with
`Initialized = TRUE` and `Scheduled = FALSE`. The observation is right and the mechanism is
not:

```
$ grep -rn '\->Initialized' work/uefi/Mu-Silicium/Mu_Basecore/MdeModulePkg/Core/Dxe/
Dispatcher/Dispatcher.c:1108:            DriverEntry->Initialized = TRUE;
Dispatcher/Dispatcher.c:1134:      DriverEntry->Initialized = TRUE;
```

Two hits, both writes, both in `CoreDispatcher` — one on the load-failure path (`:1108`) and
one on the start path (`:1134`). **Nothing in the DXE core reads the flag.** What actually
keeps a dispatched driver off the queue for the rest of the boot is `Dependent`, which was
already cleared when the driver was put *on* the queue: at `:1274` in
`CoreInsertOnScheduledQueueWhileProcessingBeforeAndAfter` and at `:2111` in the Apriori walk.
The re-evaluation sweep is the only path back onto the queue, and it offers a driver only if
`DriverEntry->Dependent` is true (`:1203`, reached from `:1193`).

So the decision that a driver is finished is taken at the moment it is scheduled, before
anything is known about whether it loads, and `Initialized` records the outcome into a
variable no code consults. The source's own header comment survives this: it says such a
driver "is marked Initialized and skipped", which is exactly what happens. It is the log's
inference from the flag to the behaviour that had to go, and it has gone.

This matters past bookkeeping. Every read-order list in this log tells the reader to treat an
`L` and an `S` as the two distinguishable ends of one mechanism, and that is still true — but
the reason there are exactly two is that the loop has two ways out, not that a flag makes
them final. A third way out would not announce itself in `Initialized`.

### The ladder, counted one more time, and the two comments that still say three

Both live comments that describe how often `P2LargestAlloc` runs say three, and the number
is 47. `P2LargestAlloc` has four call *sites* — `Dispatcher.c:280`, `:291` (the two arms of
one `if`/`else` in `P2Key`, so only one is ever evaluated), `:683` (inside `P2Tick`), and
`:2462` (`P2 FREE largest=%d pages`) — which is three live paths, and those three paths do
not run three times. `P2Tick` is called once per attempted driver, at `:1122` for each load
that failed and `:1167` for each that started, fixed at 27 + 19 = 46 walks by the
46-character SEQ; `P2 FREE largest=` walks the ladder once more before `P2Bins` (`:2464`)
prints the row. **47 walks behind the `g=` that row shows.** `P2Key`'s own walk is the 48th,
reached from the `P2Key ()` call at `:2470` after that row is already printed, so it is in no
field on the panel.

The two sentences are:

- `Dispatcher.c:453-455` — "P2LargestAlloc runs three times before this - once per P2Tick
  inside the dispatch loop, and once for `P2 FREE largest=` above". Its two-item
  enumeration is right; its total is the count of call sites, not of runs, and it undercounts
  by 44.
- `Mem/Page.c:1159` — "…the second reason the flag is set inside P2LargestAlloc rather than
  at its call sites: there are three of them." The reason is right and the count is four.

Both are recorded in step 4.43's *says / should say* table, and neither is edited here. That
is a decision, not an oversight: either edit moves every line below it in its file, which
would invalidate every number this step just verified, and the re-verification is another
pass like this one. The `P2BRINGUP` block is already scheduled for deletion once DXE reaches
BDS, which moves those lines anyway; the comments go with it, or with a step that has to
rebuild for other reasons.

### What this step did not change

No semantics, and no source. Sixteen citation numbers in this log were corrected to lines
that were read, the paragraphs and table rows around them rewritten to say what the numbers
mean rather than only where they point, and the two comment defects above recorded rather
than fixed.
Nothing was rebuilt and nothing was flashed: the payload of record is still
`cbe5a13114fc4a0465677e480a29a76fa2836cf2ae00fb9e9838c490e0102132`,
`tools/probe-fingerprint.py --expect P2FreeWhy` still exits 0, and
`uefi/patches/mu-basecore-local.patch` was not regenerated because the checkout was not
touched.

| | |
|---|---|
| instrument | all sixteen revisions of `uefi/patches/mu-basecore-local.patch`, each reconstructed by applying it to a pristine upstream checkout and read at the line each citation names |
| adds | every stale `Dispatcher.c:` number but one was exact in the revision that wrote it, so the failure mode is citations without an expiry and not invented numbers; `:486` is off by one from the loop it names; `DriverEntry->Initialized` is written twice and read nowhere, and `Dependent` is the flag that decides the queue; the ladder runs 47 times behind `g=` and both comments about it say three |
| corrects | `:486`→`:1066`; `:523-664`→`:1062-1216`; `:572`→`:1111`; `:582`→`:1127`; `:611`→`:1156`; `:326`→`:462`; `:185-204`→`:195-219`; `:189`→`:199`; `:196`→`:208`; `:617`→`:683`; `:599-628`→`:677`; `:1062`/`:1107`→`:1111`/`:1156`; `:2434`/`:2436`→`:2462`/`:2464` |
| confirms | step 4.47's and 4.48's readings of the promotion loop, and the two-fates-not-four conclusion of 4.10 — for the right reason now rather than by way of a flag nothing reads |
| does not close | the 27 `CoreLoadImage` failures, the missing XHCI host driver, and `P2 STATS discovered=`, which is still the field that decides where the 23 went |
| citation rule | a `file.c:N` in this log is a claim about the patched tree at the revision current when it was written; `git log --follow -S` plus a reconstruction resolves it, and any source edit expires every number below it |

## Step 4.50 — the USB host stack is three sibling blobs, and the 234 bytes that were read as PCI are the architectural set

### The hole is real, and it is not an extraction gap

`MdeModulePkg`'s `UsbBusDxe`, `UsbKbDxe` and `UsbMassStorageDxe` are in this
firmware's `APRIORI.inc` (lines 75–77) and `DXE.inc` (103–105), and there is
nothing underneath them. Those three are the consumers: they bind to a
`EFI_USB2_HC_PROTOCOL` that some host-controller driver is supposed to publish,
and no driver in this phone's firmware publishes one.

Measured, over the three bootloader images this project has already pulled off
the device:

| image | size | `xhci` (case-insensitive) | `usbfn` |
|---|---|---|---|
| `part-xbl.img` | 7,327,744 B | **0** | 1 |
| `part-abl.img` | 2,097,152 B | 0 | 0 |
| `part-ablbak.img` | 2,097,152 B | 0 | 0 |

The negative is a measurement and not a broken search, which is what `usbfn` is
there for: it is the device-mode (gadget) driver, a different job from the host
controller, and the same scan finds it once. The same search finds `xhci` 21
times in bitra's `XhciDxe.efi`, so a firmware that has a host controller is
something this scan can see.

### The sibling, and why it is bitra

This is the one place in the port where a blob cannot come from this phone, and
the reason is structural rather than an extraction gap: there is nothing to
extract. The alternatives in the Mu-Silicium checkout are `Binaries/bitra/` and
`Binaries/generic/`, and bitra is the SM7225 layer this platform is already
built on — the generated `gauguin.dsc` includes `BitraPkg/BitraPkg.dsc.inc`
(line 71 of the file as it stands now), with `SOC_TYPE = 0` for SM7225 — whereas
`Binaries/generic/` is one directory shared across SM6150, SM8250 and SDM845
boards and is the least likely of the two to match a register map. The three
files, and their sizes:

| file | size | module type (from its own INF) | `[Depex]` |
|---|---|---|---|
| `XhciPciEmulationDxe.efi` | 45,056 B | `DXE_DRIVER` | `DXE_DEPEX` section, 234 B |
| `XhciDxe.efi` | 94,208 B | `UEFI_DRIVER` | none in the INF, none in the FFS |
| `UsbInitDxe.efi` | 32,768 B | `DXE_DRIVER` | `DXE_DEPEX` section, 18 B |

They are copied verbatim — INF, `.efi` and `.depex` together — and not through
the `INF_TEMPLATE` that writes the other 55 packages. A templated INF would drop
the depex and would drop `MODULE_TYPE = UEFI_DRIVER` on `XhciDxe`, which is the
binding that makes it a bus driver rather than a DXE driver.

### The 234 bytes, decoded, and the PCI reading withdrawn

The prior session recorded that `XhciPciEmulationDxe`'s depex requires a PCI
stack this firmware does not have, and concluded from that the depex could never
be satisfied and a host bridge would have to be folded into the platform. That
reading came from the name and from the `[Depex] TRUE` line in the INF. It was
withdrawn here, and the bytes are the reason. The file is 234 bytes, which is
13 × 17 + 12 + 1:

```
  221 B   thirteen PUSH ops, each a 16-byte GUID
   12 B   twelve AND ops
    1 B   one END
```

Thirteen pushes joined by twelve ands is a flat conjunction. Resolving each
pushed GUID against the headers, every one of the thirteen lands in exactly one
file under `Mu_Basecore/MdePkg/Include/Protocol/`, with no ambiguity and no
second candidate:

| # | GUID | header |
|---|---|---|
| 0 | `18A031AB-B443-4D1A-A5C0-0C09261E9F71` | `Protocol/DriverBinding.h` |
| 1 | `665E3FF6-46CC-11D4-9A38-0090273FC14D` | `Protocol/Bds.h` |
| 2 | `26BACCB1-6F42-11D4-BCE7-0080C73C8881` | `Protocol/Cpu.h` |
| 3 | `26BACCB2-6F42-11D4-BCE7-0080C73C8881` | `Protocol/Metronome.h` |
| 4 | `1DA97072-BDDC-4B30-99F1-72A0B56FFF2A` | `Protocol/MonotonicCounter.h` |
| 5 | `27CFAC87-46CC-11D4-9A38-0090273FC14D` | `Protocol/RealTimeClock.h` |
| 6 | `27CFAC88-46CC-11D4-9A38-0090273FC14D` | `Protocol/Reset.h` |
| 7 | `B7DFB4E1-052F-449F-87BE-9818FC91B733` | `Protocol/Runtime.h` |
| 8 | `A46423E3-4617-49F1-B9FF-D1BFA9115839` | `Protocol/Security.h` |
| 9 | `26BACCB3-6F42-11D4-BCE7-0080C73C8881` | `Protocol/Timer.h` |
| 10 | `6441F818-6362-4E44-B570-7DBA31DD2453` | `Protocol/VariableWrite.h` |
| 11 | `1E5668E2-8481-11D4-BCF1-0080C73C8881` | `Protocol/Variable.h` |
| 12 | `665E3FF5-46CC-11D4-9A38-0090273FC14D` | `Protocol/WatchdogTimer.h` |

That is the standard architectural-protocol set plus `EFI_DRIVER_BINDING_PROTOCOL`
— the same protocols the P2 investigation is already about, and eight of them
are the eight providers that still fail (step 4.9). There is no PCI requirement
in it, and nothing named `Pci*` anywhere in the list. No PCI host bridge is
needed and none was added.

Two smaller corrections from the same decode. The `[Depex] TRUE` in
`XhciPciEmulationDxe.inf` is not what ships: the `[Binaries.AArch64]` block in
that same file names `DXE_DEPEX|XhciPciEmulationDxe.depex`, and the built FFS
carries that section at 234 bytes, byte-identical to the sibling's copy. And the
earlier count of the conjunction was thirteen ands where twelve is the only
count that fits 234 bytes — 13 × 17 + 13 + 1 is 235, one byte too many.

`UsbInitDxe`'s depex is the other kind of thing entirely: 18 bytes, one PUSH and
one END, on `E722B03F-B250-42CE-8EBD-5BD51812D037`. That GUID is in no header
under `MdePkg/Include` or `MdeModulePkg/Include`, so it is Qualcomm's own — and
it is not foreign to this device: scanning all 127 `.efi` files under
`Binaries/gauguin/QcomPkg/Drivers` and `Binaries/bitra/QcomPkg/Drivers` for its
16 bytes finds nine files carrying it, including **this phone's own**
`UsbConfigDxe.efi` and `UsbfnDwc3Dxe.efi`. So whatever publishes it is a
Qualcomm driver already in the payload, and `UsbInitDxe` is waiting on a peer
rather than on something that does not exist. If that peer never publishes it,
`UsbInitDxe` stays unstarted and idle, which is the failure mode the next
section is arranged to prefer.

### Why these do not go into the a-priori list

bitra lists `XhciPciEmulationDxe` and `XhciDxe` in its `DXE.inc` **and** in its
`APRIORI.inc`, and that half is deliberately not copied. The a-priori promotion
loop is `Dispatcher.c:2104`; for a match it sets, at `:2111` and `:2112`,

```c
          DriverEntry->Dependent = FALSE;
          DriverEntry->Scheduled = TRUE;
          InsertTailList (&mScheduledQueue, &DriverEntry->ScheduledLink);   // :2113
```

and eleven lines below that the debug trace prints `RESULT = TRUE (Apriori)`
(`:2122`). `Dependent = FALSE` is the flag that decides the queue — step 4.9's
conclusion, and step 4.10 built its experiment on it — so a driver in this batch
has its depex read and then not consulted at all.

Putting a driver with a thirteen-protocol conjunction into that batch would
therefore take the one property that makes these three files different from the
other 55 blobs and switch it off, and start it at the one moment it cannot run:
before the eight providers among those thirteen exist. So all three go into
`DXE.inc` only, and are left to the ordinary sweep, whose promotion site is
`Dispatcher.c:1274-1276` and where a satisfied depex is what promotes a driver.

Two consequences, both measured rather than expected:

* The a-priori array in this payload is **70 entries**, the same as the
  baseline's. `P2 APRI`, `P2 SEQ` and `P2 STATS apriori=46/70` keep the shape
  they have on the payload now in `boot`, so a reading taken from either is
  comparable with a reading taken from the other.
* While `BdsDxe` still fails, `EFI_BDS_ARCH_PROTOCOL` is one of the thirteen, so
  the host stack **waits** rather than adding two more `L`s to a batch that is
  already failing 27 of 46. The wait is this part of the firmware depending on
  P2, not a second fault, and it is the reason the two candidates were separated
  here rather than lumped together.

### What the mechanism is made of

`SIBLING_BLOBS` and `stage_sibling_blobs()` in `tools/make_xbl_binaries.py` name
the three files and copy them, and `XHCI_HOST_DRIVERS` / `XHCI_HOST_GUARD` in
`tools/make_uefi_platform.py` say where in the lists they go. `--xhci-host` emits
`!if $(USE_XHCI_HOST_DRIVER) == 1` around them in `DXE.inc`, and the generated
`gauguin.dsc` gains one value beside `USE_CUSTOM_DISPLAY_DRIVER`:

```
  USE_XHCI_HOST_DRIVER           = 0
```

The default cannot acquire the trio by regenerating. The generator's driver set
is `present_drivers(device/dxe)` — the extraction, nothing else — and a sibling
is unioned into it only when the flag is passed, so a stale copy of another
board's driver sitting in `Binaries/gauguin/` cannot leak into a default build.
`tools/build-apriori-variant.sh xhci-host` is the experiment that turns it on; it
writes to `work/out/usb-host/` rather than `work/out/p2-variants/`, because the
payload in that second directory is an experiment on the a-priori order and this
is not one, and its exit trap deletes the staged blobs from both the repository's
tree and the Mu-Silicium checkout.

The verbatim INFs are what reached the volume. The three FFS files carry
`BEB12BEE-F6E1-11E1-9FB8-6C626DE4AEB1`, `B7F50E91-A759-412C-ADE4-DCD03E7F7C28`
and `0A134F0E-075E-40B3-9C63-3B3906804663` — the siblings' own `FILE_GUID`s —
where every one of the other 55 blobs carries a GUID synthesised from
`GUID_NAMESPACE`/`guid_for()` because Mu-Silicium's own `Binaries` INFs name
vendor GUIDs we cannot reproduce. A file that kept a real GUID is a file that
came through its own INF.

### What was built, and the four gates

`tools/build-apriori-variant.sh xhci-host`, exit 0, with every gate green:

```
0048 Images Verified
the array is exactly the INF order of APRIORI.inc: 70 entries, zero mismatches
all images structurally check out
matches FVMAIN.Fv.txt: 126 offsets and GUIDs, zero mismatches
```

The artifact is `work/out/usb-host/Mu-gauguin-xhci-host-gzip.img`, **1,169,408 B**,
sha256 `efc8e10d09f0f286011e1aacc638a7edd2ed5fcd86884640b28d14628f58f9f3`, and
the volume it carries has 126 files against the baseline's 123. Read back out of
the built FD, the three and their sections:

| FFS file | GUID | sections |
|---|---|---|
| `XhciPciEmulation` | `BEB12BEE-…` | `0x13` depex 234 B, `0x10` PE32 45,056 B, `0x15` UI 34 B |
| `XhciDxe` | `B7F50E91-…` | `0x10` PE32 94,208 B, `0x15` UI 16 B, `0x14` version 10 B |
| `UsbInitDxe` | `0A134F0E-…` | `0x13` depex 18 B, `0x10` PE32 32,768 B, `0x15` UI 22 B |

The first build of this variant used bitra's a-priori placement as well, and
produced `b11f8c75a26b6e784d439aa29e64b61bc03f5232db169b5bc69a9bcf5ff355cb` at
the same 1,169,408 bytes with a 72-entry array. It was superseded by the build
above and should not be flashed; nothing in the repository names it any more.

This payload does not answer the open P2 question and must not take the place of
the one in `boot` before that reading has been taken. Nothing in it is known to
come up on the device — the panel is the only thing that can say, and it has
nothing to say about a driver whose depex is currently unsatisfiable.

### The patch would not apply, in either direction

Found while checking the sync step, and fixed because it disables the guard that
exists to notice exactly this. `git apply --check` failed at
`ArmPkg/Library/ArmGenericTimerPhyCounterLib/ArmGenericTimerPhyCounterLib.c:59`
and `git apply --reverse --check` failed as well, which is the state
`sync-uefi-platform.sh` reports as `die "cannot apply $PATCH to $BASECORE - the
tree has diverged, reconcile it by hand"`. The patch itself was fine. Two of the
thirteen files it edits — `Dispatcher.c` and `Mem/Page.c` — had been left LF-only
in the checkout while the rest were CRLF, and the patch's own endings are mixed
(1,690 CR against 1,892 LF), so neither direction matched.

The repair was to reconstruct the patched state from the patch rather than to
guess: apply it to a pristine worktree of the submodule, confirm with
`tr -d '\r'` that the content was identical to the checkout's, and copy those two
files back with CRLF. Now:

| reading | value |
|---|---|
| `git apply --check` | fails at `ArmGenericTimerPhyCounterLib.c:59` (tree is not pristine) |
| `git apply --reverse --check` | passes |
| `git diff --stat` | `13 files changed, 1452 insertions(+), 23 deletions(-)` |
| `Dispatcher.c` | 2,558 CR, 2,558 LF |
| `Mem/Page.c` | 2,622 CR, 2,622 LF |

and `sync-uefi-platform.sh` reports `already applied`, which it had not been able
to do. The `git diff --stat` line is part of the check: while the two files were
LF-only the same tree reported 5,303 insertions and 3,874 deletions, because
`-text` in `.gitattributes` means git does not normalise and every line of both
files counted as changed.

### Two comments that were false, and one tool change kept for its own reason

* `tools/sync-uefi-platform.sh` said the 55 extracted drivers ship under an
  "Integrity Checks" arrangement. The phrase occurs nowhere else in the
  repository and described nothing; it now says what is true, which is that these
  are Qualcomm's signed images shipped as they came out of the extraction, that
  the INF beside each one is generated, and that no PE is rewritten anywhere in
  this project.
* The generated `gauguin.dsc` justified the switch with "they join the a-priori
  batch that P2 is still diagnosing". By the time it was written that was about
  to stop being true, and it is now the opposite of true.
* `tools/apriori-order.py` gained `--define NAME=VALUE`, which it needed for the
  a-priori guard this step first emitted and no longer needs, because the guard
  is gone from `APRIORI.inc`. It is kept: the tool's behaviour on an `!if` whose
  variable it was not told about is to exit, and this flag is the only route past
  that, so it is the difference between a diagnoseable failure and a dead end if
  the platform ever grows a second conditional. Recorded so that "unused in this
  tree" is a known state rather than an oversight.

### `UsbConfigDxe`, which is adjacent to this and was not acted on

Measured while looking for where the USB role is decided, and recorded because
the next person to ask why a host controller does not see a stick will arrive
here. bitra ships **three** builds of `UsbConfigDxe` — `.efi`, `.dualrole.efi`
and `.hostmode.efi`, all 94,208 B — and its INF binds `PE32|UsbConfigDxe.hostmode.efi`.
They differ from each other by 16 to 29 bytes, all of them build-time constants
folded into AArch64 immediates plus one adjacent pair at `.data+0x358` that reads
`01 01` in the hostmode build and `03 03` in the other two.

This phone's own `UsbConfigDxe.efi` is 77,824 B — 16,384 B smaller, so it is not
the same image with a flag flipped — and the offset above does not transfer to
it. But all of the four carry `UFP (DEVICE Mode)` and `DFP (HOST Mode)` and the
`Host Client Handle` error string, so the host path is compiled into this
device's copy as well, and the sibling's pin looks like a role default for a
board whose UEFI always wants to be a host rather than a feature this phone's
build lacks. Whether this device's copy can be moved to host mode at runtime is
not answerable from strings, and is not answered here.

It is not acted on because replacing a driver extracted from this phone with
another board's is a different kind of change from adding one this phone never
had: that rule is the whole basis of the port, and breaking it to fix a
hypothesis would need its own switch and its own step.

### What this step did not change

No behaviour on the device. The USB host stack is built, gated and parked behind
a switch that is 0 in the tracked platform; nothing was flashed; the payload of
record is still `cbe5a13114fc4a0465677e480a29a76fa2836cf2ae00fb9e9838c490e0102132`
and `tools/probe-fingerprint.py --expect P2FreeWhy` still exits 0. The
`P2BRINGUP` block is untouched, and the two undercounting comments step 4.49
recorded are still there — this step edited no source under `Mu_Basecore`, so no
line number in this log moved.

| | |
|---|---|
| instrument | the sibling's three INFs and `.depex` files read as bytes; the built FD read back with `tools/fv-inventory.py`; `tools/apriori-order.py` against the payload; a 127-file byte scan of every `.efi` under `Binaries/gauguin/` and `Binaries/bitra/` |
| adds | `SIBLING_BLOBS`/`stage_sibling_blobs()` and `--sibling` in `tools/make_xbl_binaries.py`; `XHCI_HOST_DRIVERS`/`XHCI_HOST_GUARD`/`--xhci-host` in `tools/make_uefi_platform.py`; `USE_XHCI_HOST_DRIVER` in the generated DSC; the `xhci-host` experiment in `tools/build-apriori-variant.sh` |
| withdraws | "its depex needs a PCI stack the firmware does not have, so it can never be satisfied" — the 234 bytes are thirteen pushes of the standard architectural protocols plus `EFI_DRIVER_BINDING_PROTOCOL`, joined by twelve ands; no PCI in it, and no host bridge added |
| corrects | thirteen ands → twelve, on the byte count; `[Depex] TRUE` in `XhciPciEmulationDxe.inf` is not what ships; the phantom "Integrity Checks" comment in `sync-uefi-platform.sh`; the DSC's claim that the trio joins the a-priori batch |
| confirms | step 4.9's reading of the promotion loop at `Dispatcher.c:2111-2113` and `:2122`, and step 4.10's use of it — which is why the trio is in `DXE.inc` only, and why the a-priori array is still 70 entries |
| leaves | `UsbConfigDxe` where it was: this phone's own build, 77,824 B, with the host-mode path compiled in and its default role unresolved |
| does not close | the 27 `CoreLoadImage` failures, and therefore the eight arch providers the new depex names — the host stack now waits on them instead of failing beside them; `P2 STATS discovered=` is still the field that decides where the 23 went |


## Step 4.52 — the one heap address the panel ever gave was read from the wrong end

The whole of step 4.31 rests on one row, and this step corrects how that row reads:

```
Loading Driver at 0x0009CBE3000 EntryPoint=0x0009CBE41DC Fat.efi
```

Step 4.31 glossed the address as *"20.9 MiB of 28.4 MiB allocated, 7.5 MiB left"* —
a heap two thirds full at the 30th a-priori entry. That is the reading a "the heap
ran out" explanation for the 27 failures would want, and it is backwards. The
arithmetic that makes it backwards is in `Page.c`, not on the device, and it turns
out to give a **hard floor on free memory** that this project has never had before.

**The allocator carves from the top of the free run downward.** `CoreFindFreePagesI`
walks every `EfiConventionalMemory` descriptor and keeps the one with the highest
`DescEnd` (`Page.c:1008`, `:1025`), then:

```c
  //
  // If this is a grow down, adjust target to be the allocation base
  //
  Target -= NumberOfBytes - 1;                            // Page.c:1031-1033
  ...
  if ((Target & EFI_PAGE_MASK) != 0) {                    // :1038
    return 0;
  }
  return Target;                                          // :1042
```

So the base that comes back is `DescEnd - (pages - 1)`: the allocation sits at the
**top** of the chosen run, and what is left of that run is **below** it. The chain
is unbroken from there to the panel — `FindFreePages` returns it unchanged
(`:1371-1374`), `CoreInternalAllocatePages` takes it as `Start` (`:1571`),
converts (`:1590`) and stores it (`:1615`, `*Memory = Start`), and
`CoreLoadPeImage` hands that same variable to `Image->ImageContext.ImageAddress`
(`Image.c:731-740`), which is what the deleted `Image.c:862` print showed. Pool
pages come through the same `FindFreePages` (`Page.c:2515`), so they descend from
the top too. Every allocation therefore starts just below the previous one.

**And there is only one run to descend into.** Step 4.28 established that
`[EfiFreeMemoryBottom, EfiFreeMemoryTop]` = `[0x9B800000, ≈0x9D45E000]`, 7261
pages above the PrePi allocations, is the entire `EfiConventionalMemory` world
(`Gcd.c:2393-2394`). One descriptor, always carved at its top, means the descent is
monotone: **no allocation can ever sit below a base that has already been handed
out.** That is what makes a single address a measurement rather than an anecdote.

**Two numbers, and which side each belongs on.** `0x9CBE3000` is `0x013E3000` =
19.89 MiB above the floor of that region, and `0x0087B000` = 8.48 MiB below its
ceiling. Read in the direction the allocator moves, that is **≤8.5 MiB used and
≥19.9 MiB still free** when `Fat.efi` went in — the same pair step 4.31 printed,
with the sides swapped. (`20.9` is not `19.89` rounded: it is the byte count read
in decimal megabytes and labelled MiB, which is the third arithmetic slip in that
one sentence along with the direction and the 35.4-vs-28.4 MiB row.)

**The bottom-up reading is not merely off, it is impossible, and the volume's own
size proves it.** `FVMAIN.Fv` is 7,352,320 B across 123 files. Reading the address
from the bottom asks for ≈20 MiB of resident images at the 37th file and the 30th
a-priori entry, out of a volume that holds 7.01 MiB in total — a 2.8× overshoot
against a bound no unloaded file can relax, since every image page in this system
comes from one of those files. Read from the top the same address asks for ≤8.5
MiB, which the volume can supply.

**What this closes, and what it does not.** Every heap field the P2 instruments
carry — `P2 FREE largest=`, `P2 FREE why=`, `P2Tick`'s `free=`, `bs9=` — asks one
question: was there room. This row is the only heap number the device has ever
reported, and it was the one piece of arithmetic that made "the heap was nearly
full" look supportable. It says the opposite, and it says it from a bound that
does not depend on the ceiling being exactly right: **at least 19.9 MiB of the
28.4 MiB region was free.** Step 4.26's fork is unaffected — the next instrument
still goes on the pool side — but the heap side is now closed from both ends:
4.6× headroom by capacity (4.28), and a ≥19.9 MiB floor under the one address ever
read.

What is *not* resolved is the size of the used band. Under 4.28's ceiling it is
≤8.5 MiB at the 37th file, but the volume cannot have supplied more than ≈7 MiB of
image pages even with all 123 resident, and fewer than forty were. So either PrePi
took more from the top than the FVMAIN — 4.28 counts 1795 pages for the volume and
names "the HOB list's few pages and DxeCore's own image" as uncounted, and this
step is the first place that slack becomes load-bearing — or a large part of the
used band is pool and driver allocations rather than images. The host cannot
separate those two and this step does not try. The field that separates them is a
single `free=` column taken *during* dispatch, which is precisely the reading the
panel has never produced.

| | |
|---|---|
| finds | the one heap address the device ever reported reads as **≤8.5 MiB used, ≥19.9 MiB free** of a 28.4 MiB region, because `CoreFindFreePagesI` allocates from the top of the run down |
| mechanism | `Page.c:1008`/`:1025` pick the highest `DescEnd`; `:1033` sets the base to `DescEnd - (pages-1)`; `:1371`→`:1571`→`:1590`→`:1615` carry it to `*Memory`; `Image.c:731-740` puts it in `ImageContext.ImageAddress`; step 4.28's single `EfiConventionalMemory` descriptor makes the descent monotone, so everything below a handed-out base is free |
| corrects | `docs/08` step 4.31 twice — "20.9 MiB of 28.4 MiB allocated, 7.5 MiB left" (direction, and decimal-vs-binary units) and "28.4 MiB" for the 35.4 MiB `0x02360000` row, which is the conflation step 4.28 added `tools/pe-facts.py` to prevent |
| bounds | free ≥ 19.9 MiB at `Fat.efi`, from one address plus the direction of the search; and the bottom-up reading is impossible by 2.8× against the volume's own 7,352,320 B |
| instrument | no new one: `Page.c`, `Image.c` and step 4.28's measured `FVMAIN.Fv` size read against the single panel row. No source under `Mu_Basecore` was edited, so no line number in this log moved |
| does not close | the 27 `CoreLoadImage` failures, `bs9=`'s value, or whether the used band is PrePi's uncounted pages or pool — the `free=` column during dispatch is still the reading that decides, and the panel has still never shown one |

## Step 4.53 — the FACP is a template, and the empty FADT pointers are the pre-install state

Step 4.45 read the six ACPI tables back out of the payload and checked each one.
Doing that again this session, four of the six come back with a valid checksum, a
fifth (`FACS`) has no checksum field to check, and **the sixth does not**: `FACP`
stores `0xb0` at offset 9, its 276 bytes sum to 200 mod 256, and `0xe8` is the byte
that would make it valid. Its four pointer fields are empty as well —
`FIRMWARE_CTRL` (`@36`), `DSDT` (`@40`), `X_FIRMWARE_CTRL` (`@132`) and `X_DSDT`
(`@140`) all read zero.

Read on its own that is a P3 blocker of the first order. A FADT that points at no
DSDT means no `UFS0`, no `URS0`, no CPU devices and no interrupt model, and a table
whose checksum fails is one an ACPI interpreter is entitled to discard outright.
Neither of those is what is happening here, and the firmware says so in three
places that can be read without a device.

**The checksum byte is recomputed over every table before any of them is
installed.** `AcpiPlatformEntryPoint` reads the `AcpiTables` FFS file section by
section and, for each one, calls `AcpiPlatformChecksum ((UINT8 *)CurrentTable,
TableSize)` at `AcpiPlatform.c:219` — one line before `InstallAcpiTable` at `:224`.
The function itself (`:126-144`) zeroes the field and writes `CalculateCheckSum8`
over the whole table:

```c
  ChecksumOffset = OFFSET_OF (EFI_ACPI_DESCRIPTION_HEADER, Checksum);
  Buffer[ChecksumOffset] = 0;                                 // :138
  Buffer[ChecksumOffset] = CalculateCheckSum8 (Buffer, Size);  // :143
```

So the byte in the volume is not read by anything. What is in the volume is
whatever the tool that produced the blob left in a field that is defined to be
recomputed.

**The empty pointers are the documented pre-install state, and the same function
fills them.** `AddTableToList` in `AcpiTableProtocol.c` sets them from the tables
that are actually installed:

```c
        // Update pointers in FADT.  If tables don't exist this will put NULL pointers there.   // :678
        AcpiTableInstance->Fadt1->FirmwareCtrl = (UINT32)(UINTN)AcpiTableInstance->Facs1;     // :680
        AcpiTableInstance->Fadt1->Dsdt         = (UINT32)(UINTN)AcpiTableInstance->Dsdt1;     // :681
```

with the 2.0+ branch at `:715-757` doing the same for `Fadt3->XFirmwareCtrl` and
`Fadt3->XDsdt` under the long comment block at `:735-745` about the DSDT/X_DSDT
mutual-exclusion rule and the two possible install orders. EDK2's own comment is
the whole answer to the finding: **a zero on disk is expected, and the field is
populated from the DSDT and FACS once those have been installed.** Each write is
followed by another `AcpiPlatformChecksum` on the FADT — `:853`/`:894` for the FACS
path, `:943`/`:993` for the DSDT path — and `PublishTables` then re-checksums the
RSDP, RSDT and XSDT through `ChecksumCommonTables` (`:1711`) under the comment at
`:159`, "Do checksum again because Dsdt/Xsdt is updated." Both orders are handled,
which is what the two comment blocks are for.

**A positive control settles it.** If the stored byte were a computed checksum,
some platform's would be the right one. The `FACP.aml` blobs of the eighteen
Qualcomm platforms in this tree differ from Moorea's only in `OemId`,
`OemTableId`, `OemRevision` and `CreatorRevision` — same length 276, same revision,
same `FLAGS`, same `ARM_BOOT_ARCH`, same `RESET_REG`, same zeroed pointers — and
**three of them store `0x00`** (`Cedros`, `Kailua`, `Kona`; their tables sum to
162, 177 and 180). A zero cannot be a valid checksum for a 276-byte table with a
nonzero OEM revision, so the field is a leftover from whatever emitted the original
OEM tables and is not maintained by anything downstream. The asymmetry in the
payload is then not a defect but a confirmation: **exactly one of the six tables
has a bad stored checksum, and it is exactly the one table the installer
rewrites.**

**And a second correction, to my own reading rather than to the firmware's.** I had
recorded `FACS ✗` beside `FACP ✗`. The FACS has **no checksum field at all** — the
64 bytes are signature 0-3, length 4-7, hardware signature 8-11, the waking
vectors, global lock and flags through 23, `X_FirmwareWakingVector` 24-31, version
32-35, reserved 36-59, OSPM flags 60-63 — so there was nothing there to verify.
The bytes are `'FACS'`, length 64, version 2, everything else zero, which is a
valid minimal FACS. "Invalid" there was a category error, not a finding.

**What the FACP does say, since P3 is the phase that has to live with it.** Every
x86 legacy field reading zero is not missing data, it is the model:

| field | value | what it means |
|---|---|---|
| `FLAGS` `@112` | `0x300000` | `HW_REDUCED_ACPI` (BIT20, `Acpi60.h:250`) \| `LOW_POWER_S0_IDLE_CAPABLE` (BIT21, `:251`) |
| `PREFERRED_PM_PROFILE` `@45` | `8` | `PM_PROFILE_TABLET` (`Acpi60.h:206`) |
| `ARM_BOOT_ARCH` `@129` | `1` | `ARM_PSCI_COMPLIANT` (BIT0, `Acpi60.h:223`) — reset and power go through PSCI |
| `RESET_REG_SUP` in `FLAGS` | clear | the `RESET_REG` GAS that is present (`address_space_id 3`, `0x009020B4`) is declared **not in use** |
| `PM1a_EVT_BLK`, `PM1a_CNT_BLK`, `PM_TMR_BLK`, `GPE0_BLK`, `SMI_CMD`, `ACPI_ENABLE`, `PM1_EVT_LEN`, `PM1_CNT_LEN`, `PM_TMR_LEN`, `GPE0_BLK_LEN` | all `0` | under `HW_REDUCED_ACPI` there are no PM1/GPE register blocks and no SMI command port, and `SCI_INT` is ignored — this is the correct encoding |

Revision 6, length 276, `QCOM`/`QCOMEDK2`, OEM revision `0x7150`, creator `INTL`
rev `0x20230628` — the same shape as all eighteen siblings. The three things P3
actually needs from ACPI are the three that are *not* in the FADT: the interrupt
model (the DSDT's GSIs and `GpioInt`s on `UFS0`/`URS0`/`USB0`/`UFN0`), the timers
(the `GTDT`), and the interrupt controller (the `APIC`). The FADT's job is to
declare hardware-reduced ACPI and PSCI, and it does.

**Where the offsets in steps 4.45 and 4.42 came from, and which artifact they
describe.** Walking the `AcpiTables` FFS file (`7E374E25-8E01-4FEE-87F2-
390C23C606CD`, type `0x02`, size `0xb3e`) in the payload of record reproduces the
recorded offsets exactly — six consecutive RAW (`0x19`) sections starting at
`0x54d484` — and adds the detail that the file also carries a 22-byte UI (`0x15`)
section after them. What has changed is not the payload but the tree: `Build/
gauguinPkg/DEBUG_CLANGPDB/FV/FVMAIN.Fv` is now **7,524,352 bytes across 126 files,
built 2026-09-25 01:19** — the payload's 7,352,320 plus exactly the three USB host
blobs' 94,208 + 45,056 + 32,768 = 172,032, and 123 plus 3 files. That volume's
`AcpiTables` file sits at `0x577630` and its six tables at `0x57764c`, `0x577690`,
`0x577c84`, `0x577f5c`, `0x578074`, `0x5780b8`. So "reading the build tree is
reading the payload" held on 2026-09-24 and stopped holding when the host stack was
added; the offsets were never wrong, they describe the payload of record, which is
the artifact the P2 gates use.

**The signature scan those steps used is not what would have found them.** The raw
scan returns **six** `DSDT` hits and **five** `FACS` hits in the payload, not one
each. The extras are debug strings inside `AcpiTableDxe.efi`
(`9622E42C-…-54F784652F6B` at `0x53d7a8`) — "DSDT table not found", "Failed to add
DSDT in the …", "The DSDT content", and the FACS equivalents — which is a nice
coincidence given that this is the module that fills the FADT's `DSDT` and `FACS`
pointers, and a trap for anyone who repeats the scan: the hit at `0x544f24` is
followed by a length field of `0x0000000A`, which passes every plausible sanity
bound, and it sits *before* the real table. The structural walk is the method that
cannot be fooled by it, and it is what step 4.53 used.

| | |
|---|---|
| finds | the FACP's stored checksum byte and its four empty pointer fields are the **normal pre-install state** of every FADT in this tree, filled and re-checksummed at runtime by `AcpiPlatformDxe` and `AcpiTableDxe` — not a defect, and nothing to fix host-side for P3 |
| mechanism | `AcpiPlatform.c:219` re-checksums every RAW section before `InstallAcpiTable` at `:224` (`:126-144` zeroes the field and writes `CalculateCheckSum8`); `AcpiTableProtocol.c:678-681` and `:715-757` fill `FirmwareCtrl`/`Dsdt`/`XFirmwareCtrl`/`XDsdt` from the installed FACS and DSDT — EDK2's own comment at `:678` is "If tables don't exist this will put NULL pointers there" — each write followed by another `AcpiPlatformChecksum` (`:853`, `:894`, `:943`, `:993`) and by `ChecksumCommonTables` (`:1711`) for the RSDP/RSDT/XSDT |
| positive control | the eighteen sibling `FACP.aml` blobs differ only in OEM/creator fields, and three of them (`Cedros`, `Kailua`, `Kona`) store checksum byte `0x00` on tables that sum to 162/177/180 — so the byte is an unmaintained leftover everywhere, and the payload's one invalid table is the one table the installer rewrites |
| corrects | my own reading of this session: `FACS` has **no checksum field** by spec, so "FACS ✗" was a category error, not a finding. And `docs/07:2507-2516` / `docs/08:7548-7560` describe the payload of record correctly but the `Build/` tree no longer holds that volume (it holds the `xhci-host` one, +172,032 bytes = the three blobs, 126 files, 2026-09-25 01:19; its tables are at `0x57764c`…`0x578158`) — and "take each hit whose length field is sane" is not what selects the six, since the raw scan gives six `DSDT` hits and five `FACS` hits, the extras being `AcpiTableDxe` debug strings, one with a length field of `0x0000000A` that passes a sanity bound |
| bounds | the six tables are located by walking the `AcpiTables` FFS file, so the offsets, lengths and checksums are exact and not scan-dependent; the four valid stored checksums (SSDT, DSDT, APIC, GTDT) and the one invalid one (FACP) are facts about the bytes, FACS has no checksum field to be either, and the FADT's semantic fields are read rather than inferred |
| instrument | no new one: `AcpiPlatform.c`, `AcpiTableProtocol.c` and `MdePkg/Include/IndustryStandard/Acpi60.h` on the host side, plus `tools/fv-inventory.py` against `work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img`. No source under `Mu_Basecore` was edited, so no line number in this log moved |
| does not close | the P2 gate — the 27 `CoreLoadImage` failures, `bs9=`, and the census against the step 4.43 predictions are all still unread, because the device has presented nothing on any port since `2026-09-24T14:59:32`. And it does not make the DSDT complete: it still carries no I2C, GPIO, buttons or thermal zones, which is work on the DSDT rather than on the FACP |

## Step 4.54 — this unit's overlay is entry 13, and its touchscreen is Novatek over SE0 SPI

`docs/05` records the touchscreen as a **Goodix** part. The device's own tree says
**Novatek**. Both readings name the same dump — `~/backup/gauguin/dt/`, the merged
tree as the running kernel sees it, taken 2026-09-22 17:15 — so one of the two is
wrong, and which one decides what P5 has to drive.

**The dump names the touch node, and it is not Goodix.** Two nodes answer it:

```
soc/spi@880000          compatible "qcom,spi-geni"   reg <0x880000 0x4000>
                        interrupts <0 0x259 4>       spi-max-frequency 0x2faf080 (50 MHz)
  └── touch_spi@0       compatible "xiaomi,spi-for-tp"   reg 0  10 MHz   status ok
soc/ts_novatek          compatible "novatek,NVT-ts-spi"                    status ok
```

and the Novatek node is not a placeholder — it carries a full part configuration:

| property | value | meaning |
|---|---|---|
| `novatek,irq-gpio` | `<0xc1 0x16 0x2001>` | tlmm GPIO **22**, rising |
| `novatek,reset-gpio` | `<0xc1 0x15 0x0>` | tlmm GPIO **21** |
| `novatek,swrst-n8-addr` | `0x3f0fe` | the register the protocol soft-resets at |
| `novatek,spi-rd-fast-addr` | `0x3f310` | the fast-read address |
| `novatek,config-array-size` | `2` | two entries in the driver's config array |
| `spi-max-frequency` | `0x989680` | 10 MHz |
| `pinctrl-names` | `pmx_ts_active`, `pmx_ts_suspend` | |

with `soc/xiaomi_touch` (`xiaomi-touch`) beside it. **The only Goodix node in the
whole tree is the fingerprint reader:**

```
soc/fingerprint_goodix  compatible "goodix,fingerprint"   status ok
                        goodix,gpio-irq   <0xc1 0x11 0x0>   tlmm 17
                        goodix,gpio-reset <0xc1 0x12 0x0>   tlmm 18
```

Goodix sits on tlmm 17/18 and the touch on tlmm 22/21: different parts on
different pins. That is the whole of the error in `docs/05` — it read the Goodix
node, which is the fingerprint, and concluded the touchscreen was Goodix. It also
explains `uinput-goodix`: a Goodix fingerprint driver's uinput interface for
gesture and wakeup events, not a touch protocol bridge. `docs/05`'s claim that
Xiaomi "does not drive it from a normal kernel input driver" goes with it —
`novatek,NVT-ts-spi` is exactly the vendor input driver `docs/00:249` already
names, and the Novatek node in this tree carries the registers that driver uses.
What survives from that section is the part that is about the *bus*: the SPI path
really is a transport shim, and the panel's own driver is reached through it.

**Which of the nineteen overlays this unit runs is now identified rather than
inferred.** The stock `dtbo` carries 19 overlays and the live tree is the *merged*
result of one of them, so the entry can be found by looking for markers that only
one entry declares. Fourteen candidates, and twelve of them are unique to a single
entry across all nineteen:

| marker | entries declaring it | in this unit's tree |
|---|---|---|
| `novatek,NVT-ts-spi` | **13 only** | `soc/ts_novatek` |
| `goodix,fingerprint` | **13 only** | `soc/fingerprint_goodix` |
| `xiaomi,spi-for-tp` | **13 only** | `soc/spi@880000/touch_spi@0` |
| `cirrus,cs35l41` | **13 only** | `soc/i2c@984000/cs35l41@40`, `@41` |
| `ir-spi` | **13 only** | `soc/spi@98c000/irled@0` |
| `qcom,fsa4480-i2c` | **13 only** | `soc/i2c@990000/fsa4480@42` |
| `awinic,aw8624_haptic` | **13 only** | `soc/i2c@990000/aw8624_haptic@5A` |
| `ti,bq2597x-standalone` | **13 only** | `soc/i2c@990000/bq25970-standalone@66` |
| `xiaomi-touch` | **13 only** | `soc/xiaomi_touch` |
| `maxim,ds28e16` | **13 only** | `soc/maxim_ds28e16` |
| `xiaomi,testing-mode` | **13 only** | `soc/testing_mode` |
| `xiaomi,onewire_gpio` | **13 only** | `soc/onewire_gpio` |
| `qcom,nq-nci` | 02–07, 09–14 | `soc/i2c@988000/nq@28` |
| `fpc,fpc1020` | 00, 13 | `soc/fingerprint_fpc` |

Twelve markers that exactly one of nineteen overlays declares, all twelve present
in this unit's tree, and no exclusive marker of any other entry present. **Entry 13
is this unit's overlay.** (The same method is what `tools/abl-boot-check.py` does
in reverse: it checks that every symbol the overlay names exists in our tree.)

**And entry 13's own structure is ambiguous in the same way the tree is.**
`fragment@40` adds `focaltech,fts_ts` at `i2c@988000`/`0x38` *and* `fragment@74`
adds `novatek,NVT-ts-spi`, so Xiaomi's overlay for this variant declares both touch
parts. What breaks the tie is the rest of the overlay set: entry 13 is the only one
of the nineteen that mentions Novatek at all. Eleven entries declare some touch
part — `focaltech,fts_ts` on 00, 04, 07, 08, 09 and 13, `synaptics,tcm-i2c` on 01,
08, 16 and 18, `synaptics,dsx-i2c` on 04, 07, 08 and 09, and `novatek,NVT-ts-spi`
on 13 alone — and the ten that declare none are 02, 03, 05, 06, 10, 11, 12, 14, 15
and 17. Entry 13 pairs Novatek with `novatek-mp-criteria-nvtpid`, which is in this
unit's tree as a child of `ts_novatek`.

**The two touch parts are mutually exclusive, and the pins say so.** The Novatek
and FocalTech nodes claim the *same* IRQ and reset lines:

| | controller | bus | irq | reset | coords |
|---|---|---|---|---|---|
| this unit | `novatek,NVT-ts-spi` | SE0 SPI `0x880000`, 10 MHz | tlmm **22** (`0x2001`) | tlmm **21** | (none in tree) |
| alternate | `focaltech,fts_ts` | I2C `0x988000` (int `0x163`), 400 kHz | tlmm **22** (`0x2008`) | tlmm **21** | `0,0,1080,2340`, 5 fingers |

One IRQ and one reset line cannot serve two populated parts, so these are
population options for one footprint. Note that `i2c@988000`'s
`qcom,i2c-touch-active = "focaltech,fts_ts"` marker does **not** disambiguate: it is
in this unit's tree too, because it comes from the base tree rather than from the
overlay.

**What this changes.** `docs/05`'s touch section heading and chip identity are
corrected in place, and `docs/01`'s spec line is annotated with the confirmation.
For P5 the target is a Novatek NVT part on SE0 SPI, with the six cells above plus
`swrst-n8-addr 0x3f0fe` and `spi-rd-fast-addr 0x3f310` as the bring-up facts; there
is no HID-over-I2C shortcut and no mainline driver, so it needs a real one, which is
what `docs/00:249` already assumed. For P3, when the SPI controller is described,
the block is SE0 at `0x880000` — the one our generated tree calls `i2c@880000`,
because mainline only ever described that GENI SE in its I2C strapping. The tracked
`dts/sm7225-xiaomi-gauguin.dts` inherits mainline's reading unchanged: `&i2c0` at
that address with commented-out NFC/ToF/amplifier children, and mainline's own
`/* HX83112A touchscreen @ 48 */` on `&i2c8`, which is neither this unit's part nor
this unit's bus. And none of it is in our generated tree: `Resources/DTBs/
gauguin.dts` has no `spi@880000`, no `touch_spi@0` and no `ts_novatek`, because the
overlay that carries them targets symbols we replaced with empty sinks — so nothing
in the payload currently describes the touch bus at all.

| | |
|---|---|
| finds | this unit's overlay is **entry 13** of the 19 in the stock `dtbo`, and its touchscreen is a **Novatek NVT-ts-spi on SE0 SPI (`0x880000`, 10 MHz)**, not the Goodix that `docs/05` records. The Goodix part on this board is the **fingerprint** reader on tlmm 17/18 |
| evidence | the merged tree dumped from the running kernel (`~/backup/gauguin/dt/`, 2026-09-22 17:15 — the same dump `docs/05` was written from): `soc/ts_novatek = "novatek,NVT-ts-spi"` with a full part configuration, `soc/spi@880000/touch_spi@0 = "xiaomi,spi-for-tp"`, `soc/fingerprint_goodix = "goodix,fingerprint"` |
| identification | twelve markers are unique to entry 13 among all nineteen overlays and all twelve are in this unit's tree; the two non-unique markers (`qcom,nq-nci`, `fpc,fpc1020`) are present too, and no other entry's exclusive markers are |
| corrects | `docs/05`'s "Touch — Goodix over SPI" section: the part is Novatek; the Goodix reading came from the fingerprint node, and `uinput-goodix` is that driver's uinput interface. Also its "not driven by a normal kernel input driver" — `novatek,NVT-ts-spi` is the vendor driver `docs/00:249` names |
| bounds | the tree proves which touch node this unit's overlay installs and that Novatek is unique to entry 13 among nineteen. It does not prove at runtime which part answered, because the vendor tree declares both and the driver probes; confirming that is one command on the device (`ls /sys/bus/spi/drivers`, `getevent -pl`) |
| instrument | `~/backup/gauguin/dt/` read directly as big-endian cells; `~/backup/gauguin/images/part-dtbo.img` decompiled to 19 `.dts` for the marker scan. Nothing rebuilt, nothing flashed, no source edited |
| does not close | the P2 gate, and touch itself — this is P5's target, not its driver |


## Step 4.55 — nothing in the payload of record is gated by its depex, and "no depex" is the most constrained case

The panel can name an a-priori driver that failed to load: that is what `P2 SEQ`
is for. It cannot name a **non**-a-priori driver held off by its dependency
expression, because a driver that never runs prints nothing at all — no load
line, no error, no position in the SEQ band. Both failures leave the same trace
on the panel, and only one of them is readable there. So the second question has
to be asked of the image, and `tools/depex-census.py` is that question written
down as a program.

The answer for the payload of record is **zero**. No driver in it is held off by
its dependency expression, and none is unjudgeable either. The rest of this step
is why that is the right answer and not a comfortable one, because the first two
attempts at it both came back wrong — and wrong in the direction that reads as a
finding.

### The mechanism: an a-priori driver's depex is read and never evaluated

This is the fact the whole census turns on. `CoreIsSchedulable` is only consulted
for a driver still marked `Dependent`:

```c
// MdeModulePkg/Core/Dxe/Dispatcher/Dispatcher.c:1203-1207
  if (DriverEntry->Dependent) {
    if (CoreIsSchedulable (DriverEntry)) {
      CoreInsertOnScheduledQueueWhileProcessingBeforeAndAfter (DriverEntry);
      ReadyToRun = TRUE;
    }
  } else { ... }
```

and the a-priori sweep marks every file in the a-priori array otherwise, before
anything runs:

```c
// Dispatcher.c:2104-2120
  for (Index = 0; Index < AprioriEntryCount; Index++) {
    ... if (CompareGuid (&DriverEntry->FileName, &AprioriFile[Index]) && ...) {
          DriverEntry->Dependent = FALSE;
          DriverEntry->Scheduled = TRUE;
          InsertTailList (&mScheduledQueue, &DriverEntry->ScheduledLink);   // :2113
          ...
          DEBUG ((DEBUG_DISPATCH, "  RESULT = TRUE (Apriori)\n"));          // :2122
```

`Dependent = FALSE` is the flag that decides the queue — step 4.9's conclusion,
and step 4.10 built its experiment on it. So for a driver in this batch the
dependency expression is still *read* (`CorePreProcessDepex`, which is where
`BEFORE`/`AFTER` ordering is taken from) and then *not consulted at all*. The axis
that decides whether a depex can block anything is therefore not "does it name a
missing protocol" but "is this driver in the a-priori file" — and on this platform
that file names 70 GUIDs, so it is most of the volume.

### The two wrong answers, and the guard that now catches the third

Both were produced by this tool and both are kept in its docstring, because both
read as results.

* The first version compared **names**: the uninstalled-list held bare macro names
  while Mu's headers spell them `..._PROTOCOL_GUID`, so no comparison ever hit and
  the census reported **0** — which is precisely the answer that says there is
  nothing here to look at. A name-keyed match that fails silently fails
  reassuringly. It is keyed on GUIDs now.
* The second version fixed that and reported **2**: `CapsuleRuntimeDxe` needing
  Variable Write and `RealTimeClock` needing Variable. Both are in the a-priori
  array (entries 43 and 40), so both are promoted and neither depex is evaluated.
  The answer was not merely incomplete, it was wrong in the direction of a
  discovery. Hence this version reads the a-priori file **out of the volume it is
  analysing** rather than off `APRIORI.inc`, which may not be the file that
  produced the image.

That produced a third failure mode, which is why the tool now refuses to run
rather than reporting a number: `UNINSTALLED` decides what counts as a protocol
this platform is missing, so a GUID in it that no header defines would silently
drop a protocol out of the comparison and let a held-off driver report as fine.
Injecting one bogus GUID is a negative test that passes: the tool names it on
stderr and exits 1 with *"the census would silently ignore them"*. Two details in
that name map were themselves wrong first — `Protocol/Variable.h` writes
`{ 0x1e5668e2, 0x8481, 0x11d4, {0xbc, 0xf1, 0x0, ...} }`, and a one-digit byte
field dropped the Variable protocol; and every definition in MdePkg is continued
with a backslash, so a pattern without the continuation lost all of them.

### The second mechanism, which is the opposite of what "no depex" sounds like

A driver with no depex section is not unconstrained. `Dispatcher.c:893-895` sets

```c
    Depex = NULL;
    Dependent = TRUE;
```

and `CoreIsSchedulable` sends a NULL depex down the UEFI 2.0 branch
(`Dependency.c:222-228`) to `CoreAllEfiServicesAvailable`
(`DxeMain/DxeProtocolNotify.c:81-93`), which walks `mArchProtocols` and returns
`EFI_NOT_FOUND` on the first entry that is not present. That table names thirteen
protocols — Security, Cpu, Metronome, Timer, Bds, Watchdog Timer, Runtime,
Variable, Variable Write, Capsule, Monotonic Counter, Reset, Real Time Clock — and
the test is on **all** of them. With eight missing, no non-a-priori driver without
a depex can run either.

In the payload of record that is five drivers, and one of them is a driver whose
name would never suggest it:

| payload | no depex, not a-priori |
|---|---|
| record (`p2-freewhy-g`) | `BootGraphicsResourceTableDxe`, `FeatureEnablerDxe`, `MacDxe`, `PwrUtilsDxe`, `VcsDxe` |
| `usb-host` | the same five plus **`XhciDxe`** |

So "carries no depex" reads like the least constrained thing in the volume and is
in fact the most constrained. Step 4.50 recorded that `XhciDxe` has no depex
section anywhere; what that means had not been followed through, and it means the
host controller sits under the strictest condition in the file, not the loosest.

### The filter that makes the count checkable against the panel

The dispatcher is only ever shown five file types (`mDxeFileTypes`,
`Dispatcher.c:697-703`: `DRIVER` 0x07, `COMBINED_SMM_DXE` 0x08,
`COMBINED_PEIM_DRIVER` 0x0A, `DXE_CORE` 0x03, `FV_IMAGE` 0x0B). The census applies
the same filter, and the filter is not cosmetic: without it the record payload
reports 123 files, and the 43 that are bmp images, panel XMLs, `.cfg` files and
the Apriori file itself all read as drivers with no depex and no constraints.
With it, **80** remain — which is exactly the `P2 WALK seen=80` the device
printed. That agreement is what makes the rest of these numbers comparable to the
device's at all, and it is why the tool prints the number with the panel's own
field name beside it.

### The answer for the payload of record

`tools/depex-census.py work/out/p2-freewhy-g/Mu-gauguin-silicon-gzip.img`:

```
inner FV 0x703000, 123 FFS files, a-priori file names 70 GUIDs
  dispatcher-visible files (DRIVER=80): 80  <-- `P2 WALK seen=` is this same number

DEPEX section sizes seen: 18 B x18, 36 B x7, 72 B x1, 90 B x1
27 of them carry a depex, 53 do not
  of the 27 with a depex: 21 are promoted by the a-priori file (depex inert), 6 are gated
  of the 53 without one: 48 are a-priori, 5 are not
```

27 of the 80 carry a depex. **21 of those 27 are a-priori, so their depex is
inert.** The remaining six (`RamManagerDxe`, `SmbiosDxe`, `SmBiosTableDxe`,
`AcpiTableDxe`, `AcpiPlatform`, `SetupBrowser`) depend on nothing worse than
`EFI_PCD_PROTOCOL_GUID`, `EFI_ACPI_TABLE_PROTOCOL_GUID` and the HII protocols,
and `PcdDxe` is a-priori entry 2, so PCD exists before any of them is considered.
None waits on one of the eight missing architectural protocols.

The conclusion step 4.9's eight missing protocols were about therefore changes
shape. They are **not** a dependency deadlock: every one of them has a producer
sitting in the volume's own a-priori array — `VariableRuntimeDxe` at entry 32,
`ResetSystemRuntimeDxe` at 35, `WatchdogTimer` at 37, `SecurityStubDxe` at 38,
`EmbeddedMonotonicCounter` at 39, `RealTimeClock` at 40, `BdsDxe` at 45. Their
absence is a **load** failure, which is the failure `P2 SEQ` already points at,
and not a second fault hiding behind it. The depex reading and the panel reading
agree about which one this is.

There is a bound on that, and it is stated in the tool rather than left to the
reader. Producer-to-protocol is not recoverable from a volume in general: above
the nine mapped GUIDs the tool knows the *name* of a protocol a depex names but
not who installs it, so a driver gated on some other uninstalled protocol would
land in the healthy-looking bucket. The nine are the ones the device actually
reported missing, which is why the map is worth having for exactly those — and it
is not a general ability to tell a wait from a dead end.

### The `xhci-host` payload: one waiting, one unjudgeable, and no change to the other two

Run against `work/out/usb-host/Mu-gauguin-xhci-host-gzip.img` (83
dispatcher-visible, 29 with a depex, the extra 234-byte section showing up in the
histogram):

```
== non-a-priori, gated on a protocol that is not installed (1) — satisfiable, and waiting on its producer to load
  XhciPciEmulation
      needs Bds arch protocol  (665E3FF6-46CC-11D4-9A38-0090273FC14D), installed by BdsDxe at a-priori 45
      needs Monotonic Counter arch protocol  (1DA97072-…), installed by EmbeddedMonotonicCounter at a-priori 39
      needs Real Time Clock arch protocol  (27CFAC87-…), installed by RealTimeClock at a-priori 40
      needs Reset arch protocol  (27CFAC88-…), installed by ResetSystemRuntimeDxe at a-priori 35
      needs Security arch protocol  (A46423E3-…), installed by SecurityStubDxe at a-priori 38
      needs Variable Write arch protocol  (6441F818-…), installed by VariableRuntimeDxe at a-priori 32
      needs Variable arch protocol  (1E5668E2-…), installed by VariableRuntimeDxe at a-priori 32
      needs Watchdog Timer arch protocol  (665E3FF5-…), installed by WatchdogTimer at a-priori 37

== gated, and the depex names a protocol no header defines (1) — cannot be ruled in or out from the image
  UsbInitDxe
      needs E722B03F-B250-42CE-8EBD-5BD51812D037   (defined by no header under work/uefi/Mu-Silicium)
```

Eight of the thirteen terms of `XhciPciEmulationDxe`'s conjunction are among the
protocols that are not installed, and each of the eight has an a-priori producer.
That is a **wait**, and it is step 4.50's own reading of this file — "the host
stack waits rather than adding two more `L`s to a batch that is already failing 27
of 46". What this step adds is that the wait is not an interpretation: the
producers are named and indexed out of the same volume the consumer is in.

`UsbInitDxe` is reported as a separate bucket and not folded into either of the
other two. Step 4.50 established that `E722B03F-…` is Qualcomm's own GUID and is
carried by nine blobs in the tree including this phone's `UsbConfigDxe.efi`, so a
publisher may well exist; what the image says is only that no header defines it,
which makes it neither a nameable missing protocol nor a known-good one. **The two
readings are compatible and neither is withdrawn** — step 4.50 says the producer
is a peer already in the payload, this step says the image cannot confirm it
because there is no header to confirm it against. Calling it "gated, nothing
known-missing" would print an unknown as reassurance, which is the failure mode
this whole step is about.

`XhciDxe`, the third blob, is in the no-depex bucket above.

### The label that was wrong, and was changed rather than explained away

The first version of this census printed its findings under **`CAN NEVER BE
SCHEDULED`**. That was wrong, and it was wrong for the same reason as the other
two errors: it took "names a protocol that is not installed" for "names a
protocol the platform never installs". Every protocol in that list has a producer
in the volume. A driver gated on one of them is held off *while P2 is unsolved*
and becomes schedulable when its producer loads — which is a diagnosis of P2, not
a finding about the driver.

The bucket is now **"non-a-priori, gated on a protocol that is not installed"**,
and the heading says "satisfiable, and waiting on its producer to load". The
distinction the tool now states it *can* prove — that no depex in either payload
names a protocol with no producer in the volume, for the nine mapped GUIDs — is
the one that would justify the old heading, and it holds only because the answer
is zero. Had any producer been absent from the volume, that would have been the
finding, and the tool would have had a bucket for it.

### What this step did not change

Nothing was built, nothing was flashed, no source under `Mu_Basecore` was edited,
and no line number in this log moved. The payload of record is still
`cbe5a13114fc4a0465677e480a29a76fa2836cf2ae00fb9e9838c490e0102132` and
`tools/probe-fingerprint.py --expect P2FreeWhy` still exits 0. This is host-side
groundwork that makes the P2 reading sharper when it comes; it does not advance
P2, and it cannot: the panel is the only thing that can say *which* of the 27
a-priori drivers failed to load, and that reading still has not been taken.

| | |
|---|---|
| finds | **0** of the 80 dispatcher-visible files in the payload of record are held off by their dependency expression, and 0 are unjudgeable. **5** are held off by the other rule — no depex at all, which requires all thirteen architectural protocols. Every one of the nine missing architectural protocols has a producer in the volume's a-priori array, so their absence is a **load** failure, not a dependency deadlock |
| mechanism 1 | the a-priori sweep sets `Dependent = FALSE` before anything runs (`Dispatcher.c:2104-2120`), and `CoreIsSchedulable` is only called under `if (DriverEntry->Dependent)` (`:1203-1207`) — so for the 21 a-priori depex-bearing drivers the expression is read and never evaluated |
| mechanism 2 | a NULL depex is not a free pass: `Dispatcher.c:893-895` marks it `Dependent`, and the UEFI 2.0 branch (`Dependency.c:222-228`) reaches `CoreAllEfiServicesAvailable` (`DxeProtocolNotify.c:81-93`), an AND over all thirteen `mArchProtocols` entries |
| checkable | the `mDxeFileTypes` filter (`Dispatcher.c:697-703`) takes 123 files to 80, and 80 is the `P2 WALK seen=80` the device printed — the offline and on-panel counts are the same measurement. `P2 APRI`'s `entries=70` agrees too |
| xhci-host | `XhciPciEmulation` waits on eight protocols, each with a named a-priori producer (step 4.50's "waits rather than adding two more `L`s", now measured); `UsbInitDxe` is unjudgeable from the image because no header defines `E722B03F-…` — compatible with step 4.50's finding that a Qualcomm peer may publish it; `XhciDxe` is in the no-depex bucket |
| withdraws | the heading `CAN NEVER BE SCHEDULED` for a driver gated on a protocol whose producer is in the volume — that state is *waiting*, and naming it "never" turns a P2 diagnosis into a false finding about the driver. Also the two earlier counts, **0** (name-keyed, never matched) and **2** (`CapsuleRuntimeDxe`, `RealTimeClock`, both a-priori and therefore inert) |
| guard | a GUID in `UNINSTALLED` that no header defines is a hard error and exit 1, so the tool cannot under-report by silently skipping a protocol. Verified by injecting one |
| instrument | `tools/depex-census.py` — FFS walk via `tools/fv-inventory.py`, a-priori array read out of the image via `tools/apriori-order.py`, depex GUIDs resolved against `MdePkg`/`MdeModulePkg`/`EmbeddedPkg`/`ArmPkg`/`SiliciumPkg`/`QcomPkg` headers and `.dec` files; `Dispatcher.c` and `Dependency.c` read at the lines cited |
| bounds | producer-to-protocol is not recoverable from a volume in general, so above the nine mapped GUIDs a driver gated on some other uninstalled protocol would read as healthy. The reverse direction is also open: a driver whose depex names only installed protocols is not proved schedulable, only not proved blocked |
| does not close | the P2 gate. `P2 SEQ`, `P2 STATS discovered=` and the 27 `CoreLoadImage` failures are where the eight missing protocols actually live, and none of it is readable without the device |

## Step 4.56 — the decoder's fixture was three lines this firmware cannot print, and every character it gets wrong it now names as a doubt

The panel is the only read-out this project has. There is no serial console, no
log partition, no debugger — `P2Digest` prints to the framebuffer and the reading
comes off a photograph of the glass. So the decoder in `tools/panel-text.py` is
not a convenience, it is the instrument, and an instrument whose test fixture is
made of text its subject cannot emit is worse than no test: step 4.48 found the
tool's own scaffold had been decoding to the wrong *grid* for eleven steps while
reporting success, because the fixture was written from memory of the digest
rather than from the digest.

This step re-read the fixture against `Dispatcher.c`, and three of its eight
lines were not lines this firmware can print.

### The three impossible lines

* `P2 APRI … promoted=46`. There is no `promoted` field. `Dispatcher.c:2279` is
  `"P2 APRI bytes=%d entries=%d sum=%x\n"` — and `%x` prints **bare** hex, so
  `sum=0xa998b263` was the wrong spelling of a value that is real (`a998b263`,
  the digest on the panel two steps ago).
* `P2 BIN init=1 code=150 data=800 bs9=Success bs16=Success`. `code=`, `data=`,
  `bs9=` and `bs16=` are fields of no `P2 BIN` line. The four BIN formats
  (`:466`, `:473`, `:481`, `:489`) carry `init=/hob_rc=/hob_rd=`, `rc=…used=`,
  `rd=…used=` and `def=`. `bs9=` and `bs16=` are the **fifth and sixth of the six
  `%r` fields on `P2 RETRY`** (`:495`) — so the fixture was testing `bs9=` in a
  position it never occupies, and a decoder that dropped it in its real one,
  fifth of six and past a wrap, would have passed the test that exists to catch
  exactly that.
* `P2 FREE largest=16 MiB in EfiRuntimeServicesCode`. `:2462` prints
  `"P2 FREE largest=%d pages\n"`. The words are not in the format.

The replacement is twenty lines, every one of them a format string out of
`Dispatcher.c` or `Mem/Page.c` with this device's own readings substituted, each
annotated with its source line. Two of the twenty wrap, so the twenty render as
twenty-two rows of the panel — which is what the scaffold compares against. It is
two and a half times the length of the old fixture and it renders a screen the
device has actually produced, including the 46-character SEQ with its `L`s and
the `P2WhyLetter` alphabet on the `P2 WHY` row.

### The two properties it never exercised, and both have cost readings

**`L` against `l` and `1`.** The old fixture rendered the SEQ as 46 `s`. The
single most important reading this project has taken — `ssssssssssssssssssLLLsLL…`
— is a discrimination between `L`, `l` and `1` inside a band of hex GUIDs, and no
test in the scaffold could fail on it or pass on it. It now renders the real
string, and `selftest` exits with a named reason if the fixture stops mixing the
two letters.

**The wrap.** No line in the old fixture reached 90 columns, so the console's
wrap was untested — and the wrap is what puts half of `P2 RETRY` and the tail of
`P2 APRI first=` on an unlabelled row of their own. Two of the new lines exceed
the console, and `selftest` exits if none does.

The wrap needed a model of the console's layout in *text*, because `decode`
returns one row per row of the panel, not one row per printed line. `console_rows`
is that model: the leading-space skip, the break at `columns`, and the wipe as a
truncation to the last `PANEL_ROWS`. It is written as a second spelling of
`console_render`'s rules rather than sharing code with them, and `selftest`
renders its rows back through `console_render` and requires the ink to be
identical — so if the two models drift, the scaffold says so instead of blaming
the decoder. At zero degrees they agree exactly, and `decode` returns the same
twenty-two rows with the same joins.

### Where `bs9=` actually lands

`tools/console-budget.py` could already say the RETRY line is one row or two
depending on the six status names. That is not actionable, because the reading
this project is gated on is `bs9=` — the fifth of the six — and "two rows" does
not say which row it is on. The new `field_spans` walks a format the same way
`render_width` does and reports the column span of every named field, and the
mixes are now tabulated:

* all six `Success` — 86 columns, one row, `bs9=` at columns 62–73;
* all six at the status this run produced (`Out of Resources`) — 140 columns,
  two rows, **`bs9=`'s name at column 98**, on the second row, with its value
  running to 118 and the first row ending mid-`rd16=`;
* all six at the widest (`Security Violation`) — 152 columns, `bs9=` at 106.

So on the observed status the field name is not on the line's first row at all.
That is the arrangement the fixture now reproduces, and the reason a reader who
reads only the RETRY line's first row reads no `bs9=`.

### The decimation was applied to the frame, and it cost whole rows

`load_photo` cut any picture whose long side exceeded 3000 px down to 3000 before
anything else looked at it, and the stated reason was sound — the alignment's
cost is linear in pixels and no glyph needs more. What it missed is that the
photograph is mostly wall. The bound was being spent on the background around the
screen, so the *text* was sampled at whatever resolution was left over.

Measured through the CLI on one saved photograph — the scaffold's own render,
padded, tilted 2.5°, 2380x5080 px, text block 2207x1137:

| pipeline | cell | rows | characters wrong | flagged weak |
|---|---|---|---|---|
| decimate the frame to 3000 (before) | 14.2 px | 22 | **917** of 1064 | 129 |
| decimate the block to 3000 (after) | 24.0 px | 22 | **1** | 46 |
| no tilt, block (after) | 24.0 px | 22 | 0 | 49 |

The before-case does not lose characters off the end of a line — it reads the
continuation row of `P2 APRI first=…` as ``3-S3-E-J-F- | - `|`` where the console
printed `31`, which is what a 14 px cell does to a 12-px glyph. `decimate` is now
applied to the crop, and `load_photo` keeps only a memory guard (`MAX_FRAME`,
6000 px) for the frame it still has to threshold whole. The coupling that bounds
`MAX_SIDE` is recorded where the constant is: 3000 px over 90 columns is a 33 px
cell, and the cell search looks from 0.4 to 3.0 cells, so anything past 3240 px
would be looking at a cell its own estimator cannot describe.

The scaffold itself bypasses `load_photo`, so this A/B is measured through
`--decode` on a written PNG and not by `--selftest`; the scaffold's cases are
decoded at the 2x-native cell of `make_photo` and never at the decimated one.
That gap is real and is left open on purpose — closing it means a 27 Mpx render
per case for one constant's worth of coverage.

### The one failure no flag can reach is ink against the edge of the frame

Everything above is a failure the instrument reports. There is a failure it cannot
report at all, and the same A/B produced it: if the text block runs off the
photograph, the missing characters are not in the image, so there is nothing to
be doubtful about. The crop that precedes decoding has a cell of margin inside
the ink, so ink touching the border of the cropped block is unambiguous evidence
that the block was cut rather than that the margin is thin.

Measured by decoding this tool's own unpadded render — `/tmp/p2screen-2p5.png`,
1080x2400 with text at x=0, which `--render-file` writes and which is therefore
exactly the picture a careless shot of the glass produces:

| picture | rows | characters wrong | flagged weak |
|---|---|---|---|
| `p2screen-2p5.png`, ink at x=0 | 21 | **937** of 1064 | 60 |
| `p2photo-2p5.png`, padded by (110, 140) | 22 | **1** | 46 |

The clipped one loses a row outright and every row's leading characters: `P2
RETRY` reads as `ETRY`, `P2 APRI` as `PPl`, and the tool says nothing — it
returns its rows and its flags as though the picture were complete. That is the
same magnitude as the decimation bug and it is worse in kind, because the
decimation at least produced characters that were wrong in place. So `--decode`
now checks the cropped block's four borders for ink before it decodes anything,
and names the one thing the reader can do about it: re-shoot with the whole
screen inside the frame. Confirmed to fire on the clipped picture and to stay
silent on the padded one, which is the only pair of photographs that can test it.

### The softening ladder never engages, and forcing it is worse

`decode` picks one glyph-template softness for the whole picture from a ladder of
three: the font, and two softened copies written to model lens defocus. Measured
on the scaffold's own probe, it picks **`soft0` at every blur radius the scaffold
can make** — from a clean render to r=6, including r=4 and r=6, where the decode
has already collapsed to 840 and 1042 wrong characters. The lead narrows with
blur (soft0 beats soft1 by 10,300 on the probe at r=0 and by 1,155 at r=6) and
never closes.

Forcing the choice answers the other question, and answers it against the ladder:
at r=1, where the shipped pick decodes exactly, forcing `soft1` costs **1051**
characters and `soft2` **1058**; at r=4, where the sharp pick is already failing
with 840, they are worse at 1006 and 1019. So the softened copies have never been
observed to help in any condition the scaffold can produce, and the sharp font
winning is load-bearing rather than a formality. They are kept, not deleted: a
real photograph blurrier than r=3 is the case that would settle it, and no
photograph has been read yet.

One measurement of my own was wrong before it was right, and it is recorded
because it failed in the direction of a finding. The first probe accumulated
every `score_cells` call tagged by softness level, which mixes `align`'s bulk
scoring into the total, and it reported that `soft0` loses by a factor of 28 —
i.e. that the ladder was silently in use with the wrong rung. Counting each
level's *first* run of same-sized calls, which is the pick and nothing else,
reverses it. A probe that answers a question about a closure has to be checked
against what the closure does, not against what it plausibly does.

### What is left wrong, and why it is reported rather than fixed

The corrected scaffold decodes **9 of 11** degraded photographs exactly. Both
failures are one glyph wide and both are the same glyph:

* **2.5° off level** — one character, `rd16=Out of Resources`'s `R` read as `P`,
  on panel row 16 at column 89.
* **all of it** (glare, noise, blur, 1.5° tilt, a 2% zoom and a shift together) —
  three characters: `P2 APRI`'s `R` read as `P` and its `I` as `l`, and one `P`
  read as `|`.

`R` and `P` differ by four pixels: the diagonal leg at rows 6 to 9,
`#.#..`/`#..#.`/`#...#`/`#...#` against `#....`/`#....`/`#....`/`#....`. Those
four are the only pixels in the glyph with no inked neighbour, and one 3-tap pass
of `soften` puts them at 0.562, 0.375, 0.625 and 0.562 — straddling the half-ink
line — with a second pass taking all four under it (0.492, 0.344, 0.473, 0.410).
`R` is the letter this device's status line is made of: `Out of Resources`, six
times on the RETRY line alone.

So the residual is real and it is characterised. What changed is not the decode
but the report: `decode` now records the runner-up glyph at every doubtful
position, and `--decode` prints it — the two rows below are verbatim from the
tilted CLI run in the table above, and they carry three things at once: the wrap
that puts `bs9=`'s name on a row with no label (`29 |sources bs9=…`), the twelve
positions on the RETRY line that the tool is not sure of, and the one it is
actually wrong about, at column 89, named as a choice between two glyphs:

```
   28 |P2 RETRY rc16=Out of Resources rc48=Out of Resources rc112=Out of Resources rd16=Out of Pe|   <- 12 weak: 19 (o or c), 25 (o or c), 41 (o or c), 47 (o or c), 64 (o or c), 67 (R or P), 70 (o or c), 79 (1 or l), 80 (6 or S), 82 (O or C), 86 (o or c), 89 (P or R)
   29 |sources bs9=Out of Resources bs16=Out of Resources|   <- 4 weak: 17 (o or c), 23 (o or c), 39 (o or c), 45 (o or c)
```

and the selftest stops totalling "wrong" as one thing. Each differing character
is classified: **flagged** if the decoder also marked it weak, **unannounced** if
the decoder asserted it confidently. The tally for this scaffold is **4 differing
characters, 4 flagged, 0 unannounced** — the decoder never once asserted a
character the panel does not have. That is the distinction worth having, because
an unannounced difference is a defect and a flagged one is the instrument saying
which two glyphs to compare against the glass. The failures still count as
failures: `--selftest` exits 1 and prints 9/11.

| | |
|---|---|
| finds | the decoder's fixture was written from memory and **3 of its 8 lines** were formats this firmware cannot print — `promoted=`, `P2 BIN code=/data=/bs9=/bs16=`, `P2 FREE largest=16 MiB in …`. `bs9=` was being tested in a position it never occupies. The fixture is now 22 lines, each a real format string with this device's readings, cited to its `Dispatcher.c`/`Page.c` line |
| coverage now asserted | the SEQ mixes `s` and `L` (the `L`/`l`/`1` discrimination the SEQ reading depends on); at least one fixture line exceeds the console's 90 columns (the wrap); the RETRY line exists; and `bs9=` starts at or past the column limit. `selftest` exits with a named reason if any is lost |
| wrap model | `console_rows` gives the console's layout as text — leading-space skip, break at `columns`, wipe as a truncation to the last `PANEL_ROWS`. Verified by ink identity against `console_render`, and at 0° against `decode`: 22 rows, identical strings and joins |
| `bs9=`'s row | `tools/console-budget.py --mu …` now reports field spans: at the observed status (`Out of Resources` ×6) the RETRY line is 140 columns and **`bs9=`'s name starts at column 98, on the second row**, with no label on that row. At six `Success` it is 86 columns and `bs9=` is at 62–73 on row 1 |
| softness ladder | `decode` picks `soft0` at every blur radius 0–6, including radii where it is already failing (840 wrong at r=4). Forcing `soft1`/`soft2` costs 1051/1058 characters at r=1, where the shipped pick is exact. The copies are kept, not deleted, and are on record as never having been observed to help |
| decimation | the 3000 px bound was applied to the frame, so it was spent on the wall around the screen and left a 14.2 px cell. Applied to the crop instead, the same 2.5° photograph goes from **917 wrong characters of 1064** to **1**. `MAX_FRAME` (6000 px) is now only a memory guard, and `MAX_SIDE`'s ceiling is recorded where the constant is: past 3240 px the cell exceeds 3.0 cells and the estimator can no longer describe it |
| frame edge | a block that runs off the photograph loses characters that are not in the image, so no flag can reach them: this tool's own unpadded render at 2.5° decodes **937 wrong of 1064** with `P2 RETRY` reading `ETRY`, and says nothing. `--decode` now tests the cropped block's four borders for ink first and tells the reader to re-shoot |
| residual | **9/11** exact, and through `--decode` on a realistic photograph the same tilt costs **1** character of 1064. The failures are the `R`-vs-`P` (or `I`-vs-`l`) confusion: `R`'s four leg pixels have no inked neighbour, and one soften pass puts them at 0.375–0.625, at the half-ink line |
| what changed for the reader | `decode` returns the runner-up glyph at each doubtful position and `--decode` prints `89 (P or R)` instead of `89`; `selftest` classifies each differing character as flagged or unannounced and prints the tally — **4 flagged, 0 unannounced** on this scaffold |
| wrong answer recorded | my own first probe reported that `soft0` loses the pick by 28× and the ladder was therefore in use with the wrong rung. It counted `align`'s bulk scoring as part of the pick. Counting each level's first run of same-sized calls reverses the result |
| does not close | the P2 gate, and it cannot: no photograph of the panel has been read, and the device has not been attached to this host this session. `bs9=`, `P2 SEQ`, `P2 STATS discovered=` and the 27 `CoreLoadImage` failures all still need the glass |


## Step 4.57 — P3's remaining tables need ACPI names a device tree does not carry, and no SM7225 reference has them

Host-side, and it started as an attempt to write the nodes rather than to test
them. P3 item 1 asks the DSDT to describe UFS, XHCI, **I2C, GPIO, buttons and
thermal zones**, and the file has the first two. Everything the rest needs is
addressing — register windows, interrupt numbers, pin numbers — and the device
tree has all of it: `pinctrl@f100000` at `0xF100000 + 0x300000` with nine GIC
interrupts; `spmi@c440000` with five windows; `thermal-sensor@c263000` and
`@c265000` with two each; seven GENI serial engines; the touch bus at
`spi@880000` on tlmm 22 and 21. So the nodes looked like mechanical work, and
the plan was to write them, cite every address, and compile.

They cannot be written, and the reason is a property of ACPI rather than of this
board. **None of that is a name.** A device tree describes how the hardware is
wired; ACPI describes what the hardware *is*, to a driver that matches on a
string. The string — `_HID` — is in no device tree, and neither is the `_DSM`
contract these blocks carry. The only way this port can supply them is to take
them from a platform that already has them.

### Whether a Qualcomm block's `_HID` travels between SoCs, measured

That is a question with a yes/no answer, so it was measured instead of assumed.
`tools/acpi-hid-census.py` disassembles every reference DSDT in the Silicium-ACPI
tree, walks each file's device tree, attributes every `Memory32Fixed` window to
the innermost enclosing device that has a `_HID`, and tabulates the names seen at
each of gauguin's own block addresses — the base address being the one key both
the ACPI and the device-tree worlds carry.

36 reference DSDTs. Result:

> **Superseded by Step 4.58.** The 36 was `*/DSDT.aml` alone; the corpus is 66
> tables, and the board variants it skipped are where Qualcomm writes the
> *complete* device list. Lahaina ships `DSDT_MTP` and `DSDT_Minimal` and no
> plain `DSDT.aml` at all, so the trim-only glob read none of Lahaina's device
> list in any form. TLMM's row should read **11 references, 5 names** — the
> fifth being `QCOM0C0C`, and the count including Lahaina's `DSDT_MTP` and
> Kailua's, which are the two that carry gauguin's exact window. SPMI's row
> should not read 0: the test in it, not the corpus, is what returned that.
> The `QCOM<soC><block>` reading below is right; the "block id is not a function
> of the SE index either" conclusion is wrong, and Step 4.58 measures why.

| block | window | references | distinct `_HID`s |
|---|---|---|---|
| TLMM | `0x0F100000` | 7 | **4** — `QCOM1A0C` ×3 (lemonade, venus, vili), `QCOM0A0C` ×2 (lisa, a52sxq), `QCOM250C` (alioth), `QCOM090C` (renoir) |
| SE0 | `0x00880000` | 5 | **4** — `QCOM0811` ×2, `QCOM0C10`, `QCOM0511`, `QCOM140F` |
| SE0 UART | `0x00884000` | 2 | 2 — `QCOM2510`, `QCOM1A10` |
| SE1 | `0x00888000` | 3 | 3 — `QCOM0C10` (with `_CID QCOMFFEA`), `QCOM2510`, `QCOM1411` |
| SE2 | `0x00980000` | **0** | — |
| SE3 | `0x00984000` | 3 | 2 — `QCOM0A10` ×2, `QCOM2510` |
| SE5 | `0x00988000` | 2 | 2 — `QCOM250E`, `QCOM090E` |
| SE6 | `0x0098C000` | 4 | 3 — `QCOM0A10` ×2, `QCOM2510`, `QCOM1A16` |
| SE7 | `0x00990000` | 3 | 3 — `QCOM250E`, `QCOM0A10`, `QCOM1A10` |
| SPMI | `0x0C440000` | **0** | — |
| TSENS0 | `0x0C263000` | **0** | — |
| TSENS1 | `0x0C265000` | **0** | — |

The same window carries a different name on every SoC. That is not noise in the
corpus: it is the SoC's own numbering, and it is visible as one — the TLMM block
is `..0C` on all four and the rest of the SoC's blocks take successive ids under
the same two-hex prefix (`renoir` 09, `lisa` and `a52sxq` 0A, `vili`/`venus`/
`lemonade` 1A, `alioth` 25, `sur(ya)` 14, `miatoll` and `a52q` 08, `aston` 0C,
`nabu` 05). There is no SM7225 in that list, and there is nothing to interpolate
with: `SE2` is `QCOM2510` on lisa and `QCOM0A10` on a52sxq, `SE7` is `QCOM250E`
on alioth and `QCOM1A10` on venus, so the block id is not a function of the SE
index either.

Two of the twelve blocks have no reference at all — not a wrong form, no form.
The `_DSM` contracts are per-SoC in the same way: the GPIO node returns `0x0140`
on vili, `0x0100` on alioth and `0x0180` on lisa, and nothing in the tree says
what the number means.

> **Corrected by Step 4.58.** Not two. The two this sentence means are TSENS0 and
> TSENS1, and for those it is true for a stronger reason than the one given: the
> corpus has no thermal-sensor device of any kind, at any address, on any SoC —
> no `TSEN`, no `TSSC`, nothing. SPMI was the apparent third, and the test was
> the cause: its window did not move, it is *coarse*. Every reference `SPMI`
> declares `0x0C400000` for `0x02800000` — forty megabytes — and gauguin's
> arbiter at `0x0C440000` is inside that region, so an equality test on the
> address reports a miss where containment finds 22 tables.

And the same measurement applies to the reference this file's form actually came
from. `Platforms/Realme/bitra/DSDT.aml`, disassembled, has **exactly the five
devices gauguin's DSDT has** — `UFS0`, `URS0`/`USB0`/`UFN0`, `DEV0` — and
nothing else. Moorea and Rennell have no DSDT at all, only the `DSDT_Minimal.asl`
the eight CPU devices came from. So there is no Bitra-family device with these
nodes anywhere in the tree, which is consistent with the plan's own note that no
Bitra-family device has ever had a UEFI port.

### Why a guess here is worse than an omission

A node whose `_HID` no driver claims does not fail. It does not warn, retry or
fall back. Windows enumerates it, finds nothing that matches, and the device is
simply absent from Device Manager — no entry, no yellow mark, nothing to read.
The hardware behind it is not there, and the only evidence is its absence, which
looks exactly like hardware that was never described.

That is strictly worse than leaving the node out. An absent node is visibly
absent and the plan can name it. A node with a plausible wrong name closes the
question and produces no symptom. This file already carries the same rule for a
smaller case — the three USB PHY wake GSIs are omitted because
`512 + pin` and `480 + pin` are both consistent with the evidence and putting in
a wrong number would cost more than leaving it out — and this is that rule
applied to two orders of magnitude more surface.

So the DSDT gains nothing this step, and the reason is written into its header
where the next person to open it will read it.

### The name is a choice, which is what makes this workable

The reading above is easy to take one step too far, and the step is worth naming
because it decides how the work gets done. Four names for one block is not
evidence that this board has a fifth name somewhere that has not been found.
**ACPI's `_HID` is not a hardware fact.** It is a string a platform declares, and
the only thing that constrains the declaration is that some driver claims it: a
device binds to the name its `.inf` lists and to nothing else. Whatever internal
numbering Qualcomm's own firmware teams worked to — and the shared two-hex prefix
per SoC suggests there was one — it does not bind anything at runtime.

Two consequences follow, and both are constructive.

The first is that the work is possible, and only the *order* was wrong. Since the
name is declared rather than discovered, there is no lookup to fail; there is a
choice to make, and the constraint on it is a driver set we can obtain. So the
DSDT is written **to a driver, not to the SoC**: adopt a set, and every block
takes the name that set answers to.

The second is that this is the same mechanism P5 already names. "Re-bind the WoA
driver INF" for the Adreno GPU, and the touchscreen's "Windows HID miniport,
several exist upstream", are both this: the device is described so that a chosen
driver binds. P3's remaining tables are not a different kind of work from P5's —
they are the first instance of it, one stage earlier.

### What does unblock it

The names are authoritative in exactly one place: **the Windows driver set**,
whose `.inf` files list the `ACPI\...` hardware ids their drivers bind. That is
the reverse of the direction this started in — not "which name does this block
have" but "which name does the driver I am going to use answer to" — and it is
the correct direction, because the driver is the constraint. A block described
with an `_HID` no available driver claims is hardware that cannot be driven, and
the goal is that all of it is.

The consequence for sequencing: **obtain the driver set before authoring these
tables.** Writing them first is not merely wasted — it produces a DSDT whose
every added node is a silent failure, and silent failures are the expensive kind.
`tools/acpi-hid-census.py --drivers DIR` takes such a set and reports which of
gauguin's twelve blocks it covers, so the moment one is in hand the answer to
"can I write this node, and with what name" is one command.

### The bug in the first draft of the instrument, kept because it agreed with me

The census's first run reported **zero** references for every block, including
the TLMM window the ad-hoc probe had already found seven of. That reads like a
strong confirmation — a negative result is easy to believe when it matches the
argument you were about to make — and it was the parser.

`Memory32Fixed (ReadWrite,` puts its base address on the *next* line in
disassembled output, so the reader carried a "the address is on the next line"
sentinel. The sentinel was `True`, and `isinstance(True, int)` is true in Python:
every resource matched the integer branch, recorded base address `1`, and cleared
the sentinel before the real address arrived. The tool then faithfully reported
that no reference DSDT describes any of gauguin's blocks.

The fix is a string sentinel and an explicit `not isinstance(pending, bool)`.
Both are in the shipped tool, with the reason beside them. The finding above
survived the fix unchanged, which is the only reason it is worth anything — the
first version of the measurement agreed with the conclusion for a reason that had
nothing to do with the conclusion.

| | |
|---|---|
| finds | the DSDT nodes P3 still asks for — I2C, SPI, GPIO, buttons, thermal — cannot be authored from the reference corpus, because `_HID` is a *declared* string and no two SoCs declare the same one: the TLMM window carries 4 distinct names across 7 references and no SM7225-family device declares any of them. The name is free to choose, so the block is a driver set, not a missing fact — **counts corrected by Step 4.58: 5 distinct names across 11 references** |
| evidence | `tools/acpi-hid-census.py` over 36 reference DSDTs: the TLMM window carries 4 distinct `_HID`s, SE0 4, SE1 3, SE6 3, SE7 3, SE0-UART 2, SE3 2, SE5 2, SE2 0, SPMI 0, TSENS0 0, TSENS1 0. `Platforms/Realme/bitra/DSDT.aml` (SM7225) has exactly gauguin's five devices and no more; Moorea and Rennell ship no DSDT — **66 tables and TLMM 5; the SPMI 0 above is the instrument's address join failing, not the corpus, see Step 4.58** |
| second method | the same negative was confirmed by plain text search over the disassembled corpus: `0x0F100000` in 7 files, `0x0C440000` in **0**, `0x0C263000` in **0**, `0x0C265000` in **0** |
| third method, and why it was wrong | the text search above is a *third* usage of the same bad test, so it is not independent evidence: all 22 reference `SPMI` nodes contain `0x0C400000`, and gauguin's arbiter is 16 KB higher, inside the region they declare. The corpus does not lack SPMI; the test could not see a block named by containment. Only `0x0C263000` and `0x0C265000` survive as true negatives, and for a stronger reason than the search gives — Step 4.58 |
| why it matters | a device node whose `_HID` no driver claims is absent from Device Manager with no error, no warning and no yellow mark — strictly worse than an omitted node, which is at least visibly missing. The file's own precedent (the three USB PHY wake GSIs) is the same rule at smaller scale |
| unblocker | the Windows driver set's `.inf` files, which name the `ACPI\...` ids the drivers bind. `--drivers DIR` reports which of gauguin's twelve blocks a set covers. **Obtain the set before authoring these tables** — written first, every added node is a silent failure |
| instrument bug | the census's first draft used `True` as a "address is on the next line" sentinel; `isinstance(True, int)` is true, so every window recorded base 1 and the tool reported zero references for all twelve blocks. Fixed with a string sentinel; the finding survived unchanged |
| compiled | `iasl` still reports 0 errors on the edited `gauguin.asl` and the AML is still 1,520 bytes — the step adds a comment to its header and no node, deliberately |
| does not close | the P2 gate, which is unchanged and still needs the glass, and P3 item 1's remainder, which now has a named precondition instead of an open item |

## Step 4.58 — the id is two facts in one string, and the corpus carries the index half

Host-side, and it began as a check on Step 4.57's numbers rather than as new
work. 4.57's conclusion stands unchanged: these nodes cannot be read off the
reference corpus, so the DSDT has to be written to a driver set. Three of the
readings under it were wrong, all three in the same direction — toward "the
corpus has nothing" — and correcting them turns *a name for each of twelve
blocks is missing* into *one byte is missing, and ten of the twelve are then
named mechanically*.

### The corpus was 36 tables; it is 66

The census's first line read `Platforms/*/*/DSDT.aml` and `Silicon/Qualcomm/*/DSDT.aml`.
Qualcomm's reference tree also ships `DSDT_MTP`, `DSDT_QRD`, `DSDT_IDP` and
`SSDT*.aml`, and the variants are where the *complete* device list lives — a
platform's plain `DSDT.aml` is the trimmed one. Lahaina is the clearest case and
the one that mattered: it ships `DSDT_MTP` with 152 `Device` nodes and
`DSDT_Minimal` with 8, and **no plain `DSDT.aml` at all**. The old glob therefore
read none of Lahaina's device list in any form, and Lahaina is the platform whose
`GIO0` sits on gauguin's TLMM window *and* length.

Corrected counts, and the row that changes meaning:

| block | window | Step 4.57 | measured |
|---|---|---|---|
| TLMM | `0x0F100000` | 7 references, 4 names | **11 references, 5 names** |
| SPMI | `0x0C440000` | **0** | **22 references, 9 names** — by containment, below |

The fifth TLMM name is `QCOM0C0C`, and four of the new references are variants:
`Qualcomm/Lahaina/DSDT_MTP`, `Qualcomm/Cedros/DSDT_IDP`, and Kailua's `DSDT_MTP`
and `DSDT_QRD`. Counted by device name instead of by address, `GIO0` appears in
21 of the 66 tables and `SPMI` in 22.

### The second defect is the one worth remembering: an address was tested for equality

`devices_in` recorded each `Memory32Fixed` base and the census looked for
gauguin's addresses in that set. SPMI's node in every reference table reads:

```
        Device (SPMI)
        {
            Name (_HID, "QCOM1A0B")
            ...
                    Memory32Fixed (ReadWrite,
                        0x0C400000,         // Address Base
                        0x02800000,         // Address Length
```

Forty megabytes. gauguin's arbiter is at `0x0C440000`, sixteen kilobytes higher,
**inside the region every one of those tables declares**. The 4.57 line "SPMI
[`0x0C440000`] — **0**" was not a fact about the corpus; it was the wrong test.
`_CRS` declares a region, so containment is the test that matches what the
fields mean, and the tool now prints both: the census reports SPMI's 22
containing references grouped by the window they claim, above the line that says
no device sits at the exact address.

The same bad test was used twice more in 4.57 and the doc, which is why the
correction is worth writing down rather than quietly applying: the "second
method" row confirmed the negative "by plain text search over the disassembled
corpus", and a text search for `0x0C440000` is the same equality test in a
different language. A negative result reproduced by three usages of one broken
test is one result, not three.

What survives is `TSENS0` and `TSENS1`. Neither window appears in any of the 66
tables, and no device named `TSEN`, `TSSC` or anything else thermal is present
either. That negative is real, and the thermal section below says what the
corpus has instead.

### Joining on the name instead: the id is two facts

The key that does carry across SoCs is the device *name* — every reference table
calls the GPIO controller `GIO0` and the arbiter `SPMI` wherever their windows
are. `--functions` joins on that, and the result is a decomposition:

| device | 02 | 05 | 08 | 09 | 0A | 0C | 14 | 1A | 25 | 60 |
|---|---|---|---|---|---|---|---|---|---|---|
| `SPMI` | 16 | 0C | 0C | 0B | 0B | 0B | 0C | 0B | 0B | — |
| `GIO0` | 17 | 0D | 0D | 0C | 0C | 0C | 0D | 0C | 0C | 16 |
| `MMU0` | 12 | 09 | 09 | 09 | 09 | 09 | 09 | 09 | 09 | — |
| `QDSS` | 8C | 5A | 5A | 56 | 56 | 56 | 5A | 56 | 56 | — |
| `RFS0` | 35 | 17 | 17 | 15 | 15 | 15 | 17 | 15 | 15 | — |
| `GPU0` | 7E | 3A | 3A | 36 | 36 | 36 | 3A | 36 | 36 | — |

A Qualcomm scoped `_HID` is `QCOM` + two hex pairs and the pairs are not
independent. **The low pair is a block index that a whole generation of the table
generator shares**: the families {09, 0A, 0C, 1A, 25} put the arbiter at 0B, GPIO
at 0C, MMU at 09, QDSS at 56 and RFS at 15, and the older group {05, 08, 14} uses
0C, 0D, 09, 5A, 17 for the same five devices. SDM850's 02 is a third group of its
own. 4.57 read {02, 05, 08, 14} as one generation, which put the arbiter at the
wrong index for one of its four families.

`MMU0` is the exception that fixes what the axis is. It stayed at 09 — the same
value the modern group uses — across 05 08 09 0A 0C 14 1A 25 and moved only for
02. So the grouping is not a property of the silicon; it is a property of the
generator that emitted the table, which happens to correlate with it.

**And the high pair is not the SoC either.** Across the eleven tables where a
`GIO0` and an OEM table id can both be read, the pairs are SDM850 02, SDM8150
05, SDM8250 05, SDM8250 25, SDM7180 08, SDM7350 09, SDM7280 0A, SDM8550 0C,
SDM7150 14, SDM8350 1A, SDM636 60. pipa and alioth both declare `SDM8250` and
carry **05 and 25**. So the number cannot be interpolated from the marketing
name of SM7225 or its neighbours; it is a token.

### What the corpus can and cannot name now

Ten of gauguin's twelve blocks — `TLMM`, `SPMI` and the eight GENI SE blocks —
have a known index in each measured generation. `TSENS0` and `TSENS1` have none,
for the reason above. So the unknown collapses to one byte, and the check on it
is mechanical: a driver set either answers to `QCOM<byte><index>` for all ten
blocks or it does not.

`tools/acpi-hid-census.py --drivers DIR` is that check. It walks a directory of
`.inf` files, collects every `ACPI\...` id they list, and then tries all 256
family bytes against each measured index table — 768 combinations — reporting
which byte and which generation make the set cover all ten blocks, printing the
id each block would take. It was verified against four synthetic sets: a complete
modern match at byte `2C` (10/10, every id as expected), a partial legacy match
(2/10, reported as a partial and not as a family), an unrelated set (no
combination, with the message that this is a set for some other SoC), and an
empty directory (exit 1).

Two things it does not and cannot do:

- **`TSENS0` and `TSENS1`.** No driver set can supply an index for a block the
  corpus has never seen. The DSDT will need these two nodes named some other way,
  and the honest options are to leave them out or to find a vendor table that
  declares them — not to guess.
- **The thermal zones are a second namespace.** 20 of the 66 tables declare
  `ThermalZone` objects, 147 distinct `_HID`s across 415 declarations, and most
  of them reuse the table's own family prefix (`QCOM2558` on alioth, whose blocks
  are 25). Nine do not: `QCOM04C0` through `QCOM04C8`, a consecutive block that
  appears in six tables across four families — 09 (renoir, Cedros), 0A (lisa,
  a52sxq) and 1A (lemonade, Lahaina) — fixed regardless of SoC. It is the only
  portable zone group in the corpus. gauguin's device tree declares **40**
  thermal zones, so the portable nine do not correspond to it one-for-one, and
  mapping them is P3 work this step does not do.

`BTNS` needs none of this: it is a standard `ACPI0011` Generic Buttons Device
with Microsoft's `_DSD` UUID, so it can be authored now, ahead of the driver set.

| | |
|---|---|
| finds | a Qualcomm scoped `_HID` is `QCOM<family byte><block index>`. The index is fixed per *generation of the table generator* — measured: ten of gauguin's twelve blocks have a known index in each of three generations, so naming them collapses to one unknown byte, which a driver set supplies |
| evidence | `tools/acpi-hid-census.py --functions` over 66 tables: `SPMI` 0B/0C/16, `GIO0` 0C/0D/17/16, `QDSS` 56/5A/8C, `RFS0` 15/17/35, `GPU0` 36/3A/7E, `MMU0` 09 in every family but 02. The high pair is not the SoC: pipa and alioth both declare `SDM8250` and carry 05 and 25 |
| the axis | `MMU0` is the discriminator: 09 across 05 08 09 0A 0C 14 1A 25, 12 for 02 — so the grouping is the generator that emitted the table, not the silicon |
| counts corrected | the corpus is 66 tables, not 36 — Lahaina ships `DSDT_MTP` and `DSDT_Minimal` and no plain `DSDT.aml`, so the trim-only glob read none of its device list. TLMM is 11 references / 5 names, not 7/4 |
| test corrected | an address was matched for equality. Every reference `SPMI` declares `0x0C400000` for `0x02800000` — forty megabytes — and gauguin's arbiter at `0x0C440000` is inside it, so 22 tables were reported as 0. The census now reports containment as well; `--functions` joins on the device name, which needs no such repair |
| why it matters | the step's conclusion is unchanged — the DSDT is written to a driver, not to the SoC — but the search for the missing token is now finite and mechanical instead of a lookup that has to succeed |
| verified on | four synthetic `.inf` sets: complete modern match at byte `2C` (10/10), partial legacy (2/10, reported as partial), unrelated set (no match), empty directory (exit 1) |
| still open | `TSENS0`/`TSENS1` have no index in the corpus at all, and the thermal zones are a second namespace with 147 ids of their own; `SE2` has no reference at any address, exact or contained, and is disabled in the device tree anyway |
| unchanged | nothing on the device. P2's gate still needs the glass; P4 is untouched; the stock `boot` and the restore path are as they were |

