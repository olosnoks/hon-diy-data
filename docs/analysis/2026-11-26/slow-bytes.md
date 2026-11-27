# Slow bytes: 0x2A, 0x2F, 0x86 (0x80-0x8F) and the idle target 0x76-0x77 (2026-11-26)

**Data.** I worked from a read-only copy of `hondalog.db`, refreshed once at about 22:28. It covers drives 31-38 (20:31:58-22:27:54) plus the 21:49 sweep (`work-sweep/car-214932-wide.csv`). From 20:45 onwards, 0x2A and 0x2F are read at ~1 Hz. The idle target 0x76:77 is read at ~4 Hz from 21:58, and in the sweep. The engine ran from 20:32:12 with no key cycle, MIL off (0x0B = 65) and closed loop on (0x9A = 2). The car sat at idle the whole time: no cold start, no driving, no A/C.

Scripts and plots are in `work-learn/`. `build.py` and `grid.py` build a 1 Hz wall-clock grid across all drives. `fit1*.py`, `fit2.py` and `fit3.py` fit the 0x2A models. `x2f*.py` and `tick*.py` cover 0x2F, `x86*.py` covers 0x86, `tgt*.py` and `fc2.py` cover the target and the false-cold readings, and `plots.py` makes the plots. The analysis venv is `work-learn/venv` (git-ignored).

The data was cut short when the owner left to drive. Open items are marked **OPEN**.

## 0. Bottom line

| byte | verdict | confidence |
|---|---|---|
| **0x76:77 idle target** | **Set by coolant temperature.** It moves one step (13.6 rpm) per ECT raw count: about 778 + 13.6 × (raw − 48) rpm over raw 46-70, and 670 rpm at raw 39 (~81 °C). The electrical load (ELD) has no effect (residual r = 0.02). Readings of ~120 raw and above (false-cold below ~37 °C) are **ignored**: the target holds the last good value | high (ECT link); medium (floor, other inputs) |
| **0x2A** | **An idle-air learned or averaged value**, clamped to 0-6. When the engine is fully warm (ECT raw ≤ 38, ≥ 82 °C), it follows the IAC command with a time constant of ~40 s: about IACV raw / 2.5, or (0x29 − 36…39) / 7. Otherwise it moves only one step per 12-15 min, mostly down, and the IAC command does not explain those steps. It is **not** a fuel-trim cell and not a monitor counter | medium-high (warm behaviour); low (slow mode) |
| **0x2F** | **An ECT-gated monitor accumulator.** It starts 25-50 s after the ECT the ECU accepts is ≤ ~53 raw (≥ 69 °C). It ticks every 31 ± 5 s (~390 ± 50 revolutions). It climbs +2/+3 per tick to ~22, then +1 per one to two ticks, and **levels off at 25-27** (seen twice). It **resets to 0 when ECT reaches ~56-58 raw (≤ 65-67 °C)**. False-cold readings of ≥ ~120 raw do *not* reset it. It never ran before 21:23 (enable condition **OPEN**) | medium (behaviour); low (which monitor) |
| **0x86 / 0x80-0x8F** | 0x80-0x8F are constant at idle. 0x86 is −3, and −7 in 16 of 210 sweeps; 15 of those 16 fall in a rich-O2 phase (0x86 is −7 in only ~12 % of rich sweeps). It looks like a transient in the O2/STFT feedback, not a learned cell. Whether the table is indexed by cell (rpm/load) is **OPEN**: the sweep only covered one idle cell | low |
| ltft 0x22 | 139-145 all evening (+8.6 to +13.3 %). It does not move with 0x2A | reference |

**Side finding, relevant to the stall hunt.** The ECU **rejects ECT readings of ≥ ~120 raw** and holds the last accepted value. The idle target, IACV and 0x2F all ignored those readings. Moderate false-cold readings (85-110 raw, ~45-50 °C) **are** used and cause fast idle. Examples: 21:13:46 (raw 93-108 → IACV 0 → 35, rpm 1169) and 21:32:54 (median 87 → rpm 1290). Readings of 120-183 at 21:26:39, 21:27:38, 21:31:23 and in the sweep moved nothing. So the dangerous ECT glitches are the half-way ones. On a snap-back from them, the target drops by up to 100+ rpm in one read and the IAC has to unwind (see `x2f_episodes.png`, lower panel). It is not known whether the rule is a level (~110-120 raw) or a jump size (**OPEN**).

