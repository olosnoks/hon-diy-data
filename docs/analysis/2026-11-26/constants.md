# 2026-11-26: the bytes that never change at warm idle (constants and status tables)

Car: 2000 Civic EX, D16Y8, stock P2P (OBD2B). Read-only desk work: no serial port, no
database writes. Inputs:

- `work-sweep/car-214932-wide.csv`: 210 sweeps of 0x00-0xFF, 21:49:32-21:53:02, warm idle,
  with the ECT false-cold fault active (ECT raw 51-183).
- `firstcontact-report.md` (11-25 17:38) and `probe-car.md` (11-25 18:17): ISO 9141-2 replies.
- The `ecu_id` header (0x76 + 10) of all 32 real connects in the 22:13 database copy
  (`work-learn/hondalog.db`, opened `mode=ro`).

Scripts and their output are in `work-constants/`: `constants.py` (every formula in the map
plus the J1979 ones, applied to every fixed byte; `constants-out.txt`), `x86.py` (0x86 against
ECT, STFT and O2, one line per sweep; `x86-out.txt`), `dbpeek.py` (drive headers),
`startect.py` (start-up ECT and IAT of each drive; `startect-out.txt`).

## What the CSV adds to the brief's list

The list in the brief is complete. Exact values over 210 sweeps:

- 0x32: 45 ×197, 44 ×13. 0x37: 45 ×185, 44 ×25. 0x33 = 55, 0x35 = 45, 0x36 = 7,
  0x38 = 0x39 = 0x3A = 25 in every sweep. 0x30, 0x31, 0x34, 0x3B = 0.
- 0x80-0x8F: `F8 00 F9 00 F0 00 FD/F9 F8 FC F0 00 F4 F8 00 00 8E`. Only 0x86 moves.
- 0x90-0x9F: `00 00 00 00 02 0F 04 00 00 05 02 00 [load] 00 00 00`.
- 0xA0 47-50, 0xA4 160/161, 0xAD = 1. Everything else in 0xA1-0xFF is 0.
- **0x78-0x7F has never changed on any connect:** `10 99 0A 04 24 00 03 01` in all 31 headers
  from 11-25 18:19 to 11-26 21:58. That covers about 30 key cycles, warm and cold starts and two
  days. Only the first two bytes (0x76:77, the idle target) change. One side note: the target
  at connect is often 0x0A78/4 = **670 rpm** or 0x0CA7/4 = **809.75 rpm**. Those are the two
  idle levels seen on 11-25 (670 ± 50 and about 810).

## Reference: what this car's PID 01 actually is

