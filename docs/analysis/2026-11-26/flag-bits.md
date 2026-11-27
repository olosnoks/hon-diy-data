# Flag bits, one at a time: 2026-11-26

Every bit of 0x08-0x0F was checked one at a time. So were the other bytes whose value changes in only a few bit positions (0x86, 0x9A, 0xA4, 0xAD, 0x32, 0x37, 0x3C, 0x2F). Each bit's transitions were lined up with the logged events.

**Data.** A read-only copy of `hondalog.db` taken 22:13 (in `work-flags/`, git-ignored), using the raw bytes from `ecu_reads` only:

- 18,822 slow-block reads that cover 0x08-0x0F: blocks `08+10` (drives 6-32) and `03+15` (drives 33-37). That is about 5.2 h at ~1 Hz, all stationary idle.
- Drive 1 is excluded because its 0x08-0x11 block is frozen garbage (see `channel-verification.md`).
- The 1 Hz full sweep `work-sweep/car-214932-wide.csv` (210 sweeps, 21:49:32-21:53:01) is the only source for 0xA4, 0xAD and 0x86, and the idle target (0x76:0x77 /4) comes from it and from drive 37.

**Scripts** in `work-flags/`: `load.py` builds `F.pkl`; `trans.py` lists every transition (output in `transitions.txt`); `ctx.py` dumps drive starts and ends and the events (`context.txt`); `c0.py` checks 0x0C b0 against load steps and 0x0F against fuel cut; `align.py` covers ECT glitches, idle target and rpm bands; `sweep.py` covers the sweep.

## Headline

- **Bits that only ever change at a known event:**
  - 0x08 b0 (starter): 3 of 3 starts
  - 0x08 b1 (A/C switch) and 0x0B b1 (A/C clutch): 4 A/C presses
  - 0x08 b3 (brake): 3 pulses at 21:09:17-23, matching the owner's 21:09:18-23
  - 0x0F b0 (closed loop): open at 3 of 3 decel fuel cuts and after every start
  - 0x0B b0 (fuel pump), 0x0B b5 (MIL) and 0x0B b6: change only at key-on and crank
- **0x0A b7 is not "engine running".** It reads 1 for 14 s of key-on-engine-off (drive 31, rpm 0, 0x27 = 5). The only 0 is the first read after key-on in drive 19:32.
- **0x0B b6 is the bit that follows the engine.** It is 0 through key-on engine off and 1 from the first cranking read (202 rpm, 10.9 V), so it is set before the engine has run. That fits "engine turning" better than an O2 heater, but the logs cannot separate the two.
- **0x0F b0 (closed loop): the map's summary is wrong.** The bit does not drop "when revved to 1600-2400 rpm". It stayed 1 at a steady 1,900-2,170 rpm and dropped only during the throttle-closed decel fuel cut (injector 0 ms) after a blip, 3 of 3 times.
- **0x0C b1 ("fan") is wrong.** It was never set in 18,822 reads, over about 58 radiator-fan load steps and ECT up to 88 °C.
- **New candidate: 0x0C b0.** It is always 1 except for 3 single reads, each at the moment of a large electrical load step (ELD down 39-53 counts, battery dipping to 12.9-13.4 V). On the other ~55 load steps it stayed 1.
- **No flag bit changes with an ECT glitch:** 116 jumps of more than 15 °C, none with a 0x08-0x0F change within 2.5 s (the one hit is a coincidental closed-loop entry 14 s after a start). No flag bit changes with an idle-target step either (56 steps in drive 37, about 20 in the sweep). No flag bit changes in the last reads before any of the five key-offs: the ECU goes silent with every flag at its running value.
- **PSP (0x08 b2):** 1 in 18,821 of 18,822 reads, with one single-read 0 at 11-25 21:12:08.5 (792 rpm, no IACV or rpm response). The wheel was presumably never at full lock while logging, so whether the bit is inverted **cannot be told from the data**. It needs the steering test below.

## Bit-by-bit table

"% set" is over the 18,822 slow reads unless the row names the sweep (210 reads). Times are local. dN = drive id: d16 = `drive_2026_11_25_1858`, d21 = `..._1932`, d31 = `drive_2026_11_26_2031`, d33 = `..._2045`.

