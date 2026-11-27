# Output and control-state bytes: what is still uncertain (2026-11-27)

**Data.** A read-only copy of `hondalog.db` (file time 11-26 23:33, drives 6-39) is in `work-outputs/` (git-ignored). Only the raw bytes in `ecu_reads` were used. That gives 22,658 slow reads of the flag bytes over 34 drives:

- drives 6-32 (11-25 and 11-26 to 20:45) read the `08+10` block;
- drives 33-39 read `03+15`, with 0x16-0x1F, 0x27-0x3C and (from drive 37) 0x9A-0xA0 and 0x76 alongside.

The fast blocks (rpm, MAP, TPS, O2, STFT, injector) come in at ~11 Hz. Two drives were real driving: `drive_2026_11_26_2224` (38) and `drive_2026_11_26_2251` (39). The only full 0x00-0xFF sweep, `2026-11-26/work-sweep/car-214932-wide.csv`, is the sole source for 0xA4. Times are local. No position data was used.

**Scripts** (in `work-outputs/`, venv `work-outputs/venv`):

- `load.py` builds `L.pkl`: every block per drive, plus a slow grid anchored on the flag block. `lib.py` holds the decoders.
- `b6.py`, `b6elec.py`, `b6elec2.py`: 0x0B b6
- `c0.py`, `c0b.py`, `c0c.py`: 0x0C b0
- `p2f.py`, `p2f2.py`, `p2fidle.py`: 0x2F
- `x2a.py`: 0x2A
- `a4.py`: 0xA4
- `plots.py`: the plots `01_0x0B_b6.png` to `05_0xA4_b0_sweep.png`

Scales used: ELD = 77.06 − raw/2.537 A (0.394 A/count), ALT FR = raw/2.55 %, battery = raw/10.45 V, STFT = (raw/128 − 1) × 100 %.

## Verdicts

| byte/bit | verdict | confidence | proposed map name | formula |
|---|---|---|---|---|
| **0x2F** | **EVAP purge command (PCS duty or ramp)**. Runs only with the ECT reading above ~66-69 °C, ramps with airflow, is cut in decel and on throttle moves, and STFT falls as it rises. | **medium-high** (was medium) | `purge_duty` | `x` (0-255; `x / 2.55` % if it is a duty) |
| **0x2A** | **Learned idle air (IAC learn), kept in backup RAM.** At fully warm idle it is a lagged copy of the IAC command; otherwise it is frozen or moves very slowly. It survives key-off. | **medium-high** | `iac_learn` | `x` (0-6) |
| **0x0B b6** | **An O2-sensor heater control output.** It is off with the engine stopped, on from the first running read, and off above ~3,500 rpm at MAP ≥ ~84 kPa, even in closed loop at a steady 100 km/h. **Not ALTC** (no voltage change) and not "engine turning". The data cannot tell the primary heater from the secondary. | medium on "O2 heater", low on which one | `o2_heater` (unverified) | `bit(x, 6)` |
| **0x0C b0** | **ALTC, alternator control (1 = normal charging).** It drops to 0 for a fraction of a second at a sudden electrical load step at idle. In those reads ALT FR rises half as much and the battery dips 2.5× deeper than at other steps of the same size. Three published tables name this bit "Alt-C". | medium | `altc` (unverified) | `bit(x, 0)` |
| 0x0C b3 | VTEC pressure switch: set in one read only, together with VTEC (0x0A b2), at 23:03:11.7, 5,376 rpm, WOT | medium-low (n = 1) | `vtec_psw` | `bit(x, 3)` |
| 0xA4 b0 | Unknown idle-control status bit. It came on ~2 s after the radiator fan load dropped off (and the rpm overshot), and went off ~46 s later with no visible trigger. One event only. | low | `xa4_b0` | `bit(x, 0)` |
| 0x16 | Dead: 0 in 8,682 reads (crank, WOT, 5,400 rpm, 107 km/h, decel cut, hours of idle). A heater state would be a bit, not a byte, and would be on while running. Most likely an analog slot that is unused on this variant. | high that it is dead on this car; none on a name | drop after the cold-start log | – |
| 0x0D, 0x0E | Dead: 0 in all 22,658 reads, including key-on, crank, A/C, brake, PSP, VTEC, WOT and decel cut. Flags for hardware this car does not have (literature: 0x0D b3 = engine-mount solenoid on the P2T). | high that they are dead here | drop after the cold-start log | – |
| ELD input | It is the analog byte 0x19 (copy at 0x1C). No flag bit carries it. | high | `eld` (keep) | – |

