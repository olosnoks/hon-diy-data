# 0x27 and 0x3C: are they table indices?

Date: 2026-11-27. ECU: stock P2P (2000 Civic EX, D16Y8, manual).

**Short answer: neither byte is a fuel or ignition map index, and they do not address one map cell together.**

- **0x3C** is a noisy level that rises with rpm. It is not a step function of rpm: at any given rpm several levels occur, and consecutive reads are independent of each other. It does not depend on load. It reads the same in fuel cut, with no combustion, as when fuelled. That is the signature of the **knock-sensor background-noise level**. It is not a column index.
- **0x27** is an **ignition-correction or limit value in 1/4° counts**. Ignition advance rises about 0.23° per count at a fixed rpm and MAP. It has three regimes:
  1. Below 41 kPa, an exact rpm quantiser from 0 to 4 (thresholds every 58.7 rpm from 781 rpm, saturating at 1,016 rpm).
  2. From 41 to 50 kPa, a MAP ramp.
  3. Above about 50 kPa, a slowly creeping state that the ECU remembers. It drops on fast rpm rises and at WOT at high rpm, and climbs back at about 0.2-0.7 counts/s.

  This behaves like knock-control timing recovery. It is not a cell index.

## Data and method

- I worked on a copy of `C:\Users\skons\hondalog\hondalog.db` in `work-index\hondalog.db`, reading the raw bytes from `samples.raw`: 0x27 is byte 0 of the `27+14` stream and 0x3C is byte 5 of the `37+6` stream.
- I used 34 drives, about 24,500 reads of 0x27 and 23,300 reads of 0x3C. I excluded `drive_2026_11_25_1738`, whose bytes at these offsets are a different read layout (0x27 = 88-168, 0x3C = 0).
- Each slow read is timed at `t − latency`. I interpolated rpm, MAP, TPS, ign, VSS and ECT (about 12 Hz) to that instant, and also took the rpm range over ±0.15 s.
- Only drives 2224 and 2251 go above about 2,300 rpm or 60 kPa. Above 3,500 rpm there are fewer than 40 reads of each byte.
- Scripts: `load.py` (writes `D.pkl`), `build.py` (writes `S.pkl`), `lag3c.py`, `fit3c.py` and `plots.py`. They run in the work folder's own `venv\`, which has numpy, pandas, scipy, matplotlib and pypdf.

## 0x3C

### Fitted "breakpoints"

A deterministic step model does not fit. Even the best monotone threshold model misclassifies **19%** of reads.

- **Lag does not help:** first-order lags of 0.1-4 s, window averages and ±1 s time shifts all leave 19-24% misclassified.
- **Reads are independent:** at idle the value 2 appears as isolated single reads (271 runs of length 1, 6 of length 2 and 1 of length 3). Its probability rises smoothly from 0% at 650 rpm to 3-6% at 900-950 rpm.
- **No memory:** the lag-1 autocorrelation of the residual after removing the rpm mean is 0.01 at idle and 0.05 above 1,600 rpm.

The best description is therefore the rpm at which P(0x3C > k) = 50%, from a logistic fit per boundary. The width column is the 10-90% transition width.

| boundary | rpm at 50% | 10-90% width (rpm) | reads above k |
|---|---|---|---|
| 1→2 | 1,734 | 980 | 1,579 |
| 2→3 | 2,410 | 600 | 432 |
| 3→4 | 2,887 | 560 | 118 |
| 4→5 | 3,331 | 560 | 44 |
| 5→6 | 3,645 | 490 | 26 |
| 6→7 | 3,846 | 440 | 18 |
| 7→8 | 4,258 | 530 | 10 |
| 8→9 | ~4,400 | ? | 8 |
| 9→10 | ~4,800 | ? | 4 |
| 10→11 | ~5,200 | ? | 2 |

- **No hysteresis was found.** The noise is far larger than any hysteresis could be.
- **Mean level:** `E[0x3C] ≈ 1 + 0.104·(rpm/1000)^2.91`, with rms 0.2 at idle. It grows roughly with the cube of rpm.
- **Load independence:**
  - 1,600-2,400 rpm: fuel cut (MAP < 20 kPa) mean 1.74, loaded (MAP > 50 kPa) mean 1.81.
  - 2,400-3,200 rpm: fuel cut 3.15, loaded 3.01.
  - At a fixed rpm, P(higher level) is flat across MAP from 10 to 95 kPa.

Plot: `work-index\01_x3c_vs_rpm.png`.

### What 0x3C is

