# 2026-11-27: what the stock P2P ECM should contain (factory service manual checklist)

Car: 2000 Civic EX, D16Y8 SOHC VTEC, USA, M/T, OBD2B, ECM P2P.
Source: `C:\Users\skons\hondalog\manual\Honda Civic 1996-2000 Service Manual.pdf` (2,189 PDF pages).
Page numbers below are **PDF pages** (the same numbering earlier reports use), with the printed manual page
in brackets where useful. Text was extracted with pypdf, and the key pages were read from the scanned page
images (the OCR text is too garbled to trust for tables). Scripts, the text dump and page images are in
`work-manual/` (`extract.py`, `img.py`, `g.py`, `manual.txt`, `img/`).

Everything here is a summary of the manual in my own words, unless it is marked **(inference)**.
The manual describes inputs, outputs and conditions. It says nothing about RAM, so the RAM table in section 6
lists what the ECM *must* compute or hold to do what the manual describes.

---

## 0. Things to know first

- **Use the right pinout.** The manual has two ECM terminal arrangements. A 2000 D16Y8 M/T uses
  **"'99-00 models except D16Y5 engine with M/T"** (p.361-364 [11-92 to 11-95], connector views p.2135-2136
  [202-28/29]). The other table (p.357-360, "'96-98, '99-00 D16Y5 M/T") has different pin numbers and is the
  wrong one for this car. Earlier reports' "C18 SG2 / C26 ECT" already match the correct table.
- **Connectors on this car:** A (32P, grey, main harness), B (25P, grey, engine harness), C (31P, blue, engine
  harness). Connector **D (16P) carries only A/T or CVT signals**, so it is not used on an M/T car (p.364-365, p.2136).
- **Not fitted on a 2000 D16Y8 M/T** (checked against the footnotes):
  - EGR: the EGR valve, EGR solenoid, lift sensor and the P0401/P1491/P1498 codes are D16Y5-only
    (p.363 C6 = CVT only, p.362 B7 = CVT only, p.367, p.371).
  - Clutch switch input: D16Y5 M/T only (p.359 C29, p.373 footnote *6). Not on the '99-00 table at all.
  - Neutral, gear-position or cruise input: A5 (cruise), A7, A9 and A22 are A/T or CVT only (p.361).
    **So the ECM has no gear, neutral or clutch input. It can only infer them from rpm vs. VSS (inference).**
  - Lean-burn / LAF (wide-band) sensor and CKF lean-burn control: D16Y5 CVT only (p.375).
  - Shift-up indicator: only on the old '96-98 layout, pin A30 (p.2133). There is no such pin on the '99-00 layout.
  - Intake air bypass / IAB: not mentioned anywhere for the D16 engines.
  - Immobilizer: the manual has no immobilizer section and no immobilizer pin (a text search finds nothing).
  - Engine-mount solenoid: there is no such output on this pinout. So the literature label "0x0D bit 3 =
    engine-mount solenoid" (README 2026-11-26) cannot apply to a P2P in that meaning.
- **Fitted, though sometimes assumed absent:**
  - **Knock sensor**: C3. P0325 applies to "D16Y5 CVT, D16Y8 and B16A2" (p.367, p.440). The ECM runs knock control
    that sets ignition timing for the fuel's octane (p.373).
  - **ECM-driven radiator fan**: A20 FANC (p.361, p.1597).
  - **Alternator control**: C2 ALTC out, C5 ALTF in, A30 ELD in (USA) (p.363, p.373, p.1587).
  - **CKF** (crankshaft speed fluctuation sensor): C22/C31. It is used for misfire detection (p.435).
  - **Full '98+ EVAP leak system**: fuel tank pressure sensor A29, EVAP bypass solenoid A3, canister vent shut
    valve A4, purge solenoid A6. Codes P1456/P1457/P0451-P0453 apply; P0441 does not (p.368-370).
  - **Internal BARO sensor** inside the ECM (P1106-P1108: "ECM/PCM (Baro sensor)", p.368).

---

## 1. ECM connector pinout: 2000 D16Y8 USA M/T

The pin assignments come from p.361-363 [11-92 to 11-94], with the M/T fitment taken from the connector-view
footnotes on p.2135-2136. Voltages are as the manual states them, with battery = 12 V.

Type key:
- AI = analog in
- SW = switch in
- PI = pulse in
- DO = on/off low-side driver out
- PWM = duty-controlled out
- PWR/GND = power supply or ground
- COM = communication line

### Connector A (32P)

