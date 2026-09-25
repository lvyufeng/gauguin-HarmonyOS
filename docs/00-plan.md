# 00 — Phase plan

Goal: **Windows 11 on ARM running on the gauguin test phone**, with as much of the hardware
driven as is physically possible. See [`README.md`](../README.md) for the honest ceiling —
cellular and cameras are permanently out of reach, Wi-Fi/audio/GPU are hard but plausible.

Each phase ends with a commit. A phase is only "done" when its gate has actually been
observed on hardware, not when the code compiles.

## Where things actually stand (2026-09-23)

Nothing below is "done" except P0, and the only gate observed on hardware is
P0's plus the first half of P2's (see its row). This table is the honest state;
the sections under it are the plan.

| phase | gate | state |
|---|---|---|
| **P0** survey + backup | partitions dumped and verified; no existing port | **done** — 74 partitions carved and signature-checked, `boot`/`abl`/`recovery` hashes match the device, 86 XBL drivers recovered, and `git ls-files Silicon/Qualcomm` confirms no SM7225 package upstream |
| **P1** mainline kernel | device boots mainline and prints something | **not done** — `work/out/boot-pstore.img` is built, reproducible (`make_boot_image.py --kernel`), and carries both channels a device with no UART needs: the panel itself (`simple-framebuffer` + `simpledrm` + fbcon, so the boot log is photographed off the screen) and pstore (`console-ramoops-0`, readable from Android after a warm reboot). The earlier `fastboot boot` was refused with `Failed to load/authenticate boot image` on the RAM path, and the partition path has never been tried |
| **P2** UEFI skeleton | the boot manager draws on the phone's screen and UFS appears as a block device | **half met, and it is the half that decides viability** — **our firmware executes on this phone**. The image carrying the current device tree was written to `boot`, and on the reboot the panel filled with our own output, ending in `ASSERT [DxeCore] DxeMain.c(593)`. That text can only come from us: `DxeMain.c:593` is our line, and in a DEBUG build `SerialPortLib` is bound to `FrameBufferSerialPortLib`, so every `DEBUG ()` string is drawn into the framebuffer — which is why the firmware can talk while there is no shell, no boot-manager menu and no UART. (It also means text on the panel is not evidence that BDS ran.) What remains is the second half: DXE stops because at least one *architectural protocol* was never installed, and the name of the first missing one is printed two lines above the assert, on a screen that is legible by design (`GetFontScale ()` gives 10×24 glyphs, ~90×100 of them) and is wiped only when it scrolls. `docs/08` step 4.8 has the reading, and the dep chain that narrows it. The three earlier attempts stopped before any of our code for a reason now fixed: the tree in the image had no `/__symbols__`, so ABL refused the vendor overlay (`docs/07`). The gate said "reaches a shell" until the volume was inventoried and the shell turned out to be absent from *every* platform in the tree, `suryaPkg` included, so it would not have distinguished our firmware from a working one |
| **P3** ACPI | Windows installer boots and sees UFS | **item 1 well along, 2–4 not started** — the DSDT, `APIC`, `FACP`, `FACS`, `GTDT` and the shared `SSDT` are in the build and were read back out of the artifact; UFS, USB, the PMIC family, the GPIO controller, the Type-C controller, all six of the QUP engines the board leaves running, the two GPI DMA controllers those engines are wired to the two SMMUs those controllers sit behind and the `IPCC` mailbox every one of the family's `_DEP`s resolves through (Steps 4.69–4.74) are described, thirteen of the thermal zones are described with them, and buttons are not. Those need `_HID`s a device tree does not carry, and the reference corpus shows the id is `QCOM<family byte><block index>`. Both inputs are now known: the index is measured off the 66-table corpus, and the byte is `0A`, measured against the SC7280/Kodiak Windows driver set (8 of gauguin's 10 blocks named, 0 of 10 under every other byte) and corroborated by the only two corpus tables carrying those ids, `Xiaomi/lisa` and `Samsung/a52sxq`. Every node added since Step 4.63 answers a shipped `.inf` that names its id outright. The one block no driver names is the TSENS *controller*; the 29 thermal *zones* are a different matter and Step 4.66's census found all of their ids determined and claimed, nine of them (`QCOM04C0`–`QCOM04C8`) on family `04` under `qcthermalmdm7280.inf` rather than family `0A` under `qcpep`, which is why the family byte is the id space a block was defined in and not always the SoC. Step 4.67 then measured the other half and found the zone *data* is not missing either — `work/out/gauguin.dts` carries zones with sensor indices and trips — so what the zones still needed was the mapping from the device tree's per-zone names to the corpus's per-`_HID`-plus-`_UID` grouping, which is a join and not a search. Step 4.73 closed that join and found the names were never the join at all: it is the driver's id list, and the same-SoC table is the wrong source — see below. The count this cell carried from 4.66/4.67 was 40 zones, and the board's own tree has **92** (95 directories under `soc/thermal-zones`, three of them not zones) with **127 trip points**, every one of them `passive`. The same step took `PEP0` apart: it is a ~630-line skeleton plus one ~58-line case per thermal zone inside `THTZ`, the zero-zone form is shipped as a five-line `Return (0xFFFF)`, `THTZ` is called by nothing in any table that declares it, and `PEP0` reaches `IPCC`, `ABD`, `AGR0`, six subsystem `_STA` tests and the zones — so it cannot be ported whole — and not the 13,000-line node this tree described it as in two other places until Step 4.67 corrected both, a factor of five. Step 4.68 then finished the correction 4.66 had left half-done — `CCVL` is bound on one device in the family and was bound on three here, so the two that named the same `\_SB.CCST` unreachably went, while `PHYC` stayed on the same evidence — and found the node the Type-C path is actually missing at its root: `QCOM0A10`, the QUP I2C engine, claimed by `qci2c7280.inf` and carried by exactly two of the 66 tables, lisa and a52sxq, the same pair that settled `URS0`'s and `UCS0`'s ids. `PEP0`'s `Field (\_SB.ABD.ROP1)`, the `_DEP` `UCS0` cannot yet write, and the I2C addresses `PML0` needs all wait on it, which makes the I2C node the one item the other three converge on; gauguin's tree has the engines for it — five I2C serial engines across two `geniqup` wrappers, three of them `okay` in the device's own tree and two in the payload's, which corrects this cell's earlier count of one. Step 4.69 then answered the question 4.68 had left open and found it was two questions. The arithmetic one closes: `_UID = 8*wrapper + SE + 1` fits all 23 engine nodes in the three tables that carry any — lisa, a52sxq, the SC7280 CRD — with no exception, including the CRD's `I2C1` at `_UID One`, and the device name is the same ladder in decimal, `"I2C"` + `_UID` up to 9 and `"IC"` + `_UID` from 10, which is `I2C1`, `I2C2`, `I2C4`, `I2C5`, `I2C9`, `IC10`, `IC11`, `IC14` exactly. So `IC11` is the family's name for slot 11 and not a bus with known contents: gauguin's slot 11 is `i2c@988000`, the touch and NFC bus, reached by the same GSI `0x183` lisa's `IC11` is reached by. The wiring question is answered by gauguin's own tree, and the answer is wrapper 1 engine 4 — `i2c@990000`, the only bus in the tree carrying `fsa4480@42` (the Type-C analog switch) with `qcom,pm8008@8`/`@9`, `qcom,smb1396@34`, `bq25970-standalone@66` and `aw8624_haptic@5A`, and the only one marked `qcom,shared` with an explicit `qcom,clk-freq-out`. The engine index is measured four ways that all agree: the address stride `(0x990000 − 0x980000) / 0x4000`, the TLMM function the payload's `pinctrl-0` names (`qup14`), the `qcom,wrapper-core` phandle `0x193` that names `qcom,qupv3_1_geni_se@9c0000` outright, and the interrupt ladder — gauguin's wrapper-1 GSIs `0x181`–`0x185` are the corpus's `0x181`–`0x186` one per slot, and its wrapper-0 SE 0 is the CRD's `I2C1` at `0x279`, even though the two SoCs put the wrappers at different addresses (wrapper 1: gauguin `0x980000`, the CRD and a52sxq `0xA80000`; wrapper 0: gauguin `0x880000`, the CRD `0x980000`), which is the ids' own lesson a second time — the slot travels between SoCs and the address does not. The node written is **`IC13` at `QCOM0A10`**, slot 13, `_UID 0x0D`, `0x990000 + 0x4000`, INTID `0x185`; `_DEP` is omitted as on `UCS0` because every I2C node in the family depends on `\_SB.PEP0` and this table has none, and the `,Shared` suffix is omitted because lisa's only one is on `I2C2` while lisa's own charger engine `IC11` has none. The AML is now **2,465 bytes**, `b9e70ee6…65a3df6`, and the delta is exactly the node: disassembling the old and new tables and diffing them gives `IC13` and nothing else, and `SSDT`, `APIC`, `FACP`, `FACS` and `GTDT` are byte-identical sha256 for sha256 between the two payloads. Step 4.70 then finished the engines and corrected this cell's own count — it said five engines at I2C addresses with three `okay`, and the number that matters is not the node count but the enabled one: the board leaves **six** QUP engines running, three I2C, two SPI and one UART, read off the `status` of every node at a QUP address in the tree dumped from the running kernel — `0x880000` SPI with `touch_spi@0`, `0x884000` the 4-wire UART the console runs on, `0x984000` I2C with two `cs35l41`, `0x988000` I2C with `focaltech@38` and `nq@28`, `0x98c000` SPI with `irled@0`, `0x990000` I2C with the charger cluster. The two that were missed were missed because the payload's tree disagrees with the board's about exactly those two addresses (`i2c@880000` against `spi@880000`, `serial@98c000` against `spi@98c000`), and the board wins for the same reason it won the last two times. Three nodes are written — **`IC10`** (`QCOM0A10`, `_UID 0x0A`, `0x984000`, INTID `0x182`), **`IC11`** (`QCOM0A10`, `_UID 0x0B`, `0x988000`, INTID `0x183`) and **`UARD`** (`QCOM0A16`, `_UID 0x02`, `0x884000`, INTID `0x27A`) — which brings the table to 22 devices and every one of gauguin's six live engines under a node. The two SPI engines are withheld and the reason is one search, not a preference: `QCOM0A0E` is claimed by no `.inf` in any of the five driver trees on this host, where `QCOM0A0B`/`QCOM0A0C`/`QCOM0A10`/`QCOM0A16` are each claimed outright, so `SP1` and `SP12` would register two unknown devices reserving memory and GSIs against no driver — withheld, not refused, since lisa declares an `SP14` and the node is four lines the day a driver appears. That is why this cell's "the I2C engines" is now stated as a count of engines and not a count of buses: the buses are all described and what is missing from them is the drivers. Three more things moved from the corpus to the board. The GSIs: a device-tree interrupt specifier `<0 N 4>` names GIC INTID `N + 32`, ACPI's GSI is that INTID, and all six of gauguin's `interrupts` values convert to exactly the ladder the corpus shows — `0x259 + 32 = 0x279`, the CRD's `I2C1`, included — so the ladder is now measured twice from two directions. The protocols: each engine's is the TLMM group its `pinctrl-0` resolves to (`se0_spi`, `se1_4uart`, `se2_i2c`/`se2_spi`, `se6_i2c`/`se6_spi`, `se7_2uart`/`se7_i2c`, `se8_i2c`, `se9_2uart`/`se9_spi`, `se10_i2c`), and the `se` index in those names is a **fourth** index convention distinct from the slot, the address and the `_STR`: `se0`–`se5` are wrapper 0 and wrapper 1 starts at `se6`, so `se7` is wrapper 1's engine 1 and not engine 7. And the naming ladder has one exception, which venus settles: the debug UART is called `UARD` at any slot and its slot lives only in `_UID` — lisa `_UID 6`, venus `_UID 4` with `_STR "QUP_0_SE_3,DBG"` — which is why gauguin's console engine is `UARD` `_UID 2` and not `UAR2`, and why `8 * 0 + 1 + 1 = 2` still holds underneath the exception. The touch question 4.69 left open closes the opposite way from the node names: `spi@880000`'s `touch_spi@0` carries a `compatible`, a `reg` and a clock and nothing else, while `focaltech@38` on `i2c@988000` carries the interrupt, the reset, the supply, six panel phandles and `max-touch-number 5`, and `qcom,i2c-touch-active = "focaltech,fts_ts"` marks the I2C path active — so slot 11 is the touch's bus, which is the same slot lisa's `IC11` reaches by the same GSI `0x183`, and the node names were never evidence. That bus's driver set is empty in both directions, which makes `IC11` the prerequisite and not the feature. The AML is now **2,833 bytes**, `a98c1a98095f77e2a1dde01cefe99b9a91ae6af1926f7fed5053353581f5af5b` with checksum valid, the delta is exactly the three nodes (72 added disassembly lines, 3 × 24, 0 changed, 0 removed; 19 devices to 22), `SSDT`, `APIC`, `FACP`, `FACS` and `GTDT` are byte-identical to 4.69's again, the devices whose id a driver in the set claims went 6 → **9** of 22, and the same two — `QCOM0A8B` (UFS) and `QCOM24A5` — remain unclaimed. Step 4.71 then wrote the two controllers every live engine above is not self-driving without — **`QGP0` and `QGP1`, `QCOM0A88`, `_UID Zero`/`One`, windows `0x00804000 + 0x50000` and `0x00904000 + 0x50000`, GSIs `0x114`/`0x115` and `0x2A5`/`0x2A6`** — which is the pair this cell had listed first among the measured-but-unwritten. The pairing is stated twice and independently: in the board's `dmas`, where each of the five live engines names its **own wrapper's** controller (`0x186` is `qcom,gpi-dma@800000`, `0x190` is `qcom,gpi-dma@900000`, and none crosses over), and in the corpus's `_DEP`, where three engines per table name a `QGP`. A `dmas` specifier turns out to be six entries — `<phandle, tx/rx, SE index, code, 0x40, 0>` — and its second cell is the SE index in all five, a further measurement of the numbering the `IC` nodes were built on, from a property that had no part in deriving it, while its third is constant per protocol (1 on the two SPI engines, 3 on the three I2C engines) and is recorded rather than decoded. The corpus's own clearest proof of the rule this table has run on since 4.70 comes out of the same pair of tables: lisa's `SP14` and a52sxq's `IC14` are the same engine — same address, same `_UID 0x0E`, same `_STR "QUP_1_SE_5"`, same `INTID 0x186`, same `_DEP` — and differ in exactly two lines, the `_HID` (`QCOM0A0E` against `QCOM0A10`) and the name; so **the slot identifies the engine and the board identifies the protocol**, which is why gauguin's two withheld SPI engines would be `SP1` and `SP12`, and why lisa can run an SPI engine where a52sxq runs an I2C one at the identical slot. The id is broad rather than a pair for once: 20 of the 66 tables declare a `QGP` under nine distinct ids, the same block is indexed `88` in five families (09, 0A, 0C, 1A, 25), `93` in three and `F4` in one, so the index is a property of the generation and `0A` sits in the `88` group — and `qcgpi7280.inf` claims it outright. The window is a derivation and not a copy: the corpus's `_CRS` is the board's region less its first `0x4000`, `0x50000` long on lisa and on a52sxq alike, and both of gauguin's regions are `0x60000` under the reg-name `"gpi-top"` that says the skipped block is there — the second time this cell has been able to derive a family number rather than copy one. **The interrupts are the one place in the step where copying the corpus would have been wrong**: both boards declare ten lines and `qcom,max-num-gpii = 10`, the family declares two, and at wrapper 0 the family's two are gauguin's first two to the digit (`0x114`, `0x115`) while at wrapper 1 the family's `0x137`/`0x138` sit 374 from gauguin's `0x2A5`/`0x2A6`; the count transfers and the numbers do not, and nothing here explains the split. Neither node carries a `_DEP`: the family's own `QGP` nodes have none, the dependency runs engine-to-controller, and all three of the family's shapes for an engine `_DEP` begin with `PEP0`, which this table still does not have, while a one-entry `_DEP` is a shape no family table carries. The AML is now **3,027 bytes**, `fd760ef74f093d5d65d7959702f668af26f2f09daf9cb32af1f92cd6385939f3`, checksum `0xCC`, the delta is exactly the two nodes (3 changed header lines, 57 added, 0 removed; 22 devices to 24), `SSDT`, `APIC`, `FACP`, `FACS` and `GTDT` are byte-identical to 4.70's a third time, and the claim count is now **11 of 24**. Step 4.72 then reversed the negative result 4.71 left behind and wrote the node it had recorded as unbuildable: both SMMUs are derivable and both are now in the table — **`MMU0` (`QCOM0A09`, `_UID Zero`, `0x15000000 + 0x100000`, 81 interrupts in five runs `0x61`, `0x7F`–`0x96`, `0xD5`–`0xE0`, `0x15B`–`0x179`, `0x1B1`–`0x1BD`) and `MMU1` (`QCOM0A09`, `_UID One`, `0x03D40000 + 0x00020000`, ten interrupts `0x105`, `0x107`, `0x18C`–`0x193`)** — which unblocks the other half of every engine `_DEP` that names a GPI DMA. The pair is the first in this table whose *form*, meaning which resources go on which of two nodes sharing one id, came out of a driver's record of two instances rather than a sibling table's single one: `qcsmmu7280.inf` claims `ACPI\QCOM0A09` once and hangs two per-instance registry sets off it, `Parameters\0` with its context-bank page at `0x80` pages and `MDP`/`VFE`/`VIDEO` as its `PREFETCHDETAILS` clients and `Parameters\1` with its CB page at `0x10` pages and **`GPU`** as its only client — the file's own comment calls the second "GFX MMU version specific settings" — so instance 1 is the GPU's SMMU and the board's two IOMMU blocks identify themselves three ways over (`apps-smmu@15000000` and `arm,smmu-kgsl@3d40000` in the vendor tree, the same two as `qcom,sm6350-smmu-500` and `qcom,sm6350-smmu-v2` with **`qcom,adreno-smmu`** in the payload's kernel tree, and the driver's client lists). The corpus agrees about the id and supplies neither: 20 of 66 tables carry the pair, all with `_UID Zero`/`One` under the same one id, and no table declares a second id for a second SMMU. `MMU0`'s window is the board's rather than the family's for once — `0x15000000 + `0x100000` is the one SMMU window in the family that does not move with the SoC (17 of the 20) — and its 81 interrupts are `#global-interrupts = 1` plus 80 context banks, the count the board's and the ladder's shape the family's without conflict: the runs opening at `0xD5` and `0x15B` are in all 20 tables, lisa's `0xD5`–`0xE0` and `0x15B`–`0x178` sit inside gauguin's to the digit, and lisa's own count is 65 against this board's 81, across a corpus that declares 43, 57, 58, 63, 65 or 71. `MMU1`'s base is the board's and its **length is the driver's**: the board's `0x10000` is a register footprint (`attach-impl-defs` reaches `0x6b68`, the page instance 1 calls implementation defined 1 at `0x06` pages) and the instance-1 context-bank page at `0x10` pages lands exactly where a `0x10000` window ends, so the node takes the `0x20000` the family gives the same node, also the largest power of two that stops short of the GPU's region at `0x3d61000` — the first time in this file that a driver's page arithmetic rather than a sibling `_CRS` has settled a window length. `MMU1`'s eight context-bank GSIs `0x18C`–`0x193` are another family's group (`QCOM0212`/`QCOM0809`/`QCOM1409`, and a52q, miatoll, surya) while gauguin's peers lisa and a52sxq use `0x2C6`–`0x2CF`, the same kind of split the GPI DMA showed at wrapper 1, with the board's own ladder backing the eight: they lie between `MMU0`'s third run, ending at `0x179`, and its fourth, opening at `0x1B1`. **The trigger is the one place in this file where the corpus disagrees with the board**: all 1400 SMMU descriptors in the corpus are `Edge, ActiveHigh, Exclusive` and all 91 of gauguin's specifiers carry type cell 4, level, which is what the kernel programs those GIC lines with — the corpus is faithful elsewhere (its `UFS0` and `QGP0` are Level, matching the board's cells), so the disagreement is specific and is recorded rather than resolved. Three things every corpus SMMU carries are deliberately absent: the `_DEP {PEP0}` (40 of 40 nodes, and a one-entry `_DEP` is a shape no table has), `_STA` (absent from 19 of 20 `MMU0`s and 9 of 20 `MMU1`s; the ones returning `Zero` hide an SMMU rather than describe one), and `Alias (\_SB.SVMJ, _HRV)` — `SVMJ` is a single `Name (SVMJ, 0xFFFF)` under `\_SB` and this table has none. The AML is now **3,997 bytes**, `47d7dc0acda977e79b48aa9c1d4efc64020704045302a6c048f3ea26bbf1545e`, checksum `0xBC`, the delta is exactly the two nodes (2 changed header lines, 400 added, 0 removed; 24 devices to 26; 196/181 opcodes and named objects to 198/193), `SSDT`, `APIC`, `FACP`, `FACS` and `GTDT` are byte-identical a fourth time, and the claim count is now **13 of 26**. Step 4.73 then wrote the first thirteen thermal zones — **`TZ0`–`TZ7`, `TZ9`–`TZ13`, ids `QCOM0A58`/`0A59`/`0AD4` as two `_UID` instances each plus `0A91`, `0A51`, `0A4C`, `0A92`, `0ABF`, `0A4B`, `0A57`** — and closed the join 4.66 and 4.67 left open, by finding that it was never a name correspondence: the zones are joined by the **driver's id list**, and the driver is `qcpep.wd7280.inf`, which accepts `0A17`, `0A37`–`0A51`, `0A57`–`0A64`, `0A91`, `0A92`, `0ABF`, `0AC8`–`0ACB`, `0AD4` and `0AD8`–`0AE0` — the family `Xiaomi/lisa` and `Samsung/a52sxq` write and no other table does. The same-SoC table, `Samsung/a52q`, writes family `08` (`084B`, `084F`, `085C`–`085F`, `0862`, `0863`, `0865`, `0867`, `089D`, `089E`) and **no `.inf` in any of the five driver trees on this host claims one of those**, so a52q is the wrong source here for exactly the reason it was the right one in earlier steps: the id family is the driver set's, not the silicon's, which is the third time this file has learned that (4.71's GPI DMA, 4.72's SMMU). The id-per-instance pattern is the third appearance too — `0A58`, `0A59` and `0AD4` each carry `_UID Zero` and `One` in all 20 tables that have them — so a zone is named for the sensor *block* and not the sensor, and the corpus's `TZ<n>` labels (which skip 8 and 14) are kept as provenance rather than treated as identities. **A temperature is where this step deviates from its source, deliberately and once**: `_PSV` is written only where this board has a trip to cite, and the encoding both sides use is `(C + 273) * 10`, which makes the comparison possible — `0x0E60` = 95 C matches `gpu-trip0` and `npu-trip0` on `TZ6` and `TZ10`; `0x0F28` = 115 C is lisa's PMIC `_CRT` and also gauguin's `reset-mon-cfg`, so `_CRT` is written on all thirteen where lisa writes it only on the PMIC group; and the three `_UID One` zones take **`0x0EF6` = 110 C** from the board's `cpuNN-config` rather than lisa's `0x0EC4` = 105 C, because a passive trip is a thermal-design decision and gauguin's tree has no 105 C trip anywhere. The same rule drops `QCOM0ABF`'s `_PSV`, which lisa sets to `0x0EC4`: nothing here names that block, so there is no measurement to cite. `_TZD` is not written (lisa's lists `\_SB.GPU0` and `\_SB.PEP0`, neither of which this table has) and neither is `_DEP`, which is `{PEP0}` alone on all but the PMIC group and a one-entry `_DEP` is a shape no table has. **The step also caught a mistake that compiled cleanly**: the first build wrote the zones as `Device (TZ0)`, which reported `0 Errors` and the same size, opcode count and object count as the correct form — ASL's `ThermalZone` is not sugar for `Device` but AML's `ThermalZoneOp`, `0x5B 0x85` against `DeviceOp`'s `0x5B 0x82`, a different opcode of identical length, so the only instrument that showed it was the disassembler. The AML is now **5,210 bytes**, `ff492bef347825a846b03469a15fce2679ecdfe85003e1bf35f2845e79c1d639`, 237 opcodes and 326 named objects, 26 devices to 39, `SSDT`/`APIC`/`FACP`/`FACS`/`GTDT` byte-identical a fifth time, and the claim count is **23 of 25 distinct ids**. Four groups are absent and each needs a device this table has not got: the PMIC group (`0AC8`/`0AC9`/`0ACB`, the only one with a two-entry `_DEP`, a `_DSM` and a `GpioInt` `_CRS` on `\_SB.PM01` pin `0x00C0`), the ADC group (`0A5F`/`0A61`/`0A63`, `_DEP` on `ADC1`), `TZ99` (`0A5A`, whose `_TZD` names five absent containers) and the modem's nine `04C0`–`04C8` under `qcthermalmdm7280.inf`. **The space looked like the binding constraint and is not**: this step first read `FVMAIN`'s 1,160 bytes free of `0x704000` as a cap, which would have meant the PMIC group's three nodes did not fit. `[FV.FvMain]` declares `NumBlocks = 0` with `BlockSize = 0x1000`, so GenFv sizes the volume to content and rounds up to the next block — the free space is that rounding's slack, not headroom in a fixed region. A throwaway ~4 KB probe built a `0x705000` volume, one block larger with the same slack; removing it restored the same `FVMAIN.Fv` hash the payloads came from. The real cap is `FVMAIN_COMPACT`'s fixed `0x300000`, at 1,089,206 used and **2,056,522 free**, and this step measured the shrink at 0.22 (271 compact bytes per 1,216 of `FVMAIN`), which is on the order of 9 MB of headroom. The four zone groups stay withheld because each needs a device this table has not got, not for space. Step 4.74 then wrote the node `PEP0`'s own `_DEP` names — **`IPCC` at `QCOM06C2`, `_UID Zero`, `Alias (^PSUB, _SUB)`, one interrupt at GSI `0x104`, `Level, ActiveHigh`** — the first link of the chain every remaining `_DEP` in the family begins with, and the first node in this file whose source question was settled by a **provable collision** rather than by the board-wins rule. The board gives it whole (`mailbox@408000`, `"qcom,sm6350-ipcc"`/`"qcom,ipcc"`, `reg` `0x408000 + 0x1000`, `interrupts = <0 0xe4 4>` → INTID `0x104`). The corpus's 12 IPCCs use three ids, and only `QCOM06C2` (7 tables, including lisa and a52sxq) is reachable — `qcipcc7280.inf` claims it outright and claims no other IPCC id. All 12 write the triple `0x105`, `0x106`, `0x107`, which **cannot be this board's: Step 4.72 measured this board's GPU SMMU on `0x105` and `0x107`**, so copying the corpus would give two devices the same two GIC lines. `0x2EA` is in one variant and not the other, so it is a leaf. And the trigger is `Edge` in the `QCOM06C2` variant against `Level` in the `QCOM1AC2` one — **the corpus splits, so it is evidence about nothing**, and the board's type cell 4 breaks the tie; that is a different case from 4.72's, where the corpus was unanimous and the board the lone dissent. No `_DEP` is written, not because of the one-entry rule but because no corpus IPCC has one and the dependency runs the other way: `PEP0`'s `_DEP` is `Package (One) { \_SB.IPCC }`, so this node is what that resolves to, and `PEP0` now has one fewer unresolved reference. Recorded for `PEP0` itself: its `_SUB` branches on `\_SB.PSUB` against `"IDP07280"`/`"CRD07280"` with no `Else` and no trailing `Return`, and gauguin's `PSUB` is `"MTP07225"`, so on this table it has no branch to take — a defect in the method, not in the value. The AML is **5,280 bytes**, `41ed014369c3d79eef4b267646e26f1e8986ef2d5d1ec126359332b26f93f52e`, 238 opcodes, 332 named objects, 39 devices to 40, `SSDT`/`APIC`/`FACP`/`FACS`/`GTDT` byte-identical a sixth time, and the claim count is **24 of 26 distinct ids**. Two things are still measured and are not written: `FSA04480`, the Type-C analog switch (3 of 66 tables, alioth/venus/pipa, no `.inf` in the set claims it; gauguin's `fsa4480@42` is at exactly the `0x42` the corpus gives and is `disabled` in the vendor's board file — which is the vendor's switch, not the charger). And `AGR0` is `ACPI000C`, a standard Processor Aggregator Device with no vendor id, no `_CRS` and no driver, so PEP0's `NPUR` dependency is a five-line node rather than a census; the other two, `IPCC` and `ABD`, are unchanged. Step 4.70 added a third and fourth entry to the not-written list — the two live SPI engines — and they are the only pair withheld on a driver argument rather than a data one: `QCOM0A0E` is claimed by no `.inf` in any of the five driver trees on this host, where `QCOM0A0B`, `QCOM0A0C`, `QCOM0A10` and `QCOM0A16` are each claimed outright. The UCSI chain is likewise two paths and not one: the HPD path (`Q21`/`Q22` → `Notify (\_SB.UCS0, 0xA0)`) and the firmware-event path (`INTR` → `EAPQ` → `\_SB.UBTC.QUCM ()` → `Notify (\_SB.UBTC, 0x80)`), of which the first ends on a device this table has and the second on one it does not. What none of it settles is the charger *slave*: the CRD's `IC11` `Scope` addresses a part at I2C `0x76` and nothing on gauguin's `i2c@990000` is at `0x76`, so the engine is measured and the chip is not identified. No `.inf` gates the UEFI phase. `AcpiTableUpdate` is a no-op in ours alone: all 13 sibling packages implement it, 1,188–11,685 bytes, and every one patches the DSDT and reinstalls it. The two nearest SoCs, Kodiak (SM7325) and Rennell (SM7125), write 32 named fields; even the smallest sibling writes two. So the machinery exists and what is missing is the SMEM-derived values our DSDT does not declare. 2–4 are **not** volume-gated: see the space note below |
| **P4** Windows | desktop appears | not started — destroys `userdata` |
| **P5** peripherals | touch, Wi-Fi, GPU, audio | not started |

The commits so far are checkpoints inside P2, not a completed phase. Read the
phase state from this table, not from the commit titles.

### The one thing blocking progress

It is no longer a physical reset — that was the previous entry, and the reset
happened. What blocks progress now is **one line of text on the phone's screen**:
the name of the first architectural protocol DXE could not find, which
`CoreDisplayMissingArchProtocols ()` printed at `DxeMain.c:568`, two or three
lines above the assert. The phone is a distance away and holds that screen until
the next reset, so reading it costs nothing and every alternative costs a build
and a flash cycle. `docs/08` step 4.8 has what to look for and the dependency
chain that narrows the thirteen candidates.

If the screen cannot be read, the fallback is a firmware change rather than a
guess: make the missing-protocol report unconditional, or print it a second time
after the assert. That is a rebuild —
`./build_uefi.py -d gauguin -r DEBUG -c` in `work/uefi/Mu-Silicium`, then
`tools/build-p2-payloads.sh`, then `tools/flash-boot.sh` — and it should be one
cycle that answers the question whether or not anyone can read the panel.

The pieces a device session uses — the full sequence, with what each outcome
means and which payload to try next, is
[`08-device-session.md`](08-device-session.md):

1. `tools/fastboot-capture.sh` — first thing it asks is `oem fbreason`, which
   reports why ABL entered fastboot and can say `Reason:LoadImageAndAuth Fail`
   or `Reason:BootLinux Fail`. Either of those means the payload was reached.
   Then `oem uefilog` / `lkmsg` / `lpmsg`, plus `slot-unbootable` /
   `slot-retry-count`. If ABL is silent it runs `tools/unwedge-fastboot.py`,
   which classifies *which* silence it is — a reply left unread (recoverable by
   draining the endpoint), a download left waiting (recoverable in principle),
   or a fastboot thread stuck behind a still-live USB stack (not recoverable;
   power button). That was the state before the payload of step 4.8 ran; it is
   not the state now, and the difference is worth keeping straight, because it
   looked the same from the host both times.
   Note that `oem fbreason` and `oem uefilog` are commands **this phone's**
   ABL has and a Mu-Silicium-built one does not (`docs/07`), so their absence
   is not by itself a wedged fastboot.
2. `tools/pull-bootloader-log.sh` + `tools/read-logfs.py` — the device's third
   log channel, and the only one that does not depend on the payload running.
   ABL writes a log of every boot into the `logfs` partition: a FAT12 volume
   holding a ring of five 32 KiB `UEFILOG*.TXT` files, read here as a stage
   table with the last stage reached marked. Baseline, from the P0 dump: all
   five recorded boots reached `Start EBS`, i.e. handed over — so a slot that
   stops earlier is the answer, and `Apply Overlay` / `DTB offset is NULL` are
   both in ABL's own string table, meaning a refusal is written down even
   though the phone says nothing. Routes: `fastboot oem uefilog` (never yet
   returned anything here), `dd` of `/dev/block/by-name/logfs` from TWRP or
   root Android, or the P0 dump. `docs/07` has the format; `docs/08` step 4.6
   has how to read an answer.
3. `tools/restore-stock-boot.sh` — the A/B control: put the stock `boot` back
   and see whether Android returns.
4. `tools/flash-boot.sh` — the one command that writes a payload to `boot`, over
   whichever route answers. It exists because step 1b can make the fastboot route
   unreachable (the image in `boot` wedging ABL, so a reset reproduces the wedge)
   and TWRP is then not a fallback but the only way in; it also reads the
   partition back and compares the hash, so "the write landed" is established
   rather than assumed.
5. `work/out/boot-pstore.img` and its five siblings `-raw`, `-raw-txt`,
   `-raw-noefi`, `-gz-noefi`, `-gz-fixedsz` — P1's mainline kernel in the six
   shapes that differ on the properties separating our images from the one the
   phone boots (raw vs compressed, EFI-stub form of the arm64
   header, `text_offset`), each with `CONFIG_PSTORE_CONSOLE`/`PSTORE_RAM` and a
   cmdline that puts the kernel log in a pstore region **we choose**
   (`0xd0000000`, `ramoops@d0000000`, `no-map`). The phone declares no ramoops
   region of its own — its panic log goes through `mtdoops` to a raw partition —
   so there is nothing to match and the address is checked against the phone's
   DRAM partitions *and* its `no-map` carveouts (`tools/abl-boot-check.py`); the
   two earlier addresses were wrong in exactly those two ways, one outside every
   RAM partition and one inside the bootloader's `removed-dma-pool` for the modem
   and DSPs. The cmdline carries `reboot=panic_warm`, so a payload with no UART
   and no screen driver can still be read back at
   `/sys/fs/pstore/console-ramoops-0` after the phone reboots itself (step 4.5 in
   the runbook; the reasoning is in `docs/07`). `tools/build-p1-payloads.sh`
   builds the DTB and all of the images in one pass and refuses to ship a tree
   that is missing either log channel — one `ramoops` node at the address above,
   **or a `/chosen` with no `simple-framebuffer`** — because both are in that one
   file and a payload missing either is indistinguishable from one that works;
   `tools/check-payload.py` refuses to let a structurally wrong one reach the
   device. The screen half is the one that
   needs no round trip: the logo being replaced by the kernel log, and then by
   init's `alive: N s uptime` heartbeat, is P1's gate observed directly. The log
   half needs the phone to restart itself, so the kernel is also built to panic on
   the two failures this bring-up is most likely to hit (an oops in a probe, and a
   spin waiting on a clock or regulator that never comes ready) — otherwise both
   end in a kernel that neither prints nor reboots, and the ring is never read.
6. `work/out/p2-variants/Mu-gauguin-stock-{none,gzip}.img` — two stock-shaped
   builds, one per surviving candidate (uncompressed vs gzip kernel). Each pairs
   with a P1 variant on the compression property, which is what makes the pair —
   and not the individual attempt — the thing to read: see step 4b/4c in the
   runbook. A third image sits beside them, `Mu-gauguin-silicon-gzip.img`,
   deliberately not part of the pair: it varies the header version, the page size
   and where the tree lives all at once, so it cannot be read as a one-variable
   experiment, and it is kept as the fallback for the case where both stock
   variants are refused for a reason that turns out to be the stock shape itself.
   All three are built by `tools/build-p2-payloads.sh`, and the earlier build
   of the pair — made by hand, never flashed — could not have run at all: it
   carried a stale device tree with no `/__symbols__`, so ABL would have refused
   the vendor overlay (`docs/07`). What was in `boot` when the first attempt was
   made was the same shape as the third image — v1, page 2048, tree after the
   gzip stream — and carried that same stale tree, 71,737 bytes of it against
   the current build's 87,594; both images hold a *byte-identical* firmware
   (`SILICIUM_UEFI.fd` `md5 9c104725…`), so the tree is the only variable
   between them and step 4.8 is a clean one-variable experiment. `boot` now
   holds `Mu-gauguin-silicon-gzip.img`, `sha256 816b1d41…`.

Those pieces are backed by the offline tools below, which exist because a device
cycle is expensive and a bad image costs a physical reset. Every defect this
project has found in a payload was found by one of them rather than by the phone:

- `tools/abl-boot-check.py` — replays ABL's decision path over a built image and
  says whether *this phone* would take it: the arm64 header check, the
  `msm-id`/`board-id` selection, the overlay's fixups against our `/__symbols__`
  (replayed in Python *and* merged for real by `fdtoverlay`, since libfdt and
  libufdt disagree on a tree whose symbols point at phandle-less nodes — one
  refuses, the other boots with the fragments silently dropped), and the two
  placement questions (inside a DRAM partition, outside every `no-map` carveout)
  for the ramoops region and the framebuffer. Run by
  `tools/build-p1-payloads.sh` at the end, so a payload that fails it never
  reaches anyone.
- `tools/gauguin.py` — this phone's memory model in one place: the DRAM
  partitions and the `no-map` carveouts, both measured from the running phone's
  `/proc/device-tree` rather than read out of a tree's source.
- `tools/fdt.py` — just enough flattened-device-tree parsing to ask questions
  about a blob: the header, the node walk, `/__symbols__`, the overlay's
  `__fixups__`/`__overlay__` fragments, and the `qcom,msm-id`/`qcom,board-id`
  cells the selection turns on.
- `tools/make_dtbo_sinks.py` — generates the `/__symbols__` and the empty sink
  nodes the vendor overlay's 158 fixups resolve against. An input to the build
  rather than a check: without it ABL refuses every payload with
  `ApplyOverlay: ufdt apply overlay failed`, and with it the overlay lands in a
  subtree nothing binds to.
- `tools/build-device-tree.sh` — builds the one device tree every payload
  carries, and checks the built blob rather than the source it came from: the
  `/__symbols__` count, exactly one `ramoops` node at `0xd0000000`, and the four
  `/chosen/framebuffer` properties. It is a script of its own because the tree
  has two consumers — the P1 payloads and the UEFI ones — and holding it in one
  builder is what stops the second consumer building against a stale copy, which
  is what happened (`docs/07`).
- `tools/build-p1-payloads.sh` / `tools/build-p2-payloads.sh` — the two payload
  sets, each ending in both checkers so a payload that fails one never reaches
  anyone.
- `tools/read-logfs.py` — reads the bootloader's own per-boot log out of the
  `logfs` partition, from a raw image, an extracted slot, or a `oem uefilog`
  dump, and prints it as a stage table with the last stage reached marked. It is
  the one reader here that is aimed at the device rather than at a file we built,
  and it is offline in the sense that matters for the current blocker: the P0
  dump answers it with no phone at all.

The reference for all of these is Qualcomm's own `QcomModulePkg` (the ABL
source), vendored at `work/ref/mu_qcommodulepkg` from
`Daniel224455/mu_qcommodulepkg` and validated byte-for-byte against the PE
extracted from this phone. It is gitignored — a large copy of someone else's
tree — and used as the authority for what ABL does, which is why `docs/07`'s
claims about `CheckAllBitsSet`, `GZipPkgCheck` and the ramoops address can be
read against `file:line` instead of inferred from behaviour.

