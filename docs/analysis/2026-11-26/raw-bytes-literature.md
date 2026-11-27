# Unknown Honda-protocol bytes on the P2P: what the published sources say

Date: 2026-11-26. Method: web and literature research only (no car, no database).
Scope: the bytes of the `20 05 addr len` table that are still unknown on the 2000 Civic EX P2P:
0x03–0x07, 0x0D, 0x0E, 0x16, 0x1A–0x1F, 0x29–0x3B.

## Summary

- **No public source names 0x03–0x07, 0x16, 0x1A–0x1F or 0x2A–0x3B.** All the open Honda K-line projects copy one small table (RPM, VSS, flags, ECT…IAC, knock). That table goes back to "dip" (Didier Pelegri, 2004, OBD2 Integra Type R) and to the hondamotor.ru thread (2009). Every hypothesis below for those bytes is my own inference and is marked as one.
- **The address is a slot in a table, not a RAM address.** On the stock ROM the third byte of `20 05 addr len` indexes a lookup table in ROM that points at the real RAM (pgmfi 2005). So Crome, Hondata or eCtune RAM maps (real 66207 RAM addresses) cannot be carried over byte for byte. The slot layout is the same on OBD1 (P13, P28, P30) and OBD2 (P2T, P73, S2000 AP1) ECUs for the bytes we have checked. That is why kerpz's map worked here.
- **The most trustworthy sources:** kerpz (P2T), mr-sneezy (S2000 AP1, HOPE-derived) and the hondamotor.ru enum. All three get 0x00/02/10/11/12/14/20/22 right. kerpz's newer ArduinoHondaUNI adds bit names for 0x0C/0x0D and alternative formulas.
- **Useful points of agreement:**
  - 0x0D and 0x0E are **flag bytes** ("Flag6", "Flag7"). UNI names 0x0D bit 3 "Mount".
  - **0x29 is called "IAC"** in the hondamotor.ru enum. That enum has no 0x28.
  - On kerpz's P2T, **0x35–0x3A are "unknown" but present** (`dlcdata[7]..[12]` of row 0x30), and 0x3C is knock. That is exactly the run of non-zero bytes the P2P shows at idle.

## Source quality check against the P2P confirmed addresses

