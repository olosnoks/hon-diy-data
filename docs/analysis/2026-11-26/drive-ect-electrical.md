# First driving data: the ECT fault, cooling and electrical while driving (2026-11-26)

**Data.** I worked from a read-only copy of `hondalog.db` taken at 23:33 (`work-ect/`, git-ignored). There was no -wal/-shm file to copy, so the copy is the whole database. Drives:

- `drive_2026_11_26_2224`: 22:24:38-22:45:24. About 15 min of idle, then 5.5 min of driving up to 69 km/h.
- `drive_2026_11_26_2251`: 22:51:09-23:26:58. 36 min, 90 % of it moving, including 8.5 min at 85-107 km/h on the interstate.
- For comparison: the idle-only drives of 11-25 (4-30) and 11-26 (31-37).

ECT (0x10), IAT, the flag byte 0x08, batt, ALT FR and ELD all come from the slow blocks, read once a second. rpm, vss, MAP, TPS, inj, ign and the trims come from the fast blocks at 12.5 Hz. GPS speed agrees with vss to within 1-1.5 %.

Scripts are in `work-ect/`:

- `lib.py`: loader
- `stats.py`: per-drive glitch rates
- `events.py`, `corr.py`: ECT steps against the driving context
- `fan.py`: fan cycles detected from ELD
- `fueling.py`: effect on fuel, ignition and idle
- `elec.py`, `nearstall.py`: electrical
- `iat.py`
- `rmodel.py`: contact-resistance model
- `plot_overview.py`, `plots2.py`: plots

The venv is `work-ect/venv`.

## Bottom line

1. **Driving did not make the ECT fault worse. There is no sign that it is vibration-driven.**
   - In 56 min of logged driving (about 160 gear changes, 10 launches, about 50 brake applications, 8.5 min at 90-107 km/h, revs up to 5,400 rpm) there was **not one false-cold jump** (a step of 15 °C or more, the 35 °C-type reading).
   - The last one was at 22:07, 33 min before the car first moved. From 22:08 to 23:27 the circuit stayed in its other failure mode: a **steady low reading** of 65-74 °C. It made only 10 smaller steps of 4-11 °C.
   - Those 10 steps do not line up with gear changes, braking, clutch-in, rough road (vss jitter) or engine load more than chance would allow. The interstate stretch had none.
   - The 11-26 idle drives ran 33 big jumps/h. At that rate the 79 quiet minutes would have held about 40. So the fault changes state over minutes on its own, at idle as well as on the road. It behaves like a **resistive, oxidised or contaminated terminal** whose contact resistance drifts, not like a wire that breaks under road shock.
2. **The bigger problem tonight is the steady offset, not the glitches.**
   - The radiator fan, which runs on its own thermoswitch, cycled every ~3 min at idle. The ECT read **69-75 °C at each fan-on** (median 70.5 °C over 25 cycles on 11-26). In drive 2137, when the contact was briefly good, the same fan came on at a reading of **84-86 °C**, and `channel-verification.md` found 84-87 °C over 53 earlier cycles.
   - So tonight's readings are about **13-15 °C below the real coolant temperature**. The divider model puts that at roughly 0.6-1× the sensor's own warm resistance added in series, about 100-250 Ω.
   - The real coolant was very probably about 84-88 °C at idle and about 78-85 °C at highway speed. The reading stayed flat for the whole 8.5 min at 90-107 km/h in ~19 °C air, and the fan never ran while moving. That is a **thermostat that regulates**: no sign of a stuck-open thermostat and no sign of overheating.
3. **Effect on driving.**
   - **All 10 launches happened with the low steady reading (62-72 °C). None happened during a false-cold reading.**
   - The ECU believes the engine is at 62-72 °C, so the idle target was **810-1,014 rpm instead of ~670**. It tracked every step: 750 at a reading of 77 °C, 864 at 65 °C, 960-1,014 at 57-62 °C.
   - After the 22:41:07 drop the car "hung" at 1,080-1,120 rpm while coasting to a stop.
   - The effect on fuelling and ignition while driving is small: about +1-3 % fuel and about −1° timing at the colder reading, within noise.
   - **None of the six near-stall dips had an ECT step anywhere near it.** The ECT is not what pulls the rpm down at pull-away. In fact the raised target gives those launches a higher starting rpm.