### Standing decisions, with one amendment

The original rule was "**Never write to the device's storage until P4**". That was
relaxed once, with the user's explicit authorization, to write the `boot`
partition for the P2 test — and `boot` was backed up and verified beforehand
precisely so that relaxation would be safe. The rule stands for everything else:
`userdata`, the partition table, and the firmware LUNs are still off limits
until P4.

---

## P0 — Survey, backup, firmware inventory

**Why first:** the installed ROM is a `user/dev-keys` Smartisan port with no public image.
Anything that re-partitions storage before a backup exists risks an unrecoverable device.
And the port itself cannot be scoped until we know which of Qualcomm's signed drivers this
phone's firmware actually contains.

Work:

1. Identify the hardware correctly (the device lies about its model — see `01-hardware.md`)
2. Dump every partition to `~/backup/gauguin/images/`
3. Recover the DXE driver set and platform config from the phone's own XBL
4. Establish that no existing UEFI port covers this SoC

**Gate:** every partition except `userdata` dumped and verified; DXE inventory extracted;
confirm by search that no `gauguin`/`SM7225`/`Bitra` UEFI port exists anywhere public.

**Result:** satisfied. 86 signed AArch64 DXE drivers recovered, `uefiplat.cfg` recovered,
no existing port found.

---

## P1 — Mainline Linux bring-up

