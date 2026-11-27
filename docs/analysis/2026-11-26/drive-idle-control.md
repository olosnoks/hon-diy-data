# Idle control at every launch and every return to idle (drives 2224 and 2251)

2000 Civic EX, D16Y8, stock P2P ECU, manual gearbox. Data: `drive_2026_11_26_2224` (20.8 min,
the first ~15 min standing at idle) and `drive_2026_11_26_2251` (35.8 min, mostly moving). The
work is in `work-idle/` (scripts, CSVs, PNGs). The database copy, the venv and the `.npz` extracts
are not kept in git: `load.py` rebuilds the extracts from a copy of `hondalog.db`.

Channel timing: rpm, vss, map and tps about 12 Hz; idle_target about 5 Hz; iac_cmd (0x29), iacv
(0x28), ect, eld, brake about **once every 1.04 s**. So the timing of the IAC response is only
known to within about 1 s. A latency of "0.3 s" below means the first read after the event already
showed the change.

## Summary

- **Events found:** 10 launches from a standstill (2 + 8), 5 clutch take-ups while still rolling
  at 3-5 km/h, 10 arrivals at a stop (2 + 8), 6 coast-downs with the clutch in while rolling at
  8 km/h or more, and 9 small dips at a standstill with no launch (mostly electrical load steps).
- **Every drop below 600 rpm (7 bites in 6 launches) happened when the clutch bit from a
  standstill with the throttle closed.** That is all 7 of 7 such bites (one first bite that was
  given up early stopped at 720). Standstill launches with some throttle already open bottomed at
  670-906 rpm. Take-ups while still rolling at 3-5 km/h with a closed throttle bottomed at
  735-799.
- **The deepest and fastest drops (440-519 rpm, falling at 560-1,690 rpm/s) came when the clutch
  bit while the rpm was already falling back from above the target**, with the ECU taking air
  away at that moment:
  - after a first bite that was given up, whose IAC kick made the rpm overshoot
    (22:53:15 and 23:26:12);
  - during the rev-hang right after a stop (23:25:56);
  - right after a throttle blip (23:26:24).
- **Correction to the README:** 3 of the 6 deep dips (22:39:41, 23:25:56, 23:26:12) were **not**
  caught with throttle. The throttle stayed closed and the engine recovered by itself: the
  driver let the clutch slip and/or the car started to roll. The "idle hunting" before 22:53:15
  was not the idle hunting on its own. It was a first clutch bite (MAP 26->35 kPa, rpm 794->720)
  that the driver gave up, followed by the ECU's IAC kick and its removal.
- **Returns to idle are not risky on their own.** No arrival went more than 51 rpm below target.
  Coast-downs while rolling never went below target. After a stop the rpm hangs at 1,100-1,360
  for 2-6 s and then sinks to target. A launch during that sink is the A4 case.
- **The idle loop is slow and sits near the closed end of the valve.** At a standing warm idle
  iac_cmd has a median of 87 (83-98), so the valve output (iacv) is about 5-11, close to its floor
  of ~2 at iac 71-78. While driving the ECU uses up to iac 217 (iacv 78).
  - The feedback part of the loop adds about 2-3 counts per second when the rpm is 50-100 below
    target. That is worth roughly 10-50 rpm, against 300-425 rpm lost in 0.8-2.5 s.
  - The only fast action is a one-off kick of +9 to +23 counts when MAP rises. The ECU takes the
    kick back within 1-2 reads.
  - Nothing suggests the valve sticks: the output follows the command at every read, and the rpm
    rises after every kick.
- **Closest calls.** Taking 400 rpm as the stall line (an assumption):
  - A6, 23:26:24.2: 440 rpm, still falling at 770 rpm/s with 0.05 s to go. The driver had
    already reopened the throttle.
  - A5, 23:26:12.5: 495 rpm, falling at 720 rpm/s with no throttle at all. It turned round
    because the car began to roll.
  - A2, 22:53:15.9: 463 rpm. The throttle came in 0.1 s after the minimum.

## 1. Event tables

### 1a. Clutch bites from a standstill with the throttle closed (the near-stalls)

"Before the fall" is the highest rpm in the 2.5 s before the minimum, with its excess over the
target. "Fall" is the steepest drop over about 0.35 s. "Under" is target minus minimum. "Below
tgt / <600" is how long the rpm stayed below the target and below 600 rpm around the minimum.