4. **The electrical system is healthy.**
   - Battery voltage holds **13.9-14.3 V from 750 to 5,400 rpm** with ~25-30 A of load (headlights on), inside the 13.8-14.6 V a healthy Honda charging system gives. It did not sag at highway load and had one 14.64 V single-read high.
   - ALT FR falls with rpm as it should: 57 % at 750-1,000 rpm, 30 % above 3,000 rpm.
   - **The voltage dips at the near-stalls (12.73-13.11 V) are an effect, not a cause.** In every case the read 0.3-1.1 s before the rpm minimum was a normal 13.97-14.35 V, and ELD did not change. The dip shows in the first read after the minimum, 0.1-0.4 s later, with ALT FR jumping to 83-90 %. That is the alternator running out of speed below ~560 rpm while the headlights draw about 14 A. The voltage is back to 14 V within a second.
5. **IAT** heat-soaks to 50-53 °C at idle and falls to ambient (19-22 °C) after about 15 min of driving. It has no effect worth naming: one idle-target step at most, ≤1° timing and ≤2 % fuel in matched bins, all inside the noise.
6. **New for the stall hunt.** The power-steering pressure switch (0x08 bit 2) is a usable **steering proxy**. It cleared in 18 reads, all below 21 km/h (parking and tight turns). It was **active through the 23:25:55 dip and 1.8 s before the 23:26:12 dip** (both in the home driveway). So steering load is a second idle load present at two of the six pull-away dips.

## 1. ECT while driving

### 1.1 Glitch statistics

The definitions are the ones `stall-forensics.md` used. A **false-cold jump** is a step of 15 °C or more between consecutive reads at most 3 s apart. A **step** is 4 °C or more. Only reads with rpm above 400 count.

| period | running h | jumps ≥15 °C | per h | steps ≥4 °C | per h | % of reads false-cold (<55 °C) | median reading |
|---|---|---|---|---|---|---|---|
| 11-25, all idle drives | 3.85 | 68 | 17.7 | | | | 80-87 °C |
| 11-26 idle drives 2031-2158 (20:31-22:24) | 1.77 | 48 | 27 | 140 | 79 | 0.5-22 % per drive | 67-72 °C (2137: 82.5) |
| **11-26 drive 2224** (22:24-22:45) | 0.33 | **0** | **0** | 5 | 15 | 0 | 71.3 °C |
| **11-26 drive 2251** (22:51-23:27) | 0.59 | **0** | **0** | 5 | 8.4 | 0 | 67.5 °C |

Driving logs only, by speed:

| vss | hours | steps ≥4 °C | per h | raw changes ≥2 counts per h |
|---|---|---|---|---|
| stopped | 0.32 | 1 | 3 | 16 |
| 1-30 km/h | 0.19 | 3 | 16 | 75 |
| 30-60 km/h | 0.24 | 6 | 25 | 55 |
| 60-85 km/h | 0.10 | 0 | 0 | 59 |
| 85-107 km/h | 0.04 | 0 | 0 | 0 |

By rpm (≥4 °C steps per h): below 1,000 rpm 6; 1,000-1,500 21; 1,500-2,000 17; 2,000-2,500 7; above 2,500 11. By MAP: below 30 kPa 7; 30-50 kPa 16; 50-70 kPa 23; above 70 kPa 20. With only 10 events these differences are not significant: one event more or less moves a bin by 5-25/h.

**When the steps happened.** Five steps came within 2.5 min, 22:41:07-22:43:25: the first minutes the car ever moved, while the engine bay was cooling from 51 °C. There were 0 in the 15 min of idle before. Drive 2251 then had 5 steps in 31 min: 23:04:41, 23:06:38-39, 23:10:02 and 23:12:21.

### 1.2 Event coincidence (moving reads only, steps ≥4 °C, n = 9)

| condition | share of moving time | steps under the condition | rate with / without |
|---|---|---|---|
| brake bit set | 16 % | 2 | 23 / 15 per h |
| gear change within ±3 s (vss/rpm ratio: gears at 7.2, 13.0, 20.0, 27.6, 36.0 km/h per 1,000 rpm) | 38 % | 3 | 14 / 17 per h |
| clutch in or neutral while rolling (no gear ratio matches) | 36 % | 4 | 20 / 14 per h |
| launch within ±5 s | 2 % | 1 | 1 event |
| hard decel (< −3 km/h/s) | 9 % | 1 | 20 / 16 per h |
| hard accel (> 3 km/h/s) | 7 % | 1 | 23 / 16 per h |
| top 10 % vss jitter (bump proxy) | 10 % | 2 | 39 / 14 per h |
| top 10 % rpm jitter | 11 % | 1 | 18 / 16 per h |