**Why this and not UEFI directly:** a UEFI platform package is mostly a hardware
description — clock trees, regulator relationships, GPIO pins, panel timings, MMIO bases.
Writing that description blind, against a device that only has a 4.19 vendor kernel and no
schematics, is guesswork. Mainline Linux already has all of it **for the same silicon**:
`arch/arm64/boot/dts/qcom/sm7225.dtsi` + `sm6350.dtsi` describe SM7225 exactly, and
`sm7225-fairphone-fp4.dts` is a worked example for the same `msm-id 459`. Booting mainline
converts guesswork into measurement, and it is a small amount of work because the
bootloader is already unlocked.

Work:

1. Fetch a mainline kernel and `sm6350`/`sm7225` DTS support
2. Write `arch/arm64/boot/dts/qcom/sm7225-xiaomi-gauguin.dts` — clone the Fairphone 4
   board file, change panel, touch controller, regulators, and the `qcom,board-id`
3. Build `Image` + `dtb`, wrap into an Android boot image — with our tree in the
   boot image's DTB slot. That does **not** make ABL use it as-is: matching
   `msm-id`/`board-id` gets two of the six bits `CheckAllBitsSet` needs, and the
   tree declares no `pmic-id`, `softsku-id`, `platform-subtype` or `foundry-id`,
   so the vendor overlay is applied to our tree on every boot. The tree therefore
   carries the `/__symbols__` the overlay's fixups resolve against
   (`tools/make_dtbo_sinks.py`), and a payload built without it is refused before
   the kernel runs (`docs/07`). Add the pstore cmdline so the boot leaves a
   readable log at an address we choose, checked against the phone's own DRAM map
   and carveouts.
