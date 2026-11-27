# The separate ~810 rpm warm idle state (2026-11-27)

**Question.** With a correct warm ECT reading, the idle sometimes sits at ~810 rpm instead of ~670 (11-25: 55-69 % of the
time). What switches it on and off?

**Answer, in one line.** It is the **EVAP canister purge at idle**. About 7-10 min after the last "purge reset" (engine
start, rpm above ~1,000, or an accepted ECT reading below ~68 °C), the mixture starts going rich at an unchanged 670 rpm.
A few minutes later the ECU raises the idle target to 810 rpm and holds it until the next reset. On 11-26, the 0x2F ramp
reached its top value 5 s and 12 s before the two steps that were logged with it, so 0x2F is the purge enable timer or ramp.
It is not the purge duty itself. A one-line rule (810 when ≥ 450-480 s since the last reset) gets **96.5-97 % of 6,893
classified idle seconds** on 11-25 right. It catches **9 of 9 entries** (delays 410-588 s) and **every observed exit**
(3 resets during a visible 810 state gave 3 exits, plus 4 of 4 restarts and 1 pull-away). Not one 670 stretch lasted longer
than 588 s after a reset.

**Data and method.** I worked on a read-only copy of `hondalog.db` (the 11-26 23:33 file, the same data as `work-compare`;
there was no -wal or -shm file), in `work-810/` (git-ignored). Only the raw `ecu_reads` blobs were used. `build.py` builds a
1 Hz grid at every ECT read: all bytes 0x00-0x3F, 0x76-0x77 and 0x9A-0xA0, with fast bytes averaged over the second before
and slow bytes taken as the last value (≤ 3 s old). It also keeps fast rpm at ~10 Hz. Drives 6-39 are used. No GPS data is
used. Scripts: `build.py`, `ctx.py` (engine runs, fan cycles from ELD steps, revs, ECT glitches), `trans2.py` (L/M stretches
and transitions), `resets.py`, `rule.py`, `bytes_ev.py`, `trimcourse.py`, `trim_tsr.py`, `revcount.py`, `dump.py`
(second-by-second dump of every column around any time), `plots.py`. The plotting venv is `work-810/venv`.

**Bytes that were not logged.** On 11-25 the logged bytes were 0x00-0x02, 0x08-0x15, 0x17-0x19, 0x20-0x28 and 0x3C. **0x29,
0x2A, 0x2F and the idle target were not logged that night.** On 11-26 everything from 0x03 to 0x3C was logged, plus 0x9A-0xA0
and, from drive 2158, the target.

**State definition.** Steady idle: vss 0, TPS < 9 %, 550-1,300 rpm, A/C clutch off, more than 30 s after a start, not in a fan
cycle or the 10 s after one. Believable warm ECT: raw ≤ 44 (≥ 77 °C) and no read above raw 46 in the last 60 s. At that
reading the ECT term of the target is ≤ ~720 rpm, so an 810 idle cannot come from it.
- L means a 9-s median rpm below 735.
- M means 780-850 rpm. On 11-26 an IAC command at its floor (39-41) does not count as M: that is rpm above target from
  excess air.

On 11-25 the radiator fan came on at a reading of 84.8 °C (23 cycles), so the contact was mostly good. Drive 2137 (d35,
11-26) also read 83.6-85.9 °C at fan-on. In drive 2158 (d37, 11-26 22:18) the reading was ~13 °C low. That event is still
usable because the target was logged there: the ECU acts on its own reading, and the 810 floor shows directly as the target
at raw 48. (The ECT-only value at raw 48 is 778, seen at 22:03:55.)

## 1. Every entry and exit

**Entries.** The 9 entries below are every one found. "Reset" is the last engine start, rpm above 1,000, or accepted ECT
reading of 56-99 raw (≤ 67 °C) before the step.

