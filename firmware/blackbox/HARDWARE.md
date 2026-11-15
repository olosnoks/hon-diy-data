# Black box hardware

An ESP32-S3 dev board with PSRAM that watches eight things in a 2000 Civic
EX (D16Y8, OBD2B, P2P ECU) and saves 30 s before and 10 s after a stall.

**Rule one: the box must not be able to cause a stall.** Every tap goes
through a resistor of 10 kΩ or more *at the tap end of the wire*, so a
chafed or shorted lead cannot pull a car signal down. The box's own supply
has its own fuse.

**Status: nothing here has been built or tested on the car.** The firmware
has not even been compiled (see README, "Black box"). Items marked
**[unverified]** come from one web source or from general knowledge and must
be checked with a meter or scope before you trust them. They are collected
at the end.

## Pinout (ESP32-S3-DevKitC-1 N16R8)

| GPIO | use | notes |
|---|---|---|
| 4 | ADC battery | ADC1_CH3 |
| 5 | ADC ECU +B | ADC1_CH4 |
| 6 | ADC fuel pump feed | ADC1_CH5 |
| 7 | ADC IG1 | ADC1_CH6 |
| 8 | ADC alternator (optional) | ADC1_CH7. Tie to GND through 10 kΩ if unused |
| 9 | GPS TX → box RX (optional) | UART1, 9600 baud, `gps_baud = 9600` |
| 10, 11, 12, 13 | microSD CS, MOSI, SCK, MISO | SPI, 20 MHz |
| 14 | wake | IG1 through its own divider; wakes the box from deep sleep |
| 15 | tach edges | MCPWM capture, internal pull-up |
| 16 | ignition trigger edges | MCPWM capture |
| 17 | injector edges | MCPWM capture |
| 18 | marker button to GND | internal pull-up |
| 21 | status LED | to GND through 330 Ω |
| 19, 20 | USB D−, D+ | the board's "USB" socket (not "UART"): link to the laptop |
| 43, 44 | UART0 | debug log only, the board's "UART" socket |

Left alone on purpose: 35–37 (octal PSRAM), 0, 3, 45, 46 (strapping).
The numbers are one table at the top of `main/hal/hal_esp32s3.c`.

## Wiring overview

```
 battery +12 (constant) ─ fuse 1 A ─ D1 ─┬─ SMBJ24A ─ GND
                                         └─ buck 12→5 V ─ D2 ─┬─ 5V pin of the dev board
                                                              └─ hold-up capacitors ─ GND
 car ground (ECU ground or body bolt) ───── box GND (one point)

 battery ──[47k]──┬── GPIO4      ECU +B (B1) ──[47k]──┬── GPIO5
 pump feed ─[47k]─┼── GPIO6      IG1 ─────────[47k]──┬┴── GPIO7
                  │                                   └─[33k]─┬─ GPIO14 (wake)
        each ADC pin: 6.8k ‖ 10 nF to GND, Schottky clamp     └─ 10k to GND
        to 3V3 and GND

 tach (A19) ────── input stage ── PC817 ── GPIO15
 ign trig (B13) ── input stage ── PC817 ── GPIO16
 injector (B11) ── 8.2k ───────── PC817 ── GPIO17      (LED anode side to IG1)

 button ── GPIO18        LED ── GPIO21        microSD ── GPIO10-13, 3V3
 USB socket ── laptop (optional while driving)
```

## Analog rails (channels 1–4, and 5)

Per channel:

```
 tap ──[R1 47k, at the tap end]────────┬──────────┬───────┬──── ADC pin
                                       │          │       │
                                  [R2 6.8k]   [C1 10 nF]  ├─▷|── 3V3   (BAT54S or 2× 1N5819)
                                       │          │       └─|◁── GND
                                      GND        GND
```

| item | value | why |
|---|---|---|
| R1 | 47 kΩ, 0.5 W or two 24 kΩ in series | sets the ratio; limits fault current to 0.3 mA at 14 V and 2 mA at a 100 V spike |
| R2 | 6.8 kΩ 1 % | ratio (47 + 6.8) / 6.8 = 7.912: 14.4 V → 1.82 V, full scale (3.1 V) = 24.5 V |
| C1 | 10 nF | charge reservoir for the ADC. With 5.9 kΩ source: τ = 59 µs, so a 1 ms dropout settles to within 0.1 % |
| clamp | BAT54S, or two 1N5819 | a spike or a wrong connection ends at 3.6 V / −0.3 V |