| id | time | before the fall | min rpm | under | fall rpm/s | MAP kPa | throttle | below tgt / <600 s | iac_cmd (iacv) and response | ECT °C / target | ELD A | s since start |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | 22:39:41.3 (2224) | 904 (+94) | **546** | 264 | -498 | 26.5->36.5 | none for 8 s; launched on idle air; bites at 546, 590 (22:39:44.0), 662 (22:39:48.6, 4 km/h) | 8.2 / 0.42 | 99 (9.8) -> 101 at the dip, 105 at +5 s, 113 at +11 s | 72.9 / 810 | 24.2 | 903 |
| A2 | 22:53:15.9 (2251) | 1,022 (+199) after the given-up bite at 22:53:12 (720 rpm) | **463** | 361 | -564 | 24.4->40.8 | closed until the min, 15.7 % at +0.2 s | 1.5 / 0.72 | kick 90->112 for the first bite, then pulled to 95, 89 (*down* as the rpm fell); 92 at the dip, 98 at +0.5 s | 70.5 / 824 | 23.5 | 126 |
| A3 | 23:12:37.5 | 915 (+51), brake released at the bite | **554** | 311 | -759 | 25.8->38.7 | closed at the start of the fall, 10.6 % at the min | 0.7 / 0.15 | 81 (3.1) -> 85 at +0.56 s | 65.4 / 864 | 29.8 | 1,288 |
| A4 | 23:25:56.2 | 1,213 (+349), 3 s after stopping, during the rev-hang | **519** | 345 | -707 | 25.1->53.0 | **none** for 3 s; 2nd bite 662 at 23:25:58.5 | 3.5 / 0.58 | 94 -> 103 at +0.32 s, 111, 114 (the only kick in time) | 66.8 / 864 | 27.8 | 2,086 |
| A5 | 23:26:12.5 | 1,178 (+314) after a bite at 23:26:10.7 kicked iac 80->103 | **495** | 369 | -1,040 | 25.1->42.3 | **none**; the car started rolling (3 km/h) at the min; 2nd trough 590 at 23:26:13.0 | 2.9 / 0.21 | 103 -> 92 -> 88: **falling** through the dip | 67.5 / 864 | 29.8 | 2,103 |
| A6 | 23:26:24.2 | 1,421 (+556), a throttle blip released as the clutch bit | **440** | 425 | -1,689 | 22.9->54.4 | 12.9 % at the peak, closed, then 10.6 % at the min | 1.4 / 0.58 | 97 -> 92 (falling) | 67.5 / 864 | 30.2 | 2,114 |

The last three (A4-A6) are in the driveway at home, just before the 23:26:58 key-off. The A/C
clutch was off all night and the fan reading is 0 (that channel is unverified).

### 1b. All 10 launches from a standstill

| launch (vss > 0) | stood s | min rpm | target | under | throttle when the rpm began to fall | group |
|---|---|---|---|---|---|---|
| 22:39:47.9 (2224) | 910 | 546 | 810 | 264 | closed (7.8 %) | A1 |
| 22:41:44.4 (2224) | 22 | 792 | 973 | 181 | **open 12.2 %** | B0 |
| 22:53:18.1 | 129 | 463 | 824 | 361 | closed | A2 |
| 22:54:21.8 | 6 | 670 | 824 | 154 | **open 11.4 %** | B1 |
| 22:58:05.7 | 10 | 702 | 824 | 122 | **open 10.6 %** | B2 |
| 23:03:01.9 | 6 | 906 | 864 | -42 | closed at the start of the fall, 12.6 % before the min | B3 |
| 23:12:39.5 | 11 | 554 | 864 | 311 | closed, throttle during the fall | A3 |
| 23:26:04.1 | 11 | 519 | 864 | 345 | closed | A4 |
| 23:26:12.6 | 4 | 495 | 864 | 369 | closed | A5 |
| 23:26:25.1 | 8 | 440 | 864 | 425 | blip released | A6 |

Take-ups while still rolling, throttle closed when the rpm began to fall, throttle 0.1-0.6 s
later: 22:59:28 (5 km/h, 779), 23:05:20 (4 km/h, 736), 23:17:12 (3 km/h, 735), 23:19:52 (5 km/h,
799). At 3-5 km/h in 1st gear the engine only has to come down to roughly 370-610 rpm
(~120 rpm per km/h), so the clutch slips less and the rpm holds 150 rpm higher.

### 1c. Arrivals at a stop (vss reaches 0)