| Pin | Wire | Signal | Dir / type | Manual behaviour |
|---|---|---|---|---|
| A3 | BLU | 2WBS, EVAP bypass solenoid | DO | battery V at key-on (off) |
| A4 | LT GRN/WHT | VSV, EVAP canister vent shut valve | DO | battery V at key-on (off, vent open) |
| A6 | RED/YEL | PCS, EVAP purge control solenoid | PWM | battery V (off) running below 68 °C; duty-controlled above 68 °C |
| A8 | BLK/WHT | SO2SHTC, secondary O2 heater | PWM | battery V at key-on; duty-controlled when warm and running |
| A10 | BRN | SCS, service check signal | SW | 0 V when jumped, ~5 V or battery V when open |
| A16 | GRN/YEL | FLR, PGM-FI main relay / fuel pump | DO | 0 V (relay on) for 2 s after key-on, then battery V (off) until the engine runs |
| A17 | BLK/RED | ACC, A/C clutch relay | DO | 0 V = compressor on |
| A18 | GRN/ORN | MIL | DO | 0 V = lamp on |
| A19 | BLU | NEP, engine speed pulse | pulse out | pulses with the engine running (the tachometer is driven by the ICM, p.1599) |
| A20 | GRN | FANC, radiator fan relay | DO | 0 V = fan running |
| A21 | BLU/YEL | K-LINE | COM | scan tool line |
| A23 | WHT/RED | SHO2S, secondary O2 (sensor 2) | AI | >0.6 V on a WOT snap, <0.4 V on a quick close |
| A24 | BLU/WHT | STS, starter signal | SW | battery V while cranking |
| A26 | GRN | PSPSW, power steering pressure switch | SW | 0 V straight ahead, battery V at full lock |
| A27 | BLU/RED | ACS, A/C switch (request) | SW | 0 V = A/C requested, ~5 V = off |
| A29 | LT GRN | PTANK, fuel tank pressure sensor | AI | ~2.5 V with the fill cap open (atmospheric); falls toward ~1.5 V under vacuum (p.543) |
| A30 | GRN/RED | EL, electrical load detector | AI | idle with parking lights: 2.5-3.5 V; low beams: 1.5-2.5 V. Voltage falls as load rises |
| A32 | GRN/WHT | BKSW, brake switch | SW | battery V with the pedal pressed |

On an M/T car, A1, A2, A5, A7, A9, A11-A15, A22, A25, A28 and A31 are unused.

### Connector B (25P)

| Pin | Wire | Signal | Dir / type | Manual behaviour |
|---|---|---|---|---|
| B1 | YEL/BLK | IGP1, power | PWR | battery V with ignition ON |
| B2 | BLK | PG1, power ground | GND | |
| B3 | RED | INJ2 | PWM | duty-controlled |
| B4 | BLU | INJ3 | PWM | |
| B5 | YEL | INJ4 | PWM | |
| B9 | YEL/BLK | IGP2, power | PWR | |
| B10 | BLK | PG2 | GND | |
| B11 | BRN | INJ1 | PWM | |
| B12 | GRN/YEL | VTS, VTEC solenoid | DO | 0 V at low rpm, battery V at high rpm (this driver sources current) |
| B13 | YEL/GRN | ICM, ignition control module | pulse out | battery V at key-on, pulses running |
| B20 | BRN/BLK | LG1, logic ground | GND | |
| B21 | WHT/BLU | VBU, back-up power (keeps DTC and learned memory) | PWR | battery V at all times |
| B22 | BRN/BLK | LG2 | GND | |
| B23 | BLK/BLU | IACV, idle air control valve | PWM | duty-controlled. This is the single-ended 2-wire valve used on M/T except D16Y7 |

B6 (IACV P) and B15 (IACV N) are for A/T and D16Y7 only. B7, B8, B17, B18 and B25 are A/T or CVT only.
The M/T IAC valve has a 2P connector whose other pin is fed from the PGM-FI main relay (p.459, p.489).

### Connector C (31P)

| Pin | Wire | Signal | Dir / type | Manual behaviour |
|---|---|---|---|---|
| C1 | BLK/WHT | PO2SHTC, primary O2 heater | PWM | battery V at key-on; duty-controlled when warm |
| C2 | WHT/GRN | ALTC, alternator control | DO | battery V when warm and running; 0 V when driving with a small electrical load (reduced charge voltage) |
| C3 | RED/BLU | KS, knock sensor | AI (AC) | pulses when knocking |
| C5 | WHT/RED | ALTF, alternator FR signal | AI/duty in | 0 V to battery V depending on electrical load |
| C7 | GRN/WHT | SG1, sensor ground (MAP) | GND | |
| C8 / C9 | BLU / WHT | CKPP / CKPM, crank position sensor | PI | |
| C10 | BLU/BLK | VTM, VTEC pressure switch | SW | 0 V at low rpm, battery V at high rpm |
| C16 | WHT | PHO2S, primary O2 (sensor 1) | AI | >0.6 V on a WOT snap, <0.4 V on a quick close; 0.1-0.9 V at idle (p.355) |
| C17 | RED/GRN | MAP | AI | ~3 V at key-on, ~1.0 V at idle |
| C18 | GRN/BLK | SG2, sensor ground | GND | |
| C19 | YEL/RED | VCC1, 5 V supply for MAP | PWR out | |
| C20 / C21 | GRN / RED | TDCP / TDCM, TDC sensor | PI | |
| C22 / C31 | BLU/RED / WHT/RED | CKFP / CKFM, crank speed fluctuation sensor | PI | |
| C23 | BLU/WHT | VSS | PI | pulses 0 to ~5 V (or battery V) when the wheels turn |
| C25 | RED/YEL | IAT | AI | 0.1-4.8 V |
| C26 | RED/WHT | ECT | AI | 0.1-4.8 V |
| C27 | RED/BLK | TPS | AI | ~0.5 V closed, ~4.8 V WOT |
| C28 | YEL/BLU | VCC2, 5 V supply | PWR out | |
| C29 / C30 | YEL / BLK | CYPP / CYPM, cylinder position sensor | PI | |