Default `*_scale = 7912` matches these values. Resistor tolerance and the
ADC's own error are a few percent, so calibrate (below).

**About the 3.3 V zeners:** do not use one as the clamp on an ADC pin.
Low-voltage zeners conduct well below their rated voltage (tens of µA at
2 V **[unverified for your parts]**), and through 5.9 kΩ that reads as
several tenths of a volt low, non-linearly, so calibration cannot remove it.
If Schottkys are not to hand, a 3.3 V zener works as a stopgap; expect the
top of the range to read low and check with a bench supply.

The 0.3 mA each rail draws is constant on the battery tap. That is under
the car's own standby current.

### Calibration

1. Bench supply on all four rail inputs together, box powered.
2. Set 8.00 V on a good meter. Note what `hondalog-bb live` shows per rail.
3. Set 15.00 V. Note again.
4. `hondalog-bb cal vbat --low 8.00 <shown> --high 15.00 <shown>` prints the
   two lines for `BLACKBOX.CFG`. Repeat for `ecub`, `pump`, `ig1`.
5. Target after calibration: within ±0.1 V of the meter from 6 V to 16 V.

## Pulse inputs (channels 5–7)

PC817 on the output side, same for all three:

```
 3V3 ──[4.7k]──┬── GPIO15/16/17        opto transistor on  = GPIO low = "active"
               C                        (tach_invert = ign_invert = inj_invert = 1)
 PC817         E ── GND
```

PC817 turn-on is a few µs; turn-off with 4.7 kΩ is tens of µs
**[unverified, depends on the part]**. The firmware timestamps both edges
to better than 1 µs (80 MHz capture counter), so RPM and period, which use
the turn-on edge only, are exact. Pulse *width* reads long by the turn-off
delay; measure it once on the bench (step "edges" below) if you care about
injector pulse width to better than 0.1 ms.

### Injector (channel 7): opto straight across the injector

```
 IG1 / injector + (YEL/BLK) ──[8.2k at the tap]──┬──▷|──┬── injector − wire (ECU B11)
                                                 │ PC817│
                                                 └──|◁──┘   1N4148 reversed across the LED
```

The LED lights while the ECU holds the injector low (1.5 mA; the injector
itself draws about 1 A, so the load is nothing). When the ECU lets go the
wire flies up to 60–80 V **[unverified]**; the 1N4148 carries that reverse
current so the LED never sees more than 0.7 V backwards (its limit is 6 V).

### Ignition trigger (channel 6) and tach (channel 5): buffer first

These two are logic signals between the ECU and the igniter / gauge. Their
levels and drive strength on this ECU are **[unverified]**, and the igniter
is one of the suspects, so do not hang an LED on them. Use a small MOSFET so
the car sees only 100 kΩ:

```
 signal ──[100k at the tap]──┬──────┬── G  2N7000 / BSS138
                             │      │   D ──▷|── [1k] ── +5 V     (PC817 LED, 3.5 mA)
                        [1M] ┴  [ZD 5.1–10 V]  S ── GND
                            GND    GND
```

The MOSFET switches at about 2 V, so it follows a 5 V or a 12 V signal.
The 1 MΩ keeps it off when the wire is unplugged; the zener protects the
gate. LED on = signal high. If your scope shows the signal idles high and
pulses low, nothing changes: the firmware only needs edges, and `*_invert`
picks which edge counts as the start.

**Scope both signals before wiring** (engine idling, 10 ms/div): note the
high and low voltage. If either never gets above 2.5 V, the MOSFET will
not switch: use a logic-level part with a lower threshold (BSS138) and
leave the 1 MΩ out.

If you prefer the plain opto for the tach: LED anode to IG1 through 8.2 kΩ,
cathode to the tach wire, 1N4148 across the LED. That only works if the
wire is pulled to ground on each pulse and sits near 12 V otherwise
**[unverified]**.

## Button, LED, SD, GPS