None of these departs from chance at n = 9: the two bump-proxy hits are what you would expect 1-2 times out of 9. Two more checks point the same way:

- The steps do not repeat at the same place. Steps that are not part of one episode are 400-700 m apart.
- The 18 power-steering-switch reads (tight low-speed turns) sit next to one step, 4 s before 22:43:00.

Vss is whole km/h, so road bumps are only weakly visible in it. Steering angle is not logged; the PSP switch is the only steering signal there is.

**Verdict: not vibration-related on the evidence available.**

- The big false-cold jumps happened at stationary idle, 11-25 and 11-26, and stopped 33 min before the drive.
- An hour of real road shock (city stop-and-go, interstate, two hard launches to 5,400 rpm) did not bring them back.
- The small steps show no tie to shifts, braking, bumps or load.

The fault is better described as a contact resistance that drifts between levels over minutes. The levels seen are about 0, about +50 Ω, about +150-250 Ω and about +1.5 kΩ (false-cold), the last three relative to a true reading. That is the typical behaviour of an oxidised, fretted, oil-contaminated or spread terminal at the 2-pin sensor or its connector. A **fretting** contact can still respond to a deliberate wiggle even when road vibration does not trigger it, because a wiggle moves the terminal much further than the engine-mounted harness moves on the road. So the wiggle test below is still the right first test, and the static voltage-drop test catches the offset fault even when the reading is steady.

### 1.3 Does the "true" ECT behave like a healthy thermostat?

- **Fan evidence (offset).** `fan.py` found 25 ELD steps on 11-26 of +5.5 to +10.6 A, lasting 16-22 s. Each was followed by a 3-6 °C drop in ECT, which identifies them as the radiator fan. The reading at fan-on was 69-75 °C in drives 2158 and 2224, against 83.6 and 85.9 °C in drive 2137 when the contact was good. The fan thermoswitch closes at a fixed coolant temperature, so **tonight's reading is about 13-15 °C low**.
- **At speed.** The reading was 65-68 °C in city driving and 67-68 °C, flat, through 8.5 min at 90-107 km/h with IAT (≈ ambient) at 19-22 °C. Corrected, that is about 78-85 °C. A stuck-open thermostat would let the coolant drift down steadily at 100 km/h on a cold night; it did not drift.
  - **Fan:** no fan cycle anywhere in the 56 min of driving, stops included. Ram air was enough, so the coolant never reached the fan switch.
  - **23:10:02-23:12:21:** the reading rose to 74-79 °C. That is the contact improving (a 7.7 °C step in one read), not the coolant heating up, because it fell back 11 °C in one read at 23:12:21.
- **Verdict:** cooling system and thermostat look healthy. The temperature gauge is a separate sender, and the owner can cross-check it: it should sit mid-scale.
- Plots: `overview_drives.png`, `overview_evening.png`.

## 2. What the faulty ECT does to driving

**Idle target** (`summary_stats.png`, lower left; `zoom_2240_ect_steps.png`; `zoom_2310_ect_steps.png`). The target follows the accepted reading within one read, at about 13.6 rpm per raw count, as found in `slow-bytes.md`:

| ECT reading | idle target |
|---|---|
| 76-79 °C (23:10-23:12) | 750 |
| 69-74 °C (most idle) | 810-824 |
| 65-68 °C (most of drive 2251) | 850-878 |
| 60-62 °C (22:41-22:43) | 946-1,014 |
| 82-86 °C (the one good-contact stretch, drive 2137) | engine idled at 678 rpm with IACV at 0 |

The ECU acted on every moderate step:

- **22:41:07, the reading fell 67 → 62 °C while cruising at 41 km/h.** The target jumped 810 → 960. The car coasted to the stop at 1,080-1,120 rpm and idled at 960-985.
- **22:43:25, the reading came back 62 → 68 °C at a standstill.** The target fell 987 → 864. rpm followed smoothly, with IACV going 6.7 → 5.1 % and no sag below the target.

So the snap-back that hurt the idle on 11-25 (170-540 rpm sags) did not happen tonight. Its 6 °C size was too small.

**Fuel and ignition.**

