#!/usr/bin/env python3
"""Generate the gauguin board DTS from the Fairphone 4 one.

SM7225 support in mainline is written around the Fairphone 4, which is the same
silicon and the same PMIC set. The SoC-level infrastructure in that file (the
RPMh regulator tree, clock tree, UFS and USB plumbing) applies to gauguin
unchanged — verified against this phone's own device tree: its UFS supplies
resolve to the very same regulators FP4 names (`ldoe7`, `ldoa12`, `ldoa18`,
`ldoa22` -> `vreg_l7e`, `vreg_l12a`, `vreg_l18a`, `vreg_l22a`).

What differs is the board: identity, screen, and touch. This script clones the
FP4 file and rewrites exactly those parts, so the first boot exercises the SoC
support rather than a hand-written guess at it.

Usage:  make_gauguin_dts.py <linux-tree>
"""
import os
import re
import sys

SRC = "sm7225-fairphone-fp4.dts"
DST = "sm7225-xiaomi-gauguin.dts"
MAKEFILE = "Makefile"

# Identity: gauguin's own values, read from this phone's /proc/device-tree.
#   qcom,msm-id   = 000001b2 00010000 000001cb 00010000  ->  <434 0x10000>, <459 0x10000>
#   qcom,board-id = 00000023 00000000                    ->  <35 0>
# Note the msm-id list is byte-identical to the one the Fairphone 4 file already
# carries, which is the strongest possible confirmation that these are the same
# silicon and the same bootloader-selected board family.
IDENTITY = '''	model = "Xiaomi Redmi Note 9 Pro 5G (gauguin)";
	compatible = "xiaomi,gauguin", "qcom,sm7225";
	chassis-type = "handset";

	/* required for bootloader to select correct board */
	qcom,msm-id = <434 0x10000>, <459 0x10000>;
	qcom,board-id = <35 0>;
'''

# Panel framebuffer. XBL has already initialised the panel by the time Linux
# runs and leaves its framebuffer at the "Display Reserved" region declared in
# uefiplat.cfg (0xA0000000, 36 MB). Simplest reliable console for a first boot:
# keep using it, rather than re-initialising DSI from a driver whose timings we
# have not verified yet.
FRAMEBUFFER = '''		framebuffer0: framebuffer@a0000000 {
			compatible = "simple-framebuffer";
			reg = <0 0xa0000000 0 (2400 * 1080 * 4)>;
			width = <1080>;
			height = <2400>;
			stride = <(1080 * 4)>;
			format = "a8r8g8b8";
		};
'''

# Where our kernel's log has to land. The SoC file `sm6350.dtsi` carries
# Fairphone's own ramoops carveout at 0xffc00000; this phone's base tree - the
# one ABL hands to the vendor kernel - puts it at 0xbff00000 instead, 1 MiB at
# the top of the first DRAM bank, with record/console/ftrace all 0x20000 and no
# pmsg and no ECC. Android reads that region back after a failed bring-up and
# parses it by position, so both the address and the geometry have to be right
# or the log is unreadable exactly when it is the only evidence there is. The
# cmdline carries the same values; see docs/07 and docs/p1-cmdline.txt.
RAMOOPS = '''
		/* See the delete-node note at the bottom of this file. */
		ramoops@bff00000 {
			compatible = "ramoops";
			reg = <0 0xbff00000 0 0x100000>;
			record-size = <0x20000>;
			console-size = <0x20000>;
			ftrace-size = <0x20000>;
			no-map;
		};'''

