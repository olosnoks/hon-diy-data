# Poke test, 2026-11-26 22:26:14

- Port `sim:` (built-in simulator); map `p2p-guess-6 (2026-11-26 findings: ign offset, idle target, OBD2 copies)`; gearbox `manual`
- 1704 sweeps in 212.95 s (full-sweep period 1.0 s); reads ok 5017, failed 10 {'NoReply': 10}; ECU-silent spells 1
- Times scaled by 0.5 (test run)
- A row is listed when, in the hold windows against the rest windows around the step, p < 0.001 and |d| >= 1.0 (byte; d = difference of means / pooled SD) or the share of sweeps with the bit set changed by >= 50% (bit); or on an excursion: at least 2 hold sweeps (and 20% of them, or p < 0.001) outside anything seen at rest. **indirect** = the address also follows rpm or ECT outside this step (|Spearman r| >= 0.6) and rpm or ECT moved in this step. **edge** rows: bits in a state never seen at rest during the switching moments (prompt, settle, undo), not in the hold.

## Steps

| step | engine | reps | hold sweeps | rest sweeps | rpm rest -> hold | ECT raw rest -> hold | direct | indirect | edge |
|---|---|---|---|---|---|---|---|---|---|
| ac | running | 1 | 10 | 185 | 889 -> 1412 | 66 -> 98 | 7 | 8 | 0 |
| elec_load | running | 5 | 436 | 1045 | 768 -> 758 | 45 -> 44 | 0 | 0 | 0 |
| brake | running | 2 | 10 | 195 | 750 -> 746 | 36 -> 36 | 1 | 1 | 0 |
| keyon | **OFF** | 1 | 15 | 24 | 751 -> 0 | 34 -> 34 | 36 | 13 | 0 |
| engine_stop | **OFF** | 1 | 7 | 19 | 751 -> 0 | 34 -> 34 | 27 | 11 | 0 |

## Flag bytes 0x08-0x0F and 0xA4, bit by bit

Share of sweeps with the bit set, rest -> hold (percent). `0` / `1`: that value in every rest and hold sweep. **Bold**: listed as a response. `e`: an edge response. `-`: not read in that step.

| bit | known as | ac | elec_load | brake | keyon | engine_stop |
|---|---|---|---|---|---|---|
| 0x08 b0 | starter | 0 | 0 | 0 | 0 | 0 |
| 0x08 b1 | ac_sw | **0->100** | 0 | 0 | 0 | 0 |
| 0x08 b2 | psp_sw? | 1 | 1 | 1 | 1 | 1 |
| 0x08 b3 | brake | 0 | 0 | **0->100** | 0 | 0 |
| 0x08 b4 |  | 0 | 0 | 0 | 0 | 0 |
| 0x08 b5 |  | 0 | 0 | 0 | 0 | 0 |
| 0x08 b6 |  | 0 | 0 | 0 | 0 | 0 |
| 0x08 b7 | gear input? | 1 | 1 | 1 | 1 | 1 |
| 0x09 b0 |  | 0 | 0 | 0 | 0 | 0 |
| 0x09 b1 |  | 0 | 0 | 0 | 0 | 0 |
| 0x09 b2 |  | 0 | 0 | 0 | 0 | 0 |
| 0x09 b3 | scs? | 0 | 0 | 0 | 0 | 0 |
| 0x09 b4 |  | 1 | 1 | 1 | 1 | 1 |
| 0x09 b5 |  | 1 | 1 | 1 | 1 | 1 |
| 0x09 b6 |  | 0 | 0 | 0 | 0 | 0 |
| 0x09 b7 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0A b0 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0A b1 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0A b2 | vtec | 0 | 0 | 0 | 0 | 0 |
| 0x0A b3 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0A b4 | gear input? | 1 | 1 | 1 | 1 | 1 |
| 0x0A b5 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0A b6 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0A b7 | ecu_ready? | 1 | 1 | 1 | **100->0** | **100->0** |
| 0x0B b0 | fuel_pump | 1 | 1 | 1 | **100->20** | **100->14** |
| 0x0B b1 | ac_clutch | **0->100** | 0 | 0 | 0 | 0 |
| 0x0B b2 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0B b3 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0B b4 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0B b5 | mil | 0 | 0 | 0 | **0->100** | **0->100** |
| 0x0B b6 | engine_turning? | 1 | 1 | 1 | **100->0** | **100->0** |
| 0x0B b7 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0C b0 | elec load / alt ctrl? | 0 | 0 | 0 | 0 | 0 |
| 0x0C b1 | fan | 0 | 0 | 0 | 0 | 0 |
| 0x0C b2 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0C b3 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0C b4 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0C b5 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0C b6 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0C b7 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0D b0 |  | 1 | 1 | 1 | 1 | 1 |
| 0x0D b1 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0D b2 |  | 1 | 1 | 1 | 1 | 1 |
| 0x0D b3 |  | 1 | 1 | 1 | 1 | 1 |
| 0x0D b4 |  | 1 | 1 | 1 | 1 | 1 |
| 0x0D b5 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0D b6 |  | 1 | 1 | 1 | 1 | 1 |
| 0x0D b7 |  | 1 | 1 | 1 | 1 | 1 |
| 0x0E b0 |  | 1 | 1 | 1 | 1 | 1 |
| 0x0E b1 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0E b2 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0E b3 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0E b4 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0E b5 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0E b6 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0E b7 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0F b0 | closed_loop | **84->0** | 99->100 | 1 | **100->0** | **100->0** |
| 0x0F b1 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0F b2 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0F b3 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0F b4 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0F b5 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0F b6 |  | 0 | 0 | 0 | 0 | 0 |
| 0x0F b7 |  | 0 | 0 | 0 | 0 | 0 |
| 0xA4 b0 | idle state? | 0 | - | 0 | 0 | 0 |
| 0xA4 b1 |  | 1 | - | 1 | 1 | 1 |
| 0xA4 b2 |  | 1 | - | 1 | 1 | 1 |
| 0xA4 b3 |  | 1 | - | 1 | 1 | 1 |
| 0xA4 b4 |  | 1 | - | 1 | 1 | 1 |
| 0xA4 b5 |  | 0 | - | 0 | 0 | 0 |
| 0xA4 b6 |  | 0 | - | 0 | 0 | 0 |
| 0xA4 b7 |  | 1 | - | 1 | 1 | 1 |

