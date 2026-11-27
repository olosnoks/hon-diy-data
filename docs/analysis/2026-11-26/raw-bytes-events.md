# Unknown RAM bytes vs events: stalls, key-on, loads, ECT glitches (2026-11-26)

Data: a read-only copy of `hondalog.db`, taken 21:18 and refreshed up to 21:31:17 (stall list re-checked then: still only `stall_20261126_211054`). Drives `drive_2026_11_26_2045` (20:45:18–21:13:53, 1715 s) and `drive_2026_11_26_2114` (21:14:51–, still recording) read every address 0x00–0x3C: the slow blocks at ~1 Hz and rpm/MAP/trims at ~10 Hz. For key-on and stall context, I used the 2026-11-25 drives (6–30) and today's 20:31/20:34 drives, which read 0x08–0x11 (so 0x0D/0x0E), 0x27 and 0x28.

Scripts, tables and PNGs are in `docs/analysis/2026-11-26/work-events/`:
- `load.py`: raw bytes per address, plus a 1 Hz grid.
- `stalls.py` → `stall_tables.txt`, `stall_*.png`
- `events.py` → `event_response.txt`, `event_*.png`
- `overview.py` → `timeline_2045.png`, `timeline_2114.png`, `scatter_x29_iacv_x27_rpm.png`
- `stallsum.py`, `eldstall.py`, `fan.py`, `keyon.py`, `allv.py`

RPM is `16-bit / 4`, as in the project map.

## 0. Bottom line

1. **No unknown byte gave any warning before any link loss.** That covers the one capture with all bytes read and the eight older captures. The final 30 s are flat in every byte except the ones that follow RPM, IACV and ELD in real time. Those move only with an RPM/IACV/load change that is visible in the known channels at the same moment.
2. **Tonight's only "stall" capture, `stall_20261126_211054`, is not a stall.** The ECU went silent for 1.5 s (21:10:53.18 → 21:10:54.69). RPM was 645 before and 643 after, and the engine kept running. It looks like the 19:35 and 22:18 captures from 11-25. Drive 2045 has **96 short link drops** (median gap 0.2 s, one every ~12 s). None of them changes any byte (85-event average: every byte within ±1 count). These are K-line/adapter dropouts, not ECU events.
3. **No real engine death has been recorded with all bytes read so far.** From 20:32:12 (engine start) to 21:31:17 the engine ran the whole time. (21:13:53 is the end of drive 2045. RPM was **rising**, 700 → 1163, because of a false-cold ECT read when the app was stopped. Drive 2114 starts 58 s later at 770 rpm with IACV at 3.9 %, which does not look like an after-start.) **The unknown-byte question has to wait for a real stall in drive 2114 or later.**
4. **Most "unknown" bytes are dead.** 0x03–0x07, 0x0D, 0x0E, 0x16, 0x1A, 0x1B, 0x1D–0x1F, 0x2B–0x2E, 0x30, 0x31, 0x34 and 0x3B are always 0. 0x33 = 55, 0x35 = 45, 0x36 = 7 and 0x38/0x39/0x3A = 25 never change. Where 11-25 coverage exists (0x0D, 0x0E, 0x16), this held across key-on, cranking, after-start, A/C, fan cycles, ECT glitches and all nine link-loss captures.
5. **Five bytes are alive:**
   - **0x29**: unclamped IACV command. 0x28 is a monotonic function of it.
   - **0x1C**: an exact copy of ELD 0x19 (2,309 of 2,309 reads).
   - **0x27**: an RPM band.
   - **0x2A**: a slow value that moves ±1 every 13–30 min.
   - **0x2F**: a ~33 s tick counter that appeared only in drive 2114, starting 29–40 s after each large ECT snap-back.

   0x32 and 0x37 are 1-LSB noise (44/45) and respond to nothing.

## 1. Per-capture timelines (aligned on the last good read)

Tables (−30…0 s and +0…+30 s after the link returns, every byte) are in `stall_tables.txt`. Plots are `stall_<name>.png`: rpm; iacv/x29/x27; ect/eld/x1C; x2A/x32/x37/knock; map/batt/stft/ign.

