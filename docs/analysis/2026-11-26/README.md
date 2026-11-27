# 2026-11-26: the unmapped RAM bytes

Three independent passes over the bytes the map did not decode (0x03-0x07, 0x0D,
0x0E, 0x16, 0x1A-0x1F, 0x29-0x3B), logged once a second in
`drive_2026_11_26_2045` and `drive_2026_11_26_2114` (warm idle only, vss 0):

- `raw-bytes-stats.md`: statistics, correlation and lag over whole drives
- `raw-bytes-events.md`: stalls, link drops, key-on/crank, load steps, ECT glitches
- `raw-bytes-literature.md`: published Honda K-line address tables

`work-stats/` and `work-events/` hold the scripts and plots (their database copies are
git-ignored).

## Verdict per byte

| byte | what it is | evidence | confidence |
|---|---|---|---|
| 0x29 | **idle air control command** (before the output curve); 0x28 (IACV) is a fixed, monotonic, slightly lagged function of it | r = 0.95 with 0x28, same curve in both drives, 0x29 moves first; hondamotor.ru lists IAC at 0x29 | high (relation), medium (name) |
| 0x1C | **copy of ELD (0x19)** | equal at every read, 2,309 and 1,640 reads, both passes | high |
| 0x27 | **rpm band above idle** (0 below ~845 rpm, +1 per ~50 rpm, 4 at ~1010+; 5 at key-on, 10 cranking). Not an ignition limit | r = 0.83 with rpm, same thresholds in both drives | medium |
| 0x2A | slow learned or filtered value, steps ±1 every 13-30 min (6, 5, 4, 5) | no sensor follows it | low |
| 0x2F | counter that runs ~every 33 s, starting 29-40 s after a big ECT snap-back; maybe a diagnostic monitor | 3 of 3 episodes in drive 2114 | low |
| 0x32, 0x37 | steady value flickering 45/44 (bit 0), track nothing | | low |
| 0x33, 0x35, 0x36, 0x38, 0x39, 0x3A | constants 55, 45, 7, 25, 25, 25 (the same run is present, unnamed, on a P2T) | never changed | high that they are constant |
| 0x0D, 0x0E | probably flag bytes (one source: 0x0D bit 3 = engine-mount solenoid); 0 at idle, key-on and crank | | low |
| everything else | 0 in every read, key-on and crank included | | high at warm idle; untested driving or cold |

## For the stall

- No unknown byte changes before any link loss. The only capture tonight
  (`stall_20261126_211054`) was a 1.5 s K-line dropout with the engine running, like the
  96 short drops in drive 2045. No real engine death has been logged with all bytes yet.
- The **ECT false-cold fault (connector C122, see 2026-11-25) is still active**: the
  reading jumps between ~35 °C and ~80 °C within a second, over a dozen times in
  drive 2114. Each false-cold stretch raises idle to ~1000-1200 rpm; each snap back warm
  drops IACV and 0x29 within 5 s (deepest: 21:30:27, 1040 -> 685 rpm, no stall).
- At warm idle 0x29 sits at its floor (36-41) with IACV 0 while rpm stays 700-850,
  above the 670 ± 50 spec. With the +18-21 % trims from 11-25 that points to air past the
  valve (vacuum leak, fast-idle thermo valve, throttle stop). A hypothesis to check on the
  car, not proven by the logs.

## Second round (four more passes)

- `sweep-40-ff.md`: 0x00-0xFF read on the running car, 210 sweeps, 3,360 reads, **no timeouts**.
  New: **0x76-0x77 = idle target** (`x/4` rpm; rpm follows 2-3 s later, r 0.95, steps of
  ~13.6 rpm), 0x9C = calculated load, 0x9A = fuel system status (2 = closed loop),
  0xA0 = second O2 (0.92-0.97 V, but follows the front sensor in the same second: suspect).
  The old `ecu_id` read at 0x76 covered the idle target; the constant id starts at 0x78.
  0x40-0x75 and 0xA5-0xFF are 0 at idle.
- `channel-verification.md`: baro and o2 verified; **ign offset wrong** (`(x-46)/4`, the old
  formula read ~5.5° high; warm idle is 10-13.5° per PID 0E, spec 12 ± 2°); knock and fan
  wrong; batt, eld, inj plausible pending a meter check. Drive 1 (11-25 17:38) has garbage in
  0x08-0x11.
- `stall-forensics.md`: **no proven engine death in any log** (all stationary idle, rpm never
  below 581 while the ECU answered). Four 11-25 engine-offs (18:55, 19:29, 20:13, 21:16) are an
  instant ECU silence at steady rpm: a key-off or a power loss, not an idle collapse. ECT
  glitches cause sags, not deaths, but are getting more frequent (18/h -> 33/h). Two engine-offs
  followed a 29-38 A load step by 2-8 s. "Dies every couple of minutes" does not match ~5.4 h of
  running with at most 4 engine-offs: either the deaths happen while driving (never logged) or
  the app's "STALL (link lost)" alarms from K-line dropouts are being read as deaths.
