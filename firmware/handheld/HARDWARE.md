# Handheld hardware

A live-data viewer for when the laptop is not in the car. It is the owner's
Sunton **ESP32-4827S043N**: ESP32-S3-WROOM-1 N16R8, 4.3" 480x272 TN panel on
a 16-bit parallel RGB interface, no touch, microSD slot, USB-C through a
CH340C. It reads the ECU over the K-line, logs to the SD card and takes
position and time from a GPS.

The aim is the least hardware. With plan A the only new part is a USB-A
socket.

**Status: nothing here has been built or tested.** The pinout comes from
web sources that mostly agree; the USB driver facts come from reading the
driver source. Items marked **[unverified]** must be checked with a meter
before you trust them. They are collected at the end. All pin numbers are
in `include/board_pins.h`, one `#define` each, with the source beside it.

## Pin map

| GPIO | use | where | notes |
|---|---|---|---|
| 0 | BOOT button | on board | strapping pin; the one built-in button |
| 1, 3, 8, 9, 46 | LCD blue B4, B1, B0, B3, B2 | LCD connector | 3 and 46 are strapping pins |
| 4, 5, 6, 7, 15, 16 | LCD green G5, G0, G1, G2, G3, G4 | LCD connector | |
| 14, 21, 45, 47, 48 | LCD red R4, R3, R0, R2, R1 | LCD connector | 45 is a strapping pin |
| 39, 40, 41, 42 | LCD HSYNC, DE, VSYNC, PCLK | LCD connector | |
| 2 | backlight | on board | enable of the boost LED driver, active high, PWM dims it |
| 10, 11, 12, 13 | microSD CS, MOSI, SCK, MISO | slot; 11–13 also on P2 | SPI2 |
| 43, 44 | UART0 TX, RX | CH340C and P1 | laptop link (SERIAL.md), console, flashing |
| 17 | **free** → second button (optional) | P3, P4 | nothing on the board uses it |
| 18 | **free on N** → GPS RX | P3, P4 | touch interrupt on the R board only |
| 19 | **free on N** → USB D− (plan A) or K-line TX (plan B) | P3, P2 | touch SDA on the C board; see "pull-ups" below |
| 20 | **free on N** → USB D+ (plan A) or K-line RX (plan B) | P3 | touch SCL on the C board |
| 38 | touch CS / reset on R and C | not on a header | leave alone |
| 35, 36, 37 | octal PSRAM | inside the module | not available |

No speaker or amplifier: none of the sources lists one for this board.

So the N board has exactly **four** usable header pins: 17, 18, 19, 20. This
design uses all four.

### Headers

Four 4-pin sockets. The pin numbering below is from espboards.dev [C];
esp3d.io [B] lists each socket in the opposite order, which is the same
socket seen from the other end. **Which end is pin 1 is [unverified]: read
the silk and check GND and 5 V with a meter before plugging anything in.**

| socket | pin 1 | pin 2 | pin 3 | pin 4 | plug |
|---|---|---|---|---|---|
| P1 | 5 V | GPIO43 (TX0) | GPIO44 (RX0) | GND | JST 1.25 mm 4-pin [A] |
| P2 | GPIO19 (silk says IO18 [C]) | GPIO11 (MOSI) | GPIO12 (SCK) | GPIO13 (MISO) | **[unverified]** |
| P3 | GPIO17 | GPIO18 | GPIO19 | GPIO20 | JST 1.0 mm 4-pin [A] **[unverified]** |
| P4 | GND | 3V3 | GPIO17 | GPIO18 | **[unverified]** |

P3 and P4 share GPIO17 and 18. P2 pin 1 is GPIO19 again: with plan A, leave
P2 pin 1 unconnected.

P1's 5 V pin is described as an **input**, switched onto the 5 V rail through
a P-channel MOSFET (Q1, AO3401) [C]. Whether it also carries 5 V *out* when
the board is fed from USB-C is **[unverified]** and matters for plan A
(see "Power").

### Panel timing

