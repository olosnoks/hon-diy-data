# Black box files (format version 1)

Everything is little-endian and packed. The C definitions are in
`components/bbcore/include/bbcore.h`; the Python reader is
`hondalog/blackbox/eventfile.py`.

## On the SD card (FAT, 8.3 names)

| path | what |
|---|---|
| `BLACKBOX.CFG` | settings, text. Written with defaults on first boot |
| `EVENTS/EVnnnnnn.BBX` | one capture: 30 s before the trigger, 10 s after |
| `EVENTS/EVnnnnnn.CSV` | the same 1 kHz record as CSV, written by the box afterwards (`csv_export = 0` turns it off) |
| `SUMMARY/Sbbbbbbb.CSV` | one line a second for the whole of boot `bbbbbbb`, events as `#E,` lines |

## Event file

```
file header                84 bytes
block, block, block ...    each: 24-byte header, payload, CRC32
```

### File header

| offset | type | field |
|---|---|---|
| 0 | u32 | magic `0x58424248` ("HBBX") |
| 4 | u16 | version = 1 |
| 6 | u16 | header length = 84 |
| 8 | u32 | event number |
| 12 | u32 | boot number |
| 16 | u8 | reason code of the trigger |
| 17 | u8 | analog channels = 5 |
| 18 | u8 | pulse channels = 3 |
| 19 | u8 | flags: 1 = clock was set, 2 = clock came from GPS |
| 20 | u32 | sample rate = 1000 |
| 24 | u32 | pre-trigger length actually stored, ms |
| 28 | u32 | post-trigger length asked for, ms |
| 32 | u64 | trigger time, device µs |
| 40 | i64 | wall-clock µs (Unix) at device time 0; 0 = unknown |
| 48 | i32 | value that fired the trigger (mV, ms, ...) |
| 52 | char[12] | firmware version |
| 64 | char[12] | device id |
| 76 | u8, u8 | tach and ignition pulses per crank revolution |
| 78 | u16 | reserved |
| 80 | u32 | CRC32 (IEEE) of bytes 0..79 |

### Block header

| offset | type | field |
|---|---|---|
| 0 | u32 | magic `0x4B4C4242` ("BBLK") |
| 4 | u8 | type |
| 5 | u8 | flags (0) |
| 6 | u16 | record count |
| 8 | u32 | payload length in bytes |
| 12 | u32 | sequence number: order of writing |
| 16 | u64 | device µs of the first record |

Then the payload, then a CRC32 of header plus payload.

| type | payload |
|---|---|
| 1 SAMPLES | up to 256 sample records, 1 ms apart, starting at the header's time |
| 2 EDGES | up to 512 edge words |
| 3 EVENTS | event records |
| 4 CONFIG | the config text in force |
| 5 GPS | GPS fixes (28 bytes each: `u64 t_us, i32 lat_e7, i32 lon_e7, u32 utc, u16 speed_cm_s, u16 course_cdeg, u8 fix, u8 sats, u16 rsv`) |
| 0x7E INDEX | one 16-byte entry per data block: `u32 offset, u8 type, u8 flags, u16 count, u64 t0_us` |
| 0x7F END | `u32 index_offset, u32 n_blocks, u32 status (1 = complete), u32 n_samples` |

**Sample record, 28 bytes, one per millisecond**

| offset | type | field |
|---|---|---|
| 0 | u16[5] | mean of the millisecond in car-side mV: battery, ECU +B, pump feed, IG1, alternator |
| 10 | u8[4] | lowest 5 kHz reading in the millisecond for the four rails, 0.1 V steps, rounded down |
| 14 | u8[4] | highest, rounded up |
| 18 | u16 | RPM from the tach input |
| 20 | u16 | RPM from the ignition trigger input |
| 22 | u16 | last injector pulse width, µs |
| 24 | u8 | injector duty, 0.5 % steps |
| 25 | u8 | bits: 1 tach level, 2 ign level, 4 inj level, 8 button, 0x10 IG1 on, 0x20 engine running, 0x40 capturing, 0x80 record was filled in (ADC fell behind) |
| 26 | u16 | reserved |

**Edge word, 8 bytes:** bits 0–55 time in device µs, bits 56–57 channel
(0 tach, 1 ignition trigger, 2 injector), bit 60 new level (1 = active).

**Event record, 16 bytes:** `u64 t_us, u8 code, u8 source, u16 action, i32 value`.

### Order on disk, and why

1. EVENTS (everything so far, including the trigger) and CONFIG.
2. Pre-trigger SAMPLES **newest first**, with the EDGES that belong to each.
   The file is synced after the first four blocks (the last second before
   the trigger) and regularly after.
3. Post-trigger SAMPLES and EDGES as they happen, synced about once a second.
4. Late EVENTS, GPS, INDEX, END.

