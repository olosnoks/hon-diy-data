"""Guided verification sessions: prove (or disprove) one channel at a time.

A ``Script`` is a short procedure in phases ("key on, engine off", "press the
pedal slowly to the floor") with checks per channel. A ``VerificationSession``
runs one script against live data:

    session = VerificationSession(SCRIPTS["tps_sweep"], amap)
    session.start()
    ...  feed session.add_sample(values) / session.add_sweep(mem, times) ...
    ...  show session.phase.instruction; call session.next_phase() ...
    report = session.finish()
    report.verdicts["tps"].status            # "pass" | "plausible" | "fail" | "skip"
    session.suggest("tps")                   # where else it might live, if it failed
    session.confirm("tps", map_path)         # the user's decision: writes evidence

Nothing here marks a channel verified by itself. A pass is a recommendation;
``confirm`` is the user's call and records what was measured. Checks marked
weak only show a value is plausible (RPM reads 0 with the engine off): a
channel with only weak passes is "plausible", not "pass", and needs
``force=True`` to confirm.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .. import addressmap
from ..addressmap import AddressMap, Evidence, MapError
from . import correlate, mapedit
from .sweeps import Mark, SweepRecording

PASS, PLAUSIBLE, FAIL, SKIP = "pass", "plausible", "fail", "skip"
MIN_SAMPLES = 3


# ---- script model -----------------------------------------------------------

@dataclass(frozen=True)
class Phase:
    id: str
    title: str
    instruction: str
    min_s: float = 5.0            # shortest useful duration


@dataclass(frozen=True)
class Reference:
    """A reading the user types in (meter, tach, speedo) or a tool supplies
    (ISO PID, Track B)."""
    name: str
    prompt: str
    units: str = ""
    default: float | None = None


@dataclass(frozen=True)
class Question:
    """A yes/no fact about the conditions that some checks depend on."""
    name: str
    prompt: str


@dataclass(frozen=True)
class Check:
    channel: str
    kind: str                     # see _CHECKS
    phase: str
    text: str                     # the expectation in plain words
    params: Mapping[str, Any] = field(default_factory=dict)
    weak: bool = False            # plausibility only: cannot prove the address
    requires: str = ""            # a Question that must have been answered yes


@dataclass(frozen=True)
class Script:
    id: str
    title: str
    intro: str
    phases: tuple[Phase, ...]
    checks: tuple[Check, ...]
    references: tuple[Reference, ...] = ()
    questions: tuple[Question, ...] = ()

    @property
    def channels(self) -> list[str]:
        return list(dict.fromkeys(c.channel for c in self.checks))

    def phase(self, phase_id: str) -> Phase:
        return next(p for p in self.phases if p.id == phase_id)


# ---- results ----------------------------------------------------------------

@dataclass
class CheckResult:
    check: Check
    status: str                   # pass | fail | skip
    measured: str = ""            # what was seen, in words
    stats: dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        mark = {PASS: "ok  ", FAIL: "FAIL", SKIP: "skip"}[self.status]
        weak = " (plausibility only)" if self.check.weak and self.status == PASS else ""
        return f"{mark} {self.check.text}: {self.measured}{weak}"


@dataclass
class ChannelVerdict:
    channel: str
    status: str                   # pass | plausible | fail | skip
    results: list[CheckResult] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """One line for the evidence record."""
        parts = [f"{r.check.text}: {r.measured}" for r in self.results if r.status != SKIP]
        return "; ".join(parts)[:400]


@dataclass
class SessionReport:
    script: str
    title: str
    started: str
    duration_s: float
    map_version: str
    map_hash: str
    variant: str
    verdicts: dict[str, ChannelVerdict]
    references: dict[str, float]
    reference_sources: dict[str, str]
    answers: dict[str, bool]
    phases: list[dict[str, Any]]
    samples: int
    sweeps: int
    log: str = ""                 # evidence file name once saved

    def by_status(self, status: str) -> list[str]:
        return [name for name, v in self.verdicts.items() if v.status == status]

    def text(self) -> str:
        lines = [f"{self.title}  ({self.started}, map {self.map_version} #{self.map_hash})"]
        for name, verdict in self.verdicts.items():
            lines.append(f"  {name}: {verdict.status.upper()}")
            lines += [f"      {r}" for r in verdict.results]
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        doc = asdict(self)
        for verdict in doc["verdicts"].values():
            for result in verdict["results"]:
                result["check"]["params"] = dict(result["check"]["params"])
        return doc


# ---- check implementations --------------------------------------------------

class _Data:
    """What a check may look at: decoded samples per phase."""

    def __init__(self, session: "VerificationSession"):
        self.session = session

    def series(self, channel: str, phase: str) -> tuple[np.ndarray, np.ndarray]:
        """(t, values) of a channel in a phase, missing values dropped."""
        rows = self.session._samples.get(phase, [])
        pairs = [(t, v[channel]) for t, v in rows if v.get(channel) is not None]
        if not pairs:
            return np.empty(0), np.empty(0)
        arr = np.array(pairs, dtype=float)
        return arr[:, 0], arr[:, 1]


def _need(values: np.ndarray, n: int = MIN_SAMPLES) -> CheckResult | None:
    return None if len(values) >= n else CheckResult(None, SKIP, f"only {len(values)} readings")  # type: ignore[arg-type]


def _range(c: Check, d: _Data) -> CheckResult:
    _, v = d.series(c.channel, c.phase)
    if (short := _need(v)) is not None:
        return short
    lo, hi, frac = c.params["lo"], c.params["hi"], c.params.get("frac", 0.9)
    inside = float(((v >= lo) & (v <= hi)).mean())
    ok = inside >= frac
    return CheckResult(c, PASS if ok else FAIL,
                       f"{v.min():.4g} to {v.max():.4g} (median {np.median(v):.4g}), "
                       f"{inside:.0%} inside {lo:g}..{hi:g}",
                       {"min": float(v.min()), "max": float(v.max()),
                        "median": float(np.median(v)), "inside": inside})


def _rise(c: Check, d: _Data) -> CheckResult:
    """Starts low, ends high, and climbs without big reversals in between."""
    _, v = d.series(c.channel, c.phase)
    if (short := _need(v, 6)) is not None:
        return short
    falling = c.params.get("direction", "rise") == "fall"
    w = -v if falling else v
    k = max(1, len(w) // 20)
    start, end = float(np.median(v[:k])), float(np.median(v[-k:]))
    swing = float(w.max() - w.min())
    # the largest drop from a running maximum, as a share of the whole swing
    back = float((np.maximum.accumulate(w) - w).max()) / swing if swing > 0 else 1.0
    lo, hi = c.params.get("start"), c.params.get("end")
    ok_start = lo is None or (start >= lo if falling else start <= lo)
    ok_end = hi is None or (end <= hi if falling else end >= hi)
    ok_move = swing >= c.params.get("min_swing", 0.0)
    ok = ok_start and ok_end and ok_move and back <= c.params.get("max_back", 0.2)
    return CheckResult(c, PASS if ok else FAIL,
                       f"went {start:.4g} -> {end:.4g}, largest reversal {back:.0%} of the swing",
                       {"start": start, "end": end, "swing": swing, "reversal": back})


def _steady(c: Check, d: _Data) -> CheckResult:
    _, v = d.series(c.channel, c.phase)
    if (short := _need(v)) is not None:
        return short
    span = float(np.percentile(v, 95) - np.percentile(v, 5))
    ok = span <= c.params["max_span"]
    return CheckResult(c, PASS if ok else FAIL,
                       f"moved {span:.4g} (5th to 95th percentile), limit {c.params['max_span']:g}",
                       {"span": span, "median": float(np.median(v))})


def _near_channel(c: Check, d: _Data) -> CheckResult:
    _, a = d.series(c.channel, c.phase)
    _, b = d.series(c.params["other"], c.phase)
    if (short := _need(a)) is not None or (short := _need(b)) is not None:
        return short
    ma, mb = float(np.median(a)), float(np.median(b))
    ok = abs(ma - mb) <= c.params["tol"]
    return CheckResult(c, PASS if ok else FAIL,
                       f"{ma:.4g} vs {c.params['other']} {mb:.4g} "
                       f"(apart {abs(ma - mb):.3g}, allowed {c.params['tol']:g})",
                       {"value": ma, "other": mb, "difference": ma - mb})


def _near_reference(c: Check, d: _Data) -> CheckResult:
    name = c.params["ref"]
    ref = d.session.references.get(name)
    if ref is None:
        return CheckResult(c, SKIP, f"no {name} reading given")
    _, v = d.series(c.channel, c.phase)
    if (short := _need(v)) is not None:
        return short
    mid = float(np.median(v))
    tol = max(c.params.get("tol", 0.0), c.params.get("rel_tol", 0.0) * abs(ref))
    ok = abs(mid - ref) <= tol
    source = d.session.reference_sources.get(name, name)
    return CheckResult(c, PASS if ok else FAIL,
                       f"{mid:.4g} vs {source} {ref:g} (apart {abs(mid - ref):.3g}, "
                       f"allowed {tol:.3g})",
                       {"value": mid, "reference": float(ref), "difference": mid - ref})


def _switching(c: Check, d: _Data) -> CheckResult:
    """Swings across a midpoint repeatedly, reaching both low and high."""
    _, v = d.series(c.channel, c.phase)
    if (short := _need(v, 8)) is not None:
        return short
    mid = c.params["mid"]
    side = v > mid
    crossings = int((side[1:] != side[:-1]).sum())
    ok = (crossings >= c.params["min_crossings"] and v.min() <= c.params["lo"]
          and v.max() >= c.params["hi"])
    return CheckResult(c, PASS if ok else FAIL,
                       f"crossed {mid:g} {crossings} times, range {v.min():.3g} to {v.max():.3g}",
                       {"crossings": float(crossings), "min": float(v.min()),
                        "max": float(v.max())})


def _flag(c: Check, d: _Data) -> CheckResult:
    _, v = d.series(c.channel, c.phase)
    if (short := _need(v)) is not None:
        return short
    want = c.params["value"]
    share = float((np.round(v) == want).mean())
    ok = share >= c.params.get("frac", 0.9)
    return CheckResult(c, PASS if ok else FAIL, f"read {want} in {share:.0%} of readings",
                       {"share": share})


def _versus_phase(c: Check, d: _Data) -> CheckResult:
    """Median in this phase compared with the median in another phase."""
    _, a = d.series(c.channel, c.phase)
    _, b = d.series(c.channel, c.params["other_phase"])
    if (short := _need(a)) is not None or (short := _need(b)) is not None:
        return short
    ma, mb = float(np.median(a)), float(np.median(b))
    diff = ma - mb
    margin = c.params["by"]
    ok = diff >= margin if margin >= 0 else diff <= margin
    word = "above" if margin >= 0 else "below"
    return CheckResult(c, PASS if ok else FAIL,
                       f"{ma:.4g} vs {mb:.4g} in '{c.params['other_phase']}' "
                       f"({diff:+.4g}, wanted at least {abs(margin):g} {word})",
                       {"value": ma, "other": mb, "difference": diff})


_CHECKS: dict[str, Callable[[Check, _Data], CheckResult]] = {
    "range": _range, "rise": _rise, "steady": _steady, "near_channel": _near_channel,
    "near_reference": _near_reference, "switching": _switching, "flag": _flag,
    "versus_phase": _versus_phase,
}


def run_check(check: Check, data: _Data) -> CheckResult:
    answers = data.session.answers
    if check.requires and not answers.get(check.requires, False):
        return CheckResult(check, SKIP, f"only applies when: {check.requires.replace('_', ' ')}")
    try:
        result = _CHECKS[check.kind](check, data)
    except Exception as exc:                # a check must never take the session down
        result = CheckResult(check, SKIP, f"could not be evaluated: {exc}")
    result.check = check
    return result


def judge(channel: str, results: Sequence[CheckResult]) -> ChannelVerdict:
    done = [r for r in results if r.status != SKIP]
    if not done:
        status = SKIP
    elif any(r.status == FAIL for r in done):
        status = FAIL
    elif any(not r.check.weak for r in done):
        status = PASS
    else:
        status = PLAUSIBLE
    return ChannelVerdict(channel, status, list(results))


# ---- the session ------------------------------------------------------------

class VerificationSession:
    """Runs one script. Feed it data, step through the phases, read the report.

    script   one of SCRIPTS, or your own
    amap     the map being checked (its formulas decode the sweeps)
    clock    monotonic seconds; replaceable for tests and replay
    decode_sweeps  when True (default) every sweep passed to ``add_sweep`` is
             also decoded with the map and counted as a sample, so a session
             can run from the memory scanner alone. Turn it off if you feed
             poller samples through ``add_sample`` as well.
    """

    def __init__(self, script: Script, amap: AddressMap,
                 clock: Callable[[], float] = time.monotonic, decode_sweeps: bool = True):
        self.script = script
        self.amap = amap
        self.clock = clock
        self.decode_sweeps = decode_sweeps
        self.state = "ready"                  # ready | running | done
        self.references: dict[str, float] = {
            r.name: r.default for r in script.references if r.default is not None}
        self.reference_sources: dict[str, str] = {}
        self.answers: dict[str, bool] = {}
        self.recording = SweepRecording(meta={"script": script.id})
        self.report: SessionReport | None = None
        self._samples: dict[str, list[tuple[float, Mapping[str, float | None]]]] = {}
        self._index = -1
        self._t0 = 0.0
        self._phase_t0 = 0.0
        self._started = ""
        self._mark: Mark | None = None
        self._log: list[dict[str, Any]] = []
        self._taken = 0
        missing = [c for c in script.channels if amap.channel(c) is None
                   and amap.math_channel(c) is None]
        self.missing_channels = missing       # checks on these are skipped

    # -- progress --

    def now(self) -> float:
        return self.clock() - self._t0

    @property
    def phase(self) -> Phase | None:
        if self.state != "running":
            return None
        return self.script.phases[self._index]

    @property
    def phase_elapsed(self) -> float:
        return self.now() - self._phase_t0 if self.state == "running" else 0.0

    @property
    def can_advance(self) -> bool:
        """True once the current phase has run for its minimum time."""
        ph = self.phase
        return ph is not None and self.phase_elapsed >= ph.min_s

    def start(self) -> Phase:
        if self.state != "ready":
            raise RuntimeError("session already started")
        self._t0 = self.clock()
        self._started = datetime.now().isoformat(timespec="seconds")
        self.state = "running"
        self._index = -1
        phase = self._enter(0)
        assert phase is not None
        return phase

    def _enter(self, index: int) -> Phase | None:
        now = self.now()
        if self._mark is not None:
            self._mark.t1 = now
            self._log[-1]["t1"] = now
        if index >= len(self.script.phases):
            self._mark = None
            return None
        self._index = index
        self._phase_t0 = now
        phase = self.script.phases[index]
        self._mark = Mark(phase.id, now)
        self.recording.marks.append(self._mark)
        self._samples.setdefault(phase.id, [])
        self._log.append({"id": phase.id, "t0": now, "t1": None})
        return phase

    def next_phase(self) -> Phase | None:
        """End the current phase. Returns the next one, or None when the script
        is finished (then call ``finish``)."""
        if self.state != "running":
            raise RuntimeError("session is not running")
        return self._enter(self._index + 1)

    # -- data in --

    def set_reference(self, name: str, value: float | None, source: str = "") -> None:
        """A typed-in reading (meter, tach), an ISO PID value or a Track B value.
        ``source`` says which, for the evidence text."""
        if value is None:
            self.references.pop(name, None)
            self.reference_sources.pop(name, None)
            return
        self.references[name] = float(value)
        if source:
            self.reference_sources[name] = source

    def set_pid_references(self, pid_values: Mapping[str, float]) -> None:
        """Use ISO PID values (by channel name, see crosscheck.pid_values) as
        references named "<channel>_pid"."""
        for channel, value in pid_values.items():
            self.set_reference(f"{channel}_pid", value, f"ISO PID {channel}")

    def answer(self, question: str, yes: bool) -> None:
        self.answers[question] = bool(yes)

    def add_sample(self, values: Mapping[str, float | None], t: float | None = None) -> None:
        """One decoded sample (the poller's ``Sample.values``). ``t`` is in the
        session's clock; leave it out to stamp it now."""
        ph = self.phase
        if ph is None or self._mark is None:
            return
        self._samples[ph.id].append((self.now() if t is None else t, values))

    def add_sweep(self, mem: Any, times: Any = None) -> None:
        """One raw sweep: 256 values (-1 = unread) and their read times in the
        session's clock (default: now). Kept for the correlation finder and the
        evidence file."""
        if self.phase is None:
            return
        mem = np.asarray(mem, dtype=np.int16)
        now = self.now()
        stamp = np.full(256, now) if times is None else np.asarray(times, dtype=float)
        self.recording.append(mem, stamp)
        if self.decode_sweeps:
            decoded = self.amap.decode({int(a): int(mem[a]) for a in np.flatnonzero(mem >= 0)})
            self.add_sample(decoded, float(stamp.max()))

    def take(self, recorder: Any) -> int:
        """Pull finished sweeps from a ``SweepRecorder`` that shares this
        session's clock (``SweepRecorder(..., clock=session.clock, t0=session._t0)``
        or made with ``session.recorder``). Returns how many were added."""
        recorder.pump()
        mem, times = recorder.recording.data
        start = self._taken
        for i in range(start, len(mem)):
            self.add_sweep(mem[i], times[i])
        self._taken = len(mem)
        return len(mem) - start

    def recorder(self, submit: Callable[..., Any], lo: int = 0x00, hi: int = 0xFF,
                 chunk: int = 16) -> Any:
        """A SweepRecorder on this session's clock; pass ``poller.submit``."""
        from .sweeps import SweepRecorder
        return SweepRecorder(submit, lo, hi, chunk, clock=self.clock, t0=self._t0)

    # -- results --

    def evaluate(self) -> dict[str, ChannelVerdict]:
        """Verdicts on the data so far (also usable mid-session for live ticks)."""
        data = _Data(self)
        grouped: dict[str, list[CheckResult]] = {}
        for check in self.script.checks:
            if check.channel in self.missing_channels:
                result = CheckResult(check, SKIP, "the map has no such channel")
            else:
                result = run_check(check, data)
            grouped.setdefault(check.channel, []).append(result)
        return {name: judge(name, results) for name, results in grouped.items()}

    def finish(self) -> SessionReport:
        """Close the session and produce the report. Safe to call early."""
        if self.state == "ready":
            raise RuntimeError("session was never started")
        if self.state == "running":
            self._enter(len(self.script.phases))
            self.state = "done"
        self.report = SessionReport(
            script=self.script.id, title=self.script.title, started=self._started,
            duration_s=round(self.now(), 3) if self._log and self._log[-1]["t1"] is None
            else round(float(self._log[-1]["t1"] or 0.0), 3),
            map_version=self.amap.version, map_hash=self.amap.version_hash,
            variant=self.amap.variant, verdicts=self.evaluate(),
            references=dict(self.references), reference_sources=dict(self.reference_sources),
            answers=dict(self.answers), phases=[dict(p) for p in self._log],
            samples=sum(len(v) for v in self._samples.values()), sweeps=len(self.recording))
        return self.report

    def save(self, directory: str | Path) -> Path:
        """Write the evidence: a .json report (with every decoded sample) and,
        if sweeps were recorded, a .npz of the raw bytes next to it."""
        report = self.report or self.finish()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = report.started.replace(":", "").replace("-", "").replace("T", "_")
        path = directory / f"verify_{stamp}_{self.script.id}.json"
        n = 1
        while path.exists():
            n += 1
            path = directory / f"verify_{stamp}_{self.script.id}_{n}.json"
        report.log = path.name
        doc = report.to_json()
        doc["samples_by_phase"] = {
            phase: {"t": [round(t, 3) for t, _ in rows],
                    "values": {name: [v.get(name) for _, v in rows]
                               for name in self.amap.names}}
            for phase, rows in self._samples.items()}
        if len(self.recording):
            doc["sweeps_file"] = self.recording.save(path.with_suffix(".npz")).name
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        return path

    # -- acting on the result --

    def suggest(self, channel: str, top: int = 5) -> list[Any]:
        """Other places this channel might live, from the recorded sweeps.

        Uses the expectation of the channel's first usable check: a steady
        rise or fall is matched by shape, a typed or PID reference by value
        through the channel's current formula, a flag by bits that follow the
        phases, "near another channel" by correlation with that channel.
        Returns correlate.Match / ValueMatch / BitChange objects, best first.
        """
        if not len(self.recording):
            return []
        ch = self.amap.channel(channel)
        order = {"rise": 0, "near_reference": 1, "flag": 2, "near_channel": 3}
        checks = sorted((c for c in self.script.checks
                         if c.channel == channel and c.kind in order), key=lambda c: order[c.kind])
        for check in checks:
            mark = next((m for m in self.recording.marks if m.label == check.phase), None)
            if mark is None:
                continue
            t1 = mark.t1 if mark.t1 is not None else self.now()
            window = self.recording.window(mark.t0, t1)
            found: list[Any] = []
            if check.kind == "rise":
                shape = check.params.get("direction", "rise")
                found = list(correlate.rank_shape(window, shape, mark.t0, t1, top=top))
            elif check.kind == "near_reference":
                ref = self.references.get(check.params["ref"])
                if ref is not None:
                    tol = max(check.params.get("tol", 0.0),
                              check.params.get("rel_tol", 0.0) * abs(ref))
                    found = list(correlate.find_value(window, ref, ch.formula if ch else "x",
                                                      tol=tol, rel_tol=0.0, top=top))
            elif check.kind == "flag":
                on = [c.phase for c in checks if c.kind == "flag" and c.params["value"] == 1]
                if on:
                    found = list(correlate.changed_bits(self.recording, on[0]))[:top]
            else:
                try:
                    found = correlate.rank_against_channel(window, self.amap,
                                                           check.params["other"], top=top)
                except MapError:
                    pass
            if found:
                return found
        return []

    def evidence(self, channel: str, note: str = "") -> Evidence:
        """The evidence record ``confirm`` would write for a channel."""
        report = self.report or self.finish()
        verdict = report.verdicts[channel]
        refs = sorted({r.check.params["ref"] for r in verdict.results
                       if r.check.kind == "near_reference" and r.status != SKIP})
        summary = verdict.summary + (f" | note: {note}" if note else "")
        return Evidence(date=report.started, method=f"guided:{self.script.id}",
                        result=verdict.status, summary=summary, log=report.log,
                        map_version=report.map_version, map_hash=report.map_hash,
                        reference=", ".join(self.reference_sources.get(r, r) for r in refs))

    def confirm(self, channel: str, map_path: str | Path, note: str = "",
                force: bool = False, evidence_dir: str | Path | None = None) -> AddressMap:
        """The user accepts a channel: mark it verified in the map file with the
        evidence attached, and return the reloaded map.

        Refused unless the verdict is "pass". ``force=True`` also accepts
        "plausible" (weak checks only); the evidence says so. A failed channel
        cannot be confirmed at all: fix the map and run the script again.
        """
        report = self.report or self.finish()
        verdict = report.verdicts.get(channel)
        if verdict is None:
            raise MapError(f"script '{self.script.id}' does not check {channel!r}")
        if verdict.status == FAIL:
            raise MapError(f"{channel} failed its check and cannot be marked verified")
        if verdict.status == SKIP:
            raise MapError(f"{channel} was not checked (no data or no reference given)")
        if verdict.status == PLAUSIBLE and not force:
            raise MapError(f"{channel} only passed plausibility checks; that does not prove "
                           f"the address. Confirm with force=True if you accept it anyway.")
        if not report.log and evidence_dir is not None:
            self.save(evidence_dir)
        self.amap = mapedit.mark_channel(map_path, channel, self.evidence(channel, note),
                                         addressmap.VERIFIED, self.amap.variant or None)
        return self.amap

    def reject(self, channel: str, map_path: str | Path, note: str = "",
               evidence_dir: str | Path | None = None) -> AddressMap:
        """Mark a channel "suspect" in the map file, with what was measured."""
        report = self.report or self.finish()
        if channel not in report.verdicts:
            raise MapError(f"script '{self.script.id}' does not check {channel!r}")
        if not report.log and evidence_dir is not None:
            self.save(evidence_dir)
        self.amap = mapedit.mark_channel(map_path, channel, self.evidence(channel, note),
                                         addressmap.SUSPECT, self.amap.variant or None)
        return self.amap


# ---- the shipped scripts ----------------------------------------------------

def _c(channel: str, kind: str, phase: str, text: str, weak: bool = False, requires: str = "",
       **params: Any) -> Check:
    return Check(channel, kind, phase, text, params, weak, requires)


_METER = Reference("batt_meter", "Battery voltage on a multimeter at the battery posts", "V")
_TACH = Reference("tach_rpm", "RPM shown on the dash tachometer", "rpm")
_AMBIENT = Reference("ambient_c", "Air temperature where the car is parked", "°C")
_COLD = Question("cold_engine", "Has the engine been off for at least 6 hours?")

KOEO = Script(
    "koeo", "Key on, engine off",
    "Engine stopped, key in ON (II). Checks the readings that have a known value with the "
    "engine off.",
    (Phase("rest", "Key on, engine off", "Key to ON (II). Do not start the engine. Foot off "
           "the pedals. Everything electrical off.", 8.0),),
    (
        _c("rpm", "range", "rest", "RPM reads zero with the engine off", weak=True, lo=0, hi=50),
        _c("vss", "range", "rest", "speed reads zero parked", weak=True, lo=0, hi=1),
        _c("tps", "range", "rest", "throttle reads closed", weak=True, lo=-3, hi=5),
        _c("map", "near_channel", "rest", "MAP equals barometric pressure with the engine off",
           other="baro", tol=4),
        _c("map", "range", "rest", "MAP is atmospheric", weak=True, lo=70, hi=106),
        _c("baro", "range", "rest", "baro is atmospheric", weak=True, lo=70, hi=106),
        _c("batt", "near_reference", "rest", "battery agrees with the meter",
           ref="batt_meter", tol=0.3),
        _c("batt", "range", "rest", "battery is a resting 12 V battery", weak=True,
           lo=11.0, hi=13.3),
        _c("ect", "near_channel", "rest", "coolant equals intake air on a cold engine",
           requires="cold_engine", other="iat", tol=5),
        _c("ect", "near_reference", "rest", "coolant equals the air temperature on a cold engine",
           requires="cold_engine", ref="ambient_c", tol=5),
        _c("iat", "near_reference", "rest", "intake air equals the air temperature",
           requires="cold_engine", ref="ambient_c", tol=6),
        _c("mil", "flag", "rest", "MIL is lit for the bulb check", weak=True, value=1),
        _c("closed_loop", "flag", "rest", "not in closed loop", weak=True, value=0),
    ),
    (_METER, _AMBIENT), (_COLD,))

TPS_SWEEP = Script(
    "tps_sweep", "Throttle sweep (engine off)",
    "Engine stopped, key ON. The pedal is pressed slowly so the throttle reading can be "
    "watched from closed to wide open.",
    (Phase("released", "Foot off", "Key ON, engine off, foot off the accelerator.", 4.0),
     Phase("sweep", "Press slowly", "Press the accelerator slowly and evenly to the floor, "
           "taking about 10 seconds. Do not pump it.", 8.0),
     Phase("floor", "Hold on the floor", "Hold the pedal on the floor.", 3.0),
     Phase("release", "Let go", "Release the pedal.", 3.0)),
    (
        _c("tps", "range", "released", "throttle under 5% with the pedal released", lo=-3, hi=5),
        _c("tps", "rise", "sweep", "throttle rises steadily from under 5% to over 90%",
           start=5, end=90, max_back=0.15),
        _c("tps", "range", "floor", "throttle over 90% on the floor", lo=90, hi=106),
        _c("tps", "range", "release", "throttle returns to closed", lo=-3, hi=5, frac=0.5),
        _c("rpm", "range", "sweep", "RPM stays zero", weak=True, lo=0, hi=50),
    ))

IDLE = Script(
    "idle", "Warm idle",
    "Engine fully warm and idling in neutral with everything switched off.",
    (Phase("idle", "Warm idle", "Engine fully warm (radiator fan has run at least once), "
           "idling in neutral, A/C, lights and rear demister off. Hands off.", 30.0),),
    (
        _c("rpm", "near_reference", "idle", "RPM agrees with the tachometer",
           ref="tach_rpm", rel_tol=0.1, tol=60),
        _c("rpm", "range", "idle", "RPM is a normal idle", weak=True, lo=550, hi=950),
        _c("rpm", "steady", "idle", "RPM is steady", weak=True, max_span=150),
        _c("map", "range", "idle", "MAP shows idle vacuum", weak=True, lo=20, hi=48),
        _c("batt", "near_reference", "idle", "battery agrees with the meter",
           ref="batt_meter", tol=0.3),
        _c("batt", "range", "idle", "battery shows charging voltage", weak=True,
           lo=13.2, hi=14.9),
        _c("tps", "range", "idle", "throttle reads closed", weak=True, lo=-3, hi=5),
        _c("vss", "range", "idle", "speed reads zero", weak=True, lo=0, hi=1),
        _c("ect", "range", "idle", "coolant at operating temperature", weak=True, lo=75, hi=105),
        _c("inj", "range", "idle", "injector pulse is an idle pulse", weak=True, lo=1.0, hi=4.5),
        _c("ign", "range", "idle", "ignition advance is an idle value", weak=True, lo=5, hi=25),
        _c("o2", "switching", "idle", "O2 sensor switches rich/lean in closed loop",
           mid=0.45, lo=0.3, hi=0.6, min_crossings=4),
        _c("closed_loop", "flag", "idle", "closed loop is on", weak=True, value=1),
        _c("stft", "range", "idle", "short trim is near zero", weak=True, lo=-15, hi=15),
        _c("ltft", "range", "idle", "long trim is near zero", weak=True, lo=-15, hi=15),
        _c("iacv", "range", "idle", "idle valve is partly open", weak=True, lo=1, hi=80),
    ),
    (_TACH, _METER))

STEADY_3000 = Script(
    "steady_3000", "Steady 3000 rpm",
    "Idle, then hold 3000 rpm on the tachometer in neutral, then back to idle.",
    (Phase("idle", "Idle", "Warm engine idling in neutral.", 8.0),
     Phase("hold", "Hold 3000 rpm", "Raise the engine speed to 3000 rpm on the tachometer and "
           "hold it as steady as you can.", 12.0),
     Phase("back", "Back to idle", "Release the pedal and let it settle.", 5.0)),
    (
        _c("rpm", "near_reference", "hold", "RPM agrees with the tachometer",
           ref="tach_rpm", rel_tol=0.08),
        _c("rpm", "versus_phase", "hold", "RPM is well above idle", other_phase="idle", by=1500),
        _c("rpm", "steady", "hold", "RPM is steady while held", weak=True, max_span=400),
        _c("tps", "versus_phase", "hold", "throttle is open further than at idle",
           weak=True, other_phase="idle", by=1),
        _c("ign", "versus_phase", "hold", "ignition is more advanced than at idle",
           weak=True, other_phase="idle", by=3),
        _c("batt", "range", "hold", "battery shows charging voltage", weak=True,
           lo=13.2, hi=14.9),
        _c("vss", "range", "hold", "speed reads zero", weak=True, lo=0, hi=1),
    ),
    (Reference("tach_rpm", "RPM held on the dash tachometer", "rpm", 3000.0),))

WARMUP = Script(
    "warmup", "Cold start to warm",
    "Starts with a cold engine and follows it to operating temperature.",
    (Phase("cold", "Cold, engine off", "Engine cold (off for 6+ hours). Key ON, engine off.",
           8.0),
     Phase("warming", "Warm up", "Start the engine and let it idle until the radiator fan "
           "comes on, or for 10 minutes. Leave the pedal alone.", 240.0)),
    (
        _c("ect", "near_channel", "cold", "coolant equals intake air before starting",
           other="iat", tol=5),
        _c("ect", "near_reference", "cold", "coolant equals the air temperature before starting",
           ref="ambient_c", tol=5),
        _c("ect", "rise", "warming", "coolant climbs steadily to over 70 °C",
           end=70, min_swing=30, max_back=0.1),
        _c("iat", "near_reference", "cold", "intake air equals the air temperature",
           ref="ambient_c", tol=6),
        _c("iat", "steady", "warming", "intake air changes far less than coolant", weak=True,
           max_span=30),
        _c("rpm", "rise", "warming", "fast idle comes down as it warms", weak=True,
           direction="fall", min_swing=150, max_back=0.5),
        _c("closed_loop", "rise", "warming", "closed loop switches on once warm",
           start=0.2, end=0.8, max_back=1.0),
    ),
    (_AMBIENT,))

DRIVE = Script(
    "drive", "Steady road speed",
    "A passenger works the laptop. Hold a steady speed on a level road.",
    (Phase("cruise", "Hold 60 km/h", "Hold a steady 60 km/h (37 mph) by the speedometer, or "
           "type in the speed you are holding.", 10.0),),
    (
        _c("vss", "near_reference", "cruise", "speed agrees with the speedometer",
           ref="speedo_kmh", tol=5, rel_tol=0.08),
        _c("vss", "steady", "cruise", "speed is steady", weak=True, max_span=8),
        _c("rpm", "range", "cruise", "engine is running", weak=True, lo=1000, hi=5000),
    ),
    (Reference("speedo_kmh", "Speed held on the speedometer", "km/h", 60.0),))


def switch_script(channel: str, action: str, script_id: str = "", title: str = "") -> Script:
    """A script for any on/off input: off, on, off again.

    ``action`` completes "Switch on ...": "the A/C", "the headlights", "the
    brake pedal (press and hold)".
    """
    return Script(
        script_id or f"switch_{channel}", title or f"Switch test: {action}",
        f"Engine idling (or key ON). {action[:1].upper() + action[1:]} is switched on and off "
        "while everything else is left alone.",
        (Phase("off", "Off", f"Make sure {action} is OFF. Touch nothing else.", 5.0),
         Phase("on", "On", f"Switch ON {action} and leave it on.", 6.0),
         Phase("off_again", "Off again", f"Switch {action} OFF again.", 5.0)),
        (
            _c(channel, "flag", "off", "reads 0 while off", value=0),
            _c(channel, "flag", "on", "reads 1 while on", value=1, frac=0.8),
            _c(channel, "flag", "off_again", "reads 0 again", value=0, frac=0.8),
        ))


SCRIPTS: dict[str, Script] = {s.id: s for s in (
    KOEO, TPS_SWEEP, IDLE, STEADY_3000, WARMUP, DRIVE,
    switch_script("ac_sw", "the A/C", "ac_switch", "A/C switch"),
)}


def scripts_for(channel: str) -> list[Script]:
    """Scripts that check a channel, strongest evidence first."""
    def strength(script: Script) -> int:
        return -sum(1 for c in script.checks if c.channel == channel and not c.weak)
    found = [s for s in SCRIPTS.values() if channel in s.channels]
    return sorted(found, key=strength)


def coverage(amap: AddressMap) -> list[dict[str, Any]]:
    """For a map overview: every channel, its state, and how it can be checked.

    ``strong`` lists scripts that can prove the address; ``weak`` lists ones
    that only show the value is plausible; a channel with neither needs the
    correlation finder or an ISO cross-check.
    """
    rows = []
    for ch in amap.channels:
        strong = [s.id for s in SCRIPTS.values()
                  if any(c.channel == ch.name and not c.weak for c in s.checks)]
        weak = [s.id for s in SCRIPTS.values() if ch.name in s.channels and s.id not in strong]
        rows.append({"channel": ch.name, "label": ch.label, "status": ch.status,
                     "evidence": ch.evidence, "strong": strong, "weak": weak})
    return rows