For `esp_lcd_new_rgb_panel()`. Values are from the rzeldent board file [A],
which was written for esp_lcd.

| parameter | value | other sources |
|---|---|---|
| pixel clock | 8 MHz | [D] 8 MHz; [E] 12 MHz |
| HSYNC pulse / back porch / front porch | 4 / 43 / 8 | [E] the same; [D] 1 / 43 / 1 |
| VSYNC pulse / back porch / front porch | 4 / 12 / 8 | [E] the same; [D] 1 / 12 / 3 |
| `hsync_idle_low`, `vsync_idle_low` | 1, 1 | |
| `de_idle_high` | 0 | |
| `pclk_active_neg` | 1 | [D], [E] agree |
| `pclk_idle_high` | 0 | [E] has 1 |
| frame buffer | in PSRAM, 64-byte alignment | |

535 × 296 clocks per frame at 8 MHz is 50.5 frames per second. The back
porches (43, 12) and the clock edge are the same in every source; the pulse
widths and front porches differ and the panel is driven in DE mode, so they
are unlikely to matter.

**Conflict: red and blue.** Four sources ([B], [C], [D], [E]) put red on
GPIO45, 48, 47, 21, 14 and blue on 8, 3, 46, 9, 1. The rzeldent file [A]
names them the other way round. The header follows the four. If the first
test pattern shows red and blue exchanged, swap the two groups in
`board_pins.h`.

## K-line plan A: the KKL cable on USB host (recommended)

The S3's own USB pads are GPIO19 (D−) and GPIO20 (D+). On this board the
USB-C socket goes to the CH340C instead, so the pads are free and reach P3.
Wire a USB-A socket to them, plug in the KKL cable you already own, and the
S3 is the USB host. The cable does the 12 V side, as it does on the laptop.

```
 USB-A female breakout                 ESP32-4827S043N
 ┌─────────────┐
 │ 1 VBUS (red)├───────────────────── 5 V   (see "Power" for where)
 │ 2 D−  (wht) ├───────────────────── GPIO19   P3 pin 3
 │ 3 D+  (grn) ├───────────────────── GPIO20   P3 pin 4
 │ 4 GND (blk) ├───────────────────── GND      P4 pin 1 or P1 pin 4
 └─────────────┘
        ▲
        └── KKL 409.1 cable (FT232RL, 0403:6001) ── OBD2 plug: K = pin 7,
            +12 V = pin 16, GND = pins 4 and 5
```

Keep the D−/D+ wires short (under 10 cm), the same length, and twisted
together. Full speed (12 Mbit/s) tolerates this; long loose wires will not
work.

### Is anything else on GPIO19/20?

- **Touch controller.** On the C board they are the GT911's I²C lines. The N
  board has no touch panel, so no chip is on them.
- **Pull-ups: the one real risk.** The schematic has 4.7 kΩ pull-ups to
  3.3 V on both lines (R3 on GPIO20, R4 on GPIO19) for that I²C bus [C].
  Whether the factory fits them on the N board is **[unverified]**. USB
  cannot work with them: a host holds both lines down with 15 kΩ, and
  4.7 kΩ against that lifts both to about 2.5 V, which is not a valid idle
  state. **Check first:** board unpowered, measure GPIO19 to 3V3 and GPIO20
  to 3V3 on P3/P4. About 4.7 kΩ means they are fitted: remove R3 and R4
  (two 0402/0603 resistors). Megohms or open means the board is ready.
- **P2 pin 1** is GPIO19 too. Leave it open.

### Software: what the ESP-IDF driver can do

Verified by reading the source of the managed components
(`espressif/esp-usb`, `host/class/cdc/`), not by running them.

| component | version read | needs |
|---|---|---|
| `espressif/usb_host_ftdi_vcp` | 2.1.1 (2026-05-27) | `usb_host_cdc_acm` ^2.3.0 |
| `espressif/usb_host_cdc_acm` | 2.4.1 | ESP-IDF ≥ 5.0; targets include esp32s3 |
| `espressif/usb_host_vcp` | 1.0.0~6 | optional: picks the driver by VID/PID. Not needed for one known cable |