## 1. 0x2A

Plots: `overview_evening.png`, `zoom_2135_2149.png`, `fit_2a_filter.png`.

0x2A changed 26 times, not the 4 seen earlier. Drive 2137 (21:37-21:45) was the only stretch with the engine reading **fully warm** (ECT raw 35-38, 84-86 °C, no glitches). In it, 0x2A went 6 → 0 in 2 min, then 0 → 3 → 0 → 6 within 7 min:

- **Down (21:37-21:39):** 0x29 decayed from 60 to its floor of 39 and IACV went to 0. 0x2A stepped down every 6-32 s and held at 0 while the valve stayed at the floor.
- **Up, then down (21:41:28):** an ELD load kick took 0x29 to 90. 0x2A went 0 → 1 → 2 → 3, then back down to 0 as 0x29 decayed.
- **Up (21:44:17):** an unexplained step in IAC demand took 0x29 from 49 to 85 and rpm from 668 to 808. ECT, ELD and A/C were all unchanged, and 0x76 was not logged yet. 0x2A went 1 → 6 in 36 s, with the steps getting slower (5, 7, 7, 17 s), as a first-order filter would. It then stayed at 6, which looks like a clamp.

**Model tests.** These are over the warm window 21:33-21:49, scored by the mean absolute error of the 0x2A value predicted each second.

| model | error (counts) |
|---|---|
| hold the last value | ~0.8 |
| first-order filter of 0x29, gated on ECT raw ≤ 38, τ 40 s, (0x29 − 36) / 7, floor, clamped 0-6 | **0.15** (86 % of seconds exact) |
| the same filter on IACV 0x28, x / 2-2.5 | 0.18 |
| integrator of (0x29 − c), the same gate | ≥ 1.3 |
| filter of rpm | 0.38 |
| filter of STFT | 2.5 |

So the warm behaviour is a **low-pass of the IAC command, not an integrator of it**.

The gate threshold is sharp:
- At raw 39-44 the fit gets worse.
- From 21:09 to 21:13 the engine sat at raw 41-42 (~79 °C) with IACV 0 for 4.5 min, and 0x2A did not move.

This fits the Honda pattern: "learn the idle air at fully warm idle, freeze otherwise".

**Slow mode (ECT not fully warm).**
- Steps: 21:02:30 down, 21:17:48 down, 21:30:10 up (during a false-cold fast idle), 22:04:35 down, 22:18:48 down.
- The down steps came while 0x29 was at 83-88 (IACV 10-17), so they are not the same law. A slow filter of IACV gets only the 21:02 step right (0.59 → 0.70 accuracy over the evening).
- The three back-to-back intervals are 15.3, 12.4 and 14.2 min. In engine revolutions they are **11,600, 10,800 and 11,900** (cv 5 % in revolutions against 10 % in time). That hints at an update on a revolution counter, but it rests on three intervals only.
- If that is right, the next slow step was due about 11,400 revolutions after 22:18:48, around 22:32-22:33. That is after the end of my copy (**OPEN**: check it in the live DB).
- One plausible reading: the learner is fed the *feedback* part of the IAC (the command minus an ECT-dependent base), which we cannot see. That part could be slightly negative at mid ECT even while 0x29 is high.

**Not explained:** 4 → 6 in the 48 s app gap at 21:36:23-21:37:11, when no data was logged.

**What it is not:**
- ltft and STFT do not lead it (STFT fits worst of all).
- Battery voltage, IAT and time of day do not track it.
- It does not count time (it goes both ways and holds).
- It does not set the idle target (the target varies 745-1068 with 0x2A fixed at 6).

## 2. 0x2F

Plot: `x2f_episodes.png`.

