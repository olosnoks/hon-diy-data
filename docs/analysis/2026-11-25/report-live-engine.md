# Live engine monitoring — 2000 Civic EX (D16Y8, P2P), idling in driveway

Started 22:17 local, 2026-11-25. Source: hondalog.db (drive 29, started 21:42:41) via read-only SQLite backup snapshots every ~4 min.
Method: `work/live_monitor.py` on each snapshot. ECT state uses hysteresis (cold <=50 °C, warm >=60 °C); a second banding splits warm readings
into a "mid" plateau 60–77 °C and "hot" >=78 °C. Wall time = epoch_unix + t. GPS channels are present in the log but are not read or reported.

## Baseline: whole drive so far (21:42:41 – 22:15:41)

Key observations before the loop:

1. **ECT is not two-level, it is three-level.** Per-minute medians sit on plateaus: ~33 °C (21:42–21:48, false cold), ~65–70 °C
   (21:50–21:51, 21:58–22:06), ~85–90 °C (21:53–21:54, 22:07–22:13). Jumps between plateaus happen within one sample (~1 s), which is
   physically impossible for real coolant, so at least two of the three levels are false. ECT is an NTC thermistor: added series resistance
   (bad contact / contamination in the connector) makes it read *colder*, so the highest plateau (~85–90 °C, inside the FSM warm spec of
   176–200 °F / 80–93 °C) is most likely the true temperature, and the ~65 °C plateau is a second, partial version of the same connector fault.
2. **The ECU follows the reading in a graded way** (drive-so-far, by ECT band):

   | ECT band | time | RPM mean / p5 / min | IACV | ign | inj | STFT | MAP |
   |---|---|---|---|---|---|---|---|
   | false-cold <=50 | ~380 s | 1054 / 908 / 664 | 10.8 % | 23.6° | 2.33 ms | +3.9 % | 24 kPa |
   | mid 60–77 | ~780 s | 875 / 790 / 674 | 7.4 % | 21.8° | 2.36 ms | +7.4 % | 25 kPa |
   | hot >=78 (true) | ~730 s | 689 / 657 / 642 | 4.5 % | 17.5° | 2.45 ms | +11.1 % | 26 kPa |

3. 19 ECT state changes (hysteresis) in 33 min, clustered 21:49–21:58, 22:07, 22:15. Snap to warm → IACV drop, ignition retard ~3–6°, RPM
   min ~670–720 within 10 s. Several flips are 1–3 s single-sample spikes (e.g. 22:07:25 15 °C → 76 °C in 1 s).
4. **Fuel trim at true-warm idle is high:** STFT ≈ +11 % on top of LTFT ≈ +10 % (total ≈ +21 %) at the low-airflow idle; at the higher
   false-cold idle total trim is ≈ +14 %. Trim that grows as airflow falls is the classic signature of an unmetered-air (vacuum) leak or
   low fuel pressure, independent of the ECT fault. New finding worth chasing (smoke test intake, PCV/brake booster hoses, IACV gasket).
5. RPM never below 600 (min 642). No stall, no captures, stall_count 0. Fan command (ECM bit, unverified) never on; A/C off; MIL off; VSS 0.
6. Link: 86 short link=0 drops (2.6/min, almost all 0.1–0.3 s); two >1 s (21:52:31 1.2 s, 22:03:27 1.1 s). RPM stream gaps >1 s: 31 (0.94/min),
   none >2 s, longest 1.96 s at 21:52:32; effective rpm rate 8.5 Hz.
7. IAT steady 51–53 °C (heat soak, no airflow). Battery 12.8–14.8 V, mean 14.2 V (charging OK); ELD 13–40 A, alt_fr 33–79 %.
8. Note: the `drives` row for drive 29 shows status=crashed/end_reason=crash while its heartbeat is current (22:15:40); checking whether
   that is just the app's live-row state on a snapshot.

## Monitoring log


### Window 22:17:38 – 22:17:53 (drive 29, snapshot age 27 s at analysis 22:18:20)

- drive row: status=crashed end_reason=crash heartbeat=22:17:50 stall_count=0 capture_count=0
- ECT min/mean/max 85/85/85 °C; time-share warm(≥60) 100 %, false-cold(≤50) 0 %; state at end: **warm** (85 °C); samples 14
  - warm readings 85–85 °C (median 85); spec warm 80–93 °C (176–200 °F)
