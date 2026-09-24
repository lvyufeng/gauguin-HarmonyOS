#!/usr/bin/env python3
"""Print the order the a-priori batch will run in, read out of the firmware itself.

`CoreFwVolEventProtocolNotify` walks the volume's Apriori file in **index order**
and appends every match to the scheduled queue; the dispatcher then drains that
queue first-in-first-out. So the Apriori file's GUID order *is* the order the
drivers' entry points run in — and it is not the order the files sit in the
volume. Those are two independent orders, and the one that decides what happens
on the device is this one.

That array is built by GenFds from the INF list in `APRIORI.inc`, and nothing in
the build prints it back. This does. It is what makes an experiment that
reorders that file (`tools/make_uefi_platform.py --apriori-move`, driven by
`tools/build-apriori-variant.sh`) checkable against the artifact that actually
gets flashed rather than against the file that was meant to produce it — the
distinction matters here, because a firmware volume in this project has twice
been read back with a parser that was wrong in a way that still produced a
plausible list (see `_walk_from` and `fv_files` in tools/fv-inventory.py).

`--expect` compares the order against an APRIORI.inc, with each `!if` in that
file evaluated from a value the caller supplies rather than guessed, and exits
nonzero on any difference.

Usage:
    tools/apriori-order.py work/out/p2-variants/Mu-gauguin-silicon-gzip.img
    tools/apriori-order.py <fd> --expect uefi/Platforms/Xiaomi/gauguinPkg/Include/APRIORI.inc
    tools/apriori-order.py <img> --define USE_SOME_SWITCH=1
    tools/apriori-order.py <img> --xref Build/gauguinPkg/DEBUG_CLANGPDB/FV/Guid.xref
"""

import argparse
import importlib.util
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The PI a-priori file GUID. GenFv emits exactly one FREEFORM file with this
# name GUID in a volume that has an `APRIORI` section, whatever the platform.
APRIORI_GUID = "FC510EE7-FFDC-11D4-BD41-0080C73C8881"
SECTION_RAW = 0x19

DEFAULT_EXPECT = os.path.join(ROOT, "uefi/Platforms/Xiaomi/gauguinPkg/Include/APRIORI.inc")
DEFAULT_MU = os.path.join(ROOT, "work/uefi/Mu-Silicium")
DEFAULT_FV = os.path.join(DEFAULT_MU, "Build/gauguinPkg/DEBUG_CLANGPDB/FV")


def load_fv_inventory():
    """Import tools/fv-inventory.py, whose FV walker this needs and must not copy."""
    path = os.path.join(ROOT, "tools/fv-inventory.py")
    spec = importlib.util.spec_from_file_location("fv_inventory", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apriori_array(fvi, path):
    """The 16-byte GUIDs of the Apriori file, in the order the file holds them."""
    d = open(path, "rb").read()
    if d[:8] == b"ANDROID!":
        files, fv_len, offsets, inner = fvi.unpack(path)
    else:
        files, fv_len, offsets, inner = fvi.fvmain_of_fd(d, verbose=False)
    if inner is None:
        sys.exit(f"{path}: no FVMAIN in it")

    at = next((i for i, f in enumerate(files) if f[0] == APRIORI_GUID), None)
    if at is None:
        sys.exit(f"{path}: FVMAIN carries no Apriori file ({APRIORI_GUID}). "
                 f"{len(files)} files, none of them that GUID")
    guid, typ, size, _name, _state = files[at]

    body = inner[offsets[at] + 24:offsets[at] + size]
    raw = [b for st, b in fvi.sections(body) if st == SECTION_RAW]
    if not raw:
        sys.exit(f"{path}: the Apriori file has no RAW section "
                 f"(sections: {[hex(st) for st, _ in fvi.sections(body)]})")
    if len(raw) > 1:
        sys.exit(f"{path}: {len(raw)} RAW sections in the Apriori file")
    blob = raw[0]
    if len(blob) % 16:
        sys.exit(f"{path}: Apriori RAW section is {len(blob)} bytes, not a "
                 f"multiple of 16")

    return [fvi.guid_str(blob[i:i + 16]) for i in range(0, len(blob), 16)], len(files)


def inf_order(path, macros):
    """The INF list of an APRIORI.inc, with its `!if`s evaluated.

    Reading the file as text would count every branch of every conditional; the
    build counts one branch of each. Rather than re-implement the preprocessor,
    the caller supplies the value of each variable the file tests - `--display`
    fills in USE_CUSTOM_DISPLAY_DRIVER and `--define` any other - and this looks
    them up. A variable that was not supplied is an error rather than a silent
    guess, because guessing a branch the wrong way produces a shorter list that
    still reads plausible, and the whole point of this tool is to be trusted
    against the artifact.
    """
    out, stack = [], []
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        m = re.match(r'^!if\s+\$\((\w+)\)\s*==\s*(\d+)$', s)
        if m:
            name, value = m.group(1), int(m.group(2))
            if name not in macros:
                sys.exit(f"{path}: !if on $({name}), whose value was not "
                         f"supplied - pass --define {name}=0 or --define {name}=1")
            stack.append(macros[name] == value)
            continue
        if s == "!else":
            if not stack:
                sys.exit(f"{path}: !else without !if")
            stack[-1] = not stack[-1]
            continue
        if s == "!endif":
            if not stack:
                sys.exit(f"{path}: !endif without !if")
            stack.pop()
            continue
        m = re.match(r'^INF (\S+)$', s)
        if m and all(stack):
            out.append(m.group(1))
    return out


def inf_index(roots):
    """basename (lower) -> [INF paths], for every INF under `roots`.

    The INF paths in APRIORI.inc are package-relative (`SiliciumPkg/Drivers/...`)
    while the tree is rooted elsewhere (`Silicon/Silicium/SiliciumPkg/...`), and
    which directory a package name resolves to is a property of the DSC's
    PACKAGES_PATH rather than of the path. So the file is found by name and then
    identified by what it says it builds - see `module_name`. `Build` is skipped
    because it holds a copy of every INF the build has ever looked at, which is
    thousands of files of no additional information.
    """
    idx = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "Build", "Conf")]
            for fn in filenames:
                if fn.lower().endswith(".inf"):
                    idx.setdefault(fn.lower(), []).append(os.path.join(dirpath, fn))
    return idx


