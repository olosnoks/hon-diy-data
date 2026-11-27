# Next session: one day on the car, every open question

2000 Civic EX, D16Y8, stock P2P ECU, manual.
Laptop (hondalog, KKL cable on COM21) on the passenger seat.
CYD on the laptop's USB (COM23): GPS and relay.

Sources: `README.md` in this folder and the reports it links,
`../2026-11-25/README.md`, `poke-test.md`.

**Total: about 3 h 20 min on the day**, plus 5 min the evening before.
The shop checks at the end (F4-F8) add 1-1.5 h, on any day.

Short on time? Do A-D on the day. E and F1-F3 can follow
any later drive, as long as the engine is fully warm.

---

## Ground rules

- Never idle in a closed garage. Outside, or garage door wide open.
- Never touch the laptop while moving. Look only when stopped.
- Run a phone voice memo for the whole drive (D).
  Say the clock time when you start it ("10:05, start").
  Then say what you are doing ("full-lock launch, now").
  That gives Claude the event times.
- Marks: **Space** in the app (only when stopped),
  or hold the CYD button 1 s (lands on the CYD card).
- All commands run in PowerShell from the repo root:
  `cd C:\Users\skons\Documents\hon-diy-data`
- Ask Claude not to push to `main` during the session.
  The watcher restarts the app on a new commit, which splits the drive.

Tiles below are F1 Sensors tile names (map p2p-guess-7):
RPM, Speed, Coolant, Intake air, MAP, Battery, Alternator load,
Electrical load, Short trim, Long trim, Total trim, O2, O2 rear,
Idle valve, IAC command, IAC learn, Idle target, Purge duty?,
Fuel system, Closed loop, A/C, A/C clutch, Steering load,
VTEC, VTEC press. sw, O2 heater?, Starter, MIL, Fuel pump,
and the Raw 0x.. tiles.

---

## A. Before starting the engine (cold, overnight soak)

### A0. The evening before: push the map to the CYD (5 min)

The app must be closed, and the watcher must not restart it.

```powershell
New-Item tools\.watch-pause -ItemType File
```
Close the hondalog window. Then:
```powershell
.venv\Scripts\hondalog-handheld.exe push-map --port cyd:COM23
.venv\Scripts\hondalog-handheld.exe reboot --port cyd:COM23
Remove-Item tools\.watch-pause
```
- [ ] The app comes back by itself within ~20 s.
- [ ] Pass: no "map hash differs" warning in the app.
- **Do not forget the `Remove-Item`.**
  The poke test refuses to start while the pause file exists.

Also the evening before:
- [ ] Laptop fully charged. A 12 V charger or inverter if you have one.
- [ ] Windows: sleep = never, lid close = do nothing (on battery too).
- [ ] Engine off by 22:00 or so: at least 8 h of soak before A3.
- [ ] Optional: fill the tank right before the drive (D) instead,
      and note the time. Fresh fuel makes the purge effect bigger.

### A1. Re-seat and strap the OBD2 plug (5 min)

- Unplug the KKL cable. Look at the DLC pins:
  pin 7 (K-line), pins 4 and 5 (grounds). Bent, pushed back, dirty?
- Plug it in firmly, straight, fully home.
- Zip-tie or Velcro the cable to the dash frame,
  away from your knee and the pedals. Leave slack at the plug.
- Settles: the loose contact after 23:10
  (1.7 link errors/min, 5.7 s and 8 s silences while moving).
- Pass (checked in D): link errors back to the pre-23:10 rate,
  about 0 per 20 min, no "ECU silent" while driving.

### A2. Battery at rest (2 min, multimeter)

- Key OFF. Meter on the battery posts. Write it down.
- Expect 12.4-12.8 V after a night.
- Settles: the battery scale (`x / 10.45`), together with A3 and B3.

### A3. Key ON, engine off, full RAM sweep (about 3 min)

Key to ON (do not start). Then:
```powershell
.venv\Scripts\python.exe tools\poke_test.py run --port COM21 --trans manual --steps baseline
```
- The tool closes the app, sweeps 0x00-0xFF for ~20 s, gives the cable back.
- Hands off during the 20 s hold.
- Settles, cold and before the start:
  - 0x3A at the start (it was a constant 25 when warm);
  - 0x2A (IAC learn) after a key cycle: Claude compares it with
    last night's last value (logged at 23:26);
  - 0x80-0x9F at session start (counters, readiness, status bits);
  - whether 0x40-0x75 and 0xA5-0xFF stay 0 when cold.
