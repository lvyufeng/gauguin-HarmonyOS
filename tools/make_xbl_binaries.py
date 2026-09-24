#!/usr/bin/env python3
"""Build Binaries/gauguin/ for a Mu-Silicium gauguin platform from our XBL dump.

Mu-Silicium's platform packages pull Qualcomm's signed DXE drivers out of a
`Binaries/<device>/QcomPkg/Drivers/...` tree, each wrapped in a small `.inf`.
We already have that exact driver set - extracted from this phone's own XBL by
`tools/xbl_extract.py` - so what is missing is only the packaging.

Reference for the package layout: `Binaries/surya/`.

The extracted FFS file names are the drivers' own UI names, which differ from the
directory names Mu-Silicium uses in a handful of cases (TzDxe ships as
`ScmDxeLA.inf` and `TzDxeLA.inf`, `DALTLMM` is `TLMMDxe`, and so on). This maps
them.

Usage:  make_xbl_binaries.py DXE_DIR [--out DIR] [--ref DIR]
"""
import argparse
import os
import re
import shutil
import sys

# Extracted FFS name -> (driver directory, .inf basename, module type)
#
# Derived by matching against Binaries/surya/QcomPkg/Drivers, which is the same
# Qualcomm driver set for a sibling SoC.
DRIVERS = {
    "AdcDxe":                  ("AdcDxe", "AdcDxe.inf", "DXE_DRIVER"),
    "ADSPDxe":                 ("ADSPDxe", "ADSPDxe.inf", "DXE_DRIVER"),
    "ButtonsDxe":              ("ButtonsDxe", "ButtonsDxe.inf", "DXE_DRIVER"),
    "ChargerExDxe":            ("ChargerExDxe", "ChargerExDxe.inf", "DXE_DRIVER"),
    "ChipInfo":                ("ChipInfoDxe", "ChipInfoDxe.inf", "DXE_DRIVER"),
    "CipherDxe":               ("CipherDxe", "CipherDxe.inf", "DXE_DRIVER"),
    "ClockDxe":                ("ClockDxe", "ClockDxe.inf", "DXE_DRIVER"),
    "CmdDbDxe":                ("CmdDbDxe", "CmdDbDxe.inf", "DXE_DRIVER"),
    "CPRDxe":                  ("CPRDxe", "CPRDxe.inf", "DXE_DRIVER"),
    "DALSys":                  ("DALSYSDxe", "DALSYSDxe.inf", "DXE_DRIVER"),
    "DALTLMM":                 ("TLMMDxe", "TLMMDxe.inf", "DXE_DRIVER"),
    "DDRInfoDxe":              ("DDRInfoDxe", "DDRInfoDxe.inf", "DXE_DRIVER"),
    "DisplayDxe":              ("DisplayDxe", "DisplayDxe.inf", "DXE_DRIVER"),
    "EnvDxe":                  ("EnvDxe", "EnvDxe.inf", "DXE_DRIVER"),
    "FeatureEnablerDxe":       ("FeatureEnablerDxe", "FeatureEnablerDxe.inf", "DXE_DRIVER"),
    "GpiDxe":                  ("GpiDxe", "GpiDxe.inf", "DXE_DRIVER"),
    "HALIOMMU":                ("HALIOMMUDxe", "HALIOMMUDxe.inf", "DXE_DRIVER"),
    "HashDxe":                 ("HashDxe", "HashDxe.inf", "DXE_DRIVER"),
    "HWIODxeDriver":           ("HWIODxe", "HWIODxe.inf", "DXE_DRIVER"),
    "I2C":                     ("I2CDxe", "I2CDxe.inf", "DXE_DRIVER"),
    "LimitsDxe":               ("LimitsDxe", "LimitsDxe.inf", "DXE_DRIVER"),
    "MacDxe":                  ("MacDxe", "MacDxe.inf", "DXE_DRIVER"),
    "MinidumpTADxe":           ("MinidumpTADxe", "MinidumpTADxe.inf", "DXE_DRIVER"),
    "NpaDxe":                  ("NpaDxe", "NpaDxe.inf", "DXE_DRIVER"),
    "PdcDxe":                  ("PdcDxe", "PdcDxe.inf", "DXE_DRIVER"),
    "PILDxe":                  ("PILDxe", "PILDxe.inf", "DXE_DRIVER"),
    "PILProxyDxe":             ("PILProxyDxe", "PILProxyDxe.inf", "DXE_DRIVER"),
    "PlatformInfoDxeDriver":   ("PlatformInfoDxe", "PlatformInfoDxe.inf", "DXE_DRIVER"),
    "PmicDxe":                 ("PmicDxe", "PmicDxeLa.inf", "DXE_DRIVER"),
    "PwrUtilsDxe":             ("PwrUtilsDxe", "PwrUtilsDxe.inf", "DXE_DRIVER"),
    "QcomBds":                 ("QcomBds", "QcomBds.inf", "DXE_DRIVER"),
    "QcomChargerApp":          ("QcomChargerApp", "QcomChargerApp.inf", "UEFI_APPLICATION"),
    "QcomChargerDxeLA":        ("QcomChargerDxe", "QcomChargerDxeLA.inf", "DXE_DRIVER"),
    "QcomMpmTimerDxe":         ("QcomMpmTimerDxe", "QcomMpmTimerDxe.inf", "DXE_DRIVER"),
    "QcomWDogDxe":             ("QcomWDogDxe", "QcomWDogDxe.inf", "DXE_DRIVER"),
    "RngDxe":                  ("RNGDxe", "RngDxe.inf", "DXE_DRIVER"),
    "RpmhDxe":                 ("RpmhDxe", "RpmhDxe.inf", "DXE_DRIVER"),
    "SdccDxe":                 ("SdccDxe", "SdccDxe.inf", "DXE_DRIVER"),
    "SecRSADxe":               ("SecRSADxe", "SecRSADxe.inf", "DXE_DRIVER"),
    "ShmBridgeDxe":            ("ShmBridgeDxe", "ShmBridgeDxeLA.inf", "DXE_DRIVER"),
    "SmemDxe":                 ("SmemDxe", "SmemDxe.inf", "DXE_DRIVER"),
    "SPMI":                    ("SPMIDxe", "SPMIDxe.inf", "DXE_DRIVER"),
    "TsensDxe":                ("TsensDxe", "TsensDxe.inf", "DXE_DRIVER"),
    "TzDxe":                   ("TzDxe", "TzDxeLA.inf", "DXE_DRIVER"),
    "UFSDxe":                  ("UFSDxe", "UFSDxe.inf", "DXE_DRIVER"),
    "ULogDxe":                 ("ULogDxe", "ULogDxe.inf", "DXE_DRIVER"),
    "UsbConfigDxe":            ("UsbConfigDxe", "UsbConfigDxe.inf", "DXE_DRIVER"),
    "UsbDeviceDxe":            ("UsbDeviceDxe", "UsbDeviceDxe.inf", "DXE_DRIVER"),
    "UsbfnDwc3Dxe":            ("UsbfnDwc3Dxe", "UsbfnDwc3Dxe.inf", "DXE_DRIVER"),
    "UsbMsdDxe":               ("UsbMsdDxe", "UsbMsdDxe.inf", "DXE_DRIVER"),
    "UsbPwrCtrlDxe":           ("UsbPwrCtrlDxe", "UsbPwrCtrlDxe.inf", "DXE_DRIVER"),
    "ScmDxe":                  ("TzDxe", "ScmDxeLA.inf", "DXE_DRIVER"),
    "VcsDxe":                  ("VcsDxe", "VcsDxe.inf", "DXE_DRIVER"),
    "VerifiedBootDxe":         ("VerifiedBootDxe", "VerifiedBootDxe.inf", "DXE_DRIVER"),
    "VibratorDxe":             ("VibratorDxe", "VibratorDxe.inf", "DXE_DRIVER"),
}

