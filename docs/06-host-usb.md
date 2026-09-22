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

Measured rate: **two link-downs in 100 seconds**, versus the chipset controller
(`0000:00:14.0`, bus 1) which has never once dropped the mouse attached to it.

The correlating detail: the downstream Thunderbolt ports (`03:02.0` and
`6c:00.0`) have **`LnkCtl: ASPM L0s L1 Enabled`**, while the upstream links
(`00:1c.4`, `02:00.0`) are `ASPM Disabled`. The switch is left in an
aggressive-L1 state on a link whose partner is a flapping Thunderbolt bridge.
The registered policy is already `performance`; the per-link setting is what
disagrees, and `pcie_aspm=off` on the kernel command line is the way to force it
(the sysfs policy file is read-only on this build).

### What this means in practice

**Use a non-Thunderbolt port.** The chipset controller's ports are stable and
should be the only ones used for flashing work. On this machine that means the
USB-A ports, not the Type-C one — the Type-C is wired to the JHL6340.

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
kernel is expected to leave it in.

This is why the required action is physical: **hold Power for 20 seconds** (or
Power + Volume Down for 15) to force a hard reset, then let it boot.

### Corroborating timeline

The phone last presented on `usb 3-1` at 18:54:45 and vanished at 18:55:01,
during the `fastboot boot` of the P1 image. Over the next hour the Thunderbolt
controller re-registered its buses **twelve** times, and port 3-1 never once
re-detected a device. A plugged-in, powered-on phone with a live USB PHY would
have re-enumerated on at least one of those. It did not.

## Missing Android udev rules

This machine had **no** Android udev rules at all, which is a third way a
working connection looks broken: the USB device node is root-only by default and
`adb` runs as the desktop user, so `adb devices` can report `no permissions` or
show nothing even though the kernel enumerated the phone cleanly.

Added as `/etc/udev/rules.d/51-android.rules` (13 vendor IDs, `MODE="0666"`).

## If a device still will not appear

1. **Prefer a non-Thunderbolt port.** On this machine the JHL6340 is the flaky
   path; a port wired directly to the chipset PCH avoids the whole class of
   problem. This is the recommended arrangement for all flashing work.
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
