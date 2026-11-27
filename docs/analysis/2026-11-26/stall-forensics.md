# Stall forensics: which logged events were real engine deaths (2026-11-25 and 2026-11-26)

Data: a read-only copy of `hondalog.db`, refreshed at 21:52 on 11-26. It covers all 34 real drives (drive 1, the simulator, is ignored): 2026-11-25 17:56–22:44:46 and 2026-11-26 20:31:58–21:49:30. Drives 2–5 are OBD2-only. Drives 6–35 are Honda RAM reads: rpm, MAP, trims, O2 and ign at ~10 Hz, and the slow block (ECT, batt, ELD, IACV, 0x27, flags) at ~1 Hz. Every channel was re-decoded from the stored raw bytes with the project map: rpm = 16-bit/4, starter = 0x08 bit 0, 0x27 (`ign_limit`) and 0x29 (`iac_cmd`) as raw counts. Times are local.

Scripts and text outputs are in `work-stall/`:
- `ld.py`: loader
- `cand.py` → `cand.txt`: drive boundaries, read gaps, rpm < 300, starter, 0x27, battery
- `deaths.py` → `deaths.txt`: last 60 s of every channel before each engine-off
- `stats.py` → `stats.txt`: ECT glitch rate and minimum-rpm trend
- `dips.py` → `dips.txt`: every sag below 650 rpm, with context
- `zoom.py`: 0.5 s tables around one moment
- `misc.py`: ELD step rate and how often IACV sits at 0

No plots: matplotlib is not installed in the venv.

## 0. Bottom line

1. **The logs contain no proven engine death.** Every logged minute is stationary idle: VSS is 0 throughout and GPS speed is about 0. Across ~5.5 h of logged running, rpm never fell below 581 while the ECU was answering. The starter bit, the key-on value (0x27 = 5) and the crank value (0x27 = 10) appear only at the three deliberate starts. No drive shows an ECU reset.
2. **Four events show the engine stopped, but not why.** They are 11-25 18:55:13, 19:29:06, 20:13:43 and 21:16:09. In each, the ECU went silent with rpm steady at 780–866 and the battery at 13.7–14.3 V. The next log, 3–4 min later, starts with cranking or an after-start flare. That is exactly the signature of the owner's key-off at 18:17 (and 19:29 lines up with a key cycle reported then). **It is also the signature of a cable pull with the engine running**: the confirmed cable pull at 22:34:28 looks the same in the last 60 s. Only what happens afterwards separates them. If any of the four was not a key-off, it was a **loss of ECU and engine power** (PGM-FI main relay, IG1 feed or ECU ground). It was not an idle collapse: an ECU that stays powered keeps answering while rpm falls, and that never happened.
3. **The owner's "dies every couple of minutes" did not happen while logging.** Over 4.2 h (11-25) plus 1.24 h (11-26) of logged running there are at most 4 engine-offs, even if every unknown one counts as a death. That is ≤ 0.95/h on 11-25, 0 in 1.3 h on 11-26, and ≤ 0.73/h overall, or at most one per ~80 min. "Every couple of minutes" would mean ~110–165 deaths in this much running. The longest continuous runs were 84.7 min (11-25 21:20:01 → 22:44:45, two cable drops with the engine running) and 77.3 min (11-26 20:32:13 → 21:49:30). Either the deaths happen only **while driving** (never logged), or "dies" means something else. One candidate is the app's or handheld's "STALL (link lost)" alarm, which K-line dropouts set off, about once every 12 s in drive 2045.
4. **The two idle weaknesses are real but did not kill the engine:**
   - The ECT false-cold fault: 17.7 jumps/h on 11-25 and 33/h on 11-26. Its snap-backs cause 170–540 rpm sags, but the lowest point any of them reached was 685–718 rpm.
   - Too little idle air margin. The new low is **581 rpm at 11-26 21:41:26**: a 19 A load step hit while iac_cmd sat at its floor (40) and IACV was 0. On 11-26, warm idle ran with **IACV = 0 for 65 % of the time** (0 % on 11-25).

## 1. Event list and classification

