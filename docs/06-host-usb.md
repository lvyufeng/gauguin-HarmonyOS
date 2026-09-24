# 06 — The host side: a USB bug that eats attached devices

Found the hard way during P1. Worth recording because it silently breaks flashing
and looks exactly like a broken phone.

## Symptom

An attached phone enumerates, works for a few seconds to a minute, then vanishes
from `adb` and `fastboot` and does not come back. `fastboot` operations fail with
`Write to device failed (Protocol error)`, or `fastboot boot` works once after a
`reboot-bootloader` and then reports `Failed to load/authenticate boot image:
Load Error` — **including for the stock, known-good boot image**, which is the
tell that the problem is not the image.

## Cause

The phone was plugged into a **Thunderbolt 3 port backed by an Intel JHL6340
(Alpine Ridge 2C 2016) USB controller** (`8086:15db`), not the chipset's own xHCI.
That controller had runtime power management enabled:

```
$ cat /sys/bus/pci/devices/0000:6c:00.0/power/control
auto
$ cat /sys/bus/pci/devices/0000:6c:00.0/power/runtime_status
suspended
$ cat /sys/bus/pci/devices/0000:6c:00.0/d3cold_allowed
1
```

Being in `auto` with `d3cold_allowed=1` lets the controller drop to D3cold when
idle, which powers down the port. The kernel log shows it resetting roughly once
a minute, each reset dropping whatever is attached:

```
xhci_hcd 0000:6c:00.0: Host not accessible, reset failed.
xhci_hcd 0000:6c:00.0: USB bus 3 deregistered
xhci_hcd 0000:6c:00.0: xHCI Host Controller
xhci_hcd 0000:6c:00.0: new USB bus registered, assigned bus number 3
```

A device that enumerates right before a reset survives only momentarily:

```
18:54:45 usb 3-1: New USB device found, idVendor=18d1, idProduct=d00d   <- phone
18:54:45 usb 3-1: SerialNumber: d25f844e
18:55:01 usb 3-1: USB disconnect, device number 2                        <- 16s later
```

## Fix

Pin the controller out of runtime suspend:

```sh
D=/sys/bus/pci/devices/0000:6c:00.0
echo on | sudo tee $D/power/control
echo 0  | sudo tee $D/d3cold_allowed
```

Verified: `Host not accessible, reset failed` stopped entirely (0 resets in 90 s,
versus roughly one a minute before).

Made persistent by `/etc/udev/rules.d/99-usb-no-runtime-pm.rules`:

```
ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x8086", ATTR{device}=="0x15db", \
    ATTR{power/control}="on", ATTR{d3cold_allowed}="0"
```

## There is a second, deeper cause — found later

The runtime-PM fix above **was not sufficient**. A later session with the device
disconnected showed the controller still flapping, and this time there was no
device on it to blame:

```
19:52:31 xhci_hcd 0000:6c:00.0: xHCI host controller not responding
19:52:31 xhci_hcd 0000:6c:00.0: USB bus 4 deregistered
19:52:38 usb usb3: New USB device found, idVendor=1d6b, idProduct=0002   <- root hub, back again
19:52:42 xhci_hcd 0000:6c:00.0: xHCI host controller not responding      <- 4s later
19:52:42 xhci_hcd 0000:6c:00.0: USB bus 3 deregistered
```

with `power/control=on`, `d3cold_allowed=0` and `usbcore.autosuspend=-1` **all
already in effect**. So runtime PM is not the whole story. What the log shows is
the *PCIe link itself* going down, one layer below USB:

```
pcieport 0000:00:1c.4: pciehp: Slot(8): Link Down
pcieport 0000:00:1c.4: pciehp: Slot(8): Card not present
pcieport 0000:00:1c.4: pciehp: Slot(8): Card present
pcieport 0000:00:1c.4: pciehp: Slot(8): Link Up
```

Measured rate at the time: **two link-downs in 100 seconds**, versus the chipset
controller (`0000:00:14.0`, bus 1) which has never once dropped the mouse attached
to it. (Re-measured later and much worse — see below.)

