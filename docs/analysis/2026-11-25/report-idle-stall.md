# 2000 Civic EX (D16Y8, P2P): idle and stall analysis, driveway session 2026-11-25 17:56–22:14

Data: `hondalog.db` drives 2–29. Drive 1 (the simulator) is ignored. Drives 2–5 are OBD2-only at ~4 Hz. Drives 6–29 are Honda RAM reads at ~11 Hz fast / 1 Hz slow. All ECU streams were re-decoded from the stored raw bytes with the current `addressmap.toml`. Times are America/Chicago. The scripts are in `/home/claude/analysis/work/` (`load.py`, `ep2.py`, `tier.py`, `cap.py`, `misc.py`) and the plots in `plots-idle/`.
Manual references are PDF page numbers in `manual/civic-96-00.pdf`.

## 0. Bottom line

1. **ECT fault confirmed and quantified. It is on the ECT circuit only, not the shared ground.** RAM 0x10 reads a false ~30–40 °C (about 2.4–2.6 V at the ECU) in **26 episodes**: **1,724 of 11,829 one-second samples (14.6 %)** from 18:19 to 22:14. Another **1,341 samples (11.3 %)** read a partial-low 55–75 °C, while the real coolant was ~80–90 °C (OBD2 PID 05 read 78–88 °C once warm). TPS (constant 7.8 %) and IAT (constant within 0.4 °C) do **not** move at any of the 52 entries and exits. TPS and IAT share SG2 with ECT (PDF p.360, p.400), so this is not a shared-ground fault. The fault sits on the ECT signal side: the sensor or C122 connector, or the RED/WHT wire to ECM C18 (PDF p.400: "poor connection or loose wire at C122").
2. **When the reading snaps back to warm, idle sags 170–540 rpm within 3–5 s.** IACV drops to 3.9–5.9 % and logged ignition timing retards 4–8°. **No sag went below 500 rpm in the driveway.** The lowest running rpm all evening was **595** (20:31:18). The handheld's "stalls" at ~21:49:44 and ~21:52:48 were sags to **718** and **744 rpm**.
3. **None of the six "link lost" captures shows an idle collapse.** In every one, RPM is steady at 660–860 right up to the moment the ECU stops answering. The known key-off at 18:17 has exactly the same signature. After 18:55, 19:29, 20:13 and 21:16, the next log starts with an after-start flare (1,000–1,560 rpm, IACV 12–23 % falling), which means **the engine had been restarted**. The 19:35 capture is a 2.5 s link dropout with the engine running. So each of those four was either a deliberate key-off or a **loss of ECU power** (PGM-FI main relay, IG1 feed or ECU ground) that killed the engine and the link at the same instant. The data cannot tell the two apart; **the user's memory can**.
4. **New finding: the warm-idle mixture is lean-corrected to about +18–20 %.** OBD2: STFT +9.4 %, LTFT +10.2 %. Honda reads: STFT +8.6 %, LTFT +8.6 % (median). That is right at the ±20 % range (PDF p.356). Fuel pressure and an intake/vacuum leak are #2 and #9/#10 on the factory "Engine stalls" list (PDF p.353–354). This shrinks the stall margin whenever the IACV cuts air.

## 1. ECT fault: every transition

ECU ECT (RAM 0x10, 1 Hz) agrees with OBD2 PID 05 during the OBD2 drives (78–88 °C warm, median ~82 °C). Spec when warm: 176–194 °F (80–90 °C) (PDF p.355); P0116 check 176–200 °F (80–93 °C) (PDF p.397).

Classes used here: **C** = false-cold (<55 °C), **P** = partial-low (55–75 °C), **W** = warm. A C episode is a run of C samples, with gaps of ≤5 s merged.

Distribution over the evening (cold % / partial % per drive):