# Generic EDK2 modules present in XBL. Not device drivers, and not to be shipped
# as binary blobs - Mu-Silicium builds all of these from source.
EDK2_CORE = {
    "ASN1X509Dxe", "ArmCpuDxe", "ArmGicDxe", "ArmTimerDxe", "CapsuleRuntimeDxe",
    "ConPlatformDxe", "ConSplitterDxe", "DevicePathDxe", "DiskIoDxe", "DxeCore",
    "EmbeddedMonotonicCounter", "EnglishDxe", "Fat", "FontDxe", "FvDxe",
    "FvSimpleFileSystem", "GraphicsConsoleDxe", "HiiDatabase", "MetronomeDxe",
    "PartitionDxe", "PrintDxe", "RealTimeClock", "ResetRuntimeDxe", "RuntimeDxe",
    "RscRtDxe", "SCHandlerRtDxe", "SecurityStubDxe", "SimpleTextInOutSerial",
    "VariableDxe", "WatchdogTimer",
}

INF_TEMPLATE = """##
#  {name} - extracted from this device's own XBL.
#  SPDX-License-Identifier: BSD-2-Clause-Patent
##

[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = {name}
  FILE_GUID      = {guid}
  MODULE_TYPE    = {mtype}
  VERSION_STRING = 1.0

[Binaries.AArch64]
  PE32|{name}.efi|{mtype}
"""