The correlating detail: the downstream Thunderbolt ports (`03:02.0` and
`6c:00.0`) have **`LnkCtl: ASPM L0s L1 Enabled`**, while the upstream links
(`00:1c.4`, `02:00.0`) are `ASPM Disabled`. The switch is left in an
aggressive-L1 state on a link whose partner is a flapping Thunderbolt bridge.
The registered policy is already `performance`; the per-link setting is what
disagrees, and `pcie_aspm=off` on the kernel command line was the proposed way to
force it (the sysfs policy file is read-only on this build).

**That proposal has since been tested and refuted — see "ASPM is cleared on every
link, and the flap does not change" below.** The reading of the registers is
still correct; the inference from it to the cause is withdrawn, and
`pcie_aspm=off` is not worth a reboot.

### What this means in practice

**Use a non-Thunderbolt port.** The chipset controller's ports are stable and
should be the only ones used for flashing work. On this machine that means the
USB-A ports, not the Type-C one — the Type-C is wired to the JHL6340.

## What the port measures now, and why the event watch could not see it

Re-measured on 2026-09-24, machine up 1 day 12 hours, with nothing attached to the
Thunderbolt controller:

| reading | value |
|---|---|
| `pciehp: Slot(8): Link Down` | **9–10 per minute — one every 6.5 s** |
| sustained for | the whole sampled hour: 61 consecutive 5-minute buckets, every one of them 9 or 10 |
| `0000:6c:00.0` ports reading `Connected` | **0 of 4** |
| `0000:00:14.0` ports reading `Connected` | **1 of 18** — the mouse, `Link:U0` |

Two things follow.

**The rate is about seven times worse than the figure recorded above, and it is
steady rather than intermittent.** That matters because "roughly once a minute" is
what made this port look usable for a transfer: `tools/flash-boot.sh` argues for the
TWRP route partly on the grounds that a link drop costs one `adb push` and nothing
else. At one drop per 6.5 seconds, with an up-window of about four seconds, no
transfer of any size completes — the argument does not hold for that port any more.

**The failure produces no USB device event, so `tools/watch-usb.sh` could not see
it.** The script watched for `New USB device found` and `USB disconnect`; a PCIe link
down is one layer below that and deregisters the whole bus without any attached
device ever being named. Watching it while swapping cables printed nothing at all,
which reads as "no signal" rather than "the port is being torn down under the cable".
The script now opens with the port registers and a five-minute flap count for exactly
this reason, and it labels the root hub's own return as `ROOT HUB BACK` instead of
`ENUMERATED`: that line is the failing controller announcing itself, and it read
exactly like a phone arriving.

One measurement note, because it cost time: a one-minute `dmesg | grep -c` sample
read `1` while `journalctl -k` counted nine in the same minute. The kernel ring buffer
drops messages. Count flaps with `journalctl -k`, not `dmesg`.

## Re-measured on 2026-09-25: the flap has no exempt hour, and it is not ASPM

Two things were still open after the reading above — whether the 6.5-second rate
holds over a long window or was a bad hour, and whether the ASPM observation is the
cause or a coincidence. Both were settled by measurement, and the second one came
out negative.

**Every hour in a 26-hour window flaps.** `pcieport 0000:00:1c.4: pciehp: Slot(8):
Link Down`, counted per clock hour from Sep 24 00:00 to Sep 25 01:32:

| hour (Sep 24) | 00–10 | 11 | 12 | 13 | 14 | 15 | 16–23 | Sep 25 00 | 01 |
|---|---|---|---|---|---|---|---|---|---|
| drops | 551–553 each | 487 | 542 | 507 | 536 | 367 | 562–565 each | 563 | 332 (partial) |

So the floor over 26 hours is 367 and the ceiling 565, with twenty of the
twenty-six hours inside 551–565 — one drop every **6.4 to 6.5 seconds**, sustained.
Nothing is exempt: the hours around 11:00–15:00 are the only ones off the plateau,
and they are *lower*, not higher, so whatever varies there does not gate the flap.
For scale, the kernel journal for the current boot holds **1,955,642** messages and
essentially all of them belong to this loop; that is why `dmesg` shows only about
three minutes of history, and why a flap that started hours ago can look new.

