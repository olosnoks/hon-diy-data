# Unknown bytes and flag bits after the first real drives (2026-11-26)

**Data.** A read-only copy of `hondalog.db`, taken 23:4x, is in `work-drivebytes/` (git-ignored). Only the raw bytes in `ecu_reads` were used. There was no `-wal` or `-shm` file to copy.

The two drives:

- `drive_2026_11_26_2224` (id 38): 20.8 min, up to 69 km/h and 3,318 rpm.
- `drive_2026_11_26_2251` (id 39): 35.8 min, including interstate, up to 107 km/h and 5,411 rpm.

**Read rates and samples.**

- Slow blocks (`03+15`, `16+10`, `27+14`, `37+6`, `9A+7`): 0.95 Hz each, 3,198 reads. 2,018 of them were taken while moving.
- Fast blocks (`00+3`, `12+4`, `20+7`): 11.4 Hz, 38,489 reads.
- Idle target (`76+2`): 4.7 Hz.

**More bytes are read than the brief lists.** The raw replies also contain 0x33 (in `27+14`), 0x38-0x3A (in `37+6`), and 0x9B and 0x9D-0x9F (in `9A+7`). They are included below.

**Reference.** I checked the ECM terminal tables in the service manual (`~/hondalog/manual`, PDF pp. 357-363, '99-00 "except D16Y5 M/T" pinout) and the PGM-FI description (pp. 373-374). Those pages say:

- Outputs include VTS (VTEC solenoid), PO2SHTC/SO2SHTC (O2 heaters), PCS (EVAP purge, run above 68 °C), ALTC, FANC, IACV, ACC, FLR and MIL.
- Inputs include VTM (VTEC pressure switch), PSPSW, BKSW, STS, ACS, ALTF, ELD and KS (knock sensor).
- EGR is D16Y5-only.
- **No clutch switch and no gear-position input exist on this ECM version.** CLSW is listed only for the D16Y5 M/T.
- Decel fuel cut on the USA D16Y8 M/T is above 920 rpm. The rev limit is 7,000 rpm.

Scripts are in `work-drivebytes/`. Run them in this order: `load.py`, `survey.py`, `live.py` (writes `SF2.pkl`), then `t27*.py`, `p2f.py`, `misc*.py`, `psp.py`, `dash.py` and `plots.py`. They use `venv/`, which has pandas and matplotlib. Plots: `01_drive2251_overview.png`, `02_0x3C_0x27_0x2F.png`.

## 0. Bottom line

- **Most of the unknown block is still dead after 57 min of real driving:** WOT, 5th-gear interstate, decel cuts, braking and parking manoeuvres. All of these are still 0 in every read:
  - 0x03-0x07, 0x09, 0x0D, 0x0E, 0x16, 0x1A, 0x1B, 0x1D-0x1F
  - 0x21, 0x23
  - 0x2B-0x2E, 0x30, 0x31, 0x34, 0x3B
  - 0x9B, 0x9D-0x9F
- **Constants:** 0x33 = 55 and 0x38 = 0x39 = 25. 0x3A = 25, except 24 for the first 8 s after the start.
- **Came alive, or changed character:**
  - 0x0A b2 (VTEC) together with the new **0x0C b3**
  - **0x0B b6**: off at high load and above ~3,500 rpm, so it is *not* "engine turning"
  - 0x08 b2 (PSP, now polarity-proven)
  - 0x08 b3 (brake)
  - 0x0F b0 and 0x9A (both open-loop kinds)
  - **0x2F**: a fast airflow-driven ramp that pulls STFT down, most likely the EVAP purge duty
  - **0x27**: an rpm x MAP index, not just an idle rpm band
  - **0x28/0x29**: open with throttle off idle, with a dashpot decay after the car stops
  - **0x3C**: a pure rpm index, 1 to 11
  - 0xA0: behaves like a real downstream O2 at cruise
- **Gear and clutch: there is no such signal.** No byte or bit separates gears, or neutral from in-gear, beyond rpm/vss. The manual shows no clutch or gear input on this ECM. 0x08 b7 stayed 1 in every gear and through every clutch take-up.
- **No byte is derived from vehicle speed** other than vss itself (0x02, within 1.5 % of GPS).
- **No knock-retard, ignition-table or fuel-table index was found**, beyond what 0x27 and 0x3C might be.
- **VTEC: the car did get there, once.** In the 23:03:09-11 2nd-gear WOT pull, the one slow read at 23:03:11.70 (5,376 rpm, MAP 98 kPa) has **0x0A b2 = 1 and 0x0C b3 = 1**. They were never set in any other read, idle logs included. The read before (23:03:10.61, 4,786 rpm) had both at 0. So VTEC engaged between 4,786 and 5,376 rpm. That is lower than the 5,600-5,800 rpm in the brief, but consistent with the manual's VTS check ("battery voltage above 5,000 rpm"). The peak was 5,411 rpm at 23:03:11.86, after the throttle was already lifted. The 22:54:37 pull peaked at 5,037 rpm with no slow read near the peak.

## 1. Gear inference (and a correction to the brief's ratios)

vss (0x02) agrees with GPS speed to a median ratio of 0.985 (n = 1,766 reads above 10 km/h), so vss is right.

The brief's formula is `ratio x 4.06 x 1000 / (60 x pi x 0.586) = ratio x 36.76` rpm per km/h. It gives 119.5, 64.7, 43.0, 33.8 and 26.1 for 1st-5th. **The data clusters do not sit there.** The fast reads (vss ≥ 20, rpm > 1,100, throttle open) peak at:

| gear | rpm per km/h, measured (median of cluster) | brief's formula | implied overall ratio (gear x final, 0.586 m tyre) | implied gear ratio if final = 4.06 |
|---|---|---|---|---|
| 1 | 133.6 (n = 53) | 119.5 | 14.75 | 3.63 |
| 2 | 76.8 (n = 2,397) | 64.7 | 8.48 | 2.09 |
| 3 | 49.7 (n = 4,438) | 43.0 | 5.48 | 1.35 |
| 4 | 36.0 (n = 3,866) | 33.8 | 3.97 | 0.98 |
| 5 | 27.7 (n = 2,744) | 26.1 | 3.06 | 0.75 |

The steps between gears are 1.74 / 1.55 / 1.38 / 1.30. The brief's set gives 1.85 / 1.50 / 1.27 / 1.30, so this is not a tyre-size error: the gearbox and final drive on this car differ from the brief's numbers.

For the gear labels I used the measured bands: 5th 25-31, 4th 32.5-40, 3rd 45-56, 2nd 68-88, 1st 115-155 rpm/(km/h), with vss ≥ 8. Anything else counts as clutch-in, shifting or coasting. No byte depends on the gear once rpm, MAP and TPS are known.

## 2. Byte by byte

### Still dead (0 or constant in all 3,198 slow reads)

| byte(s) | value | note |
|---|---|---|
| 0x03-0x07 | 0 | |
| 0x09 (all bits) | 0 | literature b3 = SCS; never jumped |
| 0x0D, 0x0E (all bits) | 0 | 0x0D b3 = engine-mount solenoid on the P2T; none here |
| 0x16, 0x1A, 0x1B, 0x1D, 0x1E, 0x1F | 0 | |
| 0x21, 0x23 | 0 | neighbours of LTFT |
| 0x2B, 0x2C, 0x2D, 0x2E, 0x30, 0x31, 0x34 | 0 | |
| 0x3B | 0 | |
| 0x9B, 0x9D, 0x9E, 0x9F | 0 | OBD2-copy block neighbours |
| 0x33 | 55 | constant |
| 0x38, 0x39 | 25 | constant |
| 0x3A | 25; **24 for the first 8 reads after the 22:51:09 start** (22:51:10-17), together with 0x32 = 44 in the first read | an after-start detail; otherwise constant |
| 0x1C | = 0x19 (ELD) in 3,198 of 3,198 reads | a copy, as at idle |
| 0x13 (baro) | 147 (146 once) | latched |

Nothing in this list moved with WOT, decel cut, braking, 5th gear, highway speed, ECT glitches, the start or the key-off.

### Bytes that are alive

| byte | what it did while driving | verdict |
|---|---|---|
| **0x27** | At idle it is the known rpm band (0 below ~845 rpm, 4 from ~1,010 rpm). **Off idle with MAP below ~40-42 kPa it is exactly 4** (1,330 reads, any rpm from 1,000 to 3,000+, fuel cut included). **Above ~42 kPa it rises steeply**: ~7-9 at 45 kPa, 10-13 at 48 kPa, 14-16 at 51 kPa, ~19 at 55-60 kPa, scattered 8-35 above (`02_...png`, middle). Lug-type tip-ins (1,000-1,800 rpm, MAP 54-64 kPa) give 22-26. Above ~3,500 rpm at WOT it scatters 0-14. 10 at the 22:51:09 cranking read (starter bit set, inj 11.9 ms), as in the idle logs (5 at key-on, 10 cranking). | **An rpm x MAP table index or table value**: the rpm band at idle, a MAP term above ~40 kPa. **Not an ignition retard**: adding it to an ign ~ MAP + MAP² + rpm fit (1,500-2,600 rpm, closed loop) lowers rms only from 1.74° to 1.69°, and its coefficient is +0.09°/count (advance, not retard). Not a gear signal. Low confidence on the name. |
| **0x28 / 0x29** (iacv / iac_cmd) | **Off idle the IAC opens with the throttle**: 0x28 tracks TPS (Spearman 0.92), then MAP (0.83) and rpm. It goes 30-40 at light cruise, 100-140 at part throttle, ~200 (saturated) at WOT. 0x29 stays on the same curve as at idle (0x28 200 → 0x29 214-217, 155 → 187, 105 → 157, 75 → 135, 33 → 103). With the throttle closed: **24-29 while rolling without fuel cut, 17-18 in decel fuel cut, ~15 when stopped**. **Dashpot:** after the car stops with the throttle closed, 0x28 decays 20 → 17 → 15 → 8 → 6-7 over ~12-20 s (median of 30 stops), while rpm falls 1,090 → ~860 to the target. Parking-lot steering load (PSP, 23:25:55) lifted it 33 → 47. | **IAC command, including the off-idle throttle-follower and the dashpot.** Same quantity in two forms (0x29 = fixed curve of 0x28). Medium-high |
| **0x2A** | 4 → 3 at 22:31:13 (idle), then **3 for the entire 50 min of driving** | idle-air learn, **frozen off idle** (as expected; ECT never read fully warm, raw 41-72 while moving) |
| **0x2F** | **New behaviour while driving.** It restarts at 19-20 whenever the throttle settles, then climbs at a rate that grows with airflow: about +1/s at 1,000-1,500 rpm and 25-40 kPa, +4-8/s at 2,000-2,500 rpm and 55-75 kPa, +10-30/s at 2,500-3,500 rpm and 75-90 kPa. Highest values: 206 (22:54:35), **255** for 3 reads (23:11:58-23:12:00) and 210-212 holding at a steady 90 km/h (23:23). It drifts down at light load (76 → 72 at 30 kPa). **Cleared to 0 by** 56 throttle changes, 10 decel cuts and 20 others (most look like a throttle move between 1 Hz reads, one an ECT glitch to raw 63). At a standstill it shows the known slow ~31 s ticker (1, 4, 6, 9 ... to ~25). **STFT falls as 0x2F rises, at the same MAP**: median STFT raw 133-134 (+4 %) with 0x2F = 0, against 102-114 (−11 to −20 %) with 0x2F > 120 (40-60 kPa band), Spearman −0.74 on highway closed loop. | **Most likely the EVAP purge duty/ramp (PCS).** The manual's purge enable is ECT above 68 °C, which matches the idle-log enable at accepted ECT ≥ 69 °C and the reset at ≤ 65-67 °C. Purge is cut in decel and on transients and ramps with airflow, and the extra vapour fuel is what makes STFT go negative. Medium. Alternative: a purge-concentration learn. |
| **0x32, 0x37** | 45 with one-read 44s, as at idle. 0x32 dips in 3-6 % of reads at any rpm. 0x37 dips in 7-9 % below 1,500 rpm but only 0.7-1.5 % above 2,500 rpm. | still a task or timer counter; nothing to name |
| **0x3C** (mapped "knock") | **A step function of rpm only**: 1 up to ~1,700, 1-2 at 1,700-2,500, 3 at ~2,500, 4 at ~2,800-3,000, 5-6 at ~3,400-3,900, 7-11 at 4,000-5,400. Bands overlap by a few hundred rpm (filtered or hysteretic). Spearman 0.78 with rpm; at a given rpm nearly flat in MAP. The idle "1/2 blips" are the lower edge of the same thing. | An rpm-indexed value. The manual says the D16Y8 **does** have a knock sensor and knock control (p. 373, footnote 3; KS terminal on the '99-00 ECM). So "knock-sensor background-noise level", which rises with engine speed, is a candidate, but it could equally be an rpm-axis table index. Not a knock voltage, not knock retard (nothing jumps at WOT). Low |
| **0x9A** (fuel_status) | 1 for the first 14 s after the 22:51 start, 2 in closed loop, **4 in decel fuel cut and in WOT enrichment** | **OBD2 PID 03 encoding confirmed**: 1 = open loop, conditions not met; 2 = closed; 4 = open loop due to driving conditions. High |
| **0x9C** (load_ecu) | 32-249 (12.5-98 %); 98 % at WOT | consistent with PID 04 |
| **0xA0** (o2_2) | 201 (3.9 V, a start-up default) for the first 14 s after the start. At idle 50 (0.97 V). **At highway closed loop: median 0.74 V (IQR 0.53-0.84), and r = 0.11 with the front sensor at any lag** (the front sensor switches, 80 % of reads above 0.45 V). Fuel cut median 0.02 V. | **Behaves like a real post-cat sensor**: rich-biased, steadier than the front, empties lean in fuel cut. The idle-sweep concern (it followed the front within 1 s) does not hold at cruise. Upgrade to plausible-high |
| **0x76:77** (idle_target) | Still set by ECT while driving: mid-drive ECT glitches to raw 63-68 lifted it to 960-1,014 rpm at 40 km/h (22:41:07, 22:42:50, 23:06:35). **At ECT raw ≤ 50 it reads 749.75 while rolling and 809.75 when stopped** (58 against 202 reads at raw ≤ 47): it dropped 810 → 750 at the 22:39:51 pull-away, and the home stops at 23:25-23:26 were back at 810 | ECT-driven target with a **vss-dependent offset: −60 rpm (4-5 steps) while moving**, or a stopped floor of ~810 at low ECT raw. Low-medium; needs a fully warm drive |

### Flag bits

| bit | while driving | verdict |
|---|---|---|
| 0x08 b0 | 1 in the first read of the 22:51:09 start only | `starter`, confirmed again |
| 0x08 b1 | never (A/C was off) | `ac_sw`, untested while driving |
| **0x08 b2** | **0 in 18 reads, all low-speed turning or parking.** Six reads at 0-21 km/h during junction turns (GPS yaw 17-54°/s within ±3 s). Seven reads at 23:25:54-23:26:10 parking at home (rpm dipped to 521 at 23:25:56). One at a stop (22:33:21, with a 773 rpm dip). **Never 0 with yaw below 10°/s** (171 low-speed reads). At 1-25 km/h, 4 % of reads are 0 when yaw > 20°/s, 0 % when yaw < 10°/s. | **PSP switch, polarity proven: 0 = high P/S pressure (steering loaded).** Name it `psp` with formula `1 - bit(x, 2)`. High |
| **0x08 b3** | set in 16 % of reads. 97 % of reads decelerating harder than 4 km/h/s, 59 % at 1.5-4, 2 % at constant speed, 0.3 % accelerating | `brake`, **verified** |
| 0x08 b4-b6 | never | unknown |
| 0x08 b7 | 1 in every read: all gears, neutral, every clutch take-up and pull-away | **not a clutch or gear input** (this ECM has none). Probably the A/T P/N input, fixed on the M/T. Drop from consideration |
| **0x0A b2** | **1 in one read: 23:03:11.70, 5,376 rpm, 2nd gear, WOT (TPS 197, MAP 98 kPa)** | `vtec`, **seen once**: VTS solenoid on. Medium |
| 0x0A b4, b7 | always 1 | unknown, `ecu_ready` |
| 0x0A others | never | |
| 0x0B b0 | always 1 when running; 0 in the first cranking read 22:51:09 | `fuel_pump` |
| 0x0B b1 | never (A/C off) | `ac_clutch` |
| 0x0B b5 | never (MIL off) | `mil` |
| **0x0B b6** | **0 in 26 reads, all at high load and high rpm**, plus the 22:51:09 starter read (rpm still 0; the idle logs had it 1 from the first read with crank pulses, 202 rpm). It goes 1 → 0 when rpm reaches about 3,550-4,150 with MAP ≥ 84 kPa (22:54:35, 23:03:09, 23:12:47, 23:20:53 ...). It comes back when the throttle closes or rpm falls below ~3,350-3,450 (hysteresis). It stayed 1 at 3,801 rpm/73 kPa, 3,531 rpm/74 kPa and 3,227 rpm/95 kPa. It was set while the fuel was cut and while enrichment ran, so it is not the open-loop flag. Battery 0x17 differs by only 1 count (146 vs 147) and ALT FR not at all, so there is no clear alternator effect. | **"Engine turning" is wrong.** An ECM output that is on whenever the engine runs and is switched off above ~3,500 rpm at high load. Best candidate: **primary HO2S heater (PO2SHTC)**, which Honda cuts at high exhaust load and switches on from cranking. ALTC is less likely: the manual says ALTC changes at light-load cruise, and no bit does. Medium on behaviour, low on name |
| 0x0B b7 | never | not the 2nd O2 heater, or never switched |
| 0x0C b0 | always 1 (no single-read drop this time) | as before |
| 0x0C b1 | never | `fan` stays wrong |
| **0x0C b3** | **1 in the same single read as 0x0A b2 (23:03:11.70, 5,376 rpm)**, never otherwise | **VTEC pressure switch (VTM)**: the oil-pressure feedback after the solenoid. Medium-low (one read, but two never-before-set bits flipping together at the highest rpm logged) |
| 0x0F b0 | 0 in 10.5 % of reads: the 14 s after the start, every decel fuel cut, **and WOT/high-load enrichment** (0x9A = 4 in both) | `closed_loop`, confirmed. Summary: "0 after start, in decel fuel cut and in power enrichment" |
| 0x0F b1-b7 | never | |

**Decel fuel cut has no dedicated flag.** It shows as inj = 0, with 0x0F b0 = 0, 0x9A = 4 and 0x0B b6 = 1 (b6 = 0 means power enrichment, unless the rpm is low). 0x28 reads 17-18. In the fast reads the cut starts at 1,524-3,458 rpm (median 2,136) and fuel returns at a median 1,135 rpm (10-90 %: 1,060-1,214; manual threshold: cut above 920 rpm). There were 2,781 cut reads; the lowest rpm in a cut was 1,028, and none happened at vss 0.

## 3. Rename candidates

| byte/bit | proposed name | formula | confidence | evidence (short) |
|---|---|---|---|---|
| 0x08 b2 | `psp` | `1 - bit(x, 2)` | high | 18 zeros, all turning or parking; none when straight |
| 0x08 b3 | `brake` (status verified) | `bit(x, 3)` | high | 97 % set below −4 km/h/s; 2 % at constant speed |
| 0x08 b7 | drop (not clutch or gear) | – | high (that it is not clutch) | constant in all gears and clutch states; manual: no CLSW on this ECM |
| 0x0A b2 | `vtec` (seen once) | `bit(x, 2)` | medium | 1 at 5,376 rpm WOT, 0 at 4,786 |
| 0x0B b6 | `o2_heater?` (was "engine turning") | `bit(x, 6)` | medium (behaviour), low (name) | off above ~3,500 rpm at MAP ≥ 84 kPa with hysteresis; on from crank |
| 0x0C b3 | `vtec_psw` | `bit(x, 3)` | medium-low | the same single read as VTEC |
| 0x0F b0 | `closed_loop` (new summary) | `bit(x, 0)` | high | 0 after start, in fuel cut and in WOT enrichment |
| 0x9A | `fuel_status` (status → verified) | `x` (1/2/4 = PID 03) | high | 1 after start, 2 closed, 4 fuel cut and WOT |
| 0x27 | `x27_rpm_map_idx` (was `ign_limit`) | `x` | low | idle: rpm band 0-4; off idle: 4 below ~42 kPa, then ~+0.85/kPa |
| 0x28 | `iacv` (note) | `x` (relative) | medium-high | follows TPS off idle to ~200; dashpot 20 → 6 in 15 s after a stop |
| 0x29 | `iac_cmd` (note) | `x` | medium-high | same curve of 0x28 while driving |
| 0x2A | `iac_learn` | `x` | medium | frozen at 3 for 50 min of driving |
| 0x2F | `purge_duty?` (was `raw_2f` / `mon_2f`) | `x` (0-255; `x / 2.55` % if it is a duty) | medium | ramps from 19-20 with airflow; 0 in decel and on throttle change; STFT −15 to −20 points at the same MAP; 68 °C enable matches PCS |
| 0x3C | `x3c_rpm_idx` (was `knock`) | `x` | low | 1-11, steps with rpm only; possibly the knock-sensor noise level |
| 0xA0 | `o2_2` (status → plausible-high) | `x / 51.3` V | medium-high | 0.74 V median at cruise, r 0.11 with the front, 0.02 V in cut |
| 0x76 | `idle_target` (note) | `x / 4` | medium | ECT-driven while moving too; −60 rpm while rolling at ECT raw ≤ 50 |

## 4. Read plan: what to drop

Measured cost on this link (drive 39, gap between consecutive replies): about **16 ms per request plus 0.7 ms per byte**. Trimming bytes inside a block saves little; dropping whole requests saves more.

Suggested slow plan, keeping every byte that moved:

| now | proposed | keeps | saves (per slow cycle) |
|---|---|---|---|
| `03+15` (0x03-0x11) | `08+10` (0x08-0x11) | flags, ECT, IAT | 5 bytes, ~4 ms |
| `16+10` (0x16-0x1F) | `17+3` (0x17-0x19) | batt, alt_fr, eld (0x1C is a copy) | 7 bytes, ~5 ms |
| `27+14` (0x27-0x34) | `27+9` (0x27-0x2F) | 0x27-0x2A, 0x2F (0x2B-0x2E ride along; 0x32/0x33 dropped) | 5 bytes, ~4 ms |
| `37+6` (0x37-0x3C) | drop, or `3C+1` | 0x3C only if wanted (it is rpm-redundant) | ~19 ms (drop) or ~2 ms |
| `9A+7` (0x9A-0xA0) | keep | 0x9A, 0x9C, 0xA0 | – |

Dropping `37+6` and trimming the rest cuts the slow cycle from ~115 ms to ~85 ms. That is about 30 ms a second of link time at the present 0.95 Hz slow rate: the slow blocks could run ~1.3 Hz with the fast rate unchanged, or the fast blocks gain ~3 %. Going to 2-3 Hz slow would also need the slow bytes merged into fewer requests (16 bytes per request is the most the sweep used).

**Do not drop before the cold start.** Hold on to:

- 0x03-0x07, 0x0D, 0x0E, 0x16, 0x1A, 0x1B, 0x1D-0x1F, 0x2B-0x2E, 0x30, 0x31, 0x34 and 0x3B: they have not yet been seen cold, with A/C on, or with a DTC set. One cold-start log with the present plan settles that.
- 0x3A: it moves only at the start.

## 5. What still needs a test

1. **VTEC pull, repeated with the flags read fast.** A 2nd- or 3rd-gear WOT pull to ~5,800 rpm on a closed road, with the `08+10` block at 5-10 Hz (or a dedicated `0A+3`). This gives the engagement rpm, whether 0x0C b3 follows 0x0A b2 with a short lag (pressure-switch delay), and whether it depends on load or vss.
2. **Cold start from key-on, then a drive.**
   - 0x2F should stay 0 until ECT ≥ 68 °C if it is purge.
   - 0x0B b6 at crank.
   - 0x9A = 1 duration and 0xA0 light-off.
   - 0x3A at the start.
   - 0x2A across a key cycle.
   - The dead bytes when cold.
3. **0x0B b6.**
   - Rev in neutral to 4,000-4,500 rpm (no load). If it stays 1, it is load-gated, which fits a heater or ALTC cut more than an rpm threshold.
   - Then one WOT pull with a meter on the primary HO2S heater feed (ECM C1, BLK/WHT; with the heater on the wire is near 0 V, with it off near battery voltage).
4. **Purge (0x2F).** While cruising steadily, pinch the purge line between the PCS valve and the intake. If STFT stops dropping as 0x2F climbs, it is purge. Or simply log a drive just after refuelling (more vapour, bigger STFT effect).
5. **A/C while driving.** 0x08 b1 and 0x0B b1 off idle, and whether the A/C clutch drops at WOT together with 0x0B b6.
6. **Fully warm drive after the ECT connector fix.** The idle target vs vss (the −60 rpm), and 0x2A learning at a warm idle after driving.

## 6. Recommended map changes (not applied)

- `psp`: formula `1 - bit(x, 2)`, status verified. Note: "0 = high P/S pressure; seen at junction turns and parking (23:25:55, rpm dip to 521)".
- `brake`: status verified.
- `vtec`: keep. Note: "seen 1 at 5,376 rpm WOT 2nd gear (2026-11-26 23:03:11)".
- New `vtec_psw`: 0x0C `bit(x, 3)`, unverified.
- 0x0B b6: rename from "engine running / O2 heater" to `o2_heater?` (unverified), with the high-load cut note. Do not use it as an engine-running signal.
- `closed_loop`: summary "0 for ~14 s after start, in decel fuel cut, in WOT enrichment".
- `fuel_status`: verified. Add value labels 1 = OL not ready, 2 = CL, 4 = OL driving.
- `ign_limit` → `x27_rpm_map_idx` (suspect as an ignition value). Note: "idle: rpm band 0-4; off idle: 4 below ~42 kPa, rising ~0.85/kPa above".
- `raw_2f` → `purge_duty?`, max 255, slow rate (it moves several counts a second while driving, so 1 Hz under-samples it).
- `knock` → `x3c_rpm_idx`, status suspect. Note: "1-11 with rpm; maybe the knock-sensor noise level (D16Y8 has KS)".
- `iacv` / `iac_cmd`: notes "off idle opens with TPS up to ~200 raw; dashpot decay ~15 s after a stop".
- `o2_2`: status plausible. Note: "rear O2: 0.74 V median at cruise, independent of the front, 0.02 V in fuel cut; 201 raw = start-up default".
- `raw_2a` → `iac_learn` (frozen off idle).
- `fan` (0x0C b1): remove. It was never set over the whole evening, including a drive, and the radiator fan state is not in 0x08-0x0F.
- Drop the dead raw channels and trim the slow blocks as in section 4, **after** one cold-start log.
- A math channel `gear` from rpm/vss with the measured bands (5: 25-31, 4: 32.5-40, 3: 45-56, 2: 68-88, 1: 115-155 rpm per km/h, vss ≥ 8). There is no ECU gear signal to read.
