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
         * same way it settled the PMIC-GPIO node above. The suffix is not
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
         * Deliberately not added here, with the reason each time. PMAP exists in
         * 19 tables and the 7280 set claims QCOM0A2C, but its _DEP names
         * \_SB.ABD and \_SB.SCM0 and neither node is in this file, so it would
         * be a dangling dependency. PMBM (QCOM0A2A) and PMGK (QCOM0A8E) are in
         * the corpus and are NOT claimed by the 7280 set, so adding them would
         * put two devices in Device Manager that nothing binds. PML0
         * (QCOM0AD3) is claimed, but it is an I2C-attached PMIC - lisa's _CRS
         * gives it four I2C addresses on \_SB.I2C2 - and this file has no I2C
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
