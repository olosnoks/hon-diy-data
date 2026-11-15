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
poller thread, real protocol code, real recorder writing CSV, simulated
black box at 50 Hz. Per screen it reports:

- **fps / min**: frame timer rate achieved; min = slowest 1 % of frames.
- **frame ms**: work per frame (bus pump, refresh, and the paint, which the
  profile mode flushes inside the frame so it can be timed).
- **cpu %**: the whole process as a share of one core. That includes the
  poller thread, the simulated ECU and the simulated black box.
- **latency**: from the last reply byte of a poll sweep leaving the
  (simulated) wire to the frame that shows it being painted.

## Measured here (not on the T430)

Intel Xeon 2.1 GHz, 2 cores, PySide6 6.12 / pyqtgraph 0.14, Qt offscreen
platform, 1366x768, Daylight, 10 s per screen. The built-in simulator polls
at about 22 Hz, twice what the real K-line gives, so tiles repaint twice as
often as they will in the car.

| Screen | fps | min fps | frame p50 / p95 / max (ms) | CPU % | latency p50 / p95 / max (ms) |
|---|---|---|---|---|---|
| Sensors, 32 tiles + 32 sparklines (after 90 s, traces full) | 30.3 | 29.6 | 4.6 / 13.5 / 28.4 | 20.2 | 8.8 / 18.6 / 21.3 |
| Drive | 30.3 | 29.6 | 2.1 / 6.8 / 9.6 | 10.1 | 10.8 / 19.4 / 24.0 |
| Map 3D (2D fallback) | 30.3 | 29.6 | 3.6 / 6.7 / 14.9 | 12.7 | 10.5 / 19.1 / 21.9 |
| Replay, live: 13 lanes following the bus | 30.3 | 29.6 | 0.2 / 18.7 / 36.7 | 17.8 | 9.7 / 18.5 / 22.4 |
| Replay, playing a file | 30.3 | 29.6 | 4.1 / 7.6 / 9.8 | 16.2 | 7.7 / 17.8 / 19.8 |
| Raw tools, sweeping | 30.3 | 29.5 | 0.2 / 11.6 / 16.1 | 7.5 | 8.5 / 19.2 / 20.3 |
| Tune (2D fallback) | 30.3 | 29.6 | 2.8 / 7.6 / 14.6 | 11.6 | 9.8 / 19.1 / 20.8 |
| Alarms | 30.3 | 29.6 | 0.2 / 2.7 / 7.1 | 4.7 | 8.4 / 18.6 / 20.1 |

Peak RSS 154 MB. No link errors. All targets met in this environment. The
same run on the Debian trixie versions (PySide6 6.8.3, pyqtgraph 0.13.7)
gave the same figures within noise.

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