- Drive 8 (18:21): 0 / 98 %. It read 63–70 °C for 65 s.
- Drive 9 (18:35): 16 / 29 %.
- Drive 14 (18:54): 16 / 26 %.
- Drive 17 (19:03–19:06): 0 / 58 %. Repeated 62–73 °C dips.
- Drive 22 (19:43–20:15): 0.3 / 1.4 %. Single-sample spikes at 19:48:27, 19:50:25, 19:50:55 and 20:00:55.
- Drive 23 (20:18): 1.2 %.
- **Drive 25 (20:28–20:44): 28.5 / 8.7 %.**
- **Drive 28 (21:20–21:42): 73.1 / 13.1 %.**
- **Drive 29 (21:42–22:14): 25.8 / 42.5 %.** This includes a ~7 min plateau at 61–68 °C from 21:58 to 22:05.
- Drives 10–13, 16, 18–21, 24, 26 and 27 had no false-cold readings. Drive 27 was 33 min with none.

Steps are abrupt: 85 → 33 °C within one 1 s sample, and back within one sample. The extremes reached 15–17 °C (22:07:25, 21:49:46) just before snapping back. That pattern fits an intermittent contact resistance on the signal line, not a failing thermistor.

### Episode table (C episodes; exit metrics are the 10 s after the last cold sample)