4. `fastboot boot boot.img` — nothing written to the device
5. Use `extract_dtb` / `/proc/device-tree` output as the hardware reference

**Gate:** the device boots mainline, prints to a serial console or on-screen framebuffer,
and enumerates UFS. That output is the input to P2.

**Risk:** moderate. Panel and touch are the fiddly parts; both have mainline drivers for
this SoC (`mdss`/`dsi` and `novatek-nvt-ts` respectively).

---

## P2 — UEFI skeleton

**Why:** this is the phase that decides whether the project is viable. Before writing any
Windows-specific code, UEFI has to run at all on this board.

Approach, mirroring what Mu-Silicium does for other SoCs:

1. Clone `Project-Silicium/Mu-Silicium` (Project Mu — BSD licensed, the community standard)
2. Create `Silicon/Qualcomm/BitraPkg` by adapting `RennellPkg` (SM7125) and `MooreaPkg`
   (SM7150) — the closest-spec existing packages
3. Create `Platforms/Xiaomi/gauguinPkg` from the same references, parameterised with the
   values in `04-uefi-platform-config.md`
4. Feed in the drivers extracted in P0 where the open-source equivalents are not needed
5. Compile the missing open-source pieces from `edk2-porting/edk2-msm`'s `QcomPkg`
   (`UsbBusDxe`, `UsbKbDxe`, `UsbMassStorageDxe`, `AdrenoDxe`)