# Drivers this device's XBL does not carry, taken from the SM7225 sibling.
#
# Every other package under Binaries/gauguin/ is an image extracted from this
# phone's own XBL, and that is the rule the whole port runs on. These three
# cannot be, and the reason is structural rather than an extraction gap: XHCI is
# the UEFI-side USB *host* controller driver, and this phone's bootloader ships
# no host driver at all. `grep -i xhci` over xbl.img returns nothing, while the
# sanity check `usbfn` - the device-mode driver, which is a different job -
# returns one hit. There is nothing to extract, so the blob has to come from
# somewhere else or the firmware can never see a USB stick.
#
# Mu-Silicium's Binaries/bitra/ is the right somewhere else, because Bitra *is*
# this SoC: gauguin.dsc already includes BitraPkg/BitraPkg.dsc.inc, the SM7225
# layer, and Platforms/Realme/bitraPkg is the same part built for a sibling
# board. The alternative in the checkout, Binaries/generic/, is shared across
# SM6150, SM8250 and SDM845 platforms and is the least likely of the two to
# match a register map.
#
# Copied verbatim - INF, .efi and .depex together - rather than through
# INF_TEMPLATE below. XhciPciEmulationDxe's real depex is a thirteen-protocol
# conjunction that its INF supplies as `DXE_DEPEX|XhciPciEmulationDxe.depex`;
# the `[Depex] TRUE` also in that INF is not what ships, and a templated INF
# would drop both the depex and the UEFI_DRIVER binding XhciDxe needs.
SIBLING_SOURCE = "bitra"

SIBLING_BLOBS = {
    "XhciPciEmulationDxe": "QcomPkg/Drivers/XhciPciEmulationDxe",
    "XhciDxe":             "QcomPkg/Drivers/XhciDxe",
    "UsbInitDxe":          "QcomPkg/Drivers/UsbInitDxe",
}

# Driver names whose .efi carries a different base name than the .inf (a single
# package can ship two images).
EFI_OVERRIDE = {
    "PmicDxeLa.inf":  "PmicDxe.efi",
    "ShmBridgeDxeLA.inf": "ShmBridgeDxe.efi",
    "TzDxeLA.inf":    "TzDxe.efi",
    "ScmDxeLA.inf":   "ScmDxe.efi",
    "QcomChargerDxeLA.inf": "QcomChargerDxeLA.efi",
    "RngDxe.inf":     "RngDxe.efi",
}

# Stable per-driver GUIDs. Mu-Silicium's Binaries INF files carry vendor GUIDs
# that we cannot reproduce; these are generated deterministically from the name
# so the tree is reproducible, and are only used to identify the FFS file. The
# signed PE image itself is unchanged, which is what matters for loading.
GUID_NAMESPACE = "6d5f4a2e-1c8b-4f37-9a02-7b3e5c8d1f60"


