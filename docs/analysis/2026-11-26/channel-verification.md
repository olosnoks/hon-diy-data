# Channel verification without the car: 2026-11-26

This pass checks each unverified channel in `~/.config/hondalog/addressmap.toml` (p2p-guess-5) against evidence that is already logged. The evidence is: OBD2 PIDs from the same ECU (drives 2-5, firstcontact, probe), Honda reads in drives 6-35, the 11-25 and 11-26 reports, and physics. Scripts are in `work-verify/`; run `q2.py` first, because it builds `H.pkl` from the DB copy. The live DB and the map were not changed.

**Data warning:** in drive 1 (`drive_2026_11_25_1738`) the `08+10` block, which covers 0x08-0x11, holds a frozen, impossible pattern (`6B 30 F1 0E C7 DD 01 E4 45 8C` in every read). Do not use drive 1's flags, ECT or IAT. The "ECT 23 °C, fast idle" at the start of drive 1 is a bad read, not a cold engine. Drive 1's other blocks (rpm, MAP, 0x17-0x19, 0x24-0x26) look normal.

## Verdicts

| channel | addr | verdict | formula / units | evidence | 1-minute on-car test that settles it |
|---|---|---|---|---|---|
| baro | 0x13 | **verified** | `x * 0.716 - 5` kPa (same transfer function as the verified MAP) | Key-on engine-off MAP (drive 31, 151 reads) has median raw 147, the same as baro raw 147. Max MAP ever logged is raw 147. Baro is raw 148 (101.0 kPa) in drive 1 and 147 (100.3 kPa) in every later drive: one step, latched between drives. The firstcontact PID 04 (24.3 %) equals MAP/baro (24/100.3 = 23.9 %) within MAP's 1 kPa resolution. | None needed. Optional: compare with the station pressure at local elevation. The GPS track has no altitude, and the location was not sent to any lookup service. |
| o2 | 0x15 | **verified** | `x / 51.3` V (equals ADC `x*5/256` within 0.2 %) | Warm closed-loop idle, 54,860 Honda reads vs 499 PID 14 reads. Percentiles: 5 % 0.156 vs 0.175, 25 % 0.234 vs 0.230, 50 % 0.624 vs 0.605, 75 % 0.780 vs 0.760, 95 % 0.838 vs 0.835 V. Share above 0.45 V: 58.2 % vs 58.9 %. At key-on and for about 10 s after a start the ECU holds 185-200 raw (start-up value, not the sensor). | None needed. |
| batt | 0x17 | **plausible (high)** | `x / 10.45` V | Running: raw 146-151, i.e. 13.97-14.45 V, inside the regulator window. Key-on engine off: raw 132-137, i.e. 12.63-13.11 V (resting battery plus surface charge right after a run). Cranking: raw 114 (10.9 V). Over 53 radiator-fan load steps the median change is 0 raw (the regulator holds it). The alternative `x/10` gives 13.2-13.7 V key-on, too high for a resting battery. | Multimeter on the battery posts at KOEO and at warm idle, read together with 0x17. Expect about 0.1-0.3 V less at the ECU than at the posts. |
| alt_fr | 0x18 | **plausible as behaviour; scale unverified** | keep `x / 2.55` %, read as field duty | Rises at constant rpm when load comes on: median +16 counts over 53 fan steps and +27 at the 19:06:22 step. Falls when load goes off: median -17.5, n=23. Falls with rpm (r = -0.47; -0.79 in the 11-25 report). Key-on engine off: 59-62. That is how alternator field duty behaves. The brake taps at 21:09 also show a +35 jump. | At idle, low beams plus rear defogger on, then off. ALT FR should rise 20-40 counts and come back. Then hold 2500 rpm: it should fall. |
| eld | 0x19 | **plausible (medium-high)**: right direction and right step size | keep `77.06 - x / 2.5371` A (0.394 A per count, falls as current rises). The intercept is unproven. | 53 radiator-fan cycles (ECT 84-87 °C, then ECT falls 3-6 °C within a minute, every 2.5-3 min, no A/C bit): median step -19 counts = **+7.5 A**, the size of a radiator fan motor. A/C clutch plus condenser fan: -32 to -35 counts = +12.6-13.8 A. Baseline 155-160 raw = 14-16 A at idle. 0x1C is an exact copy. The raw-volts reading (x*5/256, about 3.1 V) fits the data equally well but gives no current. | Clamp meter on the battery negative cable. Low beams on and off at idle (about 9-10 A): ELD should drop about 23-25 counts if 0.394 A/count is right. |
| inj | 0x24-25 | **plausible (high)** | `x / 250` ms (4 µs per count, big-endian 16-bit) | Warm idle 2.44 ms at 25.8 kPa, 670 rpm, IAT 48 °C, trims +22 %. Regression over the closed-loop warm range (MAP 14-42 kPa): `inj = 0.0485·MAP_norm·(1+trim) + 1.02 ms`, r = 0.90. The intercept, about 1.0 ms, is a believable injector dead time at 14 V. The effective 1.44 ms × 240 cc/min × 0.745 g/cc gives 4.3 mg fuel = 63 mg air at λ1, so VE 0.57 at a 26 kPa idle: physical. `x/500` would leave less than the dead time; `x/125` would need VE above 1.4. 21 reads of exactly 0 at rpm > 1200 with throttle closed (decel fuel cut). Cranking 8.8-11 ms. Slightly longer at low battery (2.63 ms at 13.4 V vs 2.44 ms at 14.0 V), as dead-time compensation would do. | Scope or noid-light duty meter on injector #1 at warm idle. Expect about 2.4 ms (1.4 % duty at 670 rpm). Snap the throttle and let off from 3000 rpm: inj must show 0 during the cut. |
| ign | 0x26 | **wrong offset in the map; corrected formula plausible** | **`(x - 46) / 4` ° BTDC** (offset ±2 counts; slope 4-5 counts/°, 4 is the best supported) | **Warm idle (ECT ≥ 80 °C, < 720 rpm):** PID 0E median 12.0° (IQR 11.5-12, n=449); 0x26 median raw 94 (IQR 92-96, n=39,824). 94 → 12.0° with offset 46; the current `(x-24)/4` reads 17.5°. **Boundary pairs:** drive 1 ends 17:55:51 at raw 88 (750 rpm, 10.5° by the new formula); drive 2 starts 13 s later with PID 0E 10.5, 11, 11.5. Probe 18:17:43 read raw 92 (11.5°); drive 4 ended 8 s earlier at 12-12.5°. **Slope:** the one OBD2 rev blip (19° at 1185 rpm, 20 kPa) against Honda reads at the same rpm and MAP (raw 129.5, n=10) gives 5.1 counts/°. That is a transient pair, so it is weak. Light-load cruise rpm plateaus at raw 173-174 (2000-2600 rpm, 13-20 kPa): 31.8° with /4, 51° with /2 (impossible), 27° with /5. Key-on engine off: raw 43 (−0.75°, about 0). Fixed offset per slope (rms vs PID bins): /2 3.5°, /2.844 2.5°, /4 1.5°, /4.27 1.35°, /5 1.1°. The bins span only 10-13.5°, so the slope stays open. **Note on "16°":** the brief cites PID 0E 16° and a 16 ± 2° spec. The 16° is one firstcontact sample (787 rpm, ECT 75 °C, not settled). Every 11-25 report cites the factory manual as 12 ± 2° BTDC (PDF p.356, p.1575), and over 1,000 warm-idle PID 0E samples read 10-13.5°. Under the new formula 16° is raw 110, which the log shows at partly-warm or fast idle (raw 105 at 700-750 rpm, ECT 75-84). The manual PDF is not in the repo, so I could not check its page myself. | At warm idle, then held at about 2500 rpm in neutral: read PID 0E for 10 s, switch to Honda reads, then back again (same state). Two points fix offset and slope. Or use a timing light with SCS jumped: expect 12° at raw 94, and add a 2500 rpm reading for the slope. |
| ign_limit | 0x27 | **wrong as an ignition value**; the current raw "RPM band" description fits | `x` (no units) | Excluding drive 1: 0 below about 845 rpm, +1 per about 50 rpm, 4 from about 1010 rpm; 5 at key-on, 10 cranking (11-26 README); r = 0.78-0.83 with rpm. | Rev slowly from 700 to 1100 rpm in neutral: it should step 0→4 at the logged thresholds. |
| iacv | 0x28 | **plausible as the IAC output; % scale unverified** | keep `x / 2.55` but treat it as relative (0-137 raw seen) | Follows 0x29 on a fixed monotonic curve, slightly later (r = 0.95). Rises with A/C (6→44), fast idle and false-cold ECT; drops at ECT snap-back. Reads 0 at warm idle with rpm 700-850. A real IACV runs well above 0 % duty at idle, so either the scale has an offset or extra air enters past the valve (see 11-25 trims). | Duty meter or scope on the IACV signal wire at warm idle, then with low beams and the A/C on. Compare duty with 0x28 and 0x29 at the two points. |
| iac_cmd | 0x29 | **plausible (medium)**: command before the output curve | `x` raw | Floor 36-41 at warm idle; leads 0x28; one literature table names 0x29 IAC (11-26 README). | Same test as iacv. |
| knock | 0x3C | **wrong** (not a knock voltage) | `x` raw, no units, rename suggested | Excluding drive 1: value 1 in 16,989 reads, 2 in 325 one-read blips (598 changes, no rpm or load link; median ECT lower when 2). It is never a sensor waveform. The D16Y8 and P2P are generally reported to have no knock sensor, which is my recollection rather than a checked source. Confirm with the ECM pinout in the manual (no KS terminal). | None on the car. Check the ECM terminal list in the manual for a knock-sensor input. |
| vtec | 0x0A b2 | **untested** | `bit(x, 2)` | 0 in every read. The highest logged rpm is about 3300 and the D16Y8 engages VTEC at about 5,500-5,600 rpm under load, so it should be 0. No bit of 0x08-0x0F changed in any way that could be VTEC. | Not a 1-minute stationary test: SOHC VTEC needs road speed and load. Do one 2nd-gear pull past 5,800 rpm on a closed road and log 0x0A fast. Or put a test light on the VTEC solenoid lead and rev in neutral (it may still not engage without vss). |
| fan | 0x0C b1 | **wrong** | (no ECM radiator-fan output on this car) | Excluding drive 1, never set. Over the 53 radiator-fan cycles shown by ELD, the only bits that are not constant in 0x08-0x0F are: 0x08 b0 (starter, set only at cranking), 0x08 b1 (A/C switch), 0x08 b3 (brake), 0x0B b1 (A/C clutch), 0x0F b0 (closed loop). None of them follows the fan (fan-on vs fan-off share identical). The radiator fan runs on its thermoswitch (11-25 report). | None needed. Optional: pull the fan-switch connector with the engine warm and confirm that no bit moves. |
| brake | 0x08 b3 | **plausible (medium)** | `bit(x, 3)` | Set in exactly 3 reads, at 21:09:18, 21:09:21 and 21:09:23 on 11-26. Each read shows an rpm dip of 50-70, an ALT FR jump of +30-40, and a small timing bump, which is the signature of a brief electrical load (brake lamps, about 4-5 A). It is set in no other read. | Press and hold the pedal 3 times for 3 s each at idle, with 0x08 polled fast. |