6. Emit an Android boot image, `fastboot boot` it

**Gate:** the boot manager draws on the phone's screen, and the storage it lists includes
UFS as a block device. If this fails, stop and reconsider — everything downstream depends
on it.

**Status:** the first half of the gate is met and the second is not. Our firmware runs
and draws its own DEBUG stream on the panel — the whole of `docs/08` step 4.8 — and
then halts in `DxeMain` because an architectural protocol is missing. The boot manager
is not reached, so nothing is listed yet. Read the gate as "runs" (answered yes) and
"hands off to BDS" (open).

The gate originally said "`fastboot boot` shows the UEFI Shell". That was wrong twice
over, and `docs/07` has the evidence: this platform boots via `fastboot flash boot` and
not `fastboot boot` (different ABL code paths), and **no Mu-Silicium phone platform ships
the shell at all** — `ShellPkg/Application/Shell/Shell.inf` is referenced by no platform
under `Platforms/`. A gate the reference for this same `msm-id` cannot meet is not a gate.
The shell is a decision to revisit after the first execution; what the volume does
provide, and what the gate now asks for, is `BootManagerMenuApp` drawing on the panel.

**Risk:** **high, and this is the real wall.** No Bitra-family device has ever had a UEFI
port. The signed blobs are unlikely to load cleanly into a different DXE core on the first
attempt; expect a long debugging loop, and serial output is essential (the device has no
exposed UART — plan on the UEFI `ULogDxe` log buffer or on-screen debug). That is how it
went, and the on-screen half is the half that worked: the DEBUG build's console *is* the
framebuffer, so the firmware's own `DEBUG ()` strings are readable off the panel with no
UART and no shell. What it does not give is scrollback — the console clears itself when
it runs off the bottom — and it is write-only, so nothing can be read back after the
fact.

