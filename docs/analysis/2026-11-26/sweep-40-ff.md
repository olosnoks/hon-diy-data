# 2026-11-26: first read of 0x40-0xFF with the engine running

One read-only session on the car (2000 Civic EX, D16Y8, stock P2P). Engine running at
idle the whole time, vss 0. Nobody touched the throttle (TPS 20-21 raw).

- Cable taken 21:49:30.7, given back 21:53:02.6: **211.9 s off the app** (one session,
  app closed in 1 s, restarted itself as `drive_2026_11_26_2153`).
- 210 sweeps of the whole table 0x00-0xFF in sixteen 16-byte rows, one sweep a second
  (a sweep takes 0.58 s at 10400 baud), 21:49:32.4 to 21:53:02.6.
- Sent: the map's wake frame once, then only `20 05 addr 10 cksum`, through hondalog's
  own `Link` with the read-only guard on. No retries were needed.
- Raw data: `work-sweep/car-214932-reads.csv` (every request, wall clock + monotonic),
  `car-214932-wide.csv` (one line per sweep, b00..bFF), `car-214932-meta.json` (framing,
  link stats), `car-214932-bytes.csv` + `car-214932-summary.txt` (per-address statistics).
  Scripts: `work-sweep/sweep.py` (tested on `sim:` first), `analyse.py`, `take-cable.ps1`.

Conditions during the sweep: rpm 800-1074 (mean 989), MAP raw 38-42, STFT raw 128-143,
LTFT 139 constant, O2 raw 6-46 switching, IACV 16-27, 0x29 89-100. The ECT false-cold
fault was active: the raw ECT byte jumped between ~51-73 (~57-70 °C) and ~120-183
(~11-36 °C) several times, which moved the idle up and down. That turned out to be useful
(see 0x76).

## What replied

**Every row answered, every time**: 3,360 of 3,360 reads OK, no timeouts, no bad
checksums, no row needed the fallback byte-by-byte probe. The whole 0x00-0xFF table is
readable in 16-byte blocks.

Of 0x40-0xFF (192 bytes), **164 were 0 in every sweep** and 28 were not:
0x40-0x75 are all 0 (so is 0x7D, and everything from 0xA5 to 0xFF except 0xAD).

## Every non-zero byte in 0x40-0xFF

Raw values are decimal. "Best correlate" is the Spearman rank correlation with the known
channels read in the same sweep, and lags were checked from -4 to +4 sweeps.

| addr | behaviour (210 sweeps) | best correlate | best guess | confidence |
|---|---|---|---|---|
| **0x76:0x77** | 16-bit big-endian, changes in 3-9 % of sweeps, in steps of ~54 raw (13.6 rpm at x/4); /4 = 824-1068 | rpm, r = 0.95 with **0x76:77 2-3 s ahead of rpm** | **target idle speed, rpm = x/4.** It moves first and rpm follows: at 21:50:05 it went 932 -> 1014 and rpm reached 1012 seven s later; at 21:52:54 it went 973 -> 864 -> 837 -> 824 and rpm fell 974 -> 952 -> 914 -> 887 -> 824 behind it | medium-high |
| 0x78-0x7F | constant 16 153 10 4 36 0 3 1 (0x10 0x99 0x0A 0x04 0x24 0x00 0x03 0x01) | none | the fixed part of what the app logs as `ecu_id` (see below); ECU/ROM identity or calibration constants | high that they are constant |
| 0x80, 0x82, 0x84, 0x87, 0x88, 0x89, 0x8B, 0x8C | constant 248, 249, 240, 248, 252, 240, 244, 248 (signed: -8, -7, -16, -8, -4, -16, -12, -8); 0x81, 0x83, 0x85, 0x8A, 0x8D, 0x8E are 0 | none | a table of small negative signed values: learned corrections per cell or per cylinder (knock or idle learn?) | low |
| 0x86 | 253 (-3) most of the time, 249 (-7) in 16 sweeps (bit 2 flips) | STFT, r = -0.42 with 0x86 one sweep later; O2 r = -0.27 | belongs with 0x80-0x8C; drops to -7 when STFT is high (134-143) and O2 is rich | low |
| 0x8F | constant 142 | none | unknown | - |
| 0x94, 0x95, 0x96 | constant 2, 15, 4 | none | unknown | - |
| 0x99 | constant 5 | none | unknown (OBD2 block neighbour) | - |
| **0x9A** | constant 2 | none (no change to test) | **OBD2 fuel system status (PID 03): 2 = closed loop**, which matches the switching O2 and moving STFT | medium |
| **0x9C** | 53-62, only odd steps (53 55 57 59 60 62), changes in 55 % of sweeps | MAP, r = 0.48 at lag 0; mean rises 56.4 -> 60.7 as MAP raw goes 38 -> 42; slightly negative with rpm | **OBD2 calculated load (PID 04), % = x / 2.55** -> 21-24 % at idle, a normal idle load | medium |
| **0xA0** | 47-50, changes in 35 % of sweeps | O2 (0x15), r = 0.72, in step with it (no lag at 1 Hz) | **second (downstream) O2 sensor, V = x / 51.3** -> 0.92-0.97 V. Fits the literature slot and the 30-50 range. But it follows the front sensor within the same second; a healthy cat would damp and delay it. Either the cat stores little oxygen, or this is a derived front-O2 value | low-medium |
| 0xA4 | 160 / 161 (bit 0), 161 from ~21:50:54 to ~21:51:37 | rpm r = 0.62 | a status bit that was on while the idle sat at 1020-1074 rpm; not a simple rpm threshold | low |
| 0xAD | constant 1 | none | unknown flag | - |

