# Glossary

Car, ECU and electronics terms as this project uses them, then hondalog's
own words. Pin numbers and wire colours that are not yet checked on the car
are marked **[unverified]** on the pages that use them, not here.

## The car and its ECU

**ACC, ON (II)**
Key positions. ACC powers the accessories only; ON (II) powers the ECU.
hondalog needs ON.

**D16Y8**
The 1.6 litre SOHC VTEC engine of the 1996-2000 Civic EX. The car here has
it with a 5-speed manual gearbox.

**DLC**
Data link connector: the socket a scan tool plugs into. Here, the 16-pin
OBD2 socket. Hondas also have a small 3-pin service connector (the "Honda
DLC"); kerpz tested the Honda protocol through one on a P2T. Whether this
car has one that carries the K-line is **[unverified]**.

**DTC, MIL**
Diagnostic trouble code (for example P0505, or a Honda-specific P1xxx), and
the malfunction indicator lamp ("check engine") that lights for it.

**ECT, IAT**
Engine coolant temperature and intake air temperature, in °C.

**ECU**
Engine control unit: the computer that runs fuel and ignition. Honda calls
the system PGM-FI.

**ECU +B**
The ECU's power feed from the main relay. The black box watches it; the pin
used is B1 per the project's pinout table.

**IACV**
Idle air control valve: the ECU's way to hold idle speed.

**IG1**
The ignition-switched supply. If IG1 drops, the key switch (or its wiring)
cut the power.

**Main relay**
The PGM-FI main relay: a double relay that feeds the ECU and the fuel pump.
A failing one cuts the ECU or the pump without warning, which is why the
black box watches both of its outputs.

**MAP**
Manifold absolute pressure, in kPa. About 30 kPa at idle, near the
barometric pressure (about 100 kPa at sea level) at full throttle. The load
axis of Honda fuel and ignition tables.

**O2, STFT, LTFT**
The oxygen sensor's voltage, and the short-term and long-term fuel trims:
the corrections the ECU adds, in %, to hit the mixture the O2 sensor asks
for. **Closed loop** is when it uses the O2 sensor; open loop is when it
does not (cold engine, full throttle).

**OBD1, OBD2, OBD2A, OBD2B**
Generations of on-board diagnostics. For Hondas: OBD1 is 1992-1995, OBD2A is
1996-1998 and OBD2B 1999-2000. The ECUs, the connectors and the protocols
differ. This car is OBD2B.

**P2P**
The part code of this car's stock ECU (2000 Civic EX, D16Y8, manual
gearbox, OBD2B). Nobody has published a RAM map for it; working one out is
what [Mapping the P2P](mapping-the-p2p.md) is for.

**P2T**
Another OBD2 Honda ECU. The kerpz map that hondalog ships as its starting
guess was tested on a P2T.

**P28**
The OBD1 ECU of the 1992-1995 Civic EX and Si. It takes a socketed ROM chip,
so its tables can be edited and burned: Track C, [Tuning a P28](tuning-p28.md).

**PGM-FI**
Programmed fuel injection: Honda's name for its electronic fuel injection
system.

**TPS, VSS**
Throttle position sensor (in %) and vehicle speed sensor.

**VTEC**
Honda's variable valve timing and lift: a second cam profile that the ECU
switches in above a set RPM and load. On the D16Y8 it works on the intake
valves.

## Wires and protocols

**Echo**
On the single-wire K-line every byte sent comes straight back. hondalog
uses it to tell a dead cable (no echo) from a quiet ECU (echo only).

**Honda diagnostic protocol**
The read that hondalog uses: `20 05 addr len cksum` at 9600 baud, 8N1,
answered by `00 len+3 data cksum`. It reads ECU RAM directly. Whether a P2P
answers it on OBD2 pin 7 is **[unverified]**.

**ISO 9141-2**
The standard OBD2 protocol on the K-line, at 10400 baud, opened with a
5-baud "slow init" to address 0x33. Gives standard PIDs, codes, freeze frame
and readiness, but slowly. **KWP2000 fast init** is a quicker way to open a
session on the same wire, tried as a fallback.

**K-line**
A single wire (OBD2 pin 7) that carries serial data both ways, pulled up to
12 V. The **L-line** is an optional second wire for the init; this project
does not use one.

**KKL cable, KKL 409.1**
A cheap USB-to-K-line cable made for VAG-COM (Volkswagen). Electrically the
K-line is the same on a Honda. It holds a USB-serial chip (ideally an FTDI
FT232RL) and the level shifter, and takes its 12 V from OBD2 pin 16.

**FTDI, FT232RL**
The USB-serial chip in a good KKL cable, USB id `0403:6001`. Clones are
common; on Linux they work the same for Honda reads.

**CH340, CH340C**
A cheaper USB-serial chip from WCH, USB id `1a86:7523`. Some KKL cables use
one. The CYD's USB-C socket goes to a CH340C, which is how it is flashed.

**Latency timer**
An FTDI chip setting: how long it waits before passing a short burst of
received bytes to the computer. The default 16 ms costs up to 16 ms of dead
time on every request; hondalog's udev rule sets 1 ms.

**L9637D**
An ST K-line transceiver chip. Handheld option 2 uses one to put the K-line
on a UART without a KKL cable.