---

## P3 — Full UEFI with ACPI

**Why:** Windows does not read device trees. It needs ACPI tables describing the board, and
it needs the display, storage, USB host and input all working *inside UEFI* to install.

Work:

1. Add `Silicium-ACPI`'s Qualcomm Moorea/Rennell tables as a starting point, write the
   gauguin tables (DSDT/SSDT: UFS, XHCI, I2C, GPIO, buttons, thermal zones)
2. Bring up `DisplayDxe` + framebuffer
3. Bring up USB host (`UsbBusDxe`) — needed to install from a USB stick
4. Bring up `ButtonsDxe`, and a way to choose boot entries

**Gate:** a Windows 11 ARM64 installer boots off a USB stick and sees the internal UFS.

**Status (2026-09-25): item 1 is done for UFS, USB, the PMIC family, the GPIO controller
and the Type-C controller, and every one of those nodes answers a shipped driver — it is
still open for I2C, buttons and thermal zones. 2–4 are not started, and none of them can
be assessed until P2 hands off to BDS.**

The tables exist, are wired into `gauguin.fdf`
and `gauguin.dsc`, and are in the firmware volume of the build behind the staged P2
payload — `DSDT` (**2,369 bytes**, gauguin's own, `SM7225`, eight `ACPI0007` CPU devices,
the UFS and USB nodes, `SPMI`/`PMIC`/`PM01`, `GIO0` with its `OFNI` gpio count of 156,
and `UCS0`), `APIC`, `FACP`, `FACS`, `GTDT`, plus the shared `SSDT`. The tables were read
back out of the built artifact, the `APIC`
parsed subtable by subtable — its two INTIDs and its redistributor base match this
board's device tree — and the `DSDT` decompiled back out of the volume so that each node
could be read as the firmware will see it rather than as it was written.
`docs/07`'s P3 groundwork section carries the detail.

