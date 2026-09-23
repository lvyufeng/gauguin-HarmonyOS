#!/usr/bin/env python3
"""The two device-tree containers ABL reads, without pylibfdt.

`abl-boot-check.py` and `make_dtbo_sinks.py` both have to answer questions about
blobs Qualcomm's bootloader consumes - what is on a tree's root node, what does a
dtbo entry's `__fixups__` demand, which phandles are already taken - and both are
stdlib-only on purpose, because every tool in tools/ is. This is that reader, in
one place, so the two cannot drift apart on what a blob says.

It is not libfdt. Nothing here validates a tree or writes one; it walks a
structure block and reports what is in it, and the callers are all in the
business of asking whether a blob is well-formed enough for ABL to accept.

Two things about the format are load-bearing and easy to get wrong, so they are
handled here once:

  * the fdt header is big-endian;
  * a `FDT_BEGIN_NODE` name is NUL-terminated and the token after it is 4-byte
    aligned *relative to the blob's own start*, not to the file. dtbo entries are
    packed back to back with no padding - entry 13 of this device's dtbo starts at
    file offset 0x295e6d, which is not a multiple of 4 - so rounding the absolute
    offset down lands inside the name and reads a garbage token. That presents as
    "this blob has one token and no properties", which is a silent lie about a
    well-formed tree.
"""
import struct

# --- fdt structure tokens ----------------------------------------------------
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9
FDT_MAGIC = 0xD00DFEED

# --- the dtbo table header ---------------------------------------------------
# struct DtboTableHdr / struct DtboTableEntry, both packed, both big-endian.
DTBO_TABLE_MAGIC = 0xD7B7AB1E
DTBO_HDR_SIZE = 32
DTBO_ENTRY_SIZE = 32
DTBO_MAX_SIZE_ALLOWED = 24 * 1024 * 1024      # DTBO_MAX_SIZE_ALLOWED, in the vendor


def header_ok(blob, off):
    """fdt_check_header, from libfdt: magic, version, last_comp_version.

    Returns the blob's totalsize, or None if ABL would refuse it as an fdt.
    """
    if off + 40 > len(blob):
        return None
    magic = struct.unpack_from(">I", blob, off)[0]
    if magic != FDT_MAGIC:
        return None
    totalsize = struct.unpack_from(">I", blob, off + 4)[0]
    version = struct.unpack_from(">I", blob, off + 20)[0]
    last_comp = struct.unpack_from(">I", blob, off + 24)[0]
    if last_comp > 17 or version < 16 or version > 17:
        return None
    return totalsize


def scan(blob, off=0):
    """Walk a blob's structure block. Yields (name, depth, {prop: value}).

    A node is yielded as (name, depth, {}) with the depth it sits at - the root
    is depth 0, and its direct children are depth 1 - and each property as
    (None, depth, {prop: raw bytes}), where the depth is that of the node holding
    it. So root properties come out at depth 1 and a fragment's `target` at
    depth 2. Prose about this reader says "depth-1 node"; the numbers here are
    the caller-visible ones.

    Only the structure and strings blocks are needed. A malformed token ends the
    walk rather than raising, because the callers are all in the business of
    asking whether a blob is well-formed.
    """
    if header_ok(blob, off) is None:
        return
    off_struct = struct.unpack_from(">I", blob, off + 8)[0]
    off_strings = struct.unpack_from(">I", blob, off + 12)[0]
    size_struct = struct.unpack_from(">I", blob, off + 36)[0]
    p, depth, name = off + off_struct, 0, ""
    end = off + off_struct + size_struct
    while p < end:
        tok = struct.unpack_from(">I", blob, p)[0]
        p += 4
        if tok == FDT_BEGIN_NODE:
            nul = blob.index(b"\x00", p)
            name = blob[p:nul].decode("ascii", "replace")
            p = off + ((nul + 1 - off + 3) & ~3)
            yield name, depth, {}
            depth += 1
        elif tok == FDT_END_NODE:
            depth -= 1
        elif tok == FDT_PROP:
            length, nameoff = struct.unpack_from(">II", blob, p)
            p += 8
            nul = blob.index(b"\x00", off + off_strings + nameoff)
            pname = blob[off + off_strings + nameoff:nul].decode("ascii", "replace")
            val = blob[p:p + length]
            p += (length + 3) & ~3
            yield None, depth, {pname: val}
        elif tok == FDT_NOP:
            continue
        else:
            return