## 1. 0x0B bit 6

Plot: `01_0x0B_b6.png`.

**All 43 zero reads:**

| where | reads | context |
|---|---|---|
| d21 (11-25 19:32:22.6) | 1 | first read after key-on, rpm 0; 1 from the first running read (1,544 rpm) |
| d31 (11-26 20:31:58-20:32:12) | 15 | 14 s of key-on with the engine off, rpm 0; 1 at the first crank read 20:32:13.1 |
| d39 22:51:09.5 | 1 | starter read, rpm 0; 1 one read later (1,348 rpm) |
| d39, driving | 26 | 8 episodes (22:54:35, 22:54:38, 23:03:09, 23:12:47, 23:12:51, 23:20:53, 23:20:57, 23:23:52), every one at rpm 3,556-5,376 and MAP 84-99 kPa |

0x0B reads 1 (pump only) in every b6 = 0 read while running, and 65 otherwise.

**Switching map (drive 39).**

- **Off** when rpm reaches ~3,550-4,150 with MAP ≥ ~84 kPa. It is not a pure rpm threshold: it stayed on at 3,801 rpm/73 kPa, 3,531/74 and 3,227/95.
- **On again** as soon as the throttle closes (MAP 14-18 kPa, at any rpm up to 4,500) or rpm falls below ~3,350-3,450.
- The switch is immediate. In each pull it went off in the first read past the threshold; no thermal delay of more than ~1 s is visible.
- **It is not the enrichment flag.** At 23:21:02-23:21:07 the car cruised at 95-105 km/h, 3,464-3,790 rpm, 87-89 kPa, **in closed loop (0x0F b0 = 1, fuel status 2)**, and b6 stayed 0 for 6 reads. It came back only at the throttle lift at 23:21:08.
- **It is not "engine turning"** either: it goes off with the engine at 4,000+ rpm.

That pattern is the textbook Honda HO2S heater strategy: heater on from start, cut at high rpm and load where exhaust heat keeps the sensor hot and could crack an energised element.

**Electrical check (does the switch show as a 1-3 A load step?).** I used the 39 slow reads at 3,000-5,000 rpm and MAP > 70 kPa, in drive 39, with rpm and MAP in the fit.

| quantity | effect of b6 = 1 (heater on, if it is the heater) | read |
|---|---|---|
| ELD | −0.56 ± 0.51 A | no step. The 95 % bound on the extra current ELD sees is about +0.4 A. A 1-3 A heater on a circuit ELD measures would have shown; either ELD does not see that circuit, or the step is smaller |
| ALT FR | **+1.3 ± 0.6 %** | in the heater direction, about 2 σ. At 2,500-4,000 rpm ALT FR rises ~0.52 %/A (fit against ELD), so this is **~1-3 A** |
| battery | −0.04 ± 0.04 V | **no voltage change**. ALTC switches the regulator between ~14.4 V and ~12.5 V; a ~1.5 V step would be obvious. So **b6 is not ALTC** |

**Edge pairs**, consecutive reads ≤ 1.6 s apart:

| pair type | n | ALT FR change |
|---|---|---|
| rpm rising into the zone, b6 1 → 0 | 7 | −1.6 % |
| rpm rising into the zone, b6 stays 1 | 7 | −0.6 % |
| rpm falling from > 3,300, b6 0 → 1 | 5 | +2.2 % |
| rpm falling from > 3,300, b6 stays 1 | 6 | +1.8 % |

The off and on edges are −1.0 and +0.4 % beyond the matching controls. That is the right sign for a heater of ~1-2 A, but with 5-7 pairs and 0.4 % resolution it is suggestive, not proof.

**Heater 1 or 2?** The literature splits:

- kerpz UNI: 0x0B b2 = heater 1, b7 = heater 2.
- hondamotor: b6 = heater 2.

On this car b2 and b7 never set, and b6 is the only bit that behaves like a heater. Nothing in the data separates the primary (PO2SHTC) from the secondary (SO2SHTC). The rear O2 (0xA0) cannot help either: it reads its 3.9 V start-up default for the first ~14 s after every start, and every logged start was already warm (ECT reading 60-70 °C), so no light-off curve exists. With one bit behaving like a heater, the primary is more likely (it is the one Honda cuts at high load), but that is a guess.