- Note the run folder name it prints (`poke-runs\<date-time>`).
- Then wait until the app is back (~20 s) and its tiles move.

While the app is back, still key ON, engine off:
- [ ] Battery tile vs the meter on the posts.
      Pass: tile 0.1-0.3 V below the posts.
- [ ] Coolant vs Intake air tiles. After a cold soak both should be
      near the outside air. Pass: within ~3 °C of each other.
      (At a cold sensor the bad contact matters little;
      a big gap here is a different fault.)
- [ ] MIL lamp on. MIL tile set.
- [ ] Temperature gauge on the dash: at the bottom.
- [ ] Optional: read stored/pending codes and readiness with the app's
      OBD2 function. **Do not clear anything.** Note any code.
      (Readiness matters for the cat: P0420.)

Do not leave the key on longer than ~5 min. Go to B.

---

## B. Cold start and warm-up at idle (25-30 min)

### B1. Start (1 min)

- Clutch down, neutral, parking brake on. Start without throttle.
- Space (MARK) once it runs.
- Watch: Starter (set while cranking), O2 heater? (set from the
  first cranking read), RPM, Idle target.
- Settles: crank flags; the cold after-start flare.

### B2. Listen at the front of the engine (2 min)

- Hood up, engine idling. Listen near the exhaust manifold
  and the manifold-to-downpipe flange.
- A ticking that fades as it warms = exhaust leak before the front O2.
- Settles: the top suspect for the +16-20 % idle trim
  (drive-fueling.md: trim follows rpm, not MAP).
- Safety: keep hands, hair and sleeves away from the belt and fan.

### B3. Battery running (1 min, meter)

- Meter on the posts at idle.
- Pass: 13.9-14.3 V, Battery tile 0.1-0.3 V below the posts.

### B4. Warm-up at idle, hands off (20-25 min)

Neutral, parking brake on, everything off (lights, A/C, blower).
Sit and watch F1. Stop when the radiator fan first runs,
or after 30 min, whichever comes first.

Watch and note the clock time of each:
- **Idle target vs Coolant.** Target falls one step (13.6 rpm)
  per raw ECT count. RPM follows 2-3 s later.
  Settles: the target table when cold (only 57-86 °C known),
  and any after-start adder. No pass/fail: it is the measurement.
- **Fuel system** 1 (open loop) then 2 (closed).
  **Closed loop** 0 then 1. Time from start?
  (14 s on a warm restart.)
- **O2 rear.** High (3.3-3.9 V) while its heater warms, then falls
  toward ~1 V. Time it (86 s on a warm restart).
- **Purge duty?** Expect 0 until Coolant reads about 68-69 °C,
  then it starts ticking 25-50 s later (about every 31 s at idle).
  Pass: starts only above ~68 °C. Fail: counts while cold.
  Settles: 0x2F = EVAP purge (enable at 68 °C per the manual).
- **Long trim.** Expect it frozen until Coolant reads ~74 °C.
- **IAC learn.** Note the value at start and any step.
- **Raw 0x.. tiles.** Expect all 0 (or their constant).
  Any that moves while cold is a new finding: note the time.
  Settles: whether the dead bytes can be dropped
  (faster slow block, 1 Hz to ~2-3 Hz).
- **Coolant.** Should rise smoothly. A jump of 4 °C or more in one
  read is the connector fault: note the time and the jump.
- **First fan run:** you hear it; Electrical load steps about
  +7.5 A for 16-22 s, then Coolant drops 3-6 °C.
  **Write down the Coolant reading at fan-on.**
  - Pass (good contact): 84-88 °C.
  - Fail (offset fault, as on 11-26): 69-75 °C.
- Temperature gauge: should reach mid-scale.

If the idle sags below ~600 rpm on its own, press Space and note it.

---

## C. Poke test at warm idle (20-25 min)

Before: engine warm and idling, parked, parking brake on,
neutral, everything off. Hood closed (B2 done).

```powershell
.venv\Scripts\python.exe tools\poke_test.py run --port COM21 --trans manual
```
- It closes the app, runs the steps, gives the cable back.
- Each prompt waits for Enter. `s` skips a step, `q` ends it
  (what is done is kept).
