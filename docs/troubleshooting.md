# Troubleshooting

Symptoms and fixes, grouped by where the problem shows. Each entry says
what to check first and what to do. When the fix needs more explanation,
it links to the page that has it.

- [The cable](#the-cable)
- [The ECU](#the-ecu)
- [The app](#the-app)
- [Recording and data](#recording-and-data)
- [The black box](#the-black-box)
- [The handheld](#the-handheld)
- [Building and flashing firmware](#building-and-flashing-firmware)
- [WSL2](#wsl2)
- [Asking for help](#asking-for-help)

Most of this page is about software that has only run against the
simulators. The car-side entries say what *should* happen; they have not
been confirmed on this car yet.

## The cable

Start with three commands, cable in the laptop:

```sh
lsusb | grep -i -e 0403 -e 1a86   # which USB chip
sudo dmesg | tail                 # did a driver attach, and to what
ls -l /dev/hondalog /dev/ttyUSB*  # the udev link and the port
```

### Permission denied on /dev/ttyUSB0

You are not in the `dialout` group yet, or you have not logged out since
`install.sh` added you. Check with `groups`.

```sh
sudo usermod -aG dialout $USER    # install.sh already did this
```

Then log out and back in (a new terminal is not enough).
`hondalog-firstcontact` reports this as the verdict `no port`.

### The cable appears and vanishes; dmesg mentions brltty

The braille display driver `brltty` grabs FTDI adapters. Remove it and
replug the cable:

```sh
sudo apt remove brltty
```

`install.sh` prints a warning when it finds `brltty` installed.

### lsusb shows nothing new

Dead cable or dead USB port. Try another port, and another cable if you
have one.

### lsusb shows 0403:0000

A clone FTDI chip whose product id an old Windows driver has zeroed. It
works on Linux once the driver is told about it:

```sh
sudo modprobe ftdi_sio
echo 0403 0000 | sudo tee /sys/bus/usb-serial/drivers/ftdi_sio/new_id
```

It then appears as `/dev/ttyUSB0`. The `/dev/hondalog` link only matches
`0403:6001`, so pick `/dev/ttyUSB0` in the ECU chip menu (or "Other port"),
or start with `hondalog --port /dev/ttyUSB0 --connect`. The `echo` has to
be repeated after every reboot.

### lsusb shows 1a86:7523 (CH340) instead of FTDI

Some "KKL" cables use a CH340. Honda reads at 9600 baud work the same: pick
its `/dev/ttyUSB0` in the ECU chip menu. Standard OBD2 (ISO 9141-2) needs
10400 baud, which most CH340 cables get wrong. If that matters, buy the FTDI
cable ([Hardware](hardware.md#what-to-buy)).

`1a86:7523` is also what the CYD handheld's USB-C socket looks like. If the
CYD is plugged in, that is probably what you are seeing.

### The cable is plugged in but does not echo

Normal on the bench. The cable's K-line side is powered from the car
(OBD2 pin 16): with nothing on the OBD2 plug it echoes nothing and looks
dead. Test it in the car.

### Clone or genuine FTDI

Both work for Honda reads on Linux; the kernel driver treats them alike.
`hondalog-firstcontact` reports `genuine-looking`, `clone-suspected` or
`not-ftdi` from the USB descriptors. It is a hint, not proof. A clone is
the first suspect if the ISO 9141-2 init (10400 baud) fails.

## The ECU

### The ECU chip is red (ECU SILENT)

Click the chip: its menu shows the last error. With the app disconnected,
**Run first contact** in the same menu (or `hondalog-firstcontact` in a
terminal) gives a verdict and a report; what each verdict means is in
[Cable day](monday-first-contact.md#step-4-read-the-verdict).

| Last error | Meaning | Do |
|---|---|---|
| `no bytes at all, not even the echo` | The cable's car side has no power | Push the OBD2 plug fully in. Measure 12 V between OBD2 pin 16 and pins 4/5; if it is missing, find the fuse. Check the battery is connected |
| `echo only, the ECU did not answer` | The cable works; the ECU is quiet | Key in ON (II), not ACC. Try with the engine running and with it off. Then the `[protocol]` settings below |
| `bad checksum in reply ...` or `no reply frame in ...` | Bytes come back but not in the expected `00 len+3 data cksum` shape | Copy what the hex console (Raw tools, F5) shows and the first contact `.hex.log` |

**`[protocol]` settings to try**, in your map
(`~/.config/hondalog/addressmap.toml`), one at a time, reconnecting after
each:

```toml
[protocol]
init_sequence = ""        # no wake-up bytes
reply_timeout_s = 0.5     # a slower ECU
max_read_len = 8          # an ECU that ignores long reads
```

First contact writes the settings it found to work into its report
("suggested `[protocol]` settings"). The framing constants are in
`hondalog/protocol.py`, class `Framing`.

Whether the P2P answers the Honda read at all is **[unverified]**. If first
contact says `ISO only`, the ECU speaks standard OBD2 but not the Honda
protocol on pin 7; see
[Cable day](monday-first-contact.md#iso-only).

### The error count keeps climbing but data shows

A few errors are normal on a K-line. Many: a poor ground, a loose OBD2 plug,
or a long USB extension. The ECU chip menu shows timeouts, checksum errors
and retries separately; `hondalog-log info DRIVE` shows the link counters
recorded with a drive.

### Values look wrong

Expected until the map is verified: it is a P2T map, not a P2P one. Every
unverified channel shows a `?`. See [Mapping the P2P](mapping-the-p2p.md).
Drives keep their raw bytes, so once a channel is fixed,
`hondalog-log redecode DRIVE` decodes old drives again with the new map.

### Honda reads do not come back after an OBD2 session

After an ISO 9141-2 session (codes, freeze frame) the app goes back to Honda
reads by itself. In the simulator that works without a power cycle; on the
real P2P it is **[unverified]**. If the ECU stays silent, turn the key off
and on.

### The poll rate is lower than expected

About 17 Hz on the fast channels is the target with the shipped map; 20 Hz
does not fit on a 9600 baud wire. The ECU chip menu shows the rate per rate
class against its target. Check that the FTDI latency timer is 1 ms:

```sh
cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer   # want: 1
```

If it says 16, the udev rule is missing: `hondalog-firstcontact --udev`
prints it, and `install.sh` installs it as
`/etc/udev/rules.d/99-hondalog.rules`. Replug the cable afterwards.

## The app

### could not load the Qt platform plugin "xcb"

```sh
sudo apt install libxcb-cursor0 qt6-qpa-plugins
```

### Map 3D or Tune is blank, slow, or the window stutters

OpenGL is missing or in software. Check with `glxinfo -B`
([Getting started](getting-started.md#check-opengl)); if the renderer is
`llvmpipe`, start with `hondalog --no-gl` (or set `HONDALOG_GL=0`). Both
screens then draw a 2D heat map from the same data.

### The app feels slow on the laptop

`hondalog --profile` shows frame time, CPU and latency in the bottom bar and
prints a summary on exit. Compare with
[UI performance](ui-performance.md). `--no-gl` is the first thing to try.

### The shipped map and my map differ

Your map is `~/.config/hondalog/addressmap.toml`; the app copies the shipped
one there on first start and never overwrites it. To start again from the
shipped map, delete your copy and start the app.
`hondalog-map diff OLD NEW` shows what differs.

## Recording and data

### A drive shows as "crashed"

The app or the laptop died while recording. Everything up to the last
commit (every half second) is in the drive; it opens and exports like any
other. Nothing to fix.

### The database is getting big

```sh
hondalog-log list                    # what is there
hondalog-log prune --days 90 --yes   # delete old drives; kept: captures, notes, tags, keep, the newest
hondalog-log vacuum                  # give the space back to the disk
```

Details: [Data and SQL](data-and-sql.md#deleting-and-keeping).

### rm refuses a drive

A drive that is still being recorded cannot be deleted. A drive marked
keep asks you to type its name (`--force` skips that question).

### redecode says the drive has no raw bytes

Drives imported from CSV, MSL or MLG hold decoded values only. Drives
recorded by the app, by `hondalog-log record` or imported from the
handheld keep the raw bytes.

### An SQL query in another tool locks the app out

Open the database read-only while the app records: `sqlite3 -readonly
~/hondalog/hondalog.db`, or `hondalog-log sql`. Write only through hondalog.

## The black box

### hondalog-bb cannot open /dev/hondalog-bb

The udev link matches the ESP32-S3's built-in USB serial (`303a:1001`). A
dev board connected through its other socket (a USB-UART chip) shows up as
`/dev/ttyUSB0` or `/dev/ttyACM0` instead: pass `--port`. Without hardware,
`--port bbsim:` uses the built-in simulator.

### Nothing on the BLACK BOX tiles

The app looks for `/dev/hondalog-bb` when it connects. Start it with
`hondalog --bb PORT` for any other port.

The box itself has never been built; the bench test in its
[hardware page](../firmware/blackbox/HARDWARE.md#bench-test-before-it-goes-in-the-car)
comes first.

## The handheld

The handheld's own troubleshooting list covers the screen colours, panel
timing, backlight, a dark screen at power-up, GPS, the K-line link, the SD
card and imports: [Handheld](handheld.md#troubleshooting). The two most
likely first problems:

- **Link stays `CONNECTING`** with the KKL cable on the USB-A pigtail: the
  4.7 kΩ pull-ups R3/R4 on GPIO19/20
  ([Hardware](hardware.md#check-r3-and-r4-before-wiring-usb-host)).
- **`GPS OK/BAD` both 0**: the firmware build does not match where the GPS
  is plugged in (`--gps-p1` for P1, the normal build for P3 pin 2).

## Building and flashing firmware

### Where to look

`scripts/build-firmware.sh` writes one log per step to `build-logs/` and
names the failing one in its summary: `<firmware>-host-c.log`,
`-pytest.log`, `-sim.log`, `-build.log`, `-flash.log`.

### HH_IDF_ENV=...: no such file

`HH_IDF_ENV` must name the script that sets ESP-IDF up, usually
`~/esp/esp-idf/export.sh`, not the ESP-IDF folder.

### idf.py does not run after '. ...'

The file was sourced but `idf.py` is still not on the `PATH`: run
`. ~/esp/esp-idf/export.sh` by hand and read its error (usually the ESP-IDF
tools were never installed: `~/esp/esp-idf/install.sh esp32s3`).

### The first build stops while downloading

Without `HH_IDF_ENV`, the first run fetches PlatformIO, the toolchain and
ESP-IDF (about 1.5 GB). It needs internet. Behind a proxy or offline, use an
ESP-IDF you install yourself and an LVGL checkout:
[Firmware development](firmware-dev.md#the-lvgl-path).

### simulator checks fail with png.h: No such file

`sudo apt install libpng-dev`. The script normally installs it.

### Flashing does not start

Hold **BOOT**, press and release reset (or plug the board in), release
BOOT, and run the flash command again. On the CYD the port is the CH340's
`/dev/ttyUSB0`.

### On Windows, esptool crashes with UnicodeEncodeError

The progress bar cannot be printed in the cp1252 code page. Set
`PYTHONUTF8=1` first (`$env:PYTHONUTF8 = "1"` in PowerShell, `set
PYTHONUTF8=1` in `cmd.exe`):
[Handheld](handheld.md#flashing-from-windows).

### esptool says write_flash is not a command (or write-flash)

esptool 5 spells it `write-flash`, esptool 4 `write_flash`. Use the one your
version knows (`python -m esptool version`).

## WSL2

- **No `/dev/ttyUSB0`.** Attach the device with usbipd, then load the
  driver (`sudo modprobe ftdi_sio` or `ch341`):
  [Getting started](getting-started.md#the-cable-or-the-cyd-usbipd).
- **Permission denied.** `wsl --shutdown` in PowerShell, then start WSL
  again so the `dialout` group applies.
- **Map 3D misbehaves.** `hondalog --no-gl`.

## Asking for help

Send:

1. the two first contact files from `~/hondalog/logs/`
   (`firstcontact-DATE.md` and `firstcontact-DATE.hex.log`),
2. for a recording problem, `hondalog-log info DRIVE`,
3. for a firmware problem, the failing log from `build-logs/`,
4. what you expected and what happened instead.
