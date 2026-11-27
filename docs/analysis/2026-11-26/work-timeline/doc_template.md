# Drive timeline, GPS track and data quality while driving (2026-11-26, first real drives)

**Car:** 2000 Civic EX, D16Y8, stock P2P ECU, manual.

**Drives:**
- `drive_2026_11_26_2224` (A, id 38)
- `drive_2026_11_26_2251` (B, id 39)

**Data:** a read-only copy of `~/hondalog/hondalog.db` taken at 23:33. No -wal/-shm files existed, because the app was closed. The copy and all scripts are in `work-timeline/`.

**Positions:**
- Positions are given only as a distance and compass bearing from **S**, the first GPS fix of drive A.
- Drive B ends 9 m from S, so S is the owner's home.
- This document has no coordinates and no addresses. The GPX files, `grid_*.npz` and `origin.npy` in `work-timeline/` do hold raw coordinates (see §7).

## 0. Bottom line

1. **Coverage.** There are 55.4 min of ECU data: 35.0 min moving, 24.6 km driven (vss, corrected).
   - **Drive A:** 16 min of idle at S, then 3.5 min of city and arterial driving (max 69 km/h) to the gas station 1.1 km NNE of S.
   - **Drive B:** 36 min.
     - City 15 min, arterial 13.5 min.
     - One interstate stretch of 2.4 min at 81-107 km/h.
     - 7 stop/launch cycles, then parking at home.
   - Every gear from 1 to 5 was used.
   - High rpm is barely covered: only ~14 s above 4,000 rpm. VTEC shows in exactly one 1 Hz sample (5,411 rpm at 23:03:11).
   - Not covered at all: A/C, radiator fan, rain/wipers and a cold start.
2. **Link quality has two regimes in drive B.**
   - **Restart to 23:10:02.** 18.9 min and ~49,000 reads, at speeds up to 78 km/h, with **zero link errors and zero read gaps**. The link fix works.
   - **23:10:02 to key-off.**
     - **104 link_error rows in 28 episodes (1.7/min).**
     - 15 read gaps over 0.5 s, including both long silences (5.7 s and 8.1 s).
   - The error rate does **not** depend on speed:
     - 0 episodes in 2 min at 90-107 km/h.
     - 1.3/min in town.
   - It does not depend on rpm, electrical load or battery voltage either.
   - About three quarters of the error spots are on roads that were driven without errors before 23:10.
3. **The likely cause is an intermittent contact at the OBD2 plug.** EMI and ECU load fit the evidence much worse.
   - Our own echo was always there while driving (never "no bytes at all"). So the adapter kept its power the whole time.
   - Damaged replies **stop partway through the frame**, and after that the line reads idle-high:
     - The last byte of a short reply averages 6.2 one-bits. A good byte averages 1.66.
     - Bit errors lean towards 0→1: 119 against 53.
   - The step change at 23:10 says that something moved. The plug had already been knocked out once, at 22:36.
   - **Check and secure the DLC plug: K-line pin 7 and ground pins 4/5.**
4. **vss calibration: at steady cruise, true (GPS) speed = 1.011 × vss.**
   - Median of 23 windows: 1.012 (p10-p90 1.007-1.017).
   - Distance cross-check: GPS 24.42 km against vss 24.07 km (×1.015).
   - So the ECU's vss reads about **1.1 % low**.
   - The GPS speed arrives **1.7 s later** than the matching ECU reading. Shift GPS by −1.7 s before overlaying channels.
5. **GPS quality.**
   - The handheld always had a fix, with 10-15 satellites and HDOP 0.8-1.4.
   - About every 2 minutes it drops 5-13 s of fixes. They are lost, not delayed: 41 gaps over 3 s in total.
   - Single missed seconds are common too, so the real rate is ~0.85 Hz.
   - Drive B has 2 corrupt positions (thousands of km off). They are filtered out here.

## 1. Segment timeline

**How the segments are made:**
- **Grid.** Everything is put on a 1 Hz grid: each second takes the median of the ~11 Hz rpm/vss reads in that second.
- **stop.** The 5 s median of vss is below 1. A stop of 60 s or more is labelled **idle/parked**.
- **launch.** The first ≤ 15 s after a stop, until 25 km/h.
- **decel.** The falling run of speed into a stop.
- **Cruise.** Labelled by the 31 s median speed:
  - **city:** below 45 km/h
  - **arterial:** 45-80 km/h
  - **interstate:** 80 km/h and up