| stop | rpm 5 s before / at the stop | target | min rpm before relaunch or +12 s | under | iac_cmd -5 / 0 / +3 / +6 s |
|---|---|---|---|---|---|
| 22:41:22.6 (2224) | 1,106 / 1,091 | 960 | 908 (+4.9 s) | 51 | 99 / 99 / 94 / 98 |
| 22:43:12.9 (2224) | 1,096 / 1,095 | 1,001 | 986 (+7.3 s) | 14 | 91 / 96 / 91 / 88 |
| 22:54:15.5 | 1,058 / 1,147 | 824 | 931 | -107 (still above at relaunch) | 100 / 96 / 91 / 97 |
| 22:57:56.0 | 1,409 / 1,364 | 824 | 815 (+5.9 s) | 9 | 91 / 99 / 94 / 81 |
| 23:02:56.1 | 1,237 / 1,250 | 864 | 1,034 | -170 (still above at relaunch) | 94 / 98 / 86 / 95 |
| 23:12:28.3 | 1,131 / 1,133 | 864 | 830 (+4.0 s) | 34 | 90 / 91 / 83 / 83 |
| 23:25:53.1 | 1,384 / 1,291 | 864 | rpm still 1,213 when the clutch bit at +2 s -> A4 | - | 98 / 94 / 106 / 114 |
| 23:26:08.4 | 1,045 / 832 | 864 | 819 | 45 | 91 / 82 / 103 / 96 |
| 23:26:16.8 | 1,079 / 1,306 | 864 | (A6 bite) | - | 103 / 95 / 89 / 80 |
| 23:26:26.6 (final) | 914 / 1,346 | 864 | 776 at 23:26:31.9 | 89 | 80 / 98 / 78 / 75 |

After a stop the rpm hangs 130-500 above target and takes 3-7 s to reach it. The iac_cmd falls
only 5-18 counts over that time, and on its own the rpm lands softly. The risk is relaunching
during that sink (A4), when the ECU is still taking air away.

### 1d. Coast-downs with the clutch in, rolling at 8 km/h or more (closed throttle, rpm < target + 150)

2224: five segments, 1.5-11.5 s, at 38->8 km/h. The rpm was 858-1,110 with targets of 750-987,
and the minimum excess over target was +8 rpm. 2251: one 1.3 s segment (15 km/h, 880 vs 824).
**Across both drives, the rpm while rolling at 8 km/h or more never went below target**: lowest
858 (2224) and 826 (2251). The owner mostly stays in gear down to walking pace, so there is
little neutral coasting to judge.

### 1e. Dips at a standstill with no launch (closed throttle)

22:31:57 (770, -67), 22:33:14 (749, -61, ELD 9->20 A), 22:33:18 (742), 22:35:57 (721, -88,
**ELD 8.9->25.4 A**), 22:38:26 (715, -95, brake applied), 22:39:36 (750), 22:51:27 (742, 17 s
after start, 14 s below target), 22:51:39 (777), 23:26:32 (776). These undershoot by 60-103 rpm
and recover in 2-5 s. An ELD step of about 16 A on its own costs 40-90 rpm.

Plots: `work-idle/events_takeups.png` (A1-A6, three throttle-open launches B1-B3, the ELD step,
two stop arrivals), `work-idle/risk_scatter.png`, `work-idle/overview_2224.png`,
`work-idle/overview_2251.png`.

## 2. Risk factors, ranked

1. **Throttle closed when the clutch bites from a standstill: decisive.** 7 of 7 completed
   bites went to 440-590 rpm. With the throttle open at the bite the minimum was 670-906 (4
   launches). Rolling 3-5 km/h take-ups were 735-799. There is no overlap between the groups.
2. **The rpm falling back from above target at the bite, with the ECU removing air.**
   - A2, A4, A5 and A6 started 199-556 rpm above target, fell at 564-1,689 rpm/s and bottomed
     at 440-519.
   - A1 and A3 started 51-94 above target, fell at 498-759 rpm/s and bottomed at 546-554.
   - The excess rpm itself does not carry the engine through. It comes with a falling IAC:
     during the dip, iac_cmd was being pulled *down* in A2 (112->95->89 just before), A5
     (103->92->88) and A6 (97->92).
   - How it builds up: a first bite or a blip, then the ECU's MAP kick or the rev-hang, then
     the rpm overshoots, the ECU cuts air, and the real bite lands on a falling engine with a
     small valve opening.