| Source (ECU) | 0x00 rpm | 0x02 vss | 0x10 ect | 0x11 iat | 0x12 map | 0x14 tps | 0x20 stft | 0x22 ltft | 0x28 IAC | 0x08/0x0B bits | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| kerpz hobd_uni/uni2/elm (P2T OBD2 stock, P30 OBD1 chipped) | yes (x/4) | yes | yes | yes | yes | yes | yes | yes | yes ("iacv") | A/C on 0x08 b2 (wrong here, PSP), VTEC 0x0A b3 | good for analog bytes, poor for bits |
| kerpz ArduinoHondaUNI (successor, same ECUs) | yes | yes | yes | yes | yes | yes | yes | yes | yes | full bit list (0x08 b0/1/3/4/7, 0x09 b3, 0x0A b2, 0x0B b0/1/2/5/7, 0x0C b0/1/2/7, 0x0D b3, 0x0F b0) matches the P2P on 0x08 b0/b1 and 0x0B b0/b1/b5 | best overall |
| mr-sneezy (1999 S2000 AP1, OzDM; addresses "from HOPE") | yes | yes | yes | yes | yes | yes | yes | yes | yes | 0x08 b0/b1/b3, 0x0B b0/b1/b2/b5 agree with the P2P | good |
| hondamotor.ru enum (ECU not stated; OBD1/OBD2A cars of the posters) | yes | yes | yes | yes | yes | yes | yes | yes | **no 0x28; "IAC" = 0x29** | 0x08 b0/b1/b3 and 0x0B b0/b5 agree | good, but the IAC slot conflicts |
| pgmfi "Stock ROM Datalogging" (P13 OBD1 stock, CN2) | yes (period) | yes | — (only "0x10 a temp?") | — | "maybe 0x12" | yes (0x14) | — | — | — | 0x0B +2 with A/C (= 0x0B b1 A/C clutch) | consistent, thin |
| andgarriv Honda-ECU-Data ('98 Civic D14A3, OBD2A) | yes | yes | yes | yes | yes | yes | yes | yes | — | — | consistent, nothing new |
| dip K-Line OBDII v1.00 (Integra Type R OBD2; channel names only) | named | named | named | named | named | named | named | named | **two IAC channels: "Drive IAC (mA)" and "Control IAC (%)"** | flags include EVAP purge, O2 heater, Immo, SCS | names only, no addresses visible |

## Per-byte table

Idle snapshot (warm, ~800 rpm): 0x1C=168, 0x29=85 (40 a few minutes earlier), 0x2A=5, 0x32=45, 0x33=55, 0x35=45, 0x36=7, 0x37=45, 0x38=25, 0x39=25, 0x3A=25. All other target bytes were 0.

"Inference" means no source says it: it is my reading of the snapshot plus Honda system knowledge.

| Byte | Candidate meanings by source (formula, ECU) | Agreement | How the idle value fits | Confidence |
|---|---|---|---|---|
| 0x03 | none. kerpz decodes only 0x00–0x02 and bits of 0x08/0x0A from row 0x00. The hondamotor enum skips 0x03–0x07. | — | 0 at idle. Inference: a reserved slot, or the second VSS byte / a pulse count on other ECUs. | none |
| 0x04 | none | — | 0 | none |
| 0x05 | none | — | 0 | none |
| 0x06 | none | — | 0 | none |
| 0x07 | none | — | 0 | none |
| 0x0D | Flag byte "Flag6" (hondamotor, no bits given). Bit 3 = "Mount", the engine-mount control solenoid (kerpz ArduinoHondaUNI HOBD.csv, P2T). | 2 sources agree it holds flags. Only 1 names a bit. | 0 at idle fits a flag byte with nothing active. The P2P has no active mount, so b3 should stay 0. Inference: dip's flag list (Immo, EVAP purge, Drive HO2S) suggests the immobilizer or EVAP bits may live here or in 0x0E on a 2000 EX. | medium that it is flags; low on any bit |
| 0x0E | Flag byte "Flag7" (hondamotor, no bits) | 1 source | 0 at idle. Same note on the Immo and EVAP bits. | low-medium (flags) |
| 0x16 | none. It sits between O2 (0x15) and battery (0x17). The secondary O2 is **not** here on the S2000: mr-sneezy reads it at **0xA0** (x/51.3 V). | — | 0 at idle. Inference: an unused analog slot (EGR lift or a second O2 on other models). The P2P supports OBD2 PID 15 (O2 S2), so 0x16 and 0xA0 can be checked against it. | none |
| 0x19 (known, for context) | ELD `77.06 − x/2.5371` A (kerpz). ArduinoHondaUNI also has a commented-out "EGR V = x/51.3" on 0x19. | — | — | — |
| 0x1A | none | — | 0 | none |
| 0x1B | none | — | 0 | none |
| 0x1C | none in any source | — | 168 at warm idle. Inferences, best first: **(a) target or desired idle in rpm/4: 168×4 = 672 rpm**, which is exactly the P2P's M/T base-idle spec (670 ± 50) and the measured low idle level (665–680). This uses the same ×4 scaling as the RPM word at 0x00. (b) Target idle in rpm/5 = 840 (no Honda precedent for /5). (c) A raw 8-bit ADC reading of about 3.3 V (x/51 V) from a sensor not otherwise mapped, such as the fuel tank pressure sensor on the EVAP system. Tests: (a) predicts a higher value cold or in fast idle and about 202 with A/C or the M idle (810 rpm). (c) predicts slow drift and no response to A/C or ECT. | — | none (hypothesis only) |
| 0x1D | none | — | 0 | none |
| 0x1E | none | — | 0 | none |
| 0x1F | none | — | 0 | none |
| 0x27 (known, for context) | "lmt" ignition limit `(x−24)/4` (kerpz). "InjAdv2" (hondamotor). "Ignition 2 °BTDC" (dip). `(x−128)/2` (ArduinoHondaUNI). | Names differ. All agree it is a second ignition value. | The P2P shows mostly 0, else 1–4, tracking rpm. That is not a timing angle: the sources do not fit this car. | low |
| 0x29 | **"IAC – idle air control valve"** (hondamotor.ru enum, no formula, no ECU). dip's logger shows **two** IAC channels: "Drive IAC (mA)" and "Control IAC (%)". kerpz, mr-sneezy (HOPE) and UNI put IACV at 0x28 and say nothing on 0x29. | Conflict on the slot: 3 sources say 0x28, 1 says 0x29. The P2P data back 0x28 as the command (0–58 raw, follows idle-air demand). | 85 now, 40 a few minutes earlier, while 0x28 sat at single digits. Inference: the second IAC quantity, either the **drive current or duty** (dip's "Drive IAC mA") or a **learned idle-air correction** that drifts slowly as the engine warms. Test: log 0x28 and 0x29 together through warm-up, A/C on and off, and a fan kick. A drive or duty value moves within a second of 0x28. A learned value moves over minutes and survives a restart. | low-medium that it is IAC-related |
| 0x2A | none | — | 5 at idle. Inference: a small counter or state value (an idle-control or closed-loop stage). | none |
| 0x2B | none | — | 0 | none |
| 0x2C | none | — | 0 | none |
| 0x2D | none | — | 0 | none |
| 0x2E | none | — | 0 | none |
| 0x2F | none | — | 0 | none |
| 0x30 | none. kerpz reads row 0x30 but decodes only `dlcdata[14]` = 0x3C. | — | 0 | none |
| 0x31 | none | — | 0 | none |
| 0x32 | none | — | 45. Inference: part of the 0x32–0x3A block of steady values (45, 55, –, 45, 7, 45, 25, 25, 25). Steady repeated values look like set-points or learned or monitor values, not live sensors. | none |
| 0x33 | none | — | 55 (see 0x32) | none |
| 0x34 | none | — | 0 | none |
| 0x35 | "unknown" on the P2T: kerpz's comment `dlcdata[7] to dlcdata[12] unknown` = **0x35–0x3A** | 1 source (says only that they exist) | 45. The P2T also had live or non-zero data in exactly 0x35–0x3A. | none on meaning; the layout matches |
| 0x36 | as 0x35 | 1 | 7 | none |
| 0x37 | as 0x35 | 1 | 45 | none |
| 0x38 | as 0x35 | 1 | 25 | none |
| 0x39 | as 0x35 | 1 | 25 | none |
| 0x3A | as 0x35 | 1 | 25. Inference for 0x38–0x3A (three equal values): three related channels, such as per-cylinder or per-range knock or retard values, or the low byte of a 3-point table. Pure guess. | none |
| 0x3B | none | — | 0 | none |
| 0x3C (known, for context) | Knock: x/51 V (kerpz, mr-sneezy from HOPE), x/55 (UNI), "Knock" (hondamotor) | 4 sources agree on the slot | The P2P shows only 1 or 2, so it is not a voltage. It may be a knock count or flag. | slot high, formula low |

