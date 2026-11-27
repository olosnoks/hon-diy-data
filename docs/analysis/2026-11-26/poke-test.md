# Poke test: finding unknown bytes by stimulus

`tools/poke_test.py` sweeps every RAM address 0x00-0xFF about once a second while the owner
does one thing at a time to the parked car. Afterwards it reports which bytes and bits
answered each action. It sends only what `work-sweep/sweep.py` sent: the map's wake frame
and Honda read requests (`20 05 addr 10 cksum`), through hondalog's own `Link` with the
read-only guard on. It never uses the expert unlock or any OBD2 service, and it never
touches COM23 (the CYD).

Sample report from a simulator run: `poke-test-sample-sim.md`. That run used a subset of
the steps at half time, so it shows what the report looks like. It says nothing about the car.

## Running it

From the repo root, laptop on mains power if you can:

```powershell
# 1. check the steps and timings (no cable)
.venv\Scripts\python.exe tools\poke_test.py list

# 2. rehearse on the simulator (takes nothing, no prompts)
.venv\Scripts\python.exe tools\poke_test.py run --dry-run --auto --time-scale 0.3

# 3. the car
.venv\Scripts\python.exe tools\poke_test.py run --port COM21
```

On COM21 the tool:

1. refuses to start if `tools\.watch-pause` already exists, or if another hondalog-log /
   probe / firstcontact / map / sweep / crosscheck / poke_test process is running;
2. creates `tools\.watch-pause` and closes the app window gracefully, as `take-cable.ps1`
   does (the app is stopped by force only if it has not closed after 30 s);
3. opens COM21, sends the wake frame, and starts sweeping in a background thread;
4. on the way out (end of the steps, `q`, Ctrl-C or an error) it closes the port and
   **always deletes the pause file** in a `finally` block. Closing the console window
   deletes it too, through a console control handler. The watcher then restarts the app
   within about 20 s. If the process is ever killed hard, delete `tools\.watch-pause` by
   hand.

The tool asks first whether the gearbox is automatic or manual (or pass `--trans auto|manual`),
because two of the steps depend on it. It asks before each optional step (`--optional yes|no`
answers all of them at once). Every prompt waits for Enter. At the first prompt of a step,
`s` skips the step and `q` ends the test (the run so far is kept and analysed). During a
countdown the status line shows `reads 16/16  rpm 812  sweeps 140`, so you can see the line
is alive. A long beep marks the end of each hold, a short one the end of each rest.

Other options: `--steps baseline,brake,keyon` runs a subset; `--steps-file my.toml` uses
your own steps (below); `--period` sets the full-sweep period (default 1.0 s); `--no-beep`.

Output goes to `poke-runs\<yyyymmdd-hhmmss>\` (git-ignored): `reads.csv` (every request),
`wide.csv` (one line per sweep, b00..bFF, blank = not read or no reply), `markers.json`
(steps, every phase with times, owner marks), `meta.json` (port, framing, labels, link
stats, cable take/give times), `report.md` and `findings.json`.
`poke_test.py analyse poke-runs\<folder>` analyses it again (for example with
`--alpha 1e-4` for a stricter test).

## The steps and what the owner does

Before you start: engine warm and idling, parked, parking brake on, in P or neutral,
everything off. Each step is: prompt -> settle (the change settles, not measured) ->
**hold** (measured) -> undo prompt -> settle -> **rest** (hands off; the baseline for the
steps either side).

| # | step | what to do | timing (s) | for |
|---|---|---|---|---|
| 1 | baseline | nothing; hands off | hold 20 | rest reference |
| 2 | ac | A/C on, then off | settle 5, hold 20, undo 5, rest 15 | 0x08 b1, 0x0B b1, idle-up |
| 3 | headlights | low beams on, then off | 3 / 15 / 3 / 10 | ELD, unknowns |
| 4 | defogger | rear defogger on, then off | 3 / 15 / 3 / 10 | |
| 5 | blower | blower max (A/C off), then off | 3 / 15 / 3 / 10 | |
| 6 | elec_load | all at once: high beams + rear defogger + blower max, 10 s; all off, 20 s; x5. Reads **only 0x00-0x1F, back to back** (about 15 Hz in the simulator) | setup rest 20; x5: settle 1, hold 10, undo 1, rest 20 | 0x0C b0 blink |
| 7 | brake | hold the pedal 10 s, release 10 s, x2 | x2: hold 10, rest 10 | 0x08 b3 |
| 8 | steering | full lock left 5 s, centre 10 s, full lock right 5 s, centre 10 s; x3 | x6: hold 5, rest 10 | PSP 0x08 b2 |
| 9 | gear_auto (automatic only) | parking brake on, foot on the brake throughout: D 5 s, N; R 5 s, P | setup rest 10; x2: settle 1, hold 5, undo 1, rest 8 | 0x08 b4-b7, 0x0A b4 |
| 10 | gear_manual (manual only) | parking brake on, neutral: clutch pedal down 5 s, up; x3 | setup rest 10; x3: settle 1, hold 5, undo 1, rest 8 | 0x08 b4-b7, 0x0A b4 |
| 11 | blip | press Enter, then blip to ~2000 rpm once and let go; x3 | x3: hold 6, rest 10 | rpm-driven bytes |
| 12 | rpm3000 | hold ~3000 rpm steady, then let go | 3 / 10 / 5 / 15 | |
| 13 | keyon **ENGINE OFF** | key OFF (the ECU goes silent, which is expected), 10 s; key ON without starting, press Enter at once; **during the 30 s hold press Enter the moment the MIL goes out**; then start the engine | pre 10, hold 30, undo 20, rest 20 | 0x0A b7, MIL 0x0B b5, pump 0x0B b0 |
| 14 | engine_stop **ENGINE OFF**, optional, manual only | parking brake on, foot on the brake, 1st gear, let the clutch out until it stalls, **key left ON**, 15 s; then clutch down, neutral, start. Skipped on an automatic (it cannot be stalled safely) | settle 2, hold 15, undo 20, rest 20 | 0x0B b6 |
| 15 | scs **ENGINE OFF**, optional | only with the service-check jumper: key off, fit the jumper, key on 10 s; key off, remove the jumper, start | pre 3, settle 2, hold 10, undo 20, rest 20 | SCS 0x09 b3 |
| 16 | custom, optional | anything you type in; you also say how to undo it | 3 / 15 / 3 / 10 | |

There are no driving steps (VTEC, 0x0A b2, needs a drive). `list` prints this table with
totals. **Timed length: about 11.4 min (manual) or 11.0 min (automatic) for the required
steps, plus up to 2.4 min of optional steps.** Add owner time at about 50 Enter prompts, so
expect **about 17-22 min** on the line, plus a few seconds to close the app. All that time
the app is not logging.

Key-off is handled without giving up the cable. After four failed reads in a row the
sweeper switches to a probe (wake frame + one read of 0x00, about twice a second) until the
ECU answers again, then goes back to full sweeps. In the simulator the ECU was back 0.4 s
after key-on.

### Your own steps (TOML)

```toml
# my-steps.toml: these replace the default list; a name that matches a default step
# starts from that step and overrides only what is given
[[step]]
name = "brake"
repeat = 4