3. **The ECU responds too slowly.** Within the 0.8-2.5 s fall, iac_cmd rose by +4 counts or
   less in 5 of 6 deep bites. Only A4 got a kick in time (+9 at 0.3 s, then +17), and it still
   reached 519. The ignition does not help either: it holds 16.2° whenever the rpm is below
   target, so there is no timing catch. When the driver stabs the throttle at 440-550 rpm, the
   ignition goes to -3 to -11° (MAP 64-87 kPa). The rpm still recovered by about 1,500 rpm/s
   every time, so this is not a factor.
4. **Electrical load: a contributor, not separable.** All deep dips had ELD at 23-30 A
   (headlights on). The only bites with low ELD did not happen: the 15-min idle in 2224 was at
   9-10 A until the lights came on. One step of about 16 A costs 40-90 rpm at idle, and the
   ECU takes 3-4 s to recover it. A brake-light, fan or A/C step landing on a bite would add to
   it. The A/C was never on tonight.
5. **ECT and the idle target: not a factor tonight.** The ECT read 57-79 °C with no 35 °C
   glitch. The target followed it (750-1,000 rpm). Every deep dip had a sane reading
   (65-73 °C) and a target of 810-864. The ECT jumps tonight (22:41-22:43 at 60-62 °C with
   target 960-1,000; 23:05-23:12 at 76-79 °C with target 750, back to 864 at 23:12:2x) did not
   coincide with a closed-throttle bite. Earlier logs showed that a false-cold snap-back makes
   the ECU drop IACV within 5 s. If that happened during a bite, it would be mechanism 2 at a
   larger scale. This is a plausible stall path that has not been seen yet.
6. **Idle hunting: not present on its own.** At a standing idle the rpm holds target within
   about ±20-30 rpm. The ~300 rpm swings come from clutch bites and the ECU's kick and removal
   after them, not from the loop oscillating by itself.
7. **Time since start: no pattern.** The deep dips came 126 s to 2,114 s after start.
8. **Rev-hang and returns to idle: risky only as the setup for factor 2.** The hang after a
   stop is 1,100-1,360 rpm for 2-6 s. There was no undershoot past 51 rpm on arrival.

## 3. Idle loop measurements

- **Command and output.** iacv equals about 0.53 x (iac_cmd - 78) above iac ~85, with a floor
  of ~2 at iac 71-78 (`loop_scatter.png`, left). The two are read in the same block, and iacv
  follows iac_cmd at every read. There is no lag or offset between them, so nothing points to a
  stuck driver stage.
- **Where idle sits.** At a standstill with the throttle closed and rpm above 600, iac_cmd has
  a median of 87 (5-95 %: 83-98, extremes 71-114). That is iacv 5-11, i.e. 5-20 counts above
  the floor. Over both drives the ECU used iac 71-217. The idle runs near the closed end, yet
  the rpm is at or above target, and the target itself (810-864 at 65-73 °C) is above the
  670 ± 50 spec. That fits extra air from outside the IAC (a leak, the fast-idle thermo valve,
  the throttle stop). The ECU has almost no room left to *reduce* air, and this would also
  explain the slow rev-hang decay.
- **Feedback (integral) law** (`loop_scatter.png`, middle; n = 1,137 reads at a standstill with
  the throttle closed):
  - Within ±20 rpm of target: no net change, only ±1-3 counts of dither (34 % of reads change
    by 2 or more). That is the deadband.
  - Rpm 20-40 low: +0.9 counts per read. 40-80 low: +2.8. 80-150 low: +2.0.
  - Rpm above target: about -2 per read.
  - Slope: about 0.02-0.035 counts per rpm of error per second.
- **Load kick (feed-forward).** On a MAP rise with the throttle closed, iac_cmd jumped +22
  (22:53:12.2) and +23 (23:26:10.8) within one read, and +9 at 23:25:54.1. The ECU took it back
  within 1-2 reads (-17, -11, -21) as soon as MAP fell or the rpm overshot.
- **Throttle-open jumps.** When the TPS opens, iac_cmd jumps +15 to +44 (dashpot/off-idle air).
  While driving it sits at 100-217 and decays over several seconds after the throttle closes.
- **Rpm per count.**
  - Regressing the rpm change on the dither steps gives 1.5-2.5 rpm per count at 1-3 s. This
    is biased low because the dither is a reaction to the rpm.
  - ELD-step recoveries give roughly 8 rpm per count: +5 to +11 counts recovered 50-90 rpm in
    3-4 s.
  - Rough answer: 2-8 rpm per count. The +2 to +6 counts the feedback adds during a bite are
    worth about 10-50 rpm. The +22 kick is worth about 100-200 rpm: before A2, with the clutch
    released again, the rpm went 720->1,022.