**Verdict:** an O2 heater output, medium. Name it `o2_heater`, unverified. Do not use it as an engine-running signal.

## 2. 0x0C bit 0

Plot: `03_0x0C_b0_load_steps.png`.

**Five zero reads** (the 11-26 22:13 copy used in `flag-bits.md` had three). Each is a single read, back to 1 in the next read:

| time | drive | ELD step in that read | battery | IACV | next read |
|---|---|---|---|---|---|
| 11-25 19:48:08.7 | 22 | +18.1 A (154 → 108 raw) | 12.9 V | 17 → 32 | ELD settles ~12 A lower |
| 11-25 19:56:12.2 | 22 | +15.4 A | 13.2 V | 18 → 36 | |
| 11-26 21:48:01.4 | 35 | +20.9 A | 13.4 V | 20 → 36 | |
| 11-26 22:20:54.0 | 37 | +16.2 A | 13.1 V | 16 → 34 | |
| 11-26 22:23:56.1 | 37 | +17.3 A | 13.8 V | 16 → 35 | |

All five are at a standstill at idle (770-927 rpm), with no A/C and no brake. They catch the inrush of a load being switched on: ELD jumps, then falls back by 8-13 A within one or two reads. Most are the radiator fan starting.

**Comparison with the other steps.** There were 55 ELD steps of +10 A or more between consecutive idle reads. In the 50 where b0 stayed 1:

| | b0 = 0 (n = 5) | b0 = 1 (n = 50) |
|---|---|---|
| mean ELD step | 17.6 A | 17.3 A |
| ALT FR rise | **+5.5 %** (0.32 %/A) | **+12.0 %** (0.83 %/A) |
| battery change | **−0.88 V** (median −0.96) | **−0.34 V** (median −0.29) |
| battery in that read | 13.28 V | 13.82 V |
| IACV kick | +17 | +13 |

So in the b0 = 0 reads the alternator field barely rose and the battery carried the new load. That is what a short **ALTC** action looks like: the ECM holds the alternator back for a moment at a sudden load at idle, so the engine is not hit with the full extra torque before the IAC has opened. The other reading fits the same numbers: b0 = 0 just marks the first instant of a step, before the regulator has responded, and the bit could be a "load-step detected" flag. Battery and ALT FR come from a different block than 0x0C, up to ~0.5 s apart, so the timing cannot split the two.

**What it is not:**

- **Not a voltage comparator.** The pull-away dips in drive 39 read 12.7-13.1 V with b0 = 1 (23:26:24: 12.73 V).
- **Not a load-level comparator.** Steady loads of 30-41 A (11-25 21:48:20, 40.0 A) read 1.
- **Not the radiator fan output.** It was 1 through ~58 fan cycles.

**Duration.** 5 of 55 steps caught at ~1 Hz means a window of roughly **0.1 s per step** (about 0.03-0.2 s), if it happens at every step.

**Literature.** kerpz ArduinoHondaUNI, mr-sneezy and hondamotor all name 0x0C b0 "Alt-C" (`raw-bytes-literature.md`).

**No sign of ALTC at light-load cruise.** The manual says ALTC can drop the setpoint at light-load cruise, but the battery read 13.9-14.3 V across the whole drive and never sat near 12.5 V. So that mode was never used tonight, which fits b0 = 1 throughout driving.

**Verdict:** ALTC, medium. 1 = normal charging; 0 = alternator held back, for about 0.1 s at a load step.

## 3. 0x2F: EVAP purge

Plot: `02_0x2F_purge.png`.

### 3.1 STFT at fixed rpm and MAP

I used the 1,031 slow reads that were moving, in closed loop (fuel status 2) and at steady throttle (TPS range < 3 % over the previous 3 s). STFT, O2 and rich fraction are averaged over ±0.5 s from the fast reads.

| 0x2F | n | STFT median | front O2 rich share | rpm | MAP kPa |
|---|---|---|---|---|---|
| 0 | 724 | +5.0 % | 0.66 | 1,799 | 27 |
| 1-20 | 23 | +6.6 % | 0.65 | 1,431 | 29 |
| 21-60 | 218 | +3.2 % | 0.68 | 1,873 | 37 |
| 61-120 | 29 | −11.1 % | 0.74 | 2,176 | 41 |
| 121-200 | 31 | −18.8 % | 0.89 | 2,513 | 53 |
| 201-255 | 6 | −20.7 % | 0.91 | 2,554 | 57 |

LTFT was 7.8-8.6 % throughout (frozen).

