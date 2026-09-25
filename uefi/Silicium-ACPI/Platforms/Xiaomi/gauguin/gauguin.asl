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
 *   QGP0 window      0x00804000 + 0x50000   dts gpi-dma@800000 0x800000 + 0x60000, "gpi-top"
 *   QGP0 interrupt   276 (0x114)            dts interrupts <0x00 0xF4 0x04>, INTID = 32 + 244
 *   QGP1 window      0x00904000 + 0x50000   dts gpi-dma@900000 0x900000 + 0x60000, "gpi-top"
 *   QGP1 interrupt   677 (0x2A5)            dts interrupts <0x00 0x285 0x04>, INTID = 32 + 645
 *   MMU0 window      0x15000000 + 0x100000   dts apps-smmu@15000000 0x15000000 + 0x100000
 *   MMU0 interrupts  97, 127-150, 213-224, 347-377, 433-445   dts 81 SPIs, INTID = 32 + SPI
 *   MMU1 window      0x03D40000 + 0x20000    dts arm,smmu-kgsl@3d40000 base, family length
 *   MMU1 interrupts  261, 263, 396-403       dts 10 SPIs, INTID = 32 + SPI
 *
 * One id was inherited rather than checked, and Step 4.65 corrected it. URS0's
 * _HID read "QCOM0497" for several steps - bitra's, and bitra is family 04,
 * while gauguin is 0A and both of that family's tables say "QCOM0A8B". It was
 * invisible because the URS index moves with the generator group and 0497 sits
 * inside the same plausible-looking QCOM range. The node comment below carries
 * the three angles it was re-derived from. UFS0's QCOM24A5 is the contrast and
 * is correct: it is 19 of 19 tables across every family, so the driver set not
 * claiming it is the set's gap rather than this file's error.
 *
 * Three interrupts bitra's node also carries were left out of this table until
 * Step 4.62, as GSIs 526, 527 and 529: the dts reaches them through the PDC
 * (phandle 0x62) on pins 14, 15 and 17, and they are the dp_hs / dm_hs / ss PHY
 * wake lines. They were omitted because their GSI encoding was undetermined.
 * Two readings were on the table and both fit: 512 + pin, and "the INTID the
 * PDC maps the pin to" - 480 + pin, here 494/495/497.
 *
 * The device tree answers it, and the answer is 512 + pin:
 *
 *   qcom,pdc-ranges = <0x00 0x1E0 0x5E  0x5E 0x261 0x1F  0x7D 0x3F 0x01 ...>
 *
 * is a list of <first pin, GIC SPI, count> triples, so its first entry reads
 * "pins 0..93 map to SPI 480..573" - pins 14, 15 and 17 all fall in it. The dts
 * then names those three pins on the wake lines-
 *
 *   usb@a6f8800 interrupts-extended = <0x01 0x00 0x82 0x04  0x01 0x00 0x83 0x04
 *                                       0x62 0x0e 0x03  0x62 0x0f 0x03
 *                                       0x62 0x11 0x04>
 *   interrupt-names               = "pwr_event", "hs_phy_irq", "dp_hs_phy_irq",
 *                                   "dm_hs_phy_irq", "ss_phy_irq"
 *
 * - so dp_hs is pin 14, dm_hs pin 15 and ss pin 17, and INTID = 32 + SPI gives
 * 526, 527 and 529. The 480 + pin reading had stopped one step short: 480 + pin
 * is a GIC SPI number, and an ACPI Interrupt () resource carries the INTID.
 * That is the same slip the UFS pair above documents, on a different number.
 *
 * Two more things agree, and all three are independent. bitra - the same SM7225
 * line, and the source of this file's form - carries exactly 0x20E, 0x20F and
 * 0x211 in its USB0 _CRS, with Edge, Edge and Level triggers matching the type
 * cells 3, 3 and 4 the dts gives above. And 21 of the 66 tables in
 * Silicium-ACPI have a PM0x node at all; all 21 carry 0x201 = 513 for it, which
 * is the same arithmetic on the SPMI arbiter's own PDC pin 1 - gauguin's
 * spmi@c440000 says interrupts-extended = <0x62 0x01 0x04>. The trigger types
 * and the listing order below are bitra's, since it is the closest table that a
 * shipped Windows driver already binds.
 *
 * See the "USB PHY wake interrupts" note in docs/07-uefi-platform.md.
 *
 * The CPU devices are Moorea's Silicon/Qualcomm/Moorea/DSDT_Minimal.asl: eight
 * ACPI0007 processors with _UID 0-7, matching the Processor UID and MPIDR that
 * Moorea's APIC GICC entries carry (0x0, 0x100 ... 0x700), which is what
 * gauguin's device tree states too. No _LPI: gauguin's low-power idle
 * parameters have not been derived, and an unverified _LPI is worse than none.
 *
 * P3 also asks for I2C, SPI, buttons and thermal zones, and none of them is
 * here. What is here, as of Step 4.66, is the PMIC family - SPMI, PMIC and
 * PM01, Step 4.63 - the TLMM pin controller, GIO0, and its pin count, Step
 * 4.64 and 4.65 - and the Type-C controller UCS0, because unlike the others
 * their names turned out not to be a guess. The note above those nodes is how
 * each value was settled.
 *
 * UCS0 is the first node added here on the strength of the driver set's own
 * claim rather than of a sibling table: qcusbcucsi7280.inf binds
 * `ACPI\QCOM0AA4` by name, so the id is confirmed twice over - once by the
 * pinned set and once by lisa and a52sxq. PEP0, which UCS0 and URS0 both
 * depend on in lisa's table, is deliberately not ported with it; that node's
 * comment carries the measurement and the reason.
 *
 * The rest stays out for a measured reason. Every one of those nodes needs a
 * `_HID` (and for the I2C blocks a `_DSM` whose contract is documented nowhere
 * in this tree), and the device tree carries no ACPI name: it has registers and
 * pins, which are the half that is knowable.
 *
 * The PM01 census is what changed the picture, and it is worth stating in full.
 * Across the 21 tables in Silicium-ACPI that carry a PMIC-GPIO node at all - one
 * block, one function, every one of them with the same interrupt at 0x201 and
 * the same `_UID One` - the `_HID` takes nine different values, and the middle
 * byte is the SoC family and nothing else:
 *
 *   QCOM0269  02  caymanslm         QCOM1430  14  surya
 *   QCOM0530  05  mh2 cepheus       QCOM1A2D  1A  lemonade venus
 *                 nabu pipa vayu                   vili Lahaina
 *   QCOM0830  08  a52q miatoll      QCOM252D  25  alioth
 *   QCOM092D  09  renoir Cedros     QCOM0C2D  0C  Kailua Waipio
 *   QCOM0A2D  0A  lisa a52sxq
 *
 * gauguin's family byte is 0A, and that is measured rather than chosen: the
 * SC7280/Kodiak Windows driver set names 8 of gauguin's 10 blocks under it and 0
 * of 10 under every other byte. So this file's PMIC-GPIO node is `QCOM0A2D`, and
 * qcpmicgpio7280.inf claims exactly that. The same byte settles the TLMM block
 * that is still outstanding: the TLMM window 0xF100000 is QCOM1A0C on vili and
 * lemonade, QCOM0A0C on lisa and a52sxq, QCOM250C on alioth and QCOM090C on
 * renoir - and the 7280 set claims QCOM0A0C, for qcgpio7280.inf.
 *
 * So the earlier reading of this file's own header was half wrong. The choice of
 * `_HID` here is not free and not arbitrary; it is patterned, and the byte that
 * patterns it is the same byte that decides which driver set binds. Copying a
 * name from a table on another family is therefore not merely unverified, it is
 * predictably wrong.
 *
 * The consequence of getting it wrong is specific and bad. A node whose `_HID`
 * no driver claims does not fail, warn or fall back; it is absent from Device
 * Manager and the hardware behind it is simply not there. That is worse than a
 * missing node, which at least reads as missing. So every id added below was
 * checked against the driver set first - tools/acpi-hid-census.py
 * --drivers DIR --bind ID does exactly that and exits 1 on an unclaimed QCOM id.
 * Run it before adding any node here.
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

        /*
         * The Type-C controller. Of the nodes this table still owes P3 it is
         * the shortest to justify, and the best-attested: the node is 1,272
         * bytes and 46 lines in lisa, in a52sxq and in the SC7280 CRD's DSDT,
         * and the three are byte-identical to each other - zero differing
         * lines in either comparison. Two of them are iasl output for EDK2;
         * the third is creator `MSFT`, taken from a shipped Qualcomm UFS
         * firmware capsule (qcfirmware7280_UFS/qcfirmware7280v_UFS03600000.cap,
         * offset 0xb683a5, header `QCOMM `/`SDM7280 `, 85,861 bytes). The two
         * EDK2 tables are family 0A, the family that gave this file GIO0's
         * QCOM0A0C and URS0's QCOM0A8B, and the node is one id, one resource
         * and five accessors.
         *
         * `_HID` is "QCOM0AA4" and the family byte is what settles it, the
         * same way it settled GIO0's, the PMIC-GPIO node below. The suffix is not
         * fixed across blocks and generations - it is 17 for the PEP, 0C for
         * the TLMM, 8B for the URS controller and A4 for this one, and A4 is
         * not even constant for this block: the Atoll and SM7325 tables spell
         * it A9 (a52q's and miatoll's QCOM08A9, surya's QCOM14A9) while
         * SDM7350 uses A4 (renoir's and Cedros_IDP's QCOM09A4). gauguin has
         * no SDM7350 to follow and no Atoll to follow; it has lisa and
         * a52sxq. And the id is claimed, which is the check the header above
         * asks for: `tools/acpi-hid-census.py --drivers DIR --bind QCOM0AA4`
         * reports qcusbcucsi7280/qcusbcucsi7280.inf, whose INF binds
         * `ACPI\QCOM0AA4` to `qcusbcucsi7280.sys` and carries the
         * `HKR,Resources,"BinaryPath",%REG_SZ%, %13%\UCS0.bin` line that
         * hands the driver its own firmware blob.
         *
         * `_DEP` is deliberately absent, and it is the one place this node
         * does not copy lisa. lisa's reads `Package (One) { \_SB.PEP0 }`, and
         * PEP0 is the power engine: 2,501 lines and 96,100 bytes in lisa, and
         * the same size in a52sxq's with exactly two lines differing - both in
         * `_SUB`, which returns `"CRD07280"` on lisa and `"QRD07280"` on
         * a52sxq from a branch keyed on `\_SB.PSUB`. That is generator output
         * with a reference-platform string in it and not board data, and it is
         * the sort of thing a port has to notice: this table's own PSUB is
         * `"MTP07225"`, so lisa's `_SUB` verbatim would fall off the end of
         * both branches and answer zero. PEP0 is dominated by one method of
         * its own, and the domination is measurable rather than rhetorical:
         * `THTZ` is a dispatch on (zone, trip point) and is 1,826 of lisa's
         * 2,501 lines, and the node's total is a linear function of the number
         * of zones that dispatch covers - 32 cases in lisa, 24 in venus and
         * vili, and a shipped zero-zone PEP0 of 629 lines in
         * Silicon-Qualcomm-Kailua-DSDT_MTP whose whole `THTZ` is
         * `Return (0xFFFF)`. Nothing in any of the 20 tables that declare
         * `THTZ` calls it, so it is an interface for the OS side and not
         * internal logic. Its `_DEP` names `\_SB.IPCC`, and its skeleton also
         * reaches `\_SB.ABD.ROP1` and `\_SB.AGR0`; none of the three is in this
         * table. No part of it is in this table. Naming it anyway would put
         * a reference into the namespace that cannot resolve, and an `_DEP`
         * that evaluates to AE_NOT_FOUND is worth exactly what no `_DEP` is
         * worth while costing more to read: a later step would meet it as a
         * dangling name rather than as a node known to be missing. `_DEP` is
         * advisory start ordering and there is nothing here to order against.
         * The line to add when PEP0 lands is
         * `Name (_DEP, Package (One) { \_SB.PEP0 })`, and it belongs
         * alongside the matching one on URS0 - lisa's URS0 depends on both
         * PEP0 and UCS0, and this file's URS0 carries no `_DEP` either.
         *
         * `_CRS` is one `GpioIo` on GIO0 pin 0x23. That makes it the first
         * GpioIo this table has placed on GIO0 - everything on that node so
         * far is Interrupt-only - which is precisely the condition OFNI's
         * header names when it says 156 is inert only while nothing above 155
         * is addressed. 0x23 is 35, so the value does not move, and the
         * reason is not that 35 is small: the class extension validates a
         * GpioIo pin against OFNI, and 35 is inside 156 on any reading of
         * that value. The condition is now exercised rather than hypothetical.
         *
         * The five methods are one line each and all five return Names that
         * already sit in this scope above - MUXC, CCST, DPPN, HPDS and HIRQ.
         * They are how qcusbcucsi7280.sys reads the mux state, the CC state,
         * the DP pin assignment and the hotplug lines: for those the driver
         * does not touch a register, it calls into the namespace, and the
         * namespace is AML that something on the board side is expected to
         * keep current. lisa's node has no `_STA` and neither does this one,
         * so the node is present whenever the table is.
         *
         * "Something on the board side" is `IC11` in the SC7280 CRD, and it is
         * worth naming because it is why the five are constants here rather
         * than a defect in this node. IC11's interrupt handlers Q21 and Q22
         * read the PMIC's HPL0/HPH0 and write all five of these `_SB` values
         * out of them, then `Notify (\_SB.UCS0, 0xA0)`. This table has no
         * IC11, so nothing writes the five and the node answers five
         * constants, which is what the header above says. The id for that
         * missing engine is not a guess - `QCOM0A10`, claimed by
         * qci2c7280.inf, carried by exactly two of the 66 corpus tables, lisa
         * and a52sxq, the same pair that settled URS0's and this node's own
         * ids. Which engine it is on this board is still open, and gauguin's
         * tree has five I2C serial engines across two geniqup wrappers to
         * choose from.
         *
         * The far end is missing as well, and it is the end that matters more
         * than this one: this table has no `USBC000` device. In the CRD and
         * four other corpus tables that is `UBTC`, `_HID EisaId("USBC000")`
         * with `_CID PNP0CA0`, an MMIO mailbox, a child connector, and a `_DSM`
         * under the UUID `6f8398c2-7ca4-11e4-ad36-631042b5008f` - the same
         * string in every table that carries it. That is the ACPI UCSI device,
         * it is bound by Windows rather than by anything in the 7280 driver
         * set, and its `_DEP` names this node: `Package (0x03) { \_SB.IC11,
         * \_SB.GIO0, \_SB.UCS0 }`. So PEP0's field on `\_SB.ABD.ROP1`, the
         * `_DEP` this node cannot yet write, and the I2C addresses PML0 would
         * need all wait on one node, and docs/08's Step 4.68 has the
         * measurements for each of the three.
         */
        Device (UCS0)
        {
            Name (_HID, "QCOM0AA4")  // _HID: Hardware ID
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    GpioIo (Exclusive, PullDown, 0x0000, 0x0000, IoRestrictionNone,
                        "\\_SB.GIO0", 0x00, ResourceConsumer, ,
                        )
                        {   // Pin list
                            0x0023
                        }
                })
                Return (RBUF) /* \_SB_.UCS0._CRS.RBUF */
            }

            Method (MUXV, 0, NotSerialized)
            {
                Return (\_SB.MUXC)
            }

            Method (CCVL, 0, NotSerialized)
            {
                Return (\_SB.CCST)
            }

            Method (DPVL, 0, NotSerialized)
            {
                Return (\_SB.DPPN)
            }

            Method (HPDM, 0, NotSerialized)
            {
                Return (\_SB.HPDS)
            }

            Method (HPDI, 0, NotSerialized)
            {
                Return (\_SB.HIRQ)
            }
        }

        Device (URS0)
        {
            /*
             * `_HID` was "QCOM0497" until Step 4.65 - bitra's, and bitra is
             * family 04. The URS index is not fixed across tables the way UFS's
             * is: it moves with the generator group, exactly as GIO0's and SPMI's
             * do. Across the corpus, URS0 is 97 under families 04, 08 and 14 and
             * 8B under 09, 0A, 0C, 1A and 25 - and gauguin is 0A, with both of
             * that family's tables, lisa's and a52sxq's, reading "QCOM0A8B".
             *
             * Three angles agree, and none of them is bitra. lisa and a52sxq
             * both carry Name (_HID, "QCOM0A8B") on a node that is otherwise
             * identical to this one down to the _CID, the window and the _UID.
             * UFS0 above is the opposite case and is why the two are worth
             * separating: QCOM24A5 is 19 of 19 tables across every family, so
             * its absence from a driver set is the set's gap, while QCOM0497 was
             * this port's error. And where a table computes the id instead of
             * naming it - Method (URSI), aliased to _HID on vayu, cepheus and
             * caymanslm - the value it returns when its QUFN switch is zero is
             * always that board's own family id, which for gauguin is 0A8B too.
             */
            Name (_HID, "QCOM0A8B")  // _HID: Hardware ID
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
                    // The three PHY wake lines, from the PDC. Order and trigger
                    // types are bitra's: ss (PDC pin 17, level), then dm_hs
                    // (pin 15, edge) and dp_hs (pin 14, edge). See the header.
                    Interrupt (ResourceConsumer, Level, ActiveHigh, SharedAndWake, ,, )
                    {
                        0x00000211,
                    }
                    Interrupt (ResourceConsumer, Edge, ActiveHigh, SharedAndWake, ,, )
                    {
                        0x0000020F,
                    }
                    Interrupt (ResourceConsumer, Edge, ActiveHigh, SharedAndWake, ,, )
                    {
                        0x0000020E,
                    }
                })
                Method (_STA, 0, NotSerialized)  // _STA: Status
                {
                    Return (0x0F)
                }

                // `DPM0` and `HSEN` stood here and are gone. Both were bitra's -
                // lisa, a52sxq and the 7280 CRD have neither, and the CRD has no
                // URS0 at all - both were definition-only in this table, and the
                // shipped 7280 driver set references neither in any file: the
                // only occurrences of `HSEN` in 770 extracted files are inside
                // the Adreno shader compiler's own symbol,
                // _ZNK4llvm18QGPUTargetLowering10LowerMULHSENS_..., and `DPM0`
                // has none at all.
                //
                // `CCVL` went the same way in Step 4.68 and is the one of the
                // three that took a measurement rather than a search. The UCSI
                // driver really does ask for that name - the string
                // QUCSAeiBCCVL in qcusbcucsi7280.sys - so the earlier reading
                // kept it, on the reasoning that a name the driver looks up is a
                // name the namespace owes. What settles it is that the driver
                // asks for it on one device and this table answered on three: in
                // lisa the string `CCVL` occurs exactly once in the whole table,
                // inside `UCS0`, where it stood here on UCS0, on this USB0 and on
                // UFN0. Two of the three resolved to the same `\_SB.CCST` and
                // were copies nothing could reach - bitra's URS0 children each
                // carried one, and Step 4.66 added the family-0A set on UCS0
                // without taking them out. What the driver binds is UCS0, it is
                // `ACPI\QCOM0AA4`, and it calls its accessors on itself.
                //
                // `PHYC` sits below in this same device and stays, which is why
                // the rule is not "delete the duplicates". lisa binds `PHYC`
                // three times - on this USB0, on UFN0, and on a `USB1` this table
                // does not have - and a pass that removed names appearing more
                // than once would have taken it out of the two nodes that are
                // right. The test is which device the family binds a name on, not
                // how often it appears, and for these five the family binds them
                // on UCS0.
                //
                // Two sentences stood here until Step 4.66 and both were wrong.
                // One said UCS0 "declares `_DEP` on PEP0, which this table also
                // lacks", and the other drew the conclusion - "the node cannot be
                // written correctly before the node it depends on exists". lisa's
                // UCS0 does carry the `_DEP`, and this table's UCS0 deliberately
                // does not, because a `_DEP` naming a node that is not in the
                // namespace resolves to nothing: the declaration is advisory
                // start ordering, and omitting it costs an ordering that has
                // nothing to order against while including it costs a dangling
                // name. See UCS0's own comment for the line to add when PEP0
                // lands.
                //
                // `HSFL`, which HSEN used to read, is now read by nothing. It
                // stays, with `PINA`, rather than being removed alongside the
                // method: those two are the only members of the `_SB` value
                // cluster around this device that lisa, a52sxq and the CRD all
                // lack - every other member is in all three - and the cluster as
                // a whole is definition-only here, which makes it inert.
                // Removing half of it would leave a data block whose shape no
                // longer says which table it was copied from.

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
                // `CCVL` stood here and is gone - see USB0's comment above for
                // the measurement. lisa's UFN0 has no `CCVL` either, and the one
                // this device carried was a third copy of a name the family binds
                // once, on UCS0.

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

        /*
         * ---------------------------------------------------------------------
         * The PMIC family: SPMI, PMIC, PM01.  Step 4.63.
         *
         * Three nodes, and every number in them is either gauguin's own or a
         * constant measured across the corpus - nothing is inherited by
         * resemblance. The ids are the three the 7280 driver set claims:
         *
         *   QCOM0A0B  qcspmi7280.inf        QCOM0A2B  qcpmic7280.inf
         *   QCOM0A2D  qcpmicgpio7280.inf
         *
         * SPMI's window. gauguin's device tree gives the arbiter five regions:
         *
         *   core   0x0C440000 + 0x1100        obsrvr 0x0E600000 + 0x100000
         *   chnls  0x0C600000 + 0x2000000     intr   0x0E700000 + 0xA0000
         *   cnfg   0x0C40A000 + 0x26000
         *
         * which union to [0x0C40A000, 0x0E7A0000). _CRS states one window,
         * 0x0C400000 + 0x2800000, and that is not a copy: it is the value 18 of
         * the 20 SPMI _CRS in Silicium-ACPI carry - across SM8150, SM8250,
         * SM8350, SM7150, SM7125, SM6250 and SDM7280 alike - and it contains
         * every one of the five regions above. The same eight bytes appear
         * little-endian at offset 0x12 of SPMI.CONF, which is the same window
         * restated. The other two tables are Kailua's, and theirs is a different
         * window (0x0C400000 + 0x500000) for a different arbiter.
         *
         * SPMI.CONF is byte-identical in all 18 of those tables, Kailua's being
         * the sole variant, so it is copied verbatim rather than reconstructed:
         * 26 bytes whose only platform-dependent part is the window _CRS
         * already states. What the other 18 bytes configure is not established.
         *
         * PMIC.PMCF is the one method here with real platform content. 19 of the
         * 22 tables in Silicium-ACPI that have an SPMI node carry it, and no
         * table calls it: the PMIC driver calls it by name, so it is not
         * optional for a table that means to bind. (The three that omit it -
         * vili, kebab and Waipio - also omit PMAP and PMBM, and vili's whole
         * PMIC section is PMIC and PM01 and nothing else.) Its package is
         * <count>, then one entry per SPMI USID from 0 to the maximum, where a
         * present USID is the key and an absent one is keyed 0x10, and the
         * paired value is 0x10 on the newer platforms:
         *
         *   lisa, a52sxq (both 0A)   {0A, 0>10, 1>10, 2>10, 3>10, 4>10, 10>10 x5}
         *   Lahaina, venus, lemonade {0B, 0..5>10, 10>10 x4}
         *   renoir, Cedros (both 09) {06, 0,1,2,3>10, 10>10, 5>10}
         *   Kailua, Waipio (0C)      {0D, 0..7>10, 10>10 x4, 0C>16}
         *
         * Two readings of this package fit the older tables and only one fits
         * the newer. On SM8150/SM8250/SM7125 the values step by two -
         * alioth's is {04, 0>1, 2>3, 4>5, 6>7} - which reads as <primary USID,
         * companion USID>, one entry per PMIC, and that reading cannot explain
         * lisa's consecutive keys 0,1,2,3,4 or renoir's key 0x10 sitting between
         * 3 and 5. The reading that fits both is the one above: one entry per
         * USID from 0 up, placeholders for the gaps, and a value whose meaning
         * differs by generation - a companion on the old platforms, a peripheral
         * type on the new, where 0x16 appears once (Kailua, USID 12) and shows
         * the field is not a USID at all. The entry count is a per-family
         * constant: 10 for family 0A, 10 for 1A, 13 for 0C, 6 for 09.
         *
         * gauguin's device tree populates USIDs 0 through 6 - pm6350 at 0 and 1,
         * pm7250b at 2 and 3, pm6150l at 4 and 5, pmk8350 at 6, all seven
         * children of spmi@c440000 and none of them disabled, the pm8008 at
         * USID 8 being on I2C - and it is family 0A, so the package is lisa's
         * with USID 5 present instead of absent. What 0x10 means is still not
         * established; it is the value 12 tables give for every populated USID,
         * including renoir, which is SM7350 to gauguin's SM7225 and the closest
         * relative in the corpus.
         *
         * PM01's interrupt is the arbiter's own. The dts gives spmi@c440000
         * interrupts-extended = <0x62 0x01 0x04> - PDC pin 1 - and 512 + 1 is
         * 513 = 0x201, which all 21 PMIC-GPIO nodes in the corpus carry, Level,
         * ActiveHigh, Shared, except Kailua and Waipio, whose PMIC on PDC pin 3
         * adds a second. gauguin has no PMIC on pin 3.
         *
         * PM01._DSM: the GPIO Controller UUID, function 0 returning the bitmap
         * 0x03 (functions 1 and 2), function 1 returning Package (0x02){0x07,
         * 0x06}. The UUID, the bitmap and the pair are each constant across
         * every table that carries them; the pair's meaning is not established.
         * Four older tables return Buffer (One){0x00} at function 1 instead, and
         * those are all pre-0A families.
         *
         * _STA returning 0x0F is this file's convention on every device it
         * defines; the reference tables leave it out and are present by default.
         *
         * Deliberately not added here, with the reason each time. PMAP was the
         * first entry on this list and is no longer on it: its _DEP is a
         * three-entry package naming \_SB.PMIC, \_SB.ABD and \_SB.SCM0, two of
         * which were absent from this file when that was written, so it would
         * have been a dangling dependency. Steps 4.75 and 4.76 wrote ABD and
         * SCM0, the last referent arrived, and Step 4.77 wrote the node; its
         * own comment, below, carries the id and the GEPT that three sibling
         * nodes share.
         *
         *   PMBM and PMGK are in the corpus, and no id of either is claimed by
         * the 7280 set - eight ids over 17 declarations and five over 11, with
         * QCOM0A2A and QCOM0A8E the two the lisa/a52sxq pair writes - so
         * adding them would put two devices in Device Manager that nothing
         * binds. PML0 (QCOM0AD3, which qcpmic7280.inf does claim) is
         * reachable, but it is an I2C-attached PMIC - lisa's _CRS gives it
         * four I2C addresses on \_SB.I2C2 - and this file has no I2C
         * controller and gauguin's pm8008 is at a different address. PEP0
         * (QCOM0A17, qcpep.wd7280.inf) is claimed and is the largest remaining
         * single node in the reference, 2,501 lines in lisa - but only 629 of
         * those are skeleton, the rest is one case per thermal zone, and it is
         * the power engine besides: its own step, and its own comment above
         * carries the measurement.
         */
        Device (SPMI)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Name (_HID, "QCOM0A0B")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_CID, "PNP0CA2")  // _CID: Compatible ID
            Name (_UID, One)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x0C400000,         // Address Base
                        0x02800000,         // Address Length
                        )
                })
                Return (RBUF) /* \_SB_.SPMI._CRS.RBUF */
            }

            Method (CONF, 0, NotSerialized)
            {
                Name (XBUF, Buffer (0x1A)
                {
                    /* 0000 */  0x00, 0x01, 0x01, 0x01, 0xFF, 0x00, 0x02, 0x00,
                    /* 0008 */  0x0A, 0x07, 0x04, 0x07, 0x01, 0xFF, 0x10, 0x01,
                    /* 0010 */  0x00, 0x01, 0x0C, 0x40, 0x00, 0x00, 0x02, 0x80,
                    /* 0018 */  0x00, 0x00
                })
                Return (XBUF) /* \_SB_.SPMI.CONF.XBUF */
            }
        }

        // ABD - the ACPI Bridge Device: the second of the two nodes PEP0's own
        // text names. PEP0's _DEP names IPCC and its single Field names this
        // device's region, so the two are read together and are placed
        // together; after this step neither reference is missing from this
        // table.
        //
        // What it is comes from the driver's own description rather than from
        // the three letters: qcabd.inf calls itself the INF file for "the
        // Driver Frameworks ABD Driver" and gives ABD.DeviceDesc as
        // "Qualcomm(R) ACPI Bridge Device". It installs qcabd.sys as a KMDF
        // 1.33 kernel service, SERVICE_DEMAND_START, class System, and claims
        // exactly one hardware id - ACPI\QCOM0427, the only claim of any ABD id
        // among the 112 .inf files in this set.
        //
        // The id's family byte is authored and not silicon, and this is the
        // second time a whole block has turned out that way. The same node is
        // QCOM0427 in ten tables (lisa and a52sxq among them), QCOM0527 in
        // eight, QCOM1427 in surya and QCOM0242 in caymanslm, where even the
        // index moves; and the corpus splits inside one SoC, because venus and
        // vili are SM8350 and write 0527 while Lahaina is SM8350 and writes
        // 0427. Step 4.73 met the same thing from the other end, when a52q -
        // Bitra like gauguin - wrote family 08 for its thermal zones and no
        // driver in this set claimed any of those ids. So gauguin takes
        // QCOM0427 because a shipped driver claims it and for no other reason,
        // and the byte differing from the one PEP0 will carry (0A, as lisa's
        // does) is not a problem to be resolved: lisa's table holds QCOM0A17
        // and QCOM0427 at once, so the mixed pair is attested in the closest
        // sibling rather than invented here.
        //
        // The shape is the corpus's and it is 19 nodes of 21 identical:
        // Name (_UID, Zero), Alias (\_SB.PSUB, _SUB), an
        // OperationRegion (ROP1, GenericSerialBus, Zero, 0x0100) whose line is
        // byte-for-byte the same in all 21 tables, Name (AVBL, Zero), and a
        // _REG that sets AVBL when Arg0 is 0x09 - the GenericSerialBus address
        // space id, so the method is this device recording whether the OS has
        // opened its region. Two tables differ: vili adds Method (_STA)
        // returning 0x0F, and Waipio replaces the Alias with a _SUB method
        // returning \_SB.PSUB, drops the _DEP, and adds the same _STA.
        //
        // The _DEP is withheld, on the rule that has governed since the SMMUs:
        // PEP0 is not in this table, and a _DEP entry is a namespace path the
        // OS resolves when it loads the device - an entry that does not
        // resolve is not a hint, it is a failure. 19 of the 21 tables write
        // exactly Name (_DEP, Package (One) { \_SB.PEP0 }), and the exception
        // is the one that matters: Waipio is the only table in the corpus with
        // no PEP0 anywhere in it, and its ABD carries no _DEP either. That is
        // this table's situation, so what is written here is the form a
        // shipped table writes when the dependency is absent.
        //
        //   A correction, because the comment above the SMMUs gives that rule
        //   a justification that is not true. It says "a one-entry _DEP is a
        //   shape no table has". ABD's is one entry in 19 tables, and PRTC's is
        //   one entry naming \_SB.PMAP. The shape is common; what makes an
        //   entry un-writable is that it names a node this table has not got.
        //   The rule lands in the same place either way and the reason does
        //   not, and a reason that is wrong is worse than a short one.
        //
        // _STA is written, returning 0x0F, because the two corpus tables that
        // carry one both return 0x0F while the nineteen that omit it are
        // present by ACPI's default - so the two forms describe the same
        // device, and this file's convention on the devices it defines is to
        // say it outright.
        //
        // ROP1 is not private to PEP0, which is the part worth knowing before
        // the clients arrive. The address in each client's Connection is a
        // channel, and the corpus is consistent about which device owns which.
        // All 53 fields in the corpus, measured by channel:
        //
        //   0x0001  PEP0   AttribRawBytes (0x15)  FLD0, 168 bits  (18 tables)
        //   0x0001  PEP0   AttribRawBytes (0x1A)  FLD1,  40 bits  (both Kailua)
        //   0x0002  PRTC   AttribRawBytes (0x18)  FLD0, 192 bits  (19 tables)
        //   0x0003  PMGK   AttribRawBytes (0x30)  UCSI, 384 bits  (11 tables)
        //   0x0004  PMGK   AttribRawBytes (0x40)  GOEM, 512 bits  (Kailua x2, Waipio)
        //
        // Two things in that table are corrections to what this comment said
        // before Step 4.78 measured all 53 fields instead of the sixteen it had
        // read. The field name is the client's own and is not always FLD0: PMGK
        // names its two channels UCSI and GOEM, and UCSI is a name that means
        // something - the USB Type-C Connector System Software Interface - so
        // the name is data and not a formality. And Kailua's odd entry is odder
        // than it looked: it is not 0x15 spelled as 0x1A on the same field, it
        // is a different field - FLD1 at 40 bits where the other 18 tables
        // declare FLD0 at 168 - so Kailua's PEP0 reads a fifth of the payload
        // from the same channel, and asks the bus for 26 bytes to get it. Both
        // halves are recorded rather than resolved: PEP0 is not written yet, and
        // when it is, 0x15 with FLD0 at 168 bits is this board's declaration, on
        // the rule that the field's length is the one number of the two with a
        // reason behind it.
        //
        // PRTC is the one that matters most, and it is a device Windows already
        // has a driver for: its _HID is ACPI000E, the standard Time and Alarm
        // Device, and its _GRT and _SRT read and write the real time over
        // channel 0x0002. All 20 corpus PRTCs carry that _HID, and every one of
        // them carries a one-entry _DEP naming \_SB.PMAP - 19 as the string
        // "\\_SB.PMAP" and one, Waipio, as the path - so PMAP was PRTC's only
        // dependency. Step 4.77 wrote PMAP, which made that entry writable, and
        // Step 4.78 wrote PRTC on it. The spelling was settled by counting every
        // _DEP entry in the corpus rather than the twenty here, and the count
        // inverts the vote: 1,416 packages hold 3,058 entries, 3,039 of them are
        // name references and 19 are strings, and the 19 strings are these. So
        // the form 19 tables prefer is a form no other device in the corpus uses,
        // and Waipio's is the form the other 65 tables use. See the PRTC node.
        // Of the other two clients, PMGK is QCOM0A8E and no .inf in this set
        // claims it, and PEP0 is the remaining large node.
        //
        // Position, which in this file is now a derivation rather than a copy.
        // ABD stood near the end until Step 4.78 and that was wrong in a way the
        // compiler caught: PRTC's Field names \_SB.ABD.ROP1, a Field cannot
        // reference an OperationRegion that has not been declared yet, and iasl
        // rejected it as Error 6142, illegal forward reference. The rule is
        // ACPI's and not the compiler's - the namespace is built in declaration
        // order, so the region has to exist before the field is created - and it
        // is why the corpus's own order is load-bearing rather than stylistic:
        // ABD precedes PRTC in all 20 tables that carry both, as it precedes
        // PMAP, and its position is measured too. It is immediately followed by
        // PMIC in all 21 tables, and immediately preceded by SDC2 in 18 of them
        // and UFS0 in the other 3, which puts it third, fourth or fifth in every
        // table. THIS file has no SDC1 or SDC2 node, so its slot is between SPMI
        // and PMIC, and the whole node was moved there. The run continues in the
        // corpus's own order behind it: PMIC, then PML0, then PM01 - PML0 sits
        // between the two in 11 of the 21 tables that carry PMIC, and this file
        // became one of the 11 in Step 4.79, which is why the node is here and
        // not further down the file - then PMAP, then PRTC, and PM01 -> PMAP ->
        // PRTC is unbroken in 20 of 20.
        //
        // AVBL is read by nothing in this table, and could not be: in the
        // corpus its readers are the cameras (CAMS, CAMF, CAMI, CAMT, CAMU),
        // TSC1, NFCD and - in venus alone - PCI0 and PCI1, each as
        // If (\_SB.ABD.AVBL) inside a block keyed on PGID. Step 4.70 recorded
        // that the cameras are one of the two things this port cannot drive.
        // The name and the method are written anyway, because all 21 tables
        // carry them and because _REG is the OS's own handshake: leaving it out
        // would be this table deciding it knows better than the driver about
        // how its region gets opened.
        //
        // No _CRS, which is the corpus's answer and not an omission - none of
        // the 21 ABD nodes has one. The numbers 0x0001, 0x0002 and 0x0003 in
        // the clients' Connections are channels, and the resource source in
        // each is "\\_SB.ABD" itself: this device is the bus and not a device
        // on one, which is why the table has no window to give it and why the
        // driver that binds it is one whose entire description is "ACPI Bridge
        // Device".
        Device (ABD)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Name (_HID, "QCOM0427")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_UID, Zero)  // _UID: Unique ID

            OperationRegion (ROP1, GenericSerialBus, Zero, 0x0100)
            Name (AVBL, Zero)
            Method (_REG, 2, NotSerialized)  // _REG: Region Availability
            {
                If ((Arg0 == 0x09))
                {
                    AVBL = Arg1
                }
            }
        }

        Device (PMIC)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Name (_HID, "QCOM0A2B")  // _HID: Hardware ID
            Name (_CID, "PNP0CA3")  // _CID: Compatible ID
            Alias (^PSUB, _SUB)
            Name (_DEP, Package (One)  // _DEP: Dependencies
            {
                \_SB.SPMI
            })
            Method (PMCF, 0, NotSerialized)
            {
                Name (CFG0, Package (0x0B)
                {
                    0x0A,
                    Package (0x02)
                    {
                        Zero,
                        0x10
                    },

                    Package (0x02)
                    {
                        One,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x02,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x03,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x04,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x05,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x06,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x10,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x10,
                        0x10
                    },

                    Package (0x02)
                    {
                        0x10,
                        0x10
                    }
                })
                Return (CFG0) /* \_SB_.PMIC.PMCF.CFG0 */
            }
        }

        // PML0 is the companion PMIC on I2C - the part the kernel calls
        // qcom,pm8008 and the vendor calls the Leica PMIC - and its name, its id
        // and its address set are read rather than chosen.
        //
        // The name is the driver package's. qcpmic7280.inf carries two entries
        // and they are the two nodes this one sits between:
        //
        //   %PMIC.DeviceDesc%=PMIC_Inst,  ACPI\QCOM0A2B
        //   %PML0.DeviceDesc%=PMICLC_Inst,ACPI\QCOM0AD3
        //
        // - and [Strings] gives PML0.DeviceDesc as "Qualcomm(R) Power Management
        // PML0". QCOM0AD3 is the one PML0 id that any of the 112 .inf files
        // claims; the corpus's other four - QCOM1AD3 on lemonade, venus and
        // Lahaina, QCOM08B4 on a52q and miatoll, QCOM09D3 on renoir and Cedros,
        // QCOM0CD3 on both Kailua tables - are claimed by nothing in this set. So
        // the id is the claimed one and not the family's, which has been the rule
        // since Step 4.63.
        //
        // What the id covers is a bitmap, and the same .inf writes it out:
        //
        //   HKR,PMICLC,"LeicaCfgBitMap",%REG_DWORD%,3 ;bit map of I2C Leica
        //   PMIC configuration 0b11, both leica 1&2 (P&Q) present
        //
        // Leica 1 answers at 0x08 and Leica 2 is a second part on the same bus.
        // That is the whole of the corpus's address variation: 0x08 and 0x09 in
        // all nine tables that declare I2C addresses at all, 0x0C and 0x0D added
        // in six of those, and 0x10 and 0x11 in lisa alone - and lisa's _CRS
        // branches on SKUV, which is this bitmap as a namespace test. The pair
        // 0x08/0x09 is not two devices. The kernel's mfd driver for this part
        // claims its own address and the next one outright:
        //
        //   drivers/mfd/qcom-pm8008.c:204
        //   dummy = devm_i2c_new_dummy_device(dev, client->adapter,
        //                                     client->addr + 1);
        //
        // and gauguin's own board overlay names the two of them. The stock dtbo's
        // entry 18 resolves two symbols, pm8008_8 and pm8008_9, into the sinks
        // this tree generates as s136 and s137 - so the vendor's name for the
        // PM8008's two register windows is its two addresses, 8 and 9.
        //
        // gauguin carries one part. Its tree holds exactly one pm8008, the only
        // child of i2c@990000, and it answers at 0x08:
        //
        //   pmic@8 { compatible = "qcom,pm8008"; reg = <0x08>; ... }
        //
        // So Leica 1 is present, Leica 2 is not, and _CRS carries 0x08 and 0x09
        // and stops. The six tables that add 0x0C/0x0D are boards with a second
        // part and this is not one, which is the same reading as the bitmap: 0b01.
        //
        // The bus is IC13 and the pins are GIO0's, both from that same board
        // reading. i2c@990000 has reg = <0x990000 0x4000>, and this table's IC13
        // is Memory32Fixed (0x00990000, 0x00004000), _UID 0x0D, _STR
        // "QUP_1_SE_4" - the QUP whose SE index the board's own dmas property
        // confirms, 0x190 0 4 3 there and slot 4 here. The corpus names its bus in
        // this same cell - lisa \_SB.I2C2, lemonade and renoir \_SB.IC14, a52q
        // \_SB.IC10 - so the source string is the one cell of the corpus's _CRS a
        // port must change, and the address cells do not move. The pins are the
        // board's as well: reset-gpios is <&tlmm 0x3a 1> and interrupts-extended
        // is <&tlmm 0x3b 1>, and pm8008-default-state names those two pins as
        // gpio58 (reset-n) and gpio59 (int). TLMM in this table is GIO0, QCOM0A0C
        // at 0x0F100000 + 0x300000, so both pins name \_SB.GIO0 - as they do in
        // six of the eleven tables. The other four put them on \_SB.PM01, which is
        // the PMIC's own GPIO block and not this board's wiring.
        //
        // Which pin is which is written nowhere in the corpus: the GpioIo
        // parameters are byte-identical across all eleven tables and none of them
        // is named. So the order here is this file's, and it is the majority's -
        // nine of the eleven tables list two pins and seven list them ascending,
        // while both Kailua tables list 0x00A1 before 0x002A. gauguin's two are
        // 0x3A and 0x3B in ascending order, which is also reset before interrupt.
        // One note against reading lisa or a52sxq as the two-pin example: there
        // the second pin appears only in the branch that also adds Leica 2's
        // addresses, so in those two tables it belongs to the second part.
        // gauguin has one part with two pins, so the pin list is the board's.
        //
        // The GpioIo parameters do transfer and are copied exactly - Exclusive,
        // PullNone, 0x0000, 0x00C8, IoRestrictionNone - and so is the 0x000186A0
        // in each connection. That number is the slave's declared speed, 100 kHz,
        // and the corpus writes it in all nine tables that carry an I2C entry.
        // gauguin's controller runs at 0x00061A80, 400 kHz, and that is a
        // property of i2c@990000 rather than of the part; the corpus's number is
        // the vendor's for this part and is the slower of the two, so it is the
        // one written.
        //
        // _STA is 0x0B and it is a board answer and not a family one. The corpus
        // splits seven Zero to four 0x0B, and the split cuts through the id: lisa
        // and a52sxq both declare QCOM0AD3 and return 0x0B and Zero respectively,
        // so the same driver on the same part is hidden on one board and not on
        // the other. gauguin's part is populated and is used - the tree's
        // pm8008-thermal zone takes this node's phandle as its thermal-sensors -
        // and 0x0B is present and enabled without the UI presence bit. Zero would
        // tell Windows the part is not there, which on this board would leave the
        // driver that owns it unbound.
        //
        // No _UID, no _STR and no _CCA: all eleven tables omit all three, and the
        // device is a single instance. _SUB is the corpus's \_SB.PSUB in nine of
        // the eleven - lisa and a52sxq are the two that define a method, for the
        // SKU branching their _CRS does - so it is Alias (^PSUB, _SUB), which is
        // this file's spelling of it from depth 1. _DEP is the bus and one entry,
        // as in all nine I2C tables; Kailua's PML0 has no _DEP because it has no
        // I2C entry to depend on. And the position is measured rather than
        // stylistic: PML0 is immediately preceded by PMIC and immediately followed
        // by PM01 in 11 of 11, so it goes between the two nodes it is written
        // between, exactly where the ABD comment's run of the corpus's order said
        // it would.
        Device (PML0)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0B)
            }

            Name (_HID, "QCOM0AD3")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_DEP, Package (One)  // _DEP: Dependencies
            {
                \_SB.IC13
            })
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    I2cSerialBusV2 (0x0008, ControllerInitiated, 0x000186A0,
                        AddressingMode7Bit, "\\_SB.IC13",
                        0x00, ResourceConsumer, , Exclusive,
                        )
                    I2cSerialBusV2 (0x0009, ControllerInitiated, 0x000186A0,
                        AddressingMode7Bit, "\\_SB.IC13",
                        0x00, ResourceConsumer, , Exclusive,
                        )
                    GpioIo (Exclusive, PullNone, 0x0000, 0x00C8, IoRestrictionNone,
                        "\\_SB.GIO0", 0x00, ResourceConsumer, ,
                        )
                        {   // Pin list
                            0x003A
                        }
                    GpioIo (Exclusive, PullNone, 0x0000, 0x00C8, IoRestrictionNone,
                        "\\_SB.GIO0", 0x00, ResourceConsumer, ,
                        )
                        {   // Pin list
                            0x003B
                        }
                })
                Return (RBUF) /* \_SB_.PML0._CRS.RBUF */
            }
        }

        Device (PM01)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Name (_HID, "QCOM0A2D")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, One)  // _UID: Unique ID
            Name (_DEP, Package (One)  // _DEP: Dependencies
            {
                \_SB.PMIC
            })
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x00000201,
                    }
                })
                Return (RBUF) /* \_SB_.PM01._CRS.RBUF */
            }

            // The missing return path is the reference's, not an oversight: a
            // matching UUID with a revision above 1 falls out of the method and
            // returns implicit zero, which is what every corpus table does. iasl
            // warns about it (3115 and 3107) and the warning is the cost of
            // carrying the reference's behaviour rather than a tidier guess.
            Method (_DSM, 4, NotSerialized)  // _DSM: Device-Specific Method
            {
                If ((ToBuffer (Arg0) == ToUUID ("4f248f40-d5e2-499f-834c-27758ea1cd3f") /* GPIO Controller */))
                {
                    If ((ToInteger (Arg2) == Zero))
                    {
                        Return (Buffer (One)
                        {
                             0x03
                        })
                    }

                    If ((ToInteger (Arg2) == One))
                    {
                        Return (Package (0x02)
                        {
                            0x07,
                            0x06
                        })
                    }
                }
                Else
                {
                    Return (Buffer (One)
                    {
                         0x00
                    })
                }
            }
        }

        // The PMIC Apps device - the block Windows binds as "Qualcomm(R)
        // Power Management PMIC Apps Device", binary qcpmicapps7280.sys. It is
        // the PMIC's peer rather than its child: PMIC above is the SPMI group
        // this file wrote for the arbiter and its two slaves, and PMAP is the
        // apps-side subsystem the same silicon reports through, bound by its
        // own driver and not by the PMIC's.
        //
        // Why it is written now. PMIC's comment lists what that node leaves
        // out, and PMAP was the first entry on that list for one reason only:
        // its _DEP is a three-entry package naming \_SB.PMIC, \_SB.ABD and
        // \_SB.SCM0, and two of the three did not exist when the list was
        // written. Step 4.75 wrote ABD, which left SCM0, and Step 4.76 wrote
        // that. All three referents are in this table now, so the package can
        // be written whole - and the corpus writes it whole: all 20 PMAP nodes
        // carry that identical three-entry package, with no exception
        // anywhere. That is the opposite of ABD and SCM0, where the _DEP was
        // withheld for want of a referent; here there is nothing to withhold.
        // It is this file's first three-entry _DEP - PMIC's and PM01's are
        // one-entry - and its first naming ABD or SCM0.
        //
        // The id. Nine ids across those 20 declarations:
        //
        //   QCOM052F  5   mh2, cepheus, nabu, pipa, vayu
        //   QCOM0C2C  3   Kailua (both), Waipio
        //   QCOM1A2C  3   lemonade, venus, Lahaina
        //   QCOM082F  2   a52q, miatoll
        //   QCOM092C  2   renoir, Cedros
        //   QCOM0A2C  2   a52sxq, lisa
        //   QCOM0268  1   caymanslm
        //   QCOM142F  1   surya
        //   QCOM252C  1   alioth
        //
        // and exactly one of the nine is claimed by the shipped driver set -
        // QCOM0A2C, by QcPmicApps7280.inf, whose DeviceDesc is the name quoted
        // at the top of this comment:
        //
        //   %DeviceDesc%=PMIC_Inst,ACPI\QCOM0A2C
        //   %DeviceDesc%=PMIC_Inst,ACPI\VEN_QCOM&DEV_0A2C
        //
        // The check this time is stronger than the one Steps 4.75 and 4.76
        // used: those read the loose inf tree, and this one extracted all 112
        // .inf files from the 112 .cab files of the 7280 driver set and
        // grepped each id in all of them. QCOM04DD comes back qcscm.inf and
        // QCOM0427 qcabd.inf, which re-derives both of those decisions by the
        // same route, and the other eight PMAP ids come back unclaimed.
        //
        // Here, and for the first time, the two witnesses agree. QCOM0A2C is
        // family 0A, gauguin's own family, and the two tables that write it -
        // lisa and a52sxq - are the pair every other PMIC-family decision in
        // this file has been taken from. ABD was decided on the family alone
        // and SCM0 on the driver's spelling alone, each against the corpus
        // majority; PMAP is the first node where the claimed id and the family
        // id are the same id. The a52q table, which has been the
        // counterexample twice, is one here too: it is SM7225, the same SoC as
        // gauguin, and it writes QCOM082F, one of the eight ids nothing
        // claims. Same SoC, wrong family byte, the same finding as on the
        // thermal zones and on SCM0.
        //
        // Where it goes. PMAP immediately follows PM01 in all 20 tables.
        // Whether any other node's position is invariant in that way has not
        // been measured; this one was, so this one is taken.
        //
        // The shape is uniform. All 20 carry exactly these members, in this
        // order - _HID, the _SUB alias, _DEP, _STA in the four that have it,
        // GEPT, _CRS - with one node's worth of variation and it is Waipio's:
        // it drops _CRS, makes _SUB a method rather than an alias, and keeps
        // an explicit _STA of 0x0F. No PMAP in the corpus carries a _UID, and
        // this one needs none: its id is unique here, so there is nothing to
        // disambiguate.
        //
        // GEPT is not PMAP's own. The same method, spelled the same way, is on
        // two other nodes across the corpus, and the only difference between
        // the three is the constant returned:
        //
        //   PMAP.GEPT   0x02   in 20 declarations, all identical
        //   PEP0.GEPT   One    in 20 declarations, all identical
        //   PMGK.GEPT   0x03   in 10 declarations, and an eleventh that is
        //                      not: Waipio's returns Buffer (0x03)
        //                      { 0x03, 0x04, 0x06 } through a different body
        //
        // so PMAP's and PEP0's are uniform across their whole families and
        // PMGK's is not, and the table that breaks it is Waipio - the same
        // table that is the exception on PMAP itself, on ABD, and on SCM0.
        // PMAP's is the only one of the three this file can write today: PEP0
        // is the largest node still to come and PMGK is one nothing in the
        // driver set claims, and both are absent. The other two belong beside
        // those nodes.
        //
        // Nothing calls GEPT. Outside each node's own declaration the name
        // occurs in no table - the only other lines are the reference comments
        // iasl writes back on the Return it annotates. So the caller is not
        // ASL, and whether the driver evaluates it was tested rather than
        // assumed: the test is the one that established OFNI on GIO0, and it
        // fails here. qcpmicapps7280.sys has no four-character uppercase run
        // that can be an ACPI method name. Its runs of that form are RSDS,
        // PAGE, NULL, INIT, GCTL and DITM - a debug directory signature,
        // section names, and two that name nothing in any of the 66 tables;
        // GCTL is in qcgpio.sys as well, which is what a shared compiler
        // artefact looks like and a method name does not. GEPT occurs once,
        // inside the eight-byte run AeiBGEPT, which is what a compiler makes
        // of the adjacent literals "AeiB" and "GEPT"; the same image carries
        // AeoB, qcgpio.sys carries AeiA and AeoB with OFNI standing alone, and
        // qcabd.sys carries AeiBSSID - SSID being a name in none of the 66
        // tables either. The glued form does not name methods, whatever else
        // it is, so no claim is made from it in either direction. GEPT is here
        // because all 20 corpus PMAP nodes carry it, identically.
        //
        // STAT, inside it, is created and then never written or read: the
        // method returns the word at offset 2 and byte 0 stays zero. It is a
        // leftover of the generator's shape and it is carried, because
        // dropping it would be an AML difference from the reference that buys
        // nothing.
        //
        // _STA returning 0x0F is this file's convention on every device it
        // defines, and here the corpus agrees by default rather than by vote:
        // 16 of the 20 leave _STA out entirely, which the specification reads
        // as present, enabled, shown and working - the same four bits - and a
        // seventeenth, Waipio, writes 0x0F outright. The three that write
        // something else write 0x0B, which clears the UI bit and so hides the
        // device from Device Manager while still starting it, and they are
        // a52q, miatoll and surya: families 08, 08 and 14, the pre-0A
        // families, with no 0A table among them. gauguin is 0A. If this device
        // ever needs hiding, 0x0B is the same-SoC alternative and it is one
        // byte.
        //
        // _CRS carrying no resources, in the reference's own words: a two-byte
        // buffer holding only the end tag, 0x79 0x00. 18 of the 20 write
        // exactly that. The nineteenth is caymanslm, whose _CRS is a real
        // GpioInt on \_SB.PM01 - the PMIC's own GPIO controller, which gauguin
        // also has - at pin 0x01C0, edge, active-both, pull-up. That pin is
        // board data and there is no counterpart for it here: gauguin's device
        // tree gives the SPMI arbiter one interrupt, PDC pin 1 = 0x201, which
        // PM01's own _CRS already carries, and names no line for the apps
        // subsystem at all. The empty template is the reference's way of saying
        // the device has no resources of its own, which is what gauguin's tree
        // says too.
        //
        // _SUB is the corpus's \_SB.PSUB, spelled ^PSUB as everywhere else in
        // this file. PMAP does not branch on it - only PEP0 does that, and only
        // for IDP07280 and CRD07280 - so gauguin's "MTP07225" passes through
        // unread here, as it does on all 20 tables.
        Device (PMAP)
        {
            Name (_HID, "QCOM0A2C")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_DEP, Package (0x03)  // _DEP: Dependencies
            {
                \_SB.PMIC,
                \_SB.ABD,
                \_SB.SCM0
            })
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Method (GEPT, 0, NotSerialized)
            {
                Name (BUFF, Buffer (0x04){})
                CreateByteField (BUFF, Zero, STAT)
                CreateWordField (BUFF, 0x02, DATA)
                DATA = 0x02
                Return (DATA) /* \_SB_.PMAP.GEPT.DATA */
            }

            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, Buffer (0x02)
                {
                     0x79, 0x00                                       // y.
                })
                Return (RBUF) /* \_SB_.PMAP._CRS.RBUF */
            }
        }

        // The Time and Alarm Device - the first node in this table whose _HID is
        // not a QCOM id. ACPI000E is the standard id for a real-time clock, and
        // Windows supplies its driver rather than a vendor: none of the 112 .inf
        // files extracted from this board's driver package mentions ACPI000E,
        // which is what an OS-supplied id looks like from inside a vendor set.
        //
        // It is written now because PMAP landed in Step 4.77 and PMAP is the
        // only thing PRTC depends on: all 20 corpus PRTCs carry a one-entry
        // _DEP, with no exception, and every one of them names \_SB.PMAP.
        //
        // The _DEP is written as a namespace path rather than as a string, and
        // that is a measurement rather than a preference. 19 of the 20 tables
        // spell the entry as the string "\\_SB.PMAP" and Waipio spells it
        // \_SB.PMAP, so counting votes inside this one device gives 19 to 1 for
        // the string. Counting every _DEP entry in the whole corpus inverts it:
        // 1,416 _DEP packages across the 66 tables hold 3,058 entries, of which
        // 3,039 are name references - 3,038 absolute plus alioth's relative I2C9
        // - and 19 are strings. Those 19 strings are exactly these, one per
        // table, on this one device; no other device in any table spells a _DEP
        // entry as a string. So the form that wins a vote within PRTC is the
        // only form of its kind in the corpus, and the form Waipio uses alone is
        // the form everything else in the corpus uses. ACPI reads a _DEP entry
        // as a device object reference, which is what the 3,039 are, and the
        // string is a vendor habit this one node inherited. Waipio is the odd
        // table everywhere else in this family - it is the only table in the
        // corpus with no PEP0, it writes _SUB as a method, it spells _GCP as a
        // Name - and here it is the one table that writes the entry the way the
        // other 65 tables write theirs.
        //
        // The position is measured the same way PMAP's was: PMAP is followed
        // immediately by PRTC in all 20 tables that carry both, and PRTC is
        // immediately preceded by PMAP in all 20, so PM01 -> PMAP -> PRTC is a
        // three-node run and this node goes directly after PMAP. What follows
        // PRTC is not fixed - PMBM in 10 tables, PEXT in 6, BAT1 in 2, PMBT and
        // PMGK in 1 each - so the run has an end and the corpus says where it is.
        //
        // The Field is the device's only resource and it is declared on
        // \_SB.ABD.ROP1, which is why ABD is written: PRTC has no _CRS in any of
        // the 20 tables and no MMIO window, and reads its clock by opening a
        // field on ABD's GenericSerialBus operation region. The channel is
        // 0x0002, which is the same triple the ABD comment above records from
        // the producer's side - AttribRawBytes (0x18), FLD0 at 192 bits - and
        // the number in I2cSerialBusV2 is that same channel byte, so the channel
        // index is the slave address on the bus ABD presents. The resource
        // source is "\\_SB.ABD" itself, as in every client's Connection: this is
        // the family's one device that is a bus, which is what its driver's
        // description - "ACPI Bridge Device" - says it is.
        //
        // 19 of the 20 tables declare that field and the twentieth is the
        // exception that is not a variant. caymanslm's PRTC has no Field at all:
        // it reads and writes FLD0 as a bare name, which ACPI resolves outward
        // through \_SB.PRTC, then \_SB, then \ - and the only FLD0 in that table
        // is \_SB.PEP0.FLD0, declared on PEP0's channel. A field unit is not
        // reachable sideways, so that reference does not resolve, and the
        // disassembler says so: iasl emits External (FLD0, IntObj) at the top of
        // that table. A PRTC whose clock reads PEP0's channel data, and whose
        // _SRT stores a 50-byte buffer into a 168-bit field, is a broken
        // declaration and not a second shape. The effective corpus for this node
        // is therefore 19 tables, and all 19 are byte-identical inside the
        // device.
        //
        // _GRT and _SRT are the ACPI-defined read and write of the real time and
        // the arithmetic in them is the part worth reading before the sizes are
        // copied. _GRT builds a 26-byte local, lays a 16-byte TME1 at bit 0x10 -
        // byte 2 - and returns TME1, so bytes 2..17 of the buffer are the time
        // structure ACPI defines for _GRT and the first two bytes are the
        // channel's own status. The field is 24 bytes, so the local is two bytes
        // longer than the region it is filled from: BUFF = FLD0 stores 24 bytes
        // and leaves bytes 24 and 25 at zero, which is past the end of TME1 and
        // harmless. _SRT is 50 bytes for the same reason and one more: it stores
        // BUFF into FLD0 and then stores the result back into BUFF, chained as
        // BUFF = FLD0 = BUFF, and what the second store is for is the status the
        // bus writes into byte 0 on completion - the method then tests it and
        // returns One if it is non-zero. So the region is a write-then-readback
        // whose first byte is a reply code, which also explains why the 50-byte
        // local is allowed to be truncated to 24 on the way out: everything past
        // byte 23 is ACT1 and ACW1 tail, and both are set to zero immediately
        // before the store, so the truncation drops nothing that was not zero.
        //
        // _GCP returns 0x04, in all 20 tables, 19 as a method and Waipio as
        // Name (_GCP, 0x04). What bit 2 of that capability mask means is not
        // established here: ACPI000E's driver is the OS's own, nothing in the
        // 112 .inf files or the five driver trees mentions the method, and no
        // table comments it. One constraint does come out of the corpus and it
        // rules out the obvious reading - _GWS, _STW and _STV appear in none of
        // the 66 tables, so the alarm half of the device is implemented nowhere
        // and 0x04 cannot be advertising it. The value is copied and the decode
        // is recorded as open rather than guessed at.
        //
        // _STA returning 0x0F is this file's convention, and the corpus's answer
        // here is the same shape as PMAP's: 16 of the 20 leave the method out,
        // Waipio writes 0x0F, and the three that write 0x0B - present, enabled
        // and working, but not shown in Device Manager - are a52q, miatoll and
        // surya. Those are the same three tables that write 0x0B on PMAP, which
        // makes 0x0B a habit of those boards and not a statement about either
        // device: families 08, 08 and 14, the pre-0A families, and the same
        // table named as the counterexample in Steps 4.73, 4.76 and 4.77 among
        // them. gauguin's family is 0A and the method says 0x0F.
        //
        // No _UID and no _CRS, both because all 20 tables omit them. The device
        // has no resources of its own to describe - the Connection inside the
        // Field is the whole of its allocation - and nothing in the family needs
        // a second instance.
        Device (PRTC)
        {
            Name (_HID, "ACPI000E")  // _HID: Hardware ID
            Name (_DEP, Package (0x01)  // _DEP: Dependencies
            {
                \_SB.PMAP
            })
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Method (_GCP, 0, NotSerialized)  // _GCP: Get Capabilities
            {
                Return (0x04)
            }

            Field (\_SB.ABD.ROP1, BufferAcc, NoLock, Preserve)
            {
                Connection (
                    I2cSerialBusV2 (0x0002, ControllerInitiated, 0x00000000,
                        AddressingMode7Bit, "\\_SB.ABD",
                        0x00, ResourceConsumer, , Exclusive,
                        )
                ),
                AccessAs (BufferAcc, AttribRawBytes (0x18)),
                FLD0,   192
            }

            Method (_GRT, 0, NotSerialized)  // _GRT: Get Real Time
            {
                Name (BUFF, Buffer (0x1A){})
                CreateField (BUFF, 0x10, 0x80, TME1)
                CreateField (BUFF, 0x90, 0x20, ACT1)
                CreateField (BUFF, 0xB0, 0x20, ACW1)
                BUFF = FLD0 /* \_SB_.PRTC.FLD0 */
                Return (TME1) /* \_SB_.PRTC._GRT.TME1 */
            }

            Method (_SRT, 1, NotSerialized)  // _SRT: Set Real Time
            {
                Name (BUFF, Buffer (0x32){})
                CreateByteField (BUFF, Zero, STAT)
                CreateField (BUFF, 0x10, 0x80, TME1)
                CreateField (BUFF, 0x90, 0x20, ACT1)
                CreateField (BUFF, 0xB0, 0x20, ACW1)
                ACT1 = Zero
                TME1 = Arg0
                ACW1 = Zero
                BUFF = FLD0 = BUFF /* \_SB_.PRTC._SRT.BUFF */
                If ((STAT != Zero))
                {
                    Return (One)
                }

                Return (Zero)
            }
        }

        // The TLMM pin controller - the block Windows binds as "Qualcomm(R)
        // System Manager GPIO Device". Every value below is measured.
        //
        // The id. The census above settles it the same way it settled PM01: the
        // low pair is the block and the middle byte is the generator family. 0C
        // is the TLMM block on all five modern families (09 0A 0C 1A 25), 0D on
        // the older three (05 08 14) and 17 on 02 - which is why gauguin's
        // window at gauguin's exact length reads QCOM1A0C in Lahaina's
        // DSDT_MTP. gauguin's family byte is 0A, so the id is QCOM0A0C, and
        // that is the one id qcgpio7280.inf claims:
        //
        //   %GPIO.DeviceDesc%=GPIO_Inst,ACPI\QCOM0A0C
        //
        // whose binary is qcgpio.sys, and whose device description is the name
        // quoted above.
        //
        // The window is gauguin's own and not a copy: the device tree's
        // pinctrl@f100000 states reg = <0x00 0xf100000 0x00 0x300000>. Ten of
        // the 21 corpus tables carry that same pair; the rest track whatever
        // their own generation's TLMM window is.
        //
        // The interrupts. Nine of them, INTID = 32 + SPI, all Level and
        // ActiveHigh, because that is what the device tree states -
        //
        //   interrupts = <0x00 0xd0 0x04 ... 0x00 0xd8 0x04>   ->  0xF0 ... 0xF8
        //
        // - and the corpus agrees on the first one from the other side: all 21
        // tables put 0xF0 first, and 0xF0 is 32 + 0xD0, gauguin's first line.
        // What the corpus does with the *later* lines is a generator artefact
        // rather than a reading: every one of the 21 repeats its first value
        // instead of incrementing it, three or four times, so no table in the
        // corpus demonstrates what a second TLMM line looks like. gauguin's
        // device tree is the only authority for lines two through nine, it
        // declares nine, and so nine are written.
        //
        // What is deliberately not here:
        //
        //   - The rest of the corpus _CRS interrupt list. It is not the three
        //     entries an earlier reading of a truncated dump made it: it runs
        //     from 8 to 77 entries and it is board data, not SoC data - vili
        //     and venus are both SM8350 and carry 24 and 77 - with values that
        //     are INTIDs of direct-connect and PDC-mapped pin lines. gauguin's
        //     device tree does not carry that list in derivable form: its
        //     pinctrl nodes name functions and its PDC states ranges of
        //     possible pin-to-SPI mappings (qcom,pdc-ranges = <0x00 0x1E0 0x5E
        //     ...>, 153 pins over five ranges), not the pins the board uses. An
        //     invented catalogue is worse than an absent one, so this node
        //     declares the controller's own nine lines and stops. The cost is
        //     the usual one and it is stated: a per-pin line that only the
        //     catalogue would advertise is not advertised, until the device
        //     that needs it is added with its own GpioInt.
        //   - OFNI is written, and the reason is worth recording twice, because
        //     an earlier pass of this file omitted it for the wrong reason and
        //     the pass that restored it then gave it the wrong value.
        //     The omission: it counted occurrences of the name *in the 21 tables
        //     that have a GIO0*. 19 of those 21 define it - 18 as a Method, one
        //     on Blackbolt as a Name - and none of the 18 contains a third
        //     occurrence, so the rule "dead code is not ported" dropped it. The
        //     rule was right and the caller set was wrong: the caller is not in
        //     any table, it is qcgpio7280/qcgpio.sys, and qcgpio7280.inf proves
        //     the pairing - `GPIO_Inst,ACPI\QCOM0A0C` and `ServiceBinary =
        //     qcgpio.sys`, against this node's own _HID. The image carries
        //     exactly one string that can be an ACPI method name, and it is
        //     `OFNI`. (Its other four-character uppercase runs are `PAGE`,
        //     `INIT`, `RSDS` and `GCTL` - section names and a debug directory
        //     signature, consecutive with the build's own pdb path - plus
        //     register-scan fragments.) Its imports agree: ntoskrnl.exe and
        //     WDFLDR.SYS only, and of those, RtlInitializeBitMap, RtlSetBits,
        //     RtlFindSetBits and RtlNumberOfSetBits are a bitmap API, the shape
        //     of a driver sizing one bit per pin; the class extension is bound
        //     through WdfVersionBindClass rather than imported by name, which is
        //     why GpioClx appears nowhere in this image - the only file in the
        //     shipped set that imports it by name is qcpmicgpio7280.sys, the
        //     PMIC GPIO miniport.
        //     The value: OFNI is the TLMM's GPIO count, and the corpus settles
        //     what "count" means - most of the way, and the part it does not
        //     settle is worth as much as the part it does. Two numbers are
        //     available on any Qualcomm TLMM: the count of pins that have a gpio
        //     function (the driver's gpio_groups[] list) and the width Linux
        //     gives the gpiochip (.ngpios, which on all five SoCs below is
        //     gpio_groups + 1 and which the SoC dtsi's gpio-ranges copies
        //     wherever it is not one less). Where a corpus table, the mainline
        //     driver and the SoC dtsi can all be read:
        //                     corpus                    gpio_groups .ngpios dtsi
        //         sm8150  175 x4 cepheus nabu vayu mh2      175      176    176
        //         sm8250  180 x2 alioth pipa                 180      181    181
        //         sm8350  203 x2 lemonade Lahaina_MTP        203      204    204
        //         sm8350  204 x2 venus vili                  203      204    204
        //         sc7280  175 x2 lisa a52sxq                 175      176    175
        //     Ten of these fourteen tables answer the gpio count. The minority
        //     are SM8350 boards, and they are not consistent among themselves -
        //     venus and vili say 204 while lemonade and the Qualcomm reference
        //     MTP say 203, on the same silicon - so the minority is a per-board
        //     choice and not a platform generation. (Two further 204s, renoir
        //     and Cedros_IDP, are SDM7350 tables; this tree has no sm7350
        //     pinctrl driver, so they can be counted but not placed.)
        //     The evidence that bears on gauguin directly is lisa and a52sxq.
        //     Their GIO0 carries this node's own _HID, QCOM0A0C - the id
        //     qcgpio7280.inf binds qcgpio.sys to - where the SM8350 tables use
        //     QCOM1A0C and the SM8250 tables QCOM250C. Same driver, same idiom,
        //     same TLMM generation as gauguin, and both answer the gpio count
        //     (175, against a chip width of 176).
        //     The pin the two numbers differ by is real and is not a gpio:
        //     pinctrl-sm6350.c's descriptor list runs to PINCTRL_PIN(163), of
        //     which 0..155 are `GPIO_n`, 156 is `UFS_RESET` and 157..163 are the
        //     SDC lines. UFS_RESET has no gpio function - it is absent from
        //     gpio_groups[] - but Linux still numbers it line 156, which is why
        //     `.ngpios` is 157 and why gauguin's own board dts says
        //     `reset-gpios = <&tlmm 156 GPIO_ACTIVE_LOW>` under `&ufs_mem_hc`:
        //     the one tlmm reference on this board outside 0..94, and it is the
        //     line the two candidate counts disagree about. The vendor dtb's
        //     `gpio-ranges = <&tlmm 0 0 0x9D>` is that same chip width, and one
        //     build of this node answered 0x009D on the strength of it. It is
        //     0x009C = 156.
        //     What would change it, and the reason this is not a coin flip to
        //     revisit later: 156 is inert while nothing on this board names a
        //     pin above 155 through ACPI, and nothing does - there is no GpioIo
        //     or GpioInt on GIO0 at all yet. If a later step gives the UFS node
        //     the reset line the Linux side actually drives through pad 156,
        //     this must become 0x9D, because the class extension cannot hand a
        //     client a line the count excludes. One byte, one rebuild.
        //   - Kailua's GPIV, GPIC, GPIW and GPIB are not written. They are
        //     defined on a table with no _CRS, no other table has them, and the
        //     shipped driver set contains no reference to any of the four.
        //   - _AEI. Eight of the 21 tables have none at all. Nothing in this
        //     table declares a GpioInt on GIO0 yet, so there are no event pins
        //     to list; when a node is added that has one, its pin goes here.
        //     gauguin's buttons are not candidates - they hang off pm6350 GPIO
        //     2, which is PM01's block - and no _AEI is better than one that
        //     names pins nothing asked for.
        //
        // _DSM is carried because all 21 tables have it, and because the UUID
        // is the same string in every one of them: it is Microsoft's GPIO
        // Controller method, not a Qualcomm convention. Revision 3 is the
        // corpus majority and what both family-0A tables answer, and function 1
        // returns the family-0A value - 0x0100 on lisa and a52sxq, against
        // 0x0140 on venus and vili and 0xFFFF on lemonade, renoir, Cedros and
        // Lahaina. Kailua's generator answers revision 1 with a second UUID, and
        // it is the same generator that omits _REG; it is not gauguin's family.
        // Function 2 and above fall out of the method and return implicit zero,
        // the same way PM01's does and for the reason its note gives; the
        // corpus reaches that result through a BreakPoint, which is not carried.
        //
        // That the miniport is not what evaluates _DSM is worth recording, and
        // it is the one place where this file's earlier reading of qcgpio.sys
        // was wrong: the claim was that the image contains no ACPI method name
        // at all and imports nothing GPIO-related but the class extension. The
        // image does contain a method name - OFNI, above - and finding it took
        // testing the strings rather than counting them in the tables, because
        // a four-character name is short enough to appear inside compiler
        // symbols by accident: the shipped set's HSEN hits are all one mangled
        // LLVM symbol, _ZNK4llvm18QGPUTargetLowering10LowerMULHSENS_..., and
        // its URSI hits are all RECURSIVE_TILING and RECURSION_DEPTH. OFNI has
        // no such competitor, and it is the only such string in the driver.
        // _DSM and _AEI remain the class extension's, on the Windows side,
        // which is why the ids and the shapes below have to match what that
        // extension expects rather than what qcgpio.sys says - and it is also
        // why OFNI, which qcgpio.sys does say, has to be here.
        //
        // _REG and GABL are carried because 19 of the 21 have the pair. Its
        // reader set is empty here: the only nodes in the corpus that read it
        // are GIO0's own methods and RP1 on caymanslm, gating on the
        // functional-fixed-hardware region handler's arrival. Nothing on gauguin
        // reads it, so it is written and never read, which is inert.
        //
        // BAM1 - the crypto BAM, and the first node here whose _CRS a table and
        // the board state identically. Its id is the platform-prefix form every
        // node in this file already carries: QCOM0A0B at SPMI, QCOM0A0C at GIO0,
        // QCOM0A0D at IPC0, QCOM0A09 at both MMUs, QCOM0A10 at the three QUP
        // wrappers, QCOM0A16 at UAR2, QCOM0A2B/2C/2D at PMIC, PMAP and PM01.
        // The prefix 0A is this platform's and the suffix is the device. Not
        // every id in a table carries it - the subsystem services (QCOM06E0 at
        // PILC, 06E1 at RPEN, 06DC at TFTP, 06C2 at IPCC), the storage
        // controllers (QCOM24A5 at UFS0, 24BF and 2466 at SDC1 and SDC2) and
        // SCM0 and TREE (QCOM04DD, 04DE) are the same in every table, which is
        // why those four steps had to determine their ids by other means. The
        // platform-device nodes do carry it, and they carry the same suffix
        // across tables of the same platform: this file writes PMIC, PMAP, PM01
        // and PML0 as QCOM0A2B, 0A2C, 0A2D and 0AD3, and lisa writes exactly
        // that; miatoll writes 082E, 082F, 0830 and 08B4, caymanslm 0266, 0268
        // and 0269 with no PML0 at all.
        //
        // The BAM suffix is 0A wherever the prefix is 0A, so lisa and a52sxq
        // both write QCOM0A0A and miatoll writes QCOM080A. Every BAM in a table
        // carries that table's one id, never a second - the whole class shares
        // it and _UID says which instance: One here, 0x05 on BAM5, 0x06 and 0x07
        // on BAM6 and BAM7, 0x0D to 0x10 on BAMD through BAMG (BAM3 on the
        // Silicon Blackbolt table being QCOM6012, 0x03). The _UID is the number
        // in the name, in hex, in every one of the 109 BAM nodes in the corpus
        // without exception.
        //
        // Twenty-one of the 67 tables declare BAM1, under nine ids - QCOM0213,
        // 050A, 080A, 090A, 0A0A, 0C0A, 140A, 1A0A, 250A - and BAM1 and BAM5
        // carry the same one of the nine as each other in all 21. BAME and BAMF
        // carry it too wherever they appear, and the three 0C tables carry
        // neither. The
        // split is by platform and not by the generations this file groups by
        // elsewhere: QCOM050A covers mh2, cepheus, nabu, pipa and vayu, and
        // QCOM1A0A covers lemonade, venus, vili and Lahaina MTP. Exactly one of
        // the nine is claimed by any .inf in the shipped set - QCOM0A0A, by
        // qckmbam7280/qckmbam7280.inf: "Qualcomm(R)
        // Bam Bus Device", class System, ClassGuid the same {4d36e97d-...} the
        // rest of the family uses, DriverVer 06/29/2022 1.0.3521.0000, service
        // qcbam at SERVICE_KERNEL_DRIVER/SERVICE_DEMAND_START, KMDF 1.33, the
        // SoC category GUID 46 of the 112 files carry, and one hardware id and no
        // other. QCOM0213, 050A, 080A, 090A, 0C0A, 140A, 1A0A and 250A are
        // claimed by nothing in the set. That is SCM0's shape again - one id of
        // six there, one of nine here - and like SCM0's it is why the id needed no
        // argument. Two arguments in fact reach the same id and neither is
        // evidence for the other: this file's prefix is 0A, and in the two
        // corpus tables that share it the BAM suffix is 0A; and QCOM0A0A is the
        // one BAM id a shipped driver binds. What would have made it an argument
        // is absent - no table in the corpus is this board's family, bitra
        // declares no BAM at all, and the two nearest platforms are miatoll at
        // QCOM080A and caymanslm at QCOM0213. So the id is chosen by the driver
        // set with the prefix agreeing, which is what selection by the id space
        // has meant at every step that used it.
        //
        // The _CRS needed one, and it is the one place in this file where there
        // is nothing to resolve. The corpus writes base 0x01DC4000 and GSI 0x130
        // in 21 of 21 tables and length 0x24000 in 18 of them; gauguin's own
        // device tree has dma-controller@1dc4000 with reg 0x1dc4000 length
        // 0x24000 and interrupts <0 0x110 4>, and 0x110 + 32 = 0x130. Same
        // address, same length, same GSI - not the corpus over the board's
        // objection, and not the board over the corpus's, which is how every
        // other _CRS here has been decided. The length is the generation's and
        // this is the generation: 0x24000 in the eighteen older tables, 0x28000
        // in the three 0C ones and 0x28000 on sc7280, and 0x24000 in the tree. The
        // tree also names the client and it
        // is the only node that references this BAM: crypto@1dfa000 takes
        // dmas = <&cryptobam 4>, <&cryptobam 5>. It is marked
        // qcom,controlled-remotely with qcom,ee = <0>, qcom,num-ees = <4> and
        // num-channels = <0x10>, so the secure world owns it and this node
        // enumerates it rather than managing it.
        //
        // Which of the four BAMs the family-0A tables declare this board has is
        // the decision this step actually makes, and the decision is to write one
        // of them. BAM5 is the other half of the pair - BAM1's successor in 21 of
        // 21 and BAM5's predecessor in 21 of 21 - and its address is the one that
        // moves with the generation: 0x03A84000 in 0A, and 0x17184000, 0x62E84000,
        // 0x62D84000, 0x03304000 and 0x06C04000 elsewhere. Its 0x03A84000 is not
        // an unattributed number: sc7280 declares slimbam at exactly that base
        // with GIC_SPI 164, and 164 + 32 = 196 = 0xC4, which is the GSI the
        // corpus writes for BAM5 in 21 of 21 tables. In lisa it sits immediately
        // below the ADSP, whose child SLM1 is the SLIMbus at 0x03AC0000 - the
        // same 0x03AC0000 sc7280's slim-ngd occupies, and the consumer slimbam's
        // dmas = <&slimbam 3>, <&slimbam 4> names. The shipped set carries the
        // driver for that SLIMbus, qcslimbus7280.inf binding ADSP\QCOM0A0F, an id
        // the ADSP creates rather than one ACPI writes.
        //
        // None of it is here. 0x03A84000 has no node in the board's tree, and
        // neither does 0x03AC0000; this board's ADSP is at 0x3000000, where
        // sc7280's is at 0x3700000 and its SLIMbus at 0x03AC0000; neither
        // sm6350.dtsi nor sm7225.dtsi declares a BAM beyond cryptobam, and
        // neither has a SLIMbus node at all; and the corpus's two Bitra tables,
        // Realme's bitra and Xiaomi's gauguin, declare no BAM of any kind.
        // Writing BAM5 would reserve
        // 0x32000 of address space and GSI 0xC4 on the strength of another die's
        // layout, which is the ground SP1 and SP12 were withheld on. The node is
        // owed and it waits on the ADSP, which is also owed.
        //
        // BAME and BAMF are the other two the QCOM0A0A tables carry, at
        // 0x06064000 / 0x15000 / GSI 0xC7 and 0x0A704000 / 0x17000 / GSI 0xA4.
        // Neither address exists here either: 0x06064000 and 0x0A704000 appear
        // nowhere in the board tree, in sm6350.dtsi, in sm7225.dtsi or in any of
        // the reference trees, and no corpus table gives either an interrupt line
        // this board could check against a second source. They are not part of
        // the pair, and the 0C
        // generation does not carry them at all - Waipio and both Kailua declare
        // BAM1 and BAM5 and stop.
        //
        // Position. The pair is placed as a pair because BAM5's relations are
        // BAM1's, so this is one slot and it is scored once. Against the nodes in
        // this table those relations are sixteen at 21 votes each - after UFS0,
        // DEV0, ABD, PMIC and PM01, and before SPMI, RPEN, TFTP, IPC0, GLNK,
        // QGP1, SCM0 and CPU0 through CPU3, all of them 21 of 21 in both
        // directions - and a seventeenth at 20, PRTC, which precedes BAM1 in the
        // 20 of the 21 tables that carry a PRTC, vili having none. The set is 356
        // votes. Scored over the slots it admits the maximum is 335,
        // and the vote alone does not make that slot unique: every slot after
        // PRTC and before RPEN ties, because the nodes the corpus puts between
        // PRTC and BAM1 - PMBM, BCL1, PMGK and PEP0, in that order in 21 of 21 -
        // are absent here. What separates the tie is adjacency rather than
        // counting: BAM1's immediate predecessor in the corpus is PEP0 in 16
        // tables, WLDS in 4 and PMGK in 1, never PRTC, so the relation the corpus
        // states is "immediately after that absent run", and the nearest preceding
        // node this table actually has is PRTC in 20 of 21 and PM01 in vili. So
        // the pair goes immediately after PRTC - declaration 13 counting from
        // zero, the numbering the corpus indices above use - which is where it was
        // written.
        //
        // Sixteen of the seventeen relations are satisfied at that slot and the
        // seventeenth is SPMI's, and this file's order is why: SPMI is written at
        // declaration 6 and ABD, PMIC and PM01 follow it at 7, 8 and 10, so
        // "after PM01" and "before SPMI" cannot both hold. The corpus puts SPMI
        // at declaration 59 of 140 in lisa and 56 of 143 in a52sxq, counting
        // unique declarations from zero - after SCM0, TLOG and TREE. So this node
        // costs 21 votes against SPMI and nothing
        // else. That is recorded as a debt rather than a property, and it is the
        // same placement the 128-relation accounting at TFTP charges twelve times:
        // moving SPMI to its corpus slot would repair those twelve and this one
        // together, and it is the first thing a later step that reorders this file
        // should do. The two measurements now agree about where the fault is.
        //
        // The body is the shape all twenty-one agree on without exception: _HID in
        // 21, _UID One in 21, _CCA Zero in 21, _CRS in 21, and in the resource
        // template exactly one Memory32Fixed and one Interrupt in 21 of 21, with
        // no GpioIo, no second interrupt and no other resource anywhere in the
        // class. _SUB is the local form of the alias the corpus writes as
        // \_SB.PSUB in 20, with Waipio using a method in the 21st - Waipio
        // departing at the same member here as at GLNK, IPC0 and TFTP. _STA is in
        // exactly two tables, vili and Waipio, returning 0x0F in both, the same
        // pair that adds it at the four nodes above. And there is no _DEP in any
        // of the 21: this node has no dependency at all, which is unusual for a
        // node this file has added lately - TFTP names IPC0, IPC0 names GLNK,
        // GLNK names IPCC and RPEN - and it is measured rather than assumed, the
        // way ABD's withheld dependency was.
        //
        // What it hands forward: BAM5, which waits on the ADSP and its SLIMbus;
        // and the SPMI reorder, which two independent measurements now point at.
        Device (BAM1)
        {
            Name (_HID, "QCOM0A0A")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, One)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x01DC4000,         // Address Base
                        0x00024000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000130,
                    }
                })
                Return (RBUF) /* \_SB_.BAM1._CRS.RBUF */
            }
        }

        Device (GIO0)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Name (_HID, "QCOM0A0C")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, Zero)  // _UID: Unique ID
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x0F100000,         // Address Base
                        0x00300000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F0,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F1,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F2,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F3,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F4,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F5,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F6,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F7,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Shared, ,, )
                    {
                        0x000000F8,
                    }
                    // dts SPIs 0xD0 to 0xD8, in order, all <... 0x04> = level
                    // high. The rest of the corpus list is board data and is
                    // not reproducible from gauguin's device tree - see above.
                })
                Return (RBUF) /* \_SB_.GIO0._CRS.RBUF */
            }

            // 156 = gauguin's TLMM GPIO count. The reader is qcgpio.sys, not a
            // table, and the value's derivation is in the header - see the OFNI
            // bullet above this node.
            Method (OFNI, 0, NotSerialized)
            {
                Name (RBUF, Buffer (0x02)
                {
                     0x9C, 0x00                                       // ..
                })
                Return (RBUF) /* \_SB_.GIO0.OFNI.RBUF */
            }

            Name (GABL, Zero)
            Method (_REG, 2, NotSerialized)  // _REG: Region Availability
            {
                If ((Arg0 == 0x08))
                {
                    GABL = Arg1
                }
            }

            Method (_DSM, 4, NotSerialized)  // _DSM: Device-Specific Method
            {
                If ((ToBuffer (Arg0) == ToUUID ("4f248f40-d5e2-499f-834c-27758ea1cd3f") /* GPIO Controller */))
                {
                    If ((ToInteger (Arg2) == Zero))
                    {
                        Return (Buffer (One)
                        {
                             0x03
                        })
                    }

                    If ((ToInteger (Arg2) == One))
                    {
                        Return (Package (0x01)
                        {
                            0x0100
                        })
                    }
                }
                Else
                {
                    Return (Buffer (One)
                    {
                         0x00
                    })
                }
            }
        }

        // The other three engines gauguin's board leaves running, and the two
        // it leaves running that are not written. The board's own tree decides
        // both, and it also corrects a count this table's notes carried after
        // Step 4.68: the live engines are six, not four. Dumping them out of
        // the running kernel's device tree and reading the status of every
        // node at a QUP address gives, in address order:
        //
        //   0x880000  spi@880000          w0 SE0  slot  1  GSI 0x279  SPI
        //   0x884000  qcom,qup_uart@884000 w0 SE1 slot  2  GSI 0x27A  4-wire UART
        //   0x984000  i2c@984000          w1 SE1  slot 10  GSI 0x182  I2C
        //   0x988000  i2c@988000          w1 SE2  slot 11  GSI 0x183  I2C
        //   0x98c000  spi@98c000          w1 SE3  slot 12  GSI 0x184  SPI
        //   0x990000  i2c@990000          w1 SE4  slot 13  GSI 0x185  I2C
        //
        // and everything else at a QUP address - i2c@888000, i2c@980000,
        // spi@888000, spi@980000, the UARTs at 0x984000 and 0x98c000 - is
        // disabled. Step 4.69 wrote the last of the six. The first two were
        // missed because the payload's own tree disagrees with the board's
        // about them: work/out/gauguin.dts has i2c@880000 where the board has
        // spi@880000, and serial@98c000 with compatible "qcom,geni-debug-uart"
        // where the board has spi@98c000 with an irled@0 on it. The board wins,
        // and the disagreement is recorded rather than resolved.
        //
        // The GSIs above are no longer taken from a sibling table. They are in
        // gauguin's tree, and the conversion is the GIC's: a device tree
        // interrupt specifier <0 N 4> means GIC INTID N + 32, and ACPI's GSI
        // numbering is that INTID. i2c@990000's interrupts is <0 0x165 4>, so
        // its GSI is 0x185; i2c@988000's <0 0x163 4> gives 0x183; i2c@984000's
        // <0 0x162 4> gives 0x182; spi@98c000's <0 0x164 4> gives 0x184; and
        // spi@880000's <0 0x259 4> gives 0x279, which is the family's number
        // for wrapper 0's engine 0 and the CRD's I2C1. The whole ladder is
        // therefore measured on the board and not borrowed, and the two agree
        // slot for slot.
        //
        // The protocol of each engine is in the tree too, in the TLMM pin group
        // each node's pinctrl-0 resolves to: qupv3_se0_spi_pins,
        // qupv3_se1_4uart_pins, qupv3_se2_i2c_pins and _spi_pins,
        // qupv3_se6_i2c_pins and _spi_pins, qupv3_se7_i2c_pins and _2uart_pins,
        // qupv3_se8_i2c_pins, qupv3_se9_spi_pins and _2uart_pins, and
        // qupv3_se10_i2c_pins. Two engines have both an I2C group and an SPI
        // group, which is what an engine is; which one is wired is the status
        // of the node that uses it. And the se index in those names is not the
        // per-wrapper one the _STR carries: se0 through se5 are wrapper 0 and
        // wrapper 1 starts at se6, so wrapper 1's engine 1 - this pair's first
        // - is se7, not se1.
        //
        // The two SPI engines are the two not written, and for one reason:
        // nothing can bind them. Every .inf in the SC7280 set was read for the
        // id, and of the four QUP ids the set carries - QCOM0A0B, QCOM0A0C,
        // QCOM0A10, QCOM0A16 - the SPI engine's QCOM0A0E is not among them. The
        // same search over the other four driver trees in ~/work/woa-ref finds
        // it nowhere at all. Writing SP1 and SP12 would therefore register two
        // unknown devices that reserve 0x880000 and 0x98c000 and their GSIs
        // against no driver, and would describe the touch as reachable over a
        // bus this table cannot open. They are withheld, not refused: lisa
        // declares an SP14, so the family does write one, and if a driver for
        // QCOM0A0E ever appears the node is four lines of the same shape.
        //
        // What the touch actually is decides something else, and it is worth
        // writing down because it reads backwards at first. spi@880000's child
        // is touch_spi@0, and it carries a compatible, a reg of 0 and a 10 MHz
        // clock and nothing else - "xiaomi,spi-for-tp", correctly spelled, with
        // no interrupt, no reset and no supply. The touch chip's node is on the
        // I2C engine: focaltech@38 on i2c@988000, with focaltech,irq-gpio and
        // focaltech,reset-gpio on TLMM pins 22 and 21, a vdd-supply, six panel
        // phandles, its own pinctrl for the interrupt and the reset, and
        // qcom,i2c-touch-active = "focaltech,fts_ts" naming it the active one.
        // So the chip is at I2C address 0x38 on slot 11 and the SPI node is
        // there to hold the engine. No driver in the set drives the chip on
        // either bus - there is no touch .inf in it at all - which is why the
        // child is not written and why IC11 alone does not give touch.
        //
        // IC10 and IC11 are the same node as IC13 with a different slot, and
        // carry the same omission: no _DEP, because every engine in the family
        // depends on \_SB.PEP0 and this table has none. IC10's bus is the audio
        // amplifiers cs35l41@40 and cs35l41@41, which no driver in the set
        // claims; IC11's is the touch and the NFC controller nq@28, and the set
        // has no driver for either. Both nodes are written for the bus and not
        // for the slaves, on the same reasoning that withholds the SPI engines:
        // qci2c7280.inf binds the engine, and a child with no driver is an
        // unknown device. The slaves are the four-line addition when one
        // arrives.
        Device (IC10)
        {
            Name (_HID, "QCOM0A10")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, 0x0A)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Name (_STR, Unicode ("QUP_1_SE_1"))  // _STR: Description String
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x00984000,         // Address Base
                        0x00004000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000182,
                    }
                })
                Return (RBUF) /* \_SB_.IC10._CRS.RBUF */
            }
        }

        Device (IC11)
        {
            Name (_HID, "QCOM0A10")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, 0x0B)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Name (_STR, Unicode ("QUP_1_SE_2"))  // _STR: Description String
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x00988000,         // Address Base
                        0x00004000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000183,
                    }
                })
                Return (RBUF) /* \_SB_.IC11._CRS.RBUF */
            }
        }

        // The UART is the one engine here whose family name does not encode its
        // slot, and the corpus shows why in two tables at once. lisa names its
        // DBG-tagged UART UARD and that UART is at _UID 6; venus names its
        // DBG-tagged UART UARD as well and that one is at _UID 4, so the suffix
        // is a name for the role and the _UID is still the slot - venus's is
        // 8 * 0 + 3 + 1 for its _STR "QUP_0_SE_3,DBG".
        //
        // The role is not this engine's. Of the 48 QUP engine nodes the corpus
        // carries, four are tagged ",DBG" - lisa's and venus's UARDs above,
        // a52sxq's and alioth's - and ten are tagged ",4W,BT": lisa's and
        // a52sxq's UAR8, alioth's UAR7, renoir's and Cedros's UAR8, Kailua's
        // UR15 in both its tables, venus's UR21, and lemonade's and Lahaina
        // MTP's UR19. lisa carries one of each and they are two engines, UARD
        // at QUP_0_SE_5 and UAR8 at QUP_0_SE_7, so the family writes the debug
        // port and the four-wire port as two nodes. This board has one of the
        // two, and the readings below name which.
        //
        // The board's own fields say four-wire. gauguin's only enabled GENI
        // UART is qcom,qup_uart@884000, and it carries compatible
        // "qcom,msm-geni-serial-hs", the high-speed serial, which is the
        // compatible the family's Bluetooth-over-UART shape uses; a
        // pinctrl-names of "default", "active" and "sleep", three states where
        // both console nodes this board declares have two; and a pin group,
        // qupv3_se1_4uart_pins, of five sub-groups - default_ctsrtsrx,
        // default_tx, ctsrx, rts and tx - where those two consoles' groups hold
        // active and sleep and nothing else. It also carries qcom,wakeup-byte =
        // <0xfd>. Its interrupts-extended first entry <0x1 0 0x25a 4> gives the
        // GSI 0x25A + 32 = 0x27A; the second is on phandle 0xc1, the PDC, the
        // wake path rather than a resource to publish.
        //
        // The corpus says the same from the other side. Every four-wire UART it
        // carries is the dependency of a Bluetooth node: lisa's UAR8, a52sxq's
        // UAR8, alioth's UAR7 and venus's UR21 are each named in their table's
        // BTH0 _DEP, which reads {PEP0, PMIC, UAR8} in lisa's shape, in all ten
        // of them - and the shape is not the tag's, since nine further tables
        // write the same three entries around a port with no ,4W suffix, so all
        // nineteen BTH0s in the corpus name their own table's UART that way.
        // This table has no BTH0 - the board's Bluetooth is a SLIMbus
        // device, wcn3990 under slim@3ac0000 with compatible
        // "qcom,btfmslim_slave" and status "ok", powered by the bt_wcn3990 node
        // with its four rails - so the port here has no dependent to name, and
        // the payload's tree is not disagreeing about the module when it hangs
        // a bluetooth child on serial@884000 (compatible "qcom,wcn3988-bt",
        // max-speed = <0x30d400>): it is the same module over the other of its
        // two host interfaces. A step that writes Bluetooth starts from one of
        // those two.
        //
        // The console is somewhere else, and four readings agree on where. Both
        // trees' aliases name serial0 at the 98c000 node; the payload's tree
        // gives serial@98c000 compatible "qcom,geni-debug-uart", status "okay"
        // and chosen/stdout-path "serial0:115200n8"; the board's own tree gives
        // that address compatible "qcom,msm-geni-console" and names it serial0
        // in its aliases too; and the firmware names one UART path in its
        // strings, /soc/qcom,qup_uart@98c000 beside /soc/spi@98c000, in both
        // its PE and its volume image. The board disables that node all the
        // same, because the SE under it is the IR blaster's SPI - the
        // disagreement the engine list above already records for spi@98c000,
        // recorded again here and not repaired. So the console this table could
        // name sits on the SPI engine's SE, and androidboot.console=ttyMSM0,
        // which the boot image's cmdline carries, names a kernel console device
        // and not an engine: it decides nothing here.
        //
        // So the node is written as what the board enables and not as what the
        // family calls the role. The name is the slot, and the slot is the
        // engine's SE number plus one: the CRD's I2C1 is its wrapper 0 SE 0 and
        // takes _UID One, and the eight-per-wrapper form the I2C engine below
        // derives, _UID = 8 * wrapper + SE index + 1, is that same rule on an
        // SoC whose wrappers carry eight SEs. This engine's SE number is 1 - the
        // board's own alias block writes qupv3_se1_4uart for 0x884000 - so
        // 1 + 1 = 2, and UAR2 is that number in the family's own UART form, which
        // is the prefix and the _UID in all ten of the four-wire tables. The
        // lowest UAR number the corpus carries is 4, so this exact name is
        // derived and not witnessed; the form is witnessed ten times, and the
        // _STR carries no tag, the way 32 of those 48 corpus nodes do.
        //
        // The eight-per-wrapper form is not this board's, and the three
        // wrapper-1 nodes below are the ones it gets wrong. Measured over the
        // corpus it fits all nineteen of the tagged engine nodes in wrapper 0 and
        // seventeen of the twenty-seven at wrappers 1 and 2, and the ten that do
        // not fit sit in six tables whose tags contradict the addresses of the
        // same table's other nodes - alioth's "QUP_2_SE_1" sits at 0x884000,
        // below its own "QUP_0_SE_1" at 0x984000. gauguin's wrappers count six:
        // the board numbers 0x980000 through 0x990000 qupv3_se6 through
        // qupv3_se10, the SoC's own tree agrees (i2c6, i2c7, i2c8, uart9, i2c10),
        // and wrapper 1's GPI DMA masks six channels (0x3f). So 0x984000 is SE 7
        // and not the ten the ladder gives it, 0x988000 is SE 8 and not 11, and
        // 0x990000 is SE 10 and not 13: those three _UIDs, and the IC10, IC11
        // and IC13 names that follow them, are each two too high. The correction
        // is measured here and not applied - it renames three devices and is a
        // step of its own, named for the next one.
        //
        // _STA is left out on purpose, and the family's practice is split by
        // role rather than uniform. All thirteen UARDs are visible - twelve
        // write no _STA and surya's writes 0x0F - while the four-wire ports are
        // mostly hidden: sixteen of the twenty-one UAR and UR nodes return
        // 0x0B, present but not shown, including both of this generation's
        // QCOM0A16, lisa's UAR8 and a52sxq's, and the three that write no _STA
        // at all are alioth's UAR7 and pipa's UR14 and UR20, while the two that
        // return 0x0F are cepheus's UR18 and surya's UAR4. The hiding tracks the
        // claimant, and the claimant is measurable: nineteen tables in the
        // corpus carry a BTH0 and in all nineteen its _DEP is
        // {PEP0, PMIC, <that table's own UART>} - the ten ,4W,BT ports and nine
        // untagged ones - and sixteen of those nineteen hide the port while
        // three, alioth's and the two QCOM1418 tables', show it. This board's
        // Bluetooth is on SLIMbus and not on this engine, so there is nothing
        // here for Windows to collide with and no role the hiding would serve.
        //
        // The relation sets the comments below cite were measured again over the
        // four-wire ports, since that is the role this node now claims, and they
        // hold: the UART precedes PILC, RPEN, GLNK, TFTP and IPC0 in 10 of 10,
        // is followed by QGP0 in 8 of 8 and QGP1, MMU0, MMU1 and SCM0 in 10 of
        // 10 each, and precedes IC10 and IC11 in 2 of 2 each - the last the one
        // relation this file breaks, already charged to IC10 below. The three
        // sites that cited the UART read "UARD (13 of 13)" and "UARD (13)"
        // before - the debug name's own node count - and read "the four-wire
        // ports (10 of 10)" and "(10)" now; the counts standing beside them are
        // relations about other nodes and did not change. The role change moves
        // no slot, and the corrected counts are written there.
        //
        // qcuart7280.inf claims QCOM0A16, so this node has a driver, and it is
        // the only engine written in this step whose bus is not I2C.
        Device (UAR2)
        {
            Name (_HID, "QCOM0A16")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, 0x02)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Name (_STR, Unicode ("QUP_0_SE_1"))  // _STR: Description String
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x00884000,         // Address Base
                        0x00004000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000027A,
                    }
                })
                Return (RBUF) /* \_SB_.UAR2._CRS.RBUF */
            }
        }

        // The QUP I2C engine the Type-C path and the charger cluster both hang
        // on - the node Step 4.68 named at the root of the Type-C chain and
        // left open. It left two questions running together and only one of
        // them was open.
        //
        // The first is the slot, and it closes. Section 4.68 could not make the
        // CRD's _UID 0x0B follow from its _STR "QUP_1_SE_2"; it follows, and by
        // an arithmetic the whole family obeys:
        //
        //   _UID = 8 * wrapper + SE index + 1
        //
        // which fits all 23 engine nodes in the three tables that carry any -
        // lisa, a52sxq and the SC7280 CRD - with no exception, and predicts
        // the CRD's I2C1 = One for SE 0, I2C2 = 2, I2C4 = 4, I2C5 = 5,
        // UARD = 6, UAR8 = 8, I2C9 = 9, IC10 = 0x0A, IC11 = 0x0B, IC14 = 0x0E.
        // The other half of the convention is the name, and it is the same
        // ladder: the device is called "I2C" plus the decimal _UID up to 9 and
        // "IC" plus it from 10, which is exactly I2C2, I2C9, IC10, IC11, IC14
        // and nothing else. A slot number is a property of the SoC, though, and
        // what is wired to it is a property of the board - so the second
        // question is not answerable from the slot and is not the same question.
        //
        // That second question is which of gauguin's engines this is, and the
        // device tree answers it. gauguin's slot 11 is real and it is not this
        // bus: i2c@988000 sits at wrapper 1, engine 2, carries the corpus's own
        // _UID 0x0B, is reached by the same GSI 0x183 lisa's IC11 is reached by,
        // and is the touch and NFC bus (focaltech@38, nq@28). The charger
        // cluster is on the engine gauguin's tree names:
        //
        //   i2c@990000   reg 0x990000 + 0x4000, interrupts SPI 0x165, ok
        //                fsa4480@42, qcom,pm8008@8, qcom,pm8008@9,
        //                qcom,smb1396@34, bq25970-standalone@66,
        //                aw8624_haptic@5A, qcom,shared, qcom,clk-freq-out
        //
        // which is engine 4 by the stride, (0x990000 - 0x980000) / 0x4000. The
        // index is measured three ways and all three agree. The stride, against
        // the two geniqup nodes at 0x8c0000 and 0x9c0000. The TLMM function the
        // payload's pinctrl-0 names for each bus - qup00, qup01, qup02 and
        // qup10, qup11, qup12, qup13, qup14 - which is qup<wrapper><engine>
        // over all eight of them. And qcom,wrapper-core, which names the
        // wrapper outright rather than by arithmetic: this bus's phandle 0x193
        // is qcom,qupv3_1_geni_se@9c0000's.
        //
        // So the slot is 8 * 1 + 4 + 1 = 13 and the name is IC13. The
        // interrupt is the family's number for the slot rather than a
        // derivation: gauguin's whole wrapper-1 ladder, 0x181 through 0x185, is
        // the corpus's 0x181 through 0x186 one GSI per engine, even though the
        // two put wrapper 1 at different addresses - gauguin 0x980000, the CRD
        // and a52sxq 0xA80000. That is the ids' own lesson again: the slot
        // travels between SoCs and the address does not.
        //
        // _DEP is left off, as on UCS0, because every I2C node in the family
        // depends on \_SB.PEP0 and this table has no PEP0. _STR carries no
        // suffix: lisa's only ",Shared" is on I2C2 and its own charger bus
        // IC11 has none, so gauguin's qcom,shared does not map onto the suffix
        // and the suffix is not written until something shows that it does.
        Device (IC13)
        {
            Name (_HID, "QCOM0A10")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, 0x0D)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Name (_STR, Unicode ("QUP_1_SE_4"))  // _STR: Description String
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x00990000,         // Address Base
                        0x00004000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000185,
                    }
                })
                Return (RBUF) /* \_SB_.IC13._CRS.RBUF */
            }
        }

        // PILC is the Peripheral Image Loader, the block that authenticates and
        // starts the DSPs, and it is the first node in this table whose id comes
        // from the 06 generation of the service group. That is the step: the
        // generation was decided node by node until now, and here it is measured
        // across seven nodes at once, so the rule is the finding and this node is
        // its first application.
        //
        // The board first, because the hardware has to be here before the id
        // matters. gauguin's own tree carries three remoteprocs and all three are
        // named with this framework's initials:
        //
        //   remoteproc@3000000  qcom,sm6350-adsp-pas   adsp.mbn
        //   remoteproc@4080000  qcom,sm6350-mpss-pas   modem.mbn
        //   remoteproc@8300000  qcom,sm6350-cdsp-pas   cdsp.mbn
        //
        // - "pas" being the Peripheral Authentication Service, the secure half of
        // the same loader, each of the three carrying smp2p and a firmware-name
        // under qcom/sm7225/fairphone4/, and the two DSPs adding fastrpc children.
        // So this board names the block four times over, three of them in its own
        // compatible strings, and two of the three are the DSPs Windows drives
        // through this driver.
        //
        // The corpus declares a PILC in 19 of its 66 tables, under seven ids, one
        // per generation:
        //
        //   06E0 x6   a52sxq, lisa, renoir, Cedros IDP, Kailua MTP, Kailua QRD
        //   1AE0 x3   051B x5   04DF x2   023B x1 (caymanslm)
        //   14DF x1 (surya)      25E0 x1 (alioth)
        //
        // The same seven generations appear on PILC's six siblings - RPEN 06E1,
        // SSVC 06DB, TFTP 06DC, QCDB 06DE, PDSR 06DF, SOCP 06DD - each of them in
        // the 06 form 6 or 7 times and in the older forms once or twice per
        // generation, with SOCP alone having no 02 form at all. And the generation
        // is a property of the table and not of the SoC: of the 20 tables that
        // carry three or more of the seven, 19 write a single generation across
        // all of them, and the twentieth, caymanslm, writes 02 for six and 03 for
        // the seventh - the corpus's oldest board slipping once, and not a second
        // rule.
        //
        // The driver set then picks one generation of the seven, and it picks 06.
        // qcpil.inf matches ACPI\QCOM06E0 and installs the service qcPILC on it,
        // which is this node's name in lower case; qcpilfilterext.inf matches the
        // same id as Class=Extension with an upper filter, QCPILFilter.sys, so the
        // set ships a function driver and a filter for this one PIL id and for no
        // other; and no inf in the set names 051B, 1AE0, 04DF, 023B, 14DF or
        // 25E0, here or on any of the six siblings. The corpus's 1A boards -
        // lemonade and venus - write QCOM1AE0 on a node that is otherwise this
        // one, and that id binds to nothing on this host. The id is the driver's,
        // as on every node here, and the driver's generation is this table's
        // generation. This table's IPCC has carried QCOM06C2 since Step 4.73,
        // written before any of this was measured; it is in the same generation,
        // and that is the one confirmation of the rule already inside the file.
        //
        // The body follows the six tables whose id this is. _STA returning 0x0F is
        // this file's convention on every device it writes, so it is here for the
        // file's reason and not the corpus's - which is as well, because the
        // corpus's answer does not support the derivation this paragraph used to
        // make. Measured: 11 of the 19 carry it and 8 do not, and the split is
        // not "the newest against the two older" but the 06, 14, 1A and 25 forms
        // carrying it against the 02, 04 and 05 forms. Of the eight, seven write
        // _HID alone; the eighth is caymanslm's 02 form, which adds a PILX method
        // returning a one-element package and an ACPO method besides. The old
        // sentence said "9 of the 19 ... absent from the two older ones ... in all
        // 7"; it was wrong on the count and on the oldest board, and Step 4.81
        // measured it again while deriving RPEN's body, where the same split comes
        // out differently again - 12 of 21, and 14 disagrees with itself. The
        // correction is recorded rather than quietly made. The alias is narrower:
        // four of
        // the six 06 tables omit it - a52sxq, lisa, renoir and Cedros' IDP - and
        // the two that carry Alias (\_SB.PSUB, _SUB) are Kailua's MTP and QRD
        // board files, one SoC's two files agreeing with each other and with the
        // 1A form's three. The four that omit it are three phone tables and one
        // silicon IDP, and that makes PILC the first device here without a _SUB.
        // The reason is the node's role rather than a preference: the loader does
        // not sit inside a subsystem, it brings subsystems up, and the corpus's
        // own phone tables say so by carrying the alias on RPEN, TFTP, PDSR and
        // SSVC beside this node. There is also no _SUB value here that would be
        // right - this board's PSUB is "MTP07225" where the drivers' own reference
        // tables compare against IDP07280 and CRD07280, the mismatch the IPCC node
        // records - so the omission is the safer half of the choice as well.
        //
        // No _UID, no _CRS and no _DEP. The first two hold for all nineteen
        // tables: no PILC anywhere carries either. So does the third, and the
        // dependency in this group does not run through PILC - TFTP's _DEP names
        // IPC0, PDSR's names PEP0, GLNK and IPC0, SSVC's names IPC0 and QDIG, and
        // none of those four devices is in this table.
        //
        // Position, derived as usual. RPEN is immediately before PILC in 19 of 19
        // and CDI immediately after in 19 of 19, so this node is the second member
        // of a run - RPEN, PILC, CDI, SCSS, ADSP, SLM1, ADCM, AUDD - that no table
        // ever splits. Neither neighbour is here, so the slot comes from the
        // relations this file can check: the four-wire ports (10 of 10), IC10
        // (9 of 9) and IC11 (3 of 3) precede it, and MMU0, MMU1 and SCM0 (19 of
        // 19 each), IPCC (10 of 10), QGP0 (17) and QGP1 (19) follow it. Every
        // one of those nine is satisfied by the slot below, and no later slot
        // is: between QGP1 and MMU0 the two QGP relations break instead. Six further relations are
        // unsatisfiable in any slot, because this file placed UCS0, URS0, USB0,
        // UFN0, SPMI and GIO0 earlier than the corpus's order has them while the
        // corpus puts all six after PILC - SPMI, URS0, USB0, UFN0 and GIO0 in 19
        // of 19 each, UCS0 in 10 of 10, it being absent from nine of the tables.
        // This said "three" and named USB0, SPMI and GIO0 until Step 4.81 measured
        // it again while deriving RPEN's slot and found the same six there; the
        // correction is recorded rather than quietly made. They are recorded here
        // rather than repaired: this step is a node, and a reordering is its own
        // measurement.
        // RPEN is the Reset Power Error Notifier - qcrpen.inf's own description
        // string for it - the device Windows listens to for resets and power
        // errors rather than reaches hardware through. Its service is QCRPEN, its
        // binary qcrpen.sys, it is a KMDF driver, its class is System, it asks for
        // no address and no interrupt, its ACL admits only the built-in Admins and
        // Local System (D:P(A;;GA;;;BA)(A;;GA;;;SY)), it sets PnpLockDown, and it
        // declares WDTFSOCDeviceCategory - the SoC device category the Windows
        // Driver Test Framework finds these devices by.
        //
        // Its id is the other half of PILC's, and the shape of the pairing is
        // worth writing down because it is not what "two co-issued ids" usually
        // means. The corpus declares this node under seven ids - QCOM06E1 seven
        // times, QCOM0533 five, QCOM1AE1 four, QCOM04E0 two, and QCOM026D,
        // QCOM14E0 and QCOM25E1 once each - and they are the same seven
        // generations PILC's are. In five of the seven the two are consecutive:
        // the 06, 1A and 25 generations are E0 and E1, and the 14 and 04
        // generations are DF and E0. In the other two they are not: the 05
        // generation is PILC 051B beside RPEN 0533, and caymanslm's 02 generation
        // is PILC 023B beside RPEN 026D. So the two nodes are issued together
        // without being numbered together, and this id could not be derived from
        // PILC's by counting one up.
        //
        // What decides it is what decided PILC's: the driver set. qcrpen.inf
        // carries one line of hardware id - %RPEN.DeviceDesc%=RPEN_Device,
        // ACPI\QCOM06E1 - and no inf in the 112 names any of the other six, so
        // the 06 generation is the one that binds. The generation is this table's,
        // as PILC's comment records at length; this node is that rule's first
        // confirmation from outside, because it is a second node that had to come
        // out the same way on its own.
        //
        // The body is the second shortest in the file - one member longer than PILC's,
        // because it carries the alias PILC does not - and it is PILC's mirror image.
        // Where PILC's 06 tables mostly omit the alias, all twenty-one RPENs carry
        // it: twenty as Alias (\_SB.PSUB, _SUB), and one - Waipio - as the
        // Method (_SUB) { Return (\_SB.PSUB) } form that the PEP0 comment already
        // records as Waipio's habit, the same file twice. And where PILC has no
        // _CRS, no _UID and no _DEP, neither has RPEN, in any of the twenty-one.
        // The alias is therefore the pair's one difference, and the role reading
        // fits it: PILC brings subsystems up and stands outside them, RPEN reports
        // on them and stands inside. That is a reading and not a measurement - the
        // corpus never says so - but it is the reading that makes both bodies
        // consistent rather than arbitrary, and it is why this file writes the
        // alias here and does not write it there.
        //
        // _STA returning 0x0F is this file's convention on every device it writes,
        // so it is here for the file's reason and not the corpus's. The corpus's
        // own answer, measured, is 12 of the twenty-one - every 06 table, every 1A
        // table, and alioth - against 9 that write _HID and the alias alone: the
        // 05, 04, 02 and 14 forms. Note the 14: surya's PILC carries a _STA and
        // surya's RPEN does not, so within one table of one generation the two
        // nodes disagree. The id is a property of the table, as the last step
        // measured; _STA is not, and nothing here should be written as if it were.
        //
        // _DEP needs a sentence even though there is none, because RPEN is the
        // first node in this file whose absence another node's dependency list
        // names. GLNK's _DEP is Package (0x02){ \_SB.IPCC, \_SB.RPEN } in 12 of its
        // twenty-one tables and Package (One){ \_SB.RPEN } in the other nine, so on
        // all twenty-one reference boards GLNK cannot start until RPEN is there -
        // and this file's GLNK, written in Step 4.82, names this node in its own
        // _DEP. Nothing in this group depends on RPEN directly: the group's own
        // _DEP entries name IPC0 (TFTP), PEP0, GLNK and IPC0 (PDSR), IPCC and QDIG
        // (SSVC), and none of those five is in this table. GLNK is not in this
        // group and is claimed, which is why the dependency is recorded.
        //
        // Position, and it is the strongest relation in this file's corpus
        // evidence so far: RPEN is immediately before PILC in 19 of the 19 tables
        // that have a PILC. The predecessor is board-specific and useless - SPI4
        // three times, IC14 three, UR19, SP12 and UR15 twice each, then nine
        // singletons - which is the shape of a run's head when whatever precedes
        // it is the last member of the previous block. The two tables with no PILC
        // are the two that put something else after RPEN (vili names IPC0, Waipio
        // names TFTP), so RPEN leads the run's remainder rather than being pinned
        // to PILC. The run itself - RPEN, PILC, CDI, SCSS, ADSP, SLM1, ADCM, AUDD
        // - is never split in any of the 19: measured as a question about the
        // members a table actually has, no table ever interposes a node between
        // two of them, though the adjacencies vary because SCSS is missing from
        // nine of the 19.
        //
        // So the slot is fixed by the node after it, which is already here.
        // Fifteen relations agree with it. Before: UFS0, DEV0, ABD, PMIC and PM01
        // (21 of 21 each), PMAP and PRTC (20), the four-wire ports (10), PML0
        // (11) and IC10 (9).
        // After: PILC (19 of 19), QGP1 and SCM0 (21), QGP0 (19) and IPCC (12).
        // Six relations no slot can satisfy, and they are the same six PILC's
        // comment now records: UCS0 (10 of 10), URS0, USB0, UFN0 and GIO0 (20
        // each) and SPMI (21) all sit after RPEN in the corpus and all sit before
        // it in this file, because this file wrote the early block in an order of
        // its own in which ABD - unanimous before RPEN - keeps company with
        // devices the corpus puts after. They are recorded and not repaired, as
        // there: a reordering is its own measurement. The full count of what this
        // order costs the corpus is measured at TFTP below (Step 4.84) and these
        // six are six of 128 broken relations, over seven placements, one of
        // which is this one.
        Device (RPEN)
        {
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }

            Name (_HID, "QCOM06E1")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
        }

        Device (PILC)
        {
            Name (_HID, "QCOM06E0")  // _HID: Hardware ID
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }
        }

        // TFTP is the firmware transfer service, and QcTftpKmdf.inf names it in
        // one line: %QcTftpKmdf.DeviceDesc%=QcTftpKmdf_Device, ACPI\QCOM06DC,
        // the description string being "Qualcomm(R) TFTP Device" and the binary
        // QcTftpKmdf.sys, a KMDF driver (KmdfLibraryVersion 1.33), class SYSTEM
        // under the same {4d36e97d-...} the nodes around it use, StartType 3,
        // ServiceType 1. Fourteen sections, one hardware id, one file to copy,
        // and no registry data of its own beyond the SoC device category GUID
        // that forty-six of the infs in this set carry.
        //
        // It is the third of three interfaces the modem subsystem hands out.
        // qcsubsys_ext_mpss7280.inf names the set in one line -
        // HKR,AMSS,"Interfaces",%REG_MULTI_SZ%,%GUID_TFTP_INTERFACE%,
        // %GUID_DEVINTERFACE_PIL_TZ%,%GUID_DEVINTERFACE_GLINK% - and a comment
        // under it records where the three come from: "pilapi.h, tftp_api.h,
        // glink_wdf.h in order listed". PILC is the first of the three, written
        // in Step 4.82, and GLNK is the second. This is the third, and one inf
        // publishing the three as a single list is the plainest statement in the
        // driver set that they belong to one subsystem. What the service carries
        // is legible in the other two infs that mention it: mcfg_subsys_ext7280
        // maps remote paths under \rfs\msm\mpss\readonly\firmware\image\ to local
        // ones - kodiak\qdsp6m.qdb among them - under a Mappings\TFTP key named
        // by a SHA-256; and qcsubsys_ext_adsp7280 points its ramdump roots at
        // \DriverData\QUALCOMM\TFTP\rfs\msm\adsp\ramdumps\. Firmware images in
        // and ramdumps out, over the router below and the transport beside it.
        //
        // The id is fixed by this table's service series, which is the exact
        // opposite of how GLNK's was fixed. Twenty-one tables declare a TFTP,
        // under seven ids, and they do not scatter the way the GLNK ids scatter:
        // group the twenty-one by the PILC/RPEN/IPCC triple each one carries and
        // every table in a group takes the same TFTP id, and no id is shared
        // between two groups.
        //
        //   triple              TFTP   tables
        //   06E0 06E1 06C2      06DC   a52sxq, lisa, renoir, Waipio, Cedros IDP,
        //                               Kailua MTP, Kailua QRD
        //   1AE0 1AE1 1AC2      1ADC   lemonade, venus, vili, Lahaina MTP
        //   051B 0533  -        058B   mh2, cepheus, nabu, pipa, vayu
        //   04DF 04E0  -        048B   a52q, miatoll
        //   14DF 14E0  -        148B   surya
        //   023B 026D  -        02F6   caymanslm
        //   25E0 25E1 25C2      25DC   alioth
        //
        // Seven groups, seven ids, twenty-one of twenty-one with no departure in
        // either direction. The high byte is the group's high byte throughout.
        // The low byte is the group's and is not an offset from any member of it:
        // the three groups whose PILC is 06E0, 1AE0 and 25E0 all take DC, the two
        // whose PILC is 04DF and 14DF take 8B, the one whose PILC is 051B takes
        // 8B as well, and the one whose PILC is 023B takes F6. So 8B comes out of
        // three different PILC values, and one PILC value does not decide the low
        // byte on its own. vili is the case that settles the reading: it has no
        // PILC at all and keeps 1AE1 and 1AC2, and it takes 1ADC like the other
        // three of its group. The id is a function of the triple.
        //
        // This is the mirror image of the GLNK measurement. There, six tables
        // carrying 06E0, 06E1 and 06C2 byte for byte split three ways on the
        // transport id - 0A84, 0984 and 0C84 - and the service series was found
        // not to determine the transport series. The same six tables take 06DC
        // here, all six of them, so the service series does determine the service
        // id issued beside it. The transport is free of the series and the
        // service is fixed by it, which is the sharpest form the distinction has
        // taken. Within the row this table is in, that leaves nothing to choose:
        // seven tables carry 06E0, 06E1 and 06C2, and all seven declare QCOM06DC.
        // The driver set agrees and agrees only that far: of the seven ids the
        // corpus uses exactly one appears anywhere in the 112 infs, QCOM06DC in
        // QcTftpKmdf.inf, and no second inf names it.
        //
        // The body is the smallest this file has written, one member smaller than
        // IPC0's. An _HID; a _DEP naming \_SB.IPC0 alone in twenty-one of
        // twenty-one, the first dependency here with one target and no exception;
        // the alias in twenty and Waipio's Method (_SUB) in the twenty-first,
        // which is Waipio departing at the same member as at IPC0 and GLNK. No
        // _CRS in any of the twenty-one, no _UID, no _CID. And a Method (_STA)
        // returning 0x0F in twelve, which is the one member whose presence is not
        // uniform: the nine without it are caymanslm, mh2, a52q, cepheus,
        // miatoll, nabu, pipa, surya and vayu - the 02, 04, 05 and 14 generations
        // - and the twelve with it are the 09, 0A, 0C, 1A and 25 ones. This board
        // is 0A and takes the method.
        //
        // Position, and with it the first full accounting of what this file's
        // order costs the corpus. Of the 439 relations the corpus states
        // unanimously about pairs of nodes both present here, this file
        // contradicts 128, and every one of the 128 is accounted for by seven
        // placements this file made:
        //
        //   node      cost   the placement, against what the corpus does
        //   UCS0       27    at position 3; corpus puts it after PILC
        //   URS0       19    at position 4; likewise
        //   USB0       19    at position 5; likewise
        //   UFN0       19    at position 6; likewise
        //   SPMI       12    at position 7; corpus puts it after SCM0
        //   GIO0        7    at position 13; corpus puts it after SPMI
        //   IC10        1    before UAR2; corpus puts the UART first, 9 of 9
        //                    by the debug name and 2 of 2 for the four-wire
        //                    ports, and this node is the four-wire one
        //   QGP0       10    at position 22; corpus puts it after CPU7
        //   QGP1       10    at position 23; likewise
        //   MMU0 MMU1   4    at positions 24 and 25; corpus puts the pair
        //                    immediately after TFTP and before IPC0, which
        //                    is what the 4 broken relations of IPC0 and GLNK
        //                    are - they are collateral, not a move of theirs
        //
        // The four bus nodes are two thirds of the cost and they are one
        // decision: this file wrote the USB and storage block first and the
        // corpus writes that block after the cameras, near the end of its
        // tables. The rest is smaller and separately decided. That is the
        // baseline a slot argument has to be read against, and it corrects
        // something the four comments before this one imply. The corpus pins a
        // node only relative to other nodes, and this file has already declined
        // to follow it in seven places, so a slot is scored by how many of the
        // relations it satisfies and not by whether any are broken at all. The
        // six relations RPEN's, PILC's, IPC0's and GLNK's comments each record
        // as broken are six of the 128, and not a peculiarity of those slots.
        //
        // Scored that way there is one best slot for this node and it is unique.
        // The relations to satisfy are the 602 table-votes carried by the
        // thirty-two nodes this node has a unanimous relation with; the maximum
        // is 491, and exactly one slot reaches it - between PILC and IPC0. The
        // runner-up reaches 472 and the six votes it loses are PILC's: the
        // corpus puts PILC before TFTP in 19 of 19 tables that have both, vili
        // and Waipio being the two without a PILC. The six relations no slot can
        // satisfy are the block recorded above - UCS0 (10 of 10), URS0, USB0,
        // UFN0 and GIO0 (20 each) and SPMI (21) all follow TFTP in the corpus and
        // all precede it here. So the slot is fixed by the node above it and the
        // node below it, both of which are already in this file, and not by an
        // adjacency read off a corpus table.
        //
        // It cannot reproduce the neighbourhood it was measured in, and that
        // should be said plainly. In six of the seven tables that share this
        // table's service triple, TFTP stands inside the remoteproc cluster:
        // a52sxq, lisa and renoir read ... CSW0 SBTD TFTP QCSK MMU0 MMU1 IMM0
        // IMM1 GPU0 ..., and Cedros and both Kailua differ only in the four nodes
        // before SBTD. Not one of SBTD, QCSK, IMM0 or IMM1 is in this table, and
        // MMU0 and MMU1 are here but twenty nodes away. The seventh table of the
        // group is Waipio, and Waipio is the one that reads like this file:
        // ... BAM5 RPEN TFTP SCM0 TLOG SPMI IPCC IPC0 GLNK ..., with TFTP after
        // RPEN and before IPC0 and GLNK, which is the corridor this slot lands
        // in. Six tables agree on a neighbourhood that cannot be built here and
        // the seventh agrees on a relation that can. That is the whole of what
        // the corpus has to say about where this node goes.
        //
        // What it hands forward. BAM1 and BAM5 precede TFTP in 21 of 21 and
        // precede IPC0 in 21 of 21 and are still absent; in the nine generations
        // the corpus spreads them over, the two always carry the same id as each
        // other, QCOM0A0A in the two 0A tables. So they go between RPEN and this
        // node or between this node and IPC0, and their own comment will have to
        // choose, on the same 602-vote scale and against the same 128-relation
        // baseline this one was measured on. MMU0 and MMU1 want to be adjacent to
        // this node - MMU0 immediately follows TFTP in 7 tables, and the pair
        // follows QCSK in 12 - and cannot be until this file decides to move
        // them, which is a reordering and therefore its own measurement.
        Device (TFTP)
        {
            Name (_HID, "QCOM06DC")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_DEP, Package (One)  // _DEP: Dependencies
            {
                \_SB.IPC0
            })

            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }
        }

        // IPC0 is the IPC router, and qcipcrouter7280.inf names it in one line:
        // %IPC_ROUTER.DeviceDesc%=IPC_ROUTER_Device, ACPI\QCOM0A0D, description
        // string "Qualcomm(R) Data IPC Router Device", service QCIPC_ROUTER,
        // binary qcipcrouter7280.sys, KMDF 1.33, class SYSTEM, StartType 3. It
        // rides the transport GLNK and its inf says so in a place a name can be
        // read off: every one of its five transports carries PortName "IPCRTR".
        //
        // It is also the first node here whose driver ships a user-mode half.
        // The same inf copies qsocketipcrum.dll into the system directory and
        // grants the device one ACE more than qcglink7280.inf does -
        // (A;;GA;;;S-1-5-84-0-0-0-0-0), the user-mode-driver SID, on top of the
        // administrators and LocalSystem that both infs grant - which is what a
        // device with a user-mode client should look like and what the transport
        // next door, with none, does not.
        //
        // Its transport list is five entries, all Type 1, Transport "SMEM", Port
        // "IPCRTR" and MaxIntents 4, differing only in RemoteSS: "mpss", "lpass",
        // "dsps", "cdsp", "wpss". The board declares three glink-edges, labelled
        // "lpass", "modem" and "cdsp". Three of the five names line up and two
        // have no remoteproc here at all - "dsps" and "wpss" - and the one
        // difference in wording is the modem, which this inf calls "mpss" and
        // the board labels "modem". qcglink7280.inf's own SMP2P_interrupts table
        // is a second sighting of the same four remote processors, with host ids
        // SMEM_MODEM 1, SMEM_ADSP 2, SMEM_CDSP 5 and SMEM_WPSS 13 against IPCC
        // clients MPSS 2, LPASS 3, NSP0 6 and WPSS 24 - and the first three are
        // this board's remote-pids exactly, 1, 2 and 5.
        //
        // The id is GLNK's, one generation down, and the two are issued as a
        // pair. Twenty-one tables declare an IPC0, under nine ids, and they line
        // up with the twenty-one GLNK ids table for table:
        //
        //   gen   GLNK   IPC0   tables
        //   02    02F9   021C   caymanslm
        //   05    058D   050E   mh2, cepheus, nabu, pipa, vayu
        //   08    088D   080E   a52q, miatoll
        //   09    0984   090D   renoir, Cedros IDP
        //   0A    0A84   0A0D   a52sxq, lisa
        //   0C    0C84   0C0D   Kailua MTP, Kailua QRD, Waipio
        //   14    148D   140E   surya
        //   1A    1A84   1A0D   lemonade, venus, vili, Lahaina MTP
        //   25    2584   250D   alioth
        //
        // The high byte is never different between the two ids of a row, and the
        // low byte never crosses between the groups: GLNK 84 goes with IPC0 0D,
        // 8D with 0E, and F9 with 1C, and no table departs from its row. So the
        // transport and the router are issued as a pair the way RPEN and PILC are
        // (06E1 and 06E0) - and unlike those two the pairing carries no
        // generation of its own, because there is no IPC0 whose high byte differs
        // from its own GLNK's. QGP0 and QGP1 are the third index over the same
        // nine generations and they partition them the same way - 93 for 05/08/14,
        // 88 for 09/0A/0C/1A/25, F4 for 02 - so three indices now agree on the
        // families. They do not agree on the offset between them: 93 to 8D is six
        // and 88 to 84 is four and F4 to F9 goes the other way by five. The
        // family is a property of the table and the spacing between the indices
        // is not.
        //
        // With the generation fixed at 0A by QGP0 and QGP1's QCOM0A88, the row is
        // the only one that matters here, and both halves of it are written: 0A84
        // was GLNK's, in Step 4.82, and 0A0D is this node's. The corpus's only
        // two 0A tables, a52sxq and lisa, both write it. The driver set agrees
        // and agrees only that far, which is the shape the GLNK comment already
        // records: of the nine IPC0 ids exactly one appears anywhere in the 112
        // infs, QCOM0A0D in qcipcrouter7280.inf, and of the nine GLNK ids exactly
        // one does, QCOM0A84 in qcglink7280.inf. The drivers claim one generation
        // out of nine and it is the one QGP0 had already put this table in.
        //
        // The body is the smallest of any node this file has written: a _DEP
        // naming \_SB.GLNK alone in all twenty-one, an _HID, and the alias. No
        // _UID anywhere in the corpus - none in twenty-one, where the GLNK above
        // it has one in twenty-one - and no _CRS in any table, including the nine
        // where GLNK carries nine Interrupt descriptors. Those nine are the
        // transport's own lines and they stay on the transport. No _STA but the
        // same two tables, vili and Waipio. The alias in twenty and Waipio's
        // Method (_SUB) in the twenty-first: that is the second node in a row
        // where Waipio is the only departure, and with _STA it makes three
        // members on which this board and vili's are the corpus's whole
        // disagreement about this node.
        //
        // Position: the corpus agrees on what follows this node and not on what
        // precedes it. Immediately after it, in 21 of 21, is GLNK. Immediately
        // before it there is no agreement at all - RP1 in 14 tables, GIO0 in 3,
        // QPPX in 2, and RPEN and IPCC in one each - so the run is anchored at its
        // bottom and not its top. Three unanimous relations pin the slot exactly.
        // RPEN precedes IPC0 in 21 of 21 and PILC in 19 of 19, the two tables
        // without a PILC being vili and Waipio, the same two as everywhere else;
        // GLNK follows it in 21 of 21. In this file RPEN, PILC and GLNK are
        // consecutive, so of the two slots those relations allow - above PILC or
        // below it - only the second survives, and this node lands between PILC
        // and GLNK, which is where the GLNK comment said it would land and for
        // the reason it gave. Twenty-four unanimous relations are satisfied by
        // that slot, one more than GLNK's slot satisfied on its own, the
        // difference being the GLNK relation this node now supplies. Not one of
        // the six GLNK's slot breaks is repaired, and they are the same six by
        // name: MMU0 and MMU1 (20 of 20) sit above IPC0 in the corpus and below
        // it here, and UCS0 (10 of 10), UFN0, URS0 and USB0 (20 each) sit below
        // it and above it here. The cause is the early block this file wrote in
        // an order of its own; a reordering is its own measurement, so they are
        // recorded and not repaired, as at RPEN and PILC and GLNK.
        //
        // Three nodes the corpus puts before IPC0 in every table that has both
        // were not in this table when that paragraph was written - BAM1 and BAM5
        // (21 of 21) and TFTP (21 of 21) - and the prediction made here was that
        // TFTP would be written above this node and not below it. It was written
        // in Step 4.84 and it landed above, but the prediction was coarse: the
        // corpus puts PILC before TFTP in 19 of 19 tables that have both, so the
        // slot is not the one above IPC0's other side but the one between PILC
        // and this node. On the 602 table-votes TFTP's relations carry that slot
        // scores 491 and is the unique maximum; the runner-up scores 472 and
        // loses PILC's 19. The adjacency predicted here is unchanged - TFTP is
        // immediately before this node in this file now, and IPC0 is still
        // immediately before GLNK, which is the adjacency the corpus does state,
        // 21 of 21. BAM1 and BAM5 are still absent and their constraint is
        // unchanged and now sharper: they precede both TFTP and this node in 21
        // of 21, so whichever of the two slots they take, they take it above.
        Device (IPC0)
        {
            Name (_HID, "QCOM0A0D")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_DEP, Package (One)  // _DEP: Dependencies
            {
                \_SB.GLNK
            })
        }

        // GLNK is the Generic Link transport, and the name for it that ships is
        // in qcglink7280.inf: one hardware id, %GLINK.DeviceDesc%=GLINK_Device,
        // ACPI\QCOM0A84, and one description string, "Qualcomm(R) Shared Memory
        // Port Device". It is the channel layer the remote processors talk over
        // - each remoteproc on this board carries a glink-edge, and this node is
        // the other end of all of them.
        //
        // The board states the three pieces of it. The transport's memory is the
        // reserved region the smem node points at - memory@80900000,
        // reg = <0x00 0x80900000 0x00 0x200000>, phandle 0x2c - and smem itself
        // is { compatible = "qcom,smem"; memory-region = <0x2c>;
        // hwlocks = <0x2d 0x03> }, the lock being hwlock 3 of hwlock@1f40000
        // (qcom,tcsr-mutex, reg = <0x1f40000 0x40000>). The edges are three:
        //
        //   adsp  remoteproc@3000000  glink-edge { label = "lpass"; remote-pid = 2 }
        //   mpss  (the modem)         glink-edge { label = "modem"; remote-pid = 1 }
        //   cdsp  remoteproc@8300000  glink-edge { label = "cdsp";  remote-pid = 5 }
        //
        // - and all three mbox into the same controller, phandle 0x2e, which is
        // mailbox@408000, qcom,sm6350-ipcc. That controller is already this
        // table's IPCC node, written in Step 4.73 at the id the driver set claims
        // for it and at the board's own single line, INTID 0x104. So the entry
        // this node's _DEP carries is not a namespace formality: it is the
        // interrupt path the board's three glink-edges actually ride on, and this
        // is the first dependency in this table that the board states and the
        // corpus only confirms.
        //
        // The id. Twenty-one tables declare a GLNK, under nine ids: QCOM058D five
        // times, QCOM1A84 four, QCOM0C84 three, QCOM088D, QCOM0A84 and QCOM0984
        // twice each, and QCOM02F9, QCOM2584 and QCOM148D once. The low byte is
        // 84 in five of the nine (09, 0A, 0C, 1A, 25), 8D in three (05, 08, 14)
        // and F9 in one (02) - the same three-way grouping QGP0's comment
        // measured on its own index, 88/93/F4, which is a second sighting of the
        // families and not a coincidence.
        //
        // What settles the id is this table's own four, and the shape of the
        // argument is the hardest form of the standing rule so far. This table
        // already writes PILC QCOM06E0, RPEN QCOM06E1, IPCC QCOM06C2 and QGP0 and
        // QGP1 QCOM0A88. Six corpus tables write the first three of those
        // together - a52sxq, lisa, renoir, Cedros' IDP, and Kailua's MTP and QRD
        // - and they split three ways on GLNK:
        //
        //   a52sxq, lisa                   IDP07280      QCOM0A84
        //   renoir, Cedros IDP             IDP07350      QCOM0984
        //   Kailua MTP, Kailua QRD         MTP/QRD08550  QCOM0C84
        //
        // Two tables each, and Waipio - the seventh table whose RPEN is 06E1 and
        // whose IPCC is 06C2, and the one table of the seven with no PILC - is a
        // third vote for 0C84. Six tables with byte-identical service ids carry
        // three different transport ids, so the service series does not determine
        // the transport series; the table owns both and numbers them apart. That
        // is why this id could not be read off as "the generation after 06", the
        // way RPEN's could not be counted up from PILC's.
        //
        // The fourth id already here decides it. QGP0 and QGP1 are QCOM0A88, and
        // 0A is in the 88 group with 09, 0C, 1A and 25, so this table's QUP-side
        // family was fixed at 0A when those two nodes were written - by the same
        // driver-set argument QGP0's comment records at length. Within 0A there
        // is one GLNK id in the corpus and both of its tables write it: QCOM0A84.
        // The driver set agrees twice rather than once, because the pair is
        // claimed whole: qcglink7280.inf takes ACPI\QCOM0A84 and
        // qcipcrouter7280.inf takes ACPI\QCOM0A0D, and those two are the only
        // 0A-generation ids anywhere in the 112 infs - 0A0D being the next node's
        // and not written here. So the corpus and the drivers both put this node
        // in 0A, and they are two measurements of one thing rather than one
        // measurement repeated.
        //
        // The body is the corpus's, and its shape is one switch with three
        // symptoms. Nine of the twenty-one carry a Method (_CRS) returning nine
        // Interrupt descriptors - eight Edge, one Level - and a one-entry _DEP
        // naming \_SB.RPEN alone; twelve carry no _CRS at all and a two-entry
        // _DEP naming \_SB.IPCC and \_SB.RPEN. The two properties are not merely
        // correlated but coincident: the nine with the resource list are exactly
        // the nine tables that declare no IPCC device, and the twelve without one
        // are exactly the twelve that do. And the same line divides the
        // generations - 02, 05, 08 and 14 on one side, 09, 0A, 0C, 1A and 25 on
        // the other - so the block changed shape once, in the same generation
        // step that added the IPCC node to these tables. This table is on the
        // newer side and this node takes the newer form: the _DEP written and the
        // _CRS not. The withheld resource is withheld for a better reason than
        // the QGP interrupts were: there is no family-0A GLNK _CRS to copy, and
        // the nine that exist are on GIC lines belonging to other boards.
        //
        // _UID is Zero in all twenty-one, as it is on UFS0, URS0, ABD and GIO0
        // here. So is the alias, in all twenty-one - twenty as
        // Alias (\_SB.PSUB, _SUB) and one, Waipio, as the Method (_SUB) form the
        // PEP0 comment records as that board's habit. No _STA: the two tables
        // that carry one are vili and Waipio, and they are also the two tables
        // that write a GLNK _STA on a table whose RPEN has no PILC beside it.
        // That is left as the observation it is.
        //
        // Position, and it is forced by two relations rather than fixed by many.
        // Before: UFS0, DEV0, ABD, PMIC and PM01 (21 of 21 each), PMAP and PRTC
        // (20), the four-wire ports (10), PML0 (11), IC10 (9) and IC11 (3), and
        // then the two
        // nodes above - RPEN (21 of 21) and PILC (19 of 19). After: QGP0 (19 of
        // 19) and QGP1 (21 of 21), then CPU0 to CPU3 (21 each) and CPU4 to CPU7
        // (19 each). All twenty-three of those unanimous relations are satisfied
        // by one slot, and it is the only slot that satisfies them: PILC is above
        // it and QGP0 is below it and the two are adjacent in this file, so the
        // choice here is this node or no position at all. Two relations the
        // corpus nearly agrees on are broken by that slot and are recorded rather
        // than repaired - SCM0 precedes GLNK in 20 of 21 and IPCC in 11 of 12,
        // and this file placed both after it, SCM0 in Step 4.71 and IPCC in 4.73.
        // Six more are unsatisfiable in any slot, and they are the same six the
        // PILC and RPEN comments record: UCS0 (10 of 10), URS0, USB0, UFN0, MMU0
        // and MMU1 (20 each) sit on the far side of GLNK in the corpus and on the
        // near side here, because this file wrote its early block in an order
        // that is not the corpus's.
        //
        // The one thing this slot cannot reproduce is the adjacency it was
        // measured on: the corpus puts GLNK immediately after IPC0 in all
        // twenty-one tables. IPC0 was written in Step 4.83 and landed between
        // PILC and this node, as predicted here, because three unanimous
        // relations leave no other slot - RPEN before it (21 of 21), PILC before
        // it (19 of 19) and GLNK after it (21 of 21) against this file's
        // consecutive RPEN, PILC and GLNK. The prediction was right and it was
        // also short: it counted two relations where the corpus has three, and
        // it did not know that the six relations this slot breaks are the same
        // six by name. That is recorded on IPC0's own comment, not here.
        Device (GLNK)
        {
            Name (_HID, "QCOM0A84")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_UID, Zero)  // _UID: Unique ID
            Name (_DEP, Package (0x02)  // _DEP: Dependencies
            {
                \_SB.IPCC,
                \_SB.RPEN
            })
        }

        // QGP0 and QGP1 are the two GPI DMA controllers, and they are here
        // because the QUP engines above are not self-driving. Two independent
        // sources say so. The corpus: lisa's SP14 and a52sxq's IC14 each carry
        // a three-entry _DEP of \_SB.PEP0, \_SB.QGP1 and \_SB.MMU0 - engine,
        // its own wrapper's GPI DMA controller, and the SMMU in front of it.
        // The board: every live engine in gauguin's tree names one in its dmas
        //
        //   spi@880000   dmas <0x186 0 0 1 0x40 0>, <0x186 1 0 1 0x40 0>
        //   i2c@984000   dmas <0x190 0 1 3 0x40 0>, <0x190 1 1 3 0x40 0>
        //   i2c@988000   dmas <0x190 0 2 3 0x40 0>, <0x190 1 2 3 0x40 0>
        //   spi@98c000   dmas <0x190 0 3 1 0x40 0>, <0x190 1 3 1 0x40 0>
        //   i2c@990000   dmas <0x190 0 4 3 0x40 0>, <0x190 1 4 3 0x40 0>
        //
        // - and the phandle is the wrapper's, not the engine's: 0x186 is
        // qcom,gpi-dma@800000 and every wrapper-1 engine names 0x190,
        // qcom,gpi-dma@900000. Each engine's two specifiers are tx and rx in
        // the order its dma-names gives, and they differ in exactly one cell,
        // the first, 0 then 1 - so the cells after the phandle read <tx/rx, SE
        // index, code, 0x40, 0> and the list above quotes the tx one. The
        // second cell is the engine's SE index in all five - 0, 1, 2, 3, 4 for
        // wrapper 0's SPI0 and the four wrapper-1 buses - another measurement
        // of the numbering the IC nodes above were built on, from a property
        // that had no part in deriving it. The third cell is constant per
        // protocol - 1 on the two SPI engines and 3 on the three I2C engines -
        // which is one more statement of which protocol sits at each slot, and
        // it is read here and not decoded. The fourth and fifth are 0x40 and 0
        // in all ten specifiers. The UART has no dmas property at all - it is
        // the wrapper-0 four-wire port and runs in FIFO mode - and it was
        // called the console here until Step 4.86 read the two console nodes
        // the board declares and found this one is not either of them.
        //
        // Those two _DEPs are also the corpus's cleanest statement of the rule
        // this table has been built on, and it is worth the four lines. lisa's
        // SP14 and a52sxq's IC14 are the same engine: 0x00A94000 + 0x4000,
        // _UID 0x0E, _STR "QUP_1_SE_5", INTID 0x186, the same _DEP - and the
        // two tables differ in exactly two lines, the _HID and the name. lisa
        // calls it QCOM0A0E, an SPI engine, and names it SP14; a52sxq calls the
        // same slot QCOM0A10, an I2C engine, and names it IC14. So the slot
        // identifies the engine and the protocol is the board's, which is
        // exactly what Step 4.70 found on gauguin when the board's tree and the
        // payload's disagreed about two addresses - except that here two
        // shipping boards disagree about one, and the family has been doing
        // this all along. It is also why the letter in an engine's name is the
        // protocol and not the slot: gauguin's two live SPI engines are slots 1
        // and 12 and would be SP1 and SP12, and lisa's SP14 is a52sxq's IC14.
        //
        // The id is QCOM0A88, and the family evidence is broad rather than a
        // pair for once: 20 of the 66 tables declare a QGP device, under nine
        // distinct ids, and the same block is indexed 88 in five families (09,
        // 0A, 0C, 1A, 25), 93 in three (05, 08, 14) and F4 in one (02) - so
        // the index is a property of the family generation and not a constant,
        // and 0A sits in the 88 group. It is claimed outright:
        //
        //   qcgpi7280.inf -> %QCGPI.DeviceDesc%=QCGPI_Device, ACPI\QCOM0A88
        //
        // The resources are the derivation and not a copy, which is why the
        // family's exact numbers are correct here. The board gives each
        // controller a 0x60000 region named "gpi-top" - reg = <0x800000
        // 0x60000> and <0x900000 0x60000> - and the corpus's _CRS is that
        // region less its first 0x4000: 0x50000 long from base + 0x4000, on
        // lisa and on a52sxq alike. Both of gauguin's regions are 0x60000, so
        // the same subtraction gives the same window, and the GPI TOP block
        // the family steps over is the first 0x4000 of a region whose name
        // says it is there.
        //
        // The interrupts do not transfer, and this is the one place in the
        // block where the corpus's values would have been wrong. Each board
        // declares the controller ten interrupt lines - qcom,max-num-gpii = 10
        // on both - and gauguin's are 0x114 through 0x11D on wrapper 0 and
        // 0x2A5 through 0x2AE on wrapper 1. The family declares two of them on
        // both its controllers, and at wrapper 0 those two are gauguin's first
        // two to the digit, 0x114 and 0x115, which is why the family's count is
        // kept and only its numbers are replaced. At wrapper 1 lisa's two are
        // its own lines, 0x137 and 0x138, and gauguin's are 0x2A5 and 0x2A6:
        // the numbering agrees between the two SoCs for the whole wrapper-0
        // ladder and disagrees completely at wrapper 1, and nothing here
        // explains the split, so both numbers are measured rather than argued.
        // qcom,gpii-mask says how many of the ten are instantiated on this
        // board - 0x1F, five, on wrapper 0 and 0x3F, six, on wrapper 1 - and
        // the two the family declares are inside both masks.
        //
        // Neither node carries _DEP: the family's QGP nodes have none, and the
        // dependency runs the other way, from a QUP engine to its GPI DMA. The
        // family's record of that direction is real but not uniform. Three
        // engines per table carry it, and the controller each names is its own
        // wrapper's - the same pairing gauguin's dmas show:
        //
        //   lisa    I2C2 (slot 2)  _DEP {PEP0, QGP0, MMU0}
        //           I2C5 (slot 5)  _DEP {PEP0, QGP0}
        //           SP14 (slot 14) _DEP {PEP0, QGP1, MMU0}
        //
        //   a52sxq  I2C2 (slot 2)  _DEP {PEP0, QGP0, MMU0}
        //           I2C4 (slot 4)  _DEP {PEP0, QGP0}
        //           IC14 (slot 14) _DEP {PEP0, QGP1, MMU0}
        //
        // - and the third entry, MMU0, is on two of the three and not on the
        // third in both tables, so the family's _DEP is not a uniform statement
        // about this hardware and is not a complete one either: gauguin's dmas
        // put all five live engines on a controller where the family records
        // three.
        //
        // The UARTs take the same kind of entry and a shorter one. Across the 34
        // UART nodes the corpus carries, 31 write _DEP {PEP0} alone - lisa's
        // UARD and UAR8, a52sxq's, alioth's, and the rest of the family - three
        // write {PEP0, MMU0}, the UAR4 of a52q, miatoll and surya and the only
        // UART _DEP in the corpus with two entries, and none writes none at all.
        // Six of the 31 write the package width as One rather than 0x01 -
        // caymanslm's two and pipa's four - which is how a first pass here,
        // reading only 0xNN widths, came to count six nodes as having no _DEP
        // before Step 4.86 re-read them. GIO0 never appears in one. This file
        // said it did - "both tables' UARD carries {PEP0, GIO0}" stood here until
        // Step 4.86 counted the 34 - and what named GIO0 was the GpioInt in the
        // UART's _CRS, which 30 of the 34 carry, lisa's UAR8 on pin 0x1F and
        // lisa's UARD on 0x17, the four that do not being pipa's UARD, UR14,
        // UR18 and UR20. A _CRS resource is not a dependency, and the
        // correction is recorded rather than quietly made. GIO0 does carry _DEP
        // entries elsewhere in the family - {PEP0, GIO0, SPI1} in three tables,
        // {GIO0, I2C4} in three, {AFT1, GIO0, IC10} in one - so the reference
        // would be writable here, this table having declared GIO0 as its twelfth
        // device. It is not written because every UART shape the family writes
        // begins with PEP0, which is absent.
        //
        // This table therefore writes no engine _DEP at all, for the same reason
        // the IC nodes above do not: every family shape here needs PEP0, which
        // is absent, and the entries the family writes beside it - GIO0's today,
        // MMU0's since Step 4.72 - are the family's second and third entries
        // rather than entries of their own. One alone would be a shape no engine
        // in the corpus writes. That is a preference about the shape and is
        // recorded as one, not a claim that the entry is un-writable.
        // The consequence is worth stating rather than hiding - qci2c7280.inf
        // and qcgpi7280.inf are both in the Windows driver set, so once those
        // two bind, nothing in this table orders the GPI DMA ahead of the
        // engines that DMA for it. Engine _DEPs wait on PEP0, and when it lands
        // all four shapes can be written in the family's own form at once.
        // Neither QGP node carries _STA either; both nodes in the board's tree
        // are ok and a52sxq's QGP0 and QGP1 are literally byte-identical in the
        // two tables of that family, so there is nothing about this block for a
        // _STA to disagree with. What is deliberately not here is the SMMU:
        // gauguin's controllers sit behind it (iommus = <&apps_smmu 0x56 0> and
        // <&apps_smmu 0x4D6 0>, and phandle 0x17 is apps-smmu@15000000) and
        // every family _DEP that names the GPI DMA names MMU0 beside it, but
        // the family's QGP nodes do not describe it. What would is MMU0, and
        // this table has it as of Step 4.72, two nodes below; the engine _DEPs
        // that want a GPI DMA and an SMMU together can now resolve that half of
        // the reference, and still wait on the half that is PEP0.
        Device (QGP0)
        {
            Name (_HID, "QCOM0A88")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, Zero)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x00804000,         // Address Base
                        0x00050000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000114,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000115,
                    }
                })
                Return (RBUF) /* \_SB_.QGP0._CRS.RBUF */
            }
        }

        Device (QGP1)
        {
            Name (_HID, "QCOM0A88")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, One)  // _UID: Unique ID
            Name (_CCA, Zero)  // _CCA: Cache Coherency Attribute
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x00904000,         // Address Base
                        0x00050000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000002A5,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000002A6,
                    }
                })
                Return (RBUF) /* \_SB_.QGP1._CRS.RBUF */
            }
        }

        // The two SMMUs, and the first pair of nodes in this table whose form
        // came from the driver set's own record of two instances rather than
        // from a sibling table's single one.
        //
        // qcsmmu7280.inf claims one id - ACPI\QCOM0A09 - and hangs two
        // per-instance registry sets off it, Parameters\0 and Parameters\1,
        // with two different register layouts and two different client lists:
        //
        //   Parameters\0  OFFSETS 0x00 0x01 0x02 0x03 0x04 0xFF 0x80
        //                 global 0, global 1, implementation defined 0, perf,
        //                 SSD, implementation defined 1 (invalid), CB
        //                 PREFETCHDETAILS clients  MDP, VFE, VIDEO
        //   Parameters\1  OFFSETS 0x00 0x01 0x02 0x03 0xFF 0x06 0x10
        //                 global 0, global 1, implementation defined 0, perf,
        //                 SSD (invalid), implementation defined 1, CB
        //                 PREFETCHDETAILS client   GPU
        //
        // The second instance is therefore the GPU's SMMU, and the board has
        // exactly two IOMMU blocks: apps-smmu@15000000 and
        // arm,smmu-kgsl@3d40000, whose name says which is which - and whose
        // upstream compatible, qcom,adreno-smmu, says it a second time. Both
        // are QCOM0A09 with _UID Zero and _UID One: 20 of the 66 tables in
        // Silicium-ACPI carry that pair, every one of them with the same two
        // _UIDs and the same one id, and no table anywhere carries a second id
        // for a second SMMU. The driver's two layouts also say which _UID is
        // which block, and the board agrees with them: the instance-0 CB page
        // at 0x80 pages is 0x80000, inside MMU0's single 1 MB window, while the
        // instance-1 CB page at 0x10 pages is 0x10000, which is what decides
        // MMU1's length below.
        //
        // MMU0's resources are the board's, and the corpus agrees with them
        // rather than supplying them. gauguin's apps-smmu@15000000 is
        // qcom,qsmmu-v500 with reg = <0x15000000 0x100000>,
        // #global-interrupts = 1 and #iommu-cells = 2; the kernel tree in
        // Resources/DTBs describes the same block as qcom,sm6350-smmu-500 with
        // the same 1 MB window and the same two counts; and 17 of the 20 tables
        // write _CRS 0x15000000 + 0x100000. This is the one SMMU window in the
        // family that does not move with the SoC - the other three are 0x7FFB8
        // and 0x186000 twice - so here the corpus is a check and not a source.
        //
        // The interrupts are 81, in five runs: 97, 127-150, 213-224, 347-377
        // and 433-445. #global-interrupts = 1, so the first of the board's 81
        // specifiers is the global interrupt and the other 80 are one per
        // context bank, in the board's order. The count is the board's and the
        // ladder's shape is the family's, and neither is in conflict, because
        // every table counts its own SoC: the runs opening at 0xD5 and 0x15B
        // are in all 20 tables and lisa's 0xD5-0xE0 and 0x15B-0x178 sit inside
        // gauguin's to the digit, while the count there is 65 and here 81, and
        // across the corpus MMU0 declares 43 (caymanslm), 57, 58 (Kailua), 63,
        // 65 (lisa, a52sxq, alioth, lemonade) or 71 (venus, vili). The first
        // run is where the two come closest to meeting: lisa's opens at 0x80
        // and gauguin's at 0x7F, one context bank further down the same ladder.
        //
        // The seven TBU pages under this node - anoc_1_tbu@15185000 through
        // pcie_tbu@1519d000, each a 0x1000 page plus an 8-byte control register
        // in the 0x15182200 page - stay outside the window. The board describes
        // them as separate devices, no table in the corpus widens an SMMU
        // window to reach a TBU, and the driver's instance-0 layout has
        // everything it names below 0x81000.
        //
        // MMU1's base is the board's and its length is not. arm,smmu-kgsl@3d40000
        // and qcom,kgsl-iommu@3d40000 - the kgsl stack's own view of the same
        // registers - carry the same reg, <0x3d40000 0x10000>, and both the
        // vendor tree and the kernel tree in Resources/DTBs agree on it. That
        // 0x10000 is a register footprint and not the block's size:
        // attach-impl-defs on this node reaches 0x6b68, which is the page the
        // driver's instance 1 calls implementation defined 1 at 0x06 pages, and
        // the instance-1 CB page is at 0x10 pages - exactly where a 0x10000
        // window ends. So the window is the base the board gives and the 0x20000
        // lisa and a52sxq give the same node, which is also the largest power of
        // two that stops short of the GPU's next region at 0x3d61000. Eight of
        // the 20 MMU1s write 0x10000, ten write 0x20000 and Kailua's two write
        // 0x40000; the ten include gauguin's peers lisa and a52sxq, and the
        // eight include the ones whose context ladder gauguin's matches, so the
        // driver's own offsets are what decides between them here.
        //
        // MMU1's interrupts are 10: two globals, 261 and 263, with
        // #global-interrupts = 2, and eight context banks at 396-403. The eight
        // are the group three other families write - caymanslm, a52q and
        // miatoll, and surya all declare 0x18C-0x193 for their MMU1 - while
        // gauguin's peers lisa and a52sxq put their ten at 710-719. The count is
        // the board's and the ladder is neither family's, which is what happened
        // at wrapper 1 of the GPI DMA above: the numbering agrees between two
        // SoCs where they instantiate the same block and not otherwise, and both
        // numbers here are measured. The board's own ladder backs the eight:
        // 0x18C-0x193 sits in the gap between MMU0's third run, which ends at
        // 0x179, and its fourth, which opens at 0x1B1.
        //
        // Both nodes carry the trigger the board's own cells give - type 4,
        // level, active high, on 81 of 81 and 10 of 10 - and this is the one
        // place in this file where the corpus disagrees with the board about a
        // trigger. All 1400 SMMU interrupt descriptors in the corpus say Edge,
        // ActiveHigh, Exclusive, in every family and every table. The corpus is
        // faithful elsewhere - its UFS0 and its QGP0 are Level, matching the
        // type cells gauguin's dts carries for the same blocks - so the
        // disagreement is specific to this block, and it is recorded rather than
        // resolved. The board's cell is what the kernel programs that GIC line
        // with; if the SMMU driver turns out never to fire, this is the cell to
        // flip first.
        //
        // Three things every corpus SMMU carries are deliberately not here. The
        // _DEP is {PEP0} in all 40 nodes, PEP0 is absent, and the entry names a
        // node this table has not got, so it cannot be written - the rule the
        // QUP engines and the QGP nodes above already follow, and the one thing
        // that changes for all of them on the day PEP0 lands. (This used to add
        // "and a one-entry _DEP is a shape no table has", which was true of the
        // tables read when it was written and is false: Step 4.75 measured ABD's
        // _DEP at one entry in 19 of 21 tables and PRTC's at one entry naming
        // \_SB.PMAP as a string. The conclusion is unchanged and the reason was
        // wrong - size is not what makes an entry un-writable, the missing
        // referent is. See the ABD node above for the reading.) _STA is absent from 19 of the 20 MMU0s and
        // from 9 of the 20 MMU1s; where it is present it is a board's decision -
        // nine MMU1s return 0x0F, which says what leaving the method out says,
        // and three nodes return Zero, alioth's MMU1 and vili's MMU0 and MMU1,
        // which is a board hiding an SMMU from the OS rather than describing
        // one. The GPU's SMMU is hardware this port means to drive, so this
        // table takes the shorter form on both nodes. And every corpus SMMU
        // aliases \_SB.SVMJ to _HRV; SVMJ is a Name (SVMJ, 0xFFFF) declared once
        // under \_SB, 0xFFFF is what every released DSDT carries there rather
        // than SM7225's silicon revision, and this table has no SVMJ at all, so
        // adding it is its own measurement and not a side effect of this one.
        //
        // What this pair unblocks is the engine _DEPs: every family engine _DEP
        // that names a GPI DMA names MMU0 beside it, and both halves of that
        // reference now resolve. All four family shapes still need PEP0, which
        // is why none of them is written yet.
        Device (MMU0)
        {
            Name (_HID, "QCOM0A09")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, Zero)  // _UID: Unique ID
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x15000000,         // Address Base
                        0x00100000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000061,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000007F,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000080,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000081,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000082,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000083,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000084,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000085,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000086,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000087,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000088,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000089,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000008A,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000008B,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000008C,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000008D,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000008E,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000008F,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000090,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000091,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000092,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000093,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000094,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000095,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000096,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000D5,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000D6,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000D7,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000D8,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000D9,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000DA,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000DB,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000DC,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000DD,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000DE,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000DF,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000000E0,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000015B,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000015C,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000015D,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000015E,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000015F,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000160,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000161,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000162,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000163,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000164,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000165,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000166,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000167,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000168,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000169,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000016A,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000016B,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000016C,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000016D,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000016E,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000016F,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000170,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000171,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000172,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000173,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000174,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000175,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000176,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000177,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000178,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000179,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B1,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B2,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B3,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B4,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B5,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B6,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B7,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B8,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001B9,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001BA,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001BB,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001BC,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x000001BD,
                    }
                })
                Return (RBUF) /* \_SB_.MMU0._CRS.RBUF */
            }
        }

        Device (MMU1)
        {
            Name (_HID, "QCOM0A09")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)
            Name (_UID, One)  // _UID: Unique ID
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0x03D40000,         // Address Base
                        0x00020000,         // Address Length
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000105,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000107,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000018C,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000018D,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000018E,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x0000018F,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000190,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000191,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000192,
                    }
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000193,
                    }
                })
                Return (RBUF) /* \_SB_.MMU1._CRS.RBUF */
            }
        }

        // SCM0 - the Secure Channel Manager, the firmware-call interface that
        // sits on no bus and has no window, and the node PMAP's _DEP names
        // beside PMIC and ABD. It was written here, and a step before PMAP,
        // because it is the third of PMAP's three dependencies and the last one
        // this table was missing: PMIC is Step 4.63's, ABD is Step 4.75's, and
        // until this one landed a _DEP for PMAP had an entry it could not
        // write. PMAP followed in Step 4.77 and sits where the corpus puts it,
        // immediately after PM01, so the two are not adjacent in this file.
        // ABD is not adjacent either, and no longer: it was written directly
        // above this node and Step 4.78 moved it up to its corpus slot before
        // PMIC, because PRTC's Field on \_SB.ABD.ROP1 cannot be declared after
        // the region it names. Nothing about SCM0 changed; the reference is
        // kept here so the sentence above does not read as a claim about
        // position.
        // It is also wider than PMAP: across the corpus \_SB.SCM0 is named by
        // PMAP in 20 tables, MON0 in 19 and ARPC in 19, plus NSPM twice and
        // VFE0 twice, so this is a hub and not a leaf, and the reason to write
        // it is the number of things that will later name it.
        //
        // The driver: qcscm.inf, "Qualcomm(R) System Manager SCM Device", class
        // SYSTEM, a kernel-mode service at SERVICE_SYSTEM_START (service type
        // 1, LoadOrderGroup "Extended Base"), shipping qcscm.sys and SCMF.bin,
        // KMDF 1.33, PnpLockDown = 1. It binds ACPI\QCOM04DD and claims no
        // other ACPI id, and its registry section asks ACPI for nothing - the
        // WfdBuffer* and UsrShmMem* parameters are its own.
        //
        // The id is the interesting part, and it is the strongest form of the
        // rule this file has been running on since Step 4.63. The corpus gives
        // this one device SIX ids across 21 declarations:
        //
        //   QCOM04DD  9 tables (lemonade, a52sxq, lisa, renoir, Cedros, Kailua
        //             twice, Lahaina, Waipio)
        //   QCOM050B  5 (mh2, cepheus, nabu, pipa, vayu)
        //   QCOM05DD  3 (alioth, venus, vili - the third of which also carries
        //             the _STA that makes it a second shape below)
        //   QCOM080B  2 (a52q, miatoll)
        //   QCOM0214  1 (caymanslm)
        //   QCOM140B  1 (surya)
        //
        // and exactly one of the six is claimed by any .inf in the five driver
        // trees on this host - QCOM04DD, by the file above. So this is not a
        // case of the driver set agreeing with a majority: five of six ids have
        // no driver at all, and the majority is only 9 of 21. The previous
        // steps could still be read as the corpus and the driver set agreeing
        // most of the time; here the corpus's own spread is the argument for
        // taking the driver's answer, and only that. Note which id the same-SoC
        // table writes - a52q, SM7225, writes QCOM080B, and QCOM080B is one of
        // the five nothing claims. That is Step 4.73's finding a second time,
        // and on the same table.
        //
        // QCOM04DD is also exclusive to this device: nine occurrences in the
        // corpus and every one of them is SCM0, so there is no question of two
        // nodes sharing the id.
        //
        // The shape is ABD's shape, and this step is the second place the pair
        // of exceptions shows up in the same order. Eight of the nine QCOM04DD
        // tables are byte-identical: _HID, _DEP = Package (One) { \_SB.PEP0 },
        // Alias (\_SB.PSUB, _SUB), Name (_UID, Zero), and nothing else. Waipio
        // is the ninth and differs the same two ways it differs on ABD - no
        // _DEP at all, and _SUB written as a method returning \_SB.PSUB - and
        // it is also the only table in the corpus with no PEP0. So the two
        // nodes agree about their odd table, which is worth more than either
        // agreement alone.
        //
        // The _DEP is not written, and here the corpus leaves it more open than
        // it did for ABD. Across the 21 declarations the dependency is present
        // in 11 and absent in 10, and split by its referent the implication is
        // exact in all 21: every table that names \_SB.PEP0 has a PEP0, and the
        // one table that has no PEP0 is also the one with no _DEP. But 11 of
        // the 20 tables that DO have a PEP0 still omit the entry - it is a bare
        // majority, 11 against 9, and SCM0 is the first node here where the
        // corpus would leave the choice genuinely open on a board that had a
        // PEP0. That does not change this table's answer, because gauguin has
        // no PEP0 and the entry would name a device this table has not got; it
        // changes how the answer is recorded, which is as a fact about the
        // referent and not as a fact about what the family does.
        //
        // _STA is written for ABD's reason: two of the 21 carry one and both
        // return 0x0F - Waipio and vili - and the nineteen that omit it are
        // present by ACPI default, so the method costs one line and removes the
        // ambiguity about whether the omission was a decision.
        //
        // No _CRS, and here that is a fact about the device rather than about
        // the corpus: none of the 21 declarations has one, and gauguin's own
        // device tree agrees by carrying nothing to build one from - scm {
        // compatible = "qcom,scm-sm6350", "qcom,scm"; #reset-cells = <1>; } -
        // with no reg and no interrupts, because the SCM is a secure-monitor
        // call interface and not an MMIO block. The node is in the SoC dtsi
        // rather than in the board file, so it is a property of every
        // SM6350/SM7225 board and gauguin inherits it unchanged; the board file
        // has nothing to add to it, which is the same reason there is nothing
        // for a _CRS to describe.
        //
        // The corpus writes Alias (\_SB.PSUB, _SUB); this node writes the
        // relative ^PSUB the other nodes here use. The two are the same
        // reference from depth 1 - SCM0 is \_SB.SCM0 in all 21, and PSUB is
        // \_SB.PSUB - and the relative spelling is this file's.
        Device (SCM0)
        {
            Name (_HID, "QCOM04DD")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_UID, Zero)  // _UID: Unique ID
            Method (_STA, 0, NotSerialized)  // _STA: Status
            {
                Return (0x0F)
            }
        }


        // device PEP0's own _DEP names and therefore the first link of the chain
        // every remaining _DEP in the family begins with. Unlike the last four
        // steps this node is not short of a source; it has two, and they
        // disagree, and the disagreement is decidable.
        //
        // The board: mailbox@408000, compatible "qcom,sm6350-ipcc" then
        // "qcom,ipcc", reg = <0x00 0x408000 0x00 0x1000>, and
        // interrupts = <0x00 0xe4 0x04> - one line, whose INTID is 0xE4 + 32 =
        // 0x104, with the type cell 4 that this file converts to Level,
        // ActiveHigh on every other node.
        //
        // The corpus: 12 of the 66 tables declare an IPCC, under three ids -
        // QCOM06C2 in seven (lisa, a52sxq, Cedros, Kailua twice, Waipio,
        // renoir), QCOM1AC2 in four (lemonade, Lahaina, venus, vili) and
        // QCOM25C2 once (alioth) - and all three carry Name (_UID, Zero) and
        // Alias (\_SB.PSUB, _SUB). Only one of the three ids is reachable here:
        // qcipcc7280.inf claims ACPI\QCOM06C2 outright, and claims no other
        // IPCC id, so the id is the driver's as usual and the family that
        // shares it is gauguin's own pair.
        //
        // What the two disagree about is the _CRS, and the corpus disagrees
        // with itself there as well. All 12 write the triple 0x105, 0x106,
        // 0x107; seven add 0x2EA; and the trigger is Edge in the QCOM06C2
        // variant and Level in the QCOM1AC2 one. Three of those four facts
        // resolve against copying:
        //
        //   * the numbers cannot be gauguin's, and this is provable rather
        //     than merely doubtful. Step 4.72 measured this board's GPU SMMU at
        //     0x105, 0x107 and 0x18C-0x193 from the board's own interrupt
        //     cells - MMU1's first global line and one of its context banks sit
        //     on two of the three numbers the corpus would give the IPCC. One
        //     GIC line has one owner, so the triple is not this board's, and
        //     the board's own single 0x104 is what the node takes.
        //   * 0x2EA is in one variant and not the other, so it is a leaf line
        //     and not part of the block.
        //   * the trigger: the corpus's two variants disagree with each other,
        //     which leaves the board's cell as the only third opinion, and it
        //     says Level, which sides with QCOM1AC2. This is the second time in
        //     this file that a trigger has been decided against a corpus
        //     majority - 4.72 recorded the SMMU pair's Edge against this
        //     board's Level for the same kind of reason - and the two cases are
        //     worth keeping apart: there the corpus was unanimous and the board
        //     was the lone dissent, here the corpus splits and the board breaks
        //     the tie.
        //
        // One line where the corpus has three or four is the honest form of
        // this node and not a truncation: the board's node carries exactly one
        // interrupt, and this file has written what the board carries since
        // Step 4.70 - the two SPI engines were withheld for the opposite
        // reason, a driver and not a resource, and are still withheld.
        //
        // There is no _DEP. No IPCC in the corpus carries one, and the
        // dependency runs the other way: PEP0's _DEP is Package (One) {
        // \_SB.IPCC }, so this node is what that reference resolves to on the
        // day PEP0 is written. It is also the reason PEP0 has stayed out of
        // this table - its _DEP was a one-entry package naming a device the
        // table did not have, and it now has one fewer such reference.
        //
        // Not written, and noted for PEP0's own step rather than here: PEP0's
        // _SUB compares \_SB.PSUB against "IDP07280" and "CRD07280" and returns
        // whichever matched, falling off the end otherwise. This board's PSUB
        // is "MTP07225" - it is the MTP of SM7225 - so on this table that
        // method has no branch to take, which is a defect in the method and not
        // in the value.
        Device (IPCC)
        {
            Name (_HID, "QCOM06C2")  // _HID: Hardware ID
            Alias (^PSUB, _SUB)  // _SUB: Subsystem ID
            Name (_UID, Zero)  // _UID: Unique ID
            Method (_CRS, 0, NotSerialized)  // _CRS: Current Resource Settings
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive, ,, )
                    {
                        0x00000104,
                    }
                })
                Return (RBUF) /* \_SB_.IPCC._CRS.RBUF */
            }
        }

        // The first thirteen thermal zones, and the family this table's zone
        // ids belong to - which the driver set decides rather than the SoC.
        //
        // qcpep.wd7280.inf is the one driver on this host that binds a thermal
        // zone, and it lists the ids it accepts: 0A17, 0A37-0A51, 0A57-0A64,
        // 0A91, 0A92, 0ABF, 0AC8-0ACB, 0AD4 and 0AD8-0AE0. That is the family
        // lisa and a52sxq write and no other. The other SM7225 table in the
        // corpus, a52q, uses a different one - 084B, 084F, 085C, 085D, 085E,
        // 085F, 0862, 0863, 0865, 0867, 089D, 089E - and no inf on this host
        // claims a single one of those, so the same-SoC table is not the
        // modelling table here. The id family tracks the driver set, the way
        // the SMMU's did in 4.72, and lisa/a52sxq are what the driver binds.
        //
        // The zones are also the third appearance of the pattern 4.72 found on
        // the SMMUs: one id, an _UID per instance. 0A58, 0A59 and 0AD4 each
        // carry _UID Zero and _UID One in every one of the 20 tables that has
        // them, and no table anywhere gives a second id for a second instance
        // of the same block. A zone is therefore named for the sensor block it
        // belongs to and not for the sensor: the corpus device names TZ0-TZ7,
        // TZ9-TZ13 are the family's own labels, their numeric suffix is not
        // the _UID, and they are kept here so the provenance stays visible.
        //
        // _PSV, and where the two sides disagree. The board's device tree
        // carries 92 zones and 127 trips and the trips are the only place a
        // temperature appears, so a _PSV is written only where this board has
        // a trip to cite:
        //
        //   gpu-trip0   95000  0x0E60  ->  TZ6   0A91  (lisa: 0x0E60, agrees)
        //   npu-trip0   95000  0x0E60  ->  TZ10  0A92  (lisa: 0x0E60, agrees)
        //   cpuNN-config 110000 0x0EF6 ->  the three _UID One zones, on the
        //                                  eight cpu-*-step zones' own value
        //
        // The third line is a disagreement recorded rather than smoothed over:
        // lisa writes 0x0EC4 (105 C) on those three, gauguin's board has no
        // 105 C trip anywhere, and its closed-loop cpu zones step at 110 C.
        // The board's number wins because the number is a thermal-design
        // decision and the board is the design. The same rule drops 0ABF's
        // _PSV, which lisa sets to 0x0EC4: nothing on this board names that
        // block, so there is no measurement here that would put a temperature
        // on it. _CRT is the opposite case - lisa writes it only on its PMIC
        // group, but every one of gauguin's SoC zones carries a reset-mon-cfg
        // trip at 115000, which is 0x0F28, which is the number lisa's PMIC
        // group already uses; it is written here on the board's evidence.
        //
        // _TC1, _TC2, _TSP, _MTL and _TZP are the family's, because they are
        // coefficients and periods with no counterpart on either side of the
        // join, and the driver's thermal engine reads them as policy.
        //
        // _TZD is not written. lisa's GPU zone lists \_SB.GPU0 and its PMIC
        // group lists \_SB.PEP0; this table has neither device, and a _TZD
        // that names a device this table does not have is worse than no _TZD.
        // _DEP is the one every corpus zone carries and the one every omitted
        // node here is waiting on: {PEP0} alone on all but the PMIC group, and
        // the referent is a node this table has not got - the same rule the QUP
        // engines, the QGP nodes and the two SMMUs already follow, and the same
        // thing that changes for all of them on the day PEP0 lands. (This used
        // to read "and a one-entry _DEP is a shape no table has", which Step
        // 4.75 falsified - see the ABD node above.)
        //
        // Four groups are deliberately not here yet, and each is its own
        // measurement: the PMIC group 0AC8/0AC9/0ACB, which is the only one
        // whose _DEP has two entries and the only one with a _DSM and a GpioInt
        // _CRS on \\_SB.PM01 pin 0x00C0; the ADC group 0A5F/0A61/0A63, which
        // _DEPs two devices this table has not got; TZ99 0A5A, whose _TZD is a
        // 13-entry package naming five containers and which is the one zone
        // that summarizes the others; and the nine 04C0-04C8 the modem's own
        // qcthermalmdm7280.inf binds, which the modem cannot be driven from.
        ThermalZone (TZ0)
        {
            Name (_HID, "QCOM0A58")
            Name (_UID, Zero)
            Name (TTSP, One)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ0.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ1)
        {
            Name (_HID, "QCOM0A58")
            Name (_UID, One)
            Name (TPSV, 0x0EF6)
            Method (_PSV, 0, NotSerialized) { Return (\_SB.TZ1.TPSV) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ1.TCRT) }
            Name (_MTL, 0x14)
            Name (TTC1, Zero)
            Method (_TC1, 0, NotSerialized) { Return (\_SB.TZ1.TTC1) }
            Name (TTC2, One)
            Method (_TC2, 0, NotSerialized) { Return (\_SB.TZ1.TTC2) }
            Name (TTSP, One)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ1.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ2)
        {
            Name (_HID, "QCOM0A59")
            Name (_UID, Zero)
            Name (TTSP, One)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ2.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ3)
        {
            Name (_HID, "QCOM0A59")
            Name (_UID, One)
            Name (TPSV, 0x0EF6)
            Method (_PSV, 0, NotSerialized) { Return (\_SB.TZ3.TPSV) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ3.TCRT) }
            Name (_MTL, 0x14)
            Name (TTC1, Zero)
            Method (_TC1, 0, NotSerialized) { Return (\_SB.TZ3.TTC1) }
            Name (TTC2, One)
            Method (_TC2, 0, NotSerialized) { Return (\_SB.TZ3.TTC2) }
            Name (TTSP, One)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ3.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ4)
        {
            Name (_HID, "QCOM0AD4")
            Name (_UID, Zero)
            Name (TTSP, One)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ4.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ5)
        {
            Name (_HID, "QCOM0AD4")
            Name (_UID, One)
            Name (TPSV, 0x0EF6)
            Method (_PSV, 0, NotSerialized) { Return (\_SB.TZ5.TPSV) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ5.TCRT) }
            Name (_MTL, 0x14)
            Name (TTC1, Zero)
            Method (_TC1, 0, NotSerialized) { Return (\_SB.TZ5.TTC1) }
            Name (TTC2, One)
            Method (_TC2, 0, NotSerialized) { Return (\_SB.TZ5.TTC2) }
            Name (TTSP, One)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ5.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ6)
        {
            Name (_HID, "QCOM0A91")
            Name (_UID, Zero)
            Name (TPSV, 0x0E60)
            Method (_PSV, 0, NotSerialized) { Return (\_SB.TZ6.TPSV) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ6.TCRT) }
            Name (TTC1, One)
            Method (_TC1, 0, NotSerialized) { Return (\_SB.TZ6.TTC1) }
            Name (TTC2, 0x02)
            Method (_TC2, 0, NotSerialized) { Return (\_SB.TZ6.TTC2) }
            Name (TTSP, 0x02)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ6.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ7)
        {
            Name (_HID, "QCOM0A51")
            Name (_UID, Zero)
            Name (TTSP, 0x32)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ7.TTSP) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ7.TCRT) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ9)
        {
            Name (_HID, "QCOM0A4C")
            Name (_UID, Zero)
            Name (TTSP, 0x32)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ9.TTSP) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ9.TCRT) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ10)
        {
            Name (_HID, "QCOM0A92")
            Name (_UID, Zero)
            Name (TPSV, 0x0E60)
            Method (_PSV, 0, NotSerialized) { Return (\_SB.TZ10.TPSV) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ10.TCRT) }
            Name (TTC1, One)
            Method (_TC1, 0, NotSerialized) { Return (\_SB.TZ10.TTC1) }
            Name (TTC2, 0x02)
            Method (_TC2, 0, NotSerialized) { Return (\_SB.TZ10.TTC2) }
            Name (TTSP, 0x0A)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ10.TTSP) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ11)
        {
            Name (_HID, "QCOM0ABF")
            Name (_UID, Zero)
            Name (TTC1, Zero)
            Method (_TC1, 0, NotSerialized) { Return (\_SB.TZ11.TTC1) }
            Name (TTC2, One)
            Method (_TC2, 0, NotSerialized) { Return (\_SB.TZ11.TTC2) }
            Name (TTSP, 0x32)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ11.TTSP) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ11.TCRT) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ12)
        {
            Name (_HID, "QCOM0A4B")
            Name (_UID, Zero)
            Name (TTSP, 0x32)
            Method (_TSP, 0, NotSerialized) { Return (\_SB.TZ12.TTSP) }
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ12.TCRT) }
            Name (_TZP, Zero)
        }

        ThermalZone (TZ13)
        {
            Name (_HID, "QCOM0A57")
            Name (_UID, Zero)
            Name (TCRT, 0x0F28)
            Method (_CRT, 0, NotSerialized) { Return (\_SB.TZ13.TCRT) }
            Name (_TZP, Zero)
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