| # | start | end | dur s | drv | min ECT °C | ECT before→after | RPM before | RPM during (med) | IACV max during % | RPM last 5 s | exit 10 s: RPM min | IACV min % | ign min ° | inj ms | STFT % | MAP max kPa |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 18:35:25 | 18:35:39 | 15 | 9 | 46 | –→57 | – | 682 | 2.7 | 680 | 676 | 2.4 | 20.8 | 2.40 | 7.6 | 26.5 |
| 2 | 18:35:46 | 18:35:58 | 12 | 9 | 45 | 59→59 | 684 | 1271 | 14.5 | 1278 | 737 | 4.7 | 19.8 | 2.27 | 1.4 | 26.5 |
| 3 | 18:54:15 | 18:54:17 | 3 | 14 | 33 | –→77 | 808 | 800 | 7.8 | 860 | 794 | 7.8 | 17.0 | 2.45 | 7.0 | 27.2 |
| 4 | 19:48:27 | 19:48:27 | 1 | 22 | 33 | 71→84 | 834 | – | – | 832 | 664 | 4.3 | 15.5 | 2.38 | 6.3 | 28.7 |
| 5 | 19:50:25 | 19:50:25 | 1 | 22 | 34 | 83→84 | 807 | – | – | 812 | 793 | 6.7 | 16.5 | 2.37 | 7.6 | 25.8 |
| 6 | 19:50:55 | 19:50:56 | 2 | 22 | 35 | 84→84 | 810 | – | 9.4 | 808 | 796 | 7.8 | 15.5 | 2.48 | 8.6 | 27.2 |
| 7 | 20:00:55 | 20:00:55 | 1 | 22 | 36 | 83→84 | 755 | – | – | 671 | 658 | 3.9 | 17.0 | 2.47 | 10.6 | 27.2 |
| 8 | 20:18:22 | 20:18:28 | 7 | 23 | 30 | 78→85 | 692 | 689 | 3.9 | 686 | 681 | 2.7 | 15.5 | 2.50 | 14.2 | 27.2 |
| 9 | 20:29:34 | 20:29:37 | 4 | 25 | 37 | 72→86 | 809 | 784 | 5.9 | 786 | 674 | 3.5 | 15.5 | 2.42 | 10.5 | 27.2 |
| 10 | 20:30:52 | 20:35:19 | 267 | 25 | 35 | 87→76 | 662 | 664 | 9.0 | 666 | 662 | 2.7 | 16.2 | 2.43 | 10.2 | 27.2 |
| 11 | 21:20:15 | 21:26:48 | 394 | 28 | 32 | 69→78 | 666 | 665 | 13.7 | 795 | 682 | 2.7 | 15.5 | 2.34 | 9.2 | 26.5 |
| 12 | 21:29:01 | 21:29:07 | 7 | 28 | 36 | 74→87 | 812 | 802 | 7.8 | 798 | 792 | 6.3 | 15.5 | 2.35 | 7.4 | 26.5 |
| 13 | 21:29:17 | 21:31:19 | 122 | 28 | 33 | 85→78 | 807 | 817 | 7.1 | 809 | 794 | 6.3 | 16.0 | 2.32 | 7.5 | 25.8 |
| 14 | 21:32:31 | 21:32:34 | 4 | 28 | 35 | 74→81 | 810 | 794 | 7.5 | 799 | 790 | 5.9 | 15.5 | 2.33 | 8.2 | 25.8 |
| 15 | 21:32:42 | 21:32:43 | 2 | 28 | 36 | 75→76 | 811 | – | 6.7 | 822 | 788 | 5.5 | 15.5 | 2.32 | 8.0 | 25.1 |
| 16 | 21:32:56 | 21:34:40 | 106 | 28 | 33 | 76→85 | 809 | 813 | 7.1 | 818 | 795 | 6.3 | 15.5 | 2.33 | 7.5 | 25.8 |
| 17 | 21:35:51 | 21:36:32 | 42 | 28 | 31 | 70→64 | 797 | 817 | 6.7 | 813 | 801 | 5.9 | 17.2 | 2.30 | 8.1 | 25.1 |
| 18 | 21:37:23 | 21:42:39 | 317 | 28 | 30 | 57→– | 867 | 1079 | 18.8 | 1078 | 1068 | 10.6 | 22.8 | 2.31 | 3.9 | 24.4 |
| 19 | 21:42:41 | 21:49:46 | 426 | 29 | 17 | –→73 | 1083 | 1079 | 14.1 | 1080 | 718 | 4.7 | 20.0 | 2.31 | 4.9 | 26.5 |
| 20 | 21:50:25 | 21:50:32 | 7 | 29 | 33 | 74→70 | 745 | 764 | 7.5 | 765 | 692 | 4.3 | 16.0 | 2.40 | 10.8 | 27.2 |
| 21 | 21:52:58 | 21:53:15 | 18 | 29 | 33 | 58→77 | 817 | 1050 | 11.0 | 1061 | 744 | 5.9 | 19.8 | 2.33 | 3.6 | 26.5 |
| 22 | 21:53:36 | 21:53:39 | 4 | 29 | 34 | 64→88 | 781 | 910 | 7.8 | 875 | 679 | 3.9 | 15.5 | 2.38 | 8.9 | 26.5 |
| 23 | 21:56:07 | 21:56:09 | 3 | 29 | 46 | 76→61 | 678 | 705 | 5.1 | 678 | 674 | 3.5 | 16.2 | 2.39 | 7.4 | 26.5 |
| 24 | 21:57:02 | 21:57:30 | 29 | 29 | 31 | 57→63 | 1084 | 1078 | 14.5 | 1018 | 936 | 9.4 | 20.2 | 2.34 | 2.9 | 25.8 |
| 25 | 21:58:26 | 21:58:38 | 13 | 29 | 26 | 61→67 | 994 | 974 | 9.0 | 974 | 885 | 7.5 | 20.0 | 2.34 | 7.2 | 25.1 |
| 26 | 22:07:25 | 22:07:25 | 1 | 29 | 15 | 63→76 | 906 | – | – | 909 | 672 | 4.7 | 15.5 | 2.36 | 7.4 | 27.2 |

Notes:
- Episode 1 (18:35:25) came right after a 13 min gap with no logging. The ECU read 44–60 °C, and the engine went to fast idle (1,271 rpm).
- Episodes 11 and 18–19 run across app restarts. The false-cold stretch from 21:37:23 to 21:49:46 is one continuous 12.4 min episode (drives 28 → 29).
- Single-sample spikes (episodes 4–7, 26) are too short for the 1 Hz IACV to respond. Even so, RPM fell to ~660 within 10 s after 19:48:27, 20:00:55 and 22:07:25.