C6 (EGRL) is CVT/D16B5 only. C4, C11-C15 and C24 are unused.

### Summary counts (M/T D16Y8)

- **Analog inputs (9):**
  - ECT, IAT, MAP, TPS
  - PHO2S, SHO2S
  - PTANK, ELD, KS
  - The ALTF line is also read as a voltage or duty. Supply voltage is sensed through IGP1/IGP2; there is no
    separate battery-sense pin.
- **Switch inputs (7):** STS, ACS, PSPSW, BKSW, SCS, VTM. The CKP/TDC/CYP/CKF/VSS lines are pulse inputs, not switches.
- **Pulse inputs (5 sensors):** CKP, TDC, CYP, CKF, VSS.
- **Outputs:**
  - PWM (8): 4 injectors, IACV, PCS, two O2 heaters
  - On/off (9): VTS, FLR, ACC, MIL, FANC, ALTC, 2WBS, VSV, and ICM (pulse)
  - NEP (pulse)
  - K-line

---

## 2. Control functions and the conditions the manual gives

### 2.1 Idle control (IAC valve, B23)

- **What raises the idle (p.373 [11-104]):** cold engine, A/C on, transmission in gear (A/T), brake pressed,
  high power-steering load, alternator charging. The idle troubleshooting list adds the starter signal, A/C
  signal, ALT FR, brake switch and PSP switch as inputs to idle control (p.460 [11-191]). Clutch switch is D16Y5
  only, so it does not apply here.
- **After-start idle-up (p.460):** after the engine starts, the IAC valve opens "for a certain time", which
  raises idle by about 150-300 rpm. **This implies an after-start timer and a decaying idle-up term (inference).**
- **Fast idle (p.460):** IAC air is set from ECT. The curve is flat from about -20 to +20 °C, falls between
  about 40 and 80 °C, and is flat again above about 80 °C. The M/T D16Y8 has no wax fast-idle valve; the
  coolant-heated rotary valve drawn on p.459 is the A/T one.
- **Idle speed specs for a '96-00 D16Y8 M/T, USA (p.489 [11-220]):**
  - IAC disconnected (base air only, after a 3,000 rpm warm-up until the fan comes on): **450 ± 50 rpm**
  - IAC reconnected, ECM reset, no load, after 1 min: **670 ± 50**
  - Headlights on low beam (ELD idle-up): **750 ± 50**
  - Blower on HI plus A/C on: **810 ± 50**
  - Canada M/T: 750 base.
- **EVAP purge idle state, 810 rpm (p.491 [11-222], p.492 [11-223]):** for the '98 D16Y5 M/T, the '99-00 B16A2
  and the '98-00 D16Y5 CVT, the manual says that if the warm no-load idle rises to **810 ± 50 rpm, the EVAP
  system is purging the canister**. Purging stops temporarily if you raise the engine above 1,000 rpm and
  release the pedal slowly. Those procedures also disconnect the purge solenoid to set base idle.
  - The D16Y8 M/T procedure (p.489) does not repeat this note. It does use exactly 810 for the A/C step.
  - The D16Y8 and the B16A2 share the same '99-00 purge rules (p.374 footnote *4), so the sibling note very
    likely applies to the P2P as well **(inference)**.
- **Scan-tool warm-idle reference values (p.355-356 [11-86/87], p.461):**
  - MAP 24-31 kPa
  - ECT 80-90 °C (p.355); P0505 verification wants 90-96 °C after the fan has run (p.461)
  - TP about 10 %
  - Timing 12 ± 2° BTDC, SCS jumped
  - Calculated load (CLV) 15-35 % at idle, 12-30 % at 2,500 rpm no load
  - STFT and LTFT within ±20 %
  - Closed loop at idle