| # | step at | drive | last reset (kind) | s since reset | revs since reset | fan cycle near the step | 0x2F | ECT raw | IAT °C |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 11-25 18:45:25 | d11 | 18:37:38 (accepted false-cold 72) | 467 | 5,326 | fan-off 93 s before | – | 34 | 47 |
| 2 | 11-25 19:19:43-20:13 | d20 | 19:13:08 (rev 2,698) | 425 | 4,782 | step inside the 19:19:43 fan cycle | – | 37 | 46 |
| 3 | 11-25 19:41:19 | d21 | 19:32:23 (start) | 536 | 6,082 | none (160 s) | – | 36 | 48 |
| 4 | 11-25 20:07:58 | d22 | 20:00:45 (rev 1,926) | 433 | 4,917 | none (396 s) | – | 33 | 49 |
| 5 | 11-25 20:25:23-26:15 | d23 | 20:17:50 (start) | 505 | 5,710 | step inside the 20:25:23 fan cycle | – | 33 | 50 |
| 6 | 11-25 20:36:04-22 | d25 | 20:29:32 (accepted 57) | 410 | 4,684 | step inside the 20:36:04 fan cycle | – | 41 | 50 |
| 7 | 11-25 22:27:53-28:23 | d29 | 22:18:47 (accepted 81) | 576 | 6,540 | step inside the 22:27:53 fan cycle | – | 37 | 51 |
| 8 | 11-26 21:44:17 | d35 | 0x2F start 21:35:08 (last rev 21:34:32) | 549 from 0x2F start (585 from rev) | 6,195 (+~540 in a 48 s log gap) | none (154 s) | 26 → **27 at 21:44:12** | 35 | 48 |
| 9 | 11-26 22:18:06 | d37 | 0x2F start 22:10:45 | 441 from 0x2F start | 6,049 | 4 s after fan-off (fan 22:17:45-22:18:01) | 24 → **25 at 22:17:54** | 48 (low offset) | 52 |

- Every step is the same: IACV +3.1 % (range +0.5 to +6.6, 9/9 positive), or iac_cmd 49 → 84 at #8. rpm reaches 800 within 2-3 s.
- At #9 the target goes 749.75 → 809.75 at an unchanged raw 48.

**Exits.**

| # | last 810 | first 670 | cause |
|---|---|---|---|
| a | 11-25 18:55:13 | 18:58:44 | restart |
| b | 19:29:05 | 19:33:59 | restart |
| c | 20:00:45 | 20:00:48 | rev to 1,926 (the "rev to ~1,865" in earlier notes) |
| d | 20:13:43 | 20:18:20 | restart |
| e | 20:28:47 | 20:30:39 | accepted false-cold, raw 56-67 at 20:28:56-20:29:32 (ECT read ≤ 67 °C) |
| f | 21:16:08 | 21:20:06 | restart |
| g | 22:38:28 | 22:39:57 | rev to 1,345 at 22:38:55 together with a raw-104 reading |
| h | 11-26 21:45:16 | – | false-cold excursion (raw 45-152); 0x2F 26 → 0 at 21:46:53 when raw hit 56. The reading never came back warm, so the state after is not visible |
| i | 11-26 22:39:47 | – | first pull-away: 0x2F 25 → 0 at 22:39:48, target 810 → 750 at 22:39:51 at an unchanged raw 49 |

**What did not end it.**
- False-cold readings of ≥ ~120 raw, which the ECU ignores (`slow-bytes.md`). The 810 state ran straight through two of
  them in d22 (19:48:00-19:49:28 and 19:50:25-19:51:57, raw up to 127).
- Five app reconnects that left `tps 8` artefacts at the end of a log.
- 40 min of continuous 810 in run 3 (20:36-21:16, d25-d27) with no reset at all.

The old trigger list (fan cycles, a load sag, "spontaneous"; cleared by restart, rev, snap-back, pull-away) is the same set of
events. The fan and "spontaneous" entries are timer expiries. The four clearing events are all purge resets.

`fig1_1125_timeline_rule.png` shows the whole 11-25 evening: rpm, the rule's prediction, the resets and the fan cycles.