Signatures used:
- **Engine off in the gap**: the next log starts with the starter bit or 0x27 = 10 (cranking), with rpm 0 and the MIL bulb check (key-on), or with an after-start flare (1,000–1,600 rpm, IACV 12–23 % decaying, 0x27 = 4).
- **Engine running**: the next log starts at ordinary idle with no flare.
- **Real death**: the ECU keeps answering while rpm falls below ~300. This was never seen.

### Link-lost captures and long gaps

| # | time (last ECU read) | source | before | after | class |
|---|---|---|---|---|---|
| E1 | 11-25 18:16:59 | d4 (OBD2), capture 18:17:01 | 662–668 rpm, steady | +23.9 s: 1,446 → 1,185 rpm restart flare | **owner key-off (known)** |
| E2 | 11-25 18:55:13.5 | d15 end, capture 18:55:15 | 816–827 rpm. ECT 80–82 °C, batt 14.1, ELD 14–16 A, IACV 6.3 %, 0x27 = 0 | +180.5 s: 18:58:14 first read 202 rpm, starter bit, 0x27 = 10, batt 10.9 V, then 1,004 → 1,638 rpm | **engine off; key-off or power loss unknown** |
| E3 | 11-25 19:29:06.0 | d20 end, capture 19:29:07 | rpm dipped 766 at −1.3 s, rising to 803 at the last read. ECT 84 °C, batt 14.1, ELD back to 14 A after a 24 A load −20…−4 s | +196.5 s: 19:32:22 rpm 0 for 0.7 s, MIL bulb check, batt 12.6, crank at 19:32:23 | **engine off; probably owner key-off** (the 11-25 report lines it up with a key cycle reported around 19:32) |
| E4 | 11-25 20:13:43.7 | d22 end, capture 20:13:45 | **at −2.2 s: ELD 16 → 38 A, batt 14.3 → 13.7 V, IACV 7 → 14 %.** rpm 805 → 747 → 866, recovering and rising at the last read. ECT 86–87 °C | +253 s: 20:17:56 at 1,078 rpm, IACV 12.5 % decaying, 0x27 = 4 (after-start) | **engine off; key-off or power loss unknown.** Best power-loss candidate |
| E5 | 11-25 21:16:09.0 | d27 end, capture 21:16:10 | **ELD 15 → 29 A from −8 to −3 s, batt 13.8 V**, IACV kick to 12.9 %. rpm 764 → 832, 811 at the last read. ECT 88 °C | +232.7 s: 21:20:01 at 958 rpm, IACV 13.7 % decaying (after-start) | **engine off; key-off or power loss unknown** |
| E6 | 11-25 19:34:59.8 | d21, capture 19:35:01 | 663–668 rpm | +2.6 s at 670 rpm | **K-line dropout, engine running** |
| E7 | 11-25 22:18:17.7 | d29, capture 22:18:19 | 662–674 rpm | +17.9 s at 698 rpm, no flare | **cable bumped (owner), engine running** |
| E8 | 11-25 22:34:28.9 | d29 end, capture 22:34:30 | 810–820 rpm steady, batt 14.1, ELD 14 A: the same picture as E2–E5 | +210 s: d30 at 797 rpm, IACV 7.1–9.4 %, no flare | **cable (owner confirmed), engine running** |
| E9 | 11-25 22:44:45.8 | d30 end, capture 22:44:47 | 675–696 rpm rising. ECT read false-cold 37 °C for the last 10 s, batt 14.1–14.5 | no data until the next evening | **unknown; probably the loose plug** (same as E8, end of session) |
| E10 | 11-26 21:10:53.2 | d33, capture 21:10:54 | 726–728 rpm | +1.5 s at 729 rpm | **K-line dropout, engine running** |

### Other checks

- **Short read gaps inside drives:** 82 more gaps of 1.0–2.1 s, all with the same rpm before and after. These are K-line dropouts with the engine running. Drive 2045 (11-26) also had 96 shorter link drops (`raw-bytes-events.md`).
- **Drive ends where the app was stopped or crashed (28):** each next drive starts 0.9–58 s later at plain idle, with no cranking and no flare. **The engine was running** in every case.
  - d8 → d9 (11-25 18:22 → 18:35, 13 min unlogged) shows no flare either, but the gap is long enough that an off/on cannot be excluded.
  - d35 ended 21:49:30 at a steady 927 rpm (false-cold fast idle). The engine was running when the app stopped. There is no later data in the copy.
