# Sensor health and address-map verification — 2000 Civic EX (D16Y8, P2P), 2026-11-25

Plots: plots-sensors/overview.png, plots-sensors/decode_and_gaps.png. Scripts: work/. Times local (UTC−6). Page numbers = PDF pages of the factory manual. "Warm idle" = true-warm ECT 75–92 °C, 640–710 rpm, A/C off, closed loop.

## 1. Decodes
### Ignition timing (0x26) — best fit (x−46)/4 (none of the published candidates fit)
- Warm idle: PID 0E median 12.0° (IQR 11.5–12, n=696); manual 12±2° BTDC (p.356, p.1575). Raw 0x26 median 94 (IQR 93–96).
- Raw 94 → (x−128)/2 = −17°; (x−24)/4 (current) = 17.5°; (x−21)/4.266 = 17.1°; (x−28)/2.844 = 23.2°; **(x−46)/4 = 12.0°**.
- 1/4° per count: idle spread 12 counts vs PID 3.5° (fits 0.25°/count); max 170–174 at 1600–2800 rpm light load → 31–32° with /4 (other slopes impossible). Earlier one-off: raw ≈115 → 17.2° vs PID 16.5°.
- Behaves like timing: during false-cold ECT fast idle 0x26 rose to ~120 (18.5°), the coolant advance (p.373).
- Offset ±1°, slope unproven (PID and Honda never read at the same time). Decide with an interleaved PID 0E / 0x26 session or a timing light with SCS jumped (p.1575).
- 0x27 is not a timing limit: 0 in 88 % of samples, else 1–4 (rarely 10/16), tracks rpm (r=0.78). Unidentified. 0x21, 0x23 always 0; 0x29–0x2F never read.

### O2 (0x15) — x/51.3 correct (high confidence)
- Warm closed-loop idle: every percentile 1–99 % matches PID 14 within ~0.02 V (median 0.624 vs 0.605 V); 58.98 % vs 58.93 % above 0.45 V. Manual 0.1–0.9 V at idle (p.355).
- The earlier 2.03 V was the ECU's start-up value: 0x15 reads 185–200 raw (3.6–3.9 V) at key-on and 6–10 s after each restart, then decays. In closed loop it never exceeds 60 raw (1.17 V).
- Stored PID 14 bytes decode fine with A/200 (0.14–1.03 V); B = sensor trim (+6 to +12 %).

## 2. Channel health
- rpm: two idle levels — ~670–680 (spec 670±50, 750±50 with low beams, p.489) and ~805–815 (IAC byte 0x28 ~7→~17, ALT FR drops, A/C off). 810±50 = EVAP purge (p.492) or heater fan Hi with A/C (p.490) — likely purge or an electrical load, unproven.
- ECT (0x10): true 80–92 °C sawtooth = radiator fan cycling on its thermoswitch (spec 80–90 °C, p.355). 32 false-low episodes, ~1790 s, ~11 % of Honda recording time, reading 30–36 °C (raw 120–135); worst 17 °C (raw 167) for 426 s at 21:42–21:49. ECU responds: fast idle ~1080 rpm, timing +~6°, IAC to ~30. No MIL (30 °C is a valid reading). Raw value goes UP in the fault = series resistance (poor contact), not leakage to ground — fits an oily/loose C122. 0x10 is the raw ADC (~0.74 V warm, ~2.5 V in the fault); no other byte copies ECT.
- IAT (0x11): not stuck; 42→53 °C over the evening, +3–5 °C after restarts (heat soak), continues PID 0F trend.
- MAP 25.1–25.8 kPa idle, matches PID 0B (spec 24–31, p.355).
- Baro (0x13): raw 147 all evening = 100.3 kPa, equals key-on MAP. Correct.
- TPS 7.8 % = PID 11 (manual ~10 % at idle, p.356).
- Battery (0x17, x/10.45): 14.1–14.2 V running, 12.63 V key-on engine off, 10.9 V cranking. Regulator spec 13.5–15.1 V at 2000 rpm (p.1588). Healthy; confirm once with a voltmeter.
- ALT FR (0x18): ~59 key-on, 105–155 running, falls with rpm (r=−0.79) like field duty (p.478). Scale unconfirmed.
- ELD (0x19): raw 155–171 → 9.7–15.8 A with current formula; equally plausible as raw ELD volts (x·5/256 = 3.0–3.3 V; p.360, p.449). Settle with a headlight on/off test.
- Fuel trims: STFT ~+8.6 %, LTFT ~+9 % (PID LTFT 2.3→10.2 %) — inside ±20 % (p.356) but a combined ~+18 % lean correction at idle.
- Injector PW (0x24–25): median 2.39 ms — plausible.
- IAC (0x28): 0–58 raw, follows idle air demand = IAC command; % scale unknown.
- Knock (0x3C): only 1 or 2 — not a voltage.
- Fan cmd (0x0C b1): never set while the ECT sawtooth shows the fan cycling; radiator fan is thermoswitch-controlled (p.373) — this bit is not a radiator fan command.
- Flags: PSP (0x08 b2) always 1 (inverted or not PSP); brake never toggled in this data; VTEC never engaged (expected). 0x0A b7 and 0x0B b6 = "engine running". MIL, fuel pump, A/C clutch, starter consistent.
- Rates: Honda drives ~31–35 reads/s (three fast blocks ~10 Hz each, slow ~1 Hz); OBD2 drives PID 0C ~3.8 Hz, others 0.2–0.6 Hz.

