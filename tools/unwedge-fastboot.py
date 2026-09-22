#!/usr/bin/env python3
"""Tell apart the two ways an ABL fastboot stops answering, without the phone.

Why this exists: `fastboot devices` keeps printing a serial number while ABL is
wedged, because the USB descriptor layer outlives the fastboot application - so
the one cheap check available in a session says "fine" about a device that will
ignore every command for the next half hour of someone's time. What the runbook
needs at that moment is not "it is wedged" but *which* wedge, because one of
them is recoverable from the host and the other is not.

There are three states, and `fastboot` looks the same in all of them - so the
runbook needs this tool rather than another `getvar`, because one is recoverable
from the host and the others are not:

  * the truncation wedge. A client that closed mid-response (the recorded cause:
    `fastboot getvar all | head -45`) leaves ABL's fastboot thread parked inside
    a bulk IN write that nobody will finish reading. The thread is alive and
    blocked on the host. **The IN endpoint still holds the rest of its reply**,
    so reading it out releases the thread. Recoverable.

  * the download wedge. The other recorded cause: a `fastboot boot` of a 128 MB
    image whose transfer stalled, killing the client. ABL is parked inside a bulk
    OUT read waiting for data that will never come, and the **OUT endpoint
    accepts** whatever is written to it. Also recoverable in principle.

  * a stuck fastboot thread. ABL's USB handling is interrupt-driven, so a thread
    that has died or gone into a loop elsewhere still answers control requests on
    EP0 - descriptors, status, SET_CONFIGURATION - while the bulk endpoints,
    which the fastboot loop itself has to arm, are silent in *both* directions:
    nothing to read on IN, and NAKs on OUT because no receive is queued. EP0
    answering while neither of the above holds is a device whose USB stack has
    outlived its application. **Not** recoverable - power button.

So the measurement is a pair. What the drain returns says whether a reply was
left unread; what the OUT write does says whether the thread is parked in a read.
Both empty means neither, and that is the answer the runbook needs before asking
someone to walk over and hold the power button.

It is also written to be safe to run against a device that *is* answering: it
never truncates a response (the whole point), never writes to storage, and its
only state-changing operation is a standard SET_CONFIGURATION, which is
non-destructive by definition.

Usage:  tools/unwedge-fastboot.py [--log FILE] [--wait N]
"""
import argparse
import ctypes
import ctypes.util
import os
import sys
import time

VENDOR, PRODUCT = 0x18D1, 0xD00D
EP_IN, EP_OUT = 0x81, 0x01
DRAIN_TIMEOUT = 2000     # ms per read, never 0: see drain() below


class Desc(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("bLength", ctypes.c_uint8), ("bDescriptorType", ctypes.c_uint8),
        ("bcdUSB", ctypes.c_uint16), ("bDeviceClass", ctypes.c_uint8),
        ("bDeviceSubClass", ctypes.c_uint8), ("bDeviceProtocol", ctypes.c_uint8),
        ("bMaxPacketSize0", ctypes.c_uint8), ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16), ("bcdDevice", ctypes.c_uint16),
        ("iManufacturer", ctypes.c_uint8), ("iProduct", ctypes.c_uint8),
        ("iSerialNumber", ctypes.c_uint8), ("bNumConfigurations", ctypes.c_uint8)]