- Long beep = end of a hold. Short beep = end of a rest.
- The CYD will show "link lost" (and may file a stall) while the
  app is closed. Ignore it. It is not the engine.
- The app is not logging during the test.

Optional steps, answer like this:
- engine_stop: **yes**. Parking brake on, foot hard on the brake,
  1st gear, let the clutch out until it stalls, key stays ON.
- scs: **no**, unless you have the service-check jumper.
- custom: **yes**. Type:
  prompt `hold 4300 rpm in neutral`, undo `let go`.
  Settles: is the O2 heater? cut load-gated (it dropped at
  3,550-4,150 rpm only with MAP above 84 kPa on the road).
  Pass for "heater": O2 heater? stays set at 4,300 rpm no load.

What the steps settle (the report shows them; no need to watch):
- ac: A/C and A/C clutch bits, the A/C idle-up.
- headlights / defogger / blower: Electrical load steps
  (low beams ~9-10 A = ~23-25 counts if 0.394 A/count is right).
- elec_load (5 x all on): the 0x0C b0 blink at a load surge.
- brake: the brake bit, once more.
- steering: Steering load (PSP) polarity at full lock,
  and the IAC command rise.
- gear_manual (clutch pedal): expect no bit. Any bit = new input.
- blip, rpm3000: rpm-driven bytes.
- keyon (engine OFF): press Enter **the moment the MIL goes out**.
  MIL bit vs the lamp, fuel pump prime (~2 s), 0x0A b7.
- engine_stop: does O2 heater? drop at once (engine turning)?

Afterwards:
- [ ] Note the run folder (`poke-runs\<date-time>`).
- [ ] Wait until the app is back and its tiles move,
      and the CYD Link screen says VIA LAPTOP.

---

## D. Drive loop (about 65 min)

Before moving:
- [ ] App recording, tiles moving. CYD on Gauges.
- [ ] Laptop secured (seat belt round it, or in the footwell).
      Lid open is fine: it must not sleep.
- [ ] Voice memo started, clock time said.
- [ ] A/C off, lights as needed.

General safety for every step:
- Legal speeds only. No manoeuvre with traffic close.
- If RPM sags and the engine shudders at a launch: clutch in.
- If it stalls: key stays **ON** for 10 s (the log then shows a real
  engine death with the ECU still talking), say "stall" into the memo,
  then clutch down, neutral, restart.

### D1. Empty parking lot: launches (15 min)

An empty lot, nobody near, walking pace only (under 10 km/h).
Before each launch: stop, wait 5 s at idle, say the type.

1. 3 launches with a touch of throttle (normal). Baseline.
2. 3 launches with the throttle closed, wheels straight.
3. 3 launches with the throttle closed, wheel at full lock.
4. A/C ON, wait 30 s. 2 closed-throttle launches straight,
   then 2 at full lock. Leave the A/C on for D2.

- Watch afterwards (not while moving): RPM minimum per launch.
- Expect (11-26): closed throttle 440-590 rpm;
  with throttle 670-906 rpm.
  Full lock and A/C are expected to cost 40-90 rpm each.
- Settles: the steering and A/C load margin at a launch,
  the most likely way this car stalls in traffic.
- Safety: a stall here is harmless and useful. Do not repeat a
  launch straight after an aborted bite (wait the 5 s).

### D2. Town with the A/C on (10 min)

- Normal driving, normal launches (touch of throttle).
- Settles: A/C and A/C clutch bits off idle,
  compressor cycling while moving, A/C at launches in traffic.
- Expect: A/C clutch toggles every so often; Idle valve higher at stops.

### D3. Interstate: VTEC pull, cruise, A/C pull (25 min)

**D3a. VTEC pull, A/C OFF.**
Only on an empty on-ramp with a clear merge, legally.
- 2nd gear, full throttle from ~30 km/h to **5,800 rpm**
  (about 75 km/h on the Speed tile; 2nd = ~77 rpm per km/h).
  Shift up, merge normally. One pull. A second only if the ramp
  is again empty.
- Never past 6,000 rpm. No pull if the ramp is busy: skip it.
- Say "VTEC pull" into the memo.
- Expect: VTEC and VTEC press. sw set from ~4,800-5,400 rpm;
  O2 heater? clears above ~3,550-4,150 rpm at full throttle;
  Fuel system 4; O2 0.87-0.94 V (rich).