- ECT transitions in window: **0**
- ECT band hot >=78: ~15 s; RPM mean 694, p5 659, min 656; IACV 5.5 %; ign 17.5°; inj 2.53 ms; STFT 12.6 %; MAP 27 kPa
- RPM min/mean/max 656/694/769; IACV 4.7/5.5/8.2 %; ign 15.5/17.5/19.5°; inj 2.40/2.53/2.95 ms; MAP 25/27/32 kPa; TPS 7.8/7.8/7.8 %
- STFT 10.2/12.6/14.8 %; LTFT 10.9/10.9/10.9 %; O2 0.16/0.55/0.84 V; closed loop 100 % of samples
- IAT 51/51/51 °C; batt 13.97/14.09/14.26 V; alt_fr 56/60/80 %; ELD 13.6/16.2/23.8 A
  - fan: on 0 % of samples, 0 switches
  - ac_clutch: on 0 % of samples, 0 switches
  - ac_sw: on 0 % of samples, 0 switches
  - mil: on 0 % of samples, 0 switches
  - brake: on 0 % of samples, 0 switches
  - vtec: on 0 % of samples, 0 switches
- knock V 0.02/0.02/0.02
- RPM < 600: none
- link drops (link=0 episodes): 1 (4.00/min), >1 s: 0, longest 0.17 s
- data gaps (rpm stream): >0.5 s 2, >1 s 0 (0.00/min), >2 s 0, longest 0.67 s at 22:17:46; effective rpm rate 6.8 Hz

### Window 22:15:30 – 22:17:40 (drive 29, snapshot age 33 s at analysis 22:18:26)

- drive row: status=crashed end_reason=crash heartbeat=22:17:50 stall_count=0 capture_count=0
- ECT min/mean/max 59/81/87 °C; time-share warm(≥60) 99 %, false-cold(≤50) 0 %; state at end: **warm** (85 °C); samples 125
  - warm readings 63–87 °C (median 83); spec warm 80–93 °C (176–200 °F)
  - in-between readings (50–60 °C): 1 samples, [59]
- ECT transitions in window: **0**
- ECT band mid 60-77: ~28 s; RPM mean 793, p5 702, min 692; IACV 5.9 %; ign 21.5°; inj 2.37 ms; STFT 9.2 %; MAP 25 kPa
- ECT band hot >=78: ~100 s; RPM mean 668, p5 652, min 648; IACV 3.5 %; ign 19.2°; inj 2.45 ms; STFT 11.8 %; MAP 26 kPa
- RPM min/mean/max 648/698/915; IACV 2.4/4.0/9.0 %; ign 16.8/19.8/24.0°; inj 2.20/2.43/2.53 ms; MAP 24/26/27 kPa; TPS 7.8/7.8/8.2 %
- STFT 5.5/11.2/14.1 %; LTFT 10.2/10.4/10.9 %; O2 0.14/0.52/0.88 V; closed loop 100 % of samples
- IAT 51/51/51 °C; batt 13.97/14.30/14.83 V; alt_fr 35/60/72 %; ELD 12.8/14.5/17.5 A
  - fan: on 0 % of samples, 0 switches
- knock V 0.02/0.02/0.02
- RPM < 600: none
- link drops (link=0 episodes): 12 (5.54/min), >1 s: 1, longest 1.12 s
  - link loss 22:17:10 for 1.1 s
- data gaps (rpm stream): >0.5 s 4, >1 s 2 (0.92/min), >2 s 0, longest 1.34 s at 22:17:12; effective rpm rate 9.0 Hz
  - >1 s gaps at: 22:16:00(1.2s), 22:17:12(1.3s)

### Window 22:17:38 – 22:22:21 (drive 29, snapshot age 37 s at analysis 22:22:58)

- drive row: status=crashed end_reason=crash heartbeat=22:22:21 stall_count=1 capture_count=1
- ECT min/mean/max 53/80/85 °C; time-share warm(≥60) 100 %, false-cold(≤50) 0 %; state at end: **warm** (77 °C); samples 259
  - warm readings 75–85 °C (median 80); spec warm 80–93 °C (176–200 °F)
  - in-between readings (50–60 °C): 1 samples, [53]
- ECT transitions in window: **0**
- ECT band mid 60-77: ~53 s; RPM mean 689, p5 669, min 662; IACV 4.0 %; ign 18.4°; inj 2.44 ms; STFT 12.1 %; MAP 26 kPa
- ECT band hot >=78: ~229 s; RPM mean 690, p5 660, min 634; IACV 3.9 %; ign 20.3°; inj 2.46 ms; STFT 12.3 %; MAP 26 kPa
- RPM min/mean/max 634/690/782; IACV 2.0/3.9/8.2 %; ign 15.5/19.9/23.8°; inj 2.14/2.45/2.95 ms; MAP 24/26/32 kPa; TPS 7.8/7.8/8.2 %
- STFT 5.5/12.3/15.6 %; LTFT 10.9/12.0/12.5 %; O2 0.12/0.54/0.92 V; closed loop 100 % of samples
- IAT 51/51/51 °C; batt 13.78/14.10/14.26 V; alt_fr 47/57/85 %; ELD 13.6/15.5/23.8 A
  - fan: on 0 % of samples, 0 switches
