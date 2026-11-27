# 2026-11-27: the steady unknown bytes (0x32-0x3A, 0x8F, 0x80-0x8C, 0x94-0x99)

Car: 2000 Civic EX, D16Y8, M/T, stock P2P (OBD2B, EVAP monitor supported). Desk work only:
no serial port, no app, no map or code changes.

Inputs:

- A copy of `hondalog.db` (last written 11-26 23:33) in `work-analog/` (git-ignored), opened `mode=ro`. The
  database has no `ecu_reads` table: the bytes the ECU sent are in `samples.raw`, one row per
  block read, and the `streams` table names the block (`ecu/27+14` and so on). I took every byte
  from those raw replies.
- `../2026-11-26/work-sweep/car-214932-wide.csv` (210 sweeps of 0x00-0xFF, 21:49-21:53) for
  0x80-0x9F.
- The service manual PDF, text pulled with pypdf in the work venv. Page references below are
  **PDF page / printed page**. The extracted text is not kept in the repo; `manual_extract.py`
  rebuilds it.
- The scripts are in `work-analog/`: `load.py` (raw-byte loader), `frame.py` (each read with the
  nearest value of every other channel), `flick.py` and `flick2.py` (flicker statistics),
  `start39.py` (the 22:51 start), `purge.py`, `idletgt.py` and `idletr.py`, `ectscan.py` and
  `tpsscan.py` (bit-flag threshold scans), `plots.py`.

## Which addresses were really read

The brief says 0x33, 0x35, 0x36 and 0x38-0x3A were dropped from 22:00. The read plans show they
mostly kept coming along inside wider blocks:

| drive | blocks | 0x32 | 0x33 | 0x35 | 0x36 | 0x37 | 0x38-0x3A |
|---|---|---|---|---|---|---|---|
| 2045 (id 33) | 27+14, 35+8 | 1,634 | 1,634 | 1,626 | 1,626 | 1,626 | 1,626 |
| 2114, 2137, 2153 (34-36) | 27+16 / 27+14, 37+6 / 35+8 / 36+7 | 2,198 | 2,198 | 2,194 | 2,194 | 2,188 | 2,188 |
| 2158, 2224, 2251 (37-39) | 27+14, 37+6 | 4,669 | 4,669 | **none** | **none** | 4,664 | 4,664 |

Only 0x35 and 0x36 stop after 21:58. 0x33 rides in `27+14` and 0x38-0x3A in `37+6` to the end
(23:27). In total there are about 8,500 reads per byte, including about 2,500 while driving
(drives 37-39, up to 120 km/h and 3,900 rpm).

Values over the whole evening:

| byte | values (reads) |
|---|---|
| 0x32 | 45 ×8,146, 44 ×355 (4.2 %) |
| 0x37 | 45 ×7,915, 44 ×563 (6.6 %) |
| 0x33 | 55 always |
| 0x35 | 45 always |
| 0x36 | 7 always |
| 0x38, 0x39 | 25 always |
| 0x3A | 25, except 24 for the 8 reads after the 22:51:09 start |
| 0x34, 0x3B | 0 always |

0x32 and 0x37 never read 43 or 46.

## 1. 0x32 and 0x37: what the flicker tracks

Plots: `work-analog/01_flicker_vs_channels.png`, `02_timeline.png`.

**Two independent processes.** On 7,816 read pairs less than 0.3 s apart, both bytes read 44
together 20 times, against 21.0 expected by chance. The dips are single reads with no memory:

- 0x32: 319 runs of 1 read and 18 runs of 2;
- 0x37: 471 runs of 1, 41 of 2, 2 of 3 and 1 of 4.

Those counts are close to what independent draws with p = 0.04 / 0.07 give.

**Battery: not proportional.** In the logs, normal running only covers 13.9-14.4 V (raw 145-150),
and the dip rate is flat across it. The useful reads are the few at low voltage:

- The **first read of drive 2251 was taken while cranking** (185 rpm, battery 10.6 V). It gave
  0x32 = 45, 0x37 = 45.
- 17 reads at 12.7-13.7 V gave 45, except one 44 in each byte.

An input proportional to battery voltage would have read about 34 at 10.6 V and about 41 at 13 V.
So it is either ratiometric to the ECU's 5 V reference (its own supply, or a divider of it), or
it is not an A/D value at all. Caveat: there was only one cranking read.

