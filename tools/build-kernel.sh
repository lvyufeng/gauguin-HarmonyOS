#!/usr/bin/env bash
#
# P1: build a mainline Linux kernel and an Android boot image for gauguin.
#
# The point of P1 is to turn the hardware description into something measured.
# Mainline already supports this silicon (SM7225 shares sm6350.dtsi with the
# Fairphone 4, same msm-id), so booting it gives us a verified map of clocks,
# regulators, GPIOs, panel timings and MMIO - which is exactly the input the
# UEFI platform package in P2 has to encode.
#
# Nothing is written to the device: the result is loaded with `fastboot boot`.
set -euo pipefail

TREE=${TREE:-$(cd "$(dirname "$0")/.." && pwd)/work/linux}
REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=${OUT:-$REPO/work/out}
JOBS=${JOBS:-$(nproc)}
CROSS=aarch64-linux-gnu-

cd "$TREE"
mkdir -p "$OUT"

# Install the board DTS. It is tracked in the repo (`dts/`) rather than living
# only in the build tree, because the kernel tree itself is not versioned here.
install_dts() {
    local dtsdir=arch/arm64/boot/dts/qcom
    local name=sm7225-xiaomi-gauguin
    cp -v "$REPO/dts/$name.dts" "$dtsdir/$name.dts"
    local entry="dtb-\$(CONFIG_ARCH_QCOM) += $name.dtb"
    if ! grep -qxF "$entry" "$dtsdir/Makefile"; then
        # place it next to the Fairphone 4 entry, the file it derives from
        sed -i "\|^dtb-\$(CONFIG_ARCH_QCOM) += sm7225-fairphone-fp4.dtb$|a $entry" "$dtsdir/Makefile"
        echo "registered $name.dtb in $dtsdir/Makefile"
    fi
}

if [ ! -f .config ]; then
    echo "== no .config: starting from defconfig"
    make ARCH=arm64 CROSS_COMPILE=$CROSS defconfig
fi

# Every option below goes through this, and the reason is a trap worth naming.
#
# `scripts/config` upper-cases the symbol names it is given unless `--keep-case`
# is passed - and a symbol it has upper-cased matches no Kconfig entry, so
# `--enable FONT_TER16x32` writes `CONFIG_FONT_TER16X32=y`, Kconfig drops it
# without a word, and the kernel is built without the option while the file and
# the command line both say it was asked for. That is exactly what happened to
# the two console fonts here, and the only visible symptom would have been a
# photographed screen too small to read. Passing --keep-case for all of them
# costs nothing, since an all-capital name is unaffected.
cfg() { command scripts/config --keep-case "$@"; }

# Applied unconditionally, not only when the .config is first created.
#
# It used to be inside the `if` above, which made the list below write-once: a
# line added here after the tree had been configured once never reached the
# kernel, and nothing said so. `scripts/config` only touches .config when a
# value actually changes, so re-applying is free and the build stays incremental
# when none of them changed.
echo "== applying this board's options"
# SoC support: the gauguin platform blocks. These are the drivers the device
# tree references; without them the DTB will not probe.
cfg --enable ARCH_QCOM
cfg --enable PINCTRL_SM6350
cfg --enable CLK_QCOM
cfg --enable SM_GCC_6350
cfg --enable SM_DISPCC_6350
cfg --enable SM_GPUCC_6350
cfg --enable SM_CAMCC_6350
cfg --enable QCOM_RPMH
cfg --enable REGULATOR_QCOM_RPMH
cfg --enable INTERCONNECT_QCOM
cfg --enable INTERCONNECT_QCOM_RPMH
cfg --enable INTERCONNECT_QCOM_SM6350
cfg --enable QCOM_LLCC
cfg --enable QCOM_GDSC
cfg --enable QCOM_SMD_RPM
cfg --enable QCOM_SMEM
cfg --enable QCOM_SCM
cfg --enable QCOM_RPMPD
cfg --enable ARM_SMMU
cfg --enable ARM_SMMU_QCOM
cfg --enable QCOM_IOMMU

# Storage
cfg --enable SCSI_UFS_DWC_TC_PLATFORM
cfg --enable SCSI_UFS_QCOM
cfg --enable PHY_QCOM_QMP
cfg --enable PHY_QCOM_QMP_UFS
cfg --enable PHY_QCOM_SNPS_FEMTO_V2
cfg --enable MMC_SDHCI_MSM

# USB, including a serial gadget so a console can reach the host without
# needing anyone to read the phone's screen.
cfg --enable USB_DWC3
cfg --enable USB_DWC3_QCOM
cfg --enable USB_GADGET
cfg --enable USB_CONFIGFS
cfg --enable USB_CONFIGFS_SERIAL
cfg --module USB_CONFIGFS_ACM
cfg --enable USB_LIBCOMPOSITE
cfg --enable NOP_USB_XCEIV

# Console: the phone has no exposed UART, so the display is the console.
# A 1080px-wide panel with the default 8x16 font is 135 columns of tiny text;
# TER16x32 halves that to something a photograph can actually resolve. Both
# halves matter and neither is optional: the simple-framebuffer node in the
# board DTS is what gives simpledrm something to bind to, DRM_FBDEV_EMULATION
# is what turns that into an fbdev device, and FRAMEBUFFER_CONSOLE is what
# draws tty0 on it. `console=tty0` last on the command line is then what makes
# /dev/console - and so userspace's stdout - land on the panel instead of on
# the UART nobody can reach.
cfg --enable SERIAL_QCOM_GENI
cfg --enable SERIAL_QCOM_GENI_CONSOLE
cfg --enable DRM
cfg --enable DRM_SIMPLEDRM
cfg --enable DRM_FBDEV_EMULATION
cfg --enable FB
cfg --enable FRAMEBUFFER_CONSOLE
cfg --enable FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER
cfg --enable VT
cfg --enable VT_CONSOLE
cfg --enable FONTS
cfg --enable FONT_8x16
cfg --enable FONT_TER16x32
cfg --enable LOGO
cfg --disable LOGO_ASCII