## 3. Recording integrity
- No out-of-order or duplicate timestamps; continued sessions (15→16, 20→21, 22→23, 27→28→29) don't overlap.
- link_stats unreliable as stored: drives ending link_lost hold reset counters; the next drive holds the session total; drive 29's stats never updated. Session totals at 21:42: 108,483 requests, 97.6 % OK, 939 timeouts, 1645 bad frames, 1664 echo mismatches, 1836 retries, 182 wakes, 0 checksum failures. OBD2 drives' stats describe failed Honda reads.
- Gaps: 37 gaps > 0.8 s (longest 2.59 s at 19:34:59; 1.83 s at 21:52:30); 36 contain a link-down = resync/re-wake after a failed read. 21:42–21:54: extra 12+8 and 20+9 block reads (1486, nowhere else) — fast-block rate halved, spacing 0.05–0.6 s, link drops 2.2/1000 vs 1.2–1.7 baseline. From ~20:30 worst-case recovery grew 0.2–0.3 s → 0.5–0.6 s. Gaps unrelated to engine state. Reads right after 3C+1 or 00+3 fail more often than their share — check the scheduler.
- Restarts: drives 16, 21, 23, 28 begin at key-on/cranking.
- GPS (quality only): laptop GPS drive 7, 37 fixes at 1 Hz, no sat/HDOP/speed. Handheld GPS drives 17–29 ~0.85–0.9 Hz, median 11–14 sats, HDOP 0.8–1.1; 23–87 dropouts > 2 s per drive, a 901 s hole in drive 22; fix flag always 1 even with 0 sats / HDOP 115.8 (untrustworthy); speed spikes 351 and 2267 km/h while parked.

## 4. Recommended address-map changes
```toml
[[channel]]  # HIGH confidence
name = "o2"
addr = 0x15
formula = "x * 5 / 256"   # same as x/51.3 within 0.2 %
status = "verified"
notes = "185-200 raw at key-on and ~10 s after start: ECU start-up value; ignore while closed_loop == 0"

[[channel]]  # MEDIUM: offset ±1°, slope not yet proven
name = "ign"
addr = 0x26
formula = "(x - 46) / 4"
status = "unverified"

[[channel]]  # MEDIUM: not a timing limit; unknown state value 0-4
name = "ign_limit"
addr = 0x27
formula = "x"
units = "-"
status = "suspect"

[[channel]]  # MEDIUM-HIGH
name = "batt"
addr = 0x17
formula = "x / 10.45"
status = "unverified"

[[channel]]  # HIGH: constant 100.3 kPa, equals key-on MAP
name = "baro"
addr = 0x13
formula = "x * 0.716 - 5"

[[channel]]  # HIGH that this is wrong: never set while the fan cycles
name = "fan"
addr = 0x0C
formula = "bit(x, 1)"
status = "suspect"

[[channel]]  # MEDIUM: only 1 or 2, not a voltage
name = "knock"
addr = 0x3C
formula = "x"
units = "-"
status = "suspect"
```
Lower confidence: PSP may need `1 - bit(x, 2)` (turn the wheel at idle). ELD as raw volts (x*5/256) until calibrated.

## 5. Next tests
1. Clean/reseat C122; re-log; raw 0x10 should never exceed 100 when truly warm.
2. Interleaved PID 0E / 0x26 session, or timing light with SCS jumped.
3. Headlights and defogger on/off and wheel lock-to-lock at idle (ELD, ALT FR, PSP); unplug EVAP purge solenoid (p.492) to test the 810 rpm level.
4. Read 0x29–0x3B once.
5. Logger: write link_stats per drive and on rollover/crash; keep ad-hoc reads out of the poller while recording.