What admits a node here is no longer a reference table's resemblance but a shipped driver
naming its id, and four nodes now pass that test: `qcgpio7280.inf` binds
`ACPI\QCOM0A0C` for `GIO0`, `qcusbcucsi7280.inf` binds `ACPI\QCOM0AA4` for `UCS0`
(Step 4.66), `qcpmicgpio7280.inf` binds `ACPI\QCOM0A2D` for `PM01`, and the two USB
filters bind `URS\QCOM0A8B` for the `URS0` that already exists — `QcXhciFilter7280.inf`
as `URS\QCOM0A8B&HOST` and `QcUsbFnSsFilter7280.inf` as `URS\QCOM0A8B&FUNCTION`. That
last one is the exception in the group and worth stating plainly: the census tool binds
`ACPI\<id>`, so `--bind QCOM0A8B` reports it **NOT CLAIMED**, and correctly — the URS
controller is bound by the USB *device-interface* filters under their own `URS\` prefix.
Three of the four are `ACPI\` bindings the tool confirms by name and the fourth is a
binding the tool cannot see.
`UCS0` is the shortest of them: one `GpioIo` on `GIO0` pin 35, and five one-line
accessors that return the `\_SB`-scope state names `MUXC`, `CCST`, `DPPN`, `HPDS` and
`HIRQ`, all of which were already in the table. Note that the DSDT is still smaller in
scope than the list above — I2C, buttons and thermal zones are not in it, and the `GIO0`
controller is declared without the corpus's per-pin interrupt catalogue, because that
catalogue is board data and this board's device tree does not carry it.

**One sentence from the previous version of this paragraph was wrong, and Step 4.66
corrects it.** It said `UCS0` "cannot be written before `PEP0`". What cannot be written
before `PEP0` is the `_DEP` — lisa's `UCS0` carries
`Name (_DEP, Package (One) { \_SB.PEP0 })` — and a `_DEP` naming a namespace node that
does not exist resolves to nothing, so it buys nothing while costing a later reader a
dangling name to chase; `_DEP` is advisory start ordering and there is nothing here to
order against. `UCS0` itself is one id, one resource and five accessors, and it is in the
table now. `PEP0` (`QCOM0A17`, which `qcpep.wd7280.inf` binds) is still absent, and Step
4.67 measured what "large" means there: 2,501 lines and 96,109 bytes in lisa, and the same
size again in a52sxq's with exactly two lines differing — both in `_SUB`, which returns
`"CRD07280"` on lisa and `"QRD07280"` on a52sxq from a branch keyed on `\_SB.PSUB`, so it
is generator output carrying a reference-platform string and not board data, and it is the
first thing a port has to change, since this table's `PSUB` is `"MTP07225"` and lisa's
`_SUB` would fall off the end of both branches and answer zero. But 1,826 of those 2,501
lines are one method — `THTZ`, a dispatch on (zone, trip point) that is called by nothing
in any of the 20 tables that declares it — and the node's size is that dispatch's case
count on top of a skeleton of a few hundred lines, because the same node in a platform
with no zones wired to PEP is shipped as 629 lines whose whole `THTZ` is
`Return (0xFFFF)`. So the work is the skeleton plus one case per zone, and the reason it
is still absent is the other thing this step found: `PEP0` reaches `\_SB.IPCC`,
`\_SB.ABD.ROP1`, `\_SB.AGR0`, the `_STA` of six subsystems (`ADSP`, `AMSS`, `SCSS`,
`SPSS`, `WPSS`, `NSP0`) and the zones themselves, of which this table has `PSUB` and
nothing else. It is the opposite kind of node from `UCS0`: `UCS0` could be written before
its dependency, `PEP0` cannot be written before any of its six.

**And the rest is blocked on an input, not on effort.** Every one of those nodes
needs an ACPI `_HID`, and a device tree does not carry ACPI names: it has registers
and pins, which is the half that is knowable. `tools/acpi-hid-census.py` measures what
the reference DSDTs supply, and across all 66 of them the same block at the same
address carries a different `_HID` on every SoC — the TLMM window `0xF100000` is
`QCOM1A0C` on vili, lemonade and venus, `QCOM0A0C` on lisa and a52sxq, `QCOM250C` on
alioth, `QCOM090C` on renoir, `QCOM0C0C` on Kailua. No
Bitra-family reference exists either: `Platforms/Realme/bitra/DSDT.aml`, the source
of this file's form, has the same five devices gauguin has and nothing more.

**The thermal zone is the one item where that sentence has since been measured wrong,
and it is worth the correction because it changes what the item is.** It said "both TSENS
blocks are described by none of the 66: the corpus has no thermal-sensor device of any
kind". The corpus has no TSENS *controller*, which is true — but lisa and a52sxq carry 29
`ThermalZone` devices, every one of them with an `_HID`, and a census of
`qcpep.wd7280.inf`'s own device list gives each id a meaning: `QCOM0A37`–`QCOM0A51` and
`0A58`/`0A59`/`0AD4`/`0A91`/`0ABF`/`0A92`/`0A5A` are its `TSENS` entries,
`QCOM0A5D`–`QCOM0A64` its `ADC`, `QCOM0A57` its `BCL`, `QCOM0AC8`–`QCOM0ACB` its `PMIC`,
`QCOM0AD8`–`QCOM0AE0` its `SRM` — and the nine zones on lisa that are *not* in the 0A
family at all, `QCOM04C0`–`QCOM04C8`, are the ids `qcthermalmdm7280.inf` binds. So every
id is determined and claimed; what is missing is the work and the join — Step 4.67 found
the board data is not missing after all: gauguin's own device tree carries 40 zones with a
sensor index and a trip list each, 27 of them the SoC zones `sm6350.dtsi` declares, so the
trip points (`_PSV`, `_CRT`) are this board's own numbers and readable. The zones are 16 to
134 lines each and name cooling devices in `_TZD` (`\SB.MPA`, `\SB.SYSM.CLUS.CPU0-7`,
`\SB.WLTM`, `\SB.CSW0`, `\SB.GPU0`, `\SB.MJCT`) that this table does not have, and those
remain the gap. What is also still open is the *mapping*: lisa's 29 devices are 26 ids
because three ids carry a pair of zones distinguished by `_UID` `Zero` and `One`, and
nothing in this tree yet says which of the board's 40 zones is which id. None of the 29
carries a `_TMP`, so the ACPI side of a zone is its trip points and its sampling period,
not its readings.

The count is not the point; the decomposition is. A Qualcomm scoped `_HID` is
`QCOM<family byte><block index>`, and the *index* is fixed per generation of the
table generator — measured across 66 tables, the GPIO controller is `..0C` under
every modern family and `..0D` under the three older ones, the arbiter is `..0B`
and `..0C` respectively. Ten of gauguin's twelve blocks therefore have a known
index in each measured generation, and the one input left was which family byte
SM7225 carries. That byte does not follow the marketing name — pipa and
alioth both declare `SDM8250` and carry 05 and 25 — so it has to be read off a
driver set, and `--drivers DIR` tries all 256 bytes against each measured index
table and reports the one that names all ten blocks.

**That byte has now been measured, and it is `0A`.** The set is the SC7280 /
Kodiak one, `WOA-Project/Qualcomm-Reference-Drivers` → `7280_CLS/200.0.4.0`,
112 `.cab` files fetched from Windows Update by the reference-laptop OEM and
extracted to 112 `.inf`. Under the modern index table it names **8 of gauguin's
10 blocks**, and 0 of 10 under each of the other 255 bytes:

| block | id | block | id |
|---|---|---|---|
| SE0u | `QCOM0A16` | SE5 | `QCOM0A10` |
| SE1 | `QCOM0A10` | SE7 | `QCOM0A10` |
| SE2 | `QCOM0A10` | SPMI | `QCOM0A0B` |
| SE3 | `QCOM0A10` | TLMM | `QCOM0A0C` |

The two misses are `SE0` and `SE6`, which the set does not name at all; it would
take `QCOM0A0E`, the correct index for a UART in that table. So the misses are
absences in the *set*, not contradictions of the byte — a Kodiak board simply
does not publish those two as ACPI devices. Two further checks agree: the only
two tables in the whole 66-table corpus that carry these ids are
`Platforms/Xiaomi/lisa` and `Platforms/Samsung/a52sxq`, both declaring `SDM7280`,
both with `Device (GIO0) { Name (_HID, "QCOM0A0C") }`; and all eight ids land on
the expected *kind* of block in the right bit positions — `qcpmicgpio7280.inf`
claims `QCOM0A2D` and `qcpmic7280.inf` claims `QCOM0A2B` plus `QCOM0AD3`, where
`0x2D & 0x7F = 0x53` is a PMIC sub-function. A collision would not organise itself
that way across eight independent ids.

Stated honestly: `0A` is measured on Kodiak (SM7325), which is SM7225's sibling in
the same table generation, and no SM7225 Windows driver set and no independent
SM7225 reference DSDT exists to confirm it on the die itself. It is a measurement
with two independent oracles, not a proof. It also does **not** name the TSENS
sensor block: the set has no thermal-*sensor* device id at all, and neither it nor
any of the 66 tables describes one. What the set does name is the thermal **zones**
— `qcthermalmdm7280.inf` claims the consecutive range `QCOM04B4`–`QCOM04CE`,
which contains `QCOM04C0`–`QCOM04C8`, the nine that Step 4.58 found fixed across
six tables in four families with no explanation. So the zones live in a *fixed*
`QCOM04xx` space rather than the family-byte space, and that observation now has a
driver behind it instead of only the corpus. It still does not say which of
gauguin's 40 device-tree zones takes which id.

Read that as the name being **a declaration rather than a hardware fact**: a device
binds to the `_HID` its `.inf` lists and to nothing else, so the tables are written
*to a driver*, not *to the SoC*, and the corpus disagreeing on every SoC is evidence
the choice is free rather than evidence that a correct name is missing. The driver
set was the missing input; it is now in hand and read, and it is what turns
"a plausible `QCOM`-prefixed name" into "the name a real driver will claim".
Copying a name from another SoC *without* a set does not fail loudly — a node whose
`_HID` no driver claims is absent from Device Manager, and the hardware behind it is
silently not there, which is worse than a node that is visibly missing. Note what
that does and does not gate, though: **there is no `.inf` gate on the UEFI phase.**
`_HID` is firmware-supplied, TianoCore's `AcpiTableDxe` auto-computes
`_CID = PNP0C02` for any `QCOM`-prefixed `_HID` in the reserved range, and the
build already ships `QCOM0497`/`QCOM0498`/`QCOM24A5` with no driver set at all — so
none of this can break P2. What it prevents is authoring names that are *silently
wrong for specific UMDF clients later*. This is the same mechanism P5 names as
"re-bind the WoA driver INF"; P3's tables are its first instance. `BTNS` looks like
an exception —
its `_HID` is a standard `ACPI0011` Generic Buttons Device, so no vendor INF is
needed for it — but its `_CRS` names `\_SB.PM01` as the controller its `GpioInt`
resources belong to, and a PMIC node carries the same family byte (`PM01` is
`QCOM<family>2D` in the modern tables and `QCOM<family>30` under 05 08 14). So
the buttons wait for the same byte the rest of the blocks do.

Items 2–4 were said to be gated on volume space: "**the payload has 760 bytes of free
volume**", so DisplayDxe, UsbBusDxe and ButtonsDxe "will not fit" beside the P2
instrumentation. **That is a misreading of the figure, and the gate is not there.**

`FVMAIN` is declared `BlockSize = 0x1000, NumBlocks = 0` in `gauguin.fdf:19-22`, so
GenFv sizes it to the next 4 KiB boundary above its content and the reported "free"
is the slack left in that last block. Measured, it is exactly that and nothing else:
the 2026-09-24 23:30 build reports `0x702d08` taken → `0x703000` → 760 free, today's
01:20 build `0x72ced0` → `0x72d000` → 304 free, and upstream `suryaPkg` `0x676d30` →
`0x677000` → 720 free. All three are `(-taken) % 4096`; the number is always in
[0, 4095] and carries no capacity information at all. `FVMAIN` in fact **grew by
172,032 bytes between those two gauguin builds** and would have grown further.

What the payload is actually bounded by is the enclosing volume: `SILICIUM_UEFI.fd`
is `FVMAIN_COMPACT`, a fixed 3 MiB region, and today it is **35.6% used with
2,025,744 bytes free**. That 3 MiB is not a Mu-Silicium convention either — it is
the device's own memory map, from the `uefiplat.cfg` recovered out of XBL at P0:
`0x9FC00000, 0x00300000, "UEFI FD"`, walled below by `ABOOT FV` (`0x9FA00000`,
`0x00200000`, ending exactly at `0x9FC00000`) and above by `SEC Heap`
(`0x9FF00000`), with BootShim's `_StackSize` and the FV header's `FvLength` both
equal to `0x300000`.

The refutation needs no arithmetic, though: the `USE_CUSTOM_DISPLAY_DRIVER=1` build —
the one that contains `DisplayDxe` — **has already been built and validated** in this
volume (`Mu-gauguin.img` 1,210,368 bytes, `FVMAIN` `0x753000`, 47 images). So P3 items
2–4 are gated on P2 reaching BDS unconditionally, which is a statement about when the
debug instrumentation can be deleted, not about whether their drivers fit.

Step 4.63 then measured the other half of this — where the failure *does* come from.
A valid 11,536,368-byte `DSDT` (generated by `tools/acpi-pad.py`) builds with `FVMAIN
[99%Full] 18886656 (0x1203000) total, 18886408 used, 248 free` and `PROGRESS - Success`
— 2.57× the volume above, still "99% full" — while 2 MiB of incompressible noise in the
same slot fails as `the required fv image size 0x311bf0 exceeds the set fv image size
0x300000`, against `FVMAIN_COMPACT`. So the budget for a new ACPI node is that outer
volume's free space *after compression* (**2,056,522 bytes** as of Step 4.73, measured to shrink at 0.22 of new `FVMAIN` bytes — the inner `FVMAIN`'s own "1,160 free" is block rounding and not a cap), and
the `[99%Full]` percentage is a rounding artefact in both directions.

**One consequence for the record-keeping this plan asks for:** a payload hash is a hash
of a build, not of the source. `Silicon/Silicium/SiliciumPkg/Sec/Sec.c:48` compiles
`__TIME__`/`__DATE__` into `Sec.efi`, which lives in the outer volume, so two clean
builds of one tree give two `Mu-gauguin-silicon-gzip.img` hashes and **one**
`FVMAIN.Fv` hash (`85f6f9fc…542b30`). Record the `FVMAIN.Fv` hash — `tools/fv-inventory.py
<img> --dump-fvmain PATH` — when the point is *what the build contains*, and the payload
hash only when the point is *which build*, as `probe-fingerprint.py` and the `boot`
control do.

---

## P4 — Windows deployment

1. Repartition: shrink `userdata`, create an ESP, and add the Windows partition layout
2. Fetch a Windows 11 ARM64 build (UUP dump) and deploy it to the device
3. First boot

**Gate:** Windows desktop appears.

**Point of no return:** this phase destroys `userdata`. Everything the user wants to keep
must be off the device before it starts.

---

## P5 — Hardware enablement, one device at a time

In rough order of value-versus-difficulty:

| Order | Subsystem | Work |
|---|---|---|
| 1 | Storage, USB, power, buttons | should already work from P3 |
| 2 | Touchscreen | Novatek over SPI — a Windows HID miniport; several exist upstream |
| 3 | GPU acceleration | Adreno 619 `a6xx`; re-bind the WoA driver INF. CPU rendering until then |
| 4 | Wi-Fi (`wcn3990`) | hardest of the "should work" items |
| 5 | Bluetooth | follows Wi-Fi |
| 6 | Audio | needs a Windows equivalent of the ALSA UCM config |
| 7 | Sensors, vibrator | piecemeal |
| — | **Modem, cameras** | **not attempted — no driver exists** |

---

## Standing decisions

- **Never write to the device's storage until P4.** Everything through P3 runs via
  `fastboot boot`, which is non-destructive and instantly reversible.
- **Keep the backup current.** Any change to the partition layout invalidates parts of it.
- **Serial/log output before anything else.** A bring-up with no way to see what failed is
  not debuggable.