## ac

> Turn the A/C ON (A/C button lit; switch the blower on if it is off).

| # | addr | known as | change | rest | during | effect | p | note |
|---|---|---|---|---|---|---|---|---|
| 1 | 0x08 b1 | ac_sw | shift | 0% set | 100% set | +100% | <1e-12 |  |
| 2 | 0x0B b1 | ac_clutch | shift | 0% set | 100% set | +100% | <1e-12 |  |
| 3 | 0x12 b2 | ? | shift | 3% set | 100% set | +97% | <1e-12 |  |
| 4 | 0x0F b0 | closed_loop | shift | 84% set | 0% set | -84% | 5e-08 | rest drift: set 0% before, 92% after |
| 5 | 0x12 b1 | ? | shift | 97% set | 20% set | -77% | 3e-09 | rest drift: set 44% before, 100% after |
| 6 | 0x15 b5 | ? | shift | 47% set | 100% set | +53% | 7e-04 | rest drift: set 100% before, 42% after |
| 7 | 0x12 | map | shift | 50.4 (50-57) | 53.0 (52-54) | d=+2.6 | 2e-10 | rest drift: mean 55.7 before, 50.1 after |
| 8 | 0x00 | rpm [0] | shift | 13.4 (12-22) | 21.6 (20-23) | d=+5.8 | 5e-09 | indirect: is rpm; ect r=+0.69; rest drift: mean 20.6 before, 12.9 after |
| 9 | 0x00 b4 | ? | shift | 5% set | 100% set | +95% | 5e-12 | indirect: is rpm; rest drift: set 100% before, 0% after |
| 10 | 0x00 b3 | ? | shift | 95% set | 0% set | -95% | 5e-12 | indirect: is rpm; rest drift: set 0% before, 100% after |
| 11 | 0x10 b6 | ? | shift | 33% set | 100% set | +67% | 3e-05 | indirect: is ect |
| 12 | 0x10 | ect | shift | 66.4 (57-150) | 97.9 (87-109) | d=+2.5 | 1e-06 | indirect: is ect; rest drift: mean 135.3 before, 62.5 after |
| 13 | 0x26 | ign | excursion | 99.3 (92-106) | 105.7 (104-108) | 3/10 outside 92-106 | 0.002 | indirect: rpm r=+0.90 |
| 14 | 0x27 | ign_limit | excursion | 99.3 (92-106) | 105.7 (104-108) | 3/10 outside 92-106 | 0.002 | indirect: rpm r=+0.90 |
| 15 | 0x90 | ? | excursion | 17.9 (14-22) | 21.7 (21-23) | 2/10 outside 14-22 | 0.001 | indirect: rpm r=+0.90 |

## elec_load

> ALL AT ONCE: HIGH BEAMS on + REAR DEFOGGER on + BLOWER max. Press Enter the moment the last one is on.

Nothing responded in the hold.

## brake

> Press the BRAKE PEDAL and HOLD it down.

