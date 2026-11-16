# UI performance: what was measured, and what was not

Targets (docs/research/FEATURES.md, performance bar): 30 fps steady with 8
plots and 24 tiles, never below 20 fps; under 50 % of one core; under 100 ms
from serial byte to pixel.

## How to measure

```sh
QT_QPA_PLATFORM=offscreen python3 tools/bench_ui.py            # no display needed
python3 tools/bench_ui.py --onscreen --seconds 20               # on the T430
hondalog --port sim: --connect --profile                        # overlay in the bottom bar
```

The bench runs the real app against the built-in simulator (`sim:`): real
poller thread, real protocol code, real recorder writing into the database,
W2's simulated black box at 50 Hz. Per screen it reports:

- **fps / min**: frame timer rate achieved; min = slowest 1 % of frames.
- **frame ms**: work per frame (bus pump, refresh, and the paint, which the
  profile mode flushes inside the frame so it can be timed).
- **cpu %**: the whole process as a share of one core. That includes the
  poller thread, the simulated ECU and the simulated black box.
- **latency**: from the last reply byte of a poll sweep leaving the
  (simulated) wire to the frame that shows it being painted.

## Measured here (not on the T430)

Intel Xeon 2.1 GHz, 2 cores, PySide6 / pyqtgraph, Qt offscreen platform,
1366x768, Daylight, 8 s per screen, with the backends bound (`ui/wired.py`):
W1 link at true rates (fast class about 17 Hz, each read published on
arrival), W3 math channels and alarms on the poller thread, W4 recorder
writing a drive with raw bytes into SQLite, W2's simulated black box at
50 Hz, 41 tiles (24 ECU + 8 black box + 9 math).

| Screen | fps | min fps | frame p50 / p95 / max (ms) | CPU % | latency p50 / p95 / max (ms) |
|---|---|---|---|---|---|
| Sensors, 41 tiles | 30.3 | 29.4 | 2.1 / 3.8 / 5.4 | 12.8 | 1.6 / 13.7 / 17.9 |
| Drive | 30.3 | 29.5 | 1.3 / 4.6 / 10.6 | 11.5 | 3.5 / 15.0 / 16.9 |
| Map 3D (2D fallback) | 30.3 | 29.5 | 3.5 / 6.4 / 9.4 | 15.4 | 5.2 / 14.5 / 17.1 |
| Map 3D, orbiting | 30.3 | 28.3 | 2.9 / 6.1 / 12.2 | 31.0 | 5.3 / 13.8 / 17.0 |
| Replay, live lanes following the bus | 30.3 | 29.5 | 0.2 / 17.8 / 25.6 | 21.0 | 4.5 / 10.4 / 15.4 |
| Replay, playing a file | 30.3 | 29.4 | 4.7 / 7.5 / 10.4 | 21.7 | 2.4 / 15.0 / 16.9 |
| Raw tools, sweeping | 30.3 | 29.5 | 0.3 / 5.3 / 14.5 | 11.3 | 8.2 / 17.8 / 20.0 |
| Tune (2D fallback) | 30.3 | 29.5 | 3.7 / 6.5 / 10.1 | 15.5 | 3.6 / 14.1 / 15.9 |
| Alarms | 30.3 | 29.6 | 0.1 / 2.4 / 5.2 | 8.7 | 5.0 / 13.9 / 17.0 |
| Sensors again (traces full) | 30.3 | 29.5 | 2.9 / 4.9 / 6.9 | 16.0 | 3.6 / 15.0 / 18.7 |

Peak RSS 177 MB. No link errors. All targets are met, including the one the
bench now checks for the screen that is up all day: all sensors at or under
20 % of a core with the simulated black box running. From run to run on
this (shared) machine Sensors is 12 to 17 % and "Sensors again" 15 to 19.5 %.

"Sensors again" is the honest number for that screen: after a minute the
sparklines are full, and stroking 41 of them is real work. Before this was
looked at, the same run gave 23.7 % (Sensors) and 29.7 % (Sensors again),
with a frame p95 of 9.5 and 14.6 ms. Where it went, measured per thread
(`/proc/self/task`) and with cProfile on the UI thread:

| What | Before | After |
|---|---|---|
| black box thread: W2's 1 kHz model of the box, in this process | 6.1 % | 2.6 to 3.1 % |
| ... of that, the model itself, per simulated millisecond | 38 us | 12.6 us |
| UI thread, sparklines full | about 16 % | 8.4 to 9.6 % |
| tile paint calls per second | about 290 | about 160, most of them one band |

- The simulated black box next to a simulated ECU now runs the cheap model
  (`Car(sub=1)`: one ADC reading per millisecond instead of five, the filter
  steps in closed form), and its in-process port blocks until a frame is due
  instead of waking every 2 ms. `hondalog-bbsim`, `bbsim:` and the real box
  are untouched: the full model is still what the firmware's tests run on.
- A tile whose channel arrives faster than the frame timer (the box at
  50 Hz) redraws its value 10 times a second, not 30.
- A tile is repainted in bands: a new value repaints the value and gauge, a
  new sparkline sample the sparkline and footer, and the header (label,
  marker) only when the state changes.
