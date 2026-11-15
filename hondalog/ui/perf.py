"""Frame-time, CPU and latency bookkeeping for --profile and tools/bench_ui.py."""

from __future__ import annotations

import time

import numpy as np


class Perf:
    def __init__(self, size: int = 20000):
        self.work = np.zeros(size, dtype=np.float32)        # refresh + paint, ms
        self.paint = np.zeros(size, dtype=np.float32)
        self.interval = np.zeros(size, dtype=np.float32)    # frame start to frame start, ms
        self.latency = np.zeros(size, dtype=np.float32)     # wire to pixel, ms
        self.n = 0
        self.n_lat = 0
        self._last = 0.0
        self.reset()

    def reset(self) -> None:
        self.n = self.n_lat = 0
        self._last = 0.0
        self._wall0 = time.perf_counter()
        self._cpu0 = time.process_time()

    def frame(self, t0: float, t_refresh: float, t_paint: float) -> None:
        i = self.n % len(self.work)
        self.work[i] = (t_paint - t0) * 1000.0
        self.paint[i] = (t_paint - t_refresh) * 1000.0
        self.interval[i] = (t0 - self._last) * 1000.0 if self._last else 0.0
        self._last = t0
        self.n += 1

    def note_latency(self, ms: float) -> None:
        self.latency[self.n_lat % len(self.latency)] = ms
        self.n_lat += 1

    @staticmethod
    def _stats(values) -> dict:
        if not len(values):
            return {"p50": 0.0, "p95": 0.0, "max": 0.0}
        return {"p50": round(float(np.percentile(values, 50)), 2),
                "p95": round(float(np.percentile(values, 95)), 2),
                "max": round(float(values.max()), 2)}

    def summary(self) -> dict:
        n = min(self.n, len(self.work))
        wall = time.perf_counter() - self._wall0
        intervals = self.interval[1:n] if n > 1 else self.interval[:0]
        mean = float(intervals.mean()) if len(intervals) else 0.0
        return {
            "frames": self.n,
            "seconds": round(wall, 2),
            "fps": round(1000.0 / mean, 1) if mean else 0.0,
            "fps_min": round(1000.0 / float(np.percentile(intervals, 99)), 1)
            if len(intervals) else 0.0,
            "frame_ms": self._stats(self.work[:n]),
            "paint_ms": self._stats(self.paint[:n]),
            "latency_ms": self._stats(self.latency[:min(self.n_lat, len(self.latency))]),
            "cpu_pct": round(100.0 * (time.process_time() - self._cpu0) / wall, 1) if wall else 0.0,
        }

    def line(self) -> str:
        s = self.summary()
        return (f"{s['fps']:.0f} fps · frame {s['frame_ms']['p50']:.1f}/"
                f"{s['frame_ms']['p95']:.1f} ms · cpu {s['cpu_pct']:.0f}% · "
                f"lat {s['latency_ms']['p50']:.0f} ms")