# The log channel, which is the other half of "the display is the console": the
# console output is what makes a pstore log worth having, and pstore is what
# survives a boot too broken to draw on the panel. `console-ramoops-0` is the
# record Android reads back, and it is written raw - no record header - so the
# first line of the file is the kernel banner and `head -1` is a valid identity
# check. The geometry in the board DTS and the ramoops.* cmdline options is the
# vendor's, so the vendor kernel parses the same region by position.
#
# PANIC_TIMEOUT is duplicated from `panic=10` on the command line deliberately:
# the recovery path needs the phone to restart itself, and it must not depend on
# a cmdline that a future edit could drop.
cfg --enable PSTORE
cfg --enable PSTORE_CONSOLE
cfg --enable PSTORE_RAM
cfg --disable PSTORE_PMSG
cfg --set-val PANIC_TIMEOUT 10

# That log is in RAM, and the only way to read it is for the phone to restart
# itself, so whether it gets read at all depends on what the kernel does about a
# failure. Two classes of failure were ending in a kernel that neither printed
# nor rebooted:
#
#   * an oops - a wrong property in our DTB causing a NULL dereference in a
#     probe. The kernel survives that by default, and surviving is the worst
#     outcome available here: it leaves a half-initialised system with no console
#     (simpledrm binds late, long after early setup) and no reboot, so the ring
#     is never read and the session learns nothing.
#   * a spin in a probe waiting for a clock or regulator that never comes ready -
#     the classic Qualcomm bring-up failure. The softlockup detector catches it,
#     but on its own it only prints a stack trace to a console that may not
#     exist. BOOTPARAM_SOFTLOCKUP_PANIC is what turns the detection into the
#     reboot that flushes the ring.
#
# Both now end in the same sequence the runbook is built around: panic, panic=10,
# reboot=panic_warm, PSCI SYSTEM_RESET2, Android comes up, read the ring. A
# kernel that panics is strictly more useful than one that limps, on a device
# whose entire diagnosis path is "did it say anything before it stopped".
cfg --enable PANIC_ON_OOPS
cfg --enable SOFTLOCKUP_DETECTOR
cfg --enable BOOTPARAM_SOFTLOCKUP_PANIC

# Filesystems / initramfs
cfg --enable BLK_DEV_INITRD
cfg --enable RD_GZIP
cfg --enable DEVTMPFS
cfg --enable DEVTMPFS_MOUNT
cfg --enable TMPFS
cfg --enable EXT4_FS
cfg --enable PROC_FS
cfg --enable SYSFS
cfg --enable DEBUG_FS
cfg --enable MAGIC_SYSRQ

# Quality of life
cfg --disable DEBUG_INFO
cfg --disable MODULE_SIG
cfg --disable MODULE_SIG_ALL
cfg --enable IKCONFIG
cfg --enable IKCONFIG_PROC

make ARCH=arm64 CROSS_COMPILE=$CROSS olddefconfig

# The list above is a claim about the kernel that is about to be built, and a
# claim that can silently be false: every option here depends on others, and
# `olddefconfig` drops what it cannot satisfy without saying so. Ask the
# resulting .config rather than trusting the list, because the failure mode is a
# boot with no console and no way to see why.
#
# Only the options whose absence would make a device session unreadable are
# checked: the console chain, the storage the boot is meant to prove, the pstore
# log, and the panic behaviour that decides whether the log is ever read back. A
# missing USB or clock driver costs a diagnosis, not a session.
missing=""
for opt in DRM DRM_SIMPLEDRM DRM_FBDEV_EMULATION FB FRAMEBUFFER_CONSOLE VT \
           VT_CONSOLE FONTS FONT_8x16 FONT_TER16x32 \
           SCSI_UFS_QCOM PHY_QCOM_QMP_UFS \
           PSTORE PSTORE_CONSOLE PSTORE_RAM \
           PANIC_ON_OOPS SOFTLOCKUP_DETECTOR BOOTPARAM_SOFTLOCKUP_PANIC; do
    grep -qx "CONFIG_$opt=y" .config || missing="$missing $opt"
done
if [ -n "$missing" ]; then
    echo "!! these options did not survive olddefconfig:$missing" >&2
    echo "!! the console or the log channel would be missing; fix before flashing" >&2
    exit 1
fi
echo "   ok: console (simpledrm+fbcon+TER16x32), UFS, pstore, and panic-on-failure"

install_dts

echo "== building kernel (-j$JOBS)"
make ARCH=arm64 CROSS_COMPILE=$CROSS -j"$JOBS" Image.gz
make ARCH=arm64 CROSS_COMPILE=$CROSS -j"$JOBS" qcom/sm7225-xiaomi-gauguin.dtb

cp -v arch/arm64/boot/Image.gz "$OUT/"
cp -v arch/arm64/boot/dts/qcom/sm7225-xiaomi-gauguin.dtb "$OUT/"

echo "== done"
ls -l "$OUT"