The black box builds with ESP-IDF 5.3 (`espressif32@^6.9.0`); the same
works here. From IDF 6.0 the USB host library itself becomes a managed
component (`espressif/usb`), pulled in automatically.

| need | supported? | how |
|---|---|---|
| FT232R, 0403:6001 | **yes** | `ftdi_vcp_open(FT232_PID, 0, &cfg, &hdl)`. C API since 2.1.0; older versions need C++ with exceptions |
| baud rate | **yes** | `cdc_acm_host_line_coding_set()`. The driver computes the FTDI divisor from 3 MHz; 9600 and 10400 are exact enough (divisor 312.5 and 288.5) |
| 8N1 | **yes** | same call: `bDataBits = 8`, `bParityType = 0`, `bCharFormat = 0` |
| DTR / RTS | yes | `cdc_acm_host_set_control_line_state()` |
| **break** | **not in the driver** | `send_break` is marked "Not implemented" for FTDI. Send the FTDI request yourself: see below |
| **latency timer** | **not in the driver** | not mentioned anywhere in it. Send the FTDI request yourself: see below |
| receive | yes | data callback; the driver strips FTDI's two status bytes per packet. `in_buffer_size` is forced to 0, so reassemble replies in the callback |

Both missing pieces are one vendor control request each, and the CDC driver
exports `cdc_acm_host_send_custom_request()` for exactly that. The request
codes are the ones Linux `ftdi_sio` uses:

| action | bmRequestType | bRequest | wValue | wIndex |
|---|---|---|---|---|
| break on (8N1) | 0x40 | 0x04 (SET_DATA) | 0x4008 = 8 data bits + bit 14 | 0 |
| break off (8N1) | 0x40 | 0x04 | 0x0008 | 0 |
| latency timer = 1 ms | 0x40 | 0x09 (SET_LATENCY_TIMER) | 1 (ms, 1–255) | 0 |

The break bit shares a request with the data format, and the driver does
not remember it, so a later `line_coding_set` clears a break. That is the
behaviour wanted.

**Which of these does the handheld need?** The Honda protocol
(`20 05 addr len cksum`, 9600 8N1) needs **no break**: the wake-up is eleven
ordinary bytes (`Framing.init_sequence`). Break is only for the ISO 9141-2
5-baud init of the generic OBD2 path, where each bit lasts 200 ms and a
millisecond of USB jitter is harmless. The latency timer matters for speed:
the FT232R holds received bytes for up to 16 ms by default, and the laptop
app lowers that to 1 ms for the same reason (`transport.py`).

**Verdict: plan A works on paper.** Open, baud, 8N1 and data are in the
driver; break and latency need two custom requests that the API allows.
Not proven: that this FT232RL (or a clone chip in the cable) accepts them
from this host, and that the pull-ups are absent.

### Power for the cable

The cable's USB side is the FT232RL and an LED; the 12 V side takes its
power from OBD2 pin 16. FTDI's datasheet gives 15 mA typical for the chip,
so expect under 50 mA **[unverified for this cable]**. The 5 V rail carries
that easily. No VBUS switch is needed: wire VBUS straight to 5 V. There is
then no over-current protection on the socket; a 500 mA polyfuse in the
VBUS wire is cheap insurance and optional. The host stack does not need to
control or sense VBUS.

## K-line plan B: L9637D on a UART (fallback)

If USB host will not work, talk to the K-line directly. Same two header pins.

