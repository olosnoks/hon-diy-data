# hondalog documentation

hondalog is a Linux logger, stall catcher and tuning toolkit for a 2000 Honda
Civic EX (D16Y8, OBD2B, stock P2P ECU). It has three pieces of hardware and
one laptop app. This folder documents all of it.

**Read this before anything else:** almost nothing here has been run on the
car yet. The laptop software is tested against simulators (about a thousand automated
tests), both firmwares compile, and the hardware plans come from published
pinouts. Every page says which of its claims are still unverified, and
[the list is collected here](#what-is-not-verified-yet).

## Reading order for a new user

| # | Page | Read it when |
|---|---|---|
| 1 | [Getting started](getting-started.md) | Installing on the ThinkPad (Debian trixie, i3), or trying it under WSL2 |
| 2 | [Hardware](hardware.md) | Deciding what to buy and what to wire |
| 3 | [Cable day: first contact](monday-first-contact.md) | The KKL cable has arrived and the car is in front of you |
| 4 | [User guide](user-guide.md) | Using the app: every screen, every key |
| 5 | [Mapping the P2P](mapping-the-p2p.md) | Turning guessed addresses into verified channels |
| 6 | [Stall hunting](stall-hunting.md) | The intermittent stall: what to record and how to read it |
| 7 | [Data and SQL](data-and-sql.md) | The database, queries, exports, backups |
| 8 | [Handheld](handheld.md) | The CYD viewer for when the laptop stays home; start with [using it in the car without the laptop](handheld.md#using-the-cyd-in-the-car-without-the-laptop) |
| 8a | [Android radio](android.md) | The app on the car's Android head unit: installing it for free (USB stick or adb), first start, exporting drives |
| 9 | [Tuning a P28](tuning-p28.md) | Track C: the chipped OBD1 ECU |

Look things up in:

| Page | What |
|---|---|
| [Troubleshooting](troubleshooting.md) | Symptoms and exact fixes |
| [FAQ](faq.md) | Short answers |
| [Glossary](glossary.md) | P2P, P28, K-line, KKL, PGM-FI and the rest |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed since 0.1.0 |

## For people changing the code

| Page | What |
|---|---|
| [Development](development.md) | Tests, tools, conventions, how to add a channel, a math channel, an alarm |
| [Firmware development](firmware-dev.md) | Building both firmwares locally, host tests, the handheld simulator |
| [Architecture](ARCHITECTURE.md) | How the Python packages fit together; the database tables |
| [UI adapters](ui-adapters.md) | The interfaces between the Qt UI and everything behind it |
| [UI performance](ui-performance.md) | What was measured, and on what |
| [Design handoff](design/HANDOFF.md) | The UI specification the Qt build follows |
| [Feature list](research/FEATURES.md), [competitor survey](research/competitors.md) | Why the feature set is what it is |

## Hardware and file-format references

These live next to the firmware they describe.

| Page | What |
|---|---|
| [Black box hardware](../firmware/blackbox/HARDWARE.md) | Pinout, input circuits, where to tap on the car, bench tests |
| [Black box USB protocol](../firmware/blackbox/PROTOCOL.md) | Frames between the box and the laptop |
| [Black box file format](../firmware/blackbox/FORMAT.md) | `.BBX` captures, reason codes, `BLACKBOX.CFG`, trigger rules |
| [Handheld hardware](../firmware/handheld/HARDWARE.md) | CYD pinout, K-line plans A and B, GPS, power, bring-up steps |
| [Handheld file format](../firmware/handheld/FORMAT.md) | `.HHD` drives, `HANDHELD.MAP`, `HANDHELD.CFG` |

## The three tracks

| Track | Hardware | What it gives | Status |
|---|---|---|---|
| A | KKL 409.1 USB cable + laptop | Live ECU data over the K-line, recording, stall capture, replay | Software complete against the simulator. Not yet tried on the car |
| B | ESP32-S3 black box | 1 kHz record of the power rails and ignition pulses around a stall | Firmware compiles, logic tested on the PC. No board built |
| C | Chipped OBD1 P28 ECU | Editable fuel and ignition tables | File tools work on a synthetic ROM. No real P28 ROM or burner tried |

The CYD handheld is a fourth device that reuses Track A's protocol code in C.

## The commands

| Command | Does | Documented in |
|---|---|---|
| `hondalog` | The app | [User guide](user-guide.md) |
| `hondalog-sim` | Fake ECU on a pseudo-terminal | [Getting started](getting-started.md#first-run-without-the-car) |
| `hondalog-firstcontact` | First time on the car: which protocol answers | [Cable day](monday-first-contact.md) |
| `hondalog-map` | Guided verification, address finder, map diff | [Mapping the P2P](mapping-the-p2p.md) |
| `hondalog-log` | Drives: record, list, export, compare, delete, SQL | [Data and SQL](data-and-sql.md) |
| `hondalog-bb` | Black box: live values, fetch and read captures | [Stall hunting](stall-hunting.md) |
| `hondalog-bbsim` | Fake black box on a pseudo-terminal | [Stall hunting](stall-hunting.md#try-it-with-no-hardware) |
| `hondalog-handheld` | Handheld: write the address map and the road map (`map-build`) to the card, import drives | [Handheld](handheld.md) |
| `hondalog-tune` | ROM files: info, tables, diff, checksum, burn | [Tuning a P28](tuning-p28.md) |

Every command prints its options with `--help`.

## What is not verified yet

Collected from the pages that explain each item.

**On the car (Track A)**

- That the P2P answers the Honda read `20 05 addr len cksum` at all, and
  whether it wants the wake-up bytes.
- Every address and formula in the shipped address map. It is the kerpz map
  for a P2T, a different ECU.
- The ECU id location, the longest block the ECU answers, its reply time and
  therefore the real polling rate.
- ISO 9141-2 on this ECU with this cable.
- Where the OBD2 socket is in this car (see [Hardware](hardware.md#finding-the-obd2-socket)).
- The 1 ms FTDI latency timer, unplug recovery and frame rates on the real
  ThinkPad T430.

**Black box (Track B)**

- Everything electrical: ECU pins and wire colours, signal levels, hold-up
  time, SD write speed. The firmware has been compiled but never run on a
  board.

**Handheld**

- The board has four headers whose pin 1 position is not confirmed, and two
  resistors (R3, R4) that decide whether USB host works without soldering.
- Whether header P1 pin 1 gives 5 V out when the board runs from USB-C.
- The firmware has been compiled; the bring-up steps in its hardware page
  have not been recorded as done.
- The Windows `write-flash` command in
  [Handheld](handheld.md#flashing-from-windows) (the `merge-bin` step was
  run on Linux).

**Tuning (Track C)**

- No real P28 ROM, no definition for one, no burner and no Ostrich has been
  tried.

**Platforms**

- The PlatformIO build path of `scripts/build-firmware.sh`: the recorded
  builds used an installed ESP-IDF 5.3.1 (`HH_IDF_ENV`).
- WSL2 (WSLg, usbipd) instructions are written from how those tools work,
  not from a run of hondalog under them.
- MLG exports follow the published format and are checked byte by byte in
  the tests, but nobody has opened one in MegaLogViewer.