### Engine response

**While false-cold.** The ECU does **not** respond the same way every time:

- **Fast idle** (≥1,000 rpm, IACV 10.6–12 %, logged ign 23–25°, STFT dropping from ~9 % to ~4 %, i.e. about 5 % cold enrichment that closed loop then trims out) occurred in 4 episodes: 18:35:46, 21:37:23–21:49:46, 21:52:58 and 21:57:02.
- **No fast idle** in the other long episodes. 20:30:52–20:35:19 (267 s, ECT 35–41 °C) stayed at 650–750 rpm with IACV 4–9 %. 21:20:15–21:26:48 (394 s, 34–36 °C) stayed at 665 rpm with IACV 1.2 % for the first 4.5 min.
- Overall, while C: RPM median 815; **43 % of samples above 1,000 rpm** (0.4 % when warm and clean); 28 % below 720 rpm.
- The fast-idle cases began when the reading slid down over several seconds (e.g. 66 → 62 → 57 → 43 °C at 21:37:13–22). Some no-fast-idle cases also stepped down over ~6 s, though. The ECU probably filters or plausibility-checks ECT, but these logs cannot confirm that.

**On snap-back to warm** (largest cases):

| time | RPM before → min (10 s) | drop | IACV after | ign after (logged) |
|---|---|---|---|---|
| 18:35:58 | 1,278 → 737 | 540 | 4.7 % | 19.8° |
| 21:49:46 | 1,080 → 718 | 362 | 4.7 % | 20.0° (from 24–25°) |
| 21:53:15 | 1,061 → 744 | 317 | 5.9 % | 19.8° |
| 22:07:25 | 909 → 672 | 238 | 4.7 % | 15.5° |
| 21:53:39 | 875 → 679 | 196 | 3.9 % | 15.5° |
| 19:48:27 | 832 → 664 | 168 | 4.3 % | 15.5° |

At 21:49:46–51, RPM fell 1,074 → 806 → 718 in 5 s. IACV went 10.6 → 8.6 → 5.5 → 4.7 %, MAP rose 23.6 → 26.5 kPa, injector pulse stayed 2.3–2.4 ms, and STFT climbed from 2.6 to 8.6 % over 10 s as cold enrichment was removed. See plot 03.

**Near-stall check.** No running sample was below 500 rpm in any drive, OBD2 or Honda. The lowest values outside the first 8 s after a reconnect were 595 (20:31:18, a fan/ELD kick during false-cold), 602 (20:18:09), 610 (19:32:30) and 618 (OBD2, drive 4). The closest approach overall was **571 rpm at 19:32:28**, 5 s after a restart, when the post-start IACV opening collapsed from 14 % to 1.6 %. The post-start flare-then-drop is normal behaviour: the IAC opens for a set time after start, about +150–300 rpm (PDF p.460).

### Shared-ground (SG2) test

ECT, IAT and TPS share SG2 (GRN/BLK, ECM C26) (PDF p.360, p.399–400). MAP uses SG1 (PDF p.360). A high-resistance SG2 would push all SG2 sensors toward higher voltage at the same moment: ECT and IAT colder, TPS higher.

Across all 52 entries and exits (4 s before vs 2.5 s after):

- **TPS:** 7.8–7.9 % before and after every time. Raw byte 20, i.e. 7.8 % in 105,593 of ~108,000 idle samples.
- **IAT:** unchanged at every transition (e.g. 52.8 → 52.8 °C, 50.6 → 50.6 °C).
- **MAP:** moves only as a result of the RPM change (it rises when IACV closes and RPM drops).
- **Conclusion: ECT-only fault.** Suspect the C122 terminals (oil/contamination, a spread female terminal or corrosion), the sensor's own terminals, or the RED/WHT signal wire.

