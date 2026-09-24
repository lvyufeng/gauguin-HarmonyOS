/*
 * gauguin (SM7225) DSDT for the Windows-on-ARM port.
 *
 * This is the only ACPI table the platform has to author. APIC, FACP and GTDT
 * come from Silicon/Qualcomm/Moorea unmodified, because every value they carry
 * is SoC geometry and all of it was checked against gauguin's own device tree -
 * see docs/07-uefi-platform.md, "which SoC's ACPI tables gauguin can use".
 *
 * The device nodes below are the Bitra-family form, taken from
 * Platforms/Realme/bitra/DSDT.aml, with each load-bearing value re-checked
 * against Resources/DTBs/gauguin.dts rather than inherited:
 *
 *   UFS0 window      0x01D84000 + 0x14000   dts ufshc std 0x1D84000 + ice 0x1D90000
 *   UFS0 interrupt   297 (0x129)            dts SPI 265, and INTID = 32 + 265
 *   URS0 window      0x0A600000 + 0xFFFFF   dts dwc3 core 0xA600000, wrapper 0xA6F8800
 *   USB core irq     165 (0xA5)             dts usb@a600000  SPI 133, INTID = 32 + 133
 *   USB pwr_event    162 (0xA2)             dts interrupts-extended  SPI 130
 *   USB hs_phy_irq   163 (0xA3)             dts interrupts-extended  SPI 131
 *
 * Three interrupts bitra's node also carries are deliberately absent: GSIs
 * 526, 527 and 529, which the dts reaches through the PDC (phandle 0x62) on
 * pins 14, 15 and 17. Those three are the dp_hs / dm_hs / ss PHY wake lines.
 * They are omitted because their GSI encoding is undetermined - 512 + pin and
 * "the INTID the PDC maps the pin to" (480 + pin, here 494/495/497) are both
 * consistent with the evidence and the two readings differ. They are wake
 * interrupts, not the controller's own, so leaving them out does not affect
 * whether the controller starts; putting in a wrong number would. See the
 * "USB PHY wake interrupts" note in docs/07-uefi-platform.md.
 *
 * The CPU devices are Moorea's Silicon/Qualcomm/Moorea/DSDT_Minimal.asl: eight
 * ACPI0007 processors with _UID 0-7, matching the Processor UID and MPIDR that
 * Moorea's APIC GICC entries carry (0x0, 0x100 ... 0x700), which is what
 * gauguin's device tree states too. No _LPI: gauguin's low-power idle
 * parameters have not been derived, and an unverified _LPI is worse than none.
 *
 * P3 also asks for I2C, SPI, GPIO, buttons and thermal zones, and none of them
 * is here. That is not an omission - it is measured, and the measurement is the
 * reason. Every one of those nodes needs a `_HID` (and for the GPIO and I2C
 * blocks a `_DSM` whose contract is documented nowhere in this tree), and the
 * device tree carries no ACPI name: it has registers and pins, which are the
 * half that is knowable. Across the 36 reference DSDTs in Silicium-ACPI the
 * same block at the same address carries a different `_HID` on every SoC - the
 * TLMM window 0xF100000 is QCOM1A0C on vili and lemonade, QCOM0A0C on lisa and
 * a52sxq, QCOM250C on alioth and QCOM090C on renoir - and the SPMI and both
 * TSENS windows are described by none of the 36. No Bitra-family reference
 * exists: Platforms/Realme/bitra/DSDT.aml, which this file's form comes from,
 * has the same five devices this one has and nothing more.
 *
 * The consequence of copying a name from another SoC is specific and bad. A
 * node whose `_HID` no driver claims does not fail, warn or fall back; it is
 * absent from Device Manager and the hardware behind it is simply not there.
 * That is worse than a missing node, which at least reads as missing.
 *
 * But the lesson is not that this board has a hidden name. ACPI's `_HID` is not
 * a hardware fact - it is a string this port chooses, and the corpus choosing
 * differently on every SoC is evidence the choice is free. What constrains it is
 * the driver: a device binds to the name its .inf lists and to nothing else. So
 * the tables here are written *to a driver*, not *to the SoC*, and the order of
 * work is to adopt a Windows driver set and then name every block after it.
 * tools/acpi-hid-census.py reproduces the measurement above and, given a driver
 * set, reports which of gauguin's twelve blocks it covers - and which SoC's set
 * it is, read off the names it answers to. Run it before adding any node here.
 */