### Related slots outside the target range (they may help identify the targets)

| Byte | Meaning | Source |
|---|---|---|
| 0x09 | Flag2, bit 3 = SCS (service check) | hondamotor, UNI |
| 0x0C | b0 Alt-C, b1 fan control, b2 IAB, b3 VTEC-E, b7 Econo | UNI, mr-sneezy, hondamotor |
| 0x0B | b2 O2 heater 1. **b7** (UNI) or **b6** (hondamotor) = O2 heater 2 | UNI vs hondamotor. The P2P report reads 0x0B b6 as "engine running", which could equally be the secondary-O2 heater (on once running). |
| 0x40–0x5F | DTC nibbles, 2 codes per byte | kerpz, hondamotor |
| 0x61–0x6E | Second copy of VSS, RPM, ECT, IAT, MAP, PA, TPS, Bat, O2, STFT, IAC. Probably the freeze frame. | hondamotor |
| 0x76 | ECU id (10 bytes) | kerpz, hondamotor |
| 0x98 (2) | commented "freeze dtc" | kerpz |
| 0x9A (2) | OBD2 PID 03 fuel system status, raw | kerpz |
| 0x9C | OBD2 PID 04 calculated load, raw | kerpz, mr-sneezy (HOPE) |
| 0xA0 | secondary O2 (S2000) x/51.3 V | mr-sneezy |