- Each tile strokes its sparkline once a second (the bus samples it at
  2 Hz), the tiles taking turns over 30 frames.
- A channel that is read again with the same number costs a comparison.

`tools/bench_ui.py --no-blackbox` leaves the simulated box out: Sensors is
then 11 to 12 % and Drive 9 %. With a real box the model's cost is not on
the laptop; the 50 Hz frames and eight more live tiles are.

Latency is dominated by waiting for the next 33 ms frame; the UI adds no
queue of its own.

### The Map screen (F8)

Same machine and bench, a later run (`tools/bench_ui.py --check`, all
targets met; the other screens were 1 to 3 ms slower at p95 in that run,
the machine is shared). The drive is one synthetic hour around the Pearl
loop with a GPS fix every 0.1 s: **36 000 points**, the fixture tiles of
`tools/fixture_tiles.py` cached on disk. "map-pan" drags the map 6 px a
frame, turning every 2 s and zooming a level in or out every 3 s;
"map-play" plays Replay with the dot moving on the map.

| Screen | fps | min fps | frame p50 / p95 / max (ms) | CPU % | latency p50 / p95 / max (ms) |
|---|---|---|---|---|---|
| Map, dragging and zooming an hour | 30.3 | 25.3 | 0.1 / 7.6 / 23.4 | 30.6 | 5.5 / 15.1 / 20.2 |
| Map, Replay playing (the dot moves) | 30.3 | 24.7 | 0.2 / 2.3 / 11.3 | 10.4 | 5.5 / 14.4 / 19.1 |

The map's own paint: p50 1.4 ms, p95 5.7 ms, max 12.8 ms over about 530
paints. At the zoom that fits the hour (14) the track is 2 728 of the
36 000 points; its image takes 22 to 24 ms to draw, spread over frames in
6 ms slices. The CPU of "map-pan" includes the tile threads decoding and
theming the tiles that scroll in (and the simulator and the recorder, as in
every row).

How it got there: drawn the plain way (every kept point stroked in every
paint, the GPS jitter kept by a pixel-grid decimation) the same hour took
**206 ms a paint** (4.6 fps while dragging). Then: decimation by radial
distance (1.5 px), the track drawn once per zoom into an image that panning
moves, short runs of 64 points with bevel joins (Qt strokes many short
antialiased polylines four times faster than one long one that crosses
itself), and after a zoom the old image scaled until the new one is done.

## 3D with OpenGL: only software rendering was available

There is no GPU here. Under Xvfb, Mesa's llvmpipe gives a 4.5 compatibility
context, so the OpenGL code path runs and is tested, but every pixel is
rasterised on the CPU. Those timings say nothing about an HD 4000:

| Screen (llvmpipe, 964x628, MSAA 4x) | fps | frame p50 (ms) | CPU % |
|---|---|---|---|
| Map 3D, live point moving | 30.3 | 29 | 110 |
| Map 3D, orbiting every 16 ms | 22.8 | 28 | 147 |
| Tune | 29.7 | 25 | 81 |

What does carry over: the scene is 27 draw calls (7 geometry + 20 labels;
budget 40) and 1,110 triangles in surface mode (budget 6,000; 2,400 more
with every bar drawn), and pyqtgraph's Python-side cost of issuing those
calls was 1.6 to 1.9 ms per frame.

One thing to check on the T430: a `QOpenGLWidget` anywhere in a window makes
Qt composite the whole window through OpenGL. With llvmpipe that added about
8 ms to every frame on every screen (Sensors went from 4.6 to 13-16 ms). On
a real GPU this is a texture upload of the changed rectangle and should be
small, but it has not been measured. If it is not small, `hondalog --no-gl`
(or `HONDALOG_GL=0`) removes it: Map 3D and Tune then use the 2D heat map.

## What keeps it cheap

- No per-sample signals. Sources publish into `ChannelBus`; one 33 ms timer
  reads it.
- A tile repaints only when its text, gauge pixel, state or trace changed,
  and then only the band that did. Its strings, fonts, pens and the
  sparkline polygon are prepared outside `paintEvent`; the trace is stroked
  into a pixmap once a second and blitted.
- The sparkline redraws are spread over 30 frames: one or two per frame.
- Replay puts all 13 traces in one ViewBox. A file view is the replay
  session's min/max envelope for the visible window (at most two points per
  pixel column, however long the drive), stroked once and cached, so moving
  the cursor only blits a strip and only zooming or panning draws anew. A
  live view draws each trace as two 1 px lines, which is several times
  faster than a stroked 2 px line.
- The tune grid repaints the cells whose outline or trail bar changed, not
  the table.
- The 3D view repaints on demand: when data or the camera changed.
- MSAA steps down 4x, 2x, off when 30 frames in a row take over 12 ms.
- The Map screen keeps an image of the track per zoom and only moves it
  while panning; a new image (zoom, or the view nearing the edge of a big
  track) is drawn a few ms per frame while the old one is shown scaled.
  Tiles are decoded and themed on the fetcher's two threads; the UI thread
  turns an image into a pixmap. Everything the paint draws (positions,
  elided strings, the grid path, pens) is prepared when the view changes.