- **Saturation.** None at idle. The highest standstill value with the throttle closed was 114,
  well below the 217 used while driving. The ECU has the range but does not use it in time.
- **Load step response** (22:35:57, ELD 8.9->25.4 A): rpm 811->721 (-90) within about 1 s;
  iac_cmd 85->91->93->96 over 1-3 reads; back at target at about +4 s.

## 4. How close each event came

| id | min rpm | steepest fall rpm/s | fall in the last 0.3 s before the min, rpm/s | what turned it round |
|---|---|---|---|---|
| A6 | 440 | -1,689 | -766 | throttle reopened 0.2 s before the min (10.6 %) |
| A2 | 463 | -564 | -227 | throttle came in at the min (8.6 % -> 15.7 % at +0.2 s) |
| A5 | 495 | -1,040 | -720 | nothing from the ECU (iac falling); the car started rolling |
| A4 | 519 | -707 | -77 | the driver eased the clutch (MAP sank) and the IAC kick arrived |
| A3 | 554 | -759 | -567 | throttle opening during the fall |
| A1 | 546 | -498 | -150 | clutch slip; iac +2 only |

At the rate each one was still falling, a bite held for another **0.05-0.3 s** would have reached
400 rpm (A6 0.05 s, A5 0.13 s, A3 0.27 s, A2 0.28 s). What would have tipped it over:

- holding the clutch at the bite point instead of easing it, or releasing it a little faster,
  with the throttle closed;
- a bite while the rpm is falling from a blip or rev-hang, or right after an aborted bite;
- an extra load at that moment: uphill, passengers, A/C compressor, brake lights plus fan,
  costing 40-90 rpm each;
- an ECT snap-back pulling IAC air during a bite.

**A stall in traffic would most likely look like A5 or A6:** a quick second attempt at a
standstill, with the throttle closed or just released, while the ECU is unwinding its own kick.

## 5. Conclusions for the mechanic

1. **The ECU's usable idle-air authority is small and slow.**
   - At a warm idle the IACV runs at about 5-11 % of the output range the ECU uses, close to its
     floor.
   - The feedback adds about 2-3 counts per second (about 5-20 rpm/s). A clutch bite takes
     500-1,700 rpm/s out of the engine.
   - The one fast action, a +9 to +23 count MAP kick worth about 100-200 rpm, is withdrawn
     within 1-2 s. If the driver gives up a bite and retries, the retry meets a closing valve.
2. **Launches below 600 rpm happen when the clutch bites from a standstill with the throttle
   closed** (7 of 7 tonight). **They go below 520 rpm when the bite also comes while the rpm is
   falling back from above target**: after a blip, during the 2-6 s rev-hang after stopping, or
   on a second attempt.
3. **The valve and its driver look fine.** The output follows the command, the rpm responds to
   every kick, and nothing saturates. What stands out is how *little* valve the idle needs:
   - the idle sits above spec with the IACV near closed;
   - the fuel trims are +19 % at idle against +8 % on the highway;
   - the rev-hang after stops is slow.

   All three point to **extra air entering outside the IACV**. Suggested checks, in order:
   - Smoke-test the intake for vacuum leaks: PCV hose and grommet, brake booster hose,
     intake-manifold and throttle-body gaskets, IACV gasket.
   - Check the fast-idle thermo valve and the throttle stop screw and cable slack.
   - Run Honda's base-idle check (unplug the IACV connector: the rpm should drop sharply; if it
     barely moves, the air is coming from elsewhere).
   - Clean the IACV and the throttle body.

   With the leak fixed, the ECU will hold idle with the IACV well open. It can then pull the
   rpm down faster after a stop and has a larger share of the idle air under control.
4. **Also check the clutch engagement point and pedal free play.** Even a careful
   closed-throttle bite takes 260-370 rpm off this engine within about 1 s.
5. **For the owner until it is fixed:**
   - Have a little throttle in before the clutch bites.
   - After a stop or a blip, let the rpm settle at idle (about 3-5 s) before releasing the
     clutch.
   - Do not abort a bite and retry straight away without throttle.
6. **For logging.** If iac_cmd, iacv and ect could be read at 3-5 Hz (dropping the always-zero
   bytes from the slow block, see README "Next"), the ECU's kick and unwind could be timed to
   0.2-0.3 s instead of 1 s. A drive with the A/C on and a few launches on a slope would test
   the load margin.
