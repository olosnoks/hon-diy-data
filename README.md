# hondalog

Live data logger for a 2000 Honda Civic EX (D16Y8, OBD2B) over a cheap
KKL 409.1 USB cable (FTDI FT232RL). Linux desktop app: Python, PySide6,
pyqtgraph, pyserial. Built for Debian 13 on an old ThinkPad.

It reads ECU RAM with the Honda diagnostic protocol on the K-line
(`20 05 addr len cksum`, 9600 8N1), shows it, logs it to a crash-safe native
log (`.hlog`), and saves a separate capture when the engine stalls or another
trigger fires. Logs export to MegaLogViewer (`.mlg`, `.msl`) and CSV.

![dashboard](docs/screenshot.png)

**Read this first:** the address map is a guess. It is the kerpz P2T map,
and nobody has checked it on a P2P. Every channel shows an amber `?` until
you mark it verified. Until then, treat the numbers as "probably".

## Quickstart

### 1. Install

```sh
tar xzf hondalog-0.1.0.tar.gz
cd hondalog-0.1.0
./install.sh
```

The installer uses sudo. It installs the Debian packages, copies the app to
`/opt/hondalog`, adds you to the `dialout` group, adds a udev rule so the
cable shows up as `/dev/hondalog`, and adds a launcher to the menu.

**Log out and back in afterwards.** The `dialout` group only applies to new
logins, and without it you cannot open the cable. The simulator works
straight away.

### 2. Try it with the simulator (no car)

Terminal 1:

```sh
hondalog-sim
```

Terminal 2:

```sh
hondalog --port /tmp/hondalog-sim --connect
```

The simulator is a fake ECU on a pseudo-terminal. It echoes like a real
K-line, warms up, idles around 750 rpm and revs every 20 seconds. Keys in
the simulator terminal:

| key | what happens |
|-----|--------------|
| `s` | stall: RPM drops to 0, ECU keeps answering |
| `p` | power cut: ECU goes silent (like a dead main relay) |
| `r` | restart |
| `v` | rev |
| `q` | quit |

`hondalog-sim --stall-after 30` stalls on a timer. `--help` lists the rest.
In a hurry, pick the `sim:` port in the app: that is the same fake ECU
without the pty.

### 3. In the car

1. Plug the cable into the laptop. Check that Linux sees it:

   ```sh
   lsusb | grep 0403        # want: 0403:6001 ... FT232 Serial (UART) IC
   sudo dmesg | tail        # want: FTDI USB Serial Device converter now attached to ttyUSB0
   ls -l /dev/hondalog      # the udev link
   ```

2. Plug the other end into the 16-pin OBD2 port under the dash.
3. Key to ON (II). The engine can be off or running.
4. Start `hondalog`. The port box should already show `/dev/hondalog`.
   Press **Connect**.
5. The status box turns green (`ECU OK`), READS counts up, and RATE shows
   about 10 Hz.

If it stays red, go to Troubleshooting.

## Using it

**Recording.** Starts on connect (untick "Record on connect" to stop that).
Logs go to `~/hondalog/logs/log_DATE_TIME.hlog`. The file holds the ECU id
bytes, the whole address map, the units, markers and every channel with its
own timestamps. It is written every half second, so a dead laptop battery
costs at most the last second. A recording that runs longer than an hour
continues in `..._p002.hlog` and so on. Logs from 0.1.0 (`.csv`) still open.

**Marker.** Press **Space** or the big amber button when something happens
("it just stumbled"). The log gets a `MARK n` event at that moment and a line
appears on the charts. Space types a normal space while
the cursor is in a text box.

**Stall catcher.** While recording, once RPM has been above 400:

- RPM reads 0, or
- the ECU stops answering for 1 second (power loss, cable pulled),

the app saves the 30 seconds before and the 10 seconds after to
`~/hondalog/logs/stall_DATE_TIME.hlog`, shows a red banner and beeps.
"Open in replay" loads the file. Turning the key off with the engine
running counts as a stall too; ignore that one or press Stop first.

