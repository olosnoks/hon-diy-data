# Hardware

Three pieces of hardware, each optional after the first. This page says what
each is for, what to buy, and where the wiring detail lives.

| Piece | Track | Needed for | Detail |
|---|---|---|---|
| [KKL 409.1 USB cable](#1-the-kkl-4091-cable) | A | Everything on the laptop | this page |
| [ESP32-S3 black box](#2-the-black-box) | B | Millisecond record of power and ignition around a stall | [firmware/blackbox/HARDWARE.md](../firmware/blackbox/HARDWARE.md) |
| [CYD handheld](#3-the-cyd-handheld) | - | Live data and logging with no laptop in the car | [firmware/handheld/HARDWARE.md](../firmware/handheld/HARDWARE.md) |

A chipped P28 ECU, a chip burner and a wideband are Track C; see
[Tuning a P28](tuning-p28.md#parts).

**Status:** the cable is ordinary and expected to work. Neither the black
box nor the handheld has been built. Their hardware pages mark every
unconfirmed pin, colour and value with **[unverified]**, and so does this
page.

## 1. The KKL 409.1 cable

A "VAG-COM KKL 409.1" cable: a USB-to-serial chip, a K-line level shifter
and an OBD2 plug in one moulding. It was made for Volkswagens; the Honda
K-line is electrically the same thing. hondalog talks to it as a plain
serial port at 9600 baud, 8N1.

### What to buy

| | Buy | Avoid if you can |
|---|---|---|
| USB chip | **FTDI FT232RL**, USB id `0403:6001` | CH340 (`1a86:7523`): works for Honda reads, but most get the 10400 baud that OBD2 (ISO 9141-2) needs wrong |
| Listing words | "FTDI", "FT232RL", "409.1 KKL" | "ELM327" (a different device: it cannot do the Honda protocol), anything Bluetooth or Wi-Fi |

About $10 to $15.

**Genuine or clone FTDI.** Many "FT232RL" cables carry a counterfeit chip.
On Linux it does not matter for Honda reads: the kernel's `ftdi_sio` driver
treats both the same. Two places where a clone can differ:

- the 10400 baud rate for ISO 9141-2, which some clones miss;
- the latency-timer and break requests the handheld sends over USB host
  (**[unverified]** either way).

`hondalog-firstcontact` reports what the chip looks like
(`genuine-looking`, `clone-suspected` or `not-ftdi`). That verdict is read
from the USB descriptors and known counterfeit serial numbers; it is a hint,
not proof.

### Checking it on the laptop

Plug the cable into the laptop only. No car needed.

```sh
lsusb | grep -i 0403
```

| Output | Meaning |
|---|---|
| `ID 0403:6001 Future Technology Devices International, Ltd FT232 Serial (UART) IC` | The right chip |
| `ID 0403:0000 ...` | A clone whose id was zeroed by an old Windows driver. Usable: see [Troubleshooting](troubleshooting.md#lsusb-shows-04030000) |
| nothing, but `1a86:7523` appears | A CH340 cable. It works; pick its `/dev/ttyUSB0` by hand |
| nothing new at all | Dead cable or dead USB port |

```sh
sudo dmesg | tail          # want: FTDI USB Serial Device converter now attached to ttyUSB0
ls -l /dev/hondalog        # the udev link install.sh set up: -> ttyUSB0
```

The cable's car side is powered from the car (OBD2 pin 16). On the bench,
with nothing on the OBD2 plug, **it does not echo and looks dead**. That is
normal.

### The OBD2 plug

| Pin | Is |
|---|---|
| 4, 5 | Ground |
| 7 | K-line: the one data wire |
| 16 | Battery +12 V, always live |

### Finding the OBD2 socket

A 16-pin socket inside the cabin. **Its place in a 2000 Civic is
[unverified] here**: the project's own notes only say "under the dash", and
descriptions of this generation disagree between

- under the dash on the driver's side, near the steering column, and
- at the front of the centre console on the passenger's side, behind a small
  cover or low in the footwell.

Look in both places with a torch before cable day and write down where it is.

## 2. The black box

**Why.** The K-line cannot show a power dropout: when the ECU loses power it
just stops answering. The black box is a separate recorder that watches the
wires themselves, 1000 times a second, and saves the 30 seconds before and
10 seconds after a stall to a microSD card. It works with no laptop in the
car. What its captures mean is in [Stall hunting](stall-hunting.md).

**What it is.** An ESP32-S3 dev board (with PSRAM) and a handful of
resistors, optocouplers and diodes on a prototyping board.

### What it watches

| # | Signal | Tapped at | Tells you |
|---|---|---|---|
| 1 | Battery | a constant-power fuse | whether the whole car lost power |
| 2 | ECU +B | ECU pin B1 (main relay output) | whether the ECU lost its feed |
| 3 | Fuel pump feed | main relay output to the pump | whether the pump lost its feed |
| 4 | IG1 | the fuel pump fuse in the under-dash box | whether the ignition switch dropped |
| 5 | Tach pulses | ECU pin A19 | whether the coil was still being switched |
| 6 | Ignition trigger pulses | ECU pin B13 | whether the ECU was still commanding spark |
| 7 | Injector pulses | ECU pin B11 | whether the ECU was still fuelling |
| 8 | Marker button | a button you mount | "it just stumbled" |

Every one of those pins and colours is **[unverified]**: they come from one
published pinout table and have not been checked against the car.

**The rule the design is built around:** the box must not be able to cause a
stall. Every tap goes through a resistor of 10 kΩ or more at the tap end of
the wire, and the box has its own fuse.

### What to buy

The owner already has the expensive parts. From the
[bill of materials](../firmware/blackbox/HARDWARE.md#bill-of-materials):

| Part | Have | About |
|---|---|---|
| ESP32-S3 dev board with 8 MB PSRAM (N16R8 or N8R8) | yes | |
| 12 V to 5 V automotive buck converter | yes | |
| microSD breakout and a card, 32 GB or smaller, FAT32 | yes | |
| SMBJ24A TVS diode, three PC817 optocouplers | yes | |
| Two 10 F 2.7 V supercapacitors (hold-up when power dies) | no | $4 |
| BAT54S Schottky pairs (ADC clamps), 1N5819, 2N7000 or BSS138, 1N4148, small zeners | partly | $3 |
| Resistors and capacitors (values in the hardware page) | probably | $2 |
| Inline fuse holder, 1 A fuse, add-a-fuse tap | no | $5 |
| Posi-taps or solder sleeves, 22 AWG wire, an enclosure | no | $10 |
| NEO-6M GPS module (optional) | yes | |

Prices are rough guesses.

### Before it goes in the car

1. Read [the hardware page](../firmware/blackbox/HARDWARE.md) through,
   including "Not verified" at the end.
2. Build it and run the
   [bench test](../firmware/blackbox/HARDWARE.md#bench-test-before-it-goes-in-the-car):
   14 steps with a bench supply and a square wave.
3. Calibrate the four rails with `hondalog-bb cal`.
4. Scope the tach and ignition trigger signals before wiring to them.

Build and flash: [Firmware development](firmware-dev.md). Without any
hardware, `hondalog-bbsim` behaves like the box:
[try it](stall-hunting.md#try-it-with-no-hardware).

## 3. The CYD handheld

**Why.** A dashboard for the days the laptop is not in the car: it reads the
ECU over the same KKL cable, shows live values on its own screen, logs every
drive to a microSD card with GPS position, and catches stalls the same way
the laptop does. The laptop imports the card afterwards.

**What it is.** A Sunton **ESP32-4827S043N** ("CYD", cheap yellow display):
ESP32-S3 with 16 MB flash and 8 MB PSRAM, a 4.3 inch 480x272 panel with no
touch, a microSD slot, and USB-C through a CH340C serial chip. The owner has
one. How to flash and use it: [Handheld](handheld.md).

### What to buy

For the recommended build ("plan A": the KKL cable plugs into a USB-A socket
wired to the board):

| Part | Have | About |
|---|---|---|
| Sunton ESP32-4827S043N | yes | |
| microSD card, 32 GB or smaller, FAT32 | yes | |
| KKL 409.1 cable (shared with the laptop) | yes | |
| USB-A female pigtail or breakout board | no | $1.50 |
| A second KKL 409.1 cable, so one stays with the laptop | no | $12 |
| JST 1.0 mm and 1.25 mm 4-pin pigtails, if none came with the board | maybe | $2 |
| NEO-6M GPS module with antenna | yes (shared with the black box) | $5 |
| USB car charger (1 A or more) and a USB-C cable | probably | $5 |
| Momentary button, for the optional second button | probably | |

About $3.50 of new parts, plus the second cable. Plan B (a K-line driver chip instead of the cable)
is in the [hardware page](../firmware/handheld/HARDWARE.md#k-line-plan-b-l9637d-on-a-uart-fallback).

### The four headers

Four 4-pin sockets on the back of the board:

| Socket | Pin 1 | Pin 2 | Pin 3 | Pin 4 | Used for |
|---|---|---|---|---|---|
| P1 | 5 V | GPIO43 (TX0) | GPIO44 (RX0) | GND | 5 V and GND for the USB-A socket and the GPS; the GPS data wire in the `--gps-p1` build |
| P2 | GPIO19 | GPIO11 | GPIO12 | GPIO13 | nothing (leave pin 1 open) |
| P3 | GPIO17 | GPIO18 | GPIO19 (USB D-) | GPIO20 (USB D+) | the K-line on pins 3 and 4; the GPS on pin 2 (recommended); second button on pin 1 |
| P4 | GND | 3V3 | GPIO17 | GPIO18 | nothing (same two GPIOs as P3; a different connector) |

**Which end of each socket is pin 1 is [unverified].** Two sources list the
pins in opposite orders. Before plugging anything in, find GND on P1 and P4
by continuity to the USB-C shell, then find 5 V and 3.3 V with the board
powered, and mark the board.

### Check R3 and R4 before wiring USB host

The schematic for this board family has 4.7 kΩ pull-up resistors to 3.3 V on
GPIO19 and GPIO20 (R4 and R3), there for the touch controller of the
touch-screen variants. Whether the factory fits them on the no-touch N board
is **[unverified]**. USB cannot work with them in place.

**Board unpowered, multimeter on ohms:**

| Measure | Reads | Then |
|---|---|---|
| GPIO19 (P3 pin 3) to 3V3 (P4 pin 2), then GPIO20 (P3 pin 4) to 3V3 | open, or megohms | Ready for plan A |
| | about 4.7 kΩ | Remove R3 and R4 (two small surface-mount resistors) first, or use plan B |

Do this before buying or soldering anything for plan A.

### Using the CYD in the car without the laptop

The summary is below; the full wiring, settings and trade-offs are in
[Handheld](handheld.md#using-the-cyd-in-the-car-without-the-laptop).

#### No USB-C adapter

**The KKL cable cannot plug into the CYD's USB-C socket, with or without an
adapter.** That socket goes to the board's CH340 serial chip, which is a USB
device; the cable is a USB device too, and two devices cannot talk. The
ESP32-S3's USB *host* pins are GPIO19 and GPIO20, which only reach header
P3. Two ways to hook up follow from that.

#### Option 1 and option 2

| | Option 1 (start here) | Option 2 |
|---|---|---|
| K-line | KKL cable into a USB-A female pigtail: white D- to P3 pin 3 (GPIO19), green D+ to P3 pin 4 (GPIO20), red and black to 5 V and GND on P1 pins 1 and 4 | OBD2 male pigtail: pin 7 to an L9637D driver, to P3 GPIO19/20 as UART2 (`backend = uart` in `HANDHELD.CFG`) |
| Power | USB car charger into the CYD's USB-C | OBD2 pin 16 through a 1 A fuse and a 12 V to 5 V buck converter; pins 4 and 5 to GND |
| Extra to buy | A **second KKL cable**, so one stays with the laptop | L9637D, passives, buck converter, fuse, OBD2 pigtail |
| Watch out for | R3/R4 (above): check them first. Whether P1 pin 1 gives 5 V out, and which end of P3 is pin 1, are **[unverified]**: measure both | Pin 16 is always live, so the handheld must deep-sleep (`sleep_after_s`, never 0) or it drains the battery |

#### GPS placement

The GPS module only transmits: one data wire into a UART receive pin, plus
power and ground.

| Placement | Firmware build | Notes |
|---|---|---|
| **Now:** GPS TX on P1 pin 3 (GPIO44, RX0) | `--gps-p1` | One-way serial only, the console is off, and the GPS shares its wire with the CH340 through the 100 Ω series resistors. Watch `GPS OK/BAD` on the Link screen |
| **Recommended:** GPS TX on **P3 pin 2 (GPIO18)**, power from P1 pins 1 and 4 | normal | Frees UART0: the console comes back and the serial link to the laptop can be two-way |
| P4 (GND, 3V3, GPIO17, GPIO18) | normal | The pins are right, but the socket is a different connector: the GPS's stock cable does not fit |
| The SD card's SPI pins on P2 | - | Not possible: the card uses them |

> **Warning: never move the same GPS cable between P1 and P4.**
> The two sockets have power and ground at opposite ends: P1 is
> 5 V, TX0, RX0, GND and P4 is GND, 3V3, IO17, IO18. A cable wired for one
> and pushed onto the other reverses the supply to the GPS module and can
> destroy it. Re-pin the cable for the socket it goes into, and check it
> with a meter.

P1's 5 V pin is described as a power *input*. Whether it also gives 5 V
*out* when the board is fed through USB-C is **[unverified]**, and both GPS
placements above rely on it. Measure it (bring-up step 3).

#### Laptop link

With the CYD in the car on its own, its drives come back to the laptop on
the SD card ([Handheld](handheld.md#importing-drives)). A serial link over
the USB-C socket is being added (coming:
[firmware/handheld/SERIAL.md](../firmware/handheld/SERIAL.md), on another
branch). It uses UART0, which is why the GPS should move off P1. Wi-Fi
access-point mode is a possible later transport; running the radio may
glitch the display, so it would be for downloading, not for driving.

#### Flashing

From Linux or WSL, `scripts/build-firmware.sh handheld [--gps-p1] [--flash
PORT]`. From Windows, PlatformIO's esptool with `PYTHONUTF8=1` set:
`python -m esptool --chip esp32s3 --port COMx write-flash 0x0 merged.bin`.
Both are spelled out in [Handheld](handheld.md#flashing).

### Power

Start with a USB car charger into the USB-C socket. The accessory socket is
switched with the key (**[unverified]** for this car), so power is cut with
no warning at key-off; the log format is built to survive that
([Handheld](handheld.md#what-a-power-cut-costs)). The alternative, a buck
converter from OBD2 pin 16, is in the
[hardware page](../firmware/handheld/HARDWARE.md#power).

### Bring-up

The hardware page has a
[15-step bring-up list](../firmware/handheld/HARDWARE.md#bring-up-in-order):
header pins, pull-ups, display colours, SD card, buttons, GPS, USB host,
echo, ECU, key-off. Stop at the first step that fails.