def root_props(blob, off, totalsize):
    """The root node's properties, as {name: raw bytes}."""
    props = {}
    for name, depth, p in scan(blob[:off + totalsize], off):
        if name is None and depth == 1:
            props.update(p)
        elif name is not None and depth > 0 and name:
            break
    return props


def child_props(blob, off, totalsize, child):
    """The properties of a depth-1 node, as {name: raw bytes}.

    `__symbols__` and `__fixups__` are nodes, not properties, so a property-only
    reader reports both as absent from every tree in the world. That is how a
    real difference between our device tree and the one the phone actually boots
    stayed invisible: the stock trees all carry `__symbols__`, ours does not.
    """
    props, inside = {}, False
    for name, depth, p in scan(blob[:off + totalsize], off):
        if name is not None:
            inside = (name == child and depth == 1)
            continue
        if inside:
            props.update(p)
    return props


def strval(v):
    return v.rstrip(b"\x00").decode("ascii", "replace") if v else ""


def symbols(blob, off, totalsize):
    """`/__symbols__`, the label -> path table libufdt resolves fixups against."""
    return {k: strval(v)
            for k, v in child_props(blob, off, totalsize, "__symbols__").items()}


def fixups(blob, off, totalsize):
    """`/__fixups__`, the symbol -> locations table an overlay arrives with.

    The property *name* is the symbol (already a string); the value is a string
    list of the places the placeholder has to be patched.
    """
    return {k: strval(v)
            for k, v in child_props(blob, off, totalsize, "__fixups__").items()}


def fragments(blob, off, totalsize):
    """The fragments' targets, in order: an int phandle, or a path string.

    Depth 2 is `fragment@N`'s own properties - the `__overlay__` subtree below it
    can contain a property called `target` in its own right (a device node can be
    named anything), which is why the depth is checked rather than the name alone.
    """
    frags = []
    for name, depth, p in scan(blob[:off + totalsize], off):
        if name is not None or depth != 2:
            continue
        if "target" in p and len(p["target"]) >= 4:
            frags.append(struct.unpack(">I", p["target"][:4])[0])
        elif "target-path" in p:
            frags.append(strval(p["target-path"]))
    return frags


def paths(blob, off, totalsize):
    """Every property in the tree, as (node path, property name, raw value).

    The path is the one libufdt would look the node up by, so it is what
    `__symbols__` has to name and what `ufdt_get_node_by_path()` resolves - which
    is why it is built from the node *names*, and why the root is "/" rather than
    the empty string a bare `fdt_scan` yields.
    """
    stack = []
    for name, depth, p in scan(blob[:off + totalsize], off):
        if name is not None:
            if depth == 0:
                stack = []
            else:
                del stack[depth - 1:]
                stack.append(name)
            continue
        path = "/" + "/".join(stack)
        for k, v in p.items():
            yield path, k, v


def node_paths(blob, off, totalsize):
    """Every node's path, whether or not it has a property on it.

    `paths()` above is per-*property*, so a node with nothing on it never appears
    in it - and that is precisely the distinction a `__symbols__` fixup turns on.
    libufdt resolves a symbol in three steps and the third has no error path:
    `ufdt_get_node_by_path()` on the symbol's path (NULL is fatal), then
    `ufdt_node_get_phandle()` on the node it found, which **returns 0** for a node
    with no `phandle` property rather than failing. A fragment resolved to
    phandle 0 is then skipped in silence, because `ufdt_overlay_apply_fragments()`
    aborts on `OVERLAY_RESULT_MERGE_FAIL` alone and this is
    `OVERLAY_RESULT_TARGET_INVALID`.

    So a symbol naming a path with no node is a refusal, and a symbol naming a
    node with no phandle is a boot with that fragment quietly missing. Both are
    wrong; only one of them is loud.
    """
    stack, out = [], set()
    for name, depth, p in scan(blob[:off + totalsize], off):
        if name is None:
            continue
        if depth == 0:
            stack = []
        else:
            del stack[depth - 1:]
            stack.append(name)
        out.add("/" + "/".join(stack))
    return out