| # | addr | known as | change | rest | during | effect | p | note |
|---|---|---|---|---|---|---|---|---|
| 1 | 0x08 b3 | brake | shift | 0% set | 100% set | +100% | <1e-12 |  |
| 2 | 0x10 | ect | shift | 36.2 (34-37) | 35.5 (35-36) | d=-1.1 | 6e-04 | indirect: is ect; rest drift: mean 36.4 before, 34.0 after |

## keyon (ENGINE OFF)

> Turn the key to ON (II) WITHOUT starting. Press Enter the instant it is on.

Engine-off step: everything that needs the engine running moves too; the direct rows and the timeline are the interesting part.

| # | addr | known as | change | rest | during | effect | p | note |
|---|---|---|---|---|---|---|---|---|
| 1 | 0x0A b7 | ecu_ready? | shift | 100% set | 0% set | -100% | 4e-11 |  |
| 2 | 0x0B b6 | engine_turning? | shift | 100% set | 0% set | -100% | 4e-11 |  |
| 3 | 0x0F b0 | closed_loop | shift | 100% set | 0% set | -100% | 4e-11 |  |
| 4 | 0x0B b5 | mil | shift | 0% set | 100% set | +100% | 4e-11 |  |
| 5 | 0x12 b0 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 6 | 0x12 b5 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 7 | 0x17 b2 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 8 | 0x17 b4 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 9 | 0x24 b1 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 10 | 0x25 b3 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 11 | 0x25 b4 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 12 | 0x25 b6 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 13 | 0x26 b6 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 14 | 0x27 b6 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 15 | 0x28 b2 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 16 | 0x28 b3 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 17 | 0x28 b6 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 18 | 0x90 b0 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 19 | 0x90 b1 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 20 | 0x90 b3 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 21 | 0x91 b7 | ? | shift | 100% set | 0% set | -100% | 1e-10 |  |
| 22 | 0x12 b2 | ? | shift | 0% set | 100% set | +100% | 1e-10 |  |
| 23 | 0x12 b7 | ? | shift | 0% set | 100% set | +100% | 1e-10 |  |
| 24 | 0x12 | map | shift | 49 | 148 | d=+198.0 | 1e-09 |  |
| 25 | 0x25 | inj [1] | shift | 88 | 0 | d=-176.0 | 1e-09 |  |
| | | | ... 24 more (findings.json) | | | | | |

Flag-bit timeline (seconds from the start of the hold):

```
rep 1   -4.8 s: ECU silent until +0.4 s
rep 1   +2.9 s (hold): 0x0B b0 1->0
rep 1  +15.0 s (undo_settle): 0x0A b7 0->1
rep 1  +15.0 s (undo_settle): 0x0B b0 0->1, b5 1->0, b6 0->1
rep 1  +15.0 s (undo_settle): 0x0F b0 0->1
```

## engine_stop (ENGINE OFF)

> ENGINE STOP, KEY STAYS ON: parking brake ON, foot on the brake, 1st gear, let the clutch out slowly until the engine stalls. LEAVE THE KEY ON. Press Enter once it has stalled.

Engine-off step: everything that needs the engine running moves too; the direct rows and the timeline are the interesting part.

| # | addr | known as | change | rest | during | effect | p | note |
|---|---|---|---|---|---|---|---|---|
| 1 | 0x12 | map | shift | 49 | 148 | d=+198.0 | 7e-07 |  |
| 2 | 0x25 | inj [1] | shift | 88 | 0 | d=-176.0 | 7e-07 |  |
| 3 | 0x0B | b0=fuel_pump, b1=ac_clutch, b5=mil, b6=engine_turning? | shift | 65 | 32.1 (32-33) | d=-65.7 | 8e-07 |  |
| 4 | 0x0A b7 | ecu_ready? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 5 | 0x0B b5 | mil | shift | 0% set | 100% set | +100% | 2e-06 |  |
| 6 | 0x0B b6 | engine_turning? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 7 | 0x0F b0 | closed_loop | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 8 | 0x12 b0 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 9 | 0x12 b2 | ? | shift | 0% set | 100% set | +100% | 2e-06 |  |
| 10 | 0x12 b5 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 11 | 0x12 b7 | ? | shift | 0% set | 100% set | +100% | 2e-06 |  |
| 12 | 0x17 b2 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 13 | 0x17 b4 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 14 | 0x25 b3 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 15 | 0x25 b4 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 16 | 0x25 b6 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 17 | 0x28 b2 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 18 | 0x28 b3 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 19 | 0x28 b6 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 20 | 0x90 b0 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 21 | 0x90 b1 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 22 | 0x90 b3 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 23 | 0x91 b7 | ? | shift | 100% set | 0% set | -100% | 2e-06 |  |
| 24 | 0x17 | batt | shift | 148.3 (148-149) | 129 | d=-38.6 | 3e-05 |  |
| 25 | 0x91 | ? | shift | 176.3 (139-227) | 0 | d=-8.4 | 1e-04 |  |
| | | | ... 13 more (findings.json) | | | | | |

