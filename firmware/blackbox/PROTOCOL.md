# Black box USB protocol (version 1)

The box is a USB CDC serial device (ESP32-S3 built-in USB Serial/JTAG,
`303a:1001`, `/dev/hondalog-bb` with the udev rule). Baud rate is ignored.
Reference client: `hondalog/blackbox/reader.py`. Reference device without
hardware: `hondalog-bbsim` or port name `bbsim:`. Constants:
`components/bbcore/include/bbcore.h` and `hondalog/blackbox/format.py`.

## Frame

```
COBS( ver  type  seq  payload[0..512]  crc16_lo  crc16_hi )  0x00
```

| field | size | meaning |
|---|---|---|
| ver | 1 | protocol version, `1`. Frames with another version are dropped |
| type | 1 | see the tables below |
| seq | 1 | sender's counter, wraps at 256. For spotting lost frames; not checked |
| payload | 0–512 | little-endian, packed |
| crc16 | 2 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over ver..payload |

The whole thing is COBS-encoded, so `0x00` appears only as the frame end.
There is no length field: the delimiter gives the length. A receiver that
joins mid-stream throws away bytes up to the first `0x00`. Bad CRC, bad COBS
or over-long frames are dropped silently and counted.

There are no acknowledgements. Requests that have an answer are listed with
it; the client retries on timeout.

## Device to host

| type | name | payload |
|---|---|---|
| 0x01 | HELLO | `u8 proto, u8 file_ver, char fw[12], char device[12], u16 sample_hz, u16 live_hz, u32 boot_no, u32 event_count, u8 n_analog, u8 n_edge` (40 bytes) |
| 0x02 | LIVE | `u64 t_us, sample[28], u16 mv_min[5], u32 event_count, u8 state, u8 last_reason, u16 rsv` (54 bytes). Sent `live_hz` times a second (default 50) |
| 0x03 | EVENT | `u64 t_us, u8 code, u8 src, u16 action, i32 value, u32 event_no, u8 started_file` (21 bytes). Sent once per trigger, marker or state change, before the next LIVE |
| 0x05 | TIME_RSP | `i64 host_tx_us, u64 dev_rx_us, u64 dev_tx_us` |
| 0x0A | TEXT | ASCII, at most 240 bytes, whole lines. Answers to config and self-test requests |
| 0x0B | FILE_INFO | `u32 event_no, u32 size, u8 reason, u8 complete`. `event_no = 0xFFFFFFFF` ends the list |
| 0x0C | FILE_DATA | `u32 event_no, u32 offset, data[0..480]`. `offset = 0xFFFFFFFF`: no such file, or it is still being written |

`t_us` is device time: microseconds since the box booted. `sample` is one
`bb_sample_t` as stored in event files (FORMAT.md). `mv_min` is the lowest
reading of each analog channel since the previous LIVE frame, in car-side mV,
so a dropout shorter than a frame still shows. `state` bits: 0x01 SD ok,
0x02 capturing, 0x04 clock set, 0x08 engine running, 0x10 IG1 on,
0x20 GPS fix, 0x40 self-test failed. `code`, `last_reason`: reason codes,
FORMAT.md.

## Host to device

| type | name | payload | answer |
|---|---|---|---|
| 0x10 | GET_HELLO | none | HELLO |
| 0x11 | TIME_REQ | `i64 host_tx_us` (any clock; it is echoed back) | TIME_RSP |
| 0x12 | TIME_SET | `i64 unix_us_at_t0`: wall-clock microseconds at device time 0 | none. Ignored once GPS has set the clock |
| 0x13 | MARK | `i32 number` | EVENT with code 32 and that number |
| 0x14 | TRIGGER | none | EVENT with code 8; a capture starts |
| 0x15 | SELFTEST | none | TEXT lines, the last one `selftest: PASS` or `selftest: FAIL` |
| 0x16 | GET_CONFIG | none | TEXT frames with the config; the last line is `# end` |
| 0x17 | SET_CONFIG | ASCII `key=value` | TEXT `ok key=value` or `error key=value`. RAM only |
| 0x18 | SAVE_CONFIG | none | TEXT `ok saved` or `error save failed`. Writes `BLACKBOX.CFG` |
| 0x19 | LIST_FILES | none | FILE_INFO for the newest 32 captures, newest first, then the end record |
| 0x1A | READ_FILE | `u32 event_no, u32 offset, u16 len` (len at most 480) | FILE_DATA. A short answer means end of file |
| 0x1B | LIVE_RATE | `u16 hz` (0 stops the stream, at most 500) | none |

## Time sync

The host sends TIME_REQ with its monotonic clock about once a second. From
the answer and its own receive time:

```
round_trip = (host_rx - host_tx) - (dev_tx - dev_rx)
offset     = (host_tx + host_rx) / 2 - (dev_rx + dev_tx) / 2      host_us = device_us + offset
```

The client keeps the last 16 exchanges and uses the one with the shortest
round trip. It then sends TIME_SET so event files carry laptop wall-clock
time, and re-sends it when the estimate moves by more than 2 ms (crystal
drift). With no laptop, an optional GPS sets the clock instead.

## A session

```
host: GET_HELLO                      box: HELLO
host: TIME_REQ (x5, 100 ms apart)    box: TIME_RSP each
host: TIME_SET
                                     box: LIVE, LIVE, ... (50 Hz)
host: MARK 3                         box: EVENT code 32 value 3
                                     box: EVENT code 4 started_file 1 event_no 12   <- ECU +B dropped
host: LIST_FILES                     box: FILE_INFO 12 ..., FILE_INFO end
host: READ_FILE 12 0 480             box: FILE_DATA 12 0 <480 bytes>   ... until a short one
```
