# Parked idle against driving: which findings survive (2026-11-26)

**Question.** Most of what we concluded about this car (2000 Civic EX, D16Y8, stock P2P ECU, manual) came from
long parked idles on 11-25 (3.9 h) and 11-26 evening (1.8 h). The two drives of 11-26 (22:24-23:27, 56 min moving)
are the first data from a car that was driven. Which idle findings hold, which change, and which were idle artefacts?

**Data and method.** Read-only copy of `hondalog.db` (23:33 file) in `work-compare/` (git-ignored). Drives 6-39
(drives 1-5 skipped: OBD2-only or garbage in 0x08-0x11). Everything was resampled to the 1 Hz slow-block reads
(fast channels averaged over the preceding second, `build.py`). The core decodes (rpm, MAP, ECT, STFT, LTFT, IACV,
ELD) are the same in every map version used, so 11-25 and 11-26 are comparable. Ignition is not, so it is left out.
No GPS data is used. Scripts: `build.py`, `groups.py`, `levels.py`, `lm.py`, `fan.py`, `misc.py`, `iacmodel.py`,
`link.py`, `trans.py`, `slow.py`, `plots.py`; venv `work-compare/venv`.

"Steady idle" means: vss 0, TPS < 9 %, 550-1300 rpm, A/C clutch off, more than 25 s after an ECT step of 4 °C or more,
and not within a fan cycle.

**Warm idle after driving is short.** It covers the gas station (22:43:13-22:45:24, after 3 min of driving), the
hot restart there (22:51:09-22:53:17, after 6 min off) and home (23:26:27-23:26:58, straight after the driveway dips).
That is 188 steady seconds, against 13,000+ parked. The traffic stops were 8-20 s long and are all transients.

## 1. Warm idle after a drive against the long parked idles

Medians, steady idle (`groups.py`, `iacmodel.py`; `fig3_before_after_drive.png`):

| group | s | rpm | target | ECT read | IACV % | iac_cmd | 0x2A | STFT | LTFT | total | MAP kPa | ELD A | IAT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 11-25 parked, ECT warm, L | 2677 | 669 | – | 82.5 | 2.7 | – | – | 11.7 | 10.2 | **21.7** | 26.2 | 14.4 | 48 |
| 11-25 parked, ECT warm, M | 3912 | 810 | – | 83.6 | 6.7 | – | – | 7.9 | 7.8 | **16.0** | 25.2 | 15.2 | 50 |
| 11-26 parked, good contact (d35) | 359 | 677 | (670) | 84.8 | **0.0** | **39 (floor)** | 1 | 11.3 | 12.5 | **24.0** | 24.6 | 8.9 | 48 |
| 11-26 parked, reading 69-70 °C | 1338 | 829 | 837 | 69.7 | 5.5 | 87 | 5 | 8.9 | 8.6 | **17.7** | 23.4 | 10.1 | 51 |
| 11-26 parked, lights on, 22:37:40-22:39:30 | 106 | 809 | 810 | 73.8 | 9.8 | 96 | 3 | 8.0 | 8.6 | 16.6 | 27.5 | 23.8 | 51 |
| **after driving, all three stops** | 188 | 836 | 837 | 69.7 | 5.5 | 87 | 3 | 10.9 | 8.6 | **19.5** | 25.8 | 23.8 | 44 |
| of which gas station | 89 | 828 | 824 | 69.7 | 5.1 | 85 | 3 | 10.3 | 8.6 | 18.9 | 25.6 | 24.2 | 44 |
| of which hot restart | 77 | 839 | 837 | 69.7 | 7.5 | 91 | 3 | 12.3 | 8.6 | 20.9 | 27.0 | 24.2 | 45 |
| of which home (just after the dips) | 22 | 860 | 864 | 67.5 | 2.4 | 77 | 3 | 8.6 | 7.8 | 16.4 | 25.0 | 22.5 | 21 |

**Verdict: the same idle, with slightly more air getting past the valve.**