def load():
    path = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0"
    lib = ctypes.CDLL(path)
    lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.libusb_get_device_list.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))]
    lib.libusb_get_device_descriptor.argtypes = [ctypes.c_void_p,
                                                 ctypes.POINTER(Desc)]
    lib.libusb_open.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.libusb_bulk_transfer.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                         ctypes.POINTER(ctypes.c_ubyte),
                                         ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                         ctypes.c_uint]
    lib.libusb_control_transfer.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                            ctypes.c_uint8, ctypes.c_uint16,
                                            ctypes.c_uint16,
                                            ctypes.POINTER(ctypes.c_ubyte),
                                            ctypes.c_uint16, ctypes.c_uint]
    lib.libusb_clear_halt.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
    lib.libusb_close.argtypes = [ctypes.c_void_p]
    lib.libusb_exit.argtypes = [ctypes.c_void_p]
    return lib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="work/unwedge.log")
    ap.add_argument("--seconds", type=float, default=2.0,
                    help="how long to keep draining the IN endpoint")
    ap.add_argument("--wait", type=float, default=20.0,
                    help="how long to wait for the test command's answer. Long "
                         "on purpose: a slow-but-alive ABL must not be mistaken "
                         "for a dead one, and this client must never kill itself "
                         "mid-response - that truncation is the recorded cause "
                         "of one of the two wedges.")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.log) or ".", exist_ok=True)
    out = open(args.log, "w", buffering=1)

    def say(s):
        print(s)
        out.write(s + "\n")

    lib = load()
    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)) != 0:
        sys.exit("libusb_init failed")

    devs = ctypes.POINTER(ctypes.c_void_p)()
    n = lib.libusb_get_device_list(ctx, ctypes.byref(devs))
    handle = ctypes.c_void_p()
    for i in range(n):
        d = Desc()
        if lib.libusb_get_device_descriptor(devs[i], ctypes.byref(d)) != 0:
            continue
        if (d.idVendor, d.idProduct) != (VENDOR, PRODUCT):
            continue
        rc = lib.libusb_open(devs[i], ctypes.byref(handle))
        say(f"found {d.idVendor:04x}:{d.idProduct:04x}, open rc={rc}")
        break
    else:
        sys.exit("no fastboot device on the bus")

    rc = lib.libusb_claim_interface(handle, 0)
    say(f"claim interface 0 rc={rc}  (0 ok, -6 = busy: something else holds it)")

    # --- EP0: does the firmware run at all? ---------------------------------
    # This is the split. On this class of device ABL's USB stack is
    # interrupt-driven, so these control requests are answered from interrupt
    # context whether or not the fastboot thread is alive - which is exactly why
    # `fastboot devices` is a convincing lie. What they establish is the other
    # half: if they fail, nothing is running and there is no point in anything
    # below.
    cbuf = (ctypes.c_ubyte * 64)()   # >= every wLength here; libusb fills it all
    rc = lib.libusb_control_transfer(handle, 0x80, 6, (1 << 8) | 0, 0, cbuf, 18, 2000)
    say(f"EP0 GET_DESCRIPTOR(device)  rc={rc}"
        + ("  (18 = firmware answers control requests)" if rc == 18
           else "  <- NO ANSWER: nothing is running, power button"))
    if rc != 18:
        say("")
        say("VERDICT: the USB stack is dead as well as the fastboot thread. "
            "Host-side recovery is not possible. docs/08 step 1.")
        lib.libusb_close(handle)
        lib.libusb_exit(ctx)
        out.close()
        return 1

    for name, ep in (("IN  0x81", EP_IN), ("OUT 0x01", EP_OUT)):
        rc = lib.libusb_control_transfer(handle, 0x82, 0, 0, ep, cbuf, 2, 2000)
        st = (cbuf[0] | cbuf[1] << 8) if rc == 2 else None
        say(f"EP0 GET_STATUS({name})  rc={rc} value={st} "
            + ("HALTED" if st == 1 else "not halted" if st == 0 else ""))

    # SET_CONFIGURATION re-initialises the endpoints from the device's side. It
    # is standard, non-destructive, and not among the three host-side resets
    # already recorded as tried. If the fastboot thread is only parked in a
    # stale transfer this is what makes it drop it; if it is stuck, the device
    # accepts this and carries on being stuck, which is itself the answer.
    rc = lib.libusb_control_transfer(handle, 0x00, 9, 1, 0, None, 0, 3000)
    say(f"EP0 SET_CONFIGURATION(1)   rc={rc}  (>=0 = accepted by the firmware)")
    if rc >= 0:
        for ep, label in ((EP_IN, "IN  0x81"), (EP_OUT, "OUT 0x01")):
            say(f"    CLEAR_FEATURE(HALT) {label}  "
                f"rc={lib.libusb_clear_halt(handle, ep)}")
        lib.libusb_claim_interface(handle, 0)

    # --- the drain, which is the recovery for the truncation wedge -----------
    buf = (ctypes.c_ubyte * 16384)()
    got = ctypes.c_int()
    total, deadline = 0, time.time() + args.seconds
    say(f"--- draining IN (0x{EP_IN:02x}) for {args.seconds:.0f}s, "
        f"{DRAIN_TIMEOUT} ms per read")
    while time.time() < deadline:
        got.value = 0
        rc = lib.libusb_bulk_transfer(handle, EP_IN, buf, len(buf),
                                      ctypes.byref(got), DRAIN_TIMEOUT)
        if rc != 0:
            say(f"   bulk_in rc={rc} after {total} bytes "
                f"({'timeout: nothing pending' if rc == -7 else 'error'})")
            break
        if got.value == 0:
            break
        total += got.value
        say(f"   read {got.value} bytes (running total {total})")
        out.write(f"   [{bytes(buf[:got.value])[:400]!r}]\n")
        if total > 8 << 20:
            say("   stopping at 8 MiB - that is not a leftover response")
            break
    if total:
        say(f"--- drained {total} bytes: ABL had a response in flight")

    # --- the transaction, and the measurement that decides it ---------------
    cmd = b"getvar:product"
    rc = lib.libusb_bulk_transfer(handle, EP_OUT,
                                  (ctypes.c_ubyte * len(cmd)).from_buffer_copy(cmd),
                                  len(cmd), ctypes.byref(got), 3000)
    out_ok = rc == 0 and got.value == len(cmd)
    say(f"--- sent {cmd!r} on OUT: rc={rc} wrote {got.value}"
        + ("" if out_ok else "   <- NAKed: no receive is queued, so the thread is "
                             "not parked waiting for download data either"))

    resp, start, deadline = b"", time.time(), time.time() + args.wait
    while time.time() < deadline:
        got.value = 0
        rc = lib.libusb_bulk_transfer(handle, EP_IN, buf, len(buf),
                                      ctypes.byref(got), 2000)
        if rc == -7:
            continue
        if rc != 0:
            say(f"   bulk_in rc={rc} mid-response")
            break
        resp += bytes(buf[:got.value])
        if b"OKAY" in resp or b"FAIL" in resp:
            break
    say(f"--- response after {time.time() - start:.1f}s "
        f"({len(resp)} bytes): {resp!r}")

    alive = resp.startswith(b"OKAY") or b"gauguinpro" in resp
    say("")
    if alive:
        say("VERDICT: ABL answers again. This was the truncation wedge - a "
            "client that stopped reading mid-response - and finishing the read "
            "released it. Nothing about the boot path changed.")
    elif out_ok:
        say("VERDICT: the OUT write was accepted, so the fastboot thread is "
            "parked waiting for download data - the aborted-transfer wedge. The "
            "reply it wants now depends on what it was told to download; do not "
            "guess at it. docs/08 step 1, power button, and restore stock "
            "`boot` before anything else.")
    else:
        say("VERDICT: the fastboot thread is stuck, and it is stuck *outside* "
            "its transport: EP0 answers, SET_CONFIGURATION is accepted, and the "
            "bulk endpoints are not armed in either direction. Nothing was left "
            "unread for the drain to release, and no read is queued for the "
            "write to complete, so it is neither of the two known wedges. No "
            "host-side operation reaches it - docs/08 step 1, hold the power "
            "button ~20s.")

    lib.libusb_close(handle)
    lib.libusb_exit(ctx)
    out.close()
    print(f"\nlog: {args.log}")
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