### Checks the literature review asked for

- **0x61-0x6E freeze frame**: all 0. Consistent with "no freeze frame stored" (no DTC
  set), but a zero block cannot prove the slot. Read it again when a DTC is stored or the
  MIL is on; if it fills with copies of 0x10-0x15 / 0x20-0x26, that settles it.
- **0x98-0xA0 OBD2 copies**: 0x9A = 2 (closed loop) and 0x9C (load, tracks MAP) fit.
  0xA0 fits the second-O2 slot in range and position, not yet in behaviour (see the table).
  0x98, 0x9B, 0x9D-0x9F, 0xA1-0xA3 are 0.
- **Stored DTC**: nothing that looks like a DTC code or a DTC count; 0x40-0x75 and
  0xA5-0xFF are empty. 0x98 = 0 might be a "number of stored codes" slot, unproven.
- **Stall / misfire counter**: no byte counted up in 210 s; nothing in 0x40-0xFF moves
  like a counter or timer. (No stall happened during the sweep.)
- **16-bit pairs**: only 0x76:0x77 (low byte wraps 5 times, high byte steps the opposite
  way every time) besides rpm at 0x00:0x01.

### `ecu_id` is not an ID

The app reads `ecu_id` from 0x76 + 10 bytes (`addressmap.py` default `ecu_id_addr = 0x76`).
The first two of those bytes are the target-idle word, so every drive header shows a
different "ECU id":

| drive | ecu_id | 0x76:77 / 4 |
|---|---|---|
| drive_2026_11_26_2045 | `0B 2B 10 99 0A 04 24 00 03 01` | 715 rpm |
| drive_2026_11_26_2114 | `0B E9 10 99 ...` | 762 rpm |
| drive_2026_11_26_2137 | `0A 78 10 99 ...` | **670 rpm** (the factory idle spec, 670 ± 50) |
| drive_2026_11_26_2153 | `0C DE 10 99 ...` | 824 rpm (= the value at the end of this sweep) |

So the headers are a free record of the idle target at each connect, and the real constant
part is 0x78-0x7F. The `ecu_id` read should start at 0x78 (8 bytes) if it is meant to
identify the ECU.

## Candidate new channels

| name | addr | length | formula | units | why |
|---|---|---|---|---|---|
| `idle_target` | 0x76 | 2 | `x / 4` | rpm | leads rpm by 2-3 s with r = 0.95, moves in fixed steps, read 670 at a warm connect. The most useful byte for the stall: when the engine dies, it shows whether the ECU was asking for a low idle or the engine fell below what was asked |
| `load` | 0x9C | 1 | `x / 2.55` | % | rises with MAP, 21-24 % at idle like the OBD2 calculated load |
| `o2_2` (suspect) | 0xA0 | 1 | `x / 51.3` | V | the second O2 slot; mark unverified until it is checked on a decel (both sensors should go to ~0 V on fuel cut, the rear one later) |
| `fuel_status` | 0x9A | 1 | `x` (2 = closed loop) | - | OBD2 PID 03 meaning; worth watching at cold start and on a stall (open loop = 1 or 4) |
| `learn_86` (watch only) | 0x86 | 1 | `signed(x, 8)` | raw | the one byte of the 0x80-0x8C table that moved; cheap to log at 1 Hz with 0x80-0x8F as one block |

These all fit in two slow-rate reads (0x76+2 and 0x9A+7, or 0x80+16 and 0x96+11 to take
the neighbours too).

## Not answered by this run

- Behaviour off idle: no throttle, no driving, no cold start. 0x40-0x75 and 0xA5-0xFF may
  fill under other conditions; one sweep during a drive and one at a cold start would tell.
- 0xA0's identity: needs a decel or a snap-throttle, or the rear sensor unplugged.
- A ROM dump would name 0x80-0x96 at once.