- **Merging.** Runs shorter than 20 s are merged into their neighbour.
- **Gears.** A second counts for a gear when vss ≥ 8 km/h and rpm/vss is within ±5-10 % of that gear's cluster centre (§1.3).
- **km.** The vss integral × 1.011.
- **"from -> to".** The position at the start and at the end of the segment.

### 1.1 Drive A (22:24:37-22:45:25)

{{seg_A}}

**In short:**
- 16 min of warm idle at S, including the 55 s unplug.
- One city/arterial run, with a stop 0.5 km E of S.
- 2 min of idle at the gas station (1.12 km NNE of S), then key-off.

### 1.2 Drive B (22:51:09-23:26:58)

{{seg_B}}

**In short, by phase:**

| time | what happened |
|---|---|
| 22:51-22:53 | Restart, then 2 min of idle at the gas station. |
| 22:53-23:10 | City/arterial loop east, south and west of S, up to ~2.2 km away. The link was clean throughout. |
| 23:10-23:20 | More arterial/city loops south and east of S. This is the error regime. |
| 23:21-23:23 | Interstate: a 3.8 km arc NW and N of S at 81-107 km/h, in 5th. |
| 23:23-23:25 | Arterial back down the road east of S. This had the densest burst of errors (23:23:45-23:24:14). |
| 23:25:43-23:26:58 | Decel, creeping and parking at S. |

### 1.3 Gear clusters

The rpm/vss histogram (`vss_gear.png`; vss ≥ 12 km/h, both drives) has five clean peaks.

| gear | rpm per km/h of vss |
|---|---|
| 1st | 136.5 |
| 2nd | 76.2 |
| 3rd | 49.7 |
| 4th | 36.0 |
| 5th | 27.8 |

- 5th gear at a true 100 km/h is about 2,750 rpm.
- "clutch in / coasting / shifting" means the rpm/vss ratio matched no gear.

## 2. Coverage

Basis: 3,326 s of ECU data with the engine running, counted on the 1 Hz grid.

| speed band | s | min | share |
|---|---|---|---|
{{cov_speed}}

| rpm band | s | min | share |
|---|---|---|---|
{{cov_rpm}}

| load band (`load`, MAP-based %) | s | min | share |
|---|---|---|---|
{{cov_load}}

| gear | s | min | share |
|---|---|---|---|
{{cov_gear}}

| segment type | s | min | share |
|---|---|---|---|
{{cov_seg}}

**Other conditions (decoded channels):**

| channel | what was seen |
|---|---|
| ECT | 57-79 °C (never above 79.4) |
| IAT | 18.5-50 °C. The high end is heat soak during drive A's idle. |
| `ac_sw`, `ac_clutch` | 0 throughout |
| `fan` | 0 throughout |
| `vtec` | 1 in a single sample |
| `brake` | on 11-19 % of the time |
| `eld` | 8-39 A. Median 27 A in drive B (night, lights on). |
| battery | 13.4-14.6 V, apart from cranking |
| `closed_loop` | 84-99 % of the time |

## 3. Events on the track and in the timeline

**How to read the tables:**
- The E-numbers match the dashed lines in `timeline_A.png` / `timeline_B.png` and the circles in `track_A.png` / `track_B.png`.
- "route km" is the vss odometer since the start of that drive.
- "min rpm" is the lowest reading from 4 s before to 6 s after the event.

**Drive A**

{{ev_A}}

**Drive B**

{{ev_B}}

**Rpm dips: all at a standstill, just before pulling away.**
- Every dip happened at vss 0, in the second or seconds before a pull-away:
  - A-E2 (22:39:41), 7 s before the launch.
  - B-E2 (22:53:15), 3 s before.
  - B-E3 (23:12:37), 2 s before.
  - B-E6 and B-E7, while creeping into the parking spot.
- None happened while rolling, while decelerating, or at a steady idle.
- They were at three different places: the gas station (two dips), a stop 0.85 km SE of S, and S itself (two dips).
- So for the stall forensics, treat this as clutch take-up from idle, not as something about the location.