**Charts.** "Plot channels" picks what is drawn (up to 8). "Window" sets how
much history scrolls by.

The stall catcher is two rules of a general trigger engine
(`hondalog/triggers/`). A rule is a condition on any channels, a hold time,
and how much to keep before and after, for example
`when = "ect > 105"`, `hold_s = 2`. Rules for low battery, a lean spike and
overheating ship switched off, because they depend on channels that have not
been verified on the car yet.

**Replay tab.** Open any log or stall file (`.hlog`, old `.csv`, MegaLogViewer
`.msl`/`.mlg`). "Export…" saves the open log as `.mlg`, `.msl`, `.csv` or `.npz`. Drag the slider or click in a
chart to move the cursor. The table shows every channel at that moment.
Click a marker in the list to jump to it. Mouse wheel zooms the time axis.

**Raw tools tab.**

- *Hex console*: type bytes, e.g. `20 05 00 10`. "Add checksum" appends the
  last byte for you. It shows what came back after the echo and whether the
  checksum adds up.
- *Memory scanner*: sweeps an address range in chunks and shows a hex grid.
  Orange = changed in this sweep. Blue = changed earlier. Click a byte for
  its minimum, maximum and change count. "Export" writes a `.txt` dump and a
  `.csv` with every sweep.

### Mapping the P2P

1. Engine warm and idling. Raw tools, "Sweep continuously".
2. Press "Clear changes", then change one thing only: blip the throttle,
   switch on the headlights, turn on the A/C.
3. Watch which bytes turn orange. Compare with the guess in the map
   (RPM at 0x00-0x01, TPS at 0x14, battery at 0x17, and so on).
4. "Edit address map" (bottom right) opens
   `~/.config/hondalog/addressmap.toml`. Fix the address or formula, set
   `status = "verified"`, bump `version`, save.
5. Disconnect and connect again to load it.

The file explains its own fields. Formulas are plain arithmetic on `x`
(the raw number). If the file has an error the app tells you and uses the
built-in map until you fix it.

Temperatures are in °C, pressure in kPa, speed in km/h. Change the formula
and units in the map if you want others.

## Troubleshooting

**Permission denied on /dev/ttyUSB0.** You are not in `dialout` yet, or you
have not logged out since the installer added you. Check with `groups`.
Fix: `sudo usermod -aG dialout $USER`, then log out and in.

**The cable appears and vanishes; dmesg mentions `brltty`.** The braille
driver grabs FTDI adapters. `sudo apt remove brltty`, then replug.

**lsusb shows nothing new.** Dead cable or dead USB port. Try another port.

**lsusb shows `0403:0000`.** A clone FTDI chip that an old Windows driver
has zeroed. It still works on Linux if you tell the driver about it:

```sh
sudo modprobe ftdi_sio
echo 0403 0000 | sudo tee /sys/bus/usb-serial/drivers/ftdi_sio/new_id
```

It then appears as `/dev/ttyUSB0`. Type that into the port box (the
`/dev/hondalog` link only matches `0403:6001`).

**lsusb shows `1a86:7523` (CH340) instead of FTDI.** Some "KKL" cables use
that chip. It works the same. Pick its `/dev/ttyUSB0` entry in the port box.

**Clone FTDI chips in general** are fine on Linux. The kernel driver does
not care.

**Status says NO REPLY.** Hover over the status box to see the last error.

- *"no bytes at all, not even the echo"*: the cable's car side is dead.
  It is powered from the OBD2 plug (pin 16), so it only echoes when it is
  plugged into the car. Check the plug is fully seated and the car battery
  is connected.
- *"echo only, the ECU did not answer"*: the cable works, the ECU is quiet.
  - Key must be ON (II), not ACC.
  - Try it with the engine running, and with key on, engine off.
  - Check the ECU sticker (passenger footwell, behind the kick panel). This
    was written for a P2P. A different ECU may want different framing.
  - In Raw tools send `20 05 00 10` by hand and look at what comes back.
  - Edit the address map, `[protocol]` section: try `init_sequence = ""`
    (no wake-up bytes), or `reply_timeout_s = 0.5`.