- **IAC faults:** P0505 (idle speed out of control range; two-trip) and P1508 (IAC circuit, applies to
  "except A/T and D16Y7") (p.368, p.371, p.374).
- **Not in the manual:** dashpot or decel idle-up values, the size of the brake or PSP idle-up, and a "fan-on"
  idle-up.

### 2.2 VTEC (B12 VTS out, C10 VTM in)

- Pinout only: VTS is 0 V at low rpm and battery V at high rpm; VTM reads the same way (p.362-363).
- **The manual gives no engagement map** (no rpm, load, VSS or ECT thresholds). Only the test conditions appear:
  - P1259 road test for the D16Y8: 1st gear, above 6,000 rpm, held for at least 2 s, warm engine with the fan
    having cycled (p.114 [6-3]). The fault is set when the commanded state and the oil-pressure-switch state
    disagree.
  - Oil pressure with the solenoid off: below 49 kPa at 1,000, 3,000 and 5,000 rpm. With the solenoid energised
    at 5,000 rpm: above 390 kPa, and the switch reads battery V (p.115-116 [6-4/5]).
  - Solenoid resistance: 14-30 Ω (p.117).
- **Expect in RAM (inference):** a VTEC request flag, the solenoid output bit, the pressure-switch input bit, and
  probably an rpm and load hysteresis window plus a minimum-ECT and minimum-VSS gate. The project saw it on at
  ~5,400 rpm.

### 2.3 EVAP purge and EVAP leak hardware

- **Purge enable for 2000 models (p.374 [11-105], p.536 [11-267], footnote *4 covers '98, '99 and '00 all
  models):** the purge solenoid is duty-controlled after the engine starts when ECT > 68 °C **and** IAT > 0 °C
  **and** vehicle speed > 0. Purge is also allowed when the A/C clutch is on and IAT is above 41 °C; the text
  prints "160 °F (41 °C)", and 41 °C is really 106 °F, so the manual is inconsistent here.
  - The pinout states the same thing: PCS is off below 68 °C and duty-controlled above (p.361).
  - The hot vacuum check is done with the car on stands in 1st gear (p.554, p.559), which is consistent with
    purge needing VSS > 0.
  - **The p.491 note above shows purge can still be active at a parked idle.** So "vehicle speed above 0" may be
    a latch that is set once per trip rather than a live condition **(inference)**.
- **Bypass solenoid and vent shut valve:** both are off (battery V on the pin) at key-on (p.361). Their
  operating logic is not described. **There must be a leak-test sequence with a fuel-tank-pressure check
  behind P1456/P1457 (inference).**
- **Codes:** P1456 (fuel tank side leak), P1457 (canister side leak), P0451/P0452/P0453 (tank pressure sensor),
  all two-trip (p.368, p.370). P0441 applies only to the '96-'97 group without leak hardware.

### 2.4 O2 sensor heaters

- Primary (C1) and secondary (A8): battery V (off) at key-on, duty-controlled with the engine fully warm and
  running (p.361, p.363). Neither switch-on conditions nor duty values are given.
- **Codes:** P0135 (primary heater) and P0141 (secondary heater) (p.366-367). The heater element measures
  10-40 Ω (p.426).
- **Expect in RAM (inference):** two heater duty or flag values, with power cut at high load or exhaust
  temperature. The project saw 0x0B b6 off at high load.

### 2.5 Alternator control (C2 ALTC, C5 ALTF, A30 ELD; USA only)

- **Purpose:** charge voltage is set by electrical load and driving mode, to cut engine load and improve fuel
  economy (p.374).
- **ALTC states:** battery V when warm and idling (normal or high charge); 0 V when driving with a small
  electrical load (low charge voltage) (p.363). The test on p.1587 [23-117] checks with the high beams on.
- **ALTF:** 0 V to battery V with load (p.363). Turning the headlights and rear defogger on should lower it
  (p.478).
- **ELD:** parking lights 2.5-3.5 V, low beams 1.5-2.5 V; the voltage drops as load rises (p.361).
  Codes P1297 (low input) and P1298 (high input), USA only (p.369).
- **ELD also drives an idle-up:** 750 rpm with headlights on (p.489).

### 2.6 Radiator fan

- **Both an ECT switch and the ECM drive it** (circuit p.1597 [23-127]). The relay coil is grounded either by
  the **ECT switch (closes above 93 °C)** or by **ECM pin A20 FANC**, wired in parallel.
- The manual does not say when the ECM grounds FANC; on similar Honda ECMs it is with the A/C on **(inference)**.
  The condenser fan has its own relay, driven through the A/C circuit (p.1430).
- **Expect in RAM:** an FANC output bit. The ECM cannot see the ECT-switch path directly, although the ELD
  will see the fan current either way.

### 2.7 A/C compressor