**Within the same rpm (250) × MAP (5 kPa) cell** (84 cells), STFT falls **13.8 ± 0.6 points per 100 counts of 0x2F**.

The front O2 rich share and mean voltage do **not** change within a cell (+0.01 ± 0.04). That is what closed loop should do: it pulls STFT down until the O2 switches around stoichiometric again, so the extra fuel shows in STFT, not in a richer average O2. The rich share in the table rises only because higher 0x2F comes with higher load.

**Two causes are possible for that slope.** 0x2F and the time since its last reset are almost the same number (Spearman 0.99), so a regression cannot pick between "0x2F is purge" and "STFT drifts down whenever the throttle is held". The resets decide it:

- **When 0x2F is reset while the car stays in closed loop, STFT jumps back up within one read.**
  - 23:23:05.3, steady cruise: 0x2F 209 → 0 and STFT −19.5 → −10.7 → +3.1 within ~1 s. MAP went 57 → 45 → 34 kPa, a small lift at 34-36 kPa with no fuel cut.
  - 22:41:07.7: 72 → 0, STFT −26 → −12 → +5, at a steady 31 kPa.
  - 23:23:15.7: 113 → 0, STFT −13.8 → +6.9.
- **The reverse at steady load.** At 23:22:01-23:22:04 (84 kPa held) 0x2F climbed 36 → 62 → 89 → 130 → 211 while STFT went +5.3 → +4.5 → +1.8 → −1.4 → −5.3 → −13.7, lagging by ~1-2 s.
- A time-since-transient counter would not make STFT snap back the instant it is cleared. Removing purge vapour would.

### 3.2 ECT enable

Moving, closed loop, share of reads with 0x2F > 0, by the ECT reading:

| ECT raw | reading | share > 0 |
|---|---|---|
| ≤ 50 | ≥ 71 °C | 0.68 |
| 51-53 | 69-71 °C | 0.61 |
| 54-55 | ~68 °C | 0.28 |
| 56 | 67 °C | 0.19 |
| 57 | 66 °C | 0.05 |
| 58 | 65 °C | 0.02 |
| ≥ 59 | ≤ 65 °C | 0 |

At idle it starts 25-50 s after the reading is at or below raw ~53 (≥ 69 °C) and resets at raw 56-58 (65-67 °C) (`slow-bytes.md`, 11 of 11 resets). So the enable is about **69 °C on, 65-66 °C off**, applied to the reading the ECU accepts. The manual's PCS condition is ECT above 68 °C. That matches, with a small hysteresis. (The reading sat 13-15 °C low tonight, so the real coolant was hotter.)

### 3.3 Idle

At idle 0x2F ramps +2-3 per ~31 s to a plateau of 25-27 (≈ 10 % if it is a duty). With the plateau reached, STFT was **3-4 points lower** than with 0x2F = 0 at the same ECT band:

| ECT raw | STFT, 0x2F = 0 | STFT, 0x2F at plateau (21-30) |
|---|---|---|
| 46-53 | 10.5 % (n 1,310) | 6.8 % (n 1,307) |
| 54-58 | 9.1 % | 7.3 % |
| ≤ 45 | 10.9 % | 9.5 % |

rpm and IAC were the same in each pair. A small idle purge would do exactly that.

### 3.4 Other features that fit a purge command

- The ramp rate grows with airflow: about +0.08/s at idle, ~+1/s at 25-40 kPa and 1,000-1,500 rpm, +10-30/s at 75-90 kPa.
- It is capped at 255.
- It drifts down when airflow falls (76 → 72 at 30 kPa).
- It is cleared in decel fuel cut and on throttle transients.

That is a purge duty scaled to engine airflow and ramped in. It also explains the "rich EVAP-like slugs" in `drive-fueling.md`: the −10 to −25 % trims at cruise are the stretches where 0x2F climbed past ~120.

**Verdict:** purge command (PCS), **medium-high**. Name it `purge_duty`. Whether the 0-255 value is a duty % or a ramp/limit value is open. Read it faster than 1 Hz while driving, because it moves up to 80 counts per second.

## 4. 0x2A: learned idle air

Plot: `04_0x2A_iac_learn.png`. There were 25 transitions, all at a standstill.

**Fully warm (ECT raw ≤ 38, ≥ 82 °C; only drive 2137, 21:37-21:45): it follows the IAC command with a lag.**