## 2. The hypotheses, tested

The before/after numbers are means over idle, non-fan seconds 5-60 s after the step minus 60-5 s before it. "All 9" means the
sign was the same in all 9 entries.

| hypothesis | numbers | verdict |
|---|---|---|
| **EVAP purge** | **Timing:** all 9 entries come 410-588 s after a reset, and every reset seen during an 810 state ended it. **Mixture:** at 670 rpm, total trim (STFT+LTFT) is 21.2-22.9 % for the first 300 s after a reset, then falls to 20.8 (360-420 s), 19.9 (420-480 s) and 19.3 % (480-600 s) **before** the idle moves. In the 810 state it is 15.8 (480-600 s) and 14.8 % (600-900 s), then recovers slightly to 15.5 % (canister emptying?) (`fig2`). Against the rpm-only fit the residual goes +4.7 → +1.0 at L, then −1.6 at M. d35 with a good reading: 26.7 % (0x2F 11-22) → 24.4 % (22-25) → 18.8 % (25-27) at 672-684 rpm, then 14.5 % at 812 rpm after the step. d37: 18.4 → 16.4 → 14.9 %. Across all 11-26 idle, trim is +20.6-23.1 % while 0x2F is 0-22 and +15.6-16.1 % at 0x2F ≥ 23. **Manual:** p.491 says "If the idle speed increases to 810 ± 50 rpm, this means the EVAP system is purging the canister", and raising the rpm above 1,000 stops it temporarily. That matches exit c/g. **Enable:** 0x2F starts at an accepted reading ≤ 53 raw (≥ 69 °C) and resets at ~56 raw (~67 °C). That is the manual's ECT > 68 °C purge enable | **Fits.** Best explanation. Not proven until the solenoid is unplugged (section 4) |
| Electrical-load idle-up latch (ELD, alt_fr, 0x0C b0) | ELD change at the step −0.04 A (−0.6 to +0.6, same sign in 5/9). ELD just before: 13.1-15.7 A on 11-25 and 9.6-9.7 A on 11-26, the same as at L. 0x0C never changed within ±120 s of any entry. alt_fr fell 10 % at all 9, but that follows the rpm (higher alternator speed). A separate electrical idle-up does exist: the target went 746.5 → 749.75 (the manual's 750 headlight value) the second the fan came on at 22:17:45. That is a different, 750-rpm term | **Rejected** as the trigger |
| After-fan or heat-soak logic (IAT, time since fan) | IAT 46-52 °C and unchanged (Δ +0.04 °C). Time since fan-off at the step: 2-36 s for 5 entries, 93-396 s for 4. 5 of 9 steps came during or just after a fan cycle, against ~3 expected by chance (the fan is on or just off ~30 % of the time). That is weak (p ≈ 0.1) | **Rejected as the cause.** Possible **secondary gate**: the ECU may hold the purge start while the fan load is on (#9: 0x2F was already 25 during the fan and the step came 4 s after fan-off). Unproven |
| O2 heater state (0x0B b6) | 0x0B = 65 constant within ±120 s of all 9 entries | **Rejected** |
| Learned idle air (0x2A) | 0x2A follows the step (1 → 6 in 36 s at #8) and did not move before it. At #9 it sat at 5 → 4 | **Rejected** (it is an effect, not a cause) |
| Timer since start | 505-6,354 s since start. The timer is since the **last reset**, and starts are only one kind of reset | **Rejected** as stated. Correct form: time since last reset |
| A/C or heater-fan request without the A/C bits | 0x08 b1 and 0x0B b1 never set. Heater fan HI would add 10 A or more of ELD; ELD was unchanged | **Rejected** |
| IACV feedback reset | The step is a clean one-read jump in iacv/iac_cmd (the response). 0x2A is not reset and nothing else in 0x28-0x2A drops | **Rejected** |
| Something in an undecoded byte | Over all raw bytes 0x03-0x3F, only bytes that follow rpm or the IAC change consistently at the step: MAP 0x12 (−1.4 counts), alt_fr 0x18, inj 0x25, IACV 0x28/0x29, STFT 0x20 (−1.5 %, 9/9) and LTFT 0x22 (−0.8 %, 8/9). **No flag bit in 0x08-0x0F changed within ±120 s of any entry.** The only precursor is **0x2F** (2/2: its last increment came 5 s and 12 s before the step) | Nothing new. 0x2F is the timer |
| A once-per-trip latch or the EVAP leak test (coordinator's question) | It recurs 2-3 times per engine run (run 2: on 19:41, cleared by the rev at 20:00, back on at 20:07). It lasts 2.5-40 min. A leak test is a one-shot of a minute or two, and would not be cleared and restarted by every rev | **Does not fit.** The tank pressure sensor (A29) is not logged, so a leak-test phase inside the purge sequence cannot be excluded |

**Two things the purge reading has to explain.**
- **"Purge needs vehicle speed > 0" (p.374/536).** Every 11-25 entry happened with the car never having moved that night
  (vss 0 for all 3.9 h). So this ECU purges at idle while parked, as the p.491 note describes. The speed condition must
  belong to the normal off-idle purge duty, not to this idle mode. The ECT > 68 °C condition does fit.
- **"0x2F was flat at the 21:44:17 step."** It was flat because it had just finished: 22 at 21:39:19, then 23, 24, 25, 26,
  and 27 at 21:44:12, five seconds before the step. #9 is the same (25 at 22:17:54, step at 22:18:06).
  - So 0x2F is the purge **ramp or enable timer**, which saturates at 25-27. It is not the instantaneous solenoid duty.
  - The mixture goes rich while 0x2F climbs past ~22, which suggests the solenoid starts flowing during the last part of
    the ramp. The 810 idle-up switches on when the ramp completes.
  - While driving, 0x2F climbs fast with airflow (`drive-bytes.md`). That fits a ramp that counts engine air, or
    revolutions, rather than time.

## 3. The rule

**On:** idle, ECT reading ≥ ~69 °C, and **≥ 450-480 s since the last reset**. With 0x2F logged: **0x2F has finished its
ramp** (no increment for one tick period at ≥ 25). The step comes 5-12 s after the last increment.

**Off (reset):** any of
- engine start;
- rpm above ~1,000: revs, the pull-away, and probably decel fuel cut and throttle changes, since 0x2F clears on those while
  driving;
- an accepted ECT reading of ≥ ~56 raw (≤ ~67 °C).

Readings of ≥ ~120 raw are ignored, and do not reset it.

| τ (s since reset) | accuracy, 11-25 (6,893 s, 50 % M) | M recall | L recall |
|---|---|---|---|
| 420 | 95.0 % | 99.9 % | 90.1 % |
| 450 | 96.5 % | 99.0 % | 94.1 % |
| **480** | **97.0 %** | 97.4 % | 96.5 % |
| 560 | 94.1 % | 88.3 % | 100 % |

- Including the 11-26 good-reading seconds, the 480-s rule scores 95.8 % on 7,442 s.
- Every misclassified stretch is the entry delay itself: the rule says "on" up to 168 s before the step. There is no false
  "off" in the middle of a state.
- The delay spread (410-588 s, or 4,700-6,500 revolutions) is about ±20 %. Revolutions do not tighten it: the two 0x2F-timed
  entries are 6,049 and ~6,700 revs, against 441 and 549 s.
- The spread probably comes from the 25-50 s start delay of 0x2F, the "+1 per 37-76 s" tail of the ramp, and possibly the
  fan gate.
- One boundary is uncertain. Whether the ECU accepts the 100-119 raw readings decides one entry (#6): 410 s if they are
  ignored (as assumed), 330 s if a raw-108 reading counts.

**Consequences.**
- After the ECT is fixed, a warm parked idle will sit at 670 for ~7-10 min after each start or rev, then at ~810 until the
  next rev.
- Each 810 period is also 6-8 points richer in trim. The "M state ~4 % richer" finding is purge vapour, so trims logged at
  810 should not be read as the engine's own fuel error.
- While moving, the 810 floor never appears: 0x2F is reset at every throttle change.

## 4. What would confirm it

1. **The manual's test (decisive).** During an 810 period with a warm reading, unplug the EVAP purge control solenoid 2P
   connector (ECM A6). Expect the target or rpm to fall back to ~670 within seconds, or at the next ramp evaluation, and trims
   to rise 5-8 points. If the idle stays at 810 with the solenoid unplugged, the idle-up is a flag the ECU sets on its own.
   It would then be gated by the same timer but would not need flow. Either result settles which of the two it is.
2. **Log 0x2F at ≥ 2 Hz** (it is now 1 Hz in `27+14`) together with `76+2`, and **0x28-0x29 at 5 Hz**, to time the last 0x2F
   increment against the target step. At 1 Hz the 5-12 s lead is only just resolved.
3. **Find the purge duty byte.** Run one 0x40-0xFF sweep (`poke_test.py` or the wide sweep) across an L → M step, and a second
   one 30 s after a rev. A byte that is 0 at L, rises late in the 0x2F ramp and holds in M is the PCS duty. The tank pressure
   sensor (A29), the bypass valve (A3) and the vent shut valve (A4) states are also somewhere in RAM. Finding them would show
   whether a leak-test phase runs inside the sequence.
4. **Fan gate.** Watch a few entries with the target logged. If the step always waits for fan-off when 0x2F completes during
   a fan cycle, add "and no fan / ELD step in progress" to the rule.
5. **Refuelled or hot tank.** More vapour should give a deeper trim drop in the 810 state. The rpm should be the same.

## 5. Changes to earlier beliefs

- `idle-vs-drive.md` belief 4: "separate ~810 state, trigger and cause low, EVAP purge the leading guess" moves to **"EVAP
  idle purge, entered 7-10 min after the last reset, cleared by start, rpm above ~1,000 or ECT reading < 68 °C"**. Confidence
  medium-high: the timing and the trim drop agree, and the solenoid test is still to do.
- `slow-bytes.md` / `drive-bytes.md` 0x2F: change "purge duty?" to **"purge ramp / enable timer"**. It saturates at 25-27 at
  idle, and the 810 purge idle-up follows its completion within 5-12 s. The "enable condition OPEN" (IAT ~41 °C against the
  first long cold excursion) is answered by the reset logic: before 21:23 on 11-26 the ECT reading never stayed ≤ 53 raw
  long enough without a reset.
- The parked "810 floor" before the 11-26 drive (target flat at 810 for raw 46-51) was this purge idle. It was on from 22:18:06
  and dropped at the first pull-away. That drop came from 0x2F resetting, not from the ECT.
- A separate 750-rpm electrical idle-up exists (target 749.75 the moment the fan load came on, 22:17:45). It is different
  from the 810 state.

## Files (`work-810/`)

- `fig1_1125_timeline_rule.png`: 11-25 rpm with the rule (≥ 450 s since reset), the resets, the fan cycles, time since reset
  and the ECT reading.
- `fig2_trim_vs_time_since_reset.png`: total trim and the residual against the rpm-only fit, against time since reset, for
  the L and M states.
- `fig3_1126_entries_0x2F.png`: the two 11-26 entries (d35 with a good reading, d37 with the target logged): rpm, target,
  0x2F, trims, iac_cmd and ELD.
- `segments.txt`, `d37_target_steps.txt`: the state segments and every target change in d37/d38.
- `T.pkl` / `T2.pkl`, `ctx.pkl`, `resets.pkl`, `rule.pkl`, `trim_tsr.pkl`: intermediate data (git-ignored). Run order:
  `build.py`, `ctx.py`, `resets.py`, `rule.py`, `trim_tsr.py`, `plots.py`.