- **rpm and target.** rpm sat on target within ±5 rpm in both cases. The target came from the same ECT reading
  (69.7 °C, still about 15 °C low). After driving it was one 13.6-rpm step higher at the same reading (837 against
  824 at raw 52). IAT (44 against 51 °C) or the headlights could cause that; the data cannot separate them.
- **IAC.** With the headlights on (+14 A), parked before the drive needed iac_cmd 96 and IACV 9.8 % to hold 810.
  After driving, with the same lights and 0x2A unchanged (3), it needed only 85-91 to hold a *higher* target of 824-837.
  A fit on parked 11-26 idle (cmd = 87.8 + 0.036·(target−810) + 0.49·(ELD−10) − 0.43·(IAT−50), residual sd 1.6)
  predicts 98 for the post-drive stops. The observed value is 87, which is **9-12 counts less air** (about 4 % IACV),
  or 9 counts if the IAT term is dropped.
  - So the hot engine after a drive takes in more air past the closed IAC than the heat-soaked parked engine. The
    likely reasons are lower friction from hot oil, or more air past the throttle and fast-idle valve once hot.
  - The "excess idle air" finding is not an artefact of idling. If anything it is larger after a drive.
- **Trims.** +19.5 % after driving against +17.7 % parked at the same rpm and target. STFT is up 2 points and LTFT is
  the same frozen 8.6. So the lean idle trim is not an artefact of heat soak or long idling either.
- **MAP** was 25.8 against 23.4 kPa. That is the headlight load: with lights on parked it was 27.5.
- The home stop is not representative: it comes right after the 495/439 rpm dips, while the ECU was taking air back
  (cmd 77).

## 2. The ECT offset: did it exist on the idle nights too?

The reference is the radiator fan. Its thermoswitch closes at a fixed coolant temperature, so the ECT reading at
fan-on shows the offset (`fan.py`, ELD steps of +4.5 to +11 A for 12-30 s; `fig1_ect_fanon.png`).

| period | fan cycles with a steady reading | ECT reading at fan-on | share of running reads ≥77 °C / 58-76 °C / <58 °C | median reading |
|---|---|---|---|---|
| 11-25 parked | 23 | **84.8 °C** (IQR 83.6-86.0, 82.0-90.9) | 75 % / 11 % / 14 % | 82.5 |
| 11-26 parked to 22:24 | 17 | **72.1 °C** (IQR 69.7-73.8). Only drive 2137 read 83.6 and 85.9 | 15 % / 76 % / 9 % | 70.5 |
| d38 parked 22:24-22:39 | 3 | 69.7-74.7 °C | 0 / 100 / 0 | 72.1 |
| driving + stops | 0 (the fan never ran) | – | 2 / 98 / 0 | 67.5 |

**Verdict: the steady low offset is not a driving artefact. It is a fault state that took over between the two
nights.**

- On 11-25 the contact was mostly good: 23 fan-ons at 82-91 °C.
- The low-offset state already existed on 11-25, but only intermittently: fan-ons at 74.7-77 °C and readings of
  57-75 °C during 21:28-22:07.
- By the evening of 11-26 it was the normal state (76 % of reads), with brief good stretches (drive 2137).
- It stayed in that state through the whole drive.

**False-cold jumps** (≥15 °C between reads ≤3 s apart, `misc.py`):

- 11-25 parked: 17.6/h.
- 11-26 parked: 27.3/h.
- The 14 parked minutes just before the drive (22:24-22:39): **0**, and not even a 4 °C step.
- Driving: 0 jumps and 14.5 steps/h.

The quiet period began 33 min before the wheels turned, so "no glitches while driving" is the fault's state at the
time, not an effect of driving. That confirms `drive-ect-electrical.md`: the fault drifts over minutes, and road
vibration does not drive it.

## 3. Do the "two idle levels" reduce to the ECT-driven target?

**Only partly.** There are two separate mechanisms (`levels.py`, `lm.py`, `fig2_idle_levels.png`,
`fig5_810_state_good_ect.png`).