The raw reply from first contact is `48 6B 10 41 01 | 00 07 65 25 | 96`. Decoded by SAE J1979
([Wikipedia: OBD-II PIDs, service 01 PID 01](https://en.wikipedia.org/wiki/OBD-II_PIDs#Service_01_PID_01)):

| byte | value | bits | meaning |
|---|---|---|---|
| A | 0x00 | 0000 0000 | MIL off, 0 DTCs |
| B | 0x07 | 0000 0111 | misfire, fuel system and comprehensive components supported (bits 0-2); all complete (bits 4-6 = 0); spark ignition (bit 3 = 0) |
| C | 0x65 | 0110 0101 | supported: catalyst (b0), evap (b2), O2 sensor (b5), O2 heater (b6) |
| D | 0x25 | 0010 0101 | not complete: catalyst (b0), evap (b2), O2 sensor (b5); heater complete (b6 = 0) |

Other replies that the bytes can be checked against: PID 03 = `02 00`; PID 02 of freeze frame 0
= `00 00` (no freeze DTC); PID 04 = 0x3E (24.3 %); PID 15 = `C3 8D` (B1S2 0.975 V).
Mode 06 answered only TID 00 (`46 00 FF 00 01 81 81`, supported TIDs 10 11 18 19 20). TIDs 10-20
were never requested, so **no Mode 06 test value or limit exists to compare against.** Mode 05
gave only a support bitmap.

## Per-byte table

The formulas tried are from `constants.py`: the ECT/IAT poly; `x-40`; MAP `x*0.716-5`;
`x/2.55`; `x/51`; `x/51.3`; `x/200`; `x/10.45`; ELD; trim `(x/128-1)*100`; ign `(x-24)/4`,
`(x-46)/4` and `(x-128)/2`; `x*4`; `x*25`; `x*0.1`. Only the readings that give sensible
units are quoted below.

| byte(s) | value | candidates, with the arithmetic | best fit | confidence | one on-car observation that settles it |
|---|---|---|---|---|---|
| **0x9A:0x9B** | `02 00` | **J1979 PID 03 fuel system status, both bytes**: PID 03 reply was `02 00` (closed loop, no second fuel system). It sits next to 0x9C = PID 04. The two bytes match exactly. | PID 03 mirror | **high** | at a cold start or at WOT/decel, 0x9A should read 1 (open loop, not yet ready) or 4 (open loop, driving condition) at the same moment that PID 03 changes. Nothing needs to be cleared. |
| 0x9C | 53-62 | **PID 04 raw**: the PID 04 reply was 0x3E = 62 → 24.3 %. 0x9C reads 53-62 (21-24 %) and tracks MAP. | PID 04 mirror | high | snap the throttle: 0x9C and PID 04 both rise |
| 0xA0 | 47-50 | **PID 15 A / 4**: 195 / 4 = 48.75, inside 47-50. As a voltage, `x*4/200` = 0.94-1.00 V, and the OBD2 reading was 0.975 V. The map's `x/51.3` gives 0.92-0.97 V, nearly the same. So the slot looks like the rear-O2 byte at 1/4 resolution (20 mV per count). | rear O2 (B1S2) at 4× PID scale | medium (the two readings came on different days) | read 0xA0 and PID 15 in the same minute on a decel: both fall together |
| 0x98:0x99 | `00 05` | (a) kerpz's "freeze dtc" slot (2 bytes): this car's PID 02 is `00 00` and the slot holds 0x0005, so **it does not match** as a J1979 code. (b) 0x99 = D & 0x0F = 0x25 & 0x0F = 5: catalyst and evap incomplete in J1979 bit positions. It leaves out the O2 monitor (bit 5), so the fit is only partial, and 5 is a common value. (c) A small counter: trips, warm-ups or key cycles since the memory reset. Small values fit three monitors not ready. | (c) counter, or (b) | low | read 0x90-0x9F once at the start of each of the next 3 sessions. A counter steps once per key cycle or warm-up. A readiness mask changes only when a monitor completes (for example catalyst after a 10-min steady highway cruise: D goes 0x25 → 0x24 and the Honda bit should drop at the same time) |
| 0x94 | 2 | (a) Readiness in Honda's own order: "complete" mask with the O2 heater (the only complete non-continuous monitor) at bit 1. (b) A counter (see 0x99). PID 01 A = 0x00 rules out "DTC count" and "MIL" for any non-zero byte. | (b) or (a) | low | as 0x99 |
| 0x95 | 15 (0x0F) | (a) **"supported non-continuous monitors" packed into 4 bits**: catalyst, evap, O2, O2 heater = 4 monitors → 0x0F. PID 01 C (0x65) also lists exactly 4. (b) A 4-bit counter at its limit (15). | (a) | low-medium | stays 15 forever if it is (a). If (b), it never changes until a reset, and after a reset it counts up from 0. Clearing codes is **not** allowed without the owner's say-so, so watch for a reset by other means (battery work) |
| 0x96 | 4 | as 0x94 ("complete" mask with the heater at bit 2, or a counter) | as 0x94 | low | as 0x99 |
| (readiness test overall) | 94..99 = `02 0F 04 00 00 05` | J1979 bytes `00 07 65 25` do not appear anywhere in 0x80-0xFF. In packed Honda order, the "incomplete" set must have exactly 3 bits (cat, evap, O2). No byte equals 0x07, 0x0B, 0x0D or 0x0E. The only byte in 0x80-0xAD with 3 low bits set is 0x86 = FD (low nibble 1101), and that byte is live (bit 2 drops with rich O2), so it is not a stored readiness mask. So the readiness bytes, if they are here at all, are not stored as a plain packed "not ready" mask. | not a straight mirror of PID 01 | - | a monitor completing (see 0x99) is the only clean test |
| 0x97, 0x98 | 0 | "DTC count" / "freeze DTC high byte": both would be 0 on this car. A zero cannot prove either. | - | none | read again when a code is stored (do not provoke one) |
| **0x78-0x7F** | `10 99 0A 04 24 00 03 01` | ASCII: `....$...`, no. "P2P" (`50 32 50`): absent. Part-number digits: this car's family is 37820-P2P-A11/A12 (2000 EX M/T, [hondapartsnow](https://www.hondapartsnow.com/oem-2000-honda-civic-engine_control_module.html)); "11"/"12" do not appear, and reading `03 01` as "31" is a stretch. Checksums: sum8 = 0xDF, xor = 0xA1, sum16 of BE words = 0x419E; none is 0x00, 0xFF or a closing byte. **BCD date `10 99` = October 1999** is plausible for a 2000 MY calibration (MY2000 production began mid-1999), but 0x0A is not BCD, so the rest is binary (0x0A04 = 2564, 0x2400, 0x0301). | ROM/calibration identity, constant across all key cycles. "10/99" date: maybe | **high** that it is a ROM constant; low on the date | the owner reads the label on the ECU (37820-P2P-xxx plus the small calibration or date stamp). If it shows a 10/99 or 99-10 date, or a code with 2564 or 0301 in it, that settles the layout. A second P2P ECU (another car) with a different calibration would show which bytes change |
| **0x80-0x8C** | `F8 00 F9 00 F0 00 FD F8 FC F0 00 F4 F8` | (a) Signed learned table: -8 0 -7 0 -16 0 -3 -8 -4 -16 0 -12 -8 (/4 = -2.0 … -4.0 °, if retard). But the D16Y8 has no knock-learn worth a table, and an idle-air or fuel-trim learn cell would pass through values in between. (b) **Bit fields with the high nibble all set**. The deciding fact is **0x86**: it changes only between FD (1111 1101) and F9 (1111 1001), only bit 2, 17 times in 210 s, **never through -4, -5 or -6**. It goes to F9 when O2 raw is high (38-45, the rich side) and STFT ≥ 134 (`x86-out.txt`), in false-cold and warm alike (8/76 vs 8/134 sweeps). A learned value would ramp; a flag snaps. | (b) status or condition bit fields (OBD2 monitor enable conditions or test status, active-low or with spare bits set). **0x86 bit 2 = "O2 rich" or "STFT above a limit" condition** | medium (bit fields), low (which monitor) | a steady 50-60 mph cruise for 10 min (catalyst/O2 monitor conditions met): bit-field bytes should change state, a trim table should not. 0x86 bit 2 should follow every rich/lean swing of the front O2 at 10 Hz |
| 0x8F | 142 | (a) trim `(142/128-1)*100` = +10.9 %, next to LTFT 0x22 = 139 (+8.6 %; 141 = +10.2 % on 11-25 18:17), so possibly a second LTFT zone (cruise or idle). (b) ECT/IAT poly = 27.4 °C: an IAT or ECT recorded at start-up? Tonight's first connect (20:31) read IAT raw 149 (25 °C), ECT raw 67, so (b) is only close, not exact. (c) Voltage x/51 = 2.78 V: no matching sensor. | (a) second LTFT cell | low | after a 20-min drive with cruise, 0x8F moves slowly (minutes) and not with STFT if it is a learned trim. If (b), it changes only at the next cold start |
| 0xAD | 1 | flag, or a monitor/trip status. No formula gives units. | flag | none | watch it at the next cold start and after a drive |
| 0xA4 | 160/161 | `1010 000x`: a flag byte. Bit 0 was on while the idle sat at 1020-1074 rpm (the sweep report). | flag byte | low | (live, out of scope) |
| **0x38, 0x39, 0x3A** | 25, 25, 25 | ECT poly 25 → **99.5 °C**; x/51 = 0.49 V; x/2.55 = 9.8 %; x*25 = 625 rpm. Three equal fixed values: thresholds or three copies of one calibration constant. 99.5 °C is a plausible "hot" threshold (fan or A/C cut level). 625 rpm is close to the low-idle limit, but there is no Honda precedent for x*25. | ECT-scale threshold 99.5 °C, or a calibration constant | low | when the coolant passes about 99 °C (long idle, fan cycling), something keyed to it (fan bit 0x0C b1, A/C cut) should switch at ECT raw ≈ 25. The bytes themselves should stay 25 |
| 0x33 | 55 | ECT poly → **67.5 °C**; `x-40` → 15 °C; x/51 → 1.08 V; `(x-46)/4` → 2.25°. The OBD2 warm-up cycle needs ECT ≥ 71 °C (160 °F) and a rise of ≥ 22 °C (CARB 13 CCR 1968.2, [ARB attachment](https://ww2.arb.ca.gov/sites/default/files/barcu/regact/obdii06/obdattacha.pdf)). On this poly, 71 °C is raw ≈ 50, not 55, so it is close but not equal. | ECT threshold ≈ 67 °C (warm-up / monitor enable) | low | at a cold start, watch which status bit changes as ECT raw falls through 55 (for example 0x2F starting, or a bit in 0x80-0x8C / 0x94-0x99) |
| 0x35 | 45 | ECT poly → **75.6 °C**; x/51 → 0.88 V. Equals the resting value of 0x32/0x37 but never flickers, so it is a stored copy, not an ADC. | stored threshold or reference (75.6 °C if ECT-scaled) | low | as 0x33, for ECT raw falling through 45 |
| 0x32, 0x37 | 45 (44 at times) | 1-LSB flicker = a live 8-bit ADC of a steady input: x/51 = 0.88 V (0.86 V at 44). ECT poly gives 75.6 °C, but they ignored the ECT swings 51 → 183, so they are not ECT. Candidates: an unused or idle analog input, or the EVAP tank-pressure or reference input. | live ADC of a fixed voltage, about 0.88 V | low | unplug nothing. Watch them while the fuel cap is opened (tank pressure goes to atmosphere) and during a hot soak |
| 0x36 | 7 | No formula gives units (`x-40` = -33 °C, poly 135.6 °C off scale, x/51 = 0.14 V). A small integer: a count, index or mode number (BCD "07"). | calibration constant or mode index | none | none practical; needs the ROM |

## Hypotheses tested and rejected

- **PID 01 copied byte for byte**: `00 07 65 25` is not anywhere in 0x80-0xFF.
- **0x98:0x99 = freeze-frame DTC (kerpz label)**: PID 02 is `00 00`, the slot is `00 05`.
- **0x80-0x8C as a ramping learned trim**: 0x86 jumps by exactly one bit (4 counts) and never
  passes through the values in between.
- **A DTC count in a non-zero byte**: the ECU reports 0 DTCs and MIL off, so any DTC-count slot
  must read 0. Candidates are 0x97 and 0x98 (both 0); 0x94/0x96/0x99 are out.
- **ASCII or "P2P" in the ID block**: no printable run, no `50 32 50`.
- **Mode 06 values in 0x80-0x8F**: none can be checked. Mode 06 TID 10-20 were never requested, and
  pre-CAN Honda TIDs are manufacturer-specific. Honda's own charts even disagree on the catalyst
  CID ($01 vs $81, [MOTOR, Foreign Service, Jan 2012](https://www.motor.com/magazine-summary/foreign-service-jan-2012/)).
  Mode 06 values are unsigned test results with min/max limits, and this table holds small
  values with the high nibble set. That does not look like Mode 06.

## Strongest three

1. **0x9A:0x9B is J1979 PID 03 byte for byte** (`02 00` = closed loop, no second fuel system),
   next to 0x9C = PID 04 raw. 0xA0 is about PID 15 A / 4 (48.75 vs 47-50). Above 0x98 the Honda
   table carries an OBD2 PID mirror, as kerpz and mr-sneezy (HOPE) said for the P2T/S2000.
2. **0x78-0x7F is a ROM/calibration constant**, not RAM state: the same 8 bytes on all ~31 connects
   over two days and every key cycle. The old `ecu_id` should read 0x78+8. `10 99` may be a BCD
   10/99 date; the ECU label will settle it.
3. **0x80-0x8C are bit fields, not a signed trim table.** 0x86 flips only bit 2 (FD ↔ F9) with no
   values in between, and only on the rich side of the front O2 / high STFT. It is a condition or
   status bit, most likely part of the OBD2 monitor-condition bookkeeping. That fits the three
   monitors that never complete at idle. A steady highway cruise should change these bytes.

## What would settle the rest

- One read of 0x80-0x9F (two 16-byte reads) **at the start of every session**, and during a
  10-minute 50-60 mph cruise. That separates counters (0x94/0x96/0x99 step per key cycle),
  readiness masks (change when PID 01 D changes) and condition bits (0x80-0x8C change while
  cruising). Read PID 01 in the same session for comparison.
- The ECU label (part number and calibration/date stamp) for 0x78-0x7F.
- A cold start logged from key-on, to see what switches as ECT raw passes 55, 45 and 25.
- Clearing codes would reset any counters and readiness at once and prove them. It is **not
  allowed** without the owner's say-so, and it would also cost the monitors that are already ready.
- A P2P ROM dump would name every slot at once.

## Sources

- SAE J1979 PID 01 layout: [Wikipedia, OBD-II PIDs, service 01 PID 01](https://en.wikipedia.org/wiki/OBD-II_PIDs#Service_01_PID_01)
- Honda pre-CAN Mode 06 numbering and the catalyst CID $01/$81 discrepancy: [MOTOR, Foreign Service, Jan 2012](https://www.motor.com/magazine-summary/foreign-service-jan-2012/); Mode 06 TIDs not standardized before CAN: [Brake & Front End, Checking into Mode $06](https://www.brakeandfrontend.com/obd-ii-diagnostics-checking-into-mode-06/)
- Warm-up cycle definition (≥ 40 °F rise, reaching ≥ 160 °F): CARB 13 CCR 1968.2, [ARB OBD II attachment A](https://ww2.arb.ca.gov/sites/default/files/barcu/regact/obdii06/obdattacha.pdf)
- 2000 Civic EX ECU part numbers (P2P-A11/A12 M/T, A91/A92 A/T): [hondapartsnow](https://www.hondapartsnow.com/oem-2000-honda-civic-engine_control_module.html)
- 0x98/0x9A/0x9C/0xA0 slot names: kerpz ArduinoHondaOBD and mr-sneezy (HOPE-derived), as collected in `raw-bytes-literature.md`