- Settles: VTEC engagement rpm, whether the pressure switch follows
  the solenoid, the O2 heater? cut.

**D3b. Sustained cruise (10-15 min).**
- 5th gear at the legal limit, 100-110 km/h (~2,800-3,050 rpm).
- If there is a long downhill: off throttle in gear for a while.
- Expect: Coolant flat (65-68 °C if the offset is there, ~80-88 if
  not); no link errors; Purge duty? climbing toward ~210 with
  Short trim falling (rich slugs of -10 to -25 % for 30-60 s);
  Total trim about +11.5 %.
- Settles: link quality on a long run after A1, purge at cruise,
  cruise trims, thermostat at speed.

**D3c. A/C pull on the way back.**
Exit, turn round, A/C ON. On an empty on-ramp, legally:
- 3rd gear, full throttle from ~50 km/h to **~4,300 rpm**
  (about 85 km/h). Then merge, cruise 5 min with the A/C on.
- Settles: does the A/C clutch drop at full throttle,
  and together with O2 heater?
- Then exit, A/C OFF.

### D4. Hill (10 min)

Pick the steepest long grade near the route.
- Climb in 4th, part throttle, 1,800-2,200 rpm.
  Do not lug below ~1,500 rpm: shift down.
- Descend in 4th off the throttle.
- Optional, quiet street only: one hill start, parking brake held,
  **with** a touch of throttle (never closed-throttle on a slope).
- Expect: MAP 85-95 kPa on the climb, still closed loop;
  Injector 0 ms in the off-throttle descent (fuel cut).
- Settles: high-load low-rpm fuelling; the load margin of a hill start.

### D5. Home (5-10 min)

- Normal driving. At the last 2-3 stops, wait at idle 3-5 s before
  the clutch bites.
- Park where you can idle for 30 min. **Do not switch off.** Go to E.

---

## E. Hot idle after the drive (25-30 min)

Neutral, parking brake on, everything off. App still recording.

### E1. Hands-off hot idle (15 min)

- Watch: Coolant, Electrical load (fan steps), Idle target,
  RPM, Idle valve, IAC command, IAC learn.
- **Note the Coolant reading at every fan-on** (expect a run
  every ~3 min).
  - Pass: 84-88 °C. Fail: 69-75 °C (13-15 °C low).
- Idle target: ~670 rpm if the contact is good, 810-864 if not.
- RPM vs target: idle at or above target with Idle valve near its
  floor (5-11 %) = air getting past the valve.
- IAC learn: if Coolant reads 82 °C or more, it should follow the
  IAC command within ~40 s. Otherwise it stays frozen.
- Settles: fan cycling and ECT at fan-on, the hot idle target,
  0x2A learning when warm.

### E2. ECT wiggle test with the live tile (5-10 min)

Hood up, engine idling. Coolant tile visible (laptop on the fender
or a helper). Coolant updates once a second: hold each move ~3 s.
Note the steady reading and Idle target first.

At the 2-pin ECT connector C122 (RED/WHT and GRN/BLK; not the
1-wire gauge sender next to it):
1. Push the connector in, pull it gently.
2. Rock it up and down, then side to side.
3. Tug each wire ~1 cm behind it: RED/WHT, then GRN/BLK.
4. Squeeze the lock.
5. Flex the harness at each clip toward the body.
6. At the ECM (passenger kick panel), rock the connector.

- Pass: reading stays within ±1 °C.
- Fail: a step of 4 °C or more, or a drop toward 35 °C,
  that one spot repeats. Mark it (Space) and remember where.
- A jump **up** to 82-88 °C is the bad contact briefly closing.
- Safety: fan can start any time. Hands clear of fan and belt.
  No loose sleeves.

### E3. Heavy electrical load and A/C (5 min)

Hood can stay up; stay clear of the fan.
1. High beams + rear defogger + blower max + wipers, all on. 60 s.
2. Add the A/C. 60 s.
3. All off. 30 s.
- Watch: Electrical load, Alternator load, Battery, RPM,
  Idle target, Idle valve.
- Expect: RPM dips (-90 rpm for a ~17 A step on 11-26) and is back
  at target within ~4-5 s; Battery holds 13.8-14.6 V.
  Spec base idle: 670 ± 50, 750 with headlights, 810 with A/C.