- **Owner, 2026-11-26 22:0x:** the four 11-25 engine-offs (18:55, 19:29, 20:13, 21:16) were
  all key-offs. The car has not died since the laptop was plugged in. So no engine death has
  been logged yet; the stall has to be caught on a later drive (likely while driving).
- `link-dropouts.md`: why the app drops a read every ~12 s when the sweep dropped none (in progress).

Map `p2p-guess-6` applies the above (`apply_map_guess6.py`): ign offset, baro/o2 verified,
knock/fan/ign_limit suspect, notes on the plausible channels, new channels `idle_target`,
`load_ecu`, `fuel_status`, `o2_2`; the six constant raw bytes dropped to fit the CYD's 64.

## First logged drive (drive_2026_11_26_2224, manual gearbox)

- 22:36:17-22:37:3x: cable came unplugged (owner); the app filed it as "ECU silent, engine still
  running when it answered again" (new rule working).
- **22:39:41 pull-away dip:** brake released, clutch engaged from a stop: rpm 810 -> 546 within
  0.3 s, MAP 28 -> 36 kPa, then 630-760 for 6 s until rolling. Idle target stayed 810; iac_cmd only
  crept 100 -> 112. ECT read a sane 73 °C (no glitch). Lowest rpm of all logs. The idle has little
  air in reserve for a clutch load: the likely way this car "dies" while driving.
- 22:45:25: engine off at the gas station (owner key-off, not a stall; capture
  `stall_20261126_224528`): steady 820 rpm, stopped, 14.1 V, then instant ECU silence.

## Second logged drive (drive_2026_11_26_2251, from the gas station)

- **22:53:15 pull-away near-stall, rpm 463** (lowest of all logs): stopped, throttle closed
  (TPS 8 %), clutch engaging: rpm 716 -> 463 in 0.6 s while MAP rose 28 -> 41 kPa; the owner's
  throttle at 22:53:16.1 caught it (2,139 rpm a second later). What looked like idle hunting just
  before it (721 -> 1022 -> 716 rpm, iac_cmd 90 -> 112 -> 89) was a first clutch bite the driver
  gave up: the ECU kicked the air up, rpm overshot, and the ECU was taking the air back when the
  real bite came (drive-idle-control.md). ECT read 70-71 °C (no glitch).
- 23:12:37: same pattern, smaller: 904 -> 555 rpm at clutch engagement, caught with throttle.
- **23:12:57.3-23:13:03.0: 5.7 s K-line silence at ~76 km/h, engine running** (capture
  `dropout_20261126_231301`, correctly filed). With the plug having come out at 22:36, a loose
  OBD2 connector under vibration is the first suspect.
- **23:16:25.8-23:16:33.9: 8 s K-line silence while accelerating 41 -> 59 km/h, engine
  running**, but filed as a stall (`stall_20261126_231629`): the re-file rule only calls it a
  dropout when the rpm on return is below 1.25 x the rpm before + 100 (2,682), and the engine had
  revved to 2,887 meanwhile. **Fix needed:** when vss > 0 before the loss, or rpm on return is
  well above idle, it is a dropout, not a stall.

## Tonight's drives, summed up

- drive 2224: 20.8 min, up to 69 km/h; drive 2251: 35.8 min, up to 107 km/h (first interstate),
  5,411 rpm. Ended 23:26:58 at home (key-off, then unplugged).
- **No engine death.** Six pull-away dips below 600 rpm in drive 2251 alone (22:53:15, 23:12:37,
  23:25:55, 23:26:12, 23:26:13, 23:26:24), lowest 440-463 rpm, every one at a standstill with the
  throttle closed while the clutch took up. Three were caught with throttle; three (22:39:41,
  23:25:56, 23:26:12) recovered on their own as the car began to roll (drive-idle-control.md).
- Fuel trims: total trim (stft + ltft, closed loop) +18.9 % at idle, +7.8 % above 60 km/h.
  **Revised by drive-fueling.md:** the trim follows rpm, not airflow (best fit 8.2 + 6.7 x
  1000/rpm; holding rpm, trim rises slightly with MAP; still +11.7 % above 60 kPa). On a MAP-based
  ECU an intake leak raises MAP and is fuelled for, so the trims do not point to a vacuum leak.
  They fit a constant lean offset of ~6-8 % plus an rpm term at idle: the front O2 being fooled
  lean at low rpm (exhaust leak before it, idle misfire, lazy sensor), with the rear O2 reading
  rich at idle. The excess idle air (idle above target with the IACV near closed) is a separate
  finding and still stands.