TAIL = '''
/*
 * gauguin overrides.
 *
 * MDSS/DSI is left disabled for the first bring-up: the bootloader has already
 * brought the panel up and the simple-framebuffer above rides on its output.
 * Re-enabling DSI needs the panel timings from the phone's own device tree,
 * which are recorded in docs/05-hardware-map.md.
 */
&mdss {
	status = "disabled";
};

/*
 * The inherited node from sm6350.dtsi is Fairphone's and sits at 0xffc00000
 * with its own record geometry. This phone's own base tree - the one ABL hands
 * to the vendor kernel - puts ramoops at 0xbff00000 instead: 1 MiB at the top
 * of the first DRAM bank, ending exactly where bank 1 begins, with
 * record/console/ftrace all 0x20000 and no pmsg and no ECC.
 *
 * That address and geometry matter because Android is the reader: after a
 * failed bring-up the log is recovered by warm-rebooting into Android and
 * reading /sys/fs/pstore/console-ramoops-0, and the vendor kernel parses that
 * region by position. A different console_size puts our console record where
 * Android looks for a dmesg record, and an ECC size changes the record format
 * outright - either way the log becomes unreadable, which is the one thing a
 * payload with no screen output cannot afford. The build's cmdline carries the
 * same values, so it does not matter which of the two wins.
 *
 * The inherited node is deleted rather than overridden by label: overriding
 * would leave the old name on the new address, so the tree would read
 * "ramoops@ffc00000" at 0xbff00000 for anyone looking at it afterwards.
 */
/delete-node/ &ramoops;
'''


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    dtsdir = os.path.join(argv[1], "arch/arm64/boot/dts/qcom")
    if not os.path.isdir(dtsdir):
        sys.exit(f"not a kernel tree: {argv[1]}")

    text = open(os.path.join(dtsdir, SRC)).read()

    # 1. identity
    new, n = re.subn(
        r'\tmodel = "Fairphone 4";\n'
        r'\tcompatible = "fairphone,fp4", "qcom,sm7225";\n'
        r'\tchassis-type = "handset";\n\n'
        r'\t/\* required for bootloader to select correct board \*/\n'
        r'\tqcom,msm-id = <434 0x10000>, <459 0x10000>;\n'
        r'\tqcom,board-id = <8 32>;\n',
        IDENTITY, text)
    if n != 1:
        sys.exit(f"identity block: expected 1 substitution, made {n}")
    text = new

    # 2. framebuffer geometry. Upstream spells the node framebuffer@a000000
    #    while the reg inside is 0xa0000000; give it the address it really has.
    new, n = re.subn(
        r'\t\tframebuffer0: framebuffer@a000000 \{\n'
        r'\t\t\tcompatible = "simple-framebuffer";\n'
        r'\t\t\treg = <0 0xa0000000 0 \(2340 \* 1080 \* 4\)>;\n'
        r'\t\t\twidth = <1080>;\n'
        r'\t\t\theight = <2340>;\n'
        r'\t\t\tstride = <\(1080 \* 4\)>;\n'
        r'\t\t\tformat = "a8r8g8b8";\n'
        r'\t\t\};\n',
        FRAMEBUFFER, text)
    if n != 1:
        sys.exit(f"framebuffer block: expected 1 substitution, made {n}")
    text = new

    # 3. the pstore carveout. Anchored on the rmtfs node above it, which is the
    #    last one in the board's reserved-memory block, so the insert lands
    #    there rather than on whichever node happens to look similar.
    anchor = (
        '\t\t\tqcom,client-id = <1>;\n'
        '\t\t\tqcom,vmid = <QCOM_SCM_VMID_MSS_MSA>;\n'
        '\t\t};\n')
    new, n = re.subn(re.escape(anchor), anchor + RAMOOPS + "\n", text)
    if n != 1:
        sys.exit(f"rmtfs anchor: expected 1 substitution, made {n}")
    text = new

    text += TAIL

    out = os.path.join(dtsdir, DST)
    with open(out, "w") as fh:
        fh.write(text)
    print(f"wrote {out}")

    mk = os.path.join(dtsdir, MAKEFILE)
    lines = open(mk).read().splitlines(True)
    entry = "dtb-$(CONFIG_ARCH_QCOM) += sm7225-xiaomi-gauguin.dtb\n"
    if entry in lines:
        print("Makefile already lists the new dtb")
        return
    for i, line in enumerate(lines):
        if line.startswith("dtb-$(CONFIG_ARCH_QCOM)") and "sm7225-fairphone-fp4.dtb" in line:
            lines.insert(i + 1, entry)
            open(mk, "w").writelines(lines)
            print(f"added {entry.strip()} to {mk}")
            return
    sys.exit("could not find the sm7225-fairphone-fp4.dtb Makefile line")


if __name__ == "__main__":
    main(sys.argv)