| x2a | iac_cmd median | iacv median | rpm |
|---|---|---|---|
| 0-3 | 39-40 (the floor) | 0 | 673-690 |
| 4-5 | 55-64 | 2-4 | 672-692 |
| 6 | 90 | 17.5 | 814 |

- **Down:** 6 → 0 in 101 s (21:37:17-21:38:57) as iac_cmd fell to its floor of 39. The steps came 12-32 s apart.
- **Up, then down:** 0 → 3 → 0 around the 21:41:40 load kick.
- **Up:** 1 → 6 in 36 s (21:44:24-21:45:00) after the 21:44:17 step in iac_cmd to ~90 (steps 5-16 s apart). It then stayed at 6 (the top of its range).

So 0x2A ≈ (iac_cmd − 39) / 8.5, low-passed. The earlier fit was a time constant of ~40 s, mean error 0.15 counts (`slow-bytes.md`). It is **not an offset added on top of iac_cmd**: it is the learned level of the IAC command itself, quantised to 0-6.

**Not fully warm (ECT reading 69-72 °C): nearly frozen.**

- It moved one step per 12-15 min: 21:02:30, 21:17:47, 22:04:36, 22:18:47, 22:31:14. Mostly down, while iac_cmd sat at 83-88.
- One up-step came during a false-cold fast idle (21:30:09).
- It was **3 for the whole 50 min of driving** (frozen off idle).

**Kept across key-off:**

| key-off | before | after |
|---|---|---|
| 21:13:53 → 21:14:51 | 5 | 5 |
| 22:45:24 → 22:51:09 (gas-station key-off, ~6 min) | 3 | 3 |

A learned value in backup RAM behaves this way; a live control variable would restart from a default. The one unexplained change is 4 → 6 in the 48-s app gap at 21:36:23-21:37:11, when nothing was logged.

**Verdict:** IAC learned value, **medium-high**. Name it `iac_learn`. It learns only at fully warm idle. The ECT fault (reading 13-15 °C low) has kept it frozen most of the time, so its present value (3) is stale.

## 5. 0xA4 bit 0 (sweep only)

Plot: `05_0xA4_b0_sweep.png`.

**Sequence:** on 21:50:54, off 21:51:36, on 21:51:37, off 21:51:40. That is 45 of 210 sweeps.

**What lines up:**

- **Rising edge.** The radiator fan load (ELD ~+7 A, 21:50:36-21:50:51) dropped off at 21:50:52. rpm overshot the 1,014 target by +50/+55 at 21:50:52-53. b0 came on in the next sweep (~2 s after the load dropped).
- **Also near the rising edge.** The accepted ECT had just settled out of a false-cold stretch: readings of ≥ 120 raw, which the ECU ignores, until 21:50:47, then 65-69 raw from 21:50:48.
- **While on:** rpm 998-1,074, target 1,014-1,068 (it followed the accepted ECT, raw 66-74), iac_cmd 92-97, IACV 21-23, ELD 8.5-10.8 A, closed loop, 0x2F 0, 0x2A 6. Nothing else moved: no other bit, and no byte changed at either edge beyond normal noise (`a4.py` diffs every byte).
- **Falling edge (21:51:40):** steady 1,014 target, ECT raw 66, no load change. The on time was 46 s, about the same as the 47 s from the target reaching 1,014 to the rising edge.

**What it is not:**

- not an rpm threshold: 49 sweeps at 1,010-1,070 rpm read 0;
- not a target threshold;
- not an ECT level.

The best guess is an idle-control timer state: a "load removed / overspeed" hold, or an ECT-accepted delay of ~45 s. **Low.** It needs 0xA4 in the read plan.

## 6. Dead slots

- **0x16**: 0 in 8,682 reads, drives 33-39. That covers key-on, crank, 5,400 rpm WOT, 107 km/h, decel cut and VTEC.
  - A secondary-O2 heater state would be a bit, and would be on while running.
  - The secondary O2 voltage is at 0xA0 (a real post-cat sensor at cruise).
  - So 0x16 is not the heater. It is probably an analog input not fitted on this variant (EGR lift exists only on the D16Y5).