```
 OBD2 pin 16 (+12 V) ──▷|── 1N4007 ──┬───────────────┬──────── L9637D pin 7 (VS)
                                     │               │
                                  [100 nF]        [510 Ω 0.5 W]
                                     │               │
                                    GND              ├──────── L9637D pin 6 (K)
 OBD2 pin 7 (K-line) ────────────────────────────────┘

 OBD2 pins 4, 5 (GND) ──────────────────────────────────────── L9637D pin 5 (GND) ── board GND

 board 3V3 (P4 pin 2) ──┬── [100 nF] ── GND
                        └───────────────────────────────────── L9637D pin 3 (VCC)

 GPIO19 (P3 pin 3) ─────────────────────────────────────────── L9637D pin 4 (TX)
 GPIO20 (P3 pin 4) ─────────────────────────────────────────── L9637D pin 1 (RX)

 L9637D pin 8 (LI): to GND.   pin 2 (LO): not connected.
```

| part | value | why |
|---|---|---|
| L9637D | SO-8, on a SOIC-8 adapter board | the ISO 9141 K-line driver. VS 4.5–36 V, K output limited to about 60 mA, pull-ups on TX, RX and LO built in (ST product page) |
| pull-up | 510 Ω, 0.5 W (or two 1 kΩ 0.25 W in parallel) | ISO 9141 puts the pull-up to battery in the tester. 0.4 W if K is held low at 14.4 V |
| diode | 1N4007 or 1N5819 | reverse-polarity protection on the 12 V feed |
| capacitors | 100 nF on VS, 100 nF on VCC | decoupling |

- **VCC on 3.3 V, not 5 V**, so RX swings to 3.3 V and suits the S3
  directly. The pin numbers and the 3 V lower limit for VCC are from memory
  of the datasheet, not from a page read for this document:
  **[unverified], check the datasheet before soldering.**
- UART2, TX = GPIO19, RX = GPIO20, 9600 8N1, not inverted. Everything sent
  comes back on RX (single wire); the firmware drops the echo as the laptop
  code does.
- Break is `uart_set_line_inverse()` or driving the TX pin low as a GPIO
  for the 5-baud init.
- The pull-ups R3/R4, if fitted, do no harm here: a UART idles high.
- GPIO19/20 start out as USB pads owned by the chip's USB-serial-JTAG
  block. The firmware must hand them to the GPIO matrix before using the
  UART **[unverified which call is enough on IDF 5.3]**.

## Plan A or plan B

| | A: USB host + KKL cable | B: L9637D on UART |
|---|---|---|
| new parts | USB-A socket | L9637D, adapter board, 510 Ω, diode, 2 capacitors, OBD2 plug |
| cost | about $2 | about $10 |
| soldering | 4 wires (plus removing R3/R4 if fitted) | a small board, 12 V wiring |
| 12 V near the board | none: it stays inside the cable | yes |
| in the car | the cable and its USB lead, about 1.5 m | one thin lead, any length |
| firmware | USB host stack, FTDI driver, 2 custom requests | plain UART |
| latency | 1 ms (USB frame) after the latency request | none |
| break / 5-baud init | custom request | GPIO |
| the laptop's cable | shared: unplug to use the laptop | free |
| main risk | pull-ups on GPIO19/20; clone FTDI chips | none known; it is the standard circuit |

Start with A: it costs a socket and an evening. Keep the link code behind
one small "bytes in, bytes out, set baud, set break" interface, as
`hondalog/transport.py` does, so that B is a second back end and not a
rewrite. Both plans use the same two pins, so the choice does not affect
the GPS or the buttons.

## GPS

A NEO-6M module (GY-NEO6MV2 or similar) on P4, which has exactly the four
pins it needs.

```
 NEO-6M            P4
 VCC ───────────── pin 2  3V3
 GND ───────────── pin 1  GND
 TX  ───────────── pin 4  GPIO18   (UART1 RX, 9600 8N1)
 RX  ─── not connected             (pin 3, GPIO17, if you ever need to configure it)
```

- UART1, RX = GPIO18. `HH_GPS_TX` is −1: the module sends NMEA at 9600 once
  a second without being asked.