DefinitionBlock ("DSDT.aml", "DSDT", 2, "QCOMM ", "SM7225 ", 0x00000003)
{
    Scope (_SB)
    {
        Name (PSUB, "MTP07225")
        Name (EMUL, 0xFFFFFFFF)
        Device (UFS0)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Name (_HID, "QCOM24A5")  // _HID: Hardware ID
            Alias (^EMUL, EMUL)
            Name (_UID, Zero)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x01D84000,         // Address Base
                        0x00014000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000129,
                    }
                })
                Return (RBUF) /* \_SB_.UFS0._CRS.RBUF */
            }

            Device (DEV0)
            {
                Method (_ADR, 0, NotSerialized)  // _ADR: Address
                {
                    Return (0x08)
                }

                Method (_RMV, 0, NotSerialized)  // _RMV: Removal Status
                {
                    Return (Zero)
                }
            }
        }

        Name (QUFN, Zero)
        Name (DPP0, Buffer (One)
        {
             0x00                                             // .
        })
        Name (DPP1, Buffer (One)
        {
             0x00                                             // .
        })
        Name (MPP0, Buffer (One)
        {
             0x00                                             // .
        })
        Name (MPP1, Buffer (One)
        {
             0x00                                             // .
        })
        Name (HPDB, Zero)
        Name (HPDS, Buffer (One)
        {
             0x00                                             // .
        })
        Name (PINA, Zero)
        Name (DPPN, 0x0D)
        Name (CCST, Buffer (One)
        {
             0x02                                             // .
        })
        Name (PORT, Buffer (One)
        {
             0x02                                             // .
        })
        Name (HIRQ, Buffer (One)
        {
             0x00                                             // .
        })
        Name (HSFL, Buffer (One)
        {
             0x00                                             // .
        })
        Name (USBC, Buffer (One)
        {
             0x0B                                             // .
        })
        Name (MUXC, Buffer (One)
        {
             0x00                                             // .
        })
        Device (URS0)
        {
            Method (URSI, 0, NotSerialized)
            {
                If ((QUFN == Zero))
                {
                    Return ("QCOM0497")
                }
                Else
                {
                    Return ("QCOM0498")
                }
            }

            Name (_HID, "QCOM0497")  // _HID: Hardware ID
            Name (_CID, "PNP0CA1")  // _CID: Compatible ID
            Alias (PSUB, _SUB)
            Name (_UID, Zero)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Name (_CRS, ResourceTemplate ()  // _CRS: Current Resource Settings
            {
                Memory32Fixed (ReadWrite,
                    0x0A600000,         // Address Base
                    0x000FFFFF,         // Address Length
                    )
            })
            Device (USB0)
            {
                Name (_ADR, Zero)  // _ADR: Address
                Name (_S0W, 0x03)  // _S0W: S0 Device Wake State
                Name (_PLD, Package (0x01)  // _PLD: Physical Location of Device
                {
                    ToPLD (
                        PLD_Revision           = 0x2,
                        PLD_IgnoreColor        = 0x1,
                        PLD_Red                = 0x0,
                        PLD_Green              = 0x0,
                        PLD_Blue               = 0x0,
                        PLD_Width              = 0x0,
                        PLD_Height             = 0x0,
                        PLD_UserVisible        = 0x1,
                        PLD_Dock               = 0x0,
                        PLD_Lid                = 0x0,
                        PLD_Panel              = "BACK",
                        PLD_VerticalPosition   = "CENTER",
                        PLD_HorizontalPosition = "LEFT",
                        PLD_Shape              = "VERTICALRECTANGLE",
                        PLD_GroupOrientation   = 0x0,
                        PLD_GroupToken         = 0x0,
                        PLD_GroupPosition      = 0x0,
                        PLD_Bay                = 0x0,
                        PLD_Ejectable          = 0x0,
                        PLD_EjectRequired      = 0x0,
                        PLD_CabinetNumber      = 0x0,
                        PLD_CardCageNumber     = 0x0,
                        PLD_Reference          = 0x0,
                        PLD_Rotation           = 0x0,
                        PLD_Order              = 0x0,
                        PLD_VerticalOffset     = 0xFFFF,
                        PLD_HorizontalOffset   = 0xFFFF)

                })
                Name (_UPC, Package (0x04)  // _UPC: USB Port Capabilities
                {
                    One,
                    0x09,
                    Zero,
                    Zero
                })
                Name (_CRS, ResourceTemplate ()  // _CRS: Current Resource Settings
                {
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000A5,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, SharedAndWake, ,, )
                    {
                        0x000000A2,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, SharedAndWake, ,, )
                    {
                        0x000000A3,
                    }
                })
                Method (_STA, 0, NotSerialized)  // _STA: Status
                {
                    Return (0x0F)
                }

                Method (DPM0, 1, NotSerialized)
                {
                    DPP0 = Arg0
                }

                Method (CCVL, 0, NotSerialized)
                {
                    Return (CCST) /* \_SB_.CCST */
                }

                Method (HSEN, 0, NotSerialized)
                {
                    Return (HSFL) /* \_SB_.HSFL */
                }

                Method (_DSM, 4, Serialized)  // _DSM: Device-Specific Method
                {
                    Switch (ToBuffer (Arg0))
                    {
                        Case (ToUUID ("ce2ee385-00e6-48cb-9f05-2edb927c4899") /* USB Controller */){                            Switch (ToInteger (Arg2))
                            {
                                Case (Zero)
                                {
                                    Switch (ToInteger (Arg1))
                                    {
                                        Case (Zero)
                                        {
                                            Return (Buffer (One)
                                            {
                                                 0x1D                                             // .
                                            })
                                            Break
                                        }
                                        Default
                                        {
                                            Return (Buffer (One)
                                            {
                                                 0x01                                             // .
                                            })
                                            Break
                                        }

                                    }

                                    Return (Buffer (One)
                                    {
                                         0x00                                             // .
                                    })
                                    Break
                                }
                                Case (0x02)
                                {
                                    Return (Zero)
                                    Break
                                }
                                Case (0x03)
                                {
                                    Return (Zero)
                                    Break
                                }
                                Case (0x04)
                                {
                                    Return (0x02)
                                    Break
                                }
                                Default
                                {
                                    Return (Buffer (One)
                                    {
                                         0x00                                             // .
                                    })
                                    Break
                                }

                            }
                        }
                        Default
                        {
                            Return (Buffer (One)
                            {
                                 0x00                                             // .
                            })
                            Break
                        }

                    }
                }

                Method (PHYC, 0, NotSerialized)
                {
                    Name (CFG0, Package (0x00){})
                    Return (CFG0) /* \_SB_.URS0.USB0.PHYC.CFG0 */
                }
            }

            Device (UFN0)
            {
                Name (_ADR, One)  // _ADR: Address
                Name (_S0W, 0x03)  // _S0W: S0 Device Wake State
                Name (_PLD, Package (0x01)  // _PLD: Physical Location of Device
                {
                    ToPLD (
                        PLD_Revision           = 0x2,
                        PLD_IgnoreColor        = 0x1,
                        PLD_Red                = 0x0,
                        PLD_Green              = 0x0,
                        PLD_Blue               = 0x0,
                        PLD_Width              = 0x0,
                        PLD_Height             = 0x0,
                        PLD_UserVisible        = 0x1,
                        PLD_Dock               = 0x0,
                        PLD_Lid                = 0x0,
                        PLD_Panel              = "BACK",
                        PLD_VerticalPosition   = "CENTER",
                        PLD_HorizontalPosition = "LEFT",
                        PLD_Shape              = "VERTICALRECTANGLE",
                        PLD_GroupOrientation   = 0x0,
                        PLD_GroupToken         = 0x0,
                        PLD_GroupPosition      = 0x0,
                        PLD_Bay                = 0x0,
                        PLD_Ejectable          = 0x0,
                        PLD_EjectRequired      = 0x0,
                        PLD_CabinetNumber      = 0x0,
                        PLD_CardCageNumber     = 0x0,
                        PLD_Reference          = 0x0,
                        PLD_Rotation           = 0x0,
                        PLD_Order              = 0x0,
                        PLD_VerticalOffset     = 0xFFFF,
                        PLD_HorizontalOffset   = 0xFFFF)

                })
                Name (_UPC, Package (0x04)  // _UPC: USB Port Capabilities
                {
                    One,
                    0x09,
                    Zero,
                    Zero
                })
                Name (_CRS, ResourceTemplate ()  // _CRS: Current Resource Settings
                {
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000A5,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, SharedAndWake, ,, )
                    {
                        0x000000A3,
                    }
                })
                Method (CCVL, 0, NotSerialized)
                {
                    Return (CCST) /* \_SB_.CCST */
                }

                Method (_DSM, 4, Serialized)  // _DSM: Device-Specific Method
                {
                    Switch (ToBuffer (Arg0))
                    {
                        Case (ToUUID ("fe56cfeb-49d5-4378-a8a2-2978dbe54ad2") /* Unknown UUID */){                            Switch (ToInteger (Arg2))
                            {
                                Case (Zero)
                                {
                                    Switch (ToInteger (Arg1))
                                    {
                                        Case (Zero)
                                        {
                                            Return (Buffer (One)
                                            {
                                                 0x03                                             // .
                                            })
                                            Break
                                        }
                                        Default
                                        {
                                            Return (Buffer (One)
                                            {
                                                 0x01                                             // .
                                            })
                                            Break
                                        }

                                    }

                                    Return (Buffer (One)
                                    {
                                         0x00                                             // .
                                    })
                                    Break
                                }
                                Case (One)
                                {
                                    Return (0x20)
                                    Break
                                }
                                Default
                                {
                                    Return (Buffer (One)
                                    {
                                         0x00                                             // .
                                    })
                                    Break
                                }

                            }
                        }
                        Case (ToUUID ("18de299f-9476-4fc9-b43b-8aeb713ed751") /* Unknown UUID */){                            Switch (ToInteger (Arg2))
                            {
                                Case (Zero)
                                {
                                    Switch (ToInteger (Arg1))
                                    {
                                        Case (Zero)
                                        {
                                            Return (Buffer (One)
                                            {
                                                 0x03                                             // .
                                            })
                                            Break
                                        }
                                        Default
                                        {
                                            Return (Buffer (One)
                                            {
                                                 0x01                                             // .
                                            })
                                            Break
                                        }

                                    }

                                    Return (Buffer (One)
                                    {
                                         0x00                                             // .
                                    })
                                    Break
                                }
                                Case (One)
                                {
                                    Return (0x39)
                                    Break
                                }
                                Default
                                {
                                    Return (Buffer (One)
                                    {
                                         0x00                                             // .
                                    })
                                    Break
                                }

                            }
                        }
                        Default
                        {
                            Return (Buffer (One)
                            {
                                 0x00                                             // .
                            })
                            Break
                        }

                    }
                }

                Method (PHYC, 0, NotSerialized)
                {
                    Name (CFG0, Package (0x00){})
                    Return (CFG0) /* \_SB_.URS0.UFN0.PHYC.CFG0 */
                }
            }
        }

        Device (CPU0)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 0)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }

        Device (CPU1)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 1)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }

        Device (CPU2)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 2)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }

        Device (CPU3)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 3)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }

        Device (CPU4)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 4)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }

        Device (CPU5)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 5)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }

        Device (CPU6)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 6)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }

        Device (CPU7)
        {
            Name (_HID, "ACPI0007")
            Name (_UID, 7)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }
        }
    }
}