## What changed against the earlier reports

- **ign:** this pass agrees with report-sensors (11-25) on offset 46 and adds the boundary pairs, the key-on 0° point and the slope limits. report-fueling's `(x-48)/4` is within the ±2-count uncertainty.
- **ELD and ALT FR:** new here. The roughly 19-count steps every 2.5-3 min are the radiator fan, because ECT falls right after each one. Some of these steps also lift idle by about 90 rpm, from 660 to 750 rpm (10 of 53). That is the ECU's ELD idle-up, so part of the "two idle levels" seen on 11-25 is electrical load. That does not explain the steady 810 rpm level.
- **knock and fan:** the evidence moves them from unverified to wrong. Mark them `suspect`.

## Proposed TOML changes (not applied)

```diff
 [[channel]]
 name = "baro"
 ...
-status = "unverified"
+status = "verified"
+notes = "Latched near key-on; equals key-on engine-off MAP raw-for-raw (147 = 100.3 kPa). 148 in drive 1 only."
+
+[channel.evidence]
+date = "2026-11-26"
+method = "analysis"
+result = "pass"
+summary = "key-on engine-off MAP median raw 147 = baro raw 147 (drive 31, 151 reads); max MAP ever 147; PID 04 24.3 % = MAP/baro"
+reference = "MAP 0x12 at KOEO"
+map_version = "p2p-guess-5 (kerpz P2T map + raw bytes, 0x27/0x29 named)"

 [[channel]]
 name = "o2"
 ...
-status = "unverified"
+status = "verified"
+notes = "185-200 raw at key-on and ~10 s after a start is an ECU start-up value; ignore while closed_loop == 0"
+
+[channel.evidence]
+date = "2026-11-26"
+method = "crosscheck:iso"
+result = "pass"
+summary = "warm closed-loop idle percentiles 1-99 % match PID 14 within ~0.02 V; 58.2 % vs 58.9 % above 0.45 V (not simultaneous)"
+reference = "PID 14"
+map_version = "p2p-guess-5 (kerpz P2T map + raw bytes, 0x27/0x29 named)"

 [[channel]]
 name = "batt"
 ...
 status = "unverified"
+notes = "Plausible: 14.0-14.5 V running, 12.6-13.1 V KOEO, 10.9 V cranking. Confirm once with a multimeter."

 [[channel]]
 name = "alt_fr"
 ...
 status = "unverified"
+notes = "Behaves like field duty: +16 counts per ~7.5 A load step at constant rpm, falls with rpm, ~60 at KOEO. Scale unproven."

 [[channel]]
 name = "eld"
 ...
 status = "unverified"
+notes = "Radiator fan steps -19 counts (= +7.5 A), A/C -32..-35 (= +13 A): slope plausible, intercept unproven. 0x1C is a copy."

 [[channel]]
 name = "inj"
 ...
 status = "unverified"
+notes = "Physics fits: 2.44 ms warm idle = ~1.0 ms dead time + 1.44 ms effective (VE ~0.57 at 26 kPa, lambda 1); 0 in decel cut; 9-11 ms cranking."

 [[channel]]
 name = "ign"
 ...
-formula = "(x - 24) / 4"
+formula = "(x - 46) / 4"
 ...
 status = "unverified"
-source = "kerpz hobd_uni2 (P2T)"
+source = "kerpz hobd_uni2 (P2T) address; offset fitted to PID 0E on this P2P (docs/analysis/2026-11-26/channel-verification.md)"
+notes = "Raw 94 = PID 0E 12.0 deg at warm idle; raw 43 at KOEO = ~0 deg. Offset +/-2 counts; slope 4-5 counts/deg not yet proven."

 [[channel]]
 name = "ign_limit"
 ...
-status = "unverified"
+status = "suspect"                      # not an ignition value on the P2P; the raw rpm-band description stands

 [[channel]]
 name = "iacv"
 ...
 status = "unverified"
+notes = "Monotonic, lagged function of 0x29 (r 0.95). Reads 0 at warm idle: % scale or offset unproven."

 [[channel]]
 name = "knock"
-label = "Knock"
+label = "Raw 0x3C"
 addr = 0x3C
 length = 1
-formula = "x / 51"
-units = "V"
+formula = "x"
+units = ""
 min = 0
-max = 5
-decimals = 2
-status = "unverified"
+max = 4
+decimals = 0
+status = "suspect"
+notes = "Only 1 (2 for single-read blips) on the P2P: not a knock voltage. D16Y8 is understood to have no knock sensor (check the ECM pinout)."

 [[channel]]
 name = "fan"
 ...
-status = "unverified"
+status = "suspect"
+notes = "Never set over 53 radiator-fan cycles (seen in ELD); the fan is thermoswitch-controlled on this car."

 [[channel]]
 name = "brake"
 ...
 status = "unverified"
+notes = "Set in 3 reads at 2026-11-26 21:09:18-23, each with an rpm dip and ALT FR jump (brake-lamp load). Press-test to verify."
```