- Power from 3V3, as the black box does. The module draws about 45 mA
  tracking and up to 70 mA while acquiring **[unverified]**. Its own
  regulator will run in dropout on 3.3 V; if it will not get a fix, feed
  VCC from 5 V instead (the module's TX stays at 3.3 V).
- Give the antenna a view of the sky; keep it away from the display's
  ribbon cable.

## Power

| option | parts | key off |
|---|---|---|
| **1. USB car charger into USB-C** (start here) | none | the socket is switched with ACC: power is cut with no warning |
| 2. OBD2 pin 16 through a buck converter | fuse, diode, TVS, buck | pin 16 is always live: power stays on |

Budget: board with backlight about 250 mA, cable 50 mA, GPS 70 mA, SD
writes 100 mA peak: under 500 mA at 5 V **[unverified]**. Any 1 A charger
will do.

**Where VBUS for the USB-A socket comes from.** If P1 pin 1 shows 5 V while
the board runs from USB-C, take it there. If it does not (Q1 may only pass
power inwards **[unverified]**), feed the board through P1 instead and
branch the socket's VBUS off the same wire:

```
 option 1b: one cut USB cable from the charger

 charger ── USB-A plug ── red  ──┬── P1 pin 1 (5 V)
                                 └── USB-A socket VBUS
                          black ─┬── P1 pin 4 (GND)
                                 └── USB-A socket GND

 option 2: from the OBD2 plug (plan B already has a wire on pin 16)

 OBD2 pin 16 ── fuse 1 A ── 1N5819 ──┬── SMBJ24A ── GND
                                     └── buck 12→5 V ──┬── P1 pin 1 (5 V)
                                                       └── USB-A socket VBUS (plan A)
 OBD2 pins 4, 5 ── GND
```

With option 2 the handheld never loses power, so it must not stay awake:
when the ECU stops answering for a minute, close the log, turn the
backlight off and sleep, or it will flatten the battery in a few days.
Fit a switch in the 5 V wire if in doubt.

### Will the SD log survive key-off?

With option 1 the 5 V rail dies within milliseconds of key-off and the
board cannot see it coming: nothing on it measures the supply.

A capacitor does not help at this size. At 0.3 A, 2200 µF holds the rail
for about 4 ms. Supercapacitors (as on the black box) would give seconds,
but they need a charge resistor, two diodes and balancing, and there is no
power-fail signal to act on without yet another pin, and there is no pin.

**Recommendation: no extra hardware; make the log safe by format.**

1. Pre-allocate the log file in one contiguous piece at the start of a
   drive (FatFs `f_expand`). After that, logging only writes data sectors:
   the FAT and the directory entry, the parts that break a card when half
   written, are not touched again.
2. Write fixed-size records, each with a length and a CRC. On the next
   start, scan for the last good record and treat that as the end. A torn
   final record costs one record.
3. Flush about once a second (`fsync`). At 20 samples a second that is at
   most a second of data lost at key-off.
4. Never keep a file open for writing that the reader cannot recover: no
   in-place index, no header that is rewritten at the end.

This is the same idea as the black box's capture format (an incomplete
file is still readable). With option 2 the problem goes away instead: the
ECU goes quiet at key-off, the firmware closes the file and sleeps.

## Controls

**BOOT (GPIO0) is the one button.** It is a strapping pin, read once at
reset: held low then, the chip enters the serial bootloader and the screen
stays dark. After boot it is an ordinary input (button to GND, pull-up on).
So it is safe, with one rule: do not hold it while the car is started. The
CH340C can also pull GPIO0 low through the auto-flash circuit, but only
when a computer on the USB-C socket toggles DTR/RTS **[unverified that this
board has the circuit]**.

One-button use: short press = next page, long press = action on that page
(mark, night mode).

**A second button is possible on GPIO17**, the one pin left, as long as the
GPS's RX stays unconnected. Momentary switch from P3 pin 1 to GND, internal
pull-up, no resistor needed:

```
 GPIO17 (P3 pin 1) ──── button ──── GND
```

Nothing else is free: a third button would need the GPS or the K-line pins.

## Backlight