It is a mechanical-noise amplitude: it rises steeply with rpm, ignores load, does not care whether combustion is happening, and each read is an independent sample. That fits the **knock-sensor signal level** (one sample of the ECU's KS window integrator or its noise floor). The community slot name "knock" (kerpz, HOPE, hondamotor) is correct; only the voltage formula (x/51 V) is unverified.

- **Not a column index:** an rpm index would be exact and repeatable.
- **Not a VTEC selector:** it has no special behaviour near VTEC rpm. The data there are too thin to look closer.
- **Weak link to knock retard:** at reads where 0x27 fell by 5 or more under load, 0x3C was +0.22 above its rpm mean, against −0.12 when 0x27 held. At 1 Hz this is suggestive only.

## 0x27

### Regime 1: MAP below 41 kPa (idle, cruise, decel), an exact rpm quantiser

| 0x27 | rpm range |
|---|---|
| 0 | < 839.5 |
| 1 | 839.5 - 898.5 |
| 2 | 898.5 - 957 |
| 3 | 957 - 1,016 |
| 4 | ≥ 1,016 (any rpm up to 5,000+) |

- **Thresholds** are ±1-2 rpm, found from reads where rpm was steady within 2.5 rpm. They follow `781 + 58.7·n` for n = 1..4, so `0x27 = clip(floor((rpm − 781)/58.7), 0, 4)`.
- **Accuracy:** this formula matches 99.1% of 22,734 reads below 40 kPa. Every miss is within a few rpm of a threshold, which is read-timing error.
- **No hysteresis:** the thresholds are the same whether the previous read was lower or higher.
- **No ECT dependence:** thresholds were 837-840 / 896-899 / 958-960 / 1,012-1,015 in every ECT band from below 50 °C to 85 °C and above. They are the same at standstill and while rolling.
- **Possible form:** 781.25 and 1,015.6 rpm are 50 and 65 × 15.625 rpm, and the step is 234.4/4. So this regime may be a quarter-step fraction between two rpm breakpoints at 781 and 1,016 rpm. That is a hypothesis, not proved.
- **Engine-off codes:** 5 at key-on with rpm 0 (14 of 15 reads), and 10 while cranking at 50-500 rpm (2 of 2 reads, plus 10-25 in the 2026-11-25 logs).

Plot: `work-index\02_x27_idle_band.png`.

### Regime 2: 41-50 kPa, a MAP ramp

Above 1,016 rpm the value leaves 4 at about 41 kPa. The median is about `4 + 1.3·(MAP − 41)`: 5 at 42, 8 at 44, 10 at 45, 11-12 at 47-48 kPa, with a spread of ±2 that is mostly timing. In this band the value is not correlated with the level the byte reaches under heavier load (r = −0.25 to 0.01 at 44-48 kPa).

Below 1,016 rpm, the same MAP gives a lower value. At about 750 rpm: 3 at 44 kPa, 8.5 at 52 kPa, 13.5 at 56 kPa.

### Regime 3: above about 50 kPa, a remembered state, not a table

- **It changes at constant conditions.** At steady 1,930 rpm and 62 kPa it reads 12, 12, 12, 12, 13…. At steady 1,870-1,970 rpm and 70 kPa it climbs 14→15→16→16→17→17→18 over 6 s. At 2,000-2,075 rpm and 57 kPa it climbs 18→23 over 8 s.
- **It is remembered across light-load gaps.** It shows 4 when MAP dips below 41 kPa and returns to the old value afterwards (18 → 4 → 18 at 1841-1843 s). Across 53 such gaps of under 20 s, the value before against the value after gives r = 0.81, with a median change of 2.
- **It drops on fast rpm rises.** Drops of 5 or more under load happen with a median rpm rise of 286 rpm/s within the read interval. Reads that hold steady have a median rise of 18. It falls to 0 at WOT around 2,700-4,300 rpm.
- **It recovers slowly.** At steady rpm the step per read is mostly 0 or +1 (102 reads at 0, 60 at +1, 17 at +2). Recovery is fastest at 1,000-1,500 rpm (+1.4 per read) and roughly flat at 2,500 rpm.
- **Range:** 0-35 seen. Median 15-17 at 1,000-2,500 rpm, falling to 6-11 above 2,750 rpm.
- **Not a static table:** a static fit `16.7 + 0.166·MAP − 5.2·rpm/1000` leaves rms 5.7, against an sd of 6.5. An AR(1) model with the previous read, MAP, rpm and drpm still leaves rms 4.2.

Plots: `work-index\03_x27_vs_map.png` and `work-index\04_x27_timeseries_2251.png`.

### The ignition link

For loaded, warm, rolling reads (MAP > 46 kPa, 1,300-3,200 rpm, n = 508), I fitted ign to a cubic in (rpm, MAP). Adding 0x27 to that fit:

| reads | rms without 0x27 | rms with 0x27 | 0x27 coefficient |
|---|---|---|---|
| loaded (n = 508) | 1.52° | 0.94° | **+0.228°/count** |
| steady only, \|drpm\| < 40 (n = 302) | 1.08° | 0.70° | **+0.208°/count** |

Adding drpm changes nothing. Within single cells the same holds: at 1,800-2,100 rpm and 53-60 kPa, ign goes from 21.0° to 24.4° as 0x27 goes from about 10 to 24. A slope a little under 0.25 is what errors in the 1 Hz reads would produce if the true step is **0.25° per count**. That matches the quarter-degree scaling in kerpz's "ignition limit" formula `(x−24)/4`. The offset is not established: 4 is the neutral value in the light-load region, and 24 is kerpz's zero.

Plot: `work-index\05_ign_vs_x27.png`.

### What 0x27 is

0x27 is the ignition correction (or the advance limit) that the ECU applies, in 1/4° counts.

- **Light load:** a fixed schedule, with the idle rpm sub-mode 0-4 and the constant 4 off idle. Knock control is off there.
- **Above about 41 kPa:** a MAP-ramped allowance that is capped by a learned state. That state is cut on transients and high load and climbs back slowly. This is the textbook pattern of knock-control retard and advance recovery, which fits the KS level in 0x3C.

A model consistent with the data is `0x27 ≈ min(R(rpm) + ramp(MAP), K)`. Here R is the idle band, ramp is 0 below 41 kPa and about 1.3·(MAP − 41) above it, and K is the hidden knock state. Confidence: high for the 1/4° ignition link and the regime structure, medium for the knock-control reading. Its rpm and MAP thresholds (781 + 58.7n rpm; 41 kPa) are not spaced like a map's axis breakpoints.

## Comparison with Honda map axes

- **Typical layout (from memory, not verified):** P28/P30/P72-family fuel and ignition maps are about 20 rpm breakpoints × 10 MAP breakpoints. The rpm breakpoints are non-uniform, roughly 400-500 to 8,000 rpm and a few hundred rpm apart. The MAP breakpoints run from about 20 to 100 kPa in steps of about 8-10 kPa. Low-cam and high-cam maps are separate, and VTEC selects between them. Neither the project's tuning definitions (`hondalog\tuning\data\demo.toml` is a synthetic demo) nor a quick web search gave real P2P or P28 axis values.
- **0x3C:** 11 levels spread from 1,700 to 5,200 rpm, cubic in spacing, noisy and identical in fuel cut. A map column index would be exact, repeatable and roughly linear in rpm. It is not a column index.
- **0x27 regime 1:** 58.7 rpm steps between 781 and 1,016 rpm. That is far finer than any map axis. At most it is a quarter-step interpolation fraction within one rpm segment (781→1,016), used only at light load.
- **0x27 regimes 2 and 3:** a stateful value that changes at a constant (rpm, MAP). It cannot be a row index.
- **Not a VTEC cam selector:** 0x27 takes 0-35, not two states, and 0x3C is noise.
- **No joint cell address:** 0x3C × 0x27 does not address one cell. One is a sensor level and the other an ignition correction, and their joint distribution is not a grid.

## Proposed channels

I would **not** add `rpm_col`, `map_row` or `ign_map_cell` decoded from these bytes. Compute cell indices from rpm and MAP once a real P2P axis definition exists. What these bytes support:

| channel | formula | units | notes |
|---|---|---|---|
| `ks_level` (rename of `knock`) | `x` (raw 0x3C) | counts | knock-sensor noise level; 1 Hz |
| `ks_excess` | `x − (1 + 0.104·(rpm/1000)^2.91)` | counts | how far above the rpm-expected noise; possible knock indicator. Use rpm at the read time |
| `ign_corr` (rename of `ign_limit`) | `x` (raw 0x27) | counts | keep raw; codes 5 = key-on, 10 = cranking |
| `ign_corr_deg` | `(x − 4) · 0.25` | ° (+ = advance) | relative to the light-load neutral value 4. Valid when rpm > 1,016 or MAP > 41; at idle it is the 0-4 band (−1…0°) |
| `knock_ctl_region` | `map > 41 and rpm > 1016` | bool | where 0x27 carries the learned state |
| `ign_retard_event` | `prev(ign_corr) − ign_corr ≥ 5 and map > 44` | event | timing pulled; for alarms and markers |

What they are useful for:

- **Map 3D screen:** a per-(rpm, MAP)-cell mean or minimum of `ign_corr_deg` and a count of `ign_retard_event` (the knock-map binning in `hondalog\tuning\binning.py:knock_map` already does this) show where the ECU pulls timing: tip-ins at 1,800-3,000 rpm and WOT above 2,700 rpm in these logs. A per-cell mean of `ks_excess` would be a cross-check.
- **Fuel quality and octane:** a low `ign_corr` that persists at cruise load means the ECU is holding timing back.
- **Idle:** the 0-4 band duplicates rpm and adds nothing.

## Caveats

- Both bytes are read at about 1 Hz. Transitions are located only to about ±0.5 s, and a slope from errors in the reads understates the per-count gain.
- High-rpm, high-load coverage is two drives only, with fewer than 40 reads above 3,500 rpm.
- The knock-control reading of 0x27 would be settled by a ROM disassembly (the slot table points at named RAM), or by a deliberate test: a hard pull on lower-octane fuel, logging 0x27 and 0x3C as fast as the link allows.

Sources searched for axis layouts: [TunerPro forum Honda XDF thread](https://www.tunerpro.net/forum3/viewtopic.php?p=5104), [Crome manual](https://ej9.ru/data/book/crome-v1.6.x-manual-pro-dealer.pdf). Neither gave breakpoint values.