- Pass: RPM never below ~600, recovers within 5 s.
- Settles: heavy load at idle with A/C on top, the worst stationary case.

Then go straight to F1 while the engine is hot.

---

## F. Mechanic checks (logger not needed)

Order matters: F1 hot, F2 after a heat soak, F3 last
(it may store a code).

### F1. ECT static voltage check, engine hot and running (10-15 min)

Back-probe without piercing. Meter on mV.
1. At C122: RED/WHT to GRN/BLK. Write it down.
2. At the ECM: C18 to C26. Write it down.
- Healthy: both 0.5-0.7 V, equal within ~10 mV.
- Last night's state predicts ~1.0-1.15 V at the ECM.
3. If (2) is higher than (1): ECM C18 to the RED/WHT pin at C122,
   then GRN/BLK pin at C122 to ECM C26, in mV.
   The leg with tens to hundreds of mV has the bad contact.

### F2. Key off, heat soak, hot restart (15 min)

- Key OFF. While it soaks (optional, sensor unplugged):
  - Sensor resistance: about 0.3 kΩ near 80 °C (check the manual's curve).
  - Each wire ECM pin to C122 pin: under 1 Ω.
  - A spare male pin in each C122 terminal must drag firmly.
  - Look for oil or coolant in the seal, green corrosion.
- Plug it back in. After 10 min, restart with the app recording.
- Watch: O2 rear warm-up, Idle target, Intake air (heat soak).
- Settles: the hot-restart after-start and the rear O2 warm-up hot.

### F3. Base idle with the IACV unplugged (5 min)

Engine fully warm, idling, everything off, app recording.
1. Note RPM. Space.
2. Unplug the IACV connector. Space. Watch RPM for 30 s.
3. Plug it back in. Space. Key off, 10 s, restart.
- Pass (normal): RPM drops sharply, maybe stalls.
- Fail: RPM barely moves = the idle air comes from elsewhere
  (vacuum leak, fast-idle thermo valve, throttle stop).
- It may store an idle-air code and light the MIL.
  **Do not clear codes.** Tell Claude; clearing also resets the
  learned values and the readiness monitors.

### F4. Intake smoke test (shop)

PCV hose and grommet, brake booster hose, manifold and throttle-body
gaskets, IACV gasket, fast-idle thermo valve, purge lines.
Also check the throttle stop screw and cable slack,
and that the fast-idle thermo valve is shut when hot.

### F5. Tailpipe gas (shop or borrowed wideband), warm

At warm idle and at 2,500 rpm. App recording, so trims line up.
- λ < 1 or high CO at idle (with +16-20 % trim):
  front O2 fooled lean (exhaust leak, misfire, sensor).
- λ ≈ 1, low CO: really lean, fuel side (see F6).
- High HC plus O2: misfire.
- Same visit: exhaust leak check (manifold gasket, flange, O2 boss),
  front O2 response.

### F6. Fuel pressure (shop)

260-310 kPa hose off, 200-250 kPa hose on.

### F7. Clutch engagement point and free play

A high, sudden bite makes the closed-throttle launch dips worse.

### F8. Optional: photo of the ECU label

Part number and calibration stamp, for the constant at 0x78-0x7F.

---

## What to tell Claude afterwards

Clock times (from the app, the memo or your notes):
- Key-on (A3), engine start (B1), first fan-on (B4).
- The two poke run folders (A3 and C).
- Drive start and end.
- Each D1 launch, in order: type (throttle / closed / full lock / A/C).
- VTEC pull, cruise start and end, A/C pull, hill climb.
- Any stall, stumble or near-stall, and whether you caught it.
- Each fan-on in E1, and the Coolant reading at each.
- E2: the spot that made the reading jump, and the time.
- E3: start and end of the load.
- F2 restart, F3 unplug and re-plug.

Numbers and notes:
- A2/A3/B3 meter readings vs the Battery tile.
- Coolant vs Intake air at key-on.
- Exhaust ticking at the cold start: yes or no, where.
- Dash temperature gauge position when warm.
- F1 voltages (all four if done), F2 resistances.
- F3 rpm before, with the valve unplugged, after. MIL on or off.
- Any codes read (A3, after F3). Codes were not cleared.
- OBD2 plug: what the pins looked like, how it is strapped.
- Fuel: refuelled? When? Outside temperature.
- Shop results (F4-F7) whenever they come.
