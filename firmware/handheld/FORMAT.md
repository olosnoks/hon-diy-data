# Handheld files (format version 1)

Everything is little-endian and packed. The C definitions are in
`components/hhcore/include/hhcore.h`; the Python side is
`hondalog/handheld/` (`format.py` has the same layouts, `drivefile.py`
reads drives, `mapfile.py` writes and reads the map).

## On the SD card (FAT, 8.3 names)

| path | what | written by |
|---|---|---|
| `HANDHELD.MAP` | the address map, compiled | `hondalog-handheld export-map` |
| `HANDHELD.CFG` | settings, text | `export-map` (once, with the defaults), you |
| `DRIVES/Dnnnnnn.HHD` | one drive | the handheld |
| `CAPTURES/Cnnnnnn.HHD` | one stall capture: 30 s before, 10 s after | the handheld |

A drive starts when the ECU answers and ends when it has been silent for
`drive_gap_s` (120 s), or when the power goes. Without a `HANDHELD.MAP` the
handheld runs on the map built into the firmware (the repository's default
map) and says so in each file header.

## Drive and capture files

```
file header                128 bytes
block, block, block ...    each: 24-byte header, payload, CRC32
```

### What makes it safe at key-off

The board cannot see the power going, so the file is made safe by how it is
written:

1. **Append only.** Nothing already written is changed: no index, no
   header rewritten at the end. A file is valid at every block boundary.
2. **Short blocks.** A block is closed after `block_ms` (250 ms) or 480
   payload bytes, whichever comes first, and each has its own CRC. A torn
   write costs that block.
3. **Synced every `sync_ms`** (1 s), and at once after a marker, an alarm
   or a stall. That second is the most a power cut takes.