- **Idle.** At warm idle with a reading of 70-72 °C, total trim is +15.6 to +17.2 % (rpm 812-828, IACV 5.5-6.3 %). With the good reading (drive 2137) it is +23.4 % (rpm 678, IACV 0). Up to ~6-8 points of that difference could be warm-up enrichment that closed loop trims back out. But the higher airflow at 820 rpm also dilutes the vacuum leak, so the two effects cannot be separated.
- **Moving, closed loop, drive 2251.** In 25 matched rpm/MAP cells (about 60 s of data), the 63-70 °C reading against the 73-79 °C reading gave injector time +3.3 %, STFT +2.0 points (so base fuel only about +1.3 %), and ignition −1.2°. That is small, and the 73-79 °C stretch is only 2.3 min long. No open-loop cells overlapped.
- **Verdict:** while driving, the low reading costs a little fuel and perhaps 1° of timing. It is not a driveability problem in itself. Real false-cold readings (25-40 °C, ~5 % enrichment and fast idle on 11-25) did not occur while driving.

**Launches** (vss 0 for at least 2 s, then 5 km/h or more):

| time | ECT reading | target | rpm minimum (−4 to +3 s) |
|---|---|---|---|
| 22:39:47 | 72.1 | 810 | 589 |
| 22:41:44 | 61.7 | 973 | 791 |
| 22:53:18 | 69.7 | 824 | **462** |
| 22:54:21 | 69.7 | 824 | 669 |
| 22:58:05 | 68.2 | 824 | 701 |
| 23:03:01 | 66.1 | 864 | 906 |
| 23:12:39 | 65.4 | 864 | **553** |
| 23:26:04 | 67.5 | 864 | 833 |
| 23:26:12 | 67.5 | 864 | **495** |
| 23:26:25 | 67.5 | 864 | **439** |

That is 10 launches, **0 during a false-cold reading** and 10 during the low steady reading. The deep dips happened with an ECT that was steady for at least 15 s and a target of 810-864. The only launch from a high target (973, 22:41:44) had the shallowest dip.

**Warning for the repair.** Once the ECT is fixed, the warm idle target drops to ~670 rpm. That is 140-200 rpm less than every pull-away logged so far. The clutch-engagement dips of 250-350 rpm then start from 670 rather than 820-864, and could reach a stall. Fix or rule out the vacuum leak (trims of +16 to +23 % at idle) and check the IAC and idle air at the same time, or plan to launch with a little throttle for the first drives after the repair.

## 3. Electrical

**Charging** (`elec.py`; `summary_stats.png`, lower right):

| drive | batt median | p1-p99 | ALT FR by rpm band |
|---|---|---|---|
| 2158 idle | 14.16 V | 13.97-14.26 V | 38 % |
| 2224 | 14.16 V | 13.88-14.26 V | idle 40 % (before headlights), 49 % at 1,000-1,500, 33 % at 2,000-2,500 |
| 2251 | 14.16 V | 13.88-14.35 V | 57 % at 750-1,000, 43 % at 1,000-1,500, 34 % at 1,500-2,000, 32 % at 2,000-3,000, 30 % above 3,000 |

The regulator holds 13.9-14.3 V across the whole rpm range at ~25-30 A load. The highest read was 14.64 V, once (23:25:53, 1,295 rpm, rpm falling). Below 13.8 V while running, the reads fall into two groups:

- **(a) single reads at steady idle, 12.8-13.8 V, coinciding with an ELD fan-on step** (22:01:17, 22:04:14, 22:17:44, 22:20:54, 22:23:56). That is the fan motor's inrush current, normal.
- **(b) the near-stall dips** (below). There were also a few 13.7-13.8 V reads at full throttle above 4,000 rpm. That is normal.

No sag or spike shows any problem with the battery, cables, alternator or regulator.

**ELD and the headlights.** During the 22:36 cable gap, ELD rose from 9.7 A to 23.9 A (raw 171 → 135, 36 counts, **+14.2 A**) and ALT FR from 39 % to 54 %. Battery voltage barely moved (14.16 → 14.07 V), IAC command went 86 → 92 and IACV 5.1 → 7.5 % to hold 810 rpm. That is almost certainly the headlights, tail and dash lights switched on for the night drive (about 13-14 A expected). It supports the 0.394 A/count ELD scale (`channel-verification.md`).