**Episodes.** There were 14 between 21:23 and 22:28. 0x2F was 0 throughout drive 2045 and the first 8 min of 2114. Sequence within an episode:
1. It goes 0 → 1, 25-50 s after the accepted ECT has stayed at or below ~53 raw (≥ 69 °C). An ECT snap-back to warm often comes just before, which is why earlier passes saw a trigger "29-40 s after the snap-back". But episodes in drive 2158 also started 3-6 min after the last ECT jump.
2. It ticks every **31 ± 5 s** (22-42 s, n = 24). Ticks are shorter at higher rpm (r = 0.63 with 1/rpm), which works out to **~390 ± 50 revolutions** per tick. There is no fixed phase across episodes, so it is a timer restarted per episode.
3. The values were identical in both long runs: 1, 4, 6, 9, 11, 14, 16, 19, 22 (+2.6 per tick). After that come +1 steps every 37-76 s, then a level: 25-27 in drive 2137 (it also went *down* 27 → 26 → 25 → 26), and 25 flat for 10 min in drive 2158 (22:17:53-22:27:54). The same curve appeared at ECT raw 35-38 and at raw 46-56, so the curve does not depend on ECT.
4. **Reset:** it drops to 0 within one read when ECT reaches ~56-58 raw (≤ 65-67 °C). This held 11 times out of 11. It is **not** reset by false-cold readings of ≥ ~120. It even started (21:28:05, 22:00:32) and ticked (21:31:47) during them, and reset only when the reading came back through 58-80.

**What it is.**
- A pure counter does not decrement, and this plateau wanders by ±1. So it is more likely a monitor accumulator or filtered quantity that is enabled while the ECU believes the engine is warm.
- Candidates are a thermostat / ECT-rationality monitor (P0125/P0128 type, which needs the ECT held above ~70 °C), or a warm-idle monitor (IAC P0505 or the fuel-system monitor) that needs a stable warm idle before it can judge.
- Nothing in the data names it. The MIL stayed off and no DTC slot filled.
- I checked whether the plateau tracks a slow average of MAP, load (0x9C), rpm, IACV, STFT, battery or ignition. None of them fits.

**Enable condition (OPEN).** Two explanations fit equally:
- (a) IAT crossed ~41-43 °C at 21:20-21:22.
- (b) The counter was armed by the first long accepted cold excursion: 45 s at raw 70-83 (52-58 °C), 21:22:01-21:22:46. The 7-s one at 21:13:46 did not arm it.

## 3. 0x86 and 0x80-0x8F (sweep only)

Plot: `sweep_0x86.png`.

**The table.** In signed values, 0x80-0x8F read −8, 0, −7, 0, −16, 0, (−3/−7), −8, −4, −16, 0, −12, −8, 0, 0, −114 (142). All of them except 0x86 were constant for 210 s.

**0x86.**
- It was −7 in 16 sweeps. In 15 of them the O2 sensor was rich (raw ≥ 25), but only ~12 % of rich sweeps show −7.
- In the sweep before a −7, STFT averaged 138.9 against 133.7, and STFT falls during the −7 sweeps.
- ignition, injection, MAP and rpm are the same in both states, and the rpm error is not conclusive.
- Best guess: a **live O2 feedback variable**, perhaps the proportional "skip" applied for a short window after a lean → rich switch. The 0.58-s sweep catches only part of that window.
- It is not a learned cell: it flips back and forth while LTFT stays at 139.

**Cell indexing (OPEN).** Only one rpm/load cell was used, and ltft did not change during the sweep, so the sweep cannot show whether the table is indexed by load or rpm. Which cell is "active" at idle also stays **OPEN**: no cell changed except 0x86.

**No internal ECT byte.** I looked for a byte in the sweep that carries an "accepted ECT", meaning one that tracks 0x10 but holds during false-cold. The only candidates are the target itself and bytes driven by rpm or IACV.

## 4. Idle target 0x76:77

Plot: `idle_target_vs_ect.png`; step list in `work-learn/tgt_steps.txt`.