| byte | bit | % set | transitions (time, drive) | lines up with | verdict | recommended map name |
|---|---|---|---|---|---|---|
| 0x08 | 0 | 0.02 (3) | on only in the first read of each start: 11-25 18:58:14.1 (d16, first read, 202 rpm, 10.9 V), 19:32:23.6 (d21), 11-26 20:32:13.2 (d31); off 1 s later each time | key-on, then crank, 3 of 3 starts; never at any other time | **confirmed** | `starter` (already in the shipped map; missing from `~/.config` map) |
| 0x08 | 1 | 0.08 (15) | 11-25 19:06:32-34 (d17), 19:09:17-22 (d19), 19:12:40-47 (d20); 11-26 20:35:38-39 (d32) | A/C presses. In the same read ELD drops 28-38 counts (more current). 0x0B b1 follows 0-1 s later; IACV rises to 33-53 and rpm is lifted to ~1050 | **confirmed** | `ac_sw` (keep) |
| 0x08 | 2 | 99.99 | one single-read 0: 11-25 21:12:08.5 (d27) | nothing: 792 rpm, IACV 18 steady, ELD steady, no ECT glitch. Could be a short wheel movement or a read glitch | **unknown polarity** (map: PSP, from literature). Never tested at full lock | `psp_sw`, mark unverified; polarity open (`1 - bit(x,2)` if it reads 0 at lock) |
| 0x08 | 3 | 0.02 (3) | 11-26 21:09:17.2, 21:09:20.3, 21:09:22.4 on; off 1 s later each (d33) | owner pressed the brake 21:09:18-23 (three taps at 1 Hz sampling); ALT FR +20 at the taps | **confirmed** | `brake` (status -> verified) |
| 0x08 | 4 | 0 | none | - | unknown (never toggled) | - |
| 0x08 | 5 | 0 | none | - | unknown (never toggled) | - |
| 0x08 | 6 | 0 | none | - | unknown (never toggled) | - |
| 0x08 | 7 | 100 | none | constant 1 at key-on, crank, idle, A/C, brake. Candidate for a gear-position input (A/T P/N or clutch switch), because the car never left neutral or park | unknown (constant) | `x08_b7` (do not name until the gear test) |
| 0x09 | 0-7 | 0 | none | - | unknown (never toggled). Literature puts SCS at b3, and SCS was never jumped, so this is consistent | b3 `scs` (literature, untested) |
| 0x0A | 0 | 0 | none | - | unknown (never toggled) | - |
| 0x0A | 1 | 0 | none | - | unknown (never toggled) | - |
| 0x0A | 2 | 0 | none | max logged rpm ~3,300; the D16Y8 VTEC point is ~5,500 rpm under load | **untested**, consistent with VTEC | `vtec` (keep, unverified) |
| 0x0A | 3 | 0 | none | - | unknown (never toggled) | - |
| 0x0A | 4 | 100 | none | constant, key-on included | unknown (constant) | `x0a_b4` |
| 0x0A | 5 | 0 | none | - | unknown (never toggled) | - |
| 0x0A | 6 | 0 | none | - | unknown (never toggled) | - |
| 0x0A | 7 | 99.99 | 0 -> 1 at 11-25 19:32:23.6 (d21; first read 19:32:22.6 was 0, within ~1 s of key-on) | **not the engine**: it is 1 at cranking (d16 18:58:14, 202 rpm) and for all 14 reads of key-on engine off in d31 (20:31:58-20:32:12, rpm 0) | **"engine running" is wrong.** Likely an ECU-ready or key-on status that comes on about 1 s after key-on | `ecu_ready` (unverified) |
| 0x0B | 0 | 99.92 | key-on: 1 at d21 19:32:22.6 (first second, prime), 0 for all of d31's 14 s key-on reads (prime over), 0 -> 1 at d31's first crank read 20:32:13.2 | 2 s prime at key-on, then off until crank, on while running | **confirmed** (behaves as pump/main relay) | `fuel_pump` (keep) |
| 0x0B | 1 | 0.07 (14) | 11-25 19:06:33-34, 19:09:18-22, 19:12:40-47; 11-26 20:35:38-40 | 0-1 s after 0x08 b1, off with it (one read later in d32); idle-up to ~1050 rpm | **confirmed** | `ac_clutch` (keep) |
| 0x0B | 2 | 0 | none | - | unknown (never toggled) | - |
| 0x0B | 3 | 0 | none | - | unknown (never toggled) | - |
| 0x0B | 4 | 0 | none | - | unknown (never toggled) | - |
| 0x0B | 5 | 0.01 (1) | 1 only at d21 19:32:22.6 (key-on, first second), 0 from the crank read | bulb check at key-on. But in d31 it read 0 for all 14 s of key-on engine off | **likely MIL**, with one conflict: d31 had no bulb check at second 0-14 of the log (key may have been on for longer) | `mil` (keep, confirm with the key-on test) |
| 0x0B | 6 | 99.91 | 0 -> 1 at d21 19:32:23.6 and d31 20:32:13.2 (first crank reads); also 1 at d16's crank read (202 rpm) | 0 at key-on engine off, 1 from the first cranking read, never 0 while running | **likely "engine turning" (crank signal seen)**. An O2 heater would normally switch on after the start, not during cranking, but that cannot be excluded | `engine_turning` (unverified); not `o2_heater` |
| 0x0B | 7 | 0 | none | never on, even after more than 1 h of warm idle | unknown. If it were the 2nd O2 heater it would be on at warm idle, so probably not the heater (or the heater is off at idle) | - |
| 0x0C | 0 | 99.98 | 1 -> 0 -> 1 (single reads): 11-25 19:48:08.8 and 19:56:12.2 (d22); 11-26 21:48:01.4 (d35) | each is a large electrical load step in the same read: ELD 154->108, 155->116, 168->115 counts (+15-21 A), batt 12.9/13.2/13.4 V, IACV up. About 55 other similar steps (ELD -25 to -68) did not catch it at 1 Hz, A/C steps included | **new, low confidence:** an electrical-load or alternator-control state (Honda ELD / ALT C logic?) that blinks off for under 1 s at a load surge | `x0c_b0` (unverified; possibly `alt_ctrl`) |
| 0x0C | 1 | 0 | none | about 58 radiator-fan cycles (ELD steps of ~19 counts, ECT 84-88 °C) never set it | **map assignment is wrong** (the radiator fan is thermoswitch-controlled, not the ECM) | drop `fan` |
| 0x0C | 2-7 | 0 | none | - | unknown (never toggled) | - |
| 0x0D | 0-7 | 0 | none | key-on, crank, A/C, brake, fan steps, ECT glitches all 0 | unknown (never toggled). Literature b3 = engine-mount solenoid (P2T) | - |
| 0x0E | 0-7 | 0 | none | same as 0x0D | unknown (never toggled) | - |
| 0x0F | 0 | 99.52 | entries (0 -> 1): 11-25 18:58:27 (d16, 13 s after the start), 19:32:37 (d21, 14 s), 20:18:10 (d23, log began in the after-start flare), 21:20:15 (d28, same); 11-26 20:32:32 (d31, 19 s after the start at ECT 60 °C). Exits and returns: 11-25 19:10:45-46 (d19), 19:13:06-08 (d20), 20:00:45-46 (d22) | **exits = decel fuel cut, 3 of 3** (injector 0.00 ms after a throttle blip, rpm 2,700 -> 1,100). Stayed 1 at a steady 1,900-2,170 rpm. 0 during key-on and crank. 0x9A = 2 whenever both were read | **confirmed**; the map's summary is wrong | `closed_loop` (keep). Summary: "0 after start for 13-19 s and in decel fuel cut" |
| 0x0F | 1-7 | 0 | none | - | unknown (never toggled) | - |
| 0x9A | (byte) | 2 in 845 + 210 reads | none | closed loop the whole time (0x0F = 1) | **likely** OBD2 PID 03 fuel status (2 = closed loop); no open-loop period was read at this address | `fuel_status` (keep) |
| 0xA4 | 0 | 21 % of sweep (45 of 210) | on 21:50:54.9, off 21:51:36.9, on 21:51:37.9, off 21:51:40.9 (sweep) | fast idle: set only while rpm was 1,011-1,070 and the idle target was 1,014-1,068. It came on 2 s after rpm overshot the target by about 50 (1,064-1,068 vs 1,014) and went off with no visible trigger: the target did not change and there was no ECT glitch at the off edge. 32 sweeps at rpm ≥ 1,015 with it 0. No other flag moved | **unknown** (an idle-control state? not an rpm threshold) | `xa4_b0` (unverified); add 0xA4 to a read block to learn more |
| 0xA4 | 5, 7 | 100 (sweep) | none | 0xA4 = 160/161 | unknown (constant) | - |
| 0xAD | 0 | 100 (sweep) | none | constant 1 | unknown (constant) | - |
| 0x86 | 2 | 92 % (sweep) | 16 sweeps read 249 instead of 253 | part of a signed value (-3/-7) that tracks STFT and O2, not a flag | not a flag | - |
| 0x32, 0x37 | 0 | 94 %, 93 % | flicker 44/45 | low bit of a steady value; tracks nothing | not a flag | - |
| 0x3C | 0, 1 | value 1 or 2 | 1 <-> 2 blips | a 1/2 value, not two flags (see `channel-verification.md`, knock) | not a flag | - |
| 0x2F | 0-4 | counter | - | counter (see README) | not a flag | - |