| capture | last good read | final 30 s | link back | verdict |
|---|---|---|---|---|
| 18:17:01 (11-25) | OBD2-only drive | – | – | known key-off (prior report) |
| 18:55:15 | 18:55:13.6 | rpm 800–906, last 3 s 817–828. IACV 16, x27 0, ECT 78–81 °C, batt 14.1 V. No load step. 0x0D/0x0E = 0 | next drive +180 s: cranking 202 → 1638 rpm, x27 4, IACV 32 | engine off in the gap. No precursor |
| 19:29:07 | 19:29:06.0 | rpm 758–837, last 3 s 767–803. IACV 22 → 18. Load spike −22.6…−19.5 s (to 24 A) | next drive +197 s: rpm 0, then 220 → 1544 (crank), x27 4 | engine off. No precursor in 0x0D/0x0E/0x27 |
| 19:35:01 | 19:34:59.8 | rpm 660–680 | +2.6 s at 673 rpm | link artefact, engine running |
| 20:13:45 | 20:13:43.7 | rpm 797–818 steady. **At −2 s: ELD 14 → 38 A, batt 14.2 → 13.7 V, IACV 18 → 36 (kick), MAP 48**. Last read 855 rpm, x27 1 | next drive +253 s: 1078 rpm, IACV 20 (after-start) | engine off. Large electrical load switched on 2–4 s before the silence (see §4) |
| 21:16:10 | 21:16:09.0 | rpm 764–832. Load 15 → 29 A from −10.1 to −3.0 s | next drive +233 s: 958 rpm, IACV 27 | engine off |
| 22:18:19 | 22:18:17.7 | Load to 24 A at −28…−23 s with IACV kick 12 → 20. IACV back to 11 at −1 s, rpm 748 → 693 in the last second | +17.9 s at 700 rpm, IACV 12 | engine kept running: **18 s link artefact** |
| 22:34:30 | 22:34:28.9 | rpm 795–825 flat | next drive +210 s already at 800 rpm, IACV 18 | no precursor |
| 22:44:47 | 22:44:45.9 | False-cold ECT (raw 116–119, ~30 °C) from −9 s. rpm 673–687 flat | (end of night) | no precursor |
| **21:10:54 (11-26)** | 21:10:53.18 | rpm 700–720. At −3 s: ELD 10 → 17 A, x29 39 → 47, IACV 0 → 1, rpm 720 → 665 → 720 (normal load kick). Every other unknown byte constant | **+1.5 s at 643 rpm** | **link artefact, engine running.** x1C/x29 moved only with the ELD step |
| (21:30:27, drive 2114, not a capture: deepest sag tonight) | 21:30:27 | 70 s of false-cold (ECT raw 119–129) fast idle at 1040–1100 rpm, IACV 20–29, x29 92–101, x27 4, x2A 4 → 5 at −17 s | – | ECT snaps to raw 40 (~80 °C). Within 5 s: IACV 20 → 12 → 7 → 5 → 4, x29 92 → 64, rpm 1040 → 903 → 816 → 723 → **685**, x27 4 → 0. Then x29 decays to its floor (38) and IACV to 0 by +13 s. Idle settles at 705–730 with **IACV closed**. x2F starts counting at +40 s. No stall (`stall_event_2114_936.png`) |
| (21:13:53, end of drive 2045) | 21:13:53.8 | −7 s: ECT raw 45 → 85 (76 → 51 °C false-cold). −6.3 s: x29 39 → 50 → 82 → 103. IACV ramps 0 → 32 at +6 raw/s. rpm 700 → 1163, ign +4°, x27 0 → 4 | app stopped. Drive 2114 +58 s at 770 rpm | ECU fast-idle response to a false-cold ECT read, not a stall |

`stalls.py` flagged any target byte that moved in the final 30 s or sat outside its own 300 s baseline in the last 5 s. Only 0x1C (= ELD) and 0x29 (= IACV command) were flagged, and only alongside the same move in 0x19/0x28.