- **rpm < 300 with reads continuing:** only at the three starts (18:58:14, 19:32:22–23, 11-26 20:31:58–20:32:12). Never mid-drive.
- **Starter bit:** only 18:58:14.10, 19:32:23.63 and 11-26 20:32:13.17.
- **0x27 = 5 or 10:** 0x27 = 5 only at the 11-26 key-on (20:31:58–20:32:12, rpm 0, batt 12.8–13.1 V), and 0x27 = 10 only at the 18:58:14 crank. One read of 0x27 = 16 (20:00:44) came during a 1,926 rpm blip, so 0x27 is not capped at 4. It is not a key-on.
- **Battery below 13.0 V while running:** twice, 12.92 V (19:48:08, 770 rpm) and 12.82 V (21:50:10, 704 rpm). Both are load kicks, and the engine kept running.
- **11-26 20:31:58:** the first start of the evening, not a death. Battery 12.8–13.1 V and ECT 58–60 °C at key-on hint that the car had run shortly before logging began. The ECT channel is unreliable, so this is only a hint.

### Counts

| class | n |
|---|---|
| real engine death (proven) | **0** |
| engine off, cause unknown (owner key-off or power-loss death) | **4** (E2–E5; E3 leans key-off) |
| owner key-off (known) | 1 (E1) |
| K-line / cable dropout, engine running | 4 captures (E6, E7, E8, E10), plus 82 short gaps > 1 s and 96 sub-second drops in d33 |
| unknown, probably cable | 1 (E9) |
| app stop or crash, engine running | 28 drive ends |

## 2. Timelines of the four engine-off events

Full 2 s tables of every channel are in `deaths.txt`; 0.5 s tables come from `zoom.py`. Before every one of them, ECT read warm, closed loop was on (0x0F = 1), MIL was off, TPS was 7.8 %, knock was 0 and 0x27 was 0.

**E2, 18:55:13.5.**
- d15 had only 36 s of data, so the window runs back into d14.
- −55 s: the last ECT snap-back (33 → 79 °C in d14). rpm sagged to 714–725, then went back to 800.
- −34…−30 s: ECT 70 → 78 °C.
- From −30 s: steady. rpm 800–830, MAP 24–26 kPa, IACV 6.3–9 %, batt 14.0–14.2 V, ELD 14–16 A, STFT 3–9 %, LTFT 6 %, O2 switching 0.1–0.9 V.
- Last 3 s: 817–828 rpm, then silence.
- 180 s later: the starter cranks.

**E3, 19:29:06.0.**
- From −60 s: steady at 800–815 rpm, ECT 84.8 °C, batt 14.1–14.3 V.
- −21 s: ELD 15 → 24 A for 17 s. IACV 7 → 9.4 %, MAP 27 kPa, rpm dip to 758, then 816–830.
- −4 s: load off.
- Last 1.3 s: rpm 767 → 803, rising.
- Silence. 197 s later: key-on, bulb check, crank.

**E4, 20:13:43.7.**
- From −60 s: rock steady at 795–822 rpm. ECT 88–90 °C, batt 14.0–14.3 V, IACV 6.3–7.1 %, MAP 24–26 kPa, STFT 5–10 %, LTFT 7.8 %.
- −2.2 s: ELD 16 → 38 A, batt 13.7 V, ALT_FR 46 → 62 %. IACV kicks 7.1 → 14.1 %, MAP 30 kPa, rpm 805 → **747** (−1.9 s).
- −1.5 s: rpm 801. Last read 866 and rising: the idle had already recovered with overshoot.
- Silence. 253 s later: the after-start flare.

**E5, 21:16:09.0.**
- Steady at 797–821 rpm, ECT 88 °C.
- −8.6 s: ELD 15 → 29 A, batt 13.8 V. IACV 7.5 → 12.9 %, MAP 29 kPa, rpm dip to 764, then 811–832.
- −3 s: ELD back to ~24 A. −1.4 s: 18 A.
- Last read 811 rpm.
- Silence. 233 s later: the after-start flare.