Optional changes to the math channels. They follow from the inj result and are not needed for the channel verdicts:

```diff
 [constants]
+inj_dead_ms = 1.0                       # regression intercept of inj vs MAP at 14 V (2026-11-26); not measured
-ve_assumed = 0.85
+ve_assumed = 0.6                        # idle-ish value once dead time is removed

 [[math]]
 name = "fuel_flow"
-expr = "[inj_duty] / 100 * inj_flow_cc_min * cylinders * 60 / 1000"
+expr = "max([inj] - inj_dead_ms, 0) * [rpm] / 120000 * inj_flow_cc_min * cylinders * 60 / 1000"

 [[math]]
 name = "afr_est"
-expr = "clamp(([map] * displacement_l / cylinders * ve_assumed / (0.287 * ([iat] + 273.15))) / ([inj] / 60000 * inj_flow_cc_min * fuel_density_g_cc), 5, 30)"
+expr = "clamp(([map] * displacement_l / cylinders * ve_assumed / (0.287 * ([iat] + 273.15))) / (max([inj] - inj_dead_ms, 0.05) / 60000 * inj_flow_cc_min * fuel_density_g_cc), 5, 30)"
```

Bump `version` (for example `p2p-guess-6`) when applying.

## Scripts (`work-verify/`)

`lib.py` (loader), `q2.py` (builds H.pkl), `ign.py` / `ign2.py` / `ign3.py` / `ign4.py` / `crank.py` (timing), `inj.py`, `baro.py`, `eld.py` / `steps.py` / `tr.py` (load steps and traces), `bits.py` / `bits2.py` / `b3.py` (flags and knock), `o2.py`.