- knock V 0.02/0.02/0.02
- RPM < 600: none
- link drops (link=0 episodes): 16 (3.39/min), >1 s: 1, longest 17.70 s
  - link loss 22:18:17 for 17.7 s
- data gaps (rpm stream): >0.5 s 6, >1 s 1 (0.21/min), >2 s 1, longest 17.94 s at 22:18:35; effective rpm rate 9.1 Hz
  - >1 s gaps at: 22:18:35(17.9s)
- events table rows for drive: 1; last: ['8', '29', '5566.3645879', 'trigger', 'STALL (link lost)', '{"rule": "link_loss", "reason": "link", "message": "link los']

> **22:18:17–22:18:35 link loss = cable artefact, not a stall.** The OBD cable was bumped loose (per the user, via the lead). The app fired its
> `link_loss` trigger ("STALL (link lost)", capture `stall_20261125_221819`, drive stall_count now 1). The data agree with a cable fault:
> RPM was 662–689 in the samples before the 17.9 s gap and 698–702 in the first samples after it, with no zero/cranking RPM, so the engine kept
> idling through it. ECT stayed on the warm plateau across it. Excluded from engine-event and gap-rate conclusions. (Drive row status flips
> between `crashed` and `recording` from snapshot to snapshot while the heartbeat stays current: an app/DB-row quirk, not a real end of drive.)

### Window 22:22:06 – 22:26:24 (drive 29, snapshot age 20 s at analysis 22:26:44)

- drive row: status=recording end_reason= heartbeat=22:26:22 stall_count=1 capture_count=1
- ECT min/mean/max 76/81/85 °C; time-share warm(≥60) 100 %, false-cold(≤50) 0 %; state at end: **warm** (81 °C); samples 247
  - warm readings 76–85 °C (median 80); spec warm 80–93 °C (176–200 °F)
- ECT transitions in window: **0**
- ECT band mid 60-77: ~19 s; RPM mean 680, p5 674, min 672; IACV 3.8 %; ign 16.4°; inj 2.46 ms; STFT 13.3 %; MAP 26 kPa
- ECT band hot >=78: ~239 s; RPM mean 680, p5 658, min 644; IACV 3.4 %; ign 17.3°; inj 2.45 ms; STFT 12.0 %; MAP 26 kPa
- RPM min/mean/max 644/680/791; IACV 2.4/3.4/7.8 %; ign 15.5/17.3/19.5°; inj 2.31/2.46/2.92 ms; MAP 24/26/30 kPa; TPS 7.8/7.8/8.2 %
- STFT 8.6/12.1/15.6 %; LTFT 10.9/12.4/13.3 %; O2 0.16/0.53/0.90 V; closed loop 100 % of samples
- IAT 51/51/51 °C; batt 13.88/14.11/14.26 V; alt_fr 48/58/87 %; ELD 13.6/15.2/23.5 A
  - fan: on 0 % of samples, 0 switches
- knock V 0.02/0.02/0.04
- RPM < 600: none
- link drops (link=0 episodes): 18 (4.19/min), >1 s: 0, longest 0.98 s
- data gaps (rpm stream): >0.5 s 12, >1 s 4 (0.93/min), >2 s 0, longest 1.26 s at 22:23:11; effective rpm rate 9.5 Hz
  - >1 s gaps at: 22:22:29(1.2s), 22:23:11(1.3s), 22:24:08(1.2s), 22:25:52(1.2s)
- events table rows for drive: 1; last: ['8', '29', '5566.3645879', 'trigger', 'STALL (link lost)', '{"rule": "link_loss", "reason": "link", "message": "link los']

### Window 22:26:09 – 22:30:48 (drive 29, snapshot age 22 s at analysis 22:31:10)

- drive row: status=recording end_reason= heartbeat=22:30:48 stall_count=1 capture_count=1
- ECT min/mean/max 78/82/85 °C; time-share warm(≥60) 100 %, false-cold(≤50) 0 %; state at end: **warm** (84 °C); samples 269
  - warm readings 78–85 °C (median 83); spec warm 80–93 °C (176–200 °F)
- ECT transitions in window: **0**
- ECT band hot >=78: ~279 s; RPM mean 748, p5 658, min 634; IACV 5.4 %; ign 17.6°; inj 2.39 ms; STFT 8.1 %; MAP 26 kPa
- RPM min/mean/max 634/748/877; IACV 2.4/5.4/11.8 %; ign 15.5/17.6/19.5°; inj 2.27/2.39/2.87 ms; MAP 24/26/32 kPa; TPS 7.8/7.8/8.2 %
- STFT 3.9/8.1/13.3 %; LTFT 7.8/9.5/11.7 %; O2 0.12/0.51/0.92 V; closed loop 100 % of samples
- IAT 51/51/51 °C; batt 13.78/14.12/14.35 V; alt_fr 43/52/86 %; ELD 13.6/15.6/28.2 A
  - fan: on 0 % of samples, 0 switches