The false-cold level of ~2.4–2.6 V (assuming 0–5 V over 0–255) is about half of Vref. A clean open circuit would read near 5 V (very cold). A partial-low reading means added series resistance roughly equal to the ECU pull-up, i.e. a resistive contact rather than a clean break. Engine oil does not conduct, so it will not short the circuit. But oil plus dirt in an unsealed or damaged connector, or oil wicking along the wire, fits an intermittent high-resistance contact.

## 2. The six "STALL (link lost)" captures

| capture | RPM before link loss | what follows | verdict |
|---|---|---|---|
| 18:17:01 (OBD2, d4) | 662–672 steady | data resumes at +21 s with a restart flare, 1,446 → 1,184 rpm | known user key-off. This is the reference signature. |
| 18:55:15 (d15) | 816–821 steady, link dies 18:55:13.8 | next data 18:58:14: starter bit set, 202 → 1,562 rpm (cranking), battery 10.9 V | engine was **off** by 18:58. Key-off **or** ECU/engine power loss. Not an idle collapse. |
| 19:29:07 (d20) | 793–802 steady (rising) | next data 19:32:22: RPM 0, MIL bulb check, starter at 19:32:23 | same signature; matches the reported key cycle around 19:32 |
| 19:35:01 (d21) | ~665 steady | link back after 2.5 s at 669 rpm; no restart | **artefact** (link dropout, engine running) |
| 20:13:45 (d22) | 852–866 steady | next data 20:17:56: 1,076 → 649 rpm, IACV 12.5 → 1.6 % (after-start pattern) | engine was restarted in the gap |
| 21:16:10 (d27) | 811–830 | next data 21:20:01: 957 → 618 rpm, IACV 13.7 → 2.4 % | engine was restarted in the gap |