**PID**
Parameter ID: a standard OBD2 value (RPM, coolant, MAP...) asked for by
number over ISO 9141-2.

**Readiness monitors**
OBD2 self-tests the ECU reports as complete or not. Clearing codes resets
them.

**udev rule**
A Linux rule that acts when a device is plugged in. hondalog's
(`/etc/udev/rules.d/99-hondalog.rules`) names the cable `/dev/hondalog`, the
black box `/dev/hondalog-bb`, and sets the FTDI latency timer to 1 ms.

## Electronics and firmware

**CYD**
"Cheap yellow display": the nickname for Sunton's ESP32 boards with a
screen. This project's is the **Sunton ESP32-4827S043N**: ESP32-S3, 4.3 inch
480x272 panel, no touch, microSD slot.

**ESP32-S3**
The Espressif microcontroller in both the black box and the CYD. It can be
a USB host on GPIO19 and GPIO20.

**ESP-IDF, PlatformIO**
ESP-IDF is Espressif's own build system and libraries (version 5.3 here).
PlatformIO is a build tool that downloads and drives ESP-IDF for you.
`scripts/build-firmware.sh` uses either.

**esptool**
Espressif's flashing program. Version 5 spells its commands `write-flash`,
`merge-bin`; version 4 `write_flash`, `merge_bin`.

**LVGL**
The graphics library the handheld's screens are drawn with (version 9.2.2).

**N16R8**
An ESP32-S3 module with 16 MB of flash and 8 MB of PSRAM (extra RAM). N8R8
has 8 MB of flash.

**P1, P2, P3, P4**
The CYD's four 4-pin header sockets. See [Hardware](hardware.md#the-four-headers).

**R3, R4**
4.7 kΩ pull-up resistors on GPIO19 and GPIO20 in the board family's
schematic. If fitted, USB host cannot work; measure before wiring.

**NEO-6M, NMEA**
The GPS module, and the text sentences it sends (`$GPRMC` and so on) at 9600
baud.

**TVS diode, buck converter, supercapacitor**
Protection against voltage spikes on the car's 12 V; a 12 V to 5 V switching
regulator; a capacitor large enough to keep the black box running for a
few seconds after power is lost.

## Tuning (Track C)

**ROM, chip, burner**
The P28's program and tables live in a ROM chip (here an SST27SF512). A
burner (TL866II+ or T48, driven by `minipro`) writes a new image to it.

**Definition, XDF**
A file that says where each table sits in a ROM and how to scale it.
TunerPro's format is `.xdf`; hondalog also has its own `.toml` form.

**Ostrich**
Moates' ROM emulator: it stands in for the chip so tables can be changed
while the engine runs. Supported in code, not tried on hardware.

**Wideband, AFR, lambda**
A wideband O2 sensor measures the air-fuel ratio (AFR) across the whole
range; lambda is AFR divided by the stoichiometric ratio (1.0 = 14.7:1 for
petrol). The stock narrowband O2 sensor only says rich or lean.

## hondalog's own words

**Address map**
The file that says which ECU RAM address holds which value and how to turn
the bytes into units: `~/.config/hondalog/addressmap.toml`. It also holds
math channels, alarms and the `[protocol]` settings.

**Channel**
One value with a name: `rpm`, `ect`, `bb_ecub`. ECU channels come from the
map, `bb_` channels from the black box, math channels from expressions.

**Verified, unverified**
A channel is unverified (a `?` on its tile) until a check on the car shows
its address and formula are right. See [Mapping the P2P](mapping-the-p2p.md).

**Math channel**
A channel computed from others with an expression, such as injector duty
or total trim.

**Alarm**
A rule on a channel (too high, too low, too fast a change) that colours its
tile, shows a banner and can drop a marker or save a capture.

**Trigger**
A rule that saves a capture. The stall catcher is two trigger rules.

**Drive**
One recording session in the database, from connect (or key on) to
disconnect or two minutes of ECU silence. Named like
`drive_2026_11_16_0712`.

**Capture**
A window of a drive saved around a trigger: by default 30 s before and 10 s
after. Stall captures are named like `stall_20261116_071455`.

**Marker, MARK**
A moment you tagged (Space, or the MARK button): "it just stumbled".

**Redecode**
Decoding a drive's stored raw bytes again with a newer address map.

**Tracks A, B, C**
A: the KKL cable and the laptop. B: the black box. C: the chipped P28.

**Black box**
The ESP32-S3 recorder wired to the car's power rails and ignition signals.
Its captures are `.bbx` files.

**Handheld**
The CYD running hondalog's firmware. Its files on the SD card are
`HANDHELD.MAP`, `HANDHELD.CFG` and `.HHD` drives and captures.

**First contact**
`hondalog-firstcontact`: the read-only check that finds out which protocol
the ECU answers.

**Simulator**
A fake ECU (`hondalog-sim`, or the port `sim:`) or black box
(`hondalog-bbsim`, `bbsim:`) that behaves like the real thing on the wire.

**MLG, MSL**
MegaLogViewer's binary and text log formats. hondalog exports both.

**WAL**
SQLite's write-ahead log, the `-wal` file beside `hondalog.db` while it is
open. It is what makes a crash cost at most the last commit.