**B-E4 (23:12:57), the 5.7 s silence.**
- It came 20 s after the B-E3 dip, during a hard 2nd-gear pull to 79 km/h (up to 4,700 rpm).
- The ECU answered again after 5.66 s. The car was still at 73 km/h and 2,027 rpm.

**B-E5 (23:16:25), the 8.12 s silence.**
- Speed rose from 41 to 59 km/h during the silence, and rpm from 2,066 to 2,887.
- Its capture `stall_20261126_231629` is still filed as kind **stall** in this DB.
- Why: the car accelerated during the silence. The first rpm afterwards (2,887) failed the dropout check, which allows at most 1.25 × the rpm before + 100 (= 2,683).
- Commit 48d337f addresses this case. I did not re-run it on this copy.

**Key-offs (A-E3, B-E8).**
- Each shows as 4 NoReply rows ("echo only") followed by a link-loss capture.
- They are correctly filed as kind `stall`, because the engine really was switched off.

## 4. Data quality while driving

### 4.1 Read rate

| | drive A | drive B |
|---|---|---|
| all ECU reads | 53,514 in 1,247 s = **42.9/s** | 93,678 in 2,149 s = **43.6/s** |
| `00+3` (rpm, vss), `12+4` (MAP/TPS/O2), `20+7` (trims, inj, ign) | 11.1-11.25/s each | 11.38/s each |
| `76+2` (idle target) | 4.55/s | 4.68/s |
| `03+15`, `16+10`, `27+14`, `37+6`, `9A+7` | 0.92/s each | 0.95/s each |
| `24+3`, `20+3` | first 9 s only (start-up reads) | none |
| interval between reads, median / p99 | 20 / 37 ms | 21 / 39 ms |

- The rate stays flat at ~45 reads/s through idle, city, interstate and 5,400 rpm.
- It drops only at link errors. See `readrate_A.png`, `readrate_B.png` and the bottom panel of the timelines.
- The slowly polled blocks never miss their turn.

### 4.2 Every gap over 0.5 s (no read from any block)

**Drive A** has one gap, the accidental unplug.

{{gap_A}}

Other drive A errors, none of which caused a gap:
- One error pair while moving, at 22:42:58: BadFrame + NoReply on `12+4`. The retry recovered it.
- 2 BadFrame "no echo seen" rows at 22:33:50, at idle.

**Drive B** has 15 gaps. All of them came after 23:10:02, and all while moving.

{{gap_B}}

**Cause of the drive B gaps:**
- Each gap is a run of failed exchanges in a row.
- Most of them are **NoReply, "echo only, the ECU did not answer"**. ShortReply, BadChecksum and BadFrame rows appear at the start and end of the bursts.
- Drive B's 108 link_error rows:

  | kind | rows |
  |---|---|
  | NoReply | 87 |
  | ShortReply | 9 |
  | BadChecksum | 7 |
  | BadFrame | 5 |

  71 of them are first attempts and 37 are retries.
- Not one "no bytes at all" row appeared while driving. That signature appeared only at the 22:36 unplug.

### 4.3 What the errors correlate with (drive B, 10 s windows; `corr.py`)

| condition | time (min) | error episodes | rate |
|---|---|---|---|
| before 23:10, all speeds | 19.0 | 0 (one window straddles 23:10:02) | **0.05/min** |
| from 23:10, all speeds | 16.8 | 28 | **1.66/min** |
| from 23:10, 0 km/h | 0.8 | 1 | 1.2/min |
| from 23:10, 1-40 km/h | 6.2 | 8 | 1.3/min |
| from 23:10, 40-70 km/h | 6.8 | 17 | 2.5/min |
| from 23:10, 70-90 km/h | 1.0 | 2 | 2.0/min |
| from 23:10, 90-107 km/h (interstate, smoothest road) | 2.0 | **0** | 0 |
| from 23:10, rpm < 1,500 / 1,500-2,500 / > 2,500 | 4.2 / 8.3 / 4.3 | 6 / 16 / 6 | 1.4 / 1.9 / 1.4 per min |

**Windows with and without errors, in the error regime.**

This compares the moving windows after 23:10 (n = 93) that had an error episode with the ones that did not.

| measure | with errors | without errors | permutation p |
|---|---|---|---|
| vss variability, high-pass (stands in for bumps) | 4.9 | 4.5 | 0.20 |
| rpm jitter (stands in for driveline shocks) | 51 | 41 | 0.08 |
| mean speed | | | 0.43 |
| `eld` | 27.8 A | 28.0 A | |
| battery | 14.20 V | 14.19 V | |
| brake on | 11 % | 17 % | |