**Fan.** The radiator fan adds about 7.5 A (19 counts) per cycle, every ~3 min at warm idle, and did not run at all while driving.

**Near-stall dips** (`nearstall_electrical.png`; t = 0 at the rpm minimum):

| dip | rpm min | batt before (time) | batt after (time) | ALT FR before → after | ELD | brake | PSP (steering) | ECT |
|---|---|---|---|---|---|---|---|---|
| 22:39:41 | 546 | 14.16 (−0.6 s) | 13.59 (+0.4 s) | 58 → 87 % | 24 → 24 | released −0.7 s | no | 72.9, steady |
| 22:53:15 | 462 | 14.07 (−0.8 s) | 12.92 (+0.2 s) | 55 → 90 % | 23 → 24 | off | on at +2.1 s | 70.5, steady |
| 23:12:37 | 553 | 14.35 (−0.3 s) | 14.07 (+0.7 s) | 61 → 42 % | 30 → 32 | released −0.5 s | no | 65.4, steady (last step 16 s earlier, in the "safe" direction) |
| 23:25:55 | 519 | 14.16 (−1.1 s) | 13.01 (−0.1 s) | 45 → 83 % | 29 → 28 | released −2.3 s | **on from −2.2 s** | 66.8, steady |
| 23:26:12 | 495 | 14.26 (−0.8 s) | 13.11 (+0.3 s) | 41 → 84 % | 30 → 29 | released −2.9 s | **on at −1.8 s** | 67.5, steady |
| 23:26:24 | 439 | 14.16 (−1.0 s) | 12.73 (+0.1 s) | 40 → 84 % | 30 → 27 | off | no | 67.5, steady |

**Electrical verdict: no electrical event before any dip.**

- The voltage was normal in the read before each one, and ELD shows no load step.
- The low reads come after the rpm minimum: the alternator falls below its output speed, the battery carries the 25-30 A, and the regulator goes to full field (ALT FR 83-90 %). The voltage recovers within a second.
- The only extra loads present are the steady headlight load (+14 A, already compensated by the IAC) and, at the two driveway dips, power-steering pressure.

## 4. IAT

- **Idle.** IAT heat-soaks to 50-53 °C: drives 2158 and 2224, and the 2251 start at 49.7 °C after the gas-station stop.
- **Driving.** It falls to 45 °C in 66 s, 35 °C in 6 min, 25 °C in 15 min and 19-22 °C (≈ ambient) after 18 min. It stays there at 90-107 km/h.
- **Effects.**
  - **Idle target:** at the same ECT raw, the target at IAT below 32 °C and above 45 °C differs by 0-1 step (13.6 rpm), in both directions. The one larger gap, at raw 46-47 (750 against 810), compares the 23:10 driving stretch with idle, so it may not be IAT.
  - **Fuel and timing:** in 40 matched rpm/MAP cells of drive 2251 (closed loop, ECT reading below 72 °C), IAT below 24 °C against IAT above 32 °C gave injector time +1.7 %, STFT +0.6 points and ignition −0.7°. That is consistent with a correct air-density correction plus noise. Time-of-drive confounds it, because the hot-IAT cells come from the first 5 min.
- **Verdict:** IAT is plausible and well-behaved, and it affects nothing measurably. The sensor is fine. TPS and IAT share SG2 with ECT and do not move at the ECT steps (11-25), so the ECT fault is on the ECT-only part of the circuit.

## 5. Repair and test recommendations

The fault is on the **ECT-only part of the circuit**: the 2-pin ECT sensor and its connector C122 (RED/WHT signal to ECM C18, GRN/BLK sensor ground to SG2/ECM C26, per the 11-25 report and PDF p.397-400), up to the point where the GRN/BLK ground joins the shared SG2 line. Do not confuse it with the 1-wire temperature-gauge sender nearby.

**A. Static check (does not need the fault to "happen").** Engine fully warm, after the radiator fan has cycled at least once.

1. Read the live ECT tile. **Pass: 82-90 °C. Fail: below ~78 °C while the fan is cycling.** Tonight's 65-74 °C is a fail.
2. Ignition ON (engine running is fine), back-probe without piercing:
   - (i) at the sensor, C122 RED/WHT to GRN/BLK;
   - (ii) at the ECM, C18 to C26.
   - Healthy: both about 0.5-0.7 V when warm and equal within ~10 mV. The model predicts that tonight's state would show about 1.0-1.15 V at the ECM.