- **On demand:** when the A/C switch signal (A27) arrives, the ECM delays engaging the clutch relay (A17) and
  enriches the mixture for a smooth changeover (p.374 [11-105]). The A/C idle target is 810 ± 50 (p.489).
- The A/C pressure switch (opens below 200 kPa or above 3,200 kPa) and the compressor's thermal protector sit in
  the request or clutch circuit, outside the ECM (p.1429).
- **Not in the manual:** WOT cut-out and high-ECT cut-out. Hondas of this era usually have both, so treat them
  as candidates only **(inference)**.
- **Expect in RAM:** the A/C request bit (mapped, 0x08 b1), the clutch output bit (mapped, 0x0B b1), and an
  A/C delay timer **(inference)**.

### 2.8 Fuel cut (p.374 [11-105])

- **Decel cut:** throttle closed and rpm above **920 rpm for a D16Y8 USA M/T**. Canada M/T is listed as 990 and
  as 1,000 in the same list.
- **Rev limit:** **7,000 rpm** for the D16Y8, whatever the throttle position. The "5,000 rpm when not moving" rule
  is for '99 A/T only, so it does not apply here.
- **Expect in RAM (inference):** a decel-cut flag, a rev-limit flag, and probably a fuel-cut resume threshold
  that depends on ECT. The project's injector pulse width already reads 0 during decel cut.

### 2.9 Starting and fuel pump

- **Cranking:** enriched by a longer injector pulse (p.373).
- **Fuel pump relay:** on for 2 s at key-on to prime, on while the engine runs, and cut when the ignition is on
  but the engine is stopped (p.373). **This implies an engine-running flag based on crank-signal time-out, and a
  2 s prime timer (inference).**

### 2.10 Ignition and knock

- Base timing comes from rpm × manifold-pressure maps and is corrected for ECT (p.373).
- Knock control sets timing for the fuel's octane on the D16Y8 (p.373).
- P0325 is checked by holding 3,000-4,000 rpm for 60 s with the engine warm (p.440 [11-171]).
- **Expect in RAM (inference):** a knock retard value, possibly per cylinder or global; a knock-sensor
  noise or background level; and possibly a learned octane value.

### 2.11 MIL, self-diagnosis, fail-safe (p.374 [11-105])

- **MIL:** on for 2 s at key-on as a bulb check.
- **Fail-safe:** a failed sensor is replaced by a fixed value.
- **Back-up circuit:** if the ECM itself fails, the injectors run from a back-up circuit.
- **Two-trip logic** covers the O2 sensors, fuel metering, idle control, ECT, EGR and EVAP.
- **Misfire** turns the MIL on after 2 consecutive driving cycles. A misfire bad enough to damage the catalyst
  makes the MIL blink at once.
- **Catalyst** (TWC) needs 3 consecutive driving cycles.
- **A driving cycle** = engine start, closed loop reached, engine stop.
- **Jumping the SCS connector** cancels the two-trip and driving-cycle logic, so the MIL blinks codes at once
  (p.374). With SCS jumped and no stored code, the MIL stays on at key-on (p.351).

---

## 3. OBD2 monitors, the conditions the manual gives, and the counters they imply

The manual's "problem verification" drives are the closest thing it has to enable conditions. All start from
an ECM reset, a 3,000 rpm no-load warm-up until the radiator fan comes on, and usually the SCS jumped.

| Monitor | DTCs (2000 D16Y8 M/T) | Manual conditions / logic | Implies in RAM (inference) |
|---|---|---|---|
| Catalyst | P0420 | Judged from front and rear O2 output in steady driving; 2 (chart) or 3 (p.374) driving cycles. Test: 40-55 mph for ~2 min, closed-throttle decel ≥3 s, then hold 35 mph until readiness completes (p.522) | catalyst monitor state, steady-cruise timer, rear-O2 switch counter, readiness bit, 3-cycle counter |
| Primary O2 response | P0133 (slow response) | Feedback period measured in steady driving; fails above a limit; two trips. Test: 55 mph steady in 5th until readiness (p.414) | O2 switch-period timer, switch counter, readiness bit |
| Primary O2 circuit | P0131 low, P0132 high | (p.366) | |
| Secondary O2 | P0137, P0138, P0139 (slow) | (p.367) | rear O2 min/max or switch tracking |
| O2 heaters | P0135, P0141 | electrical check of the heater circuits (p.366-367) | heater output feedback state |
| Fuel system | P0171 lean, P0172 rich | watches LTFT; two trips (p.433) | LTFT (mapped), fuel status (mapped 0x9A) |
| Misfire | P0300-P0304 | crank speed fluctuation from the CKF sensor (p.435-436) | per-cylinder misfire counters, rough-road or decel lock-out, 200/1,000-rev windows |
| EVAP leak | P1456, P1457, P0451-P0453 | tank-pressure checks with vent and bypass valves (p.552-560) | leak-test state machine, PTANK value, valve output bits, readiness bit |
| Comprehensive components | MAP P0106-P0108, P1128/P1129; BARO P1106-P1108; IAT P0111-P0113; ECT P0116-P0118 (P0116 check 80-93 °C, p.397); TP P0122/P0123, P1121/P1122; KS P0325; CKP P0335/P0336; CKF P1336/P1337; CKP/TDC P1359; TDC P1361/P1362; CYP P1381/P1382; VSS P0500; IAC P0505, P1508; VTEC P1259; ELD P1297/P1298; ECM P1607 | (p.366-371) | a DTC status table, pending (first-trip) flags, freeze-frame copy (p.355-356 marks which values are freeze data) |