The 0x98–0xA0 block looks like a J1979 PID mirror. The P2P answers both protocols, so 0x9A/0x9C/0xA0 can be checked directly against PIDs 03/04/15. If they match, that is cheap proof that the P2P slot table follows the S2000/P2T layout above 0x40 too.

## Three most useful leads

1. **0x1C as target idle (rpm/4 = 672 at warm idle).** It matches the 670 ± 50 base-idle spec and the RPM word's ×4 scaling. Test it by logging 0x1C against ECT during warm-up, against the A/C idle-up, and in the two idle levels (L ≈ 670, M ≈ 810). If it is the target, it should read about 202 at M, be higher cold, and step with A/C.
2. **0x28 and 0x29 are both IAC quantities.** hondamotor.ru calls 0x29 "IAC", and dip's OBD2 logger shows separate "Drive IAC (mA)" and "Control IAC (%)" channels. Log 0x28, 0x29 and rpm together, with a fan or ELD kick, A/C, and a restart. That separates a fast drive or duty value from a slow learned correction.
3. **The 0x30 row matches the P2T, and the 0x98–0xA0 PID mirror can be proved.** kerpz saw unknown live bytes at exactly 0x35–0x3A on his P2T. Read 0x9A/0x9C/0xA0 and compare them with OBD2 PIDs 03/04/15 to confirm the shared layout. Then sweep 0x30–0x3B across rpm, load and knock events (for example a hard pull in 4th) to classify that block. Static values point to learned or set-point data; values that follow knock point to retard. The slot table itself sits in the ROM: a P2P ROM dump with asm662 would settle every byte at once, because each slot is a pointer to a named RAM variable.

Also worth getting: **HOPE (Honda OBD Protocol Examiner, Mayhem74, v1.03)**. mr-sneezy took 0x9C, 0x28, 0x3C, 0x24 and the 0x0C bits from it, so its parameter files probably hold the fullest community slot list. It is a Windows .exe on Google Drive. I did not download it: that needs the user's go-ahead.

## Sources

