#!/usr/bin/env python3
"""Parse a Qualcomm GPT image and print the partition table.

Qualcomm UFS LUNs on this platform use 4096-byte logical sectors, and some LUNs
carry the GPT at a non-zero offset, so this does not assume 512-byte sectors —
it locates the header by signature and infers the sector size from its position.

Usage:  gpt.py IMG [IMG...]
"""
import struct
import sys
import uuid

HDR = struct.Struct("<8sIIIIQQQQ16sQIII")  # 92 bytes, EFI_PARTITION_TABLE_HEADER


def parse(path):
    with open(path, "rb") as fh:
        data = fh.read()

    off = data.find(b"EFI PART")
    if off < 0:
        return None

    sig, rev, hsize, crc, _res, cur, back, first_u, last_u, dguid, pel, npe, psz, _crc = \
        HDR.unpack(data[off:off + HDR.size])

    sec = off  # the primary header always sits at LBA 1
    if sec not in (512, 4096):
        sec = 512 if off == 512 else 4096

    base = pel * sec
    parts = []
    for i in range(npe):
        ent = data[base + i * psz: base + (i + 1) * psz]
        if len(ent) < 128 or ent[0:16] == b"\x00" * 16:
            continue
        tguid = uuid.UUID(bytes_le=ent[0:16])
        pguid = uuid.UUID(bytes_le=ent[16:32])
        first, last = struct.unpack("<QQ", ent[32:48])
        attrs = struct.unpack("<Q", ent[48:56])[0]
        name = ent[56:128].decode("utf-16-le").rstrip("\x00")
        parts.append(dict(idx=i, name=name, first=first, last=last,
                          size=(last - first + 1) * sec, attr=attrs,
                          type_guid=tguid, part_guid=pguid))

    return dict(sector=sec, disk_guid=uuid.UUID(bytes_le=dguid),
                entries_lba=pel, entries=npe, entry_size=psz, parts=parts)


def main(argv):
    for path in argv[1:]:
        info = parse(path)
        print(f"== {path}")
        if info is None:
            print("   no GPT found")
            continue
        print(f"   sector={info['sector']}  disk={info['disk_guid']}  "
              f"entries@{info['entries_lba']} n={info['entries']} sz={info['entry_size']}")
        tot = 0
        for p in info["parts"]:
            tot += p["size"]
            print(f"   [{p['idx']:2}] {p['name']:20} {p['size']:>13,} bytes "
                  f"LBA {p['first']:>8}-{p['last']:<8} attr={p['attr']:#x}")
        print(f"   total {tot:,} bytes across {len(info['parts'])} partitions")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv)