def phandles(blob, off, totalsize):
    """{phandle: [node paths]} for the whole tree.

    libufdt resolves every fragment target through a phandle table it builds from
    the main tree, and `ufdt_overlay_apply_fragment()` looks the target up in it -
    so a fragment whose target phandle belongs to a real node is merged into that
    node instead of the one the symbol was meant to name. Which is the only way a
    synthetic `/__symbols__` can do damage, and therefore worth being able to
    check: a phandle with two owners is a tree whose merge is a coin toss.
    """
    out = {}
    for path, k, v in paths(blob, off, totalsize):
        if k == "phandle" and len(v) >= 4:
            out.setdefault(struct.unpack(">I", v[:4])[0], []).append(path)
    return out


def idcells(v):
    """A qcom,msm-id / board-id / pmic-id value as its cells."""
    if not v:
        return None
    return tuple(struct.unpack_from(">I", v, i)[0] for i in range(0, len(v) - 3, 4))


# --- the dtbo partition ------------------------------------------------------

def dtbo_table(path):
    """LoadAndValidateDtboImg(), the *only* thing that decides which DTB path
    ABL takes. Returns (valid, fields, reason, entries)."""
    d = open(path, "rb").read()
    if len(d) < DTBO_HDR_SIZE:
        return False, {}, "truncated", []
    magic, total, hsz, esz, cnt, eoff, _pg, _r = struct.unpack_from(">8I", d, 0)
    f = dict(magic=magic, total_size=total, header_size=hsz, entry_size=esz,
             entry_count=cnt, entry_offset=eoff, size=len(d))
    if magic != DTBO_TABLE_MAGIC:
        return False, f, f"header magic {magic:#x}, want {DTBO_TABLE_MAGIC:#x}", []
    if not total or total > DTBO_MAX_SIZE_ALLOWED:
        return False, f, f"TotalSize {total:#x} out of range", []
    if hsz != DTBO_HDR_SIZE:
        return False, f, f"HeaderSize {hsz}, want {DTBO_HDR_SIZE}", []
    if esz != DTBO_ENTRY_SIZE:
        return False, f, f"DtEntrySize {esz}, want {DTBO_ENTRY_SIZE}", []
    if eoff > DTBO_MAX_SIZE_ALLOWED:
        return False, f, "DtEntryOffset out of range", []
    if cnt * esz > len(d) or eoff + cnt * esz > len(d):
        return False, f, f"{cnt} entries from {eoff:#x} do not fit", []
    entries = []
    for i in range(cnt):
        o = eoff + i * esz
        dtsz, dtoff = struct.unpack_from(">2I", d, o)
        entries.append((dtsz, dtoff))
    return True, f, "", entries


def dtbo_entries(d, entries):
    """What each dtbo entry is, and what it demands of the tree it lands on.

    A board overlay is a dtc `-@` overlay: each fragment carries
    `target = <0xffffffff>` plus a `__fixups__` node mapping a *symbol name* to
    the places that placeholder has to be patched. 0xffffffff therefore does not
    mean "no target" - it means "not resolved yet", and `ufdt_overlay_do_fixups`
    resolves it against the main tree's `/__symbols__` before any fragment runs.
    That makes `__symbols__` a hard requirement on the tree ABL overlays onto,
    which is where the vendor base tree and ours part company.
    """
    out = []
    for i, (dtsz, dtoff) in enumerate(entries):
        blob = d[:dtoff + dtsz]
        if header_ok(blob, dtoff) is None:
            out.append(dict(index=i, ok=False))
            continue
        rp = root_props(blob, dtoff, dtsz)
        out.append(dict(
            index=i, ok=True, size=dtsz, offset=dtoff,
            # The entry's own bytes, so a caller can hand the overlay to something
            # that applies it for real (`fdtoverlay`) without re-parsing the table.
            blob=d[dtoff:dtoff + dtsz],
            msm_id=idcells(rp.get("qcom,msm-id")),
            board_id=idcells(rp.get("qcom,board-id")),
            model=strval(rp.get("model", b"")),
            root_props=rp,
            fixups=fixups(blob, dtoff, dtsz),
            frags=fragments(blob, dtoff, dtsz),
            has_symbols=bool(child_props(blob, dtoff, dtsz, "__symbols__"))))
    return out