| # | Source | URL | ECU / car | What it covers |
|---|---|---|---|---|
| 1 | kerpz ArduinoHondaOBD (hobd_uni, hobd_uni2, hobd_elm, hobd_lcd, hobd_u8g) | https://github.com/kerpz/ArduinoHondaOBD | P2T OBD2 stock, P30 OBD1 chipped | 0x00–0x3C core map. Row 0x30: "dlcdata[7] to [12] unknown" (0x35–0x3A), knock at 0x3C. 0x98, 0x9A (PID 03) and 0x9C (PID 04) in the ELM bridge. Init frame, rows, ECU id 0x76. The forks (AutotronicCommunity, JaderFarinon_OPT, nattimmis and others) add nothing. |
| 2 | kerpz ArduinoHondaUNI (successor) | https://github.com/kerpz/ArduinoHondaUNI (app.cpp, torque/HOBD.csv) | same | Full flag-bit table (0x08–0x0F, incl. 0x0D b3 Mount, 0x0C b1 fan, 0x0B b7 O2 heater 2). Alternative formulas: ign `(x−128)/2` on 0x26/0x27, knock x/55, "EGR" x/51.3 on 0x19. |
| 3 | mr-sneezy ArduinoHondaOBD1_to_OBD2_BT (code, CSVs, "Honda OBD code.docx") | https://github.com/mr-sneezy/ArduinoHondaOBD1_to_OBD2_BT | 1999 S2000 AP1 (OzDM) | Addresses "from HOPE": 0x9C CLV, 0x28 IACV, 0x3C knock, 0x24 inj, 0xA0 second O2, 0x0C bits. The docx copies the hondamotor.ru enum and the HOBDFlags list. |
| 4 | hondamotor.ru topic 98035 (OBD1/OBD2 HOBD protocol thread, 2009; enum by rvs and others) | http://hondamotor.ru/board/index.php?showtopic=98035&st=120 (also st=100, 140) | posters' cars: Civic MA9 D15Z3, Accord CH7/CC7, EG8, CR-V | `ValuesOffset` enum: Flag1–Flag8 = 0x08–0x0F, ALTF 0x18, InjAdv1/2 0x26/0x27, **IAC 0x29**, knock 0x3C, DTC 0x40/0x50, freeze copy 0x61–0x6E, ECUID 0x76. Injector-time formula `x/400 + 0.6` ms (rvs). The pages are partly mojibake. |
| 5 | pgmfi.org "Stock ROM Datalogging Information" (2005, archived) | http://web.archive.org/web/20060118122034/http://forum.pgmfi.org:80/viewtopic.php?t=5871 | P13 OBD1 stock (via CN2); the protocol was found by "dip" on an OBD2 ITR | Command format, offsets 0–2/0x0B/0x0C/0x0D/0x10/0x12/0x14/0x15 on the P13. Key claim: **"the offset of the data in the ROM's lookup table"**, so the slot is a table index, not RAM. Pages 2–5 were not retrievable (archive captures redirect to page 1; the live forum returns HTTP 500). |
| 6 | Didier Pelegri ("dip") K-Line page, archived: logger screenshot and Communication_ECU.exe strings | http://web.archive.org/web/2007/http://d.pelegri.free.fr:80/K-Line/ | Integra Type R OBD2 (P73) | Channel list of the original OBD2 logger: RPM, VSS, ECT, IAT, MAP, TPS, HO2S, Corr CT/LT, FSS, Battery, ALTF, **Drive IAC (mA), Control IAC (%)**, Injection, Ignition 1/2. Flags: Start, FP, EVAP purge, Drive HO2S, VTS, Brake, A/C sw, A/C clutch, **Immo**, SCS, MIL. No addresses are visible (VB6 binary; strings read only, not run). |
| 7 | andgarriv Honda-ECU-Data | https://github.com/andgarriv/Honda-ECU-Data | '98 Civic D14A3 (OBD2A) | Core map only. TPS `(x−25)/2.08`. Nothing in the target range. |
| 8 | Hondash ECU compatibility table | https://www.hondash.net/2014/12/obd-compatibility-table.html | lists P2P-Q01/A31/A33/A34/K71, P2T, P2E, P28, P30, P72, P73 | Confirms that the P2P family answers the Honda protocol. No parameter list. |
| 9 | HOPE (Honda OBD Protocol Examiner) by Mayhem74, drive2.ru | https://www.drive2.ru/l/1463475/ | OBD1 and OBD2 Hondas | The tool mr-sneezy used. The author says the memory layout can differ between ECUs. Parameter files not inspected (needs a download). |
| 10 | pgmfi wiki (archived) DLC, DLCCommunication, OBD0StockDatalogging | http://web.archive.org/web/2008/http://www.pgmfi.org:80/twiki/bin/view/Library/DLCCommunication | — | Physical layer only. The application layer was never written. |
| 11 | P2P confirmed behaviour (in-repo) | docs/analysis/2026-11-25/report-sensors.md, report-idle-stall.md | P2P | 0x28 = IAC command (0–58), 0x27 not a timing value, 0x3C only 1–2, idle levels 665–680 and 805–815 rpm. |

Searched with nothing relevant found: GitHub code search for the init frame and `20 05 76 0A` (only kerpz, ArduinoHondaUNI and a scan-tool car list, Wanpd993/cascade). pgmfi forum threads 5871 (pages 2–5) and 7412 (live site HTTP 500). Hondata, eCtune, Crome and Neptune: their public definitions are real 66207 RAM addresses for chipped OBD1 code, not diagnostic slots, so they do not map onto this table. Honda HDS help (2013 Civic only). VIRUXE hondaecu-cli (P28 emulator, no diagnostic-table notes). habr K-line article (SRS, not PGM-FI).