3. If (ii) is higher than (i), find the drop: measure ECM C18 to the RED/WHT pin at C122, then the GRN/BLK pin at C122 to ECM C26, each in mV with the circuit live. The leg with tens to hundreds of mV holds the bad contact.
4. Ignition OFF, sensor unplugged:
   - Sensor resistance should fit its warm curve (about 0.3 kΩ near 80 °C; check against the manual's graph).
   - Each wire end-to-end, ECM pin to C122 pin, should be under 1 Ω.
   - Push a spare male pin into each C122 female terminal: it must drag firmly, not slide loose.

**B. Wiggle test while watching the live ECT tile.** ECT updates only once a second, so hold each move for about 3 s. A helper watching the screen helps.

1. Engine warm and idling, in neutral with the parking brake on. Note the steady reading and the idle target.
2. At C122:
   - push the connector body in and pull it gently;
   - rock it up and down, then side to side;
   - tug each wire about 1 cm behind the connector separately: RED/WHT first, then GRN/BLK;
   - squeeze the connector's lock.
3. Follow the engine harness from the sensor toward the ECM. Flex it at each clip and at the point where the harness leaves the engine for the body (the only stretch that moves relative to the engine).
4. At the ECM, rock the connector that carries C18 and C26.
5. **Pass:** the reading stays within ±1 °C. **Fail:** a step of 4 °C or more, or a drop toward 35 °C, that you can repeat by moving one spot. Mark that spot.
6. The step can also go up. If a move makes the reading jump up to 82-88 °C, that is the bad contact being briefly closed. 23:10:02 tonight was one such jump, to 74-79 °C.

**C. Repair.**

- Replace the ECT sensor: cheap, and it comes with fresh terminals and a fresh O-ring.
- Inspect C122 for oil or coolant inside the seal, green corrosion, or a spread or burnt terminal. Replace the connector pigtail rather than only cleaning it if any terminal is loose.
- Fix the oil seep near the sensor noted on 11-25.
- Put a little dielectric grease on the seal, not inside the terminals.

**D. Before and after the repair.**

- **The warm idle target will drop to ~670 rpm.** That removes the 140-200 rpm cushion that tonight's pull-aways started from. Do the vacuum-leak and fuel-pressure checks (README "Next"; total trim +16 to +23 % at idle) **before or together with** the ECT repair, and launch with a touch of throttle at first.
- **Verification log** after the repair: 30 min of idle plus a drive.
  - ECT at fan-on 84-88 °C, no step of 4 °C or more, idle about 670 ± 50 rpm.
  - The lights on and off at idle (ELD should step about 14 A).
  - A few full-lock turns at idle (the PSP bit and the IAC response), to size the steering load on its own.
- **Logger:** for the wiggle test, a faster read of block 0x03 would help. The README's idea of dropping the always-zero bytes from the read plan gives 2-3 Hz. That is an app change and is not done here.

## Side notes

- **Brake bit 0x08 b3** works while driving (about 380 set reads in drive 2251, matching the slowdowns). It can be upgraded from "plausible".
- **0x08 b2 (PSP)** is not stuck at 1. It clears during tight low-speed turns (18 reads, all below 21 km/h). Name it `ps_switch` (active low) and use it as the steering proxy.
- **0x2F while driving is not the slow ECT-gated counter** described in `slow-bytes.md`. It counts up by 1-130 per read, reached 206, and resets to 0 every 2-40 s, starting at the 22:39:48 pull-away. Its "ECT monitor" label holds only at idle and needs a second look.
- **Fan bit 0x0C b1** never set during the 25 fan cycles; it stays "wrong", as before.

## Files (`work-ect/`)

- `overview_drives.png`: drives 2224 and 2251. ECT with the steps marked, vss/gear/brake, rpm and target, ELD and ALT FR, battery, IAT.
- `overview_evening.png`: the whole 11-26 evening (idle drives, then driving).
- `summary_stats.png`: jump rates per drive; step rate by speed; idle target against the ECT reading; battery against rpm.
- `zoom_2240_ect_steps.png`, `zoom_2310_ect_steps.png`: the two step clusters while driving, with target, vss, brake, STFT and ign.
- `nearstall_electrical.png`: the six pull-away dips with battery and ALT FR reads.
- `ect_all.pkl`, `drives.pkl`, `corr.pkl`, `fan.pkl`: intermediate data (git-ignored with the database copy).