**No electrical change at 23:10.** In every minute from 23:05 to 23:16, `eld` stayed at 26-28 A and the battery at 14.2 V.

**Same roads, different result.**
- About 74 % of the error clusters (28 of 38 one-second clusters) lie within 40 m of a spot that had been driven without errors before 23:10.
- Example: the road east of S was clean at 22:54 and had a burst of errors at 23:23-23:24.

**Damaged replies (16 ShortReply/BadChecksum frames, `bits.py`).**
- **Truncated replies end in 1s.**
  - The last bytes of the 9 short replies are FD, FA, FE, FF, FE, FF, F1, F3 and C0.
  - That is 6.2 one-bits per byte on average. Good reply bytes average 1.66.
- **Changed bits lean towards 1.** Against the nearest good reply of the same block, 119 bits went 0→1 and 53 went 1→0. This comparison is rough, because the real values also change between the two reads.
- **What it means.** The K-line idles high. If the receiver suddenly sees idle in the middle of a reply, the ECU's signal stopped reaching it partway through the frame.

**Verdict:**

- **Loose OBD2 plug or an intermittent pin contact: the best fit.**
  - **Evidence:**
    - The errors start at a moment in time, not at a place or a speed.
    - Replies are cut off and the line floats high. Bit errors lean one way.
    - Our echo and the adapter's power were always present. So pin 16 and the adapter's ground held.
    - The driver had already knocked the same plug out at 22:36.
  - **Suspects:** the K-line pin (7) and/or signal ground (pins 4/5).
  - **Consistent details:**
    - The rpm-jitter link is weak (p ≈ 0.08). It fits a contact that shocks disturb once it is loose.
    - The smooth interstate had no errors. That fits too, though 2 min is a small sample.
- **Electrical noise: unlikely.**
  - Electrical load, alternator duty and battery voltage did not change when the errors started.
  - Errors do not rise with rpm, although ignition noise does.
  - EMI would flip bits in both directions. It would not cut frames off cleanly.
- **ECU load: ruled out.**
  - Before 23:10 the ECU answered every read, at 5,411 rpm and through hard pulls.
  - A busy ECU replies late or not at all. It does not stop in the middle of a byte.

### 4.4 GPS quality and clock alignment

| | drive A | drive B |
|---|---|---|
| fixes | 1,189 in 1,367 s | 1,835 in 2,164 s |
| `gps_fix` | always 1 | always 1 |
| satellites (p5-max) | 10-13 | 10-15 (3 rows of 0 at 23:14:58-23:15:00) |
| HDOP (min-max) | 1.0-1.4 | 0.8-1.4 |
| gaps over 3 s | 15 (max 11 s) | 25 (max 13 s) |
| corrupt positions | 0 | 2 (thousands of km off; filtered) |

**Gaps:**
- The long gaps come about every 110-125 s.
- The GPS UTC field jumps by the same amount as the gap. So these fixes were **lost on the handheld side**, not held back and delivered late.
- Separately, about 10 % of the 1 s intervals are 2 s long.

**Clock alignment:**
- **PC clock.** The PC receives each fix a median of **0.20 s before** the fix's own UTC stamp (range 0.13-0.36 s). This stayed steady through both drives. So the PC clock runs at least 0.2 s behind GPS time.
- **GPS speed lag.** Cross-correlating accelerations (the change in GPS speed against the change in vss) gives the best match when **GPS is shifted −1.7 s**: r = 0.80 in drive B, and −1.8 s in drive A. This lag is GPS speed filtering plus transport delay together.
- **For channel overlays,** move the GPS samples 1.7 s earlier.

### 4.5 vss calibration

**Method:**
- Uses 10 s steady-cruise windows. A window counts when:
  - vss ≥ 25 km/h
  - vss varies by ≤ 2 km/h within the window
  - GPS speed varies by ≤ 3 km/h within the window
  - the window holds at least 8 GPS fixes
- 23 windows qualified, at 26-102 km/h.

**Result:**

