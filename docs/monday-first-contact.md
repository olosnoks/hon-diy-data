# Cable day: first contact

The procedure for the first time the KKL cable goes into the car. It takes
about ten minutes. The aim is one fact: **does this ECU answer the Honda
protocol on the OBD2 socket?** Everything else in hondalog depends on the
answer, and until this has been run nobody knows it.

Nothing here writes to the ECU. `hondalog-firstcontact` sends read requests,
the wake-up bytes, an OBD2 init and OBD2 read services. It never clears codes
and never resets anything.

- [Before you go to the car](#before-you-go-to-the-car)
- [Step 1: key ON](#step-1-key-on)
- [Step 2: does Linux see the cable?](#step-2-does-linux-see-the-cable)
- [Step 3: run first contact](#step-3-run-first-contact)
- [Step 4: read the verdict](#step-4-read-the-verdict)
- [Step 5: read the report](#step-5-read-the-report)
- [Step 6: first live session](#step-6-first-live-session)
- [Sending the report](#sending-the-report)

## Before you go to the car

Do these at a desk.

1. Install hondalog and log out and in once
   ([Getting started](getting-started.md)). `groups` must list `dialout`.
2. Rehearse on the simulator, so the real output is familiar:

   ```sh
   hondalog-firstcontact --port sim:
   ```

   It takes about 12 seconds and ends with `verdict: Honda protocol OK`.
3. Plug the cable into the laptop alone and check `lsusb` shows `0403:6001`
   ([Hardware](hardware.md#checking-it-on-the-laptop)).
4. Find the car's OBD2 socket
   ([Hardware](hardware.md#finding-the-obd2-socket)).
5. Charge the laptop. Take a pen: write down what the dash shows.

## Step 1: key ON

1. Plug the cable into the OBD2 socket. Push until it seats.
2. Plug the USB end into the laptop.
3. Turn the key to **ON (II)**: the position where the dash lights come on.
   Not ACC (I). The engine can stay off.

Leave hondalog itself closed, or disconnected. First contact needs the port
to itself.

## Step 2: does Linux see the cable?

```sh
lsusb | grep -i 0403       # want: 0403:6001 ... FT232 Serial (UART) IC
ls -l /dev/hondalog        # want: /dev/hondalog -> ttyUSB0
```

If either is missing, stop and go to
[Troubleshooting](troubleshooting.md#the-cable). First contact would only
tell you `no port`.

## Step 3: run first contact

```sh
hondalog-firstcontact
```

With no `--port` it uses the first FTDI adapter it finds, else
`/dev/hondalog`. Progress lines appear as it works:

```
hondalog first contact on /dev/ttyUSB0
  .. checking the adapter
  .. opening the port
  .. echo test
  .. listening for 2 s
  .. Honda read of 0x00
  .. reading the ECU id
  .. probing the longest block read
  .. measuring ECU turnaround (200 reads)
  .. sweeping 0x00-0xFF
  .. ISO 9141-2 5-baud init (about 3 s)
  .. reading codes, freeze frame and PIDs

verdict: Honda protocol OK  (12.4 s)
The ECU answers Honda RAM reads on this pin with the default framing, without
the wake-up bytes. hondalog can log at full speed. ISO 9141-2 works too, for
trouble codes and readiness.
report: /home/you/hondalog/logs/firstcontact-20261116-071455.md
raw hex log: /home/you/hondalog/logs/firstcontact-20261116-071455.hex.log
```

(That is the simulator's output. On the car, lines are missing when a step
is skipped because an earlier one failed.)

What it does, in order:

| # | Step | What it shows |
|---|---|---|
| 1 | Adapter check | FTDI or not, genuine-looking or clone-suspected, latency timer |
| 2 | Open the port | permissions and the device node |
| 3 | Echo test | one byte out; on a K-line it must come straight back. No echo = no 12 V at the plug, or a dead cable |
| 4 | Passive listen, 2 s | whether anything else is talking on the line |
| 5 | Honda read of address 0x00 | first without the wake-up bytes, then with them |
| 6 | Other reply framings | only if step 5 got bytes that did not fit |
| 7 | ECU id, longest block, reply time, a sweep of 0x00 to 0xFF | only if the Honda protocol answered |
| 8 | ISO 9141-2 init, then codes, freeze frame, readiness, supported PIDs | standard OBD2 on the same pin |

Options:

| Option | Does |
|---|---|
| `--port PORT` | Use this port. `sim:` is the built-in simulator |
| `-o FILE`, `--out FILE` | Write the report there; `-` prints it to the terminal |
| `--quick` | Skip the timing probe and the RAM sweep |
| `--no-iso` | Skip the ISO 9141-2 part |
| `--listen SEC` | Passive listen time (default 2) |
| `--map FILE` | Use this address map (default: the built-in one) |
| `--udev` | Print the udev rule and exit; `install.sh` already installed it |

The same sequence runs from inside the app: click the **ECU** chip while
disconnected and choose **Run first contact…**. The verdict appears in the
bottom strip and the two files land in the same folder.

## Step 4: read the verdict

The command ends with one of five verdicts. Its exit status is 0 for the
first two and 1 for the others.

### `Honda protocol OK`

The ECU answers `20 05 addr len cksum`. This is the result everything was
built for. Go to [step 5](#step-5-read-the-report).

The summary line also says two things worth noting:

- "with the default framing" or "with an alternate framing (...)". An
  alternate framing means replies have a different shape from the expected
  `00 len+3 data cksum`. Logging will need the framing overridden in the
  address map's `[protocol]` section; keep the report, it has the bytes.
- "without the wake-up bytes" or "only after the wake-up bytes".

### `ISO only`

The ECU speaks standard OBD2 (ISO 9141-2) on pin 7 but ignores Honda reads.

What you have now: the report already contains the stored and pending
trouble codes, the freeze frame, the readiness monitors and the list of
supported PIDs. Read them there.

What you do not have: fast logging. hondalog's live screens, recorder and
stall catcher all run on Honda reads. ISO 9141-2 gives a handful of PIDs per
second at best, and the app has no ISO logging mode (the handheld does:
`iso_fallback` in its settings).

What to do:

1. Run it again with the engine running. Some ECUs answer differently.
2. Check the ECU's part number on its sticker (passenger footwell, behind
   the kick panel). hondalog was written for a P2P.
3. Keep both files. The `.hex.log` shows exactly what the ECU sent back to
   the Honda read, if anything.
4. The report suggests trying the Honda protocol on the car's separate
   service connector instead of OBD2 pin 7. Whether this car has one that
   carries the K-line is **[unverified]**.

### `cable dead`

Nothing came back, not even the cable's own echo. The K-line has no pull-up,
which means the cable's car side has no power.

1. Push the plug in again. It needs force.
2. Measure 12 V between OBD2 pin 16 and pins 4/5. If it is missing, find the
   blown fuse.
3. Check the car battery is connected.
4. Try the cable on another car, or another cable on this one.

### `ECU silent`

The cable works (the echo came back) but the ECU answered neither protocol.

1. Key in ON (II), not ACC.
2. Try again with the engine running.
3. Check pin 7 is populated in the car's socket: look for a metal contact in
   position 7 (top row, second from the right as you face the socket with
   the wide side up).
4. Open the `.hex.log`. Any `RX` bytes after an echo are a reply in a format
   hondalog did not recognise. That file is the thing to send.

### `no port`

The serial port could not be opened.

```sh
lsusb | grep -i 0403           # does Linux see the cable?
sudo dmesg | tail              # did a driver attach? does it mention brltty?
groups                         # is dialout in the list?
```

Fixes are in [Troubleshooting](troubleshooting.md#the-cable).

## Step 5: read the report

`~/hondalog/logs/firstcontact-DATE-TIME.md` is plain Markdown. Its sections:

| Section | What to look for |
|---|---|
| Verdict and summary | The lines from the terminal |
| Next steps | Three things to do for this verdict |
| Steps | A table of every step: OK, FAIL or skipped, with the detail |
| Adapter | USB id, chip, `genuine-looking` / `clone-suspected` / `not-ftdi`, the latency timer. **Want 1 ms.** 16 ms means the udev rule did not apply |
| ECU id | The bytes at the guessed id address, as hex and as text |
| Block length | The longest read the ECU answered (it tries 1, 2, 4, 8, 16, 32, 48, 64) |
| Link timing | How long the ECU takes to reply: median, 95th percentile, maximum over 200 reads |
| Suggested `[protocol]` settings | A TOML block to paste into your address map |
| RAM sweep 0x00-0xFF | A hex dump of everything the ECU returned, once |
| ISO 9141-2 | How the init went, MIL state, codes, readiness, freeze frame, PIDs |

`firstcontact-DATE-TIME.hex.log` is every byte sent and received with
millisecond times:

```
       0.0  NOTE  echo test
       0.0  TX    55
       1.8  RX    55
    2052.2  NOTE  Honda read 0x00, no wake-up bytes
    2052.3  TX    20 05 00 02 D9
```

### Apply the suggested settings

If the report has a "Suggested `[protocol]` settings" block, copy its lines
into the `[protocol]` section of `~/.config/hondalog/addressmap.toml` (the
ECU chip menu has **Edit address map**). For example:

```toml
[protocol]
max_read_len = 64
reply_timeout_s = 0.05
inter_request_delay_s = 0.001
```

`max_read_len` lets the planner use longer reads; the two timings let the
link stop waiting sooner. The link adapts between its limits by itself, so
this is a refinement, not a requirement.

### Compare the sweep with the map

The RAM sweep is the first real look at this ECU's memory. Before trusting
any gauge, compare a few bytes with what you know (engine off, key on):

| Address | The map guesses | Expect, engine off |
|---|---|---|
| `00`-`01` | RPM | `00 00` |
| `02` | Speed | `00` |
| `14` | Throttle | a small number that rises when you press the pedal |
| `17` | Battery | about 0x80 (128 / 10.45 = 12.2 V) |

If those are plainly wrong, the P2P's layout differs from the P2T map and
[Mapping the P2P](mapping-the-p2p.md) is the next job, not logging.

## Step 6: first live session

Only after `Honda protocol OK`.

```sh
hondalog --port /dev/hondalog --connect
```

1. **The ECU chip** (top left) goes from `ECU waking K-line` to `ECU OK`
   with a read rate and an error count. On the simulator the rate is about
   17 Hz; the real figure depends on the ECU's reply time and is
   **[unverified]**. A red `ECU SILENT` means go back to step 3.
2. **Recording starts by itself** (ECU chip menu, "Record on connect"). The
   REC chip counts up.
3. **Every tile has a dashed `?` marker.** That is correct: no address has
   been verified. Do not believe a number because it looks plausible.
4. **Check three things against reality**, engine off:
   - RPM reads 0.
   - Throttle moves from near 0 to near 100 % as you press the pedal.
   - Battery is within a few tenths of a volt of a multimeter on the battery.
5. **Start the engine.** RPM should match the dash tachometer. Blip the
   throttle and watch RPM, MAP and throttle move together.
6. **Press Space once.** That is MARK: it tags the moment in the log. Use it
   whenever the car does something odd.
7. **Stop:** Ctrl+R stops the recording, or just turn the key off. Close the
   app.
8. **Look at what you recorded:**

   ```sh
   hondalog-log list
   hondalog-log info
   ```

Then, in this order:

1. [Mapping the P2P](mapping-the-p2p.md): prove the channels you care about.
   Start with `hondalog-map verify koeo` and `tps_sweep`, which need no
   running engine.
2. [Stall hunting](stall-hunting.md): leave it recording on every drive.

Turning the key off with the engine running looks like a stall to the
catcher (the ECU goes silent). It will save a capture; press Esc to dismiss
the banner.

## Sending the report

When asking for help, send **both** files of the run:

```sh
ls -t ~/hondalog/logs/firstcontact-* | head -2
```

- `firstcontact-DATE-TIME.md`: the verdict and findings.
- `firstcontact-DATE-TIME.hex.log`: the raw bytes. For any verdict other
  than `Honda protocol OK` this is the file that matters.

Both are small text files. Attach them to an issue at
<https://github.com/olosnoks/hon-diy-data/issues>, or paste them wherever
you are getting help. They contain the adapter's USB serial number, the ECU
id bytes and one dump of ECU RAM; no location, no name, nothing from the
laptop beyond the port name.

Add three things the files cannot know:

1. Key position and whether the engine was running.
2. The part number on the ECU's sticker, if you have read it.
3. Where the OBD2 socket was. That is still an open question in
   [Hardware](hardware.md#finding-the-obd2-socket).

To put the whole report on the terminal instead of a file:

```sh
hondalog-firstcontact -o -
```
