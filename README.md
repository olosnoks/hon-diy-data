# hondalog

Live data logger for a 2000 Honda Civic EX (D16Y8, OBD2B) over a cheap
KKL 409.1 USB cable (FTDI FT232RL). Linux desktop app: Python, PySide6,
pyqtgraph, pyserial. Built for Debian 13 on an old ThinkPad.

It reads ECU RAM with the Honda diagnostic protocol on the K-line
(`20 05 addr len cksum`, 9600 8N1), shows it, logs it to CSV, and saves a
separate file when the engine stalls.

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
Logs go to `~/hondalog/logs/log_DATE_TIME.csv`. The header lines (starting
with `#`) hold the ECU id bytes, the address map version and the units.

**Marker.** Press **Space** or the big amber button when something happens
("it just stumbled"). The next row in the log gets `MARK n` in the `marker`
column and a line appears on the charts. Space types a normal space while
the cursor is in a text box.

**Stall catcher.** While recording, once RPM has been above 400:

- RPM reads 0, or
- the ECU stops answering for 1 second (power loss, cable pulled),

the app saves the 30 seconds before and the 10 seconds after to
`~/hondalog/logs/stall_DATE_TIME.csv`, shows a red banner and beeps.
"Open in replay" loads the file. Turning the key off with the engine
running counts as a stall too; ignore that one or press Stop first.

**Charts.** "Plot channels" picks what is drawn (up to 8). "Window" sets how
much history scrolls by.

**Replay tab.** Open any log or stall file. Drag the slider or click in a
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

Every address starts as a guess. Three tools turn guesses into verified
channels; all of them work from the terminal (`hondalog-map`, also over ssh)
and the logic is in `hondalog/mapping/` for the UI to use.

**1. Guided checks.** `hondalog-map scripts` lists them; `hondalog-map status`
shows which script can prove which channel.

```sh
hondalog-map verify koeo --port /dev/hondalog          # key on, engine off
hondalog-map verify tps_sweep --port /dev/hondalog     # press the pedal slowly
hondalog-map verify idle --port /dev/hondalog          # warm idle; asks for the tach reading
hondalog-map verify steady_3000 --port /dev/hondalog
hondalog-map verify warmup --port /dev/hondalog        # cold start to warm
hondalog-map verify drive --port /dev/hondalog         # passenger types the speedo reading
hondalog-map verify ac_switch --port /dev/hondalog
```

Each script tells you what to do, records the whole time, and gives a verdict
per channel:

- `pass`: a check that can prove the address held (throttle went from under
  5% to over 90% as you pressed; battery within 0.3 V of your meter).
- `plausible`: the value looks sane but that proves nothing (RPM reads 0 with
  the engine off). Not marked unless you insist (`--force`).
- `fail`: it lists where the signal may really live.
- `skip`: no reading was given, or the check does not apply (engine not cold).

Nothing is marked verified until you say yes. Then the map gets
`status = "verified"` and a `[channel.evidence]` table: date, script, what was
measured, the map version and hash, and the name of the evidence file in
`~/hondalog/verification/` (a report plus every raw byte recorded).

**2. The finder.** Sweep while something changes, then rank every address as
a byte and as a 16-bit value in both byte orders:

```sh
hondalog-map find --port /dev/hondalog --channel rpm --seconds 30   # what moves with RPM?
hondalog-map find --port /dev/hondalog --value 12.4 --formula "x / 10.45"   # what reads 12.4 V?
```

In the app: Raw tools, "Sweep continuously", then "Rank addresses". It prints
a suggested formula (least squares) for each hit. Rank against a channel you
have already verified, or the result is only as good as that guess.

**3. Cross-check with standard OBD2 PIDs** (`hondalog/mapping/crosscheck.py`).
Feed it pairs of readings taken under the same steady conditions and it says
agree, scaled (with a corrected formula) or no match for RPM, ECT, IAT, MAP,
TPS, speed, both trims, O2 and timing. It needs the ISO 9141-2 link, which is
still being built; until then it runs on numbers from any scan tool.

**By hand** still works: "Edit address map" opens the file. Changing an
address or formula through the tools resets that channel to unverified and
bumps the version. `hondalog-map diff old.toml new.toml` shows what changed
and whether old logs would decode differently; `hondalog-map hash` prints the
12-digit hash that identifies a map's decoding.

An old (schema 1) map keeps working as it is. `hondalog-map upgrade` adds the
math channels and alarms to it and keeps your comments.

### Math channels and alarms

Both live in the address map, under `[[math]]` and `[[alarm]]`; the file
explains every field. Shipped math channels: injector duty, fuel flow, total
trim, RPM rate, load, a rough AFR estimate and three filtered values. Shipped
alarms: coolant over 105 °C for 2 s (critical), battery low and high, over-rev,
idle collapsing, lean under load. The limits are starting points, not Honda
figures, and every one of them reads channels that are not verified yet (the
alarm text shows a `?` until they are).

Expressions use `[rpm]` or `rpm` for a channel, `[rpm-1]` for its previous
sample, `a ? b : c`, and helpers such as `avg(x, ms)`, `slope(x, ms)`,
`minw(x, ms)`, `maxw(x, ms)`, `smooth(x, n)`. `min(a, b)` and `max(a, b)`
compare two values, so the time-window versions are spelled `minw`/`maxw`.
Nothing in an expression is run as Python: the text is parsed into a small
tree and only the listed functions exist.

For now the app shows the top alarm at the bottom of the window (click it to
acknowledge) and writes alarm markers into the log. Math channels are computed
but the dashboard does not display them until the new UI lands.

Temperatures are in °C, pressure in kPa, speed in km/h in the map and in logs.
`hondalog/units.py` converts for display (°F, psi, inHg, mph, AFR).

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

## Files

| what | where |
|------|-------|
| logs and stall captures | `~/hondalog/logs/` |
| address map (edit this) | `~/.config/hondalog/addressmap.toml` |
| shipped default map | `/opt/hondalog/hondalog/data/addressmap.toml` |
| program | `/opt/hondalog/` |
| udev rule | `/etc/udev/rules.d/99-hondalog.rules` |

To get the shipped map back, delete your copy and start the app.

Code layout: `expr/` (the safe expression evaluator), `mathch/`, `alarms/`,
`mapping/` (verification, finder, map editing), `live.py` (math + alarms per
sample), `protocol.py` (framing and checksum, no I/O), `transport.py`
(serial port, built-in simulator, request/reply), `poller.py` (worker
thread), `addressmap.py`, `logger.py` (CSV, stall catcher, log reader),
`sim.py` (fake ECU), `ui/` (Qt). `iso9141.py` is an empty stub for standard
OBD2 PIDs.

## Tests

```sh
cd /opt/hondalog
python3 -m pytest                                   # unit tests
python3 -m hondalog.bench.expr_bench                # math/alarm CPU cost against the budget
QT_QPA_PLATFORM=offscreen python3 tools/smoke.py    # full run against the simulator
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
- the verification scripts' limits (idle 550-950 rpm, MAP 20-48 kPa at idle,
  charging 13.2-14.9 V and so on) are ordinary D-series expectations, not
  figures measured on this car
- whether the MIL bit reads 1 during the key-on bulb check
- the alarm limits, the 240 cc/min injector size and the 0.85 VE guess behind
  the fuel flow and AFR estimates
- the ISO PID cross-check against a real ISO 9141-2 session (no ISO link yet)

## Uninstall

```sh
/opt/hondalog/uninstall.sh
```

Logs, settings and the Debian packages stay.