GPIO2 enables the backlight's boost driver, active high. PWM on it dims the
panel; every source drives it that way, so a night mode is possible. Use
LEDC. One user reports that the useful 8-bit duty range is about 10 to 130
and that higher values add nothing [D]. The PWM frequency the driver chip
likes is **[unverified]**; `HH_BL_PWM_HZ` = 1 kHz is a first guess. If the
panel flickers or whines, try 5 kHz, then 200 Hz.

## Bill of materials

Prices are rough guesses, except the L9637D (LCSC list price). "Have"
follows the black box's list; parts used there are not free for this.

| part | plan | qty | have? | about |
|---|---|---|---|---|
| Sunton ESP32-4827S043N | all | 1 | yes | |
| microSD card, ≤ 32 GB, FAT32 | all | 1 | yes | |
| KKL 409.1 cable (FT232RL) | A | 1 | yes | |
| USB-A female breakout board | A | 1 | | $1.50 |
| JST 1.0 mm and 1.25 mm 4-pin pigtails (if not in the box with the board) | all | 3 | maybe | $2 |
| NEO-6M GPS module with antenna | all | 1 | yes (shared with the black box) | $5 |
| USB car charger, 1 A or more, and a USB-C cable | power 1 | 1 | probably | $5 |
| momentary button (second button) | optional | 1 | probably | |
| polyfuse 500 mA (VBUS) | A, optional | 1 | | $0.50 |
| L9637D (E-L9637D013TR) | B | 1 | | $1.00 |
| SOIC-8 to DIP adapter | B | 1 | | $0.50 |
| 510 Ω 0.5 W, 1N4007, 100 nF × 2 | B | | probably | $0.50 |
| OBD2 male plug or pigtail | B, power 2 | 1 | | $6 |
| 12 → 5 V buck, 1 A fuse and holder, 1N5819, SMBJ24A | power 2 | | yes, if not in the black box | $8 |

| build | new money |
|---|---|
| **minimal: plan A, car charger, GPS you own** | **about $3.50** (socket, pigtails), $4 with the polyfuse |
| the same, buying a GPS and a charger | about $14 |
| plan B instead of A | about $10 (IC, adapter, passives, OBD2 plug, pigtails) |
| plan B powered from the OBD2 plug | about $18 |

## Bring-up, in order

Stop at the first step that fails.

| # | what | do | expect |
|---|---|---|---|
| 1 | header pins | board off; find GND on P1 and P4 by continuity to the USB-C shell; board on: find 5 V and 3V3 | pin 1 positions known, written on the board |
| 2 | **pull-ups** | board off; ohms from GPIO19 to 3V3 and GPIO20 to 3V3 | open or megohms. About 4.7 kΩ: remove R3 and R4 before plan A |
| 3 | 5 V out | board on USB-C; volts on P1 pin 1 | 4.7–5.1 V: use it for VBUS. 0 V: use power option 1b |
| 4 | display | flash a colour-bar test using `board_pins.h` | steady picture; red, green, blue in the right order (else swap R and B groups); no tearing or drift (else try the [D] porches) |
| 5 | backlight | LEDC on GPIO2, sweep the duty | smooth dimming, no flicker; note the useful range |
| 6 | SD | mount, write 1 MB, read it back, time it | mounts at 20 MHz; note the write speed |
| 7 | buttons | read GPIO0 and GPIO17 | 1 released, 0 pressed |
| 8 | GPS | wire P4, print UART1 | `$GP...` lines at 9600; a fix within a few minutes outdoors |
| 9 | USB host, bench | wire the socket; plug in the KKL cable, not in the car | the device enumerates as 0403:6001; `ftdi_vcp_open` returns OK |
| 10 | baud and latency | set 9600 8N1; send the latency request (1 ms) | both return `ESP_OK`. A stalled request means the chip is a clone that refuses it: note it and go on |
| 11 | echo | cable in the car's OBD2 port, key off; send 5 bytes | the same 5 bytes come back (the cable's 12 V side is powered from pin 16) |
| 12 | ECU | key on; send the wake-up bytes, then a read of a known address | a reply with a good checksum; then poll and count errors for 10 minutes |
| 13 | break | send break on, wait 200 ms, break off; scope or meter on K | K goes to 0 V for 200 ms. Only needed for the OBD2 path |
| 14 | key-off | log to SD while polling; turn the key off; repeat 20 times | every file opens and ends at a whole record; the card still mounts |
| 15 | current | USB meter on the charger | under 500 mA with everything running |

