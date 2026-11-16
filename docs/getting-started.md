# Getting started

How to install hondalog on the car laptop (a ThinkPad T430 running Debian 13
"trixie" with the i3 window manager), try it with no car attached, and check
that the laptop draws it fast enough. A second section covers trying it on a
Windows PC under WSL2.

Nothing on this page needs the car or the cable. For the cable, go on to
[Cable day](monday-first-contact.md).

- [Install on Debian trixie](#install-on-debian-trixie)
- [Check OpenGL](#check-opengl)
- [i3 setup](#i3-setup)
- [First run without the car](#first-run-without-the-car)
- [Performance check](#performance-check)
- [Trying it under WSL2 on Windows](#trying-it-under-wsl2-on-windows)
- [Updating and uninstalling](#updating-and-uninstalling)

## Install on Debian trixie

```sh
sudo apt install git
git clone https://github.com/olosnoks/hon-diy-data
cd hon-diy-data
./install.sh
```

Run `install.sh` as your normal user, not as root. It calls `sudo` itself and
is safe to run again. What it does, in order:

1. Installs the Debian packages: `python3`, `python3-numpy`,
   `python3-serial`, `python3-pyqtgraph` and the PySide6 modules
   (`python3-pyside6.qtcore`, `.qtgui`, `.qtwidgets`). It then tries the
   optional ones one at a time: the OpenGL modules (`python3-opengl`,
   `python3-pyside6.qtopenglwidgets`, `python3-pyside6.qtopengl`),
   `qt6-qpa-plugins`, `libxcb-cursor0`, `python3-pytest`, `sqlite3` and a few
   more. A missing optional package is reported and skipped.
2. Copies the program to `/opt/hondalog` (the `hondalog` package, `bin/`,
   `tests/`, `tools/`, `bench/`). The firmware sources, `scripts/` and this
   `docs/` folder are **not** copied: keep the clone.
3. If the Debian packages do not import cleanly, builds a virtual environment
   in `/opt/hondalog/.venv` and installs PySide6, pyqtgraph, pyserial and
   numpy into it with pip. The commands use that Python automatically.
4. Links the nine commands into `/usr/local/bin`: `hondalog`, `hondalog-sim`,
   `hondalog-firstcontact`, `hondalog-map`, `hondalog-log`, `hondalog-bb`,
   `hondalog-bbsim`, `hondalog-tune`, `hondalog-handheld`.
5. Installs `/etc/udev/rules.d/99-hondalog.rules`, which
   - names the KKL cable (USB id `0403:6001`) `/dev/hondalog`,
   - sets the FTDI latency timer to 1 ms on every plug-in,
   - names the black box (USB id `303a:1001`) `/dev/hondalog-bb`.
6. Adds a menu launcher and icon.
7. Creates `~/hondalog/logs`.
8. Adds you to the `dialout` group if you are not in it.

### Log out and back in

The `dialout` group is what lets you open `/dev/ttyUSB0`. Group membership
only applies to new logins, so after the first install **log out of i3 and
log in again** (or reboot). Check with:

```sh
groups        # the list must contain: dialout
```

The simulator needs no group and works straight away.

### brltty

`brltty` (the braille display driver) claims FTDI serial adapters: the cable
appears and vanishes a second later, and `dmesg` mentions `brltty`. The
installer prints a warning if the package is installed. If it is:

```sh
sudo apt remove brltty
```

then unplug the cable and plug it back in.

### Where things are

| What | Where |
|---|---|
| Every drive and capture | `~/hondalog/hondalog.db` (plus `-wal` and `-shm` beside it while open) |
| Exports, reports, first-contact reports, fetched black box captures | `~/hondalog/logs/` |
| Address map (the file you edit) | `~/.config/hondalog/addressmap.toml`, copied from the shipped one on first use |
| Shipped default map | `/opt/hondalog/hondalog/data/addressmap.toml` |
| Window settings (theme, units, last port) | `~/.config/hondalog/hondalog.conf` |
| ROM backups and snapshots | `~/hondalog/roms/` |
| Program | `/opt/hondalog/` |

Three environment variables move the data: `HONDALOG_DB` names another
database file, `HONDALOG_DIR` names one folder for both the database and the
loose files, and `HONDALOG_ROM_DIR` moves the ROM store.

## Check OpenGL

Map 3D (F3) and the surface on the Tune screen (F6) use OpenGL 2.1. Without
it they draw the same data as a flat heat map. See what the laptop offers:

```sh
sudo apt install mesa-utils
glxinfo -B
```

Read three lines of the output:

| Line | Good | Bad |
|---|---|---|
| `direct rendering:` | `Yes` | `No` |
| `OpenGL renderer string:` | names the graphics chip. On a T430 with integrated graphics that is an Intel HD 4000 (Ivy Bridge) | `llvmpipe`: software rendering on the CPU |
| `OpenGL version string:` | 2.1 or higher | lower |

If the renderer is `llvmpipe`, or the 3D screens stutter, start the app with
`--no-gl`:

```sh
hondalog --no-gl
```

Then no OpenGL view is created at all and both screens use the 2D heat map.
`HONDALOG_GL=0` in the environment does the same.

Not measured yet: one OpenGL widget makes Qt composite the whole window
through OpenGL, which with software rendering added about 8 ms to every frame
on every screen. On the HD 4000 it should be small. If the
[performance check](#performance-check) says otherwise, use `--no-gl`. The
numbers behind this are in [UI performance](ui-performance.md).

## i3 setup

The app's X11 window class is `hondalog`. Add to `~/.config/i3/config`:

```
# hondalog: always full screen, always on workspace 9
for_window [class="(?i)hondalog"] fullscreen enable
assign [class="(?i)hondalog"] 9

# launchers: pick keys your config does not use yet
bindsym $mod+Ctrl+h exec --no-startup-id hondalog --port /dev/hondalog --connect
bindsym $mod+Ctrl+d exec --no-startup-id hondalog --demo
bindsym $mod+Ctrl+s exec --no-startup-id hondalog --port sim: --connect
```

Reload with `$mod+Shift+r`. To see the class of a running window yourself:
`xprop WM_CLASS`, then click the window (`sudo apt install x11-utils`).

Notes:

- The screen is designed for 1366x768, the T430's native panel. Full screen
  on that panel is exactly the designed size.
- F11 inside the app also toggles full screen. With the `for_window` rule
  you do not need it.
- The app handles every key itself (F1 to F8, F9, Space, Esc). Make sure
  your i3 config does not bind those bare keys.
- Menus and the drive picker are ordinary dialogs; i3 floats them.

## First run without the car

### 1. Demo mode: look at every screen

```sh
hondalog --demo
```

A made-up car drives around, a made-up black box runs beside it, and the
Replay screen (F4) holds a sample stall capture. **Nothing is recorded and no port is
opened.** Use it to learn the keys:

| Key | Does |
|---|---|
| F1 to F8 | Sensors, Drive, Map 3D, Replay, Raw tools, Tune, Alarms, Map |
| F9 | Daylight / Night theme |
| Space | MARK |
| Esc | Acknowledge alarms, dismiss the stall banner |

The [user guide](user-guide.md) has the rest.

### 2. The built-in simulator: the real code path

```sh
hondalog --port sim: --connect
```

`sim:` is a simulated ECU inside the app, paced at true 9600 baud. Unlike
the demo this runs everything that runs in the car: the poller thread, the
protocol code, the address map, math channels, alarms, and the recorder.
A simulated black box is attached beside it. **This does record**: a drive
appears in `~/hondalog/hondalog.db`. Remove it afterwards with
`hondalog-log list` and `hondalog-log rm NAME`.

`sim:flaky` is the same ECU with line faults (dropped replies, bad checksums),
to see how the link copes.

### 3. The pty simulator: practise a stall

`hondalog-sim` is the same fake ECU on a pseudo-terminal, with keys to break
it. Terminal 1:

```sh
hondalog-sim
```

Terminal 2:

```sh
hondalog --port /tmp/hondalog-sim --connect
```

Keys in the simulator's terminal:

| Key | What happens |
|---|---|
| `s` | Stall: RPM drops to 0, the ECU keeps answering |
| `p` | Power cut: the ECU goes silent, like a dead main relay |
| `r` | Restart |
| `v` | Rev |
| `u` | Pull the cable for 3 seconds |
| `q` | Quit |

Press `p`. Within about a second the app shows a red banner over the tabs
and beeps; press Enter to open the capture in Replay. That is the whole
stall-catching path, end to end.

Useful options (all from `hondalog-sim --help`):

| Option | Does |
|---|---|
| `--stall-after SEC` | Stall once after that many seconds |
| `--stall-mode rpm` or `power` | Which kind of stall |
| `--restart-after SEC` | How long a stall lasts (default 15; 0 = until `r`) |
| `--faults flaky` | Line faults; or a list such as `drop=0.01,cksum=0.01` |
| `--seed N` | Make a run repeat exactly |
| `--unplug-after SEC`, `--unplug-for SEC` | Pull the cable on a timer |
| `--cable-dead` | No echo and no replies, as with no 12 V at the plug |
| `--iso-only` | The ECU ignores Honda reads |
| `--fast` | Skip the 9600 baud byte timing |

The pty cannot do the break signalling that ISO 9141-2 needs, so the OBD2
part of `hondalog-firstcontact` only works on `sim:` and on the real cable.

## Performance check

The targets are 30 frames a second (never under 20), under half of one CPU
core, and under 100 ms from the last serial byte to the pixel. They were met
on a server CPU with no GPU; they have **not** been measured on the T430.
Measure it:

```sh
hondalog --port sim: --connect --profile
```

`--profile` puts a line in the bottom bar, updated twice a second:

```
30 fps · frame 2.1/3.8 ms · cpu 13% · lat 2 ms · poll 17.3 Hz · q 0
```

| Field | Meaning | Want |
|---|---|---|
| `fps` | Frames actually drawn per second | 30 |
| `frame a/b ms` | Work per frame, median / 95th percentile | well under 33 |
| `cpu` | The whole process as a share of one core | under 50 % |
| `lat` | Milliseconds from the last byte of a read to its pixel | under 100 |
| `poll` | ECU reads of the fast channels per second | about 17 on the simulator |

Walk through F1 to F8, staying a few seconds on each; Map 3D (F3) and Tune
(F6) are the heavy ones, and the Map (F8) while you drag it around a long
drive. On exit the app prints a JSON summary to the
terminal (`fps`, `fps_min`, `frame_ms`, `paint_ms`, `latency_ms`, `cpu_pct`
and the 3D scene's statistics). If F3 or F6 fall below 20 fps, run with
`--no-gl` and compare.

A fuller measurement, one screen after another, is
`python3 tools/bench_ui.py --onscreen --seconds 20` from `/opt/hondalog`
(see [Development](development.md#toolsbench_uipy)).

## Trying it under WSL2 on Windows

For looking at the app and for building firmware on the Windows PC. It is
not the way to log in the car. **These steps have not been run with
hondalog**; they follow how WSLg and usbipd-win work.

### The app

Needs Windows 11, or Windows 10 with WSLg, so that Linux windows appear on
the Windows desktop.

```powershell
wsl --install -d Debian
wsl --update
```

Inside the Debian shell, install as above:

```sh
sudo apt update && sudo apt install git
git clone https://github.com/olosnoks/hon-diy-data
cd hon-diy-data
./install.sh
hondalog --demo
```

Expect:

- The installer's udev steps do nothing useful unless systemd is enabled in
  the distribution; they are allowed to fail.
- WSLg's OpenGL goes through a translation layer. If Map 3D misbehaves, use
  `hondalog --demo --no-gl`.
- On a distribution other than Debian 13 the Debian packages may be too old
  or missing; the installer then builds the virtual environment instead.

### The cable or the CYD: usbipd

WSL2 does not see USB devices until they are attached with
[usbipd-win](https://github.com/dorssel/usbipd-win). In an administrator
PowerShell:

```powershell
winget install usbipd
usbipd list                          # find the BUSID: 0403:6001 is the KKL cable,
                                     # 1a86:7523 is the CYD's CH340C
usbipd bind --busid 2-3              # once per device (use your BUSID)
usbipd attach --wsl --busid 2-3      # every time it is plugged in
```

Then in WSL:

```sh
lsusb                                # the device is listed
ls /dev/ttyUSB*                      # FTDI and CH340 both appear as ttyUSB
hondalog --port /dev/ttyUSB0 --connect
```

If `/dev/ttyUSB0` does not appear, the WSL kernel is missing the driver
module: `sudo modprobe ftdi_sio` (cable) or `sudo modprobe ch341` (CYD), and
update WSL (`wsl --update`) if the module does not exist. If opening the port
gives "Permission denied", the `dialout` group has not taken effect: close
every WSL window, run `wsl --shutdown` in PowerShell and start again, or as a
quick test `sudo chmod a+rw /dev/ttyUSB0`.

While a device is attached to WSL, Windows cannot use it. Detach with
`usbipd detach --busid 2-3`, or unplug it.

Flashing the CYD from Windows without WSL is described in
[Handheld](handheld.md#flashing-from-windows).

## Updating and uninstalling

```sh
cd hon-diy-data
git pull
./install.sh                 # copies the new version over /opt/hondalog
```

Your database, logs, address map and settings are not touched. A new
version may add channels to the *shipped* map; your copy in
`~/.config/hondalog/` stays as it is (see
[Mapping the P2P](mapping-the-p2p.md#your-map-and-the-shipped-map)).

```sh
/opt/hondalog/uninstall.sh
```

removes the program, the commands, the udev rule and the launcher. It leaves
`~/hondalog/`, `~/.config/hondalog/`, the Debian packages and your `dialout`
membership.
