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
ls -la work/out/boot.img                              # P1's mainline kernel
ls -la work/out/p2-variants/Mu-gauguin-stock-*.img    # the two P2 variants
tools/restore-stock-boot.sh --check                   # must print "ok"
```

If `--check` fails, stop. Every write in this runbook depends on that file being
the byte-identical stock image.

---

## Step 1 — Reset and power on cleanly

Hold **Power** ~20 s until the phone restarts. Then, from off, press **Power
once** and nothing else.

No volume keys. This matters: ABL's fastboot-reason code 0 (`Down Key Press`) is
written when the power-on reason is 2 or 8, so a reset performed with the power
button can produce that answer regardless of what the firmware did. Powering on
with no keys held is what makes the answer trustworthy.

Watch the screen. Note in one line what appears and whether it ever changes.

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

**4a. P1's mainline kernel** (`work/out/boot.img`, stock-shaped v2, gzip kernel,
real ramdisk). Worth trying before the UEFI builds for two reasons: it is a
much better-understood payload, and **its earlier failure was on `fastboot boot`
— the RAM chain-load path — which is not the same code path as booting from the
`boot` partition.** So its failure does not predict anything about this test,
and if it boots we have learned that the phone boots our images and that P2's
problem is specific to the UEFI build.

```sh
fastboot flash boot work/out/boot.img
```

(or, from TWRP, `adb push` + `dd`, which is what `restore-stock-boot.sh --twrp`
does and which does not depend on ABL's fastboot answering at all — use that if
the fastboot route has just stranded itself.)

**4b. `Mu-gauguin-stock-gzip.img`** — stock-shaped, compressed kernel, dummy
ramdisk. Same as the image already on the phone except the header is this
device's own shape.

**4c. `Mu-gauguin-stock-none.img`** — the same but uncompressed. This is the
pair that separates the two surviving candidates: the image that boots today has
a **raw** kernel, and everything we have produced has been compressed.

After each: if `oem fbreason` changes to `LoadImageAndAuth Fail`, the payload is
being reached and the difference between this attempt and the last one is the
variable that matters.

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
attempt  image                          fbreason                     screen
-------  -----------------------------  ---------------------------  ------
1        (as found)                     LoadImageAndAuth Fail        Redmi logo, then fastboot
2        work/out/boot.img              ...
```

That table is the entire output of a session. Everything else is in the files
`fastboot-capture.sh` wrote.