- **0x0D, 0x0E**: 0 in 22,658 reads, including all of the above plus A/C, brake and PSP.
- **0x0B b2 and b7** (the literature's heater bits): never set.

Only a cold start (and a DTC, which is not to be provoked) has not been seen yet.

## 7. Physical tests that would settle each one

All of them assume the flag block is read fast (`08+10` at 5-10 Hz, or a dedicated `0B+2`), with the owner's time marks.

| item | test | expected result if the verdict is right |
|---|---|---|
| **0x0B b6** (which heater, and is it a heater) | Back-probe the primary HO2S heater control wire at the sensor connector or the ECM (the 2026-11-26 note gives ECM C1, BLK/WHT; check it against the manual) with a meter. Read it key-on/engine-off, then start, then do one 3rd-gear WOT pull past ~4,000 rpm. Repeat on the secondary heater wire. | The wire that reads ~battery voltage at key-on (heater off), ~0 V running (ECM grounding it), and back to battery voltage during the pull, in step with b6, is the heater b6 reports. The key-on/start part alone can be done in the driveway. |
| **0x0C b0** (ALTC) | Warm idle, parked: switch rear defogger + high beam + blower max on together, hold 10 s, all off, wait 20 s; repeat 5 times. If possible, put a meter with min/max capture (or a scope) on the alternator's C (ALTC) wire. | b0 drops to 0 for ~0.1-0.5 s at each switch-on, with a battery dip and a delayed ALT FR rise, and the C wire changes state at the same moment. A drop in fast reads without any C-wire change would make it a "load-step detected" flag instead. |
| **0x2F** (purge) | Cruise steadily at ~2,500 rpm/50-60 kPa with ECT reading above 70 °C, then clamp the purge hose between the PCS valve and the intake for ~60 s (soft clamp, then release). Best right after refuelling. | 0x2F keeps ramping as before (it is the command), but STFT no longer falls below its value at 0x2F = 0; after release STFT drops again within a few seconds. |
| **0x2A** (learned) | Non-destructive: after the ECT connector repair, 15 min fully warm idle, then headlights on for 2 min, off for 2 min. Destructive but decisive (needs the owner's OK): pull the BACK UP fuse for 30 s (this also clears LTFT, any codes and the readiness monitors). | Lights on: iac_cmd rises and 0x2A climbs a step every ~10-20 s; lights off: it comes back down. After a key-off it holds. After the fuse pull it restarts at a fixed default and moves only at fully warm idle. |
| **0xA4 b0** | Add 0xA4 to the slow plan (one byte). At warm idle: headlights on for 20 s and off, then wait 60 s; next a 2-s throttle blip to ~1,500 rpm and wait 60 s. | If it is a load-removed/overspeed hold, b0 comes on ~2 s after the lights go off (or after the blip) and goes off ~45 s later. If it tracks the ECT acceptance instead, it will only move after a false-cold stretch ends. |
| **0x0C b3** (VTEC pressure switch) | One 2nd-gear WOT pull to ~5,800 rpm with the fast flag read. | b3 follows 0x0A b2 with a short lag (oil-pressure delay) on and off. |
| **0x16, 0x0D, 0x0E** | One cold start from key-on with the present read plan. | Still 0: drop them from the read plan. |

## 8. Recommended map changes (not applied)

| name | addr / bit | formula | status / note |
|---|---|---|---|
| `purge_duty` (was `raw_2f` / `mon_2f`) | 0x2F | `x` | unverified, medium-high. "EVAP purge command. Enabled at ECT reading ≥ ~69 °C (off ≤ 65-66 °C); ramps with airflow; 0 in decel cut and on throttle moves; STFT −14 pts per 100 counts." Read it faster than 1 Hz while driving |
| `iac_learn` (was `raw_2a`) | 0x2A | `x` | unverified, medium-high. "Learned idle air, 0-6; tracks iac_cmd (τ ~40 s) only at fully warm idle (ECT raw ≤ 38); survives key-off" |
| `o2_heater` (was "engine running / 2nd O2 heater") | 0x0B b6 | `bit(x, 6)` | unverified. "O2 heater output: off with the engine stopped; off above ~3,500 rpm at MAP ≥ 84 kPa; primary or secondary unknown." Not an engine-running signal |
| `altc` (new) | 0x0C b0 | `bit(x, 0)` | unverified. "Alternator control, 1 = normal; drops for ~0.1 s at large load steps at idle" |
| `vtec_psw` (new) | 0x0C b3 | `bit(x, 3)` | unverified (n = 1) |
| `xa4_b0` (new, needs 0xA4 in the plan) | 0xA4 b0 | `bit(x, 0)` | unknown |
| `fan` | 0x0C b1 | – | remove (wrong; never set) |
| 0x16, 0x0D, 0x0E | – | – | drop after the cold-start log |
