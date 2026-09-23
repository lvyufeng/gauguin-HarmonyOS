#!/usr/bin/env python3
"""What this phone answers to, as far as ABL is concerned.

These are properties of the device, not of anything in this repository, and they
are read out of the phone itself rather than invented - the ids out of its dtbo
and boot image, the memory map and the firmware's own carveouts out of the
running kernel's /proc/device-tree. They live here so that the tools which have
to agree about them - `abl-boot-check.py`, which replays ABL's DTB selection, and
`make_dtbo_sinks.py`, which has to name the board overlay - cannot drift apart on
what "this device" means.
"""

# `qcom,board-id = <0x23 0>`. ReadDtbFindMatch() compares an entry's board-id cell
# 0 against the device's variant id from the CDT, and 0x23 is the gauguin variant.
# It is the only board-id in this phone's dtbo that carries the Gauguin overlay
# (entry 13), which is why offline it is a constant rather than a measurement:
# nothing in the boot image declares it, and the CDT is not in any dump we hold.
GAUGUIN_VARIANT_ID = 0x23

# The `qcom,msm-id` platform ids this device answers to. GetPlatformMatchDtb()
# compares the low 16 bits of each cell against BoardPlatformRawChipId(), and 434
# (0x1b2) is SM7225 - both appear, as `<434 0x10000>, <459 0x10000>`, on the lagoon
# trees in the boot image's DTB slot and on every Lagoon dtbo entry.
GAUGUIN_PLATFORM_IDS = (434, 459)

# The device's DRAM, as the bootloader tells the OS it is: (base, size) pairs.
#
# Not a guess, and not a property of the tree we build. ABL reads the RAM
# partition table out of SMEM and writes it into /memory itself - and it does
# find our node, because FdtPathOffset() matches a path component that omits the
# unit address (its own doc comment says so, and FdtNodeNameEq() implements the
# rule: "memory" matches "memory@80000000" as long as the query has no '@'). The
# first partition *replaces* the reg and the rest are appended, which is why
# every tree in the boot image declares `memory { reg = <0 0 0 0>; }` and none of
# them lists any memory at all.
#
# Measured from the running phone: /proc/device-tree/memory/reg, dumped
# 2026-09-22 with the device on Android - i.e. after ABL had patched it, so this
# is what the hardware actually has rather than what a tree claims. Note the
# 0x4500000 hole between bank 0 and bank 1: it is inside the physical part and
# inside no partition, and something placed there is not placeable. That is not
# hypothetical - a ramoops node copied from the boot image's APQ 8016 tree sat at
# 0xbff00000, which is in that hole; see docs/07.
DRAM = (
    (0x0000000080000000, 0x000000003BB00000),   # bank 0, ends at 0xbbb00000
    (0x00000000C0000000, 0x00000000C0000000),   # bank 1
    (0x0000000180000000, 0x0000000100000000),   # bank 2
)


def in_dram(addr, size):
    """Whether [addr, addr+size) is inside one RAM partition.

    Whole-region containment, not overlap: a region that straddles the end of a
    bank is as unusable as one that starts outside, and harder to notice.
    """
    return any(addr >= base and addr + size <= base + size_
               for base, size_ in DRAM)


# The parts of that RAM this phone's own firmware has taken: every reserved-memory
# region the vendor tree declares `no-map`, plus the two display regions that are
# not marked no-map but are the bootloader's live scanout buffer.
#
# Being inside a DRAM partition is necessary and not sufficient, and the difference
# is not academic - it is the second half of the same bug. Bank 1 starts at
# 0xc0000000 and the phone's tree removes the first 0x7b00000 of it, so an address
# picked from the *mainline* tree's notion of what is reserved (sm6350.dtsi says
# 0x3900000, and that is where the 0xc3900000 boundary in docs/07 came from) lands
# in DRAM and in firmware memory at the same time. What `removed-dma-pool` means is
# that the bootloader has handed that DRAM to something that is not Linux; the
# regions in this table are also what keeps the modem, the ADSP and the CDSP alive,
# which is why touching one is not a performance question.
#
# Measured from the running phone's /proc/device-tree/reserved-memory, dumped
# 2026-09-22 with the device on Android, so these are the regions the platform
# actually ran with rather than a reading of some tree's source.
PHONE_RESERVED = (
    (0x80000000, 0x00600000, "hyp_region"),
    (0x80700000, 0x00260000, "xbl_aop_mem"),
    (0x80860000, 0x00020000, "qcom,cmd-db"),
    (0x808ff000, 0x00001000, "sec_apps_region"),
    (0x80900000, 0x00200000, "smem"),
    (0x80b00000, 0x01e00000, "cdsp_sec_regions"),
    (0x86000000, 0x00500000, "camera_region"),
    (0x86500000, 0x00500000, "pil_npu_region"),
    (0x86a00000, 0x00500000, "pil_video_region"),
    (0x86f00000, 0x01e00000, "cdsp_regions"),
    (0x88d00000, 0x02800000, "pil_adsp_region"),
    (0x8b500000, 0x00200000, "wlan_fw_region"),
    (0x8b700000, 0x00010000, "ipa_fw_region"),
    (0x8b710000, 0x00005400, "ipa_gsi_region"),
    (0x8b715400, 0x00002000, "gpu_region"),
    (0x8b800000, 0x0f800000, "modem_region"),
    (0xac000000, 0x01000000, "disp_rdump_region@ac000000"),
    (0xc0000000, 0x07b00000, "removed_region"),
)

# cont_splash_region and disp_rdump_region@0xa0000000 name the same range, which is
# the bootloader's live scanout buffer: the panel is already scanning out of it when
# the kernel starts, so a payload that wants a console without a working display
# driver adopts it rather than avoiding it. That is the one deliberate exception to
# the rule above, and it is an exception because the region is *ours* to use -
# nothing else on the platform is drawing from it once Linux is running.
SPLASH_BUFFER = (0xA0000000, 0x2300000)


def reserved_overlaps(addr, size, skip=()):
    """Which of the phone's carveouts [addr, addr+size) lands in, by name.

    Overlap rather than containment, the opposite of `in_dram`: the question here
    is whether we touch memory that belongs to someone else, and one byte inside
    is as bad as all of it. `skip` holds the regions we have decided to adopt.
    """
    return [name for base, size_, name in PHONE_RESERVED
            if name not in skip and addr < base + size_ and base < addr + size]
