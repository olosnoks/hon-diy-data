# FAQ

Short answers, with a link to the page that has the long one. Words you do
not know are in the [glossary](glossary.md).

## What it is

**What is hondalog?**
A Linux logger, stall catcher and tuning toolkit for one car: a 2000 Honda
Civic EX with the D16Y8 engine, OBD2B, and its stock P2P ECU. Think of it as
a home-made Hondata for a car Hondata does not support. It reads the ECU
over the K-line with a cheap KKL cable, records every drive into SQLite, and
catches stalls. A black box and a handheld add what the laptop cannot do.
Start at the [documentation index](README.md).

**Does it work on my car?**
It was written for one P2P. Other OBD2 Hondas may answer the same protocol
but the address map will be wrong for them, and nothing has been checked on
any car yet. The map has a `[variants]` section for other ECUs.

**Is any of it proven on the car?**
Not yet. The laptop software passes about a thousand tests against
simulators; both firmwares compile; the hardware plans follow published
pinouts. Every page lists what is still [unverified](README.md#what-is-not-verified-yet).

**Why not just buy Hondata?**
Hondata's loggers and tuning products for this engine family need a
chipped or replaced OBD1 ECU. None of them logs a stock P2P. The survey is
in [competitors](research/competitors.md).

## Hardware

**Which cable do I need?**
A "VAG-COM KKL 409.1" USB cable with an FTDI FT232RL chip (USB id
`0403:6001`), about $10 to $15. See [Hardware](hardware.md#1-the-kkl-4091-cable).

**Will an ELM327 work?**
No. An ELM327 speaks standard OBD2 through its own firmware and cannot send
the Honda diagnostic reads. Bluetooth and Wi-Fi adapters are ELM327s.

**My cable has a CH340 chip. Is that bad?**
Honda reads work. Standard OBD2 (ISO 9141-2) needs 10400 baud, which most
CH340 cables get wrong. See [Troubleshooting](troubleshooting.md#lsusb-shows-1a867523-ch340-instead-of-ftdi).

**Do I need the black box?**
Only to find out *why* the engine stalls. When power to the ECU drops, the
ECU simply stops answering, so the K-line log shows silence. The black box
watches the power rails and ignition pulses 1000 times a second and shows
which one fell first. See [Stall hunting](stall-hunting.md).

**Do I need the laptop in the car?**
For Track A, yes. The CYD handheld is the alternative: it reads the ECU,
shows live values, logs to SD with GPS and catches stalls on its own. See
[Handheld](handheld.md).

**Can I plug the KKL cable into the CYD's USB-C socket with an adapter?**
No. That socket goes to a CH340 USB-serial chip, which is a USB device, and
so is the cable. Two devices cannot talk. The cable goes on a USB-A socket
wired to header P3 instead, or you build an OBD2 harness with an L9637D.
See [Using the CYD in the car without the laptop](handheld.md#using-the-cyd-in-the-car-without-the-laptop).

**Where is the OBD2 socket?**
Under the dash or at the front of the centre console; which one in this car
is **[unverified]**. See [Hardware](hardware.md#finding-the-obd2-socket).

## Safety

**Can it damage the ECU?**
It only reads. The link refuses to send anything but read requests and the
wake-up bytes unless you switch on the expert unlock in Raw tools, and every
frame sent that way is written into the log. ISO 9141-2 "clear codes" needs
you to type `CLEAR`, and refuses while the engine runs.

**Can the black box cause a stall?**
It is designed not to: every tap goes through 10 kΩ or more at the tap end,
and the box has its own fuse. It has not been built yet; the
[bench test](../firmware/blackbox/HARDWARE.md#bench-test-before-it-goes-in-the-car)
comes before the car.

**Can I tune the stock P2P?**
No. hondalog reads it. Tuning is Track C: swap to a chipped OBD1 P28 and edit
its ROM. See [Tuning a P28](tuning-p28.md).

## Using it

**Can I try it without the car?**
Yes, three ways: `hondalog --demo` (made-up data, nothing recorded),
`hondalog --port sim: --connect` (the real code path against a built-in
simulated ECU and black box), and `hondalog-sim` on a pseudo-terminal, where
keys make it stall or lose power. See
[Getting started](getting-started.md#first-run-without-the-car).

**Why does every tile show a `?`**
The address map is a guess taken from a different ECU (a P2T). A channel
loses its `?` when you verify it on the car. See [Mapping the P2P](mapping-the-p2p.md).

**How fast does it read?**
About 17 reads a second for the fast channels (RPM, MAP, TPS, injector,
ignition) in the simulator at true 9600 baud byte timing; slower channels
less often. On the car it depends on the ECU's reply time, which first
contact measures.

**Where are my drives?**
In one SQLite file, `~/hondalog/hondalog.db`. `hondalog-log list` shows
them. See [Data and SQL](data-and-sql.md).

**Can I open my drives in MegaLogViewer?**
Export them: `hondalog-log export DRIVE out.mlg` (or `.msl`, `.csv`). The
MLG writer follows the published format and is checked byte by byte in the
tests, but nobody has opened one in MegaLogViewer yet.

**Can I query my drives with SQL?**
Yes: `hondalog-log sql "SELECT ..."`, the `sqlite3` shell, or DB Browser for
SQLite. Every drive has its own view with a column per channel. See
[Data and SQL](data-and-sql.md#querying-with-sql).

**What happens if the laptop dies mid-drive?**
At most the last second is lost. The database is committed every half
second; the next start marks the drive "crashed" and keeps everything up to
the last commit.

**Why mph and °F on screen but °C in the database?**
The map, the database and every export are metric. The screen converts,
US units by default; change it under ECU chip, Settings > Units. See
[User guide](user-guide.md#units).

**Does it read and clear trouble codes?**
Yes, over standard OBD2 (ISO 9141-2) on the same cable: stored and pending
codes with Honda P1xxx text, freeze frame, readiness, and clear. Whether
this ECU and cable manage the ISO init is **[unverified]**.

## Platforms and development

**Does it run on Windows?**
The app is built for Debian Linux. Under WSL2 it should run (and the
firmware builds there); that is written from how WSLg and usbipd work and
has not been run. Flashing the CYD from plain Windows with esptool is
described in [Handheld](handheld.md#flashing-from-windows).

**What laptop does it need?**
It is designed at 1366x768 for a ThinkPad T430 running Debian 13 (trixie)
and i3. `hondalog --profile` measures it on your machine.

**Why are the GitHub Actions workflows manual-only?**
Runs cost Actions minutes, and everything they do runs faster locally. The
rule is to build and test on your own machine. See
[Development](development.md#github-actions-are-manual-only).

**How do I build the firmware?**
`scripts/build-firmware.sh` on Linux or WSL. See
[Firmware development](firmware-dev.md).