Not monitored on this car: EGR (D16Y5 only) and P0441 purge flow (old group only). Secondary air does not exist.

**Freeze data (p.355-356):** DTC, rpm, VSS, MAP, ECT, loop status, STFT, LTFT, IAT, TP and CLV are frozen.
The HO2S voltage and ignition timing are not. **So the ECM holds a second copy of about 11 values somewhere in
RAM (inference).**

---

## 4. Analog inputs: expected voltages and ranges

| Input | Pin | Range / behaviour per manual | Page |
|---|---|---|---|
| ECT | C26 | 0.1-4.8 V; NTC thermistor; 80-90 °C warm (scan), 90-96 °C after the fan (P0505 check), P0116 check 80-93 °C; resistance curve on p.397 | 363, 355, 461, 397 |
| IAT | C25 | 0.1-4.8 V; NTC; equals ambient and ECT when cold | 363, 356 |
| MAP | C17 | ~3 V at key-on (≈ barometric), ~1.0 V at idle; 24-31 kPa warm idle | 363, 355 |
| BARO | internal | equals key-on MAP (P1106-P1108) | 368 |
| TPS | C27 | ~0.5 V closed, ~4.8 V WOT; ~10 % at idle on the scan tool | 363, 356 |
| Primary O2 | C16 | 0.0-1.25 V, 0.1-0.9 V switching at idle; >0.6 V rich on WOT, <0.4 V in decel | 355, 363 |
| Secondary O2 | A23 | same thresholds as the primary | 361 |
| ELD | A30 | parking lights 2.5-3.5 V, low beams 1.5-2.5 V (falls with load) | 361 |
| ALT FR | C5 | 0 V to battery V with load; falls with headlights and defogger on | 363, 478 |
| Battery | IGP1/IGP2 (B1/B9) | battery V at key-on. There is no separate sense pin; the ECM measures its supply | 362 |
| Fuel tank pressure | A29 | ~2.5 V at atmospheric (cap open); smooth fall toward ~1.5 V under vacuum; stop below 1.5 V | 361, 543 |
| Knock | C3 | pulses when knocking (AC signal) | 363 |
| VCC1, VCC2 | C19, C28 | ~5 V outputs | 363 |

There is no LAF or LABEL sensor and no EGR lift sensor on this car.

---

## 5. Highlights for the ~810 rpm idle state at a steady warm ECT

From the manual:

1. **810 ± 50 rpm is the manual's own number for "EVAP purging the canister" at a warm no-load idle** on the
   '99-00 B16A2 and '98 D16Y5 M/T (p.491) and the '98-00 D16Y5 CVT (p.492). These are the same ECM family and
   follow the same 2000 purge rules as the D16Y8 (p.374 *4).
   - The manual's way to stop it is to rev above 1,000 rpm and release slowly, and it stops only
     **temporarily**.
   - That matches the project's observation that the state "clears on a restart, a rev or pulling away". The
     ECT condition is met: purge needs ECT > 68 °C, and the state was seen at 85.9 °C.
   - It also fits the reported ~4 trim points of extra richness from fuel vapour.
2. **810 ± 50 rpm is also the D16Y8 M/T A/C idle target** (p.489). A/C request and clutch were unchanged in the
   21:44:17 event, so this is the less likely explanation. The same idle-target value may still be reused for
   other idle-up requests **(inference)**.
3. **Headlights or ELD load give 750, not 810** (p.489). An ELD step is unlikely, and ELD was unchanged anyway.
4. **The purge enable list includes "vehicle speed above 0"** (p.374, p.536). A purge state that starts while
   parked therefore needs either a once-per-trip latch on VSS, or a different cause.
   - The EVAP leak-test sequence (vent shut valve A4, bypass valve A3, PTANK A29) is a candidate. The manual does
     not describe when it runs, so this is **inference**.
   - The project's 0x2F stayed flat at 26-27 during the 810 step. Either 0x2F is not the purge duty, or the
     idle-up is held as a separate flag from the duty.
5. **The ECM-driven radiator fan (A20) is a weaker candidate.** The project notes the state "switches on after
   some fan cycles". The manual gives no fan idle-up, but the ECM does own a FANC output **(inference)**.