Rpm bands (0x0F b0 set / reads): 400-700 rpm 5,317/5,348, 700-900 11,053/11,059, 900-1,200 2,248/2,273, 1,200-2,000 103/114, 2,000-4,000 2/3. The zeros are the after-start open-loop seconds and the three fuel cuts. Every other bit that varies is flat across all rpm bands.

### Differences from the current `~/.config/hondalog/addressmap.toml`

| byte.bit | map now | change |
|---|---|---|
| 0x08.0 | (not in the user map; `starter` is in the shipped map) | add `starter`, verified |
| 0x08.2 | "PSP", always 1 | keep as `psp_sw`, unverified, polarity open until the steering test |
| 0x08.3 | `brake` unverified | -> **verified** (3 pulses match 21:09:18-23) |
| 0x08.7 | - | constant 1; candidate gear-position input, test |
| 0x0A.7 | "engine running" | **wrong**: 1 at key-on engine off. Rename `ecu_ready` (unverified) |
| 0x0A.4 | - | constant 1, unknown |
| 0x0B.6 | "engine running or 2nd O2 heater" | **engine turning** (on from the first crank read), likely |
| 0x0B.7 | literature: 2nd O2 heater | never on in more than 1 h warm: not supported |
| 0x0B.5 | `mil` verified | keep, but d31 shows 0 during 14 s of key-on; confirm |
| 0x0C.1 | `fan` | **wrong**, drop |
| 0x0C.0 | - | new: drops for less than 1 s at large load steps (3 seen); unverified |
| 0x0F.0 | summary "off when revved 1600-2400" | summary wrong: off in **decel fuel cut** and the first 13-19 s after a start |
| 0xA4.0 | - | new unknown status bit at fast idle (sweep only) |