- knock V 0.02/0.02/0.04
- RPM < 600: none
- link drops (link=0 episodes): 15 (3.23/min), >1 s: 0, longest 0.90 s
- data gaps (rpm stream): >0.5 s 5, >1 s 2 (0.43/min), >2 s 0, longest 1.87 s at 22:30:05; effective rpm rate 10.0 Hz
  - >1 s gaps at: 22:28:33(1.0s), 22:30:05(1.9s)
- events table rows for drive: 1; last: ['8', '29', '5566.3645879', 'trigger', 'STALL (link lost)', '{"rule": "link_loss", "reason": "link", "message": "link los']

> **22:27:51 — new: idle step-up with ECT unchanged (warm 83.6 °C).** For ~6 min before, IACV sat near its floor (2.4–3.5 %) with RPM
> 655–670 (bottom edge of the FSM no-load idle, 700 ± 50 rpm; 750 ± 50 Canada). At 22:27:51 ELD jumped 14 → 23.5 A, alt_fr spiked to 74 %,
> MAP 26 → 28.5 kPa, RPM dipped to 645, then the ECU opened IACV 3.5 → 7.8 % and RPM rose to ~750, later ~810 (FSM loaded idle 810 ± 50).
> ELD fell back to ~14 A at 22:28:07, but IACV stayed at ~6.5–8 % and RPM held ~805–815 through 22:30:48. ECT dipped 83.6 → 78.5 °C over
> 22:28:07–22:28:20 then climbed back to 84 °C. A ~9 A, ~16 s load followed by a short ECT dip looks like the radiator fan cycling on
> its thermoswitch (FSM: ON 91–95 °C; the ECM fan bit stayed 0, which fits a thermoswitch-driven fan), but the reported ECT (83.6 °C) is
> below the switch-on temperature, so this is unconfirmed. If the user switched on lights/blower at 22:27:50, that explains it instead.
> STFT eased from ~+11 % to ~+7 % and LTFT drifted from 11.7 % to 7.8 % at the higher idle (more airflow, smaller lean correction).

### Window 22:30:33 – 22:34:28 (drive 29, snapshot age 47 s at analysis 22:35:16)

- drive row: status=recording end_reason= heartbeat=22:34:49 stall_count=2 capture_count=2
- ECT min/mean/max 78/82/86 °C; time-share warm(≥60) 100 %, false-cold(≤50) 0 %; state at end: **warm** (83 °C); samples 224
  - warm readings 78–86 °C (median 83); spec warm 80–93 °C (176–200 °F)
- ECT transitions in window: **0**
- ECT band hot >=78: ~236 s; RPM mean 810, p5 796, min 768; IACV 6.9 %; ign 17.5°; inj 2.36 ms; STFT 6.7 %; MAP 25 kPa
- RPM min/mean/max 768/810/848; IACV 6.3/6.9/13.7 %; ign 15.5/17.5/19.5°; inj 2.29/2.36/2.78 ms; MAP 24/25/30 kPa; TPS 7.8/7.8/8.2 %
- STFT 3.1/6.7/10.9 %; LTFT 7.0/7.1/7.8 %; O2 0.14/0.51/0.90 V; closed loop 100 % of samples
- IAT 51/51/51 °C; batt 13.78/14.12/14.26 V; alt_fr 42/46/56 %; ELD 13.6/15.9/35.7 A
  - fan: on 0 % of samples, 0 switches
- ELD step 22:33:01: 14 → 36 A; RPM 10 s before/after 805/816; IACV 7.1→8.9 %
- 1-min means:
  ```
  22:30 rpm809 ect81 iacv7.0 ign17.6 stft+6.6 ltft+7.3 eld16
  22:31 rpm810 ect82 iacv6.6 ign17.5 stft+7.0 ltft+7.0 eld15
  22:32 rpm808 ect84 iacv7.4 ign17.6 stft+6.8 ltft+7.0 eld17
  22:33 rpm813 ect81 iacv6.6 ign17.2 stft+6.7 ltft+7.0 eld15
  ```
- knock V 0.02/0.02/0.04
- RPM < 600: none
- link drops (link=0 episodes): 20 (5.09/min), >1 s: 1, longest 1.22 s
  - link loss 22:32:27 for 1.2 s
- data gaps (rpm stream): >0.5 s 11, >1 s 2 (0.51/min), >2 s 0, longest 1.62 s at 22:32:28; effective rpm rate 9.4 Hz
  - >1 s gaps at: 22:31:10(1.1s), 22:32:28(1.6s)