Plan B replaces steps 9–13: build the L9637D circuit, check K idles at
battery voltage and RX at 3.3 V, then steps 11 and 12 on UART2.

## Not verified

- Nothing has been built, compiled or run.
- **Whether R3/R4 (4.7 kΩ pull-ups on GPIO19/20) are fitted on the N
  board.** This decides whether plan A needs a soldering iron.
- Whether P1 pin 1 gives 5 V out when the board is powered from USB-C.
- Pin 1 position and plug type of each header. Two sources list the pins
  in opposite orders.
- Red/blue pin groups: one source of five has them exchanged.
- Sync pulse widths and front porches: sources differ (4/8, 4/8 against
  1/1, 1/3). Pixel clock 8 MHz against 12 MHz.
- That GPIO18 is really unconnected on the N board. It follows from the R
  and C descriptions; no source describes the N board itself.
- That the board has no speaker circuit (absent from every source, not
  confirmed from a schematic).
- The FTDI driver facts are from reading version 2.1.1's source. Break and
  latency through `cdc_acm_host_send_custom_request()` have not been tried.
  Whether `cdc_acm_host_send_break()` returns an error or sends a request
  the FT232R ignores was not checked; do not use it.
- That the cable's chip is a genuine FT232RL. Clones may refuse the
  latency request or behave differently on break.
- Current drawn by the cable from VBUS, by the board, and by the GPS.
- L9637D pin numbers, and that VCC may be 3.3 V.
- The call needed to release GPIO19/20 from the USB-serial-JTAG block for
  plan B.
- Backlight PWM frequency and useful duty range.
- Whether the board has the DTR/RTS auto-flash circuit on GPIO0.
- That the accessory socket in this car is switched with the key.
- SD behaviour at power loss. The format rules above reduce the risk; step
  14 measures it.

## Sources

- [A] rzeldent/platformio-espressif32-sunton, `esp32-4827S043N.json` and
  `esp32-4827S043C.json`: <https://github.com/rzeldent/platformio-espressif32-sunton>
- [B] ESP3D-TFT hardware page, Sunton 4.3" 480x272:
  <https://esp3d.io/esp3d-tft/version_1x/hardware/esp32-s3/sunton-43-4827/>
- [C] espboards.dev, CYD ESP32-4827S043 (headers, R3/R4, R17, Q1, U5):
  <https://www.espboards.dev/esp32/cyd-esp32-4827s043/>
- [D] Arduino_GFX discussion 587, "ESP32S3-4827S043 Quick reference":
  <https://github.com/moononournation/Arduino_GFX/discussions/587>
- [E] SquareLine forum, "Porting to a new board Sunton Esp32 480x272":
  <https://forum.squareline.io/t/porting-to-a-new-board-sunton-esp32-480x272/933>
- FTDI host driver, source and changelog:
  <https://github.com/espressif/esp-usb/tree/master/host/class/cdc/usb_host_ftdi_vcp>,
  registry page <https://components.espressif.com/components/espressif/usb_host_ftdi_vcp>
- CDC-ACM host driver (`cdc_acm_host_send_custom_request`):
  <https://github.com/espressif/esp-usb/tree/master/host/class/cdc/usb_host_cdc_acm>
- L9637 product page (ST): <https://www.st.com/en/automotive-analog-and-power/l9637.html>;
  LCSC listing for the price: <https://lcsc.com/product-detail/Interface-ICs_STMicroelectronics_E-L9637D013TR_E-L9637D013TR_C153038.html>
- Not reached: the openHASP page, lang-ship.com and the Sunton/LCDwiki
  PDFs for this exact board. The LCDwiki 4.3" ESP32-S3 pages found are a
  different product.