- Button: momentary, GPIO18 to GND. Mount it where you can hit it without
  looking. Hold 30 ms or more.
- LED on GPIO21: one blink every 2 s = armed, key on; two blinks = engine
  running; three blinks = a capture is on the card; fast flicker = capturing;
  slow even blink = no card or card error; fast even blink = self-test failed.
- microSD breakout on 3V3 (not 5 V unless the breakout has its own
  regulator). FAT32, 32 GB or smaller. A capture is about 1 MB plus a 3 MB
  CSV; `csv_export = 0` saves the space and the write time.
- NEO-6M (optional): its TX to GPIO9, 3V3 and GND. Gives position, speed and
  a clock when no laptop is attached.

## Power and hold-up

```
 +12 constant ─ fuse 1 A ─ D1 (1N5819) ─┬─ TVS SMBJ24A ─ GND
                                        └─ buck (set to 5.2 V) ─ D2 (1N5819) ─┬─ 5V pin
                                                                              │
                                              ┌──[4.7 Ω 1 W]──┬───────────────┤
                                              │               └──|◁── D3 ─────┘
                                           SC1 10 F 2.7 V ‖ 10 kΩ
                                              │
                                           SC2 10 F 2.7 V ‖ 10 kΩ
                                              │
                                             GND
```

- **Feed from constant battery power, not IG1.** The interesting moment is
  when IG1 or the main relay drops; the box has to be alive through it.
- D1: reverse-polarity protection. TVS: load-dump and spikes.
- Two 10 F 2.7 V supercapacitors in series (5 F at 5.4 V) on the 5 V rail,
  each with a 10 kΩ balancing resistor. They charge through 4.7 Ω (about 1 A
  at first, then tapering; without it the buck sees a short at power-up) and
  discharge through D3 around the resistor.
- D2 stops the capacitors feeding back into the buck when the 12 V side dies.
- Energy: the board runs down to about 4.4 V at its 5 V pin (3.3 V regulator
  dropout **[unverified for your board]**). From 4.9 V to 4.4 V, 5 F gives
  ½·5·(4.9² − 4.4²) ≈ 11 J. The box draws about 0.75 W while writing
  **[unverified]**: roughly 10 s of hold-up. The target is 2 s.
- Without supercapacitors: 3 × 10,000 µF 25 V on the 12 V side (after D1)
  gives about 1.5–2 s. That is marginal.

**What the firmware does when power fails.** It cannot see its own 5 V
rail; it watches the battery channel. Rule 9 (`brownout`): battery at the
box below 6.5 V for 3 ms starts a capture at once, whatever else is going
on. The writer puts down the trigger, the config and then the pre-trigger
data newest first, syncing the card after the first second's worth, so
whatever time the capacitors buy is spent on the data nearest the event. An
incomplete file is still readable (FORMAT.md). If a capture was already
running when power died, its pre-trigger part is already on the card.

A 30 s capture is about 1 MB. Over SPI at 20 MHz a decent card takes that
in 1–2 s **[unverified]**. If yours is slower, set `pre_s = 15`, or measure:
bench step 8.

**Standby.** Awake, the box draws about 35 mA from the battery
**[unverified]**. Five minutes after IG1 goes off (`sleep_after_s`) it deep
sleeps and wakes when IG1 returns (GPIO14). Boot takes about a second, so
the first second after key-on is not recorded. Asleep, the draw is the
buck's idle current plus the dividers, a few mA **[unverified]**. Set
`sleep_after_s = 0` to stay awake always (fine for a daily driver, not for a
car that sits for weeks).

## Where to tap on the car

The ECU is in the passenger footwell behind the kick panel. Three plugs:
A (32 pins), B (25 pins), C (31 pins). Tap at the ECU where possible: short
leads, dry, and you see what the ECU sees. Use posi-taps or solder and
heat-shrink; put the series resistor right there.

