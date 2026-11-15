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
poller thread, real protocol code, real recorder writing the native log,
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
1366x768, Daylight, 8 s per screen, after the backends were bound
(`ui/wired.py`): W1 link at true rates (fast class about 17 Hz, each read
published on arrival), W3 math channels and alarms on the poller thread,
W4 recorder writing a native log with raw bytes, W2's simulated black box
at 50 Hz, 41 tiles (24 ECU + 8 black box + 9 math).

| Screen | fps | min fps | frame p50 / p95 / max (ms) | CPU % | latency p50 / p95 / max (ms) |
|---|---|---|---|---|---|
| Sensors, 41 tiles | 30.3 | 29.5 | 3.3 / 8.2 / 12.8 | 21.5 | 4.7 / 14.8 / 16.0 |
| Drive | 30.3 | 29.7 | 1.8 / 6.0 / 8.7 | 16.6 | 4.3 / 15.4 / 17.2 |
| Map 3D (2D fallback) | 30.3 | 29.6 | 4.0 / 9.2 / 15.3 | 22.2 | 5.0 / 14.9 / 18.0 |
| Map 3D, orbiting | 30.3 | 27.9 | 3.2 / 6.1 / 17.4 | 35.7 | 5.7 / 14.4 / 16.5 |
| Replay, live lanes following the bus | 30.3 | 29.6 | 0.3 / 18.6 / 31.4 | 26.0 | 5.0 / 13.8 / 16.5 |
| Replay, playing a file | 30.3 | 29.5 | 5.2 / 9.4 / 23.9 | 28.5 | 7.0 / 14.7 / 16.6 |
| Raw tools, sweeping | 30.3 | 29.6 | 0.3 / 4.0 / 19.5 | 15.6 | 7.4 / 17.6 / 20.0 |
| Tune (2D fallback) | 30.3 | 29.5 | 3.0 / 5.6 / 12.1 | 19.3 | 5.8 / 14.3 / 17.2 |
| Alarms | 30.3 | 29.5 | 0.1 / 3.0 / 12.3 | 13.4 | 5.6 / 14.0 / 17.2 |
| Sensors again (traces full) | 30.3 | 29.5 | 3.8 / 13.9 / 23.9 | 26.7 | 6.2 / 13.6 / 15.9 |

Peak RSS 167 MB. No link errors. All four targets are met.

Against the numbers before the backends were bound (stand-in sources,
32 tiles): frame rate and frame times are unchanged, latency is lower (the
link stamps each read itself, and reads are published one by one instead of
once per sweep), and CPU is higher: Sensors went from 20 % to between 21 and
30 % from run to run. Most of that is the simulated black box, which is now
W2's real simulator: a 1 kHz model of the box running in this process, where
the stand-in was a few lines at 50 Hz. `tools/bench_ui.py --no-blackbox`
leaves it out: Sensors is then 13 to 15 % and Drive 10 %. With a real box
that model's cost is not on the laptop; the 50 Hz frames and eight more
live tiles are.

Latency is dominated by waiting for the next 33 ms frame; the UI adds no
queue of its own.

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
- A tile repaints only when its text, gauge pixel, state or trace changed.
  Its strings, fonts, pens and the sparkline polygon are prepared outside
  `paintEvent`; the trace is stroked into a pixmap at 2 Hz and blitted.
- The 2 Hz sparkline redraws are spread over four frames.
- Replay puts all 13 traces in one ViewBox. A file view is stroked once and
  cached, so moving the cursor only blits a strip. A live view draws each
  trace as two 1 px lines, which is several times faster than a stroked
  2 px line.
- The 3D view repaints on demand: when data or the camera changed.
- MSAA steps down 4x, 2x, off when 30 frames in a row take over 12 ms.
