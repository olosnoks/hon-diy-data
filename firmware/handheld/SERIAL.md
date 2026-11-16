# The handheld's laptop link (USB-C serial)

The CYD's USB-C socket goes to a CH340C (USB id `1a86:7523`) on UART0. With
the link on (`serial = 1` in `HANDHELD.CFG`, the default) the handheld streams
everything it learns to the laptop as binary frames: every raw ECU read, a
tick per fast cycle, GPS fixes, its events (markers, alarms, stall
triggers, trouble codes) and its counters. The laptop decodes the raw reads
with **its own** address map and feeds them into the same live path as the
KKL cable (`LivePipeline` -> `Recorder` -> triggers), so a drive streamed
from the handheld is recorded like any other, with source `cyd`.

The handheld keeps logging to its SD card either way. The link is an
extra: nothing on the handheld waits for it.

Code: `components/hhcore/hh_serial.c` (portable: framing, ring, commands),
`components/hhcore/port/hh_serial_port.c` (UART, tasks, ESP_LOG),
`components/hhcore/port/hh_serfs.c` (the SD card for the file commands);
on the laptop `hondalog/handheld/serial_proto.py` (layouts),
`serial_link.py` (`CydPoller`, `CydClient`), `sync.py`, `cydsim.py`.

## Two firmware variants

| | default | `HH_GPS_P1` (`scripts/build-firmware.sh handheld --gps-p1`) |
|---|---|---|
| GPS | P4, GPIO18, UART1 | P1, GPIO44 (RX0), UART0 at 9600 baud |
| link UART | UART0, TX GPIO43, RX GPIO44 | UART1, TX routed to GPIO43, no RX |
| direction | **two-way** | **TX only**, handheld -> laptop |
| live stream, hello, stats, events, GPS | yes | yes |
| commands (marker, files, map, clock, DTC ...) | yes | no |
| Link screen `PC` row | `PC` (laptop heard in the last 3 s) / `NO PC` | `TX` (it cannot know who listens) |
| ESP-IDF console | replaced by the link once it starts | off (`sdkconfig.gps_p1`) |

The default build is the main, tested path: move the GPS to P4 (GPIO18) and
the handheld can be talked to. With the GPS on P1, GPIO44 belongs to the
GPS, and a UART has one baud rate for both directions, so the link moves to
UART1 and keeps only its TX pin. The laptop **never writes** to a handheld
whose hello says TX-only: on that board the CH340's TX line is wired to
the GPS's.

The Link screen shows the link in two rows: `PC` (`OFF`, `TX`, `PC` with a
green dot, or `NO PC` with a ring) and `PC SENT/LOST` (frames sent, frames
dropped because the laptop did not keep up or nobody was listening).

## Settings (`HANDHELD.CFG`)

| key | default | |
|---|---|---|
| `serial` | 1 | 0 = no link; UART0 stays the plain ESP-IDF log console |
| `serial_baud` | 921600 | 460800 if the laptop sees damaged frames. The CH340C's nearest rate to 921600 is 923077 (+0.16 %) |

The laptop finds the rate itself: it tries 921600, then 460800, then
115200, each for 3 s, until frames check out (`cyd:/dev/ttyUSB0@460800`
fixes it).

## Frames

The frame is the black box's (`firmware/blackbox/PROTOCOL.md`), so one
deframer reads both:

    COBS( ver  type  seq  payload[0..512]  crc16_lo crc16_hi )  0x00

- `ver` = 1. `crc16` is CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over
  `ver type seq payload`.
- `seq` counts frames in each direction separately. The handheld numbers a
  frame when it is queued, so a gap on the laptop is exactly the number of
  frames dropped on the way (ring overflow or damage).
- All numbers little-endian, structs packed. `t_us` is the handheld's own
  microsecond clock since boot.