- *"bad checksum"* or *"no reply frame"*: bytes arrive but do not fit the
  expected `00 len+3 data cksum` shape. Copy what the hex console shows.
  The framing constants are all in `hondalog/protocol.py` (class `Framing`)
  and can be overridden in the map's `[protocol]` section.

**ERRORS keeps climbing but data shows.** A few errors are normal on a noisy
line. Lots of them: bad ground or a poor cable. The log's `link` column is 0
for sweeps where nothing valid came back.

**Values look wrong.** Expected, the map is a guess. See "Mapping the P2P".

**Window does not start: `could not load the Qt platform plugin "xcb"`.**
`sudo apt install libxcb-cursor0 qt6-qpa-plugins`.

## Log files from the command line

```sh
hondalog-log info    FILE                 # streams, rates, markers, header
hondalog-log import  old_log.csv          # 0.1.0 CSV, MSL or MLG -> .hlog
hondalog-log export  FILE out.mlg         # also .msl .csv .npz .hlog
hondalog-log export  FILE out.csv --channels rpm,map,stft --filter "ect > 80" --hz 10
hondalog-log repair  FILE                 # finish a log cut off by a crash
hondalog-log report  stall_....hlog       # one-page HTML report
hondalog-log redecode FILE out.hlog       # decode the raw bytes with your current map
hondalog-log list    [-q words]           # search names, notes, markers
hondalog-log prune   --days 90 --yes      # delete old session logs (captures are kept)
```

MLG files follow EFI Analytics' published MLG 2.0 layout and are checked
byte by byte in the tests. Nobody has opened one in MegaLogViewer from this
project yet. Parquet is not offered: Debian trixie has no `python3-pyarrow`.

`redecode` needs a log that holds raw bytes. Those are stored when the
poller hands over single reads (`Recorder.feed_read`); a log recorded sweep
by sweep has decoded values only.

## Files

| what | where |
|------|-------|
| logs and stall captures | `~/hondalog/logs/` |
| address map (edit this) | `~/.config/hondalog/addressmap.toml` |
| shipped default map | `/opt/hondalog/hondalog/data/addressmap.toml` |
| program | `/opt/hondalog/` |
| udev rule | `/etc/udev/rules.d/99-hondalog.rules` |

To get the shipped map back, delete your copy and start the app.

Code layout: `protocol.py` (framing and checksum, no I/O), `transport.py`
(serial port, built-in simulator, request/reply), `poller.py` (worker
thread), `addressmap.py`, `logger.py` (the recorder), `data/` (native log format,
reader, library, retention), `triggers/` (trigger engine, stall presets),
`analysis/` (filters, table histogram, scatter, cursors, compare, plot
decimation), `export/` (MLG, MSL, CSV, importers), `logtool.py`
(`hondalog-log`), `sim.py` (fake ECU), `ui/` (Qt). `iso9141.py` is an empty stub for standard
OBD2 PIDs.

## Tests

```sh
cd /opt/hondalog
python3 -m pytest                                   # unit tests
QT_QPA_PLATFORM=offscreen python3 tools/smoke.py    # full run against the simulator
python3 bench/bench_data.py                         # log write/open/histogram timings
```

(If the installer had to build a venv, use `/opt/hondalog/.venv/bin/python`
instead of `python3`, and `pip install pytest` into it first.)

## Not verified yet

Everything was tested against the simulator only. Still to be proven on the
real car:

- that a P2P answers `20 05 addr len cksum` with `00 len+3 data cksum`
- whether it needs the wake-up bytes, and whether they do any harm
- every address and formula in the map
- the ECU id location (0x76, 10 bytes)
- the real polling rate (the simulator gives about 10 Hz)

## Uninstall

```sh
/opt/hondalog/uninstall.sh
```

Logs, settings and the Debian packages stay.