**ASPM is cleared on every link, and the flap does not change.** The registers were
cleared at runtime on all three links that had it enabled —
`sudo setpci -s <dev> CAP_EXP+10.w=0000` for `0000:03:01.0`, `0000:03:02.0` and
`0000:6c:00.0` — and re-read to confirm, `ASPM Disabled` on each (the two upstream
links, `00:1c.4` and `02:00.0`, were already disabled, so the whole chain now has
none). The rate before the change was 9 drops in 60 s; after it, **15 drops in
90 s**, i.e. 10.0 per minute against the 9.2 per minute the old rate predicts. The
flap is unchanged. ASPM is refuted as the cause, and with it `pcie_aspm=off` as the
cure — which is worth knowing without having spent a reboot on it.

Two corrections to the section above, both from the same session.
`/sys/module/pcie_aspm/parameters/policy` now reads
`[default] performance powersave powersupersave`, so the active policy is `default`
and not the `performance` that paragraph claims. And clearing the bits with
`setpci` is a runtime change only: it does not survive a reboot, and it costs a
little idle power on a link that is unusable either way.

**Every time this host has ever seen the phone, it was on `usb 3-1`.** The journal
keeps every boot back to Sep 21. Grepping it for the phone's vendor IDs over its
whole history gives **32 enumeration events, on one bus: `usb 3-1`**, first
`2026-09-22T14:49:23` (`2717:ff18`), last **`2026-09-24T14:59:32`** (`2717:ff68`).
Not one appears on `usb 1-x` or `usb 2-x`, which is the chipset controller
(`0000:00:14.0`) — the one that has never dropped the mouse.

That is the whole of this project's connection history, and it means the advice to
fall back to a chipset port has never actually been followed: there is no reading
anywhere in it from a chipset port, for good or for ill. So "the USB-A port did not
work either" is not evidence against a chipset port, because the log says the phone
was on `usb 3-1` for all 32 events and the chipset ports have not been tried.

**And the phone has presented nothing at all since that last event.** A 30-second
live watch on 2026-09-25 01:32, with the dock cycling four or five times inside the
window, produced **zero** `New USB device found` lines for it, and `lsusb -t` shows
nothing attached to bus 3 or bus 4. Ten and a half hours, no enumeration on any
port. Read against "The same reading has a second, non-hardware cause" below, that
is the expected state of a phone that is off, or that is sitting in the boot loop
of our own payload — which brings up no USB device stack at all, so it presents
nothing on any port for as long as the loop keeps resetting it. Nothing in the host
side of this document explains a total absence; a flapping link explains a device
that arrives and leaves, not one that never arrives.

## `usb 3-1` is the dock, and the chipset controller has had six kernel events in three days

The two paragraphs above say the phone has only ever been seen on `usb 3-1` and
that `usb 3-1` is not a chipset port. They do not say *whose* port it is, and that
gap has a cost: the standing instruction in this project is "plug into a chipset
USB-A port", and on a desk with a dock on it, "the USB-A port" reads as the one on
the dock. So the chain was resolved rather than assumed.

```
/sys/bus/usb/devices/usb3
  -> ../../../devices/pci0000:00/0000:00:1c.4/0000:02:00.0/0000:03:02.0/0000:6c:00.0/usb3
```

| | device | driver |
|---|---|---|
| `0000:00:1c.4` | Intel Cannon Point-LP PCI Express Root Port #5  (`8086:9dbc`) | `pcieport` |
| `0000:02:00.0` | Intel JHL6340 Thunderbolt 3 Bridge, Alpine Ridge 2C 2016  (`8086:15da`) | `pcieport` |
| `0000:03:02.0` | the same bridge's downstream port  (`8086:15da`) | `pcieport` |
| `0000:6c:00.0` | Intel JHL6340 Thunderbolt 3 USB 3.1 Controller  (`8086:15db`) | `xhci_hcd` |

**`usb 3-1` is the USB port on the Thunderbolt dock.** Every one of the 32
enumerations in this project's history is on the dock's controller — the same
silicon the `## Cause` section at the top of this document identifies as the
fault, still powered on and still cycling.

**The failure is not a link blip; it is the bus ceasing to exist.** Each
`Link Down` on `00:1c.4` runs the whole teardown, so a device attached to the dock
is not "dropped from an idle link" — its controller is removed and re-probed. In
the current boot's kernel log (2,038,841 lines, sudo needed to read it):