## 2. Bytes vs events

Event-triggered means are in `event_response.txt` and `event_*.png`. Times are relative to the event sample (1 Hz grid, so ±1 s).

| byte | key-on / crank | after-start | ELD load step (n=20) | ECT false-cold entry (n=6) | ECT snap back warm (n=8) | IACV step (n=7) | short link drop (n=85) | brake bit 0x08.3 (n=3) | knock 0x3C 1→2 (n=72) |
|---|---|---|---|---|---|---|---|---|---|
| **0x1C** | not read | not read | **= ELD, same sample** | – | – | = ELD | none | none | none |
| **0x29** | not read | not read | **+8 at 0…+1 s, held** | **+12 within 5 s** | **−14 within 5 s** | +10 (it drives it) | none | +10 at 0 s | none |
| **0x27** | **5 at key-on (engine off), 10 cranking (11-25), 4 just after start** | 4 → 0 in ~10 s when warm. Stays 3–4 for minutes at 61–66 °C (fast idle) | none | +1 at +5…12 s (follows rpm) | −1.4 at +7 s (follows rpm) | +1.3 at +15 s | none | none | none |
| **0x2A** | not read tonight (6 on 11-25 18:17 probe) | – | none | none | none | none | none | none | none |
| **0x2F** | not read | – | 2 of 6 increments in the same second as an ELD step | resets at 21:25:07 and 21:28:40 coincide with ECT steps | **episodes start 29–40 s after a large snap-back (3/3, drive 2114)** | none | none | none | none |
| 0x32, 0x37 | – | – | none | none | none | none | none | none | independent of 0x3C |
| 0x03–07, 0D, 0E, 16, 1A, 1B, 1D–1F, 2B–2E, 30, 31, 33–36, 38–3B | 0x0D/0x0E/0x16 = 0 at key-on, crank, after-start (11-25/20:31) | constant | constant | constant | constant | constant | constant | constant | constant |

Known channels, for reference:
- **ELD step:** ALT_FR +8 at 0 s; rpm dip ~20 rpm, recovered in 1 s.
- **False-cold ECT:** rpm +87 within 5–10 s; STFT −4.
- **Snap-back to warm:** rpm −92 within 5 s; ign −5.
- **Brake bit:** at 21:09:17–22, rpm ±35, ALT_FR +20.

Tonight's loads are small. ELD peaked at 16.8 A, compared with 24–42 A fan spikes on 11-25. No A/C was used tonight. Closed loop (0x0F) stayed set for the whole of both drives, so there was no closed-loop entry to test.

## 3. Best-guess meanings