Reconnects after "stopped" app restarts (drives 7–15, 17–20, 22, 24–25) **never** show this flare. Only the reconnects after link-lost endings do. A genuine idle stall with the key ON would show RPM falling to 0 while the ECU keeps answering, because the ECU stays powered. That never happened. **Question for the user: at 18:55, 20:13 and 21:16, did you switch the engine off, or did it die by itself?** If it died by itself, the link and the engine stopped at the same instant with no RPM decay first. That points to the **PGM-FI main relay** or the ECU power/ground supply (probable cause #3, page 11-237, PDF p.354/p.506), not to idle control.

## 3. Idle quality when ECT reads correctly

Clean warm idle (ECT ≥76 °C, no false-cold in the previous 120 s, throttle closed, drives 12+, n = 6,977 s):

- **RPM: two levels.** Median 804; 5th–95th percentile 664–820.
  - **L ≈ 665–680 rpm**, IACV 2.4–4.3 %. This matches the M/T no-load spec of **670 ± 50** (USA; IAC disconnected 450 ± 50) (PDF p.489). A/T spec is 700 ± 50 in N/P (PDF p.490).
  - **M ≈ 805–815 rpm**, IACV ~6.7 %. This sits at the "heater fan HI + A/C on" spec of **810 ± 50** (PDF p.489–490), yet no A/C, brake or starter bit is set and ELD is the same (~15 A). The PSP bit (0x08 b2) reads 1 the whole time, so it explains nothing.
  - Every restart (18:58, 19:32, 20:17, 21:20) comes back at L. Several M → L drops line up with ECT glitches (18:36:37, 19:06:47, 20:29:42, 21:26:52, 21:53:47, 22:07:32, 22:11:12). Several L → M rises line up with fan/ELD kicks. The trigger is not confirmed. Both levels are within spec.
- **MAP:** 25.8 kPa (OBD2 25.0). Spec at idle 24–31 kPa (PDF p.355). OK.
- **TPS:** 7.8 % (OBD2 7.8 %). Spec approx. 10 % (PDF p.356). OK.
- **Ignition:** OBD2 PID 0E reads **12.0°** at idle (10.5–12.5°). Spec 12 ± 2° BTDC (PDF p.356; that spec assumes the SCS connector is jumped). OK. **The Honda 0x26 formula `(x-24)/4` reads ~5.5° high** (17.5° vs 12.0°). Treat logged `ign` as relative only, and mark the channel as suspect or fix the offset.
- **Fuel trims:** STFT +8.6 % / LTFT +8.6 % (Honda); +9.4 % / +10.2 % (OBD2). Each is within ±20 % (PDF p.356), but **together they add about +18–20 %**: the ECU is adding a lot of fuel at idle. O2 switches 0.16–0.88 V (spec 0.1–0.9 V, PDF p.355) and closed loop is set 99.5 % of the time.
- **Rev and return:** four blips over 1,400 rpm. Undershoot below the previous idle:
  - 19:10:41 (peak 2,166): 4 rpm.
  - 19:12:59 (peak 2,698): 81 rpm.
  - 20:00:37 (peak 1,926): **157 rpm**, landing at 658 because it dropped from M to L.
  - No dip below 650 after a blip.
- **A/C idle-up** (19:09:17, 19:12:39; A/C switch and clutch bits). At clutch-in, RPM dips 28–40 rpm (664 → 636, 761 → 721) and MAP jumps to 33–34 kPa. IACV goes 2.4 → 17.3 % and 7.8 → 20 %. RPM then overshoots to 1,115–1,225 for the 4–8 s the A/C was on, and returns to 668–692. The compensation works but is coarse. The A/C was never held on long enough to check the 810 ± 50 A/C spec (PDF p.489).
- **Fan and electrical loads.** 27 ELD spikes to 28–42 A, median interval ~257 s, almost certainly the radiator fan cycling. Each comes with a 1 s IACV kick to ~14 %. RPM dip at these: median 42, max 125 rpm. The `fan` bit (0x0C b1) never set, so that mapping is wrong for this car. ELD baseline 8.9–23.8 A (p1–p99); ALT FR 34–66 %.
- **Battery:** 14.0–14.4 V (p1–p99). Minimum 10.9 V during cranking at 18:58:14. Brief dips to 12.6–12.9 V at the 19:32 key-on and at load kicks (19:48:08, 21:50:10). Charging is healthy.

## 4. Ranked hypotheses for the stalls (including stalls while driving)

1. **Intermittent ECT signal at C122 / sensor (confirmed fault, likely contributor).**
   - What the log shows: false-cold makes the ECU add fast-idle air (IACV ~11 %), cold enrichment (~5 %, visible as STFT falling from ~9 % to ~4 %) and advance (~+6° on the logged scale). Each snap to warm removes all three within 2–3 s, with 170–540 rpm sags in 3–5 s. ECT feeds fuel, IAC and timing (PDF p.355, p.460).
   - Predicted effect while driving:
     - While false-cold: the engine is richer, "hangs" around 1,000–1,100 rpm at stops, and idles high.
     - The risky case is a snap to warm **as the throttle closes** for a stop. The idle bypass air is then the only air source, and the ECU cuts it from ~11 % to ~5 % and removes enrichment just as RPM is falling from the decel.
   - In the driveway, starting from a stable 1,080 rpm, the worst sag bottomed at 718. On the road, the drop starts from a falling RPM and adds to fan, A/C and alternator loads and the lean base trim below. Getting below ~500 rpm is plausible but not shown in this data.
   - It also explains erratic idle (670/810/1,080) and partly explains the trims swinging.
2. **Lean base mixture (total trim +18–20 %).**
   - Causes to check: low fuel pressure (stall cause #2, pages 11-230/231 ≈ PDF p.499–500); an intake or vacuum leak (IAC O-rings, PCV, brake booster hose, intake gasket; #7/#9 on the list); a MAP offset (#11).
   - With the ECU already near its trim limit, any sudden loss of air or fuel (IACV snap, fan load, A/C clutch) has less margin. The factory "Engine stalls" order is 2, 4, 11, 7, 20, 8, 5, 15: fuel pressure, ignition, MAP, IAC valve, contaminated fuel, idle adjustment, CKP/TDC/CYP, brake switch (PDF p.353–354).
3. **ECU power interruption (PGM-FI main relay / IG1 / ECU ground).**
   - The four link-lost endings followed by restarts look exactly like key-offs: steady RPM, then ECU silence. If the user did **not** key off, this is the most direct match to "dies instantly, often while driving".
   - The main relay is a classic Honda heat-related failure: probable cause #3, page 11-237 (PDF p.354; relay test PDF p.505–508).
4. **IAC valve / idle control** (#7, page 11-194; idle adjustment #8, PDF p.489–490).
   - The IACV responds correctly to every command. Undershoot after revs is ≤157 rpm and A/C idle-up works.
   - But each restart dropped to 571–625 rpm when the post-start opening ended, and the base idle steps between 670 and 810. A sticky or carboned valve, or bad O-rings, would add to hypotheses 1–2.
5. **Lower likelihood from this data:**
   - Charging/battery: healthy.
   - TPS/MAP sensors: stable, in spec.
   - Ignition timing: 12° by OBD2, in spec.
   - Crank sensor: no RPM dropouts while running.

## 5. Next tests (in order)

1. **Ask the user** whether the engine died by itself at 18:55, 20:13 and 21:16, or whether they keyed off.
2. **Fix the ECT circuit, then re-log the same idle session.**
   - Clean, dry and inspect C122 and the sensor terminals; re-tension or replace the 2P pigtail; fix the oil leak. A new ECT sensor is cheap and comes with a fresh terminal interface.
   - Wiggle test with the engine warm and hondalog live: flex C122 and the harness toward the ECM. Pass = ECT stays 80–90 °C. Fail = reproducible drops to ~35 °C.
   - With the ignition ON, measure ECT signal (C18, RED/WHT) to SG2 (C26, GRN/BLK) at the ECM by back-probe. Expect 0.4–0.7 V warm (PDF p.397–400). Watch for jumps toward ~2.5 V while flexing.
   - Do **not** clear the ECU by pulling fuses mid-test, or the trims are lost.
3. **Fuel pressure test** (PDF p.499–500) at idle and with the pressure-regulator vacuum hose off. Then a **smoke/vacuum-leak test** of the intake, IAC O-rings, PCV and brake-booster hose. Goal: bring total trim back under ~±10 %.
4. **Main relay:** relay test per PDF p.505–508. Add a logging trigger for "link lost while rpm > 400 and **no** key-off". Even better, log battery or IG1 voltage on the handheld side, so a power-loss stall can be told apart from a key-off.
5. **Drive log after the ECT repair** with the stall trigger (`rpm < 50`, armed above 400) **plus** a trigger on an ECT step of more than 20 °C/s. Watch the decel-to-stop events. If stalls continue with ECT clean, hypotheses 2 and 3 move up.
6. **Logger housekeeping:**
   - Mark `ign` (0x26) as suspect or add a −5.5° offset, verified against PID 0E.
   - Mark `fan` (0x0C b1) as wrong; it never toggles while ELD shows ~27 fan cycles.
   - IACV and ELD scaling are still unverified, so use them as relative values only.

## Plots (`/home/claude/analysis/plots-idle/`)

- `01_overview.png`: whole evening. ECT (with OBD2 PID 05), RPM, IACV, ign, STFT, MAP, ELD.
- `02_late.png`: 21:15–22:15, the densest false-cold period.
- `03_snapbacks_2149_2153.png`: the two handheld "stall" events (sags to 718 / 744 rpm).
- `04_entry_fast_idle_2137.png`: false-cold entry that triggers fast idle.
- `05_false_cold_no_fast_idle_2030.png`: 4.5 min of false-cold with **no** fast idle.
- `06_ac_idle_up.png`: the A/C idle-up events.
- `07_link_lost_captures.png`: the six link-lost captures (RPM plus link state, 300 s after each).