| event | count |
|---|---|
| `0000:00:1c.4: pciehp: Slot(8): Link Down` | 16,035 |
| `0000:00:1c.4: pciehp: Slot(8): Link Up` | 16,035 |
| `xhci_hcd 0000:6c:00.0: USB bus 3 deregistered` | **16,037** |
| `xhci_hcd 0000:6c:00.0: USB bus 4 deregistered` | **16,037** |
| `xhci_hcd 0000:6c:00.0: xHCI Host Controller` (new) | 32,076 |
| `xhci_hcd 0000:6c:00.0: remove, state` | 32,074 |
| `xhci_hcd 0000:6c:00.0:` — every line | **160,424** |
| `xhci_hcd 0000:00:14.0:` — every line | **6** |

The chipset controller's six lines are all from `Sep 22 13:41:36`: two
`xHCI Host Controller`, two `new USB bus registered` (buses 1 and 2), one
`hcc params`, and `Host supports USB 3.1 Enhanced SuperSpeed`. **It has not been
touched since** — no reset, no removal, in two and a half days — and the mouse
plugged into it (`1-1`, `12d1:10d8`) has been up for the whole of it. The dock
controller's 160,424 lines are the same interval.

A live watch made the shape of it visible directly, 2026-09-25 02:35:10 to
02:36:53 — **103 seconds, 16 deregistrations of each bus**, i.e. one every 6.4 s,
which is exactly the `Link Down` rate the section above measured over 26 hours:

```
02:35:16  DISCONNECTED
02:35:16  CONTROLLER  xhci_hcd 0000:6c:00.0: USB bus 4 deregistered
02:35:16  DISCONNECTED
02:35:16  CONTROLLER  xhci_hcd 0000:6c:00.0: USB bus 3 deregistered
02:35:18  ROOT HUB BACK  1d6b:0002   <- the host's own hub, not a device
02:35:18  ROOT HUB BACK  1d6b:0003
```

A device on `usb 3-1` therefore has a **6.4-second window in which to enumerate,
and then its bus is destroyed and rebuilt underneath it**. That is the mechanism
behind the `## Symptom` at the top — "works for a few seconds to a minute, then
vanishes" — stated as an interval rather than as a tendency, and it is why
`fastboot boot` can succeed once and then fail on the stock image.

**So the port to use has a name.** `0000:00:14.0`, the Cannon Point-LP chipset
xHCI: bus 1 (USB 2.0, 12 ports) and bus 2 (USB 3.1, 6 ports), 18 ports of which
one is the mouse. Never `3-x` or `4-x`, which are the dock's, no matter which
physical socket on the dock the cable goes into.

That also re-reads one thing the user reported. "我换了usb-A，但是没有反应" —
switched to the USB-A, no response — is not evidence about a chipset port: the
log has no enumeration from `usb 1-x` or `usb 2-x` at any point in its history, so
if the cable was moved to a USB-A socket it was still on the dock's controller.
Both readings are the same reading, and neither of them is about the laptop's own
ports.

## The same reading has a second, non-hardware cause

`Not-connected Link:RxDetect` on every port means nothing is pulling up D+. There are
two ways to arrive there, and on this project the second is the one that applies:

- the phone is off, the cable is out, or its port is damaged;
- **the phone is running firmware with no USB device stack in it.** A phone stuck in
  the boot loop of our own UEFI payload — which brings up no USB at all — presents
  nothing on any port, for as long as the loop keeps resetting it. That is precisely
  the cost recorded in `docs/08` step 5 as the deliberate override: leaving our image
  in `boot` keeps the phone in the loop, and the loop has no USB.

So on this phone "not connected" is not evidence of a hardware fault, and not evidence
about the cable either. It is the expected state of a device booting something with no
gadget driver in it. Getting it back on the bus means getting it *out* of the loop
first: hold Power for 20 seconds to force a hard reset, bring it up to recovery, and
plug into a chipset USB-A port.

## Distinguishing "phone is off" from "host ate the phone"

These look identical from `adb devices`. The kernel log separates them, and the
distinction matters because only one of them is fixable in software:

- **No `New USB device found` line at all** — the phone never reached the host.
  Cable, port, or the phone is off. No driver work will fix this.