def module_name(path, idx, by_name):
    """(BASE_NAME, INF path) for one APRIORI.inc line, or (None, candidates).

    Not derived from the file name, because that is wrong often enough here to
    matter: `TLMMDxe/TLMMDxe.inf` builds `DALTLMM`, `TzDxe/TzDxeLA.inf` builds
    `TzDxe`, `HWIODxe/HWIODxe.inf` builds `HWIODxeDriver`, and the DXE core's
    `DxeMain.inf` builds `DxeCore`. Deriving the name instead produced fifty
    confident "OUT OF ORDER" lines about drivers that were in the right order -
    a wrong reader reporting a wrong firmware, which is the failure this tool
    exists to avoid, so the INF itself is read.

    Where two files share a name, the one whose BASE_NAME the build's cross
    reference knows is the one that was built; that test, rather than an order of
    preference over directories, is what picks between them.
    """
    names = []
    for inf in idx.get(path.rsplit("/", 1)[-1].lower(), []):
        m = re.search(r'^\s*BASE_NAME\s*=\s*(\S+)',
                      open(inf, encoding="utf-8", errors="replace").read(), re.M)
        if not m:
            continue
        names.append((m.group(1), inf))
        if m.group(1).lower() in by_name:
            return m.group(1), inf
    return None, names