| channel | where | wire colour | confidence |
|---|---|---|---|
| ECU +B | ECU **B1** (IGP1). B9 (IGP2) is the same feed | YEL/BLK | pin and colour from one pinout table for 99–00 Civic. **Check:** 12 V with key ON, 0 V with key off |
| tach | ECU **A19** (NEP) | BLU | same source. **Check:** pulses with the engine running |
| ignition trigger | ECU **B13** (ICM) | YEL/GRN | same source |
| injector | ECU **B11** (INJ1). INJ2 = B3 RED, INJ3 = B4 BLU, INJ4 = B5 YEL | BRN | same source |
| IG1 | load side of the No. 13 FUEL PUMP 15 A fuse, under-dash box (it feeds the main relay's IG1 terminal) | | fuse name from the 96–00 service manual. **Check:** 12 V only with key ON |
| fuel pump feed | main relay output to the pump, or the pump connector under the rear seat | YEL/GRN | **[unverified]** colour is from a 96–98 diagram |
| battery | a constant-power fuse under the dash, or the battery itself | | **Check:** 12 V with the key out |
| ground | ECU B2 / B10 (PG1, PG2) or a body bolt near the ECU | BLK | same pinout table |
| alternator (optional) | alternator B+ at the under-hood box | | not researched |

Not the pump feed: ECU **A16** (FLR, GRN/YEL) is the wire the ECU grounds to
*switch* the pump half of the main relay. It is a useful extra, though: put
it on the spare analog channel (GPIO8, `alt` in the data) and you can tell
"the ECU stopped asking for the pump" (A16 rises to 12 V) from "the relay
dropped out although asked" (A16 stays low, pump feed falls).

**Main relay terminal numbers: do not rely on any list.** Two sources
disagree on the numbering (a 96–98 guide has terminal 2 as ground and 6 as
the starter input; the 96–00 manual page has 2 as starter, 3 ground, 5 IG1,
7 battery). Identify the wires at the relay plug by colour and by meter.

Sources: OBD2B ECU pinout table for 1999–2000 Civic
(<https://civic-eg.com/causeforalarm/version9/resources/ecu_pinouts/obd2b_pinouts.html>);
Honda Civic 1996–2000 service manual, PGM-FI main relay page
(<https://manualslib.com/manual/464698/Honda-Civic.html?page=508>);
96–98 main relay wiring
(<https://mail.troubleshootmyvehicle.com/honda/1600/main-relay-wiring-diagram>).

## Bill of materials

Prices are rough guesses, not quotes.

| part | qty | have? | about |
|---|---|---|---|
| ESP32-S3 dev board with 8 MB PSRAM (N16R8 or N8R8) | 1 | yes | |
| automotive 12 → 5 V buck, adjustable | 1 | yes | |
| microSD breakout + card (≤ 32 GB) | 1 | yes | |
| SMBJ24A TVS | 1 | yes | |
| PC817 | 3 | yes | |
| 1N5819 Schottky (D1, D2, D3, clamps if no BAT54S) | 3–13 | probably | $1 |
| BAT54S dual Schottky | 5 | | $1 |
| 2N7000 or BSS138 | 2 | probably | $1 |
| zener 5.1–10 V (gate clamps) | 2 | probably | |
| 1N4148 | 2 | probably | |
| supercapacitor 10 F 2.7 V | 2 | | $4 |
| resistors: 47k ×5 (0.5 W), 6.8k 1 % ×5, 33k, 10k ×4, 100k ×2, 1M ×2, 8.2k, 4.7k ×3, 1k ×4, 330 Ω, 4.7 Ω 1 W | | probably | $2 |
| capacitors: 10 nF ×5, 100 nF ×3, 470 µF 16 V | | probably | |
| inline fuse holder + 1 A fuse, add-a-fuse tap | 1 + 1 | | $5 |
| momentary button, LED | 1 + 1 | probably | |
| posi-taps or solder sleeves, 22 AWG wire, enclosure | | | $10 |
| NEO-6M GPS (optional) | 1 | yes | |

## Bench test before it goes in the car

You need a bench supply, a second switchable 12 V output (or three toggle
switches from one supply), and a pulse source: a function generator, or any
5 V square wave. Wire the supply to the box's power input and, through
switches, to the four rail inputs.

`hondalog-bb live` shows the values; `hondalog-bb list` and `fetch` pull
captures; `hondalog-bb info <file>` prints the order of events and a verdict.

| # | what | do | expect |
|---|---|---|---|
| 1 | self-test | `hondalog-bb selftest` with 12 V on battery input | every line PASS |
| 2 | rails | 8 V, 12 V, 15 V on all rails | each within 0.1 V after calibration |
| 3 | edges | 233 Hz square wave into tach and ignition inputs (that is 7,000 rpm), 30 Hz 10 % duty into the injector | 7000 rpm ± 10, injector 3.33 ms. Leave it 10 min; self-test must still show `0 dropped` |
| 4 | "running" | with 3 in place and all rails up for 1 s | LED double-blinks; `bb_running` = 1 |
| 5 | **main relay** | open the ECU +B switch only | event `ecub_glitch` or `ecub_drop`; verdict "ECU +B dropped while IG1 and battery stayed up". Then stop the pulses within a second to mimic the engine dying |
| 6 | **igniter** | stop the tach signal only, keep the ignition trigger going | event `tach_stop` after 250 ms; verdict "Tach stopped while the ECU kept sending ignition pulses" |
| 7 | **fuel pump** | open the pump switch, keep pulses 2 s more, then stop them | event `pump_drop`; verdict "Fuel pump feed dropped, the engine died after it" |
| 8 | key off | open the IG1 switch (ECU +B and pump with it, as in the car) | event `ig1_drop`; verdict "IG1 dropped first" |
| 9 | 1 ms dropout | interrupt ECU +B for about 1 ms (tap the wire, or a MOSFET and the generator) | `ecub_glitch`; in replay `bb_ecub_min` goes to about 0 V for one or two samples while `bb_ecub` barely moves |
| 10 | battery dip | drop the supply to 8 V for half a second with pulses running | event `vbat_dip` |
| 11 | button | press it | event `button`, LED flickers 10 s |
| 12 | **power loss** | with pulses running, pull the box's supply | on next power-up `hondalog-bb list` shows a new capture with reason `brownout`; `info` reads it. "complete" means the capacitors outlasted the whole write. If it says incomplete, note how many ms of data it holds: that is your real hold-up |
| 13 | no card | pull the card, power up | slow even blink; live data still works |
| 14 | clock | connect the laptop for 10 s, trigger with the button | `info` shows the right wall-clock time |

Without a generator, the simulator shows what each result should look like:
`hondalog-bbsim --fault main_relay` and `hondalog-bb live --port /tmp/hondalog-bbsim`.

## Porting (RP2040 / Pico 2)

Only `main/hal/hal_esp32s3.c` knows the chip. A port implements
`main/hal/bb_hal.h`:

- ADC: round-robin with DMA into a ring, 5 channels at 5 kHz each. The
  RP2040 has only 4 external ADC inputs (3 on a Pico), so the alternator
  channel goes, and one rail needs an external ADC or a comparator.
- Edges: a PIO program per pin that pushes a timestamp on each edge.
- Ring buffer: 30 s is 840 KB plus edges. The RP2040 has 264 KB, the
  RP2350 520 KB. `bb_engine_init` shrinks `pre_s` to fit what
  `hal_big_alloc` returns (about 6 s on an RP2040, 14 s on a Pico 2), or
  add SPI PSRAM.
- Storage: FatFs over SPI behind the seven `bb_io_t` functions.
- USB: TinyUSB CDC.

`bbcore` itself is plain C99 and already runs on a PC in the tests.

## Not verified

- The firmware has not been compiled or run on a board.
- ECU pins B1, A19, B13, B11, A16 and their colours: one web table, not
  checked against the car or a factory manual.
- Fuel pump feed wire colour and the main relay's terminal numbering for a
  2000 model.
- Voltage levels and drive strength of the tach (A19) and ignition trigger
  (B13) signals.
- Injector flyback voltage (60–80 V is a typical figure, not measured).
- PC817 switching times with these resistor values.
- Current draw awake, asleep and while writing; regulator dropout of your
  dev board; therefore the hold-up time.
- SD write speed over SPI, and so whether a full 30 s capture lands within
  2 s on your card.
- Zener leakage figures.
- ADC accuracy after calibration (the ±0.1 V target).
- The S3's ADC rate in continuous mode is known to be approximate; the
  firmware measures and tracks it, tested on the PC with a 3 % error.