## Bits that need a physical test

Log with the 0x08-0x0F block at the fastest rate the app allows (ideally a dedicated `08+8` read at 5-10 Hz). Note the wall-clock time of each action (say "now" into a phone voice memo, or press the marker key).

1. **PSP, 0x08 b2 (polarity):** at warm idle, turn the wheel to full lock left and hold for 5 s, back to centre for 10 s, then full lock right for 5 s, centre. Do it 3 times. Expect b2 to flip at lock (to 0 if inverted) and IACV/0x29 to rise.
2. **Gear-position input, 0x08 b7 / 0x0A b4 / 0x08 b4-b6:** parking brake on, foot hard on the brake. Auto: select D for 5 s, then N, then R for 5 s, then P. Manual: clutch pedal down for 5 s (engine running, in neutral), then release. Look for the bit that flips.
3. **Key-on sequence, 0x0A b7 / 0x0B b5 (MIL) / 0x0B b0 (pump):** with the logger already powered and connecting, turn the key OFF -> ON (do not start) and leave it 30 s. Watch the dash and note the second the MIL goes out, if it does. Then start. This shows whether the MIL bit follows the bulb and when 0x0A b7 comes on.
4. **Engine-running bit, 0x0B b6 vs O2 heater:** at warm idle, stall the engine with the key left ON. Manual: parking brake on, 1st gear, let the clutch out slowly. Auto: use the key-off/key-on below instead. Leave the key ON for 15 s. Expect 0x0B b6 to drop at once if it means "engine turning", pump b0 to drop after about 2 s, and the MIL b5 to come back. Auto-only alternative: unplug the secondary O2 sensor (under the car, behind the cat) at warm idle; a heater output bit would not change, so this only rules things out.
5. **0x0C b0 (load step):** at warm idle, switch on headlights (high beam), rear defogger and blower on max all at once. Hold 10 s, all off, wait 20 s. Repeat 5 times. Needs the fast flag read: at 1 Hz it caught 3 of about 58 steps.
6. **Brake, 0x08 b3 (one more check):** hold the pedal down for 10 s, then release for 10 s, twice. Already matched once; this makes it unambiguous.
7. **SCS, 0x09 b3:** only if the SCS service jumper is at hand. Key off, jumper the 2-pin service-check connector under the passenger dash, key ON (MIL blinks codes). Log 10 s, then remove the jumper.
8. **VTEC, 0x0A b2:** not a stationary test. One 2nd-gear pull past 5,800 rpm on a closed road with the flag block logged.
9. **0xA4 b0 and 0xAD:** no physical action. Add 0xA4 (e.g. a 0x9A+20 block) to the slow read plan for one cold start and one drive.