- events table rows for drive: 2; last: ['9', '29', '6537.668769700002', 'trigger', 'STALL (link lost)', '{"rule": "link_loss", "reason": "link", "message": "link los']

## !! 22:34:29 — ECU went silent (link loss, capture `stall_20261125_223430`) — written up immediately

- The app fired `link_loss` ("STALL (link lost)") at 22:34:30.5; drive stall_count → 2. Last ECU sample 22:34:28.
- **As of the 22:35:28 snapshot the ECU is still silent** (link=0 for ~60 s, no rpm samples since 22:34:28). The app itself is still
  recording (heartbeat 22:35:24, status recording).
- 30 s before (2 s means, 22:34:00–22:34:28): RPM 805–820 (last raw samples 810–814), ECT 81.5–82.5 °C (warm, steady, no flip),
  IACV 6.3–6.7 %, ign 16.4–18.0°, inj 2.3–2.4 ms, MAP 24.5–25.4 kPa, STFT +4.8…+9.0 %, ELD 14–15.6 A, batt 14.0–14.2 V. Nothing was trending.
- **Reading:** the data stop dead at a steady 812 rpm. At ~9 Hz a real stall would show RPM falling over the last 0.5–1 s and the
  battery voltage dropping off charge; neither happened, and there was no ECT flip or idle sag beforehand. So this looks like the ECU link
  dropping (cable/connector, like the 22:18 bump, or the ignition being switched off), not the engine dying from the idle fault.
  These data cannot tell whether the engine is still running. **The user should look at the car / OBD cable.**