1. **The ECT term.** It explains every idle level from 810 to 1,014 rpm on 11-26 and in the drives. At stops after
   driving the target was 824 at 70.5 °C, 837 at 69.7, 850-864 at 67-69, and 973-1,014 at 60-62. While moving it
   followed every step within one read: 22:40:56-22:41:07 (762 → 960), 23:10:02 (864 → 750) and 23:12:21 (750 → 864).
2. **A separate ~810 rpm state that has nothing to do with the ECT reading.**
   - On 11-25, with a correct warm reading (raw ≤ 40, ≥ 80 °C), the idle sat at 803-815 rpm for 55-69 % of the time.
     The rest of the time it sat at 666-675. Stretches lasted up to 30 min.
   - The M state had IACV 6.7 % against 2.7 % at L, and a total trim 5.7 points lower. The rpm-only trim fit predicts
     1.7 points, so the M state is about 4 points richer. That fits EVAP purge (FSM 810 ± 50), still unproven.
   - The 11-26 data show the switch-on cleanly once. At **21:44:17 in drive 2137 the ECT read a steady 85.9 °C and
     iac_cmd stepped 49 → 84 in one read**. rpm rose 670 → 810. ELD, A/C, brake, PSP (0x08 = 0x84 throughout), MAP
     and TPS did not change. 0x2A then counted up 1 → 6. The only other change was 0x2F ticking 26 → 27 five seconds
     earlier (lead only, n = 1).
   - Triggers seen on 11-25:
     - **On:** 3 of 12 fan cycles that started at L ended at M (19:19:43, 20:25:23, 20:36:04), one after a load sag
       (22:27:55), and one spontaneously (11-26 21:44:17).
     - **Off:** every restart came back at L (4 of 4). Also a rev to 1,865 (20:00:48) and ECT snap-backs from a cold
       reading (20:29:41, 21:26:53, 21:53:44, 22:07:32).
   - **Parked before the drive, with the low reading, the target behaved like max(ECT term, 810).** It was flat at 810
     for raw 46-51 (70.5-75.6 °C), where the moving data and the slow-bytes fit give 750-805.
     - That 810 floor **dropped out at the first pull-away: 22:39:51, target 810 → 750 at an unchanged raw 49**.
     - It was never visible again, because the reading never got warm enough at a stop for the ECT term to fall
       below 810.
     - While moving, the target flattened at **750** for raw 44-50. That value is the FSM headlight idle (750 ± 50),
       and the lights were on.

So the 670/810 bimodality of 11-25 was **not** the ECT. The ~810-1,014 levels of 11-26 and of the drives **were**.
Fixing the ECT removes the second kind but not the first. After the repair, expect the warm idle to sit at either
~670 or ~810 depending on that latch, with the latch apparently cleared by revving, restarting or pulling away.

## 4. The other findings

- **Idle target set by ECT:** holds, and it works the same while driving (above). There are three refinements:
  - a parked 810 floor;
  - a target 40-60 rpm lower while moving at raw 49-53 (750-794 against 810-837 parked);
  - one step higher at stops after driving than parked at the same reading.
- **LTFT frozen while the ECT reads low:** holds, and it is not an idle effect. LTFT changed only while the
  reading was ≥ 76.5 °C (raw ≤ 44):

  | period | LTFT changes at ≥ 76.5 °C | LTFT changes at 45-76 °C |
  |---|---|---|
  | 11-26 parked | 25 in 926 s | 0 in 4,755 s |
  | driving | 2 in 108 s (23:11:33, 8.6 → 7.8, the only time the reading touched 77 °C) | 0 in 2,266 s |
  | 11-25 | 141 in 10,161 s | 3 in 1,455 s |

  Driving does not unlock it; the reading does (`fig4_learners_gated.png`).