Flag-bit timeline (seconds from the start of the hold):

```
rep 1   +1.2 s (hold): 0x0B b0 1->0
rep 1   +8.3 s (undo_settle): 0x0A b7 0->1
rep 1   +8.3 s (undo_settle): 0x0B b0 0->1, b5 1->0, b6 0->1
rep 1   +8.3 s (undo_settle): 0x0F b0 0->1
```

## Per address

Which steps moved each address. **Bold**: a direct response; `(i)` indirect; `(e)` at the switching moments only.

| addr | known as | steps |
|---|---|---|
| 0x00 | rpm [0] | ac: byte (i), b4 (i), b3 (i); keyon: b0 (i), b1 (i), b3 (i), byte (i); engine_stop: byte (i), b0 (i), b1 (i), b3 (i) |
| 0x01 | rpm [1] | keyon: b7 (i), byte (i), b0 (i), b2 (i), b4 (i), b6 (i), b1 (i); engine_stop: b7 (i), byte (i) |
| 0x08 | b0=starter, b1=ac_sw, b2=psp_sw?, b3=brake, b7=gear input? | **ac: b1**; **brake: b3** |
| 0x0A | b2=vtec, b4=gear input?, b7=ecu_ready? | **keyon: b7**; **engine_stop: b7** |
| 0x0B | b0=fuel_pump, b1=ac_clutch, b5=mil, b6=engine_turning? | **ac: b1**; **keyon: b6, b5, byte, b0**; **engine_stop: byte, b5, b6, b0** |
| 0x0F | b0=closed_loop | **ac: b0**; **keyon: b0**; **engine_stop: b0** |
| 0x10 | ect | ac: b6 (i), byte (i); brake: byte (i) |
| 0x12 | map | **ac: b2, b1, byte**; **keyon: b0, b5, b2, b7, byte**; **engine_stop: byte, b0, b2, b5, b7** |
| 0x15 | o2 | **ac: b5**; **keyon: b3, byte**; **engine_stop: byte** |
| 0x17 | batt | **keyon: b2, b4, byte, b0**; **engine_stop: b2, b4, byte** |
| 0x20 | stft | **keyon: b0** |
| 0x24 | inj [0] | **keyon: b1**; engine_stop: b1 (i) |
| 0x25 | inj [1] | **keyon: b3, b4, b6, byte**; **engine_stop: byte, b3, b4, b6** |
| 0x26 | ign | ac: byte (i); **keyon: b6**; engine_stop: b6 (i) |
| 0x27 | ign_limit | ac: byte (i); **keyon: b6**; engine_stop: b6 (i) |
| 0x28 | iacv | **keyon: b2, b3, b6, byte (i)**; **engine_stop: b2, b3, b6, byte (i)** |
| 0x90 | ? | ac: byte (i); **keyon: b0, b1, b3, byte (i)**; **engine_stop: b0, b1, b3, byte (i)** |
| 0x91 | ? | **keyon: b7, byte, b2, b3, b1**; **engine_stop: b7, byte** |

## Unnamed addresses that responded directly

- 0x12 b0: engine_stop
- 0x12 b0: keyon
- 0x12 b1: ac
- 0x12 b2: ac
- 0x12 b2: engine_stop
- 0x12 b2: keyon
- 0x12 b5: engine_stop
- 0x12 b5: keyon
- 0x12 b7: engine_stop
- 0x12 b7: keyon
- 0x15 b3: keyon
- 0x15 b5: ac
- 0x17 b0: keyon
- 0x17 b2: engine_stop
- 0x17 b2: keyon
- 0x17 b4: engine_stop
- 0x17 b4: keyon
- 0x20 b0: keyon
- 0x24 b1: keyon
- 0x25 b3: engine_stop
- 0x25 b3: keyon
- 0x25 b4: engine_stop
- 0x25 b4: keyon
- 0x25 b6: engine_stop
- 0x25 b6: keyon
- 0x26 b6: keyon
- 0x27 b6: keyon
- 0x28 b2: engine_stop
- 0x28 b2: keyon
- 0x28 b3: engine_stop
- 0x28 b3: keyon
- 0x28 b6: engine_stop
- 0x28 b6: keyon
- 0x90 b0: engine_stop
- 0x90 b0: keyon
- 0x90 b1: engine_stop
- 0x90 b1: keyon
- 0x90 b3: engine_stop
- 0x90 b3: keyon
- 0x91: engine_stop
- 0x91: keyon
- 0x91 b1: keyon
- 0x91 b2: keyon
- 0x91 b3: keyon
- 0x91 b7: engine_stop
- 0x91 b7: keyon