- The OBD2 plug fell out once (22:36) and the link went silent 5.7 s and 8 s while moving: loose
  connector.

## Deep dive on the drives (five passes)

- `drive-idle-control.md`: 10 launches; all 7 bites below 600 rpm came with the **throttle closed
  as the clutch bit from a standstill** (open throttle: 670-906 rpm, no overlap). Deepest
  (440-519) when the bite came while rpm was falling back from above target, with the ECU taking
  air away. The ECU adds air at ~5-20 rpm/s against 500-1,700 rpm/s taken by the clutch. Closest
  unaided call 23:26:12 (495 rpm, still falling at 720 rpm/s when the car rolled).
- `drive-fueling.md`: trims follow rpm, not airflow (see above); WOT rich (AFR ~11.5), decel cut
  normal, no fuel starvation; 0xA0 is a real rear O2 but tracks the front at cruise (weak cat?);
  LTFT frozen at 8.6 % while ECT reads low; rich EVAP-like slugs at cruise.
- `drive-ect-electrical.md`: no false-cold jump in 56 min of driving (last at 22:07); the fault
  drifts on its own, not with vibration. Tonight the reading sat ~13-15 °C low (fan switched on at
  a reading of 69-75 °C vs 84-86 °C when the contact was good), so the idle target stayed
  810-1,014. **Fixing ECT alone drops the target to ~670 and removes that margin at launch.**
  Charging system healthy; the voltage dips follow the rpm dips.
- `drive-bytes.md`: VTEC on 23:03:11.7-12.7 at ~5,400 rpm WOT (0x0A b2) with a new bit 0x0C b3
  (VTEC pressure switch?); 0x2F = EVAP purge duty?; 0x0B b6 = O2 heater? (off at high load);
  PSP polarity 0 = steering load; 0x27 = rpm x MAP index; 0x3C rpm-stepped 1-11; no gear or clutch
  input on this ECM.
- `drive-timeline.md`: 24 km, 55 min, all gears, 37 % stopped; every dip at a standstill 2-7 s
  before pulling away; link clean until 23:10, then a loose OBD2 contact; vss reads 1.1 % low vs
  GPS; GPS drops 5-13 s of fixes every ~2 min. (GPS files and track plots are git-ignored.)

## Third round (2026-11-27)

- `idle-vs-drive.md`: most idle findings hold. **Changed:** besides the ECT term in the idle
  target there is a separate ~810 rpm idle state that does not depend on ECT (21:44:17 on 11-26:
  ECT steady 85.9 °C, iac_cmd 51 -> 84 in one read, rpm 670 -> 808 in 6 s, ELD/MAP/A/C unchanged;
  0x2F flat at 26-27, so not a purge change if 0x2F is purge). It clears on restart, a rev or
  pulling away; cause unknown. The radiator fan cycles only when parked (none while driving). The
  ECT offset was mostly absent on 11-25 (fan-on reading 84.8 °C) and mostly present on 11-26
  (72.1 °C). The glitches stopped 14 min before the drive, so "none while driving" is not a
  driving effect.
- `vehicle-dynamics.md`: engine power normal (one clean 2nd-gear WOT pull: 118-127 N·m at
  2,800-4,900 rpm, roughly 85-94 % of a D16Y8 there; the report was first written against the
  D16Y7's 106 hp rating and carries a correction: the EX's D16Y8 is 127 hp at 6,600, 145 N·m at
  5,500, never reached in the logs; no intake restriction, no fuel starvation, cat not clogged); no hesitation or flat
  spot on 23 tip-ins; route nearly flat (~20 m relief, modelled: nothing logs altitude); about
  34-36 mpg overall; the low ECT reading costs ~0.09 L/h at idle.
- `next-session-plan.md`: one ~3 h 20 min session: cold soak and sweep, cold start to first fan,
  poke test, a drive loop (launch matrix with throttle/closed/full lock/A/C, VTEC pull, interstate,
  hill), hot idle with the ECT wiggle test, then the mechanic checks.
- Local only, not in git: `driver-technique.md`, `trips.md`, `nearstall-timeline.md` (every
  near-stall had a closed throttle at the clutch bite; every good launch had it open within
  0.2 s; nothing else separates them before the bite).

## Next

- At the first real engine death, look at 0x29/0x28, 0x2F, ELD and battery in the last 2 s.
- Log one cold start and one drive before dropping the always-zero bytes from the read
  plan (that would lift the slow block from 1 Hz to ~2-3 Hz).
- A P2P ROM dump through asm662 would name every slot at once.
