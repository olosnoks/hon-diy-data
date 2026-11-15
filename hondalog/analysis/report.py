"""A one-page HTML report for a capture (or any log): what happened, in order.

Self-contained: inline SVG charts, no scripts, no network. Opens in any
browser and prints to one page.
"""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Sequence

import numpy as np

from ..data.reader import Log
from .cursors import value_at
from .lod import envelope

DEFAULT_CHANNELS = ("rpm", "map", "tps", "batt", "o2", "inj", "ign", "ect", "link")
W, H = 860, 70


def _chart(log: Log, name: str, t0: float, t1: float, marks: Sequence[float]) -> str:
    series = log.series(name)
    env = envelope(series.t, series.v, t0, t1, columns=W // 2)
    ok = ~np.isnan(env.lo)
    if not ok.any() or t1 <= t0:
        return f'<div class="chart"><b>{html.escape(name)}</b> <i>no data</i></div>'
    lo, hi = float(np.nanmin(env.lo)), float(np.nanmax(env.hi))
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad

    def x(t: np.ndarray | float) -> np.ndarray:
        return (np.asarray(t) - t0) / (t1 - t0) * W

    def y(v: np.ndarray | float) -> np.ndarray:
        return H - (np.asarray(v) - lo) / (hi - lo) * H

    parts = []
    xs, top, bottom = x(env.t), y(env.hi), y(env.lo)
    run: list[int] = []
    for i in [*range(len(xs)), None]:           # break the line at gaps
        if i is not None and ok[i]:
            run.append(i)
            continue
        if run:
            if env.reduced:
                pts = [f"{xs[j]:.1f},{top[j]:.1f}" for j in run] + [f"{xs[j]:.1f},{bottom[j]:.1f}" for j in reversed(run)]
                parts.append(f'<polygon points="{" ".join(pts)}" class="band"/>')
            else:
                pts = [f"{xs[j]:.1f},{top[j]:.1f}" for j in run]
                parts.append(f'<polyline points="{" ".join(pts)}" class="line"/>')
            run = []
    for mark in marks:
        if t0 <= mark <= t1:
            parts.append(f'<line x1="{float(x(mark)):.1f}" x2="{float(x(mark)):.1f}" y1="0" y2="{H}" class="mark"/>')
    units = log.units(name)
    label = f"{html.escape(name)} <span>{html.escape(units)}  ({lo + pad:.4g} to {hi - pad:.4g})</span>"
    return (f'<div class="chart"><b>{label}</b>'
            f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(parts)}</svg></div>')


def capture_report(log: Log, channels: Sequence[str] | None = None, title: str = "") -> str:
    """HTML for one log. For a capture, the window is the whole file and the
    table shows every channel just before, at and after the trigger."""
    meta = log.meta
    names = [n for n in (channels or DEFAULT_CHANNELS) if n in log.channels]
    names = names or log.channels[:8]
    t0, t1 = log.t0, log.t1
    trig = float(meta["trigger_time_s"]) if "trigger_time_s" in meta else None
    if trig is None and "stall_time_s" in meta:
        trig = float(meta["stall_time_s"])
    marks = [e.t for e in log.events_of("trigger", "marker", "alarm")]
    name = log.path.name if log.path else "log"
    out = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
           f"<title>{html.escape(title or name)}</title><style>",
           "body{font:13px/1.4 sans-serif;margin:24px;color:#111;max-width:900px}",
           "h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 6px}",
           "table{border-collapse:collapse}td,th{padding:2px 10px 2px 0;text-align:left;vertical-align:top}",
           "th{font-weight:600}.num{text-align:right;font-variant-numeric:tabular-nums}",
           ".chart{margin:6px 0}.chart span{color:#666;font-weight:normal}",
           "svg{display:block;background:#f6f6f6;border:1px solid #ddd}",
           ".band{fill:#1f6fb2;stroke:#1f6fb2;stroke-width:1}.line{fill:none;stroke:#1f6fb2;stroke-width:1.2}",
           ".mark{stroke:#c0392b;stroke-width:1;stroke-dasharray:3 2}",
           "</style></head><body>",
           f"<h1>{html.escape(title or name)}</h1>"]
    if trig is not None:
        what = meta.get("trigger_message") or meta.get("stall_trigger") or meta.get("trigger", "")
        out.append(f"<p><b>Trigger:</b> {html.escape(str(what))} at {trig:.2f} s"
                   f" ({html.escape(str(meta.get('trigger_wall_time', meta.get('stall_wall_time', ''))))})</p>")
    out.append("<table>")
    for key in ("started", "ecu", "ecu_id", "port", "address_map_version", "unverified_channels",
                "capture_of", "window", "notes"):
        if meta.get(key):
            out.append(f"<tr><th>{html.escape(key.replace('_', ' '))}</th><td>{html.escape(str(meta[key]))}</td></tr>")
    out.append(f"<tr><th>span</th><td>{t0:.2f} s to {t1:.2f} s ({log.duration:.1f} s, {log.rows} rows)</td></tr>")
    out.append("</table>")

    out.append("<h2>Order of events</h2><table><tr><th>time</th><th>from trigger</th><th>kind</th><th>what</th></tr>")
    for event in log.events:
        rel = f"{(event.t - trig) * 1000:+.0f} ms" if trig is not None else ""
        detail = event.text + (f" ({event.data['message']})" if event.data.get("message") else "")
        out.append(f"<tr><td class='num'>{event.t:.3f} s</td><td class='num'>{rel}</td>"
                   f"<td>{html.escape(event.kind)}</td><td>{html.escape(detail)}</td></tr>")
    if not log.events:
        out.append("<tr><td colspan='4'><i>no markers or triggers</i></td></tr>")
    out.append("</table>")

    if trig is not None:
        out.append("<h2>Values around the trigger</h2><table><tr><th>channel</th>"
                   "<th class='num'>5 s before</th><th class='num'>1 s before</th>"
                   "<th class='num'>at trigger</th><th class='num'>1 s after</th><th>units</th></tr>")
        for ch in log.channels:
            series = log.series(ch)
            cells = "".join(f"<td class='num'>{value_at(series, trig + d):.4g}</td>" for d in (-5, -1, 0, 1))
            out.append(f"<tr><td>{html.escape(ch)}</td>{cells}<td>{html.escape(series.units)}</td></tr>")
        out.append("</table>")

    out.append("<h2>Channels</h2>")
    out += [_chart(log, n, t0, t1, marks) for n in names]
    extra = [s for s in log.streams.values() if s.info.source not in ("ecu", "import", "")]
    for stream in extra:                        # black box rails and anything else recorded
        out.append(f"<h2>{html.escape(stream.name)}</h2>")
        out += [_chart(log, c.name, t0, t1, marks) for c in stream.info.columns[:8] if c.name not in names]
    out.append("<p style='color:#666'>Channels marked unverified in the address map are guesses."
               " A K-line log cannot show a millisecond power dropout; only the black box can.</p>")
    out.append("</body></html>")
    return "\n".join(out)


def write_report(log: Log, path: str | os.PathLike, channels: Sequence[str] | None = None,
                 title: str = "") -> Path:
    path = Path(path)
    path.write_text(capture_report(log, channels, title), encoding="utf-8")
    return path
