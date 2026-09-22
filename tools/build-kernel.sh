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
    echo "== configuring"
    make ARCH=arm64 CROSS_COMPILE=$CROSS defconfig

    # SoC support: the gauguin platform blocks. These are the drivers the
    # device tree references; without them the DTB will not probe.
    scripts/config --enable ARCH_QCOM
    scripts/config --enable PINCTRL_SM6350
    scripts/config --enable CLK_QCOM
    scripts/config --enable SM_GCC_6350
    scripts/config --enable SM_DISPCC_6350
    scripts/config --enable SM_GPUCC_6350
    scripts/config --enable SM_CAMCC_6350
    scripts/config --enable QCOM_RPMH
    scripts/config --enable REGULATOR_QCOM_RPMH
    scripts/config --enable INTERCONNECT_QCOM
    scripts/config --enable INTERCONNECT_QCOM_RPMH
    scripts/config --enable INTERCONNECT_QCOM_SM6350
    scripts/config --enable QCOM_LLCC
    scripts/config --enable QCOM_GDSC
    scripts/config --enable QCOM_SMD_RPM
    scripts/config --enable QCOM_SMEM
    scripts/config --enable QCOM_SCM
    scripts/config --enable QCOM_RPMPD
    scripts/config --enable ARM_SMMU
    scripts/config --enable ARM_SMMU_QCOM
    scripts/config --enable QCOM_IOMMU

    # Storage
    scripts/config --enable SCSI_UFS_DWC_TC_PLATFORM
    scripts/config --enable SCSI_UFS_QCOM
    scripts/config --enable PHY_QCOM_QMP
    scripts/config --enable PHY_QCOM_QMP_UFS
    scripts/config --enable PHY_QCOM_SNPS_FEMTO_V2
    scripts/config --enable MMC_SDHCI_MSM

    # USB, including a serial gadget so a console can reach the host without
    # needing anyone to read the phone's screen.
    scripts/config --enable USB_DWC3
    scripts/config --enable USB_DWC3_QCOM
    scripts/config --enable USB_GADGET
    scripts/config --enable USB_CONFIGFS
    scripts/config --enable USB_CONFIGFS_SERIAL
    scripts/config --module USB_CONFIGFS_ACM
    scripts/config --enable USB_LIBCOMPOSITE
    scripts/config --enable NOP_USB_XCEIV

    # Console: the phone has no exposed UART, so the display is the console.
    # A 1080px-wide panel with the default 8x16 font is 135 columns of tiny
    # text; TER16x32 halves that to something a photograph can actually resolve.
    scripts/config --enable SERIAL_QCOM_GENI
    scripts/config --enable SERIAL_QCOM_GENI_CONSOLE
    scripts/config --enable DRM
    scripts/config --enable DRM_SIMPLEDRM
    scripts/config --enable FB
    scripts/config --enable FRAMEBUFFER_CONSOLE
    scripts/config --enable FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER
    scripts/config --enable FONTS
    scripts/config --enable FONT_8x16
    scripts/config --enable FONT_TER16x32
    scripts/config --enable LOGO
    scripts/config --disable LOGO_ASCII

    # Filesystems / initramfs
    scripts/config --enable BLK_DEV_INITRD
    scripts/config --enable RD_GZIP
    scripts/config --enable DEVTMPFS
    scripts/config --enable DEVTMPFS_MOUNT
    scripts/config --enable TMPFS
    scripts/config --enable EXT4_FS
    scripts/config --enable PROC_FS
    scripts/config --enable SYSFS
    scripts/config --enable DEBUG_FS
    scripts/config --enable MAGIC_SYSRQ

    # Quality of life
    scripts/config --disable DEBUG_INFO
    scripts/config --disable MODULE_SIG
    scripts/config --disable MODULE_SIG_ALL
    scripts/config --enable IKCONFIG
    scripts/config --enable IKCONFIG_PROC

    make ARCH=arm64 CROSS_COMPILE=$CROSS olddefconfig
fi

install_dts

echo "== building kernel (-j$JOBS)"
make ARCH=arm64 CROSS_COMPILE=$CROSS -j"$JOBS" Image.gz
make ARCH=arm64 CROSS_COMPILE=$CROSS -j"$JOBS" qcom/sm7225-xiaomi-gauguin.dtb

cp -v arch/arm64/boot/Image.gz "$OUT/"
cp -v arch/arm64/boot/dts/qcom/sm7225-xiaomi-gauguin.dtb "$OUT/"

echo "== done"
ls -l "$OUT"