def xref_names(path):
    """GUID (upper) <-> module name, from the build's own Guid.xref.

    Keyed by the bare module name, because that is what the extra column holds
    (`D6A2CB7F-... DxeCore`). A name that resolves to two GUIDs is reported
    rather than resolved to whichever came first: in this tree
    `TzDxe/TzDxeLA.inf` and `TzDxe/ScmDxeLA.inf` are two different drivers out of
    one package, so the collision is a real shape here and not a hypothetical.
    """
    by_guid, by_name, dupes = {}, {}, {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r'^([0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27})\s+(\S+)', line)
        if not m:
            continue
        guid = m.group(1).upper()
        name = m.group(2).replace("\\", "/").rsplit("/", 1)[-1].lower()
        by_guid[guid] = m.group(2)
        if name in by_name and by_name[name] != guid:
            dupes.setdefault(name, {by_name[name]}).add(guid)
        by_name.setdefault(name, guid)
    return by_guid, by_name, dupes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="a payload .img, or an FD (SILICIUM_UEFI.fd)")
    ap.add_argument("--expect", metavar="APRIORI.inc",
                    help="check the order against this file's INF list "
                         f"(default: {os.path.relpath(DEFAULT_EXPECT, ROOT)})")
    ap.add_argument("--display", choices=("qcom", "simple"), default="simple",
                    help="which branch of the file's USE_CUSTOM_DISPLAY_DRIVER "
                         "conditional is the built one (default: simple, which is "
                         "what the current payloads are built with)")
    ap.add_argument("--define", action="append", default=[], metavar="NAME=VALUE",
                    help="the built value of an `!if $(NAME) == VALUE` the file "
                         "tests and that --display does not cover, e.g. "
                         "--define USE_XHCI_HOST_DRIVER=1 if APRIORI.inc has grown "
                         "one; repeatable. Without it an `!if` on a name that was "
                         "not supplied is an error, by design - see inf_order")
    ap.add_argument("--xref", metavar="Guid.xref",
                    help="resolve GUIDs to module names through the build's own "
                         f"cross reference (default: {os.path.relpath(DEFAULT_FV, ROOT)}/Guid.xref)")
    ap.add_argument("--mu", default=os.environ.get("MU", DEFAULT_MU),
                    help="the Mu-Silicium checkout the INF paths resolve against "
                         f"(default: {os.path.relpath(DEFAULT_MU, ROOT)})")
    ap.add_argument("--inf-root", action="append", default=[],
                    help="add a directory the INF paths resolve against; the "
                         "default set is the checkout, its Mu_Basecore submodule, "
                         "and this repository's own uefi/ tree")
    args = ap.parse_args()

    fvi = load_fv_inventory()
    guids, nfiles = apriori_array(fvi, args.image)

    xref = os.path.join(DEFAULT_FV, "Guid.xref")
    by_guid, by_name, dupes = ({}, {}, {})
    if args.xref or os.path.isfile(xref):
        by_guid, by_name, dupes = xref_names(args.xref or xref)

    print(f"{os.path.basename(args.image)}: FVMAIN {nfiles} files, "
          f"Apriori file {len(guids)} GUIDs")
    for i, g in enumerate(guids):
        print(f"  {i:3d}  {g}  {by_guid.get(g, '?')}")

    expect_path = args.expect or DEFAULT_EXPECT
    macros = {"USE_CUSTOM_DISPLAY_DRIVER": 1 if args.display == "qcom" else 0}
    for d in args.define:
        name, sep, value = d.partition("=")
        if not sep or not name.strip() or not value.strip().lstrip("-").isdigit():
            ap.error(f"--define wants NAME=VALUE with an integer value, got '{d}'")
        macros[name.strip()] = int(value)
    want_paths = inf_order(expect_path, macros)
    roots = args.inf_root or [args.mu, os.path.join(args.mu, "Mu_Basecore"),
                              os.path.join(ROOT, "uefi")]
    idx = inf_index(roots)
    missing_root = [r for r in roots if not os.path.isdir(r)]
    if missing_root:
        print(f"  note: no such INF root: {', '.join(missing_root)}")
    print(f"  INF roots walked: {len(roots)} -> {len(idx)} distinct INF names")

    # Positional, and deliberately so: `want` keeps one slot per active INF line
    # so that a line that cannot be resolved shortens nothing. Padding it out
    # instead made an early failure look like fifty later ones, which is how this
    # tool's first run here reported 69 out-of-order entries for an array that
    # was in the right order.
    want, bad = [], 0
    for path in want_paths:
        name, where = module_name(path, idx, by_name)
        if name is None:
            seen = ", ".join(sorted({n for n, _ in where})) or "no INF of that name"
            print(f"  UNRESOLVED: {path} - {seen} is not in the cross reference, "
                  f"so its GUID is unknown")
            want.append((path, None))
            bad += 1
            continue
        guid = by_name.get(name.lower())
        if guid is None:
            print(f"  UNRESOLVED: {path} builds {name}, which is not in the "
                  f"cross reference")
            want.append((path, None))
            bad += 1
            continue
        want.append((path, guid))

    if len(want_paths) != len(guids):
        print(f"  MISMATCH: {len(want_paths)} active INF lines, "
              f"{len(guids)} GUIDs in the array")
        bad += 1

    for i, ((path, guid), got) in enumerate(zip(want, guids)):
        if guid is not None and guid != got:
            print(f"  OUT OF ORDER at {i}: INF says {path} ({guid}),")
            print(f"                     the array says {got} "
                  f"({by_guid.get(got, '?')})")
            bad += 1

    if bad:
        print(f"\n{bad} problem(s): the firmware does not run the order "
              f"{os.path.basename(expect_path)} implies")
        sys.exit(1)
    print(f"\nthe array is exactly the INF order of {os.path.basename(expect_path)}: "
          f"{len(guids)} entries, zero mismatches")


if __name__ == "__main__":
    main()