- **`New USB device found` followed by a disconnect** — the host enumerated it
  and dropped it. That is the controller problem above.

`tools/watch-usb.sh` prints this live, labels each event, and names recognised
vendors, so the two cases are distinguishable while the cable is still in hand.

### The definitive test: xHCI port registers

The kernel exposes the raw port state, which settles the question without
guessing:

```sh
sudo ls /sys/kernel/debug/usb/xhci/0000:6c:00.0/ports/
sudo head -1 /sys/kernel/debug/usb/xhci/0000:6c:00.0/ports/port01/portsc
```

`Powered Not-connected Disabled Link:RxDetect` means the host is looking for a
receiver and finding none — **nothing is electrically presenting itself on that
port**. `Connected` means there is a device there, whatever adb thinks.

Checked across all 22 ports of both controllers (chipset 18, Thunderbolt 4):
the mouse's port reads `Connected Enabled Link:U0`, every other port reads
`Not-connected Link:RxDetect`.

That is the answer to "the phone is plugged in but nothing sees it": **a phone
whose USB device controller is not pulling up D+ is indistinguishable from an
empty port.** A powered-off phone does that. So does a phone sitting in a hung
kernel that has reset the DWC3 controller without binding a gadget driver —
which is exactly the state the last `fastboot boot` of an unsupported mainline
kernel is expected to leave it in. And so does a phone booting a payload that
brings up no USB at all, which is the state in force during P2 — see "The same
reading has a second, non-hardware cause" above.

This is why the required action is physical: **hold Power for 20 seconds** (or
Power + Volume Down for 15) to force a hard reset, then bring it up to recovery.

### Corroborating timeline

The phone last presented on `usb 3-1` at 18:54:45 and vanished at 18:55:01,
during the `fastboot boot` of the P1 image. Over the next hour the Thunderbolt
controller re-registered its buses **twelve** times, and port 3-1 never once
re-detected a device. A plugged-in, powered-on phone with a live USB PHY would
have re-enumerated on at least one of those. It did not.

Note which bus that is: `3-1` is on `0000:6c:00.0`, so **the phone was on the
Thunderbolt path**, which is the one controller on this machine that will not hold
a link. That is the arrangement to stop using.

## Missing Android udev rules

This machine had **no** Android udev rules at all, which is a third way a
working connection looks broken: the USB device node is root-only by default and
`adb` runs as the desktop user, so `adb devices` can report `no permissions` or
show nothing even though the kernel enumerated the phone cleanly.

Added as `/etc/udev/rules.d/51-android.rules` (13 vendor IDs, `MODE="0666"`).

## If a device still will not appear

1. **Prefer a non-Thunderbolt port.** On this machine the JHL6340 is the flaky
   path; a port wired directly to the chipset PCH avoids the whole class of
   problem. This is the recommended arrangement for all flashing work. Run
   `tools/watch-usb.sh` first — it reads the port registers and counts the PCIe
   link-downs, so it says whether anything is electrically present *and* whether
   the port it is present on can hold a link, before any cable gets swapped.
   **Be exact about which socket that is**: the chipset controller is
   `0000:00:14.0` and its ports are `usb 1-x` and `usb 2-x`; `usb 3-x` and
   `usb 4-x` belong to the dock's JHL6340 and flapping, and a "USB-A port" on the
   dock is still `3-x`. One check settles it before flashing:

   ```sh
   lsusb -t | grep -A2 'Bus 001\|Bus 002'   # bus 1/2 = chipset; 3/4 = dock
   ```
2. Check the controller did not re-enter suspend:
   `cat /sys/bus/pci/devices/0000:6c:00.0/power/runtime_status`
3. Re-scan the bus without unplugging: `echo 1 | sudo tee /sys/bus/pci/rescan`
4. Restart the adb server: `adb kill-server && adb start-server`
5. Check the host actually saw it at all — if there is no `New USB device found,
   idVendor=…` line for it in the log, the problem is physical (cable, port, or
   the device is off), not software:

   ```sh
   sudo journalctl --since "30 min ago" | grep -E 'usb [0-9]-[0-9]'
   ```

The last point is the important diagnostic split: **no enumeration event in the
kernel log means nothing reached the host**, and no amount of driver work will
fix a cable.