> **Update:** the user confirms the engine kept running at 22:34:29; the OBD plug had worked loose again (it doesn't seat fully). The
> 22:34:29 capture and the silence after it are a **cable artefact, not a stall**. Drive 29 was closed by the app (complete, last
> heartbeat 22:36:24). After the reseat the app opened **drive 30 (drive_2026_11_25_2237)**, and ECU data resumed at **22:37:59** at
> ~797 rpm (engine idling, which confirms it). Total outage was 22:34:29–22:37:59 (~3.5 min). Monitoring continues on drive 30.

### Window 22:37:59 – 22:38:47 (drive 30, snapshot age 29 s at analysis 22:39:16)

- drive row: status=crashed end_reason=crash heartbeat=22:38:44 stall_count=0 capture_count=0
- ECT min/mean/max 45/74/90 °C; time-share warm(≥60) 62 %, false-cold(≤50) 13 %; state at end: **cold** (49 °C); samples 47
  - warm readings 84–90 °C (median 90); spec warm 80–93 °C (176–200 °F)
  - false-cold readings 45–49 °C (median 47)
  - in-between readings (50–60 °C): 12 samples, [58, 56, 56, 55, 55, 56, 52, 51]
- ECT transitions in window: **1**
  - 22:38:37 warm→cold (ECT 45 °C): 10 s after: RPM min 1138 mean 1253 (10 s before 931); IACV 9.4→15.2 %; ign 22.4→28.2°; inj 2.38→2.32 ms; STFT 7.1→-0.5 %; MAP 25→24 kPa
- ECT band false-cold <=50: ~6 s; RPM mean 1246, p5 1151, min 1138; IACV 15.0 %; ign 28.0°; inj 2.33 ms; STFT 0.3 %; MAP 24 kPa
- ECT band hot >=78: ~29 s; RPM mean 807, p5 786, min 782; IACV 7.7 %; ign 18.0°; inj 2.40 ms; STFT 7.0 %; MAP 26 kPa
- RPM min/mean/max 750/931/1293; IACV 5.5/9.6/15.7 %; ign 15.5/21.2/30.8°; inj 2.23/2.38/2.80 ms; MAP 23/25/29 kPa; TPS 7.8/7.8/8.2 %
- STFT -3.1/5.3/10.2 %; LTFT 6.2/6.7/7.0 %; O2 0.16/0.60/0.94 V; closed loop 100 % of samples
- IAT 51/51/51 °C; batt 13.88/14.11/14.26 V; alt_fr 31/44/56 %; ELD 14.0/17.3/24.6 A
  - fan: on 0 % of samples, 0 switches
- ELD step 22:38:03: 16 → 23 A; RPM 10 s before/after 813/811; IACV 7.7→8.2 %
- 1-min means:
  ```
  22:37 rpm931 ect74 iacv9.6 ign21.2 stft+5.3 ltft+6.7 eld17
  ```
- knock V 0.02/0.02/0.04
- RPM < 600: none
- link drops (link=0 episodes): 4 (4.97/min), >1 s: 0, longest 0.26 s
- data gaps (rpm stream): >0.5 s 1, >1 s 0 (0.00/min), >2 s 0, longest 0.90 s at 22:38:00; effective rpm rate 9.9 Hz

### Window 22:38:32 – 22:43:12 (drive 30, snapshot age 21 s at analysis 22:43:33)

- drive row: status=recording end_reason= heartbeat=22:43:10 stall_count=0 capture_count=0
- ECT min/mean/max 38/74/91 °C; time-share warm(≥60) 72 %, false-cold(≤50) 25 %; state at end: **warm** (81 °C); samples 273
  - warm readings 76–91 °C (median 87); spec warm 80–93 °C (176–200 °F)
  - false-cold readings 38–49 °C (median 42)
  - in-between readings (50–60 °C): 9 samples, [55, 55, 56, 52, 51, 53, 51, 50]
- ECT transitions in window: **4**
  - 22:38:37 warm→cold (ECT 45 °C): 10 s after: RPM min 1138 mean 1254 (10 s before 931); IACV 9.4→15.2 %; ign 22.4→28.2°; inj 2.38→2.32 ms; STFT 7.1→-0.5 %; MAP 25→24 kPa
  - 22:38:56 cold→warm (ECT 86 °C): 10 s after: RPM min 680 mean 738 (10 s before 1251); IACV 14.1→6.4 %; ign 27.9→16.9°; inj 2.32→2.37 ms; STFT -0.3→-1.2 %; MAP 24→27 kPa
  - 22:41:08 warm→cold (ECT 41 °C): 10 s after: RPM min 657 mean 668 (10 s before 735); IACV 5.8→4.2 %; ign 17.7→17.7°; inj 2.42→2.44 ms; STFT 10.6→11.4 %; MAP 25→26 kPa
  - 22:42:03 cold→warm (ECT 77 °C): 10 s after: RPM min 664 mean 675 (10 s before 674); IACV 3.5→3.3 %; ign 17.1→17.0°; inj 2.46→2.45 ms; STFT 12.4→12.2 %; MAP 26→26 kPa
- ECT band false-cold <=50: ~73 s; RPM mean 801, p5 665, min 657; IACV 6.2 %; ign 19.4°; inj 2.42 ms; STFT 9.4 %; MAP 26 kPa
- ECT band mid 60-77: ~10 s; RPM mean 674, p5 666, min 664; IACV 3.4 %; ign 17.0°; inj 2.45 ms; STFT 12.0 %; MAP 26 kPa
- ECT band hot >=78: ~188 s; RPM mean 686, p5 658, min 651; IACV 4.3 %; ign 17.4°; inj 2.45 ms; STFT 11.1 %; MAP 26 kPa
- RPM min/mean/max 651/732/1345; IACV 2.7/5.1/15.7 %; ign 15.5/18.2/30.8°; inj 1.84/2.44/5.87 ms; MAP 20/26/34 kPa; TPS 7.8/7.8/14.9 %
- STFT -4.7/10.4/14.8 %; LTFT 6.2/7.9/9.4 %; O2 0.10/0.51/0.97 V; closed loop 100 % of samples
- IAT 51/52/52 °C; batt 13.88/14.18/14.55 V; alt_fr 31/56/80 %; ELD 13.2/15.2/24.2 A
  - fan: on 0 % of samples, 0 switches
- ELD step 22:40:40: 14 → 22 A; RPM 10 s before/after 664/756; IACV 5.4→7.9 %
- 1-min means:
  ```
  22:38 rpm921 ect71 iacv8.5 ign21.0 stft+4.4 ltft+6.7 eld15
  22:39 rpm665 ect90 iacv4.0 ign18.0 stft+12.0 ltft+7.0 eld15
  22:40 rpm708 ect71 iacv5.7 ign17.2 stft+11.4 ltft+7.9 eld17
  22:41 rpm675 ect60 iacv3.3 ign17.0 stft+12.5 ltft+8.8 eld15
  22:42 rpm666 ect81 iacv3.0 ign17.9 stft+12.4 ltft+9.4 eld14
  ```
- knock V 0.02/0.02/0.04
- RPM < 600: none
- link drops (link=0 episodes): 10 (2.14/min), >1 s: 0, longest 0.73 s
- data gaps (rpm stream): >0.5 s 5, >1 s 0 (0.00/min), >2 s 0, longest 0.99 s at 22:40:09; effective rpm rate 10.0 Hz

> **22:41:08–22:42:03 — new: a sustained false-cold reading that the ECU did NOT act on.** ECT jumped 83 → 41 °C in one sample and read
> 37–48 °C for 55 s, then snapped to 77 °C. Through that whole period RPM stayed at 657–675, IACV 3–4 % (it even fell), ignition ~17°, STFT
> +11–12 %. That is warm-idle behaviour, not the fast idle seen at 22:38:37 (45 °C → 1250 rpm, IACV 15 %, ign 28°).
> Looking back over every warm→cold drop on drives 29–30 (cold readings held long enough to judge):
>
> | time | 5 s before | first cold | held | RPM | IACV | ign | ECU acted? |
> |---|---|---|---|---|---|---|---|
> | 21:52:58 | 86 85 83 **58** | 35 | 18 s | 810→1046 | 7.5→10.9 | 17.5→23.5 | yes |
> | 21:53:36 | 64 63 64 64 | 34 | 4 s | 795→911 | 5.9→7.8 | 21.5→23.0 | yes |
> | 22:15:03 | 84 84 83 81 | 16 | 14 s | 688→679 | 4.7→4.3 | 18.3→16.6 | **no** |
> | 22:38:37 | 55 56 52 51 (slid down from 90) | 45 | 19 s | 931→1260 | 9.4→14.6 | 22.4→28.0 | yes |
> | 22:41:08 | 86 86 85 84 | 41 | 55 s | 735→672 | 5.8→4.2 | 17.7→17.2 | **no** |
>
> The two drops the ECU ignored both went straight from the hot plateau (~84 °C) to cold in a single sample. The drops it acted on came
> through an intermediate value (58 °C, or a slide through 50s) or started from the mid plateau. **Hypothesis:** the ECU checks ECT
> for plausibility/rate of change and holds its last good value when the reading jumps by ~40–70 °C in under a second, but a fault that
> arrives in steps gets through. That would also mean the logged byte at 0x10 is the raw converted reading, not always the value idle
> control uses. Five events is not enough to call it confirmed. Either way, this explains why some false-cold flips cause the 1050–1250 rpm
> fast idle and others do nothing.

### Window 22:42:57 – 22:44:45 (drive 30, snapshot age 168 s at analysis 22:47:33)

- drive row: status=complete end_reason=link_lost heartbeat=22:46:46 stall_count=1 capture_count=1
- ECT min/mean/max 37/79/88 °C; time-share warm(≥60) 90 %, false-cold(≤50) 10 %; state at end: **cold** (37 °C); samples 102
  - warm readings 76–88 °C (median 84); spec warm 80–93 °C (176–200 °F)
  - false-cold readings 37–38 °C (median 37)
- ECT transitions in window: **1**
  - 22:44:36 warm→cold (ECT 38 °C): 10 s after: RPM min 668 mean 678 (10 s before 664); IACV 4.3→4.3 %; ign 18.1→16.7°; inj 2.42→2.43 ms; STFT 9.6→11.4 %; MAP 26→26 kPa
- ECT band false-cold <=50: ~10 s; RPM mean 678, p5 670, min 668; IACV 4.3 %; ign 16.7°; inj 2.43 ms; STFT 11.4 %; MAP 26 kPa
- ECT band mid 60-77: ~4 s; RPM mean 668, p5 658, min 654; IACV 4.3 %; ign 17.7°; inj 2.43 ms; STFT 10.1 %; MAP 26 kPa
- ECT band hot >=78: ~95 s; RPM mean 684, p5 656, min 642; IACV 5.0 %; ign 18.1°; inj 2.45 ms; STFT 11.1 %; MAP 26 kPa
- RPM min/mean/max 642/683/796; IACV 3.1/4.9/8.2 %; ign 15.5/18.0/19.5°; inj 2.30/2.45/2.93 ms; MAP 24/26/32 kPa; TPS 7.8/7.8/7.8 %
- STFT 7.0/11.1/14.1 %; LTFT 9.4/10.2/10.9 %; O2 0.10/0.56/0.90 V; closed loop 100 % of samples
- IAT 51/51/52 °C; batt 13.68/14.26/14.64 V; alt_fr 48/60/82 %; ELD 13.2/15.6/23.8 A
  - fan: on 0 % of samples, 0 switches
- ELD step 22:44:00: 15 → 24 A; RPM 10 s before/after 663/752; IACV 4.7→8.0 %
- 1-min means:
  ```
  22:42 rpm662 ect83 iacv4.0 ign18.4 stft+11.5 ltft+9.9 eld15
  22:43 rpm712 ect74 iacv5.9 ign17.4 stft+10.4 ltft+10.7 eld17
  ```
- knock V 0.02/0.02/0.02
- RPM < 600: none
- link drops (link=0 episodes): 3 (1.66/min), >1 s: 0, longest 0.84 s
- data gaps (rpm stream): >0.5 s 7, >1 s 1 (0.55/min), >2 s 0, longest 1.29 s at 22:43:32; effective rpm rate 9.3 Hz
  - >1 s gaps at: 22:43:32(1.3s)
- events table rows for drive: 1; last: ['10', '30', '7154.751479300001', 'trigger', 'STALL (link lost)', '{"rule": "link_loss", "reason": "link", "message": "link los']

## !! 22:44:46 — third ECU link loss (capture `stall_20261125_224447`) — written up immediately

- Last ECU sample 22:44:45; the app fired `link_loss` at 22:44:47.6, kept logging link=0 until 22:46:46, then **closed drive 30
  (complete / link_lost)**. As of the 22:47:15 snapshot **there is no drive 31**, so nothing is being recorded right now.
- 30 s before: RPM 734–776 (22:44:15–23), down to 659–687 after IACV stepped 6.5 → 4.3 % at 22:44:25; last raw RPM 675–696,
  **rising slightly** into the cut. Battery 14.1–14.5 V to the last sample. ECT slid 87 → 76.5 °C over 20 s, then jumped to 38 °C at
  22:44:35 and read 37–38 °C for 10 s.
- Same signature as the 22:18 and 22:34 cable drops: data stop dead at a steady idle with the alternator charging, no RPM decay.
  **Most likely the loose OBD plug again, not a stall.** (Not confirmed by the user yet.)
- The 22:44:35 drop (76.5 → 38 °C in one sample, held 10 s until the link dropped) was **also ignored by the ECU**: RPM 672–687,
  IACV fixed at 4.3 %, ignition 16–17°. That is a third straight-to-cold drop with no fast idle, consistent with the plausibility
  hypothesis above. Note the ECU's IACV step-down at 22:44:25 (6.5 → 4.3 %) came 10 s *before* the logged drop, while the logged ECT
  was sliding 84 → 82 °C.

> **22:51–22:55:** the laptop dropped off the remote bridge ("device not connected"), so no snapshots could be taken. Retrying.

> **22:51–23:07:** the laptop stayed off the bridge on every retry (22:51, 22:52, 22:55, 22:58, 23:02, 23:07). The last data seen is
> drive 30, ending at 22:44:45. Monitoring stopped there.

## Summary (22:17–23:07 session; data covers 21:42–22:44:45, drives 29–30)

- **No engine stall at any point.** RPM never went below 600 (min 634). All three "STALL (link lost)" captures (22:18:19, 22:34:30,
  22:44:47) stop dead at a steady idle with the battery at 14+ V. The first two are confirmed as the loose OBD plug. The third has the
  same signature.
- **ECT fault confirmed and better described:** the reading sits on three levels, ~33–45 °C, ~63–70 °C and ~80–91 °C, and jumps between
  them within one sample. The ~85–90 °C level matches the thermostat (opens 76–80 °C, fully open 90 °C) and the FSM warm spec, so it is the
  true temperature. The other two are a connector fault adding resistance (reads colder). In total: 19 state changes on drive 29 and
  5 on drive 30.
- **The ECU's response depends on the band:** false-cold → ~1050–1250 rpm, IACV 11–15 %, ign 24–28°; mid → ~875 rpm, IACV 7 %;
  true-warm → 655–690 rpm, IACV 3–4.5 %, ign ~17.5° (bottom edge of the FSM 700 ± 50 rpm idle).
- **New: the ECU ignores some false-cold drops.** Three drops straight from the hot level to cold in one sample (22:15:03 for 14 s,
  22:41:08 for 55 s, 22:44:35 for 10 s+) caused no fast idle. Drops that passed through an intermediate value did cause it. This suggests
  the ECU rejects implausible jumps. It is a hypothesis, not confirmed.
- **New: fuel trim stays high at true-warm idle.** STFT is about +11–12 % on top of LTFT +10–13 %, so total trim is about +20–25 %. It
  shrinks at higher idle and airflow (total about +14 % at fast idle, LTFT drifted to +7 % during the ~810 rpm period). That points to
  an unmetered-air (vacuum) leak or low fuel pressure, separate from the ECT fault.
- **New: electrical-load idle-up.** ELD steps of 8–22 A (22:27:51, 22:33:01, 22:38:03, 22:40:40, 22:44:00) bring IACV up and RPM to
  ~750–810. After 22:27:51 the idle stayed at ~810 rpm for over 6 min. The ECM fan bit never came on. Load source unknown
  (thermoswitch fan or user loads).
- **Other readings:** IAT steady 51–53 °C. Battery 13.7–14.8 V, charging normally. Knock 0.02 V. Closed loop 100 %. MIL, A/C and VSS off.
- **Link quality, excluding plug pulls:** 2–5 short link drops per minute (0.1–0.3 s). RPM gaps over 1 s fell from about 0.94/min
  (21:45–22:16) to about 0–0.9/min afterwards, none over 2 s. Effective RPM rate 9–10 Hz.
- **Recommendations:** clean, dry and reseal ECT connector C122 (and fix the oil leak). Fix how the OBD plug seats. Smoke-test the intake
  for vacuum leaks and check fuel pressure.
