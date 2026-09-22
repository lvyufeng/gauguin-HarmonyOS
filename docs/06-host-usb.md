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