**Reference: E8, 22:34:28.9 (cable, engine confirmed running).** Steady at 794–825 rpm, batt 14.1–14.2 V, ELD 14–17 A. The final 60 s cannot be told apart from E2 or E3.

**Minimum-rpm trend** (10 s minima, 10 min before each event, stitched across drives):
- The median of the minima over the last 5 min was 796, 798, 795 and 796 rpm.
- The lowest minimum in the last 2 min was 714 (E2, the snap-back), 758, 747 and 764.
- E4 had spent −10…−5 min in the low idle state (L, ~660 rpm), then went to ~800, the 810-rpm state (M), which is a higher idle, not a decay.
- **No downward trend before any event.**

## 3. Hypotheses

| hypothesis | verdict | evidence |
|---|---|---|
| (a) ECT false-cold → snap-back to warm cuts IACV and fuel and the idle dies | **contradicted for every engine-off; supported only as a sag cause** | ECT read a steady 81–88 °C at E2–E5. Glitch edges in the 10 min before: 1, 0, 0, 0 (E2's was 55 s earlier and the idle had recovered to 800). The drives holding E3–E5 had ECT glitch rates of 0, 16 and 0 /h. The three most glitch-heavy drives (d28 45/h, d29 29/h, d34 75/h) had no engine-off at all. Snap-backs do cause the biggest sags (170–540 rpm), but the deepest ever bottomed at 685 rpm (11-26 21:30:27) and 718 rpm (11-25 21:49:51). |
| (b) Idle collapse from too little air: iac_cmd at floor, rpm decaying, MAP rising | **contradicted as the cause of E2–E5; supported as a shrinking margin** | At E2–E5, rpm in the last 2 s was 747–866 and rising or flat, MAP 24–30 kPa, IACV 6–14 % (not at floor) and 0x27 = 0. There was no decay at ~10 Hz. **But** the lowest rpm of either day, **581 at 11-26 21:41:26**, came when a 19 A load (ELD 6.9 → 25.4 A, batt 13.6 V) hit with iac_cmd at its floor (40) and IACV 0. rpm fell 665 → 581 in 0.5 s, and iac_cmd jumped to 90 to recover it in ~2 s. Of the 51 sags below 650 rpm (48 with ELD logged), 17 had ELD > 20 A within 5 s. On 11-26, warm idle (ECT ≥ 76 °C) ran with **IACV = 0 for 65 % of the time** at a median 690 rpm. On 11-25 that share was 0 %, at a median 798 rpm with IACV 2.4–7 %. Together with the +18–22 % trims, this points to air the IACV does not control. |
| (c) Electrical: battery/ELD dip, ECU power loss or reset | **consistent with E2–E5 but unproven; no ECU reset seen** | Every engine-off is an instant ECU silence at steady rpm with the alternator charging (batt 13.7–14.3 V). That is what a main-relay, IG1 or ECU-ground drop would look like, and also what a key-off looks like. **E4 and E5 had a 29–38 A load step 2–8 s before the silence**, with batt sagging to 13.7–13.8 V. Load steps ≥ 8 A come once per ~180 s, so ≥ 2 of 4 events within 8–10 s of one would happen by chance only ~1–2 % of the time. That is suggestive, but it rests on 4 events and a window chosen after looking. The known key-off (E1) and the cable pull (E8) had no step. No mid-drive reset signature (link drop → 0x27 = 5, rpm 0, bulb check) was seen. The battery never fell below 12.8 V while running. |
| (d) Fuel: trims at limit, O2 stuck lean | **contradicted as the immediate cause** | At E2–E5: STFT 3–10 %, LTFT 6–8 % (total +10–18 %, below the ±20 % limits each). O2 kept switching 0.1–0.9 V right up to the last read. The lean base trim (+21.6 % at 670 rpm, `report-fueling.md`) remains a margin problem, not a trigger. |
| (e) Something else | **two candidates** | (1) **Logger artefacts read as stalls.** K-line and DLC dropouts set off the "STALL (link lost)" trigger: 4 of 10 captures are proven dropouts with the engine running, one more is probably one, and d33 had a link drop every ~12 s. If the owner counts those alarms, that alone could explain "every couple of minutes". (2) **The deaths happen only while driving** (decel to a stop, fan or A/C load), which none of these logs cover. |

## 4. ECT glitch statistics

ECT here is RAM 0x10 read at 1 Hz. A glitch edge is a step of ≥ 15 °C between consecutive reads ≤ 3 s apart, which matches the sudden jumps but not slow slides to the ~60–70 °C partial level.

| day | logged running | edges | of which drops to cold | per hour |
|---|---|---|---|---|
| 11-25 | 3.85 h (Honda drives) | 68 | 34 | **17.7/h** (8.8 drops/h) |
| 11-26 | 1.24 h | 41 | 19 | **33/h** (15.3 drops/h) |

- **Per drive:** d28 45/h with 73 % of reads false-cold; d29 29/h; d30 44/h; d34 75/h with 22 % false-cold and 61 % partial.
- **Clean drives:** d20 (0.29 h) and d27 (0.51 h) had no glitches. **Those two drives ended in engine-offs E3 and E5.**
- **Timing:** glitch rate and ECT state do not predict the time of any engine-off. If anything, the engine-offs fell in the clean stretches and the glitch storms had none.

## 5. Running time and death rate

| | 11-25 | 11-26 |
|---|---|---|
| engine-on span (wall clock) | 17:56–22:44:46 (4.8 h, one 13 min gap unlogged) | 20:32:13–21:49:30 (1.29 h) |
| logged running | 4.2 h (0.37 h OBD2 + 3.85 h Honda) | 1.24 h |
| engine-offs | 1 known key-off + 4 unknown (18:55, 19:29, 20:13, 21:16: 34–63 min apart) | 0 |
| deaths/h if all 4 unknown were deaths | ≤ 0.95/h | 0 |
| longest continuous run | 84.7 min (21:20:01–22:44:45) | 77.3 min (whole session) |

Owner's claim: "every couple of minutes", i.e. 20–30/h. The logged rate is at least 20–40× lower even on the most pessimistic count. At stationary idle in the driveway, the engine does not die every couple of minutes.

## 6. Recommended checks, in priority order

1. **Pin down what "dies" means, then test the power path.**
   - Ask the owner:
     - Did they switch the engine off at 18:55, 19:29, 20:13 and 21:16 on 11-25?
     - Does "dies" mean the engine stops (tach to 0, warning lights on), or the app or handheld showing STALL?
     - Does it happen while driving or at idle?
   - On the car:
     - Check the **PGM-FI main relay** (tap test and cracked solder joints; FSM relay test, PDF p.505–508).
     - Check the **ECU grounds** and **G101** at the head/thermostat housing, and the **IG1 feed** to the ECU.
   - Do it warm at idle while logging. Switch the radiator fan, headlights, blower and rear defrost on and off repeatedly: E4 and E5 followed 29–38 A load steps. Flex the harness at the relay and the ECU.
   - For the next drive, log IG1 or battery voltage on the handheld side (the `bb_ig1` / `bb_vbat` blackbox channels exist). The next engine-off will then show which it was: key-off (IG1 off), power loss (IG1 on, ECU silent) or idle collapse (ECU answering, rpm falling).
2. **Repair the ECT circuit (C122 connector, terminals, sensor).** The fault is getting worse: 33 jumps/h on 11-26 against 17.7/h on 11-25. It drives every large idle swing, and while driving it can cut fast-idle air just as the throttle closes. Clean or replace the 2P connector and pigtail, fit a new sensor and fix the oil contamination. Wiggle-test with live logging.
3. **Find the unmetered air, then check the idle air path.**
   - Run a smoke test of the intake, IAC valve O-rings, PCV, brake-booster hose and the fast-idle thermo valve (FITV), and check the throttle stop.
   - Warm idle ran with the IACV fully shut 65 % of the time on 11-26, total trim is +20 %, and a 19 A load at that point dropped the idle to 581 rpm. That is the smallest margin seen. Add a decel-to-stop or A/C load on the road and it is the most plausible idle-collapse path.
   - Then check the IAC valve itself.
4. **Fuel pressure test** at idle and with the regulator vacuum hose off (FSM PDF p.499–500). This settles whether the lean trim is fuel or air.
5. **Fix how the OBD plug seats and the K-line adapter connects.** It is not a car fault, but it creates most of the "STALL" captures and hides real events.