Suggested tests that need no code change: during an 810 episode, note whether pulling the purge solenoid 2P
connector drops the idle to 670. This is the manual's own method on p.491. Also log PTANK (A29) with a meter to
see whether a leak test is running.

---

## 6. Expected RAM quantities

"Mapped" refers to `~/.config/hondalog/addressmap.toml` (read only) and the 2026-11-26 README:
- **V** = verified
- **U** = mapped but unverified
- **S** = suspect
- **–** = not mapped (unaccounted for)

Behaviour at **warm idle / driving / key-on** follows the manual where a page is cited. Rows with no page are
**(inference)**.

### 6.1 Sensor values

| Quantity | Unit / range | Warm idle | Driving | Key-on, engine off | Page | Mapped |
|---|---|---|---|---|---|---|
| Engine speed | rpm | 670 (450 base air) | to 7,000 cut | 0 | 355, 489, 374 | 0x00 V |
| Vehicle speed | km/h | 0 | VSS | 0 | 355 | 0x02 V |
| ECT | °C | 80-96 | same | ambient when cold | 355, 461 | 0x10 V |
| IAT | °C | ~ambient+ | | ambient | 356 | 0x11 V |
| MAP | kPa | 24-31 | to ~BARO | ≈BARO | 355 | 0x12 V |
| BARO | kPa | ≈atmospheric | | ≈MAP | 368 | 0x13 V |
| TP | % | ~10 % (raw) | 0-100 | ~10 % | 356 | 0x14 V |
| Primary O2 | V | 0.1-0.9 switching | 0.0-1.25 | start-up value | 355 | 0x15 V |
| Secondary O2 | V | slow, mid-rich | | | 361 | 0xA0 U |
| Battery / supply voltage | V | 13.5-14.5 | | 12.x | (–) | 0x17 U |
| ALT FR | %/V | rises with load | | | 363 | 0x18 U |
| ELD | V or A | parking lights 2.5-3.5 V, low beams 1.5-2.5 V | | | 361 | 0x19 U (+0x1C copy) |
| Fuel tank pressure (PTANK) | V / kPa | ~2.5 V ± small | changes with fuel temperature and slosh | ~2.5 V | 361, 543 | – |
| Knock sensor level / noise | counts | low | rises with rpm | 0 | 363 | 0x3C S (rpm-stepped; may be this) |
| Knock retard | ° | 0 | >0 on knock | 0 | 373 | – |
| CKF crank speed fluctuation / misfire counters ×4 | counts | ~0 | ~0 | 0 | 435-436 | – |
| Crank, TDC and CYP sync / fault status | flags | sync OK | | not synced | 370 | – |
| CLV (calculated load) | % | 15-35 | 12-30 at 2,500 rpm no load | | 356 | 0x9C U |

### 6.2 Actuator commands and outputs

| Quantity | Unit / range | Warm idle | Driving | Key-on, engine off | Page | Mapped |
|---|---|---|---|---|---|---|
| Injector pulse width | ms | ~2.4 | 0 in decel cut | 0 (cranking 9-11) | 373-374 | 0x24 U |
| Ignition advance | ° BTDC | 12 ± 2 | map value minus knock retard | | 356, 373 | 0x26 U |
| IAC duty, output | % | depends on load | | | 459 | 0x28 U |
| IAC command, before the output curve | counts | | | | 459 | 0x29 U |
| Purge solenoid duty | % | 0 below 68 °C; duty above, with VSS > 0 | duty | 0 | 374, 536 | 0x2F U (in doubt) |
| Primary O2 heater duty | % | duty | may cut at high load | off | 363 | – (0x0B b6 U may be one of the two) |
| Secondary O2 heater duty | % | duty | | off | 361 | – |
| Fuel pump relay output | bit | on | on | on for 2 s, then off | 373 | 0x0B b0 V |
| A/C clutch output | bit | follows request after a delay | possible WOT cut | off | 374 | 0x0B b1 V |
| MIL output | bit | off | | on for 2 s (bulb check) | 374 | 0x0B b5 V |
| VTEC solenoid output | bit | 0 | 1 above the switch point | 0 | 362 | 0x0A b2 U |
| Radiator fan output (FANC) | bit | per ECM logic | | off | 361, 1597 | – |
| Alternator control output (ALTC) | bit | high charge | low charge when the load is small | | 363 | – |
| EVAP bypass solenoid output | bit | normally off | leak test | off | 361 | – |
| EVAP vent shut valve output | bit | normally open | leak test | open | 361 | – |

### 6.3 Switch inputs