| measure | GPS / vss |
|---|---|
| **median** | **1.012** |
| **least-squares fit through 0** | **1.011** |
| fit with an offset | GPS = 1.008 × vss + 0.25 |
| 25-45 km/h | 1.007 |
| 45-70 km/h | 1.013 |
| 70-90 km/h | 1.012 |
| 90+ km/h | 1.009 |
| drive B distance: GPS speed integral 24.42 km (gaps bridged) against vss 24.07 km | 1.015 |

**Use true speed ≈ 1.011 × vss.** At a true 100 km/h, the ECU reports about 99.

This is the ECU's vss channel only. The dashboard speedometer was not logged, so its own error is unknown.

## 5. Conditions not yet covered (for the next session)

| condition | what this log shows | what to log next |
|---|---|---|
| **Cold start** | Lowest ECT seen: 57 °C. | Start from a cold-soaked engine (overnight, ECT ≈ ambient). Log fast idle and the warm-up until the fan first cycles. |
| **Full warm-up / fan cycling** | ECT never passed 79.4 °C. `fan` was never on. | A long hot idle after a drive (10+ min), or a restart after heat soak. |
| **A/C on** | `ac_sw` was 0 throughout. | Idle with A/C (the idle-up step). Launches with A/C (the case most likely to stall). Cruise with the compressor cycling. |
| **VTEC / high rpm** | ~14 s above 4,000 rpm, one VTEC sample, TPS max 83 %. | A few WOT pulls in 2nd and 3rd through 5,600 rpm towards the limiter. |
| **Sustained interstate** | 2.4 min at 90 km/h or more (max 107). | 10+ min of 5th-gear cruise at 100-120 km/h. Long engine-braking descents. |
| **Heavy electrical load** | Not covered. | High beams, rear defroster, blower on max and wipers, all on at once at idle. Watch `eld`, `alt_fr` and the IACV response. |
| **Rain / wipers / wet roads** | Not covered. | Any wet-weather drive. |
| **Power-steering load** | Not covered. | Full-lock parking at idle, combined with clutch take-up. All 5 rpm dips happened at pull-away. |
| **Daytime / warmer air** | IAT stayed at 18-50 °C, at night. | A warm afternoon run and a restart after heat soak. |
| **Hills / grades** | Not assessed (baro roughly constant). | A steep climb in a high gear (lugging) and a descent. |
| **Link after re-seating the DLC plug** | Two regimes: 0.05/min and 1.66/min. | Re-seat the plug, then drive the same loop and compare against those two rates. Strap the cable so the driver's knee or feet cannot move it. |

## 6. Files (`work-timeline/`)

| file | content |
|---|---|
| `timeline_A.png`, `timeline_B.png` | Speed (vss and GPS), rpm, segment colours, events, reads/s, link errors, ECU and GPS gaps |
| `track_A.png`, `track_B.png` | Track in metres from S, coloured by rpm, with events and link-error episodes marked |
| `readrate_A.png`, `readrate_B.png` | Read rate per block, in 10 s bins |
| `vss_gear.png` | GPS speed against vss at steady cruise; rpm/vss gear clusters |
| `drive_2026_11_26_2224.gpx`, `drive_2026_11_26_2251.gpx` | Made with `hondalog-log --db <copy> export ... --channel rpm`. **They contain raw coordinates.** |
| `load.py`, `common.py`, `grid.py`, `segments.py`, `coverage.py`, `events_loc.py`, `quality.py`, `corr.py`, `bits.py`, `vsscal.py`, `samespot.py`, `plots.py`, `frag.py`, `writedoc.py`, `doc_template.md` | Scripts; run them with `work-timeline/venv` |
| `segments.txt`, `segments.json`, `events_loc.json`, `quality.json`, `frag.json` | Intermediate results. Positions are only relative to S, in metres. |
| `grid_A.npz`, `grid_B.npz`, `origin.npy` | 1 Hz grids. **They contain raw coordinates.** |

## 7. Privacy / git

- `.gitignore` already covers `docs/analysis/*/work-*/*.db*` and `.../venv/`.
- It does **not** cover the GPX, `.npz` and `.npy` files in this folder, and those contain the home coordinates.
- Before committing, add:

  ```
  docs/analysis/*/work-*/*.gpx
  docs/analysis/*/work-*/*.npz
  docs/analysis/*/work-*/*.npy
  ```

- The PNGs and this document hold only positions relative to S.