If power dies part-way, what is on the card is the stretch closest to the
stall. A reader must not expect time order: sort sample blocks by their
time. A file without END is incomplete but valid: walk the blocks from byte
84, keep those whose CRC is right, and on a bad one search forward for the
next block magic.

## Reason codes

| code | name | default rule |
|---|---|---|
| 1 | tach_stop | no tach edge for 250 ms, IG1 on, engine was running |
| 2 | ign_stop | no ignition trigger edge for 250 ms, IG1 on |
| 3 | ig1_drop | IG1 below 6 V for 10 ms, engine was running |
| 4 | ecub_drop | ECU +B below 6 V for 10 ms with IG1 on |
| 5 | pump_drop | pump feed below 6 V for 20 ms, IG1 on, running |
| 6 | vbat_dip | battery below 9 V for 20 ms, running |
| 7 | button | marker button held 30 ms |
| 8 | host | TRIGGER from the laptop (not a rule) |
| 9 | brownout | battery at the box below 6.5 V for 3 ms |
| 10 | inj_stop | no injector edge for 2 s while the tach keeps going (marks only) |
| 11 | ecub_glitch | ECU +B falling faster than 3 V per ms, IG1 on, running |
| 12–31 | | free for your own rules |
| 32 | host_mark | marker from the laptop; value = its number |
| 33 | time_sync | clock set (value 1 = from GPS) |
| 34 | running | engine seen running |
| 35, 36 | ig1_on, ig1_off | |
| 37 | overrun | ring data was overwritten before it was saved |

Codes 1–31 are triggers. 32 and up are plain events.

## BLACKBOX.CFG

`key = value` lines, `#` starts a comment. Unknown or out-of-range lines are
skipped and reported on the debug UART; the rest still applies.

| key | default | meaning |
|---|---|---|
| `pre_s`, `post_s` | 30, 10 | seconds kept before and after a trigger |
| `<rail>_scale` | 7912 | car mV = pin mV × scale / 1000 + offset. Rails: vbat ecub pump ig1 alt |
| `<rail>_offset_mv` | 0 | |
| `tach_invert`, `ign_invert`, `inj_invert` | 1 | 1: GPIO low = signal active (opto output) |
| `tach_ppr`, `ign_ppr` | 2 | pulses per crank revolution (4-cylinder: 2) |
| `edge_min_us` | 300 | shorter periods are treated as noise |
| `run_rpm`, `run_ms` | 400, 500 | "running" = above run_rpm for run_ms |
| `pulse_timeout_ms` | 300 | no edge for this long: RPM reads 0 |
| `ig1_on_mv`, `ig1_off_mv`, `ig1_debounce_ms` | 8000, 6000, 10 | IG1 state with hysteresis |
| `holdoff_s` | 5 | minimum gap between captures (button, laptop and brownout ignore it) |
| `host_mark_triggers` | 0 | 1: a marker from the laptop also saves a capture |
| `live_hz` | 50 | USB live frames per second |
| `summary_s` | 1 | seconds per summary line |
| `csv_export` | 1 | write `EVnnnnnn.CSV` after each capture |
| `sleep_after_s` | 300 | deep sleep this long after IG1 goes off; 0 = never |
| `gps_baud` | 0 | 9600 for a NEO-6M; 0 = no GPS |
| `alt_enable` | 0 | reserved |
| `edge_ring` | 32768 | edges kept in RAM |
| `rule.N` | see above | a trigger rule |

### Rules

```
rule.N = source op level hysteresis hold_ms qualifiers action
rule.4 = ecub < 6000 1000 10 ig1 capture
```

- **source**: `vbat ecub pump ig1 alt` (mV; `<` tests the lowest 5 kHz
  reading of the millisecond, `>` the highest), `slew_<rail>` (change of the
  mean in mV per ms, negative = falling), `tach_age ign_age inj_age` (ms
  since the last edge), `rpm_tach rpm_ign`, `inj_pw` (µs), `inj_duty`
  (0.5 %), `button`.
- **op**: `<` or `>`.
- **hysteresis**: the rule arms only once the source is on the good side of
  the level by this much (and the qualifiers hold). It fires once, then must
  arm again. That is what stops a rail that is already dead from firing.
- **hold_ms**: the condition must hold this many samples in a row (1–60000).
- **qualifiers**: `ig1`, `running`, `ign_running`, `tach_alive`, joined with
  `+`, or `-` for none.
- **action**: `capture` (save an event file), `mark` (record the event
  only), `off`. `rule.N = off` disables a rule.

`N` is 1–31 except 8 and becomes the reason code. Examples:

```
rule.6  = vbat < 10500 500 50 running capture        # stricter battery dip
rule.12 = alt < 12800 300 2000 running mark          # not charging for 2 s
rule.13 = inj_duty > 170 10 200 running capture      # injector above 85 % duty
rule.14 = slew_ig1 < -3000 2000 1 running capture    # IG1 glitch
```
