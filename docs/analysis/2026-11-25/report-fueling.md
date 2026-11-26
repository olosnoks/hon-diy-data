# Fueling analysis — 2000 Civic EX (D16Y8, P2P, speed-density), driveway idle 2026-11-25

Data: drives 2–5 OBD2-only (17:56–18:19), drives 6–29 Honda RAM reads (18:19–22:14, ~115k fast rows), re-decoded with the current addressmap.toml. Plots: plots-fuel/. Scripts: work/.

## Bottom line
At warm idle the total fuel trim is high (+21.6 % at 670 rpm) and falls steadily as airflow rises, while injector pulse width barely changes. That points mainly to air/oxygen the model doesn't see (biggest at idle), not to fuel delivery. The bad ECT signal is not causing the lean trim.

## ECT states
warm (all readings ≥74 °C) 72 % of samples; false-cold (≤45 °C) 11 %; mixed 17 % (including long stretches reading ~57–70 °C, e.g. 21:59–22:07 — the connector also biases partially, not only hard drop-outs).

## Idle states
- ~670 rpm: MAP 26.2 kPa, IACV ~2.7 %
- ~810 rpm: MAP 25.2 kPa, IACV 6.7 %, ELD unchanged — probably EVAP purge (manual: idle 810±50 during purge, PDF p.491), unproven
- ~1080 rpm fast idle: MAP 23.6 kPa, IACV 11 % — only during long false-cold stretches

## Decode checks
- O2 (0x15, x/51.3) — correct; matches OBD2 PID 14 quantiles (0.156/0.253/0.624/0.780/0.838 V vs 0.175/0.230/0.605/0.760/0.835 V), 59.2 % vs 58.1 % above 0.45 V.
- Ignition (0x26, (x−24)/4) — reads ~5.5–6° high: 17.5° vs OBD2 PID 0E 11.9° (n=4401) and manual 12±2° BTDC (PDF p.356). Constant offset (x−48)/4 fits idle; scale unconfirmed — check with a timing light + SCS jumper.
- Injector PW (0x24, x/250 ms) — plausible (2.44 ms at 670 rpm / 26 kPa), unverified.

## Warm-ECT fueling (steady, closed loop, no A/C)
| condition | rpm | MAP kPa | inj ms | STFT % | LTFT % | total % | rel. airflow | seconds |
|---|---|---|---|---|---|---|---|---|
| base idle | 671 | 26.22 | 2.443 | 11.18 | 10.38 | 21.6 | 1.00 | ~2430 |
| 810 idle | 809 | 25.21 | 2.373 | 7.80 | 7.96 | 15.8 | 1.16 | ~3870 |
| rev hold | 1378 | 23.06 | 2.262 | 6.55 | 7.81 | 14.4 | 1.81 | ~5 |
| rev hold | 1967 | 23.96 | 2.346 | 1.80 | 9.56 | 11.4 | 2.68 | ~5 |

- Fit: total ≈ 5.3 + 16.3 / relative airflow → ~16 points behave like a fixed g/s error, ~5 points like a constant % error. Caveat: rev holds only ~5 s each — confirm with a longer hold.
- Pulse width ~constant 2.26–2.44 ms from 670 to 2000 rpm → an injector/pressure-type error would give the same % at both; instead trim falls ~10 points → argues against fuel delivery.
- MAP at idle 25.1–26.5 kPa, inside spec 24–31 kPa (PDF p.355), strong-vacuum end.
- IACV nearly closed at 670 rpm (2.35–2.75 %, decode unverified) → extra air entering (leak, fast-idle thermo valve, idle screw; PDF p.461–462). With speed-density an intake leak mostly shows as low IAC duty, so probably present but not the main trim cause.
- LTFT: re-learned +2.3 → +10.2 % 17:56–18:12 (recent reset); then trails STFT in 0.78 % steps; frozen during false-cold fast idle. P0171 is set from LTFT over two trips (PDF p.433–434) — borderline.
- Closed loop 99.5 % of samples.
- O2 switching ~0.11 Hz at idle, swing 0.15–0.88 V (spec 0.1–0.9 V, PDF p.355); ~1 s edges — slightly lazy or slow idle mixing, not proof of a bad sensor.
- A/C only ~10 s engaged — not enough data.

## Effect of the false-cold ECT
No measurable fuel enrichment at matched idle states; injector PW and STFT flat for 120 s across 9 warm→cold and 8 cold→warm steps. It does change idle (fast idle 1080 after ~15 min of false-cold), timing (+~6.5° on the Honda decode), and freezes LTFT learning. A low-reading ECT can only add fuel (pushing trims negative), and the OBD2-only drives already had ~+20 % trim with ECT reading normal — so the ECT fault is not the cause of the lean trim.

## Ranked hypotheses
1. Unmetered air/oxygen whose effect shrinks with flow (~16 of 21.6 points): best candidate an exhaust leak before the primary O2 (manifold gasket, sensor boss, cracked manifold); next an intake leak only partly hidden by the IAC (PDF p.461–462, p.384).
2. EVAP purge in the 810 state (−3–4 points there; PDF p.491).
3. Fuel delivery (pressure/filter/pump/injectors): ≤ ~5 constant points (PDF p.433–434).
4. Sensor bias (IAT high, MAP gain): a few points at most.
5. ECT fault: ruled out as the lean-trim cause — still fix it (idle, timing, LTFT freeze).

## Tests, in order
1. Fuel pressure (PDF p.500, '97+): 260–310 kPa (38–46 psi) regulator hose off and pinched; 200–250 kPa (28–36 psi) connected.
2. Smoke test the intake (manifold/throttle-body gaskets, PCV valve/grommet/hose, brake booster hose and check valve, fast-idle thermo valve, IAC gasket, purge lines, regulator hose).
3. Spray test at warm 670 rpm while logging STFT/RPM; pinch PCV hose and listen for the click (PDF p.535).
4. Exhaust leak before the O2 on a cold start (ticking at manifold joints/sensor boss; briefly block tailpipe and listen).
5. Purge check: block the purge line at warm idle; purge-valve vacuum-hold test (PDF p.434).
6. Long rev hold 2500–3000 rpm ≥60 s with ECT good: trim near 0–5 % → idle-only problem; trim still high → fuel delivery. O2 should switch at 3000 rpm (PDF p.434).
7. Injectors: stethoscope click and swap (PDF p.437–438); renew O-rings/seal rings (PDF p.501–502).
8. Fix the C122 connector and oil leak first, then confirm ECT 80–93 °C after the fan cycles (PDF p.397), ECM reset, let LTFT re-learn.
9. Base idle 670±50 rpm (750 headlights, 810 A/C or heater Hi) (PDF p.490–491); if IACV stays ~0 after leak fixes, check the idle screw and fast-idle thermo valve.

## Suggested map updates (not applied)
- o2 (0x15): verified against OBD2 PID 0x14.
- ign (0x26): suspect, ~+6° vs OBD2; likely (x−48)/4 pending a timing light.