def guid_for(name):
    """Deterministic RFC-4122 v5-style GUID from a driver name."""
    import hashlib
    import uuid
    h = hashlib.sha1(GUID_NAMESPACE.encode() + name.encode()).digest()
    return str(uuid.UUID(bytes=bytes(
        [h[0], h[1], h[2], h[3], h[4], h[5],
         (h[6] & 0x0F) | 0x50, h[7], (h[8] & 0x3F) | 0x80, h[9],
         h[10], h[11], h[12], h[13], h[14], h[15]]))).upper()


def present_drivers(dxe_dir):
    """The `QcomPkg/Drivers/.../<inf>` paths we can actually ship.

    One entry per mapped driver whose .efi really is in the extraction, so the
    generated driver lists never reference a blob that is not there.
    """
    return {f"QcomPkg/Drivers/{d}/{i}"
            for src, (d, i, _) in DRIVERS.items()
            if os.path.isfile(os.path.join(dxe_dir, src + ".efi"))}


def stage_sibling_blobs(mu_root, out, names):
    """Copy the non-XBL blobs into Binaries/gauguin/ and return their INF paths.

    `names` are keys of SIBLING_BLOBS; the returned set is keyed the way
    present_drivers() keys its own, so a caller can union the two and hand the
    result to the platform generator as the drivers it may reference.

    Additive and idempotent: it overwrites the three directories it owns and
    touches nothing else, so re-running it is how a checkout picks the blobs up
    after a fresh clone of Mu-Silicium took them away.
    """
    src_root = os.path.join(mu_root, "Binaries", SIBLING_SOURCE, "QcomPkg", "Drivers")
    staged = set()
    for name in names:
        rel = SIBLING_BLOBS[name]
        src = os.path.join(src_root, name)
        inf = os.path.join(src, f"{name}.inf")
        if not os.path.isfile(inf):
            sys.exit(f"missing {inf} - Mu-Silicium's Binaries/{SIBLING_SOURCE} is "
                     f"the only source for {name}, and this checkout has no copy")
        dest = os.path.join(out, rel)
        os.makedirs(dest, exist_ok=True)
        copied = []
        for f in sorted(os.listdir(src)):
            shutil.copyfile(os.path.join(src, f), os.path.join(dest, f))
            copied.append(f)
        staged.add(f"{rel}/{name}.inf")
        print(f"   {SIBLING_SOURCE} -> gauguin: {rel}  ({', '.join(copied)})")
    return staged


def fix_bmp_offset(data):
    """Return (bytes, note) with bfOffBits corrected, or (data, None) if fine.

    Seven of the bitmaps in XBL - the battery and thermal indicator images -
    declare a `bfOffBits` that is exactly 2 bytes short of where their pixel
    data actually begins. Everything else about them is a standard BMP: the
    file size, the dimensions and a 4-byte-aligned row stride all agree with
    each other, and there is a 4-entry palette (biClrUsed = 4) starting at 54
    that ends at 70, leaving 2 bytes of padding before the data.

    Mu-Silicium's build-time BMP check catches this, and EDK2's decoder would
    read 2 bytes early and shear every row. Rewriting the one field is safe
    because the offset it should hold is not a guess - it is `filesize -
    height * stride`, which the rest of the header already agrees on. The
    extracted originals in device/dxe/ are left untouched; this only affects
    what gets packaged.
    """
    import struct
    if len(data) < 54 or data[:2] != b"BM":
        return data, None

    off, = struct.unpack("<I", data[10:14])
    width, = struct.unpack("<i", data[18:22])
    height, = struct.unpack("<i", data[22:26])
    bpp, = struct.unpack("<H", data[28:30])
    if width <= 0 or height <= 0 or bpp == 0:
        return data, None

    stride = ((width * bpp + 31) >> 3) & ~0x3
    correct = len(data) - abs(height) * stride
    if off == correct:
        return data, None
    if not (0 < correct < len(data)):
        return data, f"unfixable: computed offset {correct} out of range"

    return data[:10] + struct.pack("<I", correct) + data[14:], \
        f"bfOffBits {off} -> {correct}"