- The handheld starts every UART write with an extra `0x00`. Anything on the
  wire that is not a frame (the ROM's boot text, a `printf` in a library)
  then ends at that byte and costs the laptop one damaged chunk, never the
  frame after it. Empty chunks are skipped.
- Type numbers 0x40-0x6F are the handheld's; the black box uses 0x01-0x1F.

### Handheld -> laptop

| type | name | payload |
|---|---|---|
| 0x40 | HELLO | 96 bytes, below. Every 2 s, and on GET_HELLO |
| 0x41 | READ | `u64 t_us, u8 addr, u8 len, u16 latency_us` + `len` raw ECU bytes. One per K-line block read. `t_us` is when the request went out; `t_us + latency_us` is when the reply's last byte was in |
| 0x42 | TICK | `u64 t_us, u16 latency_us, u16 hz_c, u32 n`: one fast cycle done (every fast block read once). `hz_c` = cycle rate x 100 |
| 0x43 | LINK | `u64 t_us, u8 state, u8 up, u8 backend, u8 iso`. On every change, and repeated (about 4/s) while the ECU is silent so time moves on for the laptop. `state`: 0 no reply, 1 connecting, 2 ok, 3 OBD2 only, 4 K-line cable not found |
| 0x44 | GPS | 28 bytes, the GPS record of FORMAT.md (`u64 t_us, i32 lat_e7, i32 lon_e7, u32 utc, u16 speed_cms, u16 course_cdeg, u8 fix, u8 sats, u16 hdop_c`). Once per RMC sentence |
| 0x45 | EVENT | 16 bytes, the event record of FORMAT.md (`u64 t_us, u8 code, u8 src, u16 aux, i32/f32 value`): marker, link up/down, alarm raised/cleared/acknowledged, stall trigger, trouble code, drive start ... |
| 0x46 | STATS | 96 bytes, below. Once a second |
| 0x47 | LOG | ESP_LOG text, at most 240 bytes per frame |
| 0x48 | ISO | `u64 t_us, u8 mode, u8 pid, u8 len, u8 flags` + `len` bytes: an OBD2 (ISO 9141-2) reply, when the handheld fell back to OBD2 |
| 0x49 | TIME_RSP | `i64 host_tx_us, u64 dev_rx_us, u64 dev_tx_us` (answer to TIME_REQ) |
| 0x4A | ACK | `u8 cmd, u8 seq, i16 status, u32 value`: the command's type and seq, the result |
| 0x4B | FILE_INFO | `u8 kind, u8 flags, u16 rsv, u32 file_no, u32 size`. flags 1 = open for writing. The list ends with `file_no = 0xFFFFFFFF`, `size` = entries sent, flags 2 = more after the last one |
| 0x4C | FILE_DATA | `u8 kind, u8 flags, u16 len, u32 file_no, u32 offset, u32 crc32` + `len` bytes. flags 1 = end of file. `offset = 0xFFFFFFFF`: no such file (flags 2: it is being written) |

HELLO (96 bytes):

| offset | type | field |
|---|---|---|
| 0 | char[4] | `"HHSL"` |
| 4 | u8 | link revision (1) |
| 5 | u8 | variant: 0 default, 1 GPS on P1 |
| 6 | u16 | capabilities: 1 commands are heard (two-way), 2 file list/read, 4 file push, 8 LOG frames. 2 and 4 only with a working SD card |
| 8 | char[12] | firmware version |
| 20 | char[12] | device name |
| 32 | char[12] | address map hash (all 12 characters, no terminator): compare with the laptop map's `version_hash` |
| 44 | u8[8] | first 8 bytes of the map text's sha256 |
| 52 | u8[10], u8 | ECU id, its length (0 = not read yet) |
| 63 | u8 | link state (as LINK) |
| 64 | u8, u8 | backend (0 USB KKL cable, 1 UART), link mode (1 = OBD2) |
| 66 | u8 | channels in the handheld's map |
| 67 | u8 | flags: 1 built-in map, 2 clock set, 4 clock from GPS, 8 SD card ok |
| 68 | u32 | boot number |
| 72 | u32 | baud rate |
| 76 | u64 | `t_us` now |
| 84 | i64 | wall clock now, unix microseconds (0 = not known) |
| 92 | u16, u16 | hello interval (ms), largest payload |

STATS (96 bytes): `u64 t_us, i64 unix_us`; the K-line since boot: `u32 reads,
errors, timeouts, checksum_failures, retries, reconnects`; `u16 hz_c, u8
link_state, u8 flags` (1 SD ok, 2 recording, 4 GPS fix, 8 clock set, 16
stall trigger armed, 32 capturing, 64 laptop heard, 128 built-in map);
`u32 drive_no, log_bytes, free_mb, markers, stall_count, nmea_good,
nmea_bad`; this link since boot: `u32 tx_frames, tx_dropped, tx_bytes,
rx_frames, rx_bad, ring_peak`.

### Laptop -> handheld (two-way firmware only)

| type | name | payload | answer |
|---|---|---|---|
| 0x60 | GET_HELLO | none | HELLO |
| 0x61 | TIME_REQ | `i64 host_tx_us` (echoed) | TIME_RSP. The laptop sends one a second: it is also the heartbeat behind `PC` |
| 0x62 | TIME_SET | `i64 unix_us` now | ACK: 0 taken, 1 ignored (GPS time is in use) |
| 0x63 | MARK | none | ACK, and an EVENT marker (the long press) |
| 0x64 | ACK_ALARM | none | ACK |
| 0x65 | DRIVE | `u8 on`: 0 ends the drive file and starts no other, 1 lets drives start again | ACK, value = on |
| 0x66 | LIST_FILES | `u8 kind, u8[3], u32 after_no` | up to 24 FILE_INFO with numbers above `after_no`, lowest first, then the end record |
| 0x67 | READ_FILE | `u8 kind, u8, u16 len, u32 file_no, u32 offset` (len at most 480) | FILE_DATA |
| 0x68 | PUT_BEGIN | `u8 kind, u8[3], u32 size, u32 crc32` | ACK, value = bytes already held (the same file again carries on) |
| 0x69 | PUT_DATA | `u32 offset` + up to 480 bytes | ACK, value = bytes held. Status -8 (wrong offset): value says where to go on |
| 0x6A | PUT_END | none | ACK. The file is read back from the card, its length and CRC checked, a map is test-loaded; only then does it replace the old one (kept as `HANDHELD.MBK` / `.CBK`). For a CFG, value = lines it will skip. Used from the next power-up |
| 0x6B | DTC | none | ACK; the codes come as EVENTs (`dtc` per code, or `dtc_none`) |

File kinds: 1 `DRIVES/Dnnnnnn.HHD`, 2 `CAPTURES/Cnnnnnn.HHD`,
3 `HANDHELD.MAP` (at most 256 KB), 4 `HANDHELD.CFG` (at most 8 KB).
ACK status: 0 ok, 1 ignored, -1 bad argument, -2 card error, -3 CRC
mismatch, -4 not expected now, -5 refused (the map would not load), -6
busy, -7 not supported (no card, or older firmware), -8 wrong offset.

**Nothing on this link can put anything but a read on the K-line.**
`hh_serial.c` has no transport. Every command touches the card, the clock,
or asks the engine for something the button already offers. DTC asks for
the engine's own Mode 03 read, behind the same guard (`hh_iso_allowed`) as
the button: live data pauses for up to about 10 s while it runs.

## Inside the handheld

    engine --tap--> hh_serial_tap() --+
    ESP_LOG ------> hh_serial_log() --+--> ring (32 KB PSRAM; oldest dropped, counted)
    commands -----> hh_serial_push() -+         |
                                                v  TX task (priority 2, core 0)
                                     hh_serial_poll(): frames --> uart_write_bytes
    UART RX --> RX task (priority 3) --> hh_serial_rx() --> commands      (default build)

- The engine's tap is called with its lock held and only copies a record into
  the ring. When the ring is full the **oldest** frames go first and are
  counted (`tx_dropped`, on the Link screen and in STATS); the newest data is
  what a laptop that comes back wants. Without PSRAM the ring is 8 KB.
- The TX task is the lowest-priority task of the data side and is woken
  when a frame is queued. It is the only thing that waits on the UART.
- ESP_LOG: from the moment the link starts, `esp_log_set_vprintf` turns each
  line into a LOG frame, and (default build) stdout goes through the UART
  driver, so a stray `printf` lands whole between two writes. What the ROM
  and the bootloader print before that is at 115200 baud: the laptop counts
  it as damaged data and carries on.

## Bandwidth

Measured on the host build against the simulated ECU (shipped map, fast
cycle about 19 Hz): **about 1.9 kB/s, 80 frames/s**, i.e.

| baud | capacity | live stream uses |
|---|---|---|
| 921600 | 92 kB/s | 2.1 % |
| 460800 | 46 kB/s | 4.2 % |
| 115200 | 11.5 kB/s | 17 % |

The K-line itself (9600 baud) is the limit: the stream cannot go much
above 3-4 kB/s whatever the map. The ring holds about 17 s of it (4 s
without PSRAM).

File transfer is stop-and-wait in 480-byte chunks, each with its own CRC32:
504 bytes on the wire per chunk (5.5 ms at 921600, 11 ms at 460800) plus the
round trip through USB, so expect roughly **40-55 kB/s at 921600** and 25-35
kB/s at 460800 (estimated, not measured on the board). An hour of driving
is about 6 MB on the card: two to three minutes. A drive that power cut short
keeps its preallocated length (16 MB, FORMAT.md); `sync` stops 256 KB after
its last good block instead of copying the rest.

## On the laptop

Port names (anything that takes a port: the app, `hondalog-log record`,
`hondalog-handheld`):

| port | |
|---|---|
| `cyd:` | find it: `/dev/hondalog-cyd`, else a CH340 (`1a86:7523`), else `hondalog-cydsim`'s pty |
| `cyd:/dev/ttyUSB0` | that port; `cyd:/dev/ttyUSB0@460800` also fixes the speed |
| `cyd:sim`, `cyd:sim:p1` | the built-in simulator, two-way or TX only (`:stall@8`, `:power@8`, `:nogps` add a stall, a power cut, no GPS) |

**The app.** The connection menu lists `CYD (serial)` entries for every
handheld found and for the two built-in simulators. Once connected the info
bar shows the link: variant, rate, `kB/s at baud`, frames lost / damaged /
dropped, the handheld's SD state and K-line counters, and `MAP MISMATCH`
when the handheld's map is not yours (the status line says so once, too).
Markers and alarm acknowledgements from the laptop go to a two-way handheld
as well, so its SD file has them; a marker from the handheld's button
arrives as a marker in the laptop's drive. The handheld's stall trigger
opens a capture on the laptop (as the black box's does).

**Recording without the GUI:**

    hondalog-log record --port cyd: --seconds 600

The drive's header has `source: "cyd"` and a `cyd` section (device,
firmware, variant, map hash, mismatch, frames lost); GPS goes into the `gps`
stream with the same columns as an imported drive.

**Map mismatch.** The handheld sends raw bytes; the laptop decodes them with
its own map, so the values are always the laptop map's. If the handheld's
map is another one, the handheld may read other blocks: channels of your
map that none of its reads contain stay empty (`uncovered_channels` in the
header). `hondalog-handheld push-map` fixes it for the next power-up.

**The command line (two-way firmware):**

    hondalog-handheld status   [--port cyd:]           # hello, counters, damaged frames
    hondalog-handheld sync     [--port cyd:] [--db FILE] [--end-drive] [--no-captures] [--no-import] [--mirror DIR]
    hondalog-handheld push-map [--port cyd:] [--map TOML] [--cfg HANDHELD.CFG]
    hondalog-handheld set-time [--port cyd:]
    hondalog-handheld command  mark|ack-alarm|dtc|drive-stop|drive-start [--port cyd:]

`sync` copies the card into a mirror next to the database
(`~/hondalog/handheld/<device>/`, with `HANDHELD.MAP` so the importer finds
the map by its hash) and imports it with the same importer as a card in a
reader: what is already in the database is skipped. It is resumable: a file
already copied whole is not asked for again (`SYNC.json` remembers the
card's lengths), a transfer cut off half-way carries on from its `.part`
file, a damaged chunk is asked for again. The drive being written is
skipped unless `--end-drive` ends it first (a new one starts at once).
`status` works on a TX-only handheld; the others say why they cannot.

**The simulator** puts the same stream on a pty, either variant:

    hondalog-cydsim [--variant default|p1] [--sd DIR] [--stall-after S] [--stall-mode rpm|power] [--damage 0.01]
    hondalog --port cyd:/tmp/hondalog-cydsim --connect

With `--sd` it writes real drive files there and answers the file
commands, so `sync` has something to pull. Keys: `s` stall, `p` power cut,
`r` restart, `m` marker, `q` quit.

**Install.** `install.sh` adds the udev rule from
`packaging/99-hondalog.rules`: the CH340 becomes `/dev/hondalog-cyd`,
group `dialout`, and ModemManager is told to leave it alone
(`ID_MM_DEVICE_IGNORE`).

## Troubleshooting

| symptom | cause and fix |
|---|---|
| the port appears and vanishes at once | **brltty** claims `1a86:7523` as a braille display. `sudo apt remove brltty` |
| the port is busy for a few seconds after plugging in, or junk reaches the handheld | **ModemManager** probing it with AT commands. The udev rule stops that; if it was installed before the rule: `sudo udevadm control --reload-rules`, replug, or `sudo systemctl disable --now ModemManager` |
| `Permission denied` | not in `dialout` yet: log out and in after `install.sh` |
| the handheld restarts (or stays dark) when the laptop opens the port | DTR/RTS drive the board's auto-reset circuit. The laptop releases RTS first, then DTR, which should never pulse EN; another program (a serial terminal, `screen`) may not. Close it and replug |
| `no hello from a handheld` | the link is off (`serial = 0` in `HANDHELD.CFG`), the firmware is older than the link, or the speed is wrong: try `cyd:/dev/ttyUSB0@460800`. `hondalog-handheld status` says what it heard |
| `damaged` keeps rising | a long or poor cable at 921600: `serial_baud = 460800` in `HANDHELD.CFG` (push it with `push-map --cfg`, then power-cycle) and `cyd:...@460800` |
| `dropped` rises | the laptop did not read for a while (suspended, overloaded). The handheld's own log is unaffected |
| `MAP MISMATCH` | `hondalog-handheld push-map`, then power-cycle the handheld |
| a command says `tx-only` | the GPS-on-P1 build cannot listen. Copy the drives off the SD card (`hondalog-handheld import /media/...`) or move the GPS to P4 and flash the default build |
| GPS `BAD` rises on the Link screen while the laptop is attached (P1 build) | the CH340's TX and the GPS share GPIO44 through 100 ohm: the GPS module must out-drive the idle CH340. See below |
| a file is reported `still being written` | it is the current drive: `sync --end-drive` |
| text in the stream before the first hello | the ROM's and bootloader's boot messages at 115200; harmless |

## Not verified on hardware

- The CH340C at 921600 baud (923077 actual, +0.16 %) over a real USB-C
  cable: frame damage and the real chunk throughput. 460800 is the fallback.
- GPS on P1 with a laptop attached: GPIO44 is driven by both the GPS's TX
  and the CH340C's TX (idle high, through the board's 100 ohm). Whether
  every GPS module wins that contention is unknown; watch GPS OK/BAD.
- Whether this board has the DTR/RTS auto-reset circuit, and whether the
  laptop's RTS-then-DTR release on open avoids a reset with the CH340
  Linux driver.
- Text the ROM and the bootloader print at reset, and a `printf` from a
  library, are framed off by the leading `0x00`; seen only in the code.