**Temperatures, baro, time: nothing.**

- IAT (raw 81-163, about 19-53 °C) has no effect on either byte.
- Baro sat at raw 147 all evening, so it cannot be tested, but the bytes also ignore MAP.
- ECT covers raw 34-189 through the false-cold glitches. 0x32 ignores it.
- 0x37's dip rate rises with ECT raw (3 % → 9-13 %). This is the rpm effect below in another
  form: false-cold readings lift the idle to about 1,000-1,200 rpm, and hot readings
  (raw ≤ 45) only happen while driving.
- No drift over 2 h 40 min, 7 drives and one engine restart: the 5-minute dip rates stay
  within 3.4-4.7 % (0x32) and 4.4-7.7 % (0x37), and the resting value is 45 from start to end.
- The database has no fuel-level channel. Over the evening the tank warmed and emptied a
  little, purge ran up to 255, and the car drove at highway speed. None of this moved either
  byte by even one count.

**Engine state: only 0x37 responds, and only weakly.**

| rpm | 600-800 | 800-1000 | 1000-1500 | 1500-2200 | 2200-4000 |
|---|---|---|---|---|---|
| 0x37 at 44, MAP raw < 45 | 5.4 % | 7.8 % | 10.7 % | 7.4 % | 3.0 % |
| 0x37 at 44, MAP raw 60-90 | – | – | 8.6 % | 3.3 % | 1.8 % |
| 0x32 at 44, any MAP | 3.8-6.5 % | 3.8-6.1 % | 3.3-4.3 % | 4.5-8.1 % | 2.3-5.5 % |

- 0x37 dips most at about 1,100 rpm with a light load. It almost never dips above 2,500 rpm
  (1 of 313 reads) or above 90 km/h (0 of 124).
- 0x32 hardly changes. Its only clear feature is 8 % at 1,500-2,200 rpm with a closed throttle.
- Neither byte ever *rises* to 46.

**How to read this.** For an 8-bit A/D sample of a fixed voltage, the numbers fit a true value
just above the 44/45 code edge: about 1.5 σ above it, with noise σ below 0.2 LSB (under 4 mV).
That noise level is why 46 never appears. The engine-state effect on 0x37 would then be a shift
much smaller than 1 LSB, such as a few mV of ground offset, or the sampling moment landing
differently against injector and coil events. That suggests 0x37 is sampled in a
crank-synchronous routine and 0x32 in a time-based one.

The software reading is still open: a value that is decremented and then reloaded from 45,
with 0x35 = 45 next door as the reload constant. Note that the flicker is always bit 0, and
0x3A's start-up step (25 → 24) is also bit 0. Nothing in these logs separates the two readings.

### Which input, if it is an A/D channel

From the '99-00 ECM terminal table (PDF p.361-363 / 11-92 to 11-94), the analog inputs on this
ECM are:

- already mapped: TPS, ECT, IAT, MAP, PHO2S, SHO2S, ELD, ALTF, battery (IGP), and the internal
  baro sensor;
- not mapped: PTANK (A29), KS (C3) and EGRL (C6).

Each candidate checked against the data:

| candidate | test | verdict |
|---|---|---|
| **EVAP fuel tank pressure (FTP), A29** | Manual: "about 2.5 V" with the fuel fill cap open (p.361 / 11-92). In the P0451 check (p.543 / 11-274), the voltage falls from about 2.5 V to about 1.5 V under pump vacuum, and you must stop there to avoid damaging the sensor. The FTP is fitted on '99 and '00 models, all of them (p.380). 0.88 V would mean a tank vacuum beyond the sensor's safe test range. A real FTP also moves with slosh, heat and purge while driving. These bytes did not move by even one count in 40 minutes of driving with purge at up to 255. | **rejected** (unless the byte is stored in an unknown offset form, and even then the lack of any movement argues against it) |
| an unused input pin held at a fixed voltage (EGRL C6, which the tables tie to the D16Y5's EGR; LABEL, which the '96-98 table lists for the primary HO2S label resistor) | Unconnected inputs with identical bias resistors inside the ECM would read the **same** fixed voltage, give independent samples, and be ratiometric. That explains two equal values, the 45 at 10.6 V cranking, and the lack of any physical response. The EGR lift sensor reads about 1.2 V at rest on cars that have one (p.360 / 11-91). | **best fit** |
| an ECU internal reference (a divider of VCC, or a check channel) | Fits the ratiometric behaviour. Only one such channel would be expected, not two. | possible |
| sensor ground (SG1 or SG2) read back | Spec is "less than 1.0 V at all times" (p.360). A healthy ground reads about 0 V, not 0.88 V, and would follow load currents. | unlikely |
| second MAP or baro channel | The bytes do not follow MAP (raw 32-123), not even during WOT and decel. Baro is 0x13. | rejected |
| knock circuit | The knock sensor is fitted ('99-00 D16Y8, p.440). Its byte is 0x3C (0-11, rises with rpm and load while driving). | rejected for 0x32/0x37 |
| IACV feedback | The rotary IACV has no position feedback, and the bytes ignore 0x28/0x29 (0 → 56). | rejected |
| ECU temperature sensor | No drift over 2.7 h while the cabin warmed up. | unlikely |

## 2. The constants: arithmetic and grouping

The ECT/IAT formula is the map's `poly(x, 155.04149, -3.0414878, 0.03952185, -0.00029383913,
1.0792568e-6, -1.5618437e-9)` (from `addressmap.toml`).

| byte | value | ECT poly | x/51 V | x×50 rpm | MAP kPa | other |
|---|---|---|---|---|---|---|
| 0x33 | 55 | **67.5 °C** | 1.08 | 2,750 | 34.4 | – |
| 0x35 | 45 | **75.6 °C** | 0.88 | 2,250 | 27.2 | – |
| 0x36 | 7 | 135.6 °C (beyond the sensor range) | 0.14 | 350 | 0 | a count or index |
| 0x38-0x3A | 25 | **99.5 °C** | 0.49 | 1,250 | 12.9 | – |
| 0x3A start | 24 | 101.1 °C | 0.47 | 1,200 | – | – |
| 0x8F | 142 | 27.4 °C | 2.78 | – | 96.7 | trim (142/128-1) = +10.9 % |

Checks against the logs and the manual:

- **0x33 = 55 vs the EVAP purge enable.** The manual says the ECM runs the purge solenoid when
  coolant is "above 154 °F (68 °C)" (p.374). 68 °C is raw 54.3 on this poly. 0x2F (the purge
  ramp, see `drive-bytes.md`) is non-zero in:
  - 0 % of stationary reads at ECT raw ≥ 58;
  - 10-28 % at raw 55-57;
  - 38-68 % at raw ≤ 54.

  `04_purge_vs_ect.png` shows this. So the purge edge sits at raw 55-57, and 0x33 = 55 is on it.
  The edge is soft because the ECU probably uses a filtered ECT. That fits an ECT threshold for
  purge, with or without a small hysteresis. **Medium-low.**
- **0x35 = 45 (75.6 °C).** That is near the thermostat's start-to-open of 76-80 °C (p.266) and
  above the 71 °C (raw 50) of the OBD2 warm-up definition. I tested it against the idle target:
  749.75 rpm whenever ECT raw ≤ 45, 809.75 rpm above. But every raw ≤ 45 read was taken while
  moving, and 749.75 is the moving target at any ECT (351 of 370 reads at raw 46-50 while
  moving). So **that link is a confound, not evidence.** No bit in any logged byte switches at
  raw 45/46 (`ectscan.py`). Low.
- **0x38 = 0x39 = 0x3A = 25 (99.5 °C).** The radiator fan switch closes at 199 °F (p.1597
  wiring; p.1961 text). The ECM also has its own fan output (FANC, A20). A 100 °C value could be
  an ECM fan, A/C cut or hot-restart threshold. ECT never got near it tonight (warmest raw 34 =
  87 °C), so nothing could be tested. TPS closed throttle is raw 20-21, so 25 is not a learned
  TPS idle either. No bit switches at TPS raw 25 (`tpsscan.py`). Low.
- **0x36 = 7.** As a raw ECT value, 7 is 0.14 V (135 °C), roughly the level a short-to-ground
  check would use. As rpm ×50 it is 350 rpm, near a typical "engine running" detection speed.
  Neither can be tested. None.
- **Grouping.** On the ECT scale, 0x33 / 0x35 / 0x38-0x3A form a plausible monotone set of
  coolant thresholds (67.5 / 75.6 / 99.5 °C: purge enable, warm or thermostat-open, hot). Only
  the first has support in the data. The three equal 25s look like one constant used by three
  functions, or the three points of a small table.

### 0x3A: 25 → 24 → 25 at the start (`03_start_2251.png`)

Only one logged start includes this block: 22:51:09, ECT raw 52 (70 °C), so a warm restart.

| second after the start | 0x3A | rpm | front O2 (0x15) | 0x9A |
|---|---|---|---|---|
| 1 (cranking, 10.6 V) | 25 | 185 | 200 (3.9 V, rail) | 1 |
| 2-9 (8 reads) | **24** | 1,276 → 820 | 200 | 1 |
| 9.x | 24 | 820 | **178, starts falling** | 1 |
| 10 | **25** | 819 | 135 | 1 |
| 16 | 25 | 820 | 0.5 V region | **2** (closed loop) |

- It is **not a warm-up flag tied to ECT.** ECT was a steady raw 52 the whole time.
- It is **not a timer:** it steps by one count and back, and does not count.
- The return to 25 came **one read after the front O2 left its 3.9 V bias rail**, and about 6 s
  before closed loop. One start is not enough to tell "O2 sensor active" from a fixed 8 s
  after-start window.
- Bit view: 25 = `0001 1001` and 24 = `0001 1000`, so bit 0 was clear for 8 s. The 0x32/0x37
  dips are also bit 0. This would fit a status byte whose bit 0 means "O2 active" or
  "after-start over", with the other bits fixed.

**Best guess: a post-start state.** Confidence low.

## 3. 0x80-0x8C and 0x94-0x99 against PID 01

PID 01 = `00 07 65 25`:

- MIL off, 0 DTCs;
- misfire, fuel and CCM supported and complete;
- catalyst, EVAP, O2 sensor and O2 heater supported;
- catalyst, EVAP and O2 sensor **not complete**; heater complete.

PID 00 = `BE 3E B8 10`, so PID 41 (this-drive-cycle status) is not supported. PID 1C = 01.
LTFT re-learned from +2.3 % on 11-25 17:56 (`2026-11-25/report-fueling.md`), so the ECU was
probably reset shortly before. That explains three incomplete monitors on a car that has driven.

Sweep values (210 sweeps):

`80-8F: F8 00 F9 00 F0 00 FD|F9 F8 FC F0 00 F4 F8 00 00 8E`, `94-99: 02 0F 04 00 00 05`.

Tests:

| hypothesis | arithmetic | result |
|---|---|---|
| PID 01 copied byte for byte | `00 07 65 25` not present anywhere in 0x80-0xFF | rejected (as on 11-26) |
| complements or bit-reversals | ~B = 0xF8 (= 0x80, 0x87, 0x8C); ~C = 0x9A; ~D = 0xDA; rev(D) = 0xA4; rev(C) = 0xA6 | ~B = F8 appears 3 times, which is probably coincidence. Nothing else matches |
| **0x94 and 0x99 = the two nibbles of D** | D = 0x25 → high nibble 2 = 0x94, low nibble 5 = 0x99 | exact, but the same split of C (6, 5) and B (0, 7) is absent (0x95 = 15, 0x96 = 4). A one-in-a-few coincidence. Low |
| packed Honda order (cat / evap / O2 / heater as 4 bits) | supported = 0x0F = **0x95**. A complete mask would have 1 bit (the heater) and an incomplete mask 3 bits. 0x94 = 2 and 0x96 = 4 each have 1 bit, 0x99 = 5 has 2 bits, and **no byte has 3 of the low 4 bits set** | 0x95 = supported mask, and 0x94 = complete mask (heater at bit 1), are consistent. The incomplete mask would be computed, not stored. Low-medium |
| per-monitor status bytes 0x80-0x8C (13 bytes, high nibble all 1s) | low nibbles 8 0 9 0 0 0 D 8 C 0 0 4 8. **Bit 2 is set in exactly three bytes (0x86, 0x88, 0x8B), and three monitors are incomplete.** 0x86 is also the one byte that moves: bit 2 drops (FD → F9) with a rich front O2 and STFT ≥ 134. That fits 0x86 = the O2-sensor monitor, with bit 2 = "not yet complete / conditions pending". Bit 3 is set in 6 bytes (7 monitors are supported); bit 0 in 2 | a lead, not proof: the count could be chance. Low |
| counters since the reset (key cycles, warm-ups, trips) | 2, 4, 5 are small, which fits an ECU reset on about 11-25. 15 could be a 4-bit counter at its limit | still open. A counter is the one hypothesis that **must** have moved after the 11-26 drives |
| 0x8F = second LTFT cell | 142 = +10.9 %. LTFT 0x22 was 138-145 all evening (139 during the sweep) | plausible. 27.4 °C on the ECT scale matches no start-up IAT or ECT tonight (IAT at the 20:31 start was 149) |

## Per-byte verdicts

| byte | verdict | confidence | cheapest confirming observation |
|---|---|---|---|
| 0x32 | live 8-bit sample of a fixed, ratiometric ~0.88 V input; most likely an unused ECM input pin with an internal bias (or an internal reference); time-based sampling. Not the EVAP FTP | medium that it is a fixed ratiometric analog, low on which pin | key ON, engine off: multimeter between ECM C6 (EGRL) and C18 (SG2), and A29 (PTANK) and C18. About 0.88 V at C6 and about 2.5 V at A29 settles it. App-only alternative: 0x32 at key-on, engine off, still 45 |
| 0x37 | same kind as 0x32 (a second unused input with the same bias, or the same pin sampled crank-synchronously); its flicker falls with rpm and load | as 0x32 | same meter check, and the dip rate should go to about 0 (or stay constant) with the engine off |
| 0x33 = 55 | ECT threshold 67.5 °C, most likely the EVAP purge enable (manual: 68 °C) | medium-low | a cold start logged with 0x2F: purge (0x2F > 0) must never start while ECT raw > 55, and should start soon after raw ≤ 55 |
| 0x35 = 45 | ECT threshold 75.6 °C (warm-up or thermostat-open level); stored constant, never flickers | low | the same cold-start log: look for any bit (0x80-0x8C, 0x0B/0x0C, 0x94-0x99) that changes as ECT raw falls through 45 |
| 0x36 = 7 | calibration constant (count, index, or a 0.14 V ECT short-circuit limit) | none | needs the ROM; nothing cheap |
| 0x38 = 0x39 = 25 | constant; on the ECT scale 99.5 °C (ECM fan or A/C-cut level?) | low | a long hot idle until the fan runs: if ECM FANC or A/C cut switches when ECT raw ≈ 25, that confirms it. The bytes themselves stay 25 |
| 0x3A = 25 (24 after start) | the same constant, but bit 0 is cleared for the first ~8 s after a start and returns as the front O2 comes off its bias rail: a post-start / O2-active status bit | low | one **cold** start logged from key-on: if the 24 period lasts as long as the O2 stays at 200 (tens of seconds cold), it is O2-active; if it is 8 s again, it is a fixed after-start timer |
| 0x8F = 142 | second long-term fuel-trim cell (+10.9 %) | low | read 0x80-0x9F after 20 min of steady cruising: a trim cell moves slowly (minutes) and differently from 0x22 |
| 0x94 / 0x95 / 0x96 / 0x99 = 2 / 15 / 4 / 5 | 0x95 = supported non-continuous monitors (packed 0x0F); 0x94 = complete mask (heater); 0x96 and 0x99 = other monitor bookkeeping or counters since the reset | low (0x95 low-medium) | **one read of 0x80-0x9F plus PID 01 at the next key-on.** After the 11-26 highway driving, a counter has stepped, and a monitor that completed shows as a PID 01 D bit clearing together with a bit in 0x94/0x96/0x99 |
| 0x80-0x8C | per-monitor status or condition bit fields, high nibble unused. Lead: bit 2 = "incomplete" (three bytes have it, three monitors are incomplete), and 0x86 = O2 monitor | low | the same read: when PID 01 D drops a bit (for example O2: 0x25 → 0x05), exactly one of 0x86/0x88/0x8B should lose bit 2 |

## Caveats

- There is only one engine start with these blocks (22:51, warm) and only one cranking read. No
  key-on, engine-off reads exist for 0x32-0x3A.
- Battery voltage only spans 13.9-14.4 V while running, so the ratiometric conclusion rests on
  18 low-voltage reads, one of them while cranking.
- The ECU's internal ECT is filtered. The logged raw ECT with its false-cold glitches is not
  what the ECU compares against its thresholds, so every ECT-threshold result is soft.
- The 1 Hz reads would hide anything faster than one second in 0x33-0x3A.