- **0x2A, learned idle air:** holds and gains detail.
  - With a good reading it moves within seconds: 6 → 0 in 2 min while iac_cmd sat at its floor (39) with rpm above
    the 670 target, then 0 → 6 within 45 s after the 810 step.
  - With a low reading it steps ±1 every 13-30 min, parked only (6 → 5 → 4 → 3, 22:04-22:31).
  - It did not move once in 55 min of driving and post-drive stops (stayed at 3).
  - That makes it an idle-only learner with an ECT gate. "Learned idle air" moves from low to medium confidence.
- **Excess idle air, idle above spec with IACV near closed:** holds, but it is smaller and more conditional than
  "700-850 above spec" suggested.
  - With a good reading, warm idle was 669 rpm at IACV 2.7 % on 11-25. That is on spec, with the valve in control.
  - On 11-26 it was 677 rpm with iac_cmd at its floor and 0x2A learned down to 0: the valve was fully shut and the rpm
    still 7-45 above target.
  - Most of the "above spec" idle (780-880) was the ECT target or the 810 state, not excess air.
  - The excess air is real at the warm target and grew after driving (section 1). The cause is still open: vacuum
    leak, fast-idle thermo valve, throttle stop.
- **Fuel trims:** hold, and they are the same parked, after driving and at the hot restart. Against the drive fit
  (8.2 + 6.7 × 1000/rpm, `drive-fueling.md`):

  | condition | observed | fit |
  |---|---|---|
  | 11-25 L | 21.7 | 18.2 |
  | 11-25 M | 16.0 | 16.5 |
  | 11-26 good contact | 24.0 | 18.1 |
  | 11-26 low reading | 17.7-18.5 | 16.3-16.4 |
  | after driving | 19.5 | 16.2 |

  So rpm explains most of the variation. Idle with the valve shut or the LTFT freshly learned sits 3-6 points above
  the fit. The M state sits on the fit: purge fuel would offset that.
- **Radiator fan:** an idle-only phenomenon. It cycles every 2.5-3.5 min parked, and **never ran in 56 min of
  driving or at the 1-2 min stops**.
  - At L on 11-25 the fan-on feed-forward lifted the idle to ~750-760 for the cycle.
  - On 11-26 at a target ≥ 810 there is no visible lift, because the target already covers it.
- **Electrical idle-up:** can't tell from the drives. The headlights (+14 A, the whole drive) were compensated by
  IAC (+9 counts at 22:37). Any ELD idle-up target sits under the 810+ ECT target at every stop. The 750 floor while
  moving is the only hint of one.
- **K-line dropouts:** hold as an app artefact, not an idle effect. Read gaps ≥ 0.5 s in the fast block:

  | period | gaps ≥ 0.5 s | of which ≥ 2 s |
  |---|---|---|
  | 11-25 | 153/h | |
  | 11-26 parked | 85/h | |
  | after the 22:24 app fix, parked (14 min) | 0 | |
  | after the 22:24 app fix, driving to 23:10 (25 min) | 0 | |
  | 23:10-23:27 | 67/h | 3 (10.6/h), longest 8.1 s while moving: the loose OBD2 plug |

  The owner also unplugged the cable at 22:36.

## What we now believe

