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

## Next

- At the first real engine death, look at 0x29/0x28, 0x2F, ELD and battery in the last 2 s.
- Log one cold start and one drive before dropping the always-zero bytes from the read
  plan (that would lift the slow block from 1 Hz to ~2-3 Hz).
- A P2P ROM dump through asm662 would name every slot at once.