- **Steps.** 77 changes in 4,363 reads. The step is 13.5-13.75 rpm (54-55 raw); bigger jumps are multiples of it. The range was 745-1068.
- **ECT.** With the ECT stable, the median target goes up by one step per ECT raw count from raw 48 (778) to 54 (864). It then flattens slightly (raw 55-58 all give 864-878), and reaches 946 at raw 62 and 1014-1028 at raw 65-68. The target follows the accepted ECT within about one read.
- **Warm floor.** The drive-2137 header (0x76 was then read as `ecu_id`) shows **670 rpm at raw 39**, which is the factory spec. Extrapolating the line would give ~655, so 670 is probably the floor at ≥ ~80 °C.
- **Other inputs.** ELD loads up to 37 A leave no residual (corr 0.02). A/C was never used. Time since start could not be tested.
- **Earlier headers.** They read 30-40 rpm *lower* at the same raw ECT: 715 at raw 46 (IAT 29 °C), 762 at raw 48 (IAT 40 °C), against 778-790 in drive 2158 (IAT 51 °C). So a second input is possible (IAT or time) (**OPEN**).
- **False-cold readings.** Readings of ≥ ~120 raw are ignored (6 of 6 episodes: the target held). Moderate cold readings move it.
- **Consequence.** The faulty ECT circuit reads 66-73 °C most of the evening while the engine is really at ~85 °C (the drive-2137 readings, with the fan cycling). That alone raises the commanded idle from 670 to 780-880 rpm. It explains the "warm idle above spec", and it keeps 0x2A in its frozen mode. **Fixing the ECT connector should drop the idle target to ~670 and let the IAC learner run.**

## 5. Logging that would settle the open items

1. **One cold start from key-on** with the map as it is (0x2A, 0x2F, 0x76 already in). It would show:
   - the target-vs-ECT table down to cold, plus any after-start adder;
   - whether 0x2A survives a key cycle (a learned value should);
   - whether 0x2F arms on a real warm-up (enable condition a or b);
   - the 0x27 / after-start behaviour.
2. **One idle after the ECT connector is fixed**, 15 min fully warm (raw ≤ 38). Expect a target of 670 and 0x2A settling to IACV / 2.5. 0x2F should reach its plateau and either set a "complete" flag or stay there.
3. **Add 0x80-0x8F as a slow block** (1 Hz, one 16-byte read), and log one real drive. Cells that change with rpm/load would show the indexing.
4. **One 2-minute session** reading 0x86, 0x15 and 0x20 at ~10 Hz (expert/raw tools), to time 0x86 against O2 switches.
5. **The next real snap-back from a moderate false-cold** (85-110 raw), with the target and IACV at their present rates. That is the event most likely to pull the idle down hard. To tell a level rule from a jump-size rule for ECT rejection, a jump of ~110-120 raw is needed.
6. **Check the live DB** for a 0x2A step at about 22:32-22:33. That tests the ~11,400-revolution slow update.

## 6. Recommended map changes (not applied)

| name | addr | len | formula | rate | status / note |
|---|---|---|---|---|---|
| `iac_learn` (rename `raw_2a`) | 0x2A | 1 | `x` (0-6; ≈ IACV raw / 2.5 when warm) | slow (1 Hz) | unverified. "Idle-air learned value; tracks the IAC command (τ ~40 s) only when ECT ≥ ~82 °C" |
| `mon_2f` (rename `raw_2f`) | 0x2F | 1 | `x` | slow | unverified. "ECT-gated monitor accumulator; runs at ECT ≥ 69 °C, resets at ≤ 66 °C, levels off at ~25" |
| `idle_target` | 0x76 | 2 | `x / 4` | medium (keep) | upgrade to "plausible": ECT-driven, 13.6 rpm/step |
| math `idle_err` | – | – | `rpm - idle_target` | – | the most direct stall-margin signal |
| `tbl_80` block (`learn_86` etc.) | 0x80 | 16 | `signed(x, 8)` per byte | slow | watch only; one 16-byte read |
| alarm `ect_moderate_cold` | – | – | ECT raw between ~80 and ~115 after warm-up | – | flags the glitches the ECU acts on (fast idle, then a hard drop on snap-back) |

Once `idle_target` is read at medium rate, the drive-header `ecu_id` problem goes away (the map already reads `ecu_id` from 0x78).
