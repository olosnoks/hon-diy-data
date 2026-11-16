# Mapping the P2P

The address map says which byte of ECU memory is which sensor and how to
turn it into a number. **The shipped map is a guess.** Its addresses and
formulas come from kerpz's ArduinoHondaOBD project, which was tested on a
P2T (another OBD2 Honda ECU) and a chipped P30. Nobody has checked one of
them on a P2P. Every channel therefore starts as `unverified`, and the app
shows an amber `?` on it until you prove it.

This page is the method for proving them, and the reference for the map
file itself.

- [Your map and the shipped map](#your-map-and-the-shipped-map)
- [The workflow](#the-workflow)
- [Guided verification](#guided-verification)
- [The finder: which address is it really?](#the-finder-which-address-is-it-really)
- [Marking a channel verified](#marking-a-channel-verified)
- [The map file (schema 2)](#the-map-file-schema-2)
- [Math channels](#math-channels)
- [Alarms](#alarms)
- [Expression reference](#expression-reference)
- [Units](#units)
- [Not verified](#not-verified)

## Your map and the shipped map

| File | Is |
|---|---|
| `/opt/hondalog/hondalog/data/addressmap.toml` | The shipped default. Read-only |
| `~/.config/hondalog/addressmap.toml` | **Yours.** Copied from the default the first time anything needs it. Every tool reads and writes this one |

To get the shipped map back, delete your copy and start the app. Updating
hondalog never touches your copy.

Changes to the file take effect when you **disconnect and connect again**.

`hondalog-map` works on your map unless told otherwise:

```sh
hondalog-map status                    # every channel: verified or not, and how to check it
hondalog-map hash                      # the 12-digit hash that identifies this map's decoding
hondalog-map --map other.toml status   # another file
hondalog-map --variant P2T status      # another ECU variant of the same file
```

## The workflow

1. **Run first contact** ([Cable day](monday-first-contact.md)). Without
   `Honda protocol OK` there is nothing to map.
2. **Run the engine-off checks.** `koeo` and `tps_sweep` need only the key
   on. They cover RPM, speed, throttle, MAP, baro, battery and both
   temperatures.
3. **Run the idle checks** with the engine warm: `idle`, then `steady_3000`.
4. **Fix what failed** with the finder, then run the check again.
5. **Run the slow ones** when the chance comes: `warmup` (needs a cold
   engine), `drive` (needs a passenger), `ac_switch`.
6. **Leave the rest unverified** until there is a way to check them. A
   channel no script can prove says so in `hondalog-map status`:
   `check with: finder / ISO cross-check`.

Order matters. The finder ranks addresses against a reference channel, and
the result is only as good as that reference. Prove RPM first; then RPM can
be used to find other things.

## Guided verification

Seven scripts. Each tells you what to do, records every byte of the sweep
while you do it, and gives a verdict per channel.

```sh
hondalog-map scripts
```

| Script | Conditions | Channels it checks | Asks you for |
|---|---|---|---|
| `koeo` | Key ON, engine off | rpm, vss, tps, map, baro, batt, ect, iat, mil, closed_loop | battery voltage on a multimeter; the air temperature; whether the engine has been off 6 hours |
| `tps_sweep` | Key ON, engine off; press the pedal slowly to the floor | tps, rpm | |
| `idle` | Fully warm, idling in neutral, everything switched off | rpm, map, batt, tps, vss, ect, inj, ign, o2, closed_loop, stft, ltft, iacv | the tachometer reading; battery voltage |
| `steady_3000` | Idle, hold 3000 rpm, back to idle | rpm, tps, ign, batt, vss | the RPM you held (default 3000) |
| `warmup` | Cold start, idle until the radiator fan runs | ect, iat, rpm, closed_loop | the air temperature |
| `drive` | A passenger works the laptop; hold a steady speed | vss, rpm | the speedometer reading **in km/h** (default 60, which is 37 mph) |
| `ac_switch` | Idling or key ON; switch the A/C on and off | ac_sw | |

### From the terminal

```sh
hondalog-map verify koeo --port /dev/hondalog
```

It asks for the reference readings (Enter skips one), then walks through
the phases: do what it says, press Enter when done. At the end it prints the
verdicts, saves the evidence, and asks for each channel that passed whether
to mark it verified.

Works over ssh and with no display, so a second person can run it from
another machine.

| Option | Does |
|---|---|
| `--port PORT` | Required. `sim:` practises on the simulator |
| `--ref NAME=VALUE` | Give a reference reading up front: `batt_meter`, `tach_rpm`, `ambient_c`, `speedo_kmh` |
| `--yes cold_engine` | Answer yes to the cold-engine question up front |
| `--auto` | No prompts: each phase runs its minimum time |
| `--speed N` | With `--auto`: scale the phase times |
| `--confirm rpm,tps` | Mark these verified if they pass, without asking |
| `--force` | Also confirm results that are only `plausible` |
| `--hi 0xFF` | Highest address to record (default 0x3F). Use 0xFF so the finder has the whole memory to search if a check fails |
| `--evidence DIR` | Where to save the evidence |

Example with everything given up front:

```sh
hondalog-map verify idle --port /dev/hondalog --ref tach_rpm=750 --ref batt_meter=14.1
```

### In the app

Raw tools (F5), the "Map a channel · guided" card. Pick the channel, press
N, follow the steps. See
[User guide](user-guide.md#guided-mapper). It runs the first script that can
check the channel you picked and judges that one channel.

### Verdicts

| Verdict | Means | Marked verified? |
|---|---|---|
| `pass` | A check that can actually prove the address held. Throttle went from under 5 % to over 90 % as you pressed; battery within 0.3 V of your meter; RPM within 75 of the tachometer | If you say yes |
| `plausible` | The value looks sane, which proves little. RPM reading 0 with the engine off is plausible for any byte that holds zero | Only with `--force` ("Accept anyway" in the app) |
| `fail` | A check failed. The tool lists the addresses that *did* behave as the check expects | No. The app can move the channel to one of the candidates |
| `skip` | No reference reading was given, or the check does not apply (engine not cold) | No |

A typical line of output:

```
  rpm: FAIL
      FAIL RPM agrees with the tachometer: 1384 vs typed in 750 (apart 634, allowed 75)
      FAIL RPM is a normal idle: 1288 to 1502 (median 1384), 0% inside 550..950
  map: PLAUSIBLE
      ok   MAP shows idle vacuum: 34.38 to 35.81 (median 35.1), 100% inside 20..48 (plausibility only)
```

The limits the scripts use (idle 550 to 950 rpm, MAP 20 to 48 kPa at idle,
charging 13.2 to 14.9 V and so on) are ordinary D-series expectations, not
figures measured on this car.

### Evidence

Every run saves two files:

| File | Holds |
|---|---|
| `verify_DATE_TIME_SCRIPT.json` | The report: verdicts, checks, reference readings, map version and hash |
| `verify_DATE_TIME_SCRIPT.npz` | Every raw byte of every sweep, with times |

`hondalog-map verify` puts them in `~/hondalog/verification/`. The in-app
mapper puts them in `~/hondalog/logs/`.

## The finder: which address is it really?

When a check fails, or a channel has no script, sweep the memory while
something changes and rank every address by how well it followed.

### From the terminal

```sh
# what moves with RPM? (rev the engine during the 30 seconds)
hondalog-map find --port /dev/hondalog --channel rpm --seconds 30

# what reads 12.4 V right now through the battery formula?
hondalog-map find --port /dev/hondalog --value 12.4 --formula "x / 10.45"
```

Each address is tried as a single byte and as a 16-bit value in both byte
orders. Output:

```
addresses that move with rpm:
  0x25 u8        score 1.000  r +0.998  rho +1.000  n 9  fit: x * 2.62522 + 1162.71 (rmse 3.02)
  0x90 u16be     score 1.000  r +0.995  rho +1.000  n 9  fit: x * 0.2705225 - 110.419 (rmse 4.6)
```

`fit` is a suggested formula by least squares against the reference.

| Option | Does |
|---|---|
| `--channel NAME` | Rank against this channel of the map |
| `--value NUMBER` | Find addresses that read this value now |
| `--formula EXPR` | The formula to read `--value` through (default: the channel's, else `x`) |
| `--tol N` | Relative tolerance for `--value` |
| `--seconds N` | How long to sweep |
| `--inverse` | Also try 1/x (OBD1 ECUs store RPM as a period) |
| `--top N` | How many results |
| `--hi 0xNN` | Highest address |

`--channel` ranks against what the map *currently decodes* for that channel.
If that channel is itself wrong, so is the ranking. Use a verified channel,
or `--value` with a number from a meter.

### In the app

Raw tools (F5), S to sweep, **R** for Rank addresses, **G** to rank. There
you can also rank against a shape you make by hand ("rises steadily"), which
needs no reference channel at all: press C to clear, then press the pedal
slowly or let the engine warm up while it sweeps.
[User guide](user-guide.md#rank-addresses).

**Assign to channel…** writes the address, length and byte order into your
map. The channel becomes unverified, evidence for the old address is
dropped, and the map version moves on. The formula is not touched: check it
with the guided script afterwards.

### Cross-checking against OBD2 PIDs

`hondalog/mapping/crosscheck.py` compares a Honda channel with the standard
OBD2 PID for the same thing (RPM, ECT, IAT, MAP, TPS, speed, both trims, O2,
timing) and says agree, scaled (with a corrected formula) or no match. It is
a library function with no command yet, and it has not been run against a
real ISO 9141-2 session.

## Marking a channel verified

Three ways, best first:

| Way | Writes |
|---|---|
| A guided check that passed (terminal or app) | `status = "verified"` and a `[channel.evidence]` table: date, method (`guided:idle`), result, a summary of what was measured, the evidence file, the map version and hash, and the reference reading |
| Right-click a tile on Sensors, "Mark verified" | `status = "verified"`, method `manual` |
| Edit the file | Whatever you type |

Rules the tools keep:

- Nothing is marked until you say yes.
- Changing a channel's address, length, byte order or formula **through the
  tools** resets it to unverified and bumps the map version. Editing the
  file by hand does not, so do it yourself.
- Verified marks belong to one ECU variant. They never carry over to
  another.

To see what changed between two maps, and whether old logs would decode
differently:

```sh
hondalog-map diff old.toml new.toml
```

```
decoding changed: 44979f9238b1 -> 3bf378891115
~ channel tps: addr 20 -> 22
+ math vacuum
```

(Addresses in this output are decimal.) The exit status is 1 when the maps
differ.

Every drive records the map version and hash it was decoded with, and keeps
the raw bytes. After you correct the map, `hondalog-log redecode DRIVE`
decodes an old drive again with the new one
([Data and SQL](data-and-sql.md#redecode)).

## The map file (schema 2)

TOML. The shipped file explains every field in its comments; this is the
summary.

### Top level

```toml
schema = 2
version = "p2p-guess-2 (kerpz P2T map)"
ecu = "P2P expected, not confirmed"
ecu_variant = "P2P"
```

| Key | Is |
|---|---|
| `schema` | 2. A schema 1 map (0.1.0) still loads as it is |
| `version` | Free text. **Bump it when you change the file**; it is written into every drive |
| `ecu_variant` | Which of the `[variants]` to load |

The **hash** (`hondalog-map hash`) is computed from everything that decides
a decoded value, so two maps with the same version text but different
formulas are still told apart.

### `[variants]`, `[ecu_id]`, `[protocol]`

```toml
[variants.P2P]
description = "2000 Civic EX, D16Y8, OBD2B. The target. Not confirmed on the car."

[ecu_id]
addr = 0x76
length = 10

[protocol]
# max_read_len = 16
# reply_timeout_s = 0.2
# init_sequence = "68 6a f5 af bf b3 b2 c1 db b3 e9"
# rate_fast_hz = 20
```

`[protocol]` overrides the link's framing and timing. Everything in it is
commented out in the shipped file, which means "use the default". The keys:

| Key | Default | Is |
|---|---|---|
| `baud` | 9600 | |
| `cmd_read` | 0x20 | The read command byte |
| `reply_header` | 0x00 | First byte of a reply; -1 accepts any |
| `init_sequence` | 11 bytes | The wake-up bytes; `""` sends none |
| `init_delay_s` | 0.3 | Pause after them |
| `max_read_len` | 16 | Longest block to ask for. First contact measures the real one |
| `rate_fast_hz`, `rate_medium_hz`, `rate_slow_hz` | 20, 5, 1 | Target reads per second for each rate class |
| `reply_timeout_s` | 0.2 | Ceiling for the adaptive reply timeout |
| `min_reply_timeout_s` | 0.02 | Its floor |
| `inter_byte_timeout_s` | 0.05 | |
| `inter_request_delay_s`, `max_inter_request_delay_s` | 0.002, 0.05 | Gap between requests: floor and ceiling |
| `adaptive_timing` | true | |
| `retries` | 1 | Extra attempts per read |

### `[[channel]]`

```toml
[[channel]]
name = "tps"
label = "Throttle"
addr = 0x14
length = 1
formula = "(x - 24) / 2"
units = "%"
min = 0
max = 100
decimals = 0
gauge = true
plot = true
status = "unverified"
rate = "fast"
source = "kerpz hobd_uni2 (P2T)"
```

| Field | Is |
|---|---|
| `name` | Column name: letters, digits, underscore |
| `label` | Text on the screen |
| `addr` | RAM address, 0x00 to 0xFF |
| `length` | Bytes to read, 1 to 4 |
| `formula` | An [expression](#expression-reference). `x` is the bytes as one unsigned number, `b[0]`, `b[1]` the single bytes |
| `endian` | `"big"` (default) or `"little"`: byte order of `x` |
| `units` | See [Units](#units) |
| `min`, `max` | Gauge and plot range |
| `decimals` | Digits after the point |
| `kind` | `"value"` (default) or `"flag"` (a 0/1 lamp) |
| `status` | `"unverified"`, `"verified"`, or `"suspect"` (checked, and it failed) |
| `rate` | `"fast"`, `"medium"` or `"slow"`: how often it is read. Left out, the name decides: rpm, map, tps, inj, ign are fast; o2, stft, ltft, vss medium; the rest slow |
| `source`, `notes` | Free text |

Two sub-tables:

```toml
[channel.evidence]                 # written by the verification tool
date = "..."
method = "guided:idle"

[channel.variants.P28]             # this channel on another ECU
formula = "1875000 / (x + 1)"
```

A variant can override `addr`, `length`, `formula`, `endian`, `units`,
`min`, `max`, `decimals`, `rate`, `label`, `source`, `notes`, or drop the
channel with `absent = true`.

### How rate classes become read rates

The planner merges nearby addresses into as few requests as it can, and the
scheduler interleaves fast, medium and slow reads. With the shipped map on
the simulator (2 ms ECU reply time, 1 ms USB latency) that gives about
17 reads per second for the fast class, 4.6 for medium and 1 for slow.
The 20 Hz target does not fit on a 9600 baud wire with this map; the ECU
chip menu shows the shortfall per class. Mark fewer channels `fast` if you
want the fast ones faster.

### Upgrading a 0.1.0 map

```sh
hondalog-map upgrade
```

Brings a schema 1 map up to schema 2 in place: adds the shipped math
channels and alarms and keeps your comments and edits.

## Math channels

Values worked out from other channels, live and again on replay. They are
their own group on the Sensors screen and their own stream in every drive.

```toml
[constants]
cylinders = 4
inj_flow_cc_min = 240

[[math]]
name = "inj_duty"
label = "Injector duty"
expr = "[inj] * [rpm] / 1200"
units = "%"
min = 0
max = 100
decimals = 1
description = "Share of the time the injector is open."
```

Fields: `name`, `expr`, `label`, `units`, `min`, `max`, `decimals`, `gauge`,
`plot`, `kind`, `description`, `enabled`.

- A math channel may use another math channel. Loops are refused.
- `[constants]` are named numbers any expression can use.
- A math channel is only as good as its inputs. All the shipped ones are
  built on unverified addresses.

Shipped:

| Name | Expression | Is |
|---|---|---|
| `inj_duty` | `[inj] * [rpm] / 1200` | Injector duty, % |
| `fuel_flow` | from duty and injector size | L/h; ignores injector dead time |
| `total_trim` | `[stft] + [ltft]` | % |
| `rpm_rate` | `slope([rpm], 500)` | rpm/s over the last half second |
| `load` | `[map] / [baro] * 100` | % |
| `afr_est` | speed-density air mass over fuel mass | A rough guide only; it is not a wideband |
| `rpm_smooth` | `smooth([rpm], 5)` | Mean of the last 5 readings |
| `map_avg` | `avg([map], 500)` | 0.5 s mean |
| `batt_min_5s` | `minw([batt], 5000)` | Lowest battery reading in 5 s: catches short sags |

The 240 cc/min injector size and the 0.85 volumetric efficiency behind
`fuel_flow` and `afr_est` are assumptions, not measurements.

To add one, append a `[[math]]` block to your map, bump `version`, and
reconnect:

```toml
[[math]]
name = "vacuum"
label = "Manifold vacuum"
expr = "[baro] - [map]"
units = "kPa"
min = 0
max = 100
decimals = 0
```

## Alarms

Checked on every sample, on measured and math channels alike.

```toml
[[alarm]]
name = "batt_low"
label = "Battery low"
channel = "batt"
low = 12.0
hold_s = 3
hysteresis = 0.3
gate = "[rpm] > 400"
severity = "warn"
actions = ["tile", "banner", "marker"]
message = "Battery {value:.1f} V with the engine running"
```

| Field | Is |
|---|---|
| `name` | Letters, digits, underscore |
| `channel` | The channel that `low` / `high` / rate limits look at. Its tile takes the alarm's colour |
| `low`, `high` | Trip below / above this value |
| `rate_low`, `rate_high` | Trip when the value changes faster than this per second, fitted over `rate_window_ms` (default 500) |
| `when` | An expression that trips the alarm, for combinations of channels |
| `clear_when` | An expression that re-arms a `when` alarm (default: `when` being false) |
| `gate` | The rule only runs while this expression is true |
| `hold_s` | Must stay tripped this long before the alarm is raised |
| `clear_hold_s` | Must stay clear this long before it clears |
| `hysteresis` | For `low` / `high`: must come back inside the limit by this much to clear |
| `severity` | `"warn"` or `"critical"` |
| `priority` | Higher shows first (default 50 for warn, 100 for critical) |
| `latching` | `true`: stays up until acknowledged |
| `actions` | Any of `"tile"`, `"banner"`, `"beep"`, `"marker"`, `"capture"` |
| `message` | Text. `{value}`, `{value:.1f}`, `{label}`, `{units}`, `{high}`, `{low}`, `{rate}` and any channel name in braces are filled in |
| `label`, `enabled` | |

What the actions do in the laptop app today:

| Action | Effect |
|---|---|
| `"marker"` | Drops a marker into the log when the alarm is raised |
| `"capture"` | Saves a capture around it, like a stall (30 s before, 10 s after) |
| `"tile"` | The channel's tile takes the alarm state |
| `"banner"`, `"beep"` | Accepted and stored, but the app does not look at them: every active alarm shows in the bottom strip, and every **critical** alarm beeps when raised |

A worked example that saves a capture when the battery sags with the engine
running:

```toml
[[alarm]]
name = "ecu_volts_sag"
label = "ECU voltage sag"
channel = "batt_min_5s"
low = 11.0
hold_s = 0.2
hysteresis = 0.5
gate = "[rpm] > 400"
severity = "critical"
latching = true
actions = ["tile", "marker", "capture"]
message = "Battery sagged to {value:.1f} V at {rpm:.0f} rpm"
```

Until `batt` is verified this will fire on whatever byte 0x17 really is, so
verify first.

## Expression reference

One expression language is used for channel formulas, math channels, alarm
conditions (`when`, `clear_when`, `gate`), trigger rules and export filters.
Nothing in an expression is run as Python: the text is parsed into a small
tree, and only the functions listed here exist.

### Names

| Write | Means |
|---|---|
| `[rpm]` or `rpm` | The channel's current value |
| `[rpm-1]`, `[rpm-3]` | Its value 1 or 3 samples ago |
| `cylinders` | A `[constants]` entry |
| `x`, `b[0]` | In a channel `formula` only: the raw bytes as one number, and one byte |

A channel with no value yet makes the whole expression "no value", unless
`valid()` or `coalesce()` handles it.

### Operators

| Kind | Operators |
|---|---|
| Arithmetic | `+` `-` `*` `/` `//` (floor division) `%` `**` |
| Comparison | `<` `<=` `>` `>=` `==` `!=` |
| Logic | `&&` `\|\|` `!`, or `and` `or` `not` |
| Choice | `a ? b : c`, or `b if a else c` |
| Bits | `&` `\|` `^` `<<` `>>` `~` |

### Plain functions

| Function | Gives |
|---|---|
| `abs(x)` | |
| `min(a, b, ...)`, `max(a, b, ...)` | Smallest / largest **argument** |
| `round(x[, digits])`, `int(x)`, `float(x)`, `floor(x)`, `ceil(x)` | |
| `sqrt(x)`, `exp(x)`, `ln(x)`, `log(x)` (also natural), `log10(x)`, `pow(x, y)` | |
| `sin(x)`, `cos(x)` | Radians |
| `sign(x)` | -1, 0 or 1 |
| `clamp(x, lo, hi)` | x held inside lo..hi |
| `between(x, lo, hi)` | 1 when lo <= x <= hi |
| `bit(x, n)` | Bit n of x; 0 is the least significant |
| `signed(x[, bits])` | Two's complement (default 8 bits) |
| `poly(x, c0, c1, ...)` | c0 + c1·x + c2·x² ... |
| `table(x, x0, y0, x1, y1, ...)` | Straight lines between points; the ends are held |
| `thermistor(x, a, b, c[, full])` | Steinhart-Hart on an ADC count, in °C |

### Functions over time

These remember earlier samples. Windows are in milliseconds (`ms`) or in
samples (`n`).

| Function | Gives |
|---|---|
| `avg(x, ms)` | Mean over the last ms |
| `slope(x, ms)` | Rate of change per second, fitted over the last ms |
| `minw(x, ms)`, `maxw(x, ms)` | Smallest / largest value in the last ms |
| `stdev(x, ms)` | Standard deviation over the last ms |
| `ema(x, ms)` | Low-pass filter with that time constant |
| `smooth(x, n)` | Mean of the last n samples |
| `median(x, n)` | Median of the last n samples |
| `prev(x[, n])` | Value n samples ago; the same as `[x-n]` |
| `delta(x)` | Change since the previous sample |
| `rate(x)` | Change per second since the previous sample |
| `integ(x)` | Running integral over seconds |
| `held(cond, ms)` | 1 once `cond` has been true for ms |

`min(a, b)` and `max(a, b)` compare their arguments. The versions over a
time window are spelled `minw` and `maxw`.

### Other

| Function | Gives |
|---|---|
| `valid(x)` | 1 when x has a value right now, else 0 |
| `coalesce(a, b, ...)` | The first argument that has a value |
| `time()` | Seconds since the start of the session or log |

### Examples

```
[inj] * [rpm] / 1200                         injector duty
[o2] < 0.2 && [tps] > 60 && [rpm] > 3000     lean under load
rate(rpm) < -1500 and rpm > 300              RPM collapsing
table(ect, 0, 1.4, 40, 1.15, 80, 1.0)        a correction curve
coalesce([o2], 0.45)                         O2, or 0.45 while there is no reading
held([batt] < 12, 3000)                      battery under 12 V for 3 s
```

The handheld cannot run the functions over time or `[x-1]`. Math channels
and alarms that use them are left out when the map is compiled for it, and
`hondalog-handheld export-map` lists which.

## Units

Write units in the map as metric: `°C`, `kPa`, `km/h`, `L/h`, `V`, `%`,
`ms`, `rpm`. The database and every export keep the map's units exactly.

The screen converts for display. The default is US units (mph, °F, gal/h),
with MAP and baro left in kPa. Change it under ECU chip menu > Settings >
Units ([User guide](user-guide.md#units)). A channel is converted only if
its `units` text is one the app knows how to convert; anything else is
shown as written.

Alarm limits are written in the map's units too (`high = 105` on `ect` is
105 °C) and are converted along with the value for display.

## Not verified

- Every address and formula in the shipped map.
- The ECU id location (0x76, 10 bytes).
- The verification scripts' limits, which are general D-series
  expectations.
- Whether the MIL bit reads 1 during the key-on bulb check, which the `koeo`
  script assumes.
- The alarm limits, the injector size and the VE guess.
- The OBD2 PID cross-check against a real ISO 9141-2 session.