| Quantity | Warm idle | Driving | Key-on, engine off | Page | Mapped |
|---|---|---|---|---|---|
| A/C request (ACS) | per switch | | | 361 | 0x08 b1 V |
| Starter (STS) | 0 | 0 | 1 while cranking | 361 | 0x08 b0 V |
| PSP switch | 1 at full lock | | | 361 | 0x08 b2 V (inverted) |
| Brake (BKSW) | per pedal | | | 361 | 0x08 b3 V |
| VTEC pressure switch (VTM) | 0 | 1 when VTEC is on | 0 | 363 | 0x0C b3 U |
| SCS service connector | 0 normally | | | 361 | – |

### 6.4 Internal states, flags and learned values

| Quantity | Behaviour | Page | Mapped |
|---|---|---|---|
| Closed / open loop | closed at warm idle; open at WOT, in decel cut, when cold and right after start | 356 | 0x0F b0 V; status 0x9A U |
| STFT | ±20 %; in closed loop | 356 | 0x20 V |
| LTFT | ±20 %; learned and kept on VBU | 356 | 0x22 V |
| Idle target (rpm) | 670 / 750 / 810, plus an ECT term | 489, 460 | 0x76 U |
| Learned idle air | slow; frozen off idle; cleared by a reset | 489 (reset after adjustment) | 0x2A U |
| Fast-idle ECT term | follows the ECT curve | 460 | – (inside 0x76) |
| After-start idle-up and its timer | +150-300 rpm for a set time after start | 460 | – |
| Idle-up request flags (ELD / A/C / PSP / brake / purge-810) | | 373, 489, 491 | – |
| A/C engage delay timer | runs on A/C request | 374 | – |
| Crank / after-start enrichment and decay counter | high when cranking | 373 | – |
| Engine-running flag, fuel pump prime timer | | 373 | – |
| Decel fuel-cut flag | set when the throttle is closed above 920 rpm | 374 | – |
| Rev-limit fuel-cut flag | set at 7,000 rpm | 374 | – |
| VTEC request / enable window | rpm, load and ECT gates (not documented) | 114 | – |
| Neutral / gear estimate (rpm ÷ VSS) | the ECM has no switch for this | 361 | – |
| Purge enable latch (ECT > 68, IAT > 0, VSS > 0) | | 374, 536 | – |
| EVAP leak-test state | | 552-560 | – |
| Radiator fan request (ECM side) | | 1597 | – |
| Alternator mode (high / low charge) | | 363, 374 | – |
| O2 heater control state / heater fault | | 363, 426 | – |
| Knock control state / learned octane | | 373 | – |
| rpm × MAP or rpm-band index (table axis pointer) | | 373 | 0x27 S |
| DTC status, pending (first-trip) flags, two-trip and driving-cycle counters | | 374 | 0x0B b5 shows the MIL only; the rest – |
| Readiness bits per monitor (catalyst, O2, O2 heater, EVAP, misfire, fuel, comprehensive components) | | 414, 522 | – |
| Catalyst monitor accumulators | | 522 | – |
| O2 feedback-period timer and switch counter | | 414 | – |
| Freeze-frame copy (11 values) | | 355-356 | – |
| Time since start / warm-up timers | | (–) | – |
| Fail-safe substitute flags per sensor | | 374 | – |

### Still unaccounted for (no RAM address yet)

1. **Outputs:**
   - radiator fan FANC
   - alternator control ALTC
   - EVAP bypass solenoid
   - EVAP vent shut valve
   - secondary O2 heater, and confirmation of which heater 0x0B b6 is
   - purge duty (0x2F is still in doubt)
2. **Inputs:**
   - SCS service-check bit
   - fuel tank pressure (PTANK)
   - knock retard and the true knock-sensor level (0x3C suspect)
   - CKF misfire counters
   - crank, TDC and CYP sync and fault flags
3. **Idle internals:**
   - after-start idle-up and its timer
   - fast-idle ECT term
   - **separate idle-up flags, including the purge-810 / leak-test state**
   - A/C engage delay timer
4. **Fuel internals:**
   - decel-cut flag and rev-limit flag
   - crank and after-start enrichment
   - purge enable latch
5. **VTEC:** request and enable window.
6. **Neutral or gear estimate.**
7. **Diagnostics:**
   - DTC table and pending flags
   - readiness bits
   - two-trip and driving-cycle counters
   - catalyst and O2 monitor accumulators
   - EVAP leak-test state
   - freeze-frame copy
   - fail-safe flags
8. **Raw bytes with no candidate yet:** 0x03-0x07, 0x0D, 0x0E, 0x16, 0x1A, 0x1B, 0x1D-0x1F, 0x2B-0x2E, 0x30-0x3B.
   - The quiet ones (zero at warm idle) are good candidates for the fault, readiness and cut flags above. Look at
     them during decel cut, at the rev limit, cold start, fan-on and an 810 episode.
   - 0x0D bit 3 cannot be an engine-mount solenoid on this pinout.