| addr | behaviour | best guess | confidence |
|---|---|---|---|
| 0x29 | 36–106. 0x28 (IACV) is a monotonic, slightly lagged function of it: 0x28 = 0 while 0x29 ≤ ~40, ~5 at 70, ≈ 0x29 − 72 above ~80 (scatter plot). Moves first; 0x28 follows, rate-limited to ~6 raw/s | **IACV command before output mapping and clamping (idle-air controller output)** | high |
| 0x1C | identical to 0x19 in every read | ELD copy (raw or filtered buffer that equals 0x19 at this read rate) | high |
| 0x27 | Running engine: clean RPM quantiser. 0 below ~845, 1 at 845–900, 2 at 900–950, 3 at 950–1010, 4 at ≥ 1010 (saturates). Same thresholds in both drives at different ECT. 5 at key-on with the engine stopped, 10 while cranking | **RPM band / RPM-indexed table index**, probably for idle ignition or IACV scheduling. The project map calls it `ign_limit`, which looks wrong | medium (the function is high; its purpose is unknown) |
| 0x2A | Changes are rare, in steps of 1: 6 (11-25 18:17 probe, hot), 6 at 20:45 → 5 at 21:02:29 → 4 at 21:17:47 → **5 at 21:30:10**. No short-term response to any event | **slow adaptive or learned value, or a slowly filtered table index (ECT/IAT) with hysteresis; not a timer.** One idea: an idle-air learned offset that steps down while the IACV is pinned at 0 (drive 2045 ran ~51 % of the time with 0x29 at its floor and 0x28 = 0). **But it is not monotonic: it went back up 4 → 5 at 21:30:09.98, during a sustained false-cold fast idle** | low |
| 0x2F | 0 for all of drive 2045 (27 min). In drive 2114: 0 → 1 (21:23:28) → 4 (21:24:00) → 6 (21:24:35) → 0 (21:25:07); 0 → 1 (21:28:04) → 4 (21:28:38) → 0 (21:28:40). A third episode started at 21:31:07. Steps are ~32–35 s apart. **Each of the three episodes started 29–40 s after a large ECT snap-back to warm (21:22:51, 21:27:35, 21:30:27) and reset on the next ECT jump.** It did not appear after drive 2045's smaller snap-backs (6–10 °C) | **a diagnostic monitor counter that ticks per evaluation window** (idle-control deviation, or ECT rationality, both of which tonight's ECT glitches would trip). Could also be a misfire window count. Watch for a MIL or stored code | low |
| 0x32, 0x37 | 45 with independent 1-LSB flicker to 44 (4 % / 7 %). No event response | live ADC channels of a steady input, e.g. a reference or unused analog pin; ~0.88 V if 0–5 V | low |
| 0x33, 0x35, 0x36, 0x38–0x3A | fixed 55, 45, 7, 25, 25, 25 | constants / calibration copies in RAM | medium |
| 0x03–07, 0D, 0E, 16, 1A, 1B, 1D–1F, 2B–2E, 30, 31, 34, 3B | always 0, including key-on and crank where read | unused or unpopulated features (cruise, EGR, auto-trans, etc.) | high that they are useless for this fault |

## 4. Timers and counters

- **There is no free-running or wrapping counter in 0x03–0x3B.** Nothing increments steadily, so no key-on, crank, after-start or warm-up timer is visible there. The after-start behaviour sits in IACV (0x28/0x29) and 0x27 (5/10 → 4 → 0), and RPM drives 0x27.
- **0x2A:** one step per ~13–30 min, both directions (3 steps observed: down, down, up).
- **0x2F:** ticks at ~32–35 s while active, then resets.
- **Electrical loads before link loss:** brief load spikes (≥ 8 A, median 1.1 s long) occur about once per 97 s. Four of nine captures had one in the last 25 s, against ~2 expected by chance (not significant). The tightest case is **20:13:45**: 38 A spike, batt dip to 13.7 V and IACV kick 2–4 s before the ECU went silent. That fits, but does not prove, the power/ground hypothesis from the 11-25 report.

## 5. What to do next

- Keep logging drive 2114 with all bytes. The first real engine death with the ECU still answering, or with a clean link loss, will settle the question. The things to check at that moment are 0x29/0x28 (did the controller saturate?), 0x2F (was the monitor counting?) and ELD/batt in the final 2 s.
- To shorten the slow-block period, the dead addresses (list in §3) can be dropped from the read plan. That would bring 0x10/0x17/0x19/0x28/0x29/0x2F to ~2–3 Hz.
- **Warm idle runs with the idle-air valve shut.** Whenever ECT reads warm (~75–80 °C), 0x29 sits at its floor (36–41) and 0x28 = 0, yet RPM stays 700–850, above the 670 ± 50 spec. Together with the +18–20 % fuel trims from 11-25, this points to air that bypasses the IACV (a vacuum leak, a fast-idle thermo valve not fully closed, or a throttle-stop/bypass setting). It also means a sudden load, or the end of a fast idle, has to wind the controller up from the floor through a low-gain zone (0x29 ~40–72 gives only 0–5 raw IACV) before real air arrives. That is a plausible way for a sag to become a stall. **This is a hypothesis to test on the car (smoke test; check the FITV and throttle stop), not something the logs prove.**
- The ECT circuit fault is still the dominant idle disturbance tonight. Every large IACV/RPM move in both drives starts with an ECT step.