4. **Space taken in advance.** A drive file is extended to `prealloc_mb`
   (16 MB, about three hours) when it is created, so that logging writes
   data sectors only and the FAT is not touched again until the drive ends.
   The space holds whatever the card had there. Two things keep that out of
   a reading: every block CRC is seeded with the file's own random `nonce`
   (an old file's blocks do not check out), and block numbers only go up.
   At a clean end the file is cut to its real length. Past the
   preallocated size the file simply grows.
5. **No END is normal.** A file without an END block was cut by power: it
   is complete up to its last good block.

### File header

| offset | type | field |
|---|---|---|
| 0 | u32 | magic `0x46444848` ("HHDF") |
| 4 | u16 | version = 1 |
| 6 | u16 | header length = 128 |
| 8 | u32 | file number (the `nnnnnn` of the name) |
| 12 | u32 | boot number |
| 16 | u8 | kind: 1 drive, 2 capture |
| 17 | u8 | flags: 1 = clock was set, 2 = clock came from GPS, 4 = space preallocated, 8 = built-in map |
| 18 | u8 | K-line backend: 0 USB host + KKL cable, 1 UART + L9637D |
| 19 | u8 | link mode: 0 Honda reads, 1 ISO 9141-2 only |
| 20 | u32 | nonce, mixed into every block CRC |
| 24 | u64 | `t0`: device µs of file second 0 |
| 32 | i64 | wall-clock µs (Unix, UTC) at `t0`; 0 = not known yet (see TIME) |
| 40 | char[12] | `address_map_hash` of the map in use |
| 52 | u8[8] | first 8 bytes of the SHA-256 of that map's TOML text |
| 60 | char[12] | firmware version |
| 72 | char[12] | device id |
| 84 | u8[10], u8, u8 | ECU id bytes, their count, reserved |
| 96 | u32 | capture: number of the drive it was cut from (0 = none was open) |
| 100 | u8, u8[3] | capture: reason (1 rpm, 2 link, 3 alarm), reserved |
| 104 | u64 | capture: trigger time, device µs |
| 112 | u32 | bytes preallocated |
| 116 | u8[8] | reserved |
| 124 | u32 | CRC32 (IEEE) of bytes 0..123 |

### Block

| offset | type | field |
|---|---|---|
| 0 | u32 | magic `0x4B424848` ("HHBK") |
| 4 | u8 | type |
| 5 | u8 | flags (0) |
| 6 | u16 | record count |
| 8 | u32 | payload length in bytes |
| 12 | u32 | sequence number, from 0, +1 per block |
| 16 | u64 | `t0`: device µs the records' times count from |

Then the payload, then `CRC32(nonce as 4 bytes, header, payload)`.

| type | payload |
|---|---|
| 1 READS | read records |
| 2 GPS | GPS fixes |
| 3 EVENTS | event records |
| 4 CONFIG | the settings in force, as `HANDHELD.CFG` text |
| 5 ISO | OBD2 (ISO 9141-2) reply records |
| 6 NAMES | text, one line per alarm: `A <index> <name>` |
| 7 CAPTURE | capture records |
| 8 TIME | time records |
| 0x7F END | `u32 blocks, u32 reads, u32 status (1), u32 reason (1 link lost, 2 stopped, 3 sleep), u64 end time µs` |

**Read record**, 8 bytes + data: one K-line read, exactly as the ECU
answered it.

| offset | type | field |
|---|---|---|
| 0 | u32 | µs after the block's `t0`: when the ECU sampled it (reply complete minus `latency`) |
| 4 | u8 | address |
| 5 | u8 | length `n` |
| 6 | u16 | latency, µs: estimated age of the data when the reply was complete |
| 8 | u8[n] | the data bytes |

Values are not stored: the importer decodes the bytes with the map named in
the header, and `hondalog-log redecode` can do it again with a corrected map.

**ISO record**, 8 bytes + data: `u32 dt_us, u8 mode (0x41), u8 pid, u8 n, u8 flags, u8[n] data`.

**GPS fix**, 28 bytes, the black box's record: `u64 t_us, i32 lat_e7,
i32 lon_e7, u32 utc (Unix s, 0 = no date), u16 speed cm/s, u16 course
0.01°, u8 fix, u8 sats, u16 HDOP × 100 (0 = unknown)`. One per RMC sentence
(1 Hz), fix or no fix.

**Event record**, 16 bytes: `u64 t_us, u8 code, u8 src, u16 aux, 4 bytes value`.

| code | name | src | aux | value |
|---|---|---|---|---|
| 1 | marker | | | i32 marker number since boot |
| 2 | link up | backend | link mode | |
| 3 | link down | | | |
| 4, 5, 6 | alarm raised, cleared, acknowledged | alarm index | 1 warn, 2 critical | f32 the channel's value |
| 7 | stall | 1 rpm, 2 link, 3 alarm | | f32 last rpm before it |
| 8, 9 | cable lost, cable back | | | |
| 10 | ISO session | key byte 1 | key byte 2 | u32 PID 00 bitmap |
| 11 | trouble code | index | how many | i32 raw two bytes (`0x0301` = P0301) |
| 12 | log blocks dropped | | | i32 how many (the card fell behind) |
| 13 | drive start | backend | link mode | i32 drive number |
| 14 | no trouble codes stored | | | |

**Capture record**, 32 bytes: `u64 trigger, u64 window start, u64 window
end (device µs), u8 reason, u8 complete, u16 reserved, u32 firings in the
window`. Written when the post-trigger time is over. The data of a capture
is the drive's own data between start and end; a stall whose record is
missing (power went first) is still marked by its event.

**Time record**, 24 bytes: `u64 t_us, i64 Unix µs at that moment, u8 source
(1 GPS, 2 the clock as it was, 3 set by the laptop over the serial link,
SERIAL.md), 7 reserved`. The handheld has no battery
clock: a drive that starts before the first GPS fix has 0 in its header and
gets its wall time from the first TIME record. Readers use the newest GPS
one: `epoch = unix - (t - t0)`.

### Capture files

Same layout, kind 2. Written at the moment a stall rule fires (RPM above
400, then below 50; or no reply for 1 s after the engine was seen running):
the READS of the last 30 s from the ring buffer in PSRAM, oldest first,
synced at once; then everything the drive gets until 10 s after the
trigger. It is a second copy for the case that the drive file is lost, and
a small file to hand to someone. The importer skips it when the drive it
names is there.

### Reading

Walk the blocks from byte 128. Accept a block if its magic, length and CRC
(with the header's nonce) are right and its sequence number is higher than
the last accepted one. Otherwise search forward for the next block magic.
Stop at END or at the end of the file. A gap in the sequence numbers means
blocks never reached the card (event 12 says how many). Blocks of
different types are not interleaved in time order: sort by record time.

## HANDHELD.MAP

```
header      64 bytes
sections    type u8, reserved u8, count u16, length u32, body   (the "core")
TOML text   the map's source, then its CRC32                     (not read by the handheld)
```

| offset | type | field |
|---|---|---|
| 0 | u32 | magic `0x504D4848` ("HHMP") |
| 4 | u16 | schema = 1 |
| 6 | u16 | header length = 64 |
| 8 | u32 | core length: header plus sections (at most 32768) |
| 12 | u32 | CRC32 of the core with this field as zero |
| 16 | char[12] | `AddressMap.version_hash` |
| 28 | u8[32] | SHA-256 of the TOML text |
| 60 | u32 | length of the TOML text (0 = none) |

| section | body |
|---|---|
| 1 INFO | `char[48] version, char[12] variant, i16 ECU id address (-1 none), u8 ECU id length, u8 0` |
| 2 PROTO | `[protocol]`: baud, command byte, lengths, wake-up bytes, the timing limits and the three target rates (as f64, so the planner computes with the laptop's numbers) |
| 3 CONST | f64 constants the programs use |
| 4 CHAN | 80 bytes per channel: `char[16] name, char[20] label, char[8] units, f32 lo, hi, warn_lo, warn_hi, alarm_lo, alarm_hi, u8 decimals, verified, rate class (0 fast, 1 medium, 2 slow), kind (0 value, 1 flag, 2 math), u8 address, length, little-endian, 0, u16 program offset, length`. Measured channels first, in map order, then math channels |
| 5 PROG | the programs, back to back |
| 6 ALARM | 92 bytes per rule: names, channel index, severity, flags, priority, low, high, hysteresis, hold, clear hold, rate limits and window, and the offsets of the `when`, `clear_when` and `gate` programs |
| 7 STALL | `f32 rpm_run, rpm_zero, link_down_s, pre_s, post_s, i16 rpm channel, u16 0` |

Unknown section types are skipped. The file is refused as a whole if a
checksum, a length or a program is wrong; the handheld then uses its
built-in map.

Not everything in a map reaches the handheld: math channels and alarms
that need history (`avg`, `slope`, `smooth`, `[rpm-1]` ...) are left out,
and `export-map` lists them. A channel formula that cannot be compiled
stops the export.

### Programs

A formula, math expression or condition is a postfix program for a stack
of doubles. `export-map` compiles it from the syntax tree `hondalog.expr`
parses, so the handheld has no parser of its own and no second grammar.
There are no jumps: a program runs once from its first byte to `END`. NaN
means "no value".

| byte | operands | does |
|---|---|---|
| `00` END | | the result is the one value on the stack |
| `01` CONST | u16 index | push a constant |
| `02` IMM | u8 | push 0..255 |
| `03` X | | push the raw bytes as one unsigned number |
| `04` BYTE | u8 i | push raw byte i (`b[i]`) |
| `05` CH | u8 i | push channel i's value (`[rpm]`) |
| `06` TIME | | push seconds |
| `10`–`13` | | negate, logical not, bitwise not, `valid()` |
| `20`–`2B` | | `+ - * / // % ** & | ^ << >>` |
| `30`–`35` | | `< <= > >= == !=` |
| `38`–`3B` | | and, or, `a ? b : c`, coalesce |
| `40` CALL | u8 function, u8 argument count | `abs min max round int float floor ceil sqrt exp ln log10 pow sin cos sign clamp between bit signed poly table thermistor` (in that order from 0) |

## HANDHELD.CFG

`key = value` lines, `#` starts a comment. A line that is unknown or out of
range is skipped; the rest still applies.

| key | default | meaning |
|---|---|---|
| `backend` | `auto` | `usb`: KKL cable on the USB host socket. `uart`: L9637D on the UART. `auto`: USB |
| `gps_baud` | 9600 | 0 = no GPS |
| `iso_fallback` | 1 | try ISO 9141-2 when Honda reads stay unanswered |
| `iso_after_s` | 8 | ... for this long |
| `drive_gap_s` | 120 | ECU silent this long ends the drive |
| `sync_ms` | 1000 | card sync period |
| `block_ms` | 250 | longest time a block stays open |
| `prealloc_mb` | 16 | space taken per drive file; 0 = none |
| `stall_pre_s`, `stall_post_s` | -1 | capture window; -1 = the map's (30, 10) |
| `capture_files` | 1 | write `CAPTURES/` files |
| `sleep_after_s` | 300 | deep sleep this long after a drive ended; 0 = never |
| `sleep_probe_s` | 20 | asleep: wake this often and ask the ECU |
| `usb_latency_ms` | 1 | FTDI latency timer |
| `utc_offset_min` | 0 | for the clock on the screen; files are UTC |
| `serial` | 1 | the binary link to the laptop on the USB-C port (SERIAL.md); 0 = plain log console |
| `serial_baud` | 921600 | its speed; 460800 if the laptop sees damaged frames |
| `rotate` | 0 | display rotation, 0 or 180 (upside down). Firmware built with `-DHH_ROTATE_DEFAULT=180` defaults to 180 |