| # | belief | evidence | confidence | status vs. idle-only view |
|---|---|---|---|---|
| 1 | The ECT circuit has a resistive contact fault with two failure modes: false-cold jumps (~35 °C) and a steady ~13-15 °C low offset. They drift over minutes, independent of driving. | Fan-on reading 84.8 °C on 11-25 against 72.1 °C on 11-26. Jumps 17.6/h → 27.3/h → 0 from 22:07 onwards, parked and driving alike. | high | holds; the offset existed only intermittently on 11-25 and became the normal state on 11-26 |
| 2 | The coolant itself is fine: the thermostat regulates and nothing overheats. | Fan-on at 82-91 °C reading with good contact. Flat reading at 90-107 km/h. Fan never needed while moving. | medium-high | holds |
| 3 | The idle target has an ECT term (~13.6 rpm per raw count) that tracks the reading within one read, parked and moving. | Target against raw on d37-d39. Every step while driving followed. | high | holds |
| 4 | There is a separate ~810 rpm idle state, independent of ECT. It switches on after some fan cycles, sags or by itself, and clears on restart, a rev or pulling away. | 11-25: 55-69 % of warm-reading idle at 810. 11-26 21:44:17 step at 85.9 °C. 810 floor parked, gone at 22:39:51. | medium (existence high, trigger and cause low; EVAP purge is the leading guess) | **changes**: "two idle levels" is not just ECT |
| 5 | Fixing the ECT drops the warm target to ~670 (or ~810 when that state is latched) and removes the 140-200 rpm launch cushion seen on every logged pull-away. | Rows 3-4. Every launch was at a 810-864 target. | medium-high | holds, with the 810-state caveat |
| 6 | There is some excess idle air: valve at its floor with rpm 7-45 above a 670 target, and about 10 IAC counts less needed after a drive than parked. | 11-26 drive 2137. Post-drive against pre-drive at equal lights and 0x2A. | medium | holds; smaller than first stated, and not an idling artefact |
| 7 | Idle trims are +16-24 % and fall with rpm. They are the same after driving, so they are not a heat-soak or long-idle effect. The cause is the front O2 seeing lean at low rpm, or unmetered air or oxygen. | Section 1 and section 4 trim rows. | medium (size high, cause low) | holds |
| 8 | LTFT and 0x2A learn only while the ECT reads ≥ ~76.5 °C. 0x2A also learns only at idle. Both were frozen for the whole drive. | 0 LTFT changes in 2,266 s at a low reading while driving, 2 in 108 s at 77 °C. 0x2A unchanged for 55 min. | high (LTFT), medium (0x2A meaning) | holds; driving does not unfreeze them |
| 9 | The radiator fan cycles only at parked idle. Its feed-forward idle-up only shows when the target is low (670). | 0 fan cycles in 56 min of driving. | high | idle-specific (an artefact of idling) |
| 10 | Electrical load (headlights, fan) is compensated by IAC. The charging system is healthy. | +9 counts for +14 A. 13.9-14.3 V across all rpm. | high (compensation), can't tell (a separate ELD target) | holds |
| 11 | The K-line dropouts were an app artefact (fixed 22:24). Later gaps are a loose OBD2 plug. | 0 gaps ≥ 0.5 s from 22:24-23:10, parked and moving. 67/h after 23:10. | high | holds; not idle-related |
| 12 | The pull-away dips (440-550 rpm) are a launch problem: closed throttle, clutch bite, an IAC that reacts too slowly. They are not a warm-idle instability. | Warm idle after driving sat on target ±5 rpm. All dips came at clutch take-up. | medium-high | new from the drives; the parked idles never showed it |

## Caveats

- Only 188 s of steady post-drive idle, in three windows. The home window is contaminated by the dips.
- There is no post-drive idle with a good ECT reading. The excess-air comparison is made at a 824-837 target, not 670.
- IAT and headlights differ between the windows. The IAC model corrects for them, but the IAT coefficient comes from
  parked data only.
- The 11-25 data have no idle target, iac_cmd or 0x2A logged. The L/M analysis there rests on rpm and IACV only.

## Files (`work-compare/`)

- `fig1_ect_fanon.png`: ECT reading and fan-on readings, 11-25 and 11-26, plus the reading distributions.
- `fig2_idle_levels.png`: steady idle rpm against the ECT reading, and the idle target against the reading (parked,
  stopped after driving, moving).
- `fig3_before_after_drive.png`: 22:20-23:27 rpm, target, iac_cmd, ELD, trims, ECT and IAT, with the compared windows.
- `fig4_learners_gated.png`: LTFT and 0x2A against the ECT reading, 20:45-23:27.
- `fig5_810_state_good_ect.png`: the 21:44:17 switch into the 810 state with a steady warm reading.
- `t1hz.pkl`, `fan.pkl`, `groups.pkl`: intermediate data (git-ignored with the database copy).