[[step]]
name = "horn"
prompt = "Hold the horn."
settle_s = 0
hold_s = 3
undo = "Let go."
rest_s = 10
```

`include_defaults = true` at the top keeps the whole default list and applies the entries
as overrides or additions. Keys: `prompt`, `settle_s`, `hold_s`, `undo`, `undo_settle_s`,
`rest_s`, `repeat`, `engine` (`running`/`off`; an engine-off step must have an undo),
`optional`, `trans` (`auto`/`manual`), `note`, `prompts`/`undos` (lists, one per
repetition, cycling), `setup` + `setup_rest_s`, `teardown`, `pre` + `pre_s`, `mark`
(Enter during the hold records a timestamp), `fast` (`"0x00-0x1F"`: read only that range,
back to back), `sim`/`sim_undo`/`sim_pre` (simulator actions).

## Reading the report

- **Steps table**: hold and rest sweep counts per step, rpm and raw ECT at rest against the
  hold, and how many direct, indirect and edge responses there were. A step with
  alternating prompts (steering left/right, gear D/R) is analysed as a whole and per
  variant (`steering:1` = left, `steering:2` = right).
- **Flag bytes 0x08-0x0F and 0xA4, bit by bit**: for every bit and step, the share of
  sweeps with the bit set at rest and in the hold. **Bold** marks a response, `e` a
  response at the switching moment only. This table answers the flag-bit questions
  directly. For example, PSP should read `1->0` or `0->100` under `steering`.
- **Per step**: the responding addresses, ranked. Direct responses come first, then
  indirect ones. Columns: kind (`shift` = the distribution moved; `excursion` = values
  never seen at rest), rest and hold values, effect (`d` = difference of means over the
  pooled SD for bytes, change in % set for bits), p (Mann-Whitney for bytes, Fisher exact
  for bits). A row is listed at p < 0.001 with |d| >= 1 or |change| >= 50 %, or on an
  excursion (at least 2 sweeps, 20 % of the hold or p < 0.001, and p < 0.01 always).
  - **indirect: rpm r=...** means rpm (or ECT) moved in this step and the address follows
    it elsewhere in the run (|Spearman r| >= 0.6). It is probably a consequence of the
    idle-up or the warm-up, not of the switch. "(this step only)" means rpm did not vary
    outside the step, so the correlation includes it and is weaker evidence.
  - **rest drift** means the rest before and after the step disagree (the engine was still
    warming up or the idle was settling). Treat that row with care.
  - **At the switching moments** (edge rows): bits in a state never seen at rest during
    the prompt, settle and undo windows. Blinks such as 0x0C b0 at a load surge happen
    there, not in the hold.
- **Owner marks** (keyon: "MIL went out"): the time into the hold, and every bit that was
  steady for 3 sweeps before and after the mark and changed across it.
- **Flag-bit timeline** (engine-off steps): every change of 0x08-0x0F and 0xA4 with its
  time from the start of the hold, and when the ECU was silent. Pump prime, MIL and
  0x0A b7 timing are read here.
- **Per address**: which steps moved each address. **Unnamed addresses that responded
  directly** are the new candidates.

Engine-off steps move everything that depends on the engine running (rpm, MAP, injector,
0x0B b6...) against the running baseline. Their useful output is the direct rows on flag
bits and the timeline. At 1 Hz a sweep takes about 0.6 s and a byte is sampled once per
sweep, so a sub-second event outside elec_load can be missed or caught in one sweep only.
A repeated step (brake, steering, elec_load, blip) is what makes a single-sweep catch count.

## Tests

`tests/test_poke_test.py` covers:

- the step parser: defaults valid, inheritance by name, include_defaults, 15 rejection
  cases, selection, gearbox filtering, durations;
- the statistics;
- synthetic runs with a planted responding bit (ranked first, the noise byte stays out),
  rpm-driven bytes flagged indirect, a 0x0C-b0-style blink found as an edge, and an
  engine-off timeline with a MIL mark;
- the cable guard: refuses on an existing pause file or another port user, and always
  deletes its own pause file on Ctrl-C;
- refusing COM23;
- a short end-to-end run on `sim:` where the A/C switch, A/C clutch and brake bits come
  out as direct responses.