def copy_raw_files(src_dir, dst_dir):
    """Copy the extracted config/panel/bitmap files, fixing BMP offsets."""
    os.makedirs(dst_dir, exist_ok=True)
    copied = fixed = 0
    for f in sorted(os.listdir(src_dir)):
        if not f.endswith(".raw"):
            continue
        name = f[:-4]
        path = os.path.join(src_dir, f)
        if name.endswith(".bmp"):
            with open(path, "rb") as fh:
                data = fh.read()
            data, note = fix_bmp_offset(data)
            if note:
                print(f"   {name}: {note}")
                fixed += 1
            with open(os.path.join(dst_dir, name), "wb") as fh:
                fh.write(data)
        else:
            shutil.copyfile(path, os.path.join(dst_dir, name))
        copied += 1
    return copied, fixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dxe_dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mu", default=None,
                    help="Mu-Silicium checkout to read sibling blobs from "
                         "(default: <repo>/work/uefi/Mu-Silicium)")
    ap.add_argument("--sibling", action="append", default=[],
                    choices=sorted(SIBLING_BLOBS), metavar="NAME",
                    help="also stage a driver this device's XBL does not carry "
                         "(see SIBLING_BLOBS); repeatable")
    ap.add_argument("--copy-raw", action="store_true",
                    help="also copy the config/panel files into RawFiles/")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(args.dxe_dir)))
    out = args.out or os.path.join(repo, "uefi", "Binaries", "gauguin")
    out = os.path.normpath(out)
    mu_root = args.mu or os.path.join(repo, "work", "uefi", "Mu-Silicium")

    available = {f[:-4] for f in os.listdir(args.dxe_dir) if f.endswith(".efi")}
    print(f"{len(available)} extracted drivers; {len(DRIVERS)} mapped")

    # XBL also carries Qualcomm's build of the generic EDK2 core (DxeCore,
    # RuntimeDxe, VariableDxe, ...). Those must NOT be shipped as blobs - the
    # firmware has to build them from source alongside the platform, or the
    # module versions will not match the rest of the tree. Kept out of DRIVERS
    # deliberately, and reported separately so the omission is visible.
    unmapped = sorted(available - set(DRIVERS))
    core = [d for d in unmapped if d in EDK2_CORE]
    other = [d for d in unmapped if d not in EDK2_CORE]
    print(f"  {len(core)} are EDK2 core modules - built from source, not shipped as blobs")
    if other:
        print(f"  {len(other)} Qualcomm drivers with no mapped package: {', '.join(other)}")

    written = 0
    for src_name, (drvdir, infname, mtype) in sorted(DRIVERS.items(), key=lambda kv: kv[1]):
        src = os.path.join(args.dxe_dir, src_name + ".efi")
        if not os.path.isfile(src):
            continue
        dest = os.path.join(out, "QcomPkg", "Drivers", drvdir)
        os.makedirs(dest, exist_ok=True)

        efi_name = EFI_OVERRIDE.get(infname, src_name + ".efi")
        shutil.copyfile(src, os.path.join(dest, efi_name))
        with open(os.path.join(dest, infname), "w") as fh:
            fh.write(INF_TEMPLATE.format(name=efi_name[:-4], guid=guid_for(infname),
                                         mtype=mtype))
        written += 1

    print(f"wrote {written} driver packages to {out}/QcomPkg/Drivers")
    if args.sibling:
        names = sorted(set(args.sibling))
        print(f"staging {len(names)} driver(s) this XBL does not carry, "
              f"from Binaries/{SIBLING_SOURCE}:")
        stage_sibling_blobs(mu_root, out, names)
    if args.copy_raw:
        n, fixed = copy_raw_files(args.dxe_dir, os.path.join(out, "RawFiles"))
        print(f"copied {n} raw files to {out}/RawFiles"
              + (f" ({fixed} BMP offsets corrected)" if fixed else ""))


if __name__ == "__main__":
    main()
