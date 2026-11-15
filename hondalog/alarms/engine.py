"""Alarm rules and the state machine that runs them once per sample.

A rule trips when any of its tests is true:

    low / high          value below / above a limit
    rate_low/rate_high  value changing faster than a limit, in units per second
                        (least-squares slope over ``rate_window_ms``)
    when                any expression, e.g. "[o2] < 0.2 && [tps] > 60"

and, if ``gate`` is set, only while the gate expression is true. Life cycle::

    idle --trip--> pending --held for hold_s--> ACTIVE --clear for clear_hold_s--> idle
                                                  |                                 ^
                                                  +-- latching: stays up as "latched"
                                                      until acknowledge() ---------+

Clearing a limit needs the value to come back inside by ``hysteresis``, so a
value sitting on the limit does not chatter. While an input has no value the
rule holds its state: a dropped link neither raises nor clears anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

import numpy as np

from ..expr import Expr, ExprError, compile_expr
from ..expr.funcs import Slope

WARN, CRITICAL = "warn", "critical"
_DEFAULT_PRIORITY = {WARN: 50, CRITICAL: 100}
ACTIONS = ("tile", "banner", "beep", "marker", "capture")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIELD = re.compile(r"\{(\w+)(?::([^{}]*))?\}")

IDLE, PENDING, ACTIVE, LATCHED = "idle", "pending", "active", "latched"
RAISED, CLEARED, ACKNOWLEDGED = "raised", "cleared", "acknowledged"


class AlarmError(ValueError):
    """An alarm rule is wrong (bad expression, unknown channel, bad limits)."""


@dataclass
class AlarmRule:
    name: str
    channel: str = ""                    # for low/high/rate tests; also colours its tile
    low: float | None = None
    high: float | None = None
    rate_low: float | None = None        # units per second, e.g. -1500 for "falling fast"
    rate_high: float | None = None
    rate_window_ms: float = 500.0
    when: str = ""                       # expression that trips the alarm
    clear_when: str = ""                 # optional expression that re-arms a ``when`` alarm
    gate: str = ""                       # the rule only runs while this is true
    hold_s: float = 0.0                  # must stay tripped this long before raising
    clear_hold_s: float = 0.0            # must stay clear this long before clearing
    hysteresis: float = 0.0              # re-arm band for low/high
    severity: str = WARN
    priority: int | None = None          # higher wins; default 50 warn, 100 critical
    latching: bool = False               # stays up until acknowledged
    actions: tuple[str, ...] = ("tile", "banner")
    message: str = ""                    # "{label} {value:.0f} {units}"; see _format
    label: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise AlarmError(f"bad alarm name {self.name!r}")
        who = f"alarm {self.name!r}"
        if self.severity not in _DEFAULT_PRIORITY:
            raise AlarmError(f"{who}: severity must be 'warn' or 'critical'")
        if self.priority is None:
            self.priority = _DEFAULT_PRIORITY[self.severity]
        self.actions = tuple(self.actions)
        bad = [a for a in self.actions if a not in ACTIONS]
        if bad:
            raise AlarmError(f"{who}: unknown action {bad[0]!r} (use {', '.join(ACTIONS)})")
        limits = (self.low, self.high, self.rate_low, self.rate_high)
        for value in (*limits, self.hold_s, self.clear_hold_s, self.hysteresis,
                      self.rate_window_ms):
            if value is not None and (isinstance(value, bool)
                                      or not isinstance(value, (int, float))):
                raise AlarmError(f"{who}: limits and times must be numbers")
        if any(v is not None for v in limits) and not self.channel:
            raise AlarmError(f"{who}: low/high/rate limits need a 'channel'")
        if all(v is None for v in limits) and not self.when.strip():
            raise AlarmError(f"{who}: needs a limit (low, high, rate_low, rate_high) or 'when'")
        if self.low is not None and self.high is not None and self.low >= self.high:
            raise AlarmError(f"{who}: low must be below high")
        if self.hold_s < 0 or self.clear_hold_s < 0 or self.hysteresis < 0:
            raise AlarmError(f"{who}: hold times and hysteresis cannot be negative")
        if self.rate_window_ms <= 0:
            raise AlarmError(f"{who}: rate_window_ms must be more than 0")
        if self.clear_when.strip() and not self.when.strip():
            raise AlarmError(f"{who}: 'clear_when' only makes sense with 'when'")
        self.label = self.label or self.name.replace("_", " ")

    @property
    def uses_rate(self) -> bool:
        return self.rate_low is not None or self.rate_high is not None


@dataclass(frozen=True)
class AlarmEvent:
    """Something happened to an alarm. Returned by ``AlarmEngine.update``."""
    kind: str                    # "raised" | "cleared" | "acknowledged"
    name: str
    severity: str
    priority: int
    t: float                     # sample time in seconds
    value: float | None          # the rule's channel at that moment, if it has one
    message: str
    actions: tuple[str, ...]
    channel: str = ""
    unverified: bool = False     # an input comes from an unverified address

    @property
    def marker_text(self) -> str:
        """Text for a log marker: "ALARM ect_high: Coolant 106 °C"."""
        word = {RAISED: "ALARM", CLEARED: "CLEAR", ACKNOWLEDGED: "ACK"}[self.kind]
        return f"{word} {self.name}: {self.message}"


@dataclass
class AlarmStatus:
    """Live state of one rule, for the UI."""
    rule: AlarmRule
    state: str = IDLE            # idle | pending | active | latched
    since: float = 0.0           # when the current state began
    raised_at: float | None = None
    acknowledged: bool = False
    value: float | None = None
    rate: float | None = None
    message: str = ""
    unverified: bool = False
    count: int = 0               # times raised this session

    @property
    def name(self) -> str:
        return self.rule.name

    @property
    def active(self) -> bool:
        """True while the alarm should be shown (tripped, or latched)."""
        return self.state in (ACTIVE, LATCHED)

    @property
    def severity(self) -> str:
        return self.rule.severity

    @property
    def priority(self) -> int:
        return self.rule.priority or 0


@dataclass
class _Runtime:
    status: AlarmStatus
    when: Expr | None = None
    clear_when: Expr | None = None
    gate: Expr | None = None
    slope: Slope | None = None
    clear_since: float | None = None
    inputs: frozenset[str] = field(default_factory=frozenset)


class AlarmEngine:
    """Runs a set of rules. Call ``update`` once per sample.

    rules      AlarmRule list (disabled rules are dropped)
    names      every channel name the rules may use (measured and math)
    constants  named numbers for expressions
    verified   optional {channel: bool}; events and statuses of rules that read
               an unverified channel carry ``unverified=True``
    labels     optional {channel: (label, units)} for messages
    """

    def __init__(self, rules: Sequence[AlarmRule], names: Iterable[str],
                 constants: Mapping[str, float] | None = None,
                 verified: Mapping[str, bool] | None = None,
                 labels: Mapping[str, tuple[str, str]] | None = None):
        known = frozenset(names)
        self._args = (list(rules), known, dict(constants or {}),
                      None if verified is None else dict(verified), dict(labels or {}))
        self._labels = dict(labels or {})
        self._listeners: list[Callable[[AlarmEvent], None]] = []
        self._rt: list[_Runtime] = []
        seen: set[str] = set()
        for rule in rules:
            if not rule.enabled:
                continue
            if rule.name in seen:
                raise AlarmError(f"duplicate alarm {rule.name!r}")
            seen.add(rule.name)
            if rule.channel and rule.channel not in known:
                raise AlarmError(f"alarm {rule.name!r}: unknown channel {rule.channel!r}")
            rt = _Runtime(AlarmStatus(rule))
            inputs = {rule.channel} if rule.channel else set()
            for attr in ("when", "clear_when", "gate"):
                text = getattr(rule, attr).strip()
                if not text:
                    continue
                try:
                    expr = compile_expr(text, names=known, constants=constants)
                except ExprError as exc:
                    raise AlarmError(f"alarm {rule.name!r}, {attr}: {exc}") from exc
                setattr(rt, attr, expr)
                inputs |= expr.deps
            if rule.uses_rate:
                rt.slope = Slope(rule.rate_window_ms)
            rt.inputs = frozenset(inputs)
            if verified is not None:
                rt.status.unverified = any(not verified.get(n, True) for n in inputs)
            self._rt.append(rt)
        self._by_name = {rt.status.name: rt for rt in self._rt}
        self._by_channel: dict[str, list[_Runtime]] = {}
        for rt in self._rt:
            if rt.status.rule.channel:
                self._by_channel.setdefault(rt.status.rule.channel, []).append(rt)
        self._n_active = 0

    # -- queries --

    def __len__(self) -> int:
        return len(self._rt)

    @property
    def rules(self) -> list[AlarmRule]:
        return [rt.status.rule for rt in self._rt]

    def inputs(self, name: str) -> frozenset[str]:
        """Channels a rule reads."""
        return self._by_name[name].inputs

    def status(self, name: str) -> AlarmStatus:
        return self._by_name[name].status

    def statuses(self) -> list[AlarmStatus]:
        return [rt.status for rt in self._rt]

    def active(self) -> list[AlarmStatus]:
        """Alarms to show now, most important first (priority, then oldest)."""
        if not self._n_active:
            return []
        live = [rt.status for rt in self._rt if rt.status.active]
        return sorted(live, key=lambda s: (-s.priority, s.raised_at or 0.0))

    def top(self) -> AlarmStatus | None:
        """The one alarm a banner should show, or None."""
        live = self.active()
        return live[0] if live else None

    def severity_for(self, channel: str) -> str | None:
        """Worst active severity among rules on this channel (tile colour)."""
        worst: str | None = None
        for rt in self._by_channel.get(channel, ()):
            if rt.status.active and "tile" in rt.status.rule.actions:
                if rt.status.severity == CRITICAL:
                    return CRITICAL
                worst = WARN
        return worst

    def subscribe(self, callback: Callable[[AlarmEvent], None]) -> Callable[[], None]:
        """Call ``callback(event)`` for every event; returns an unsubscribe function.

        Callbacks run on the thread that calls ``update``; an exception in one
        is swallowed so it cannot stop polling.
        """
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)
        return unsubscribe

    # -- the per-sample step --

    def update(self, values: Mapping[str, float | None], t: float) -> list[AlarmEvent]:
        """Feed one sample (measured and math values). Returns new events."""
        events: list[AlarmEvent] = []
        for rt in self._rt:
            st = rt.status
            rule = st.rule
            tripped = self._tripped(rt, values, t)
            if tripped is None:
                continue                                    # unknown: hold state
            state = st.state
            if tripped:
                rt.clear_since = None
                if state == IDLE:
                    st.state, st.since = PENDING, t
                    state = PENDING
                if state == PENDING and t - st.since >= rule.hold_s:
                    self._raise(rt, t, values, events)
                elif state == LATCHED:
                    st.state, st.since = ACTIVE, t          # came back before the ack
            elif state == PENDING:
                st.state, st.since = IDLE, t
            elif state == ACTIVE:
                if rt.clear_since is None:
                    rt.clear_since = t
                if t - rt.clear_since >= rule.clear_hold_s:
                    rt.clear_since = None
                    if rule.latching and not st.acknowledged:
                        st.state, st.since = LATCHED, t
                    else:
                        self._clear(rt, t, events)
        if events:
            self._publish(events)
        return events

    def _tripped(self, rt: _Runtime, values: Mapping[str, float | None],
                 t: float) -> bool | None:
        """True = tripped, False = clear, None = cannot tell (missing input).

        Inside the re-arm band (hysteresis, or ``clear_when`` not yet true) an
        active alarm counts as still tripped and anything else as clear.
        """
        st = rt.status
        rule = st.rule
        if rt.gate is not None:
            gate = rt.gate.truth(values, t)
            if gate is None:
                return None
            if not gate:
                if rt.slope is not None:
                    rt.slope.reset()
                return False
        trip = False
        clear = True
        if rule.channel:
            value = values.get(rule.channel)
            if value is None or value != value:
                return None
            st.value = value
            hyst = rule.hysteresis
            if rule.low is not None:
                if value < rule.low:
                    trip = True
                elif value < rule.low + hyst:
                    clear = False
            if rule.high is not None:
                if value > rule.high:
                    trip = True
                elif value > rule.high - hyst:
                    clear = False
            if rt.slope is not None:
                rate = rt.slope.update(t, value)
                st.rate = rate
                if rate is not None:
                    if rule.rate_low is not None and rate < rule.rate_low:
                        trip = True
                    if rule.rate_high is not None and rate > rule.rate_high:
                        trip = True
        if rt.when is not None:
            cond = rt.when.truth(values, t)
            if cond:
                trip = True
            elif cond is None:
                if not trip:
                    return None
            elif rt.clear_when is not None and not trip:
                if not rt.clear_when.truth(values, t):      # False or unknown: not yet
                    clear = False
        if trip:
            return True
        if clear:
            return False
        return st.state == ACTIVE       # in the re-arm band: an active alarm stays up

    def _raise(self, rt: _Runtime, t: float, values: Mapping[str, float | None],
               events: list[AlarmEvent]) -> None:
        st = rt.status
        st.state, st.since, st.raised_at = ACTIVE, t, t
        st.acknowledged = False
        st.count += 1
        st.message = self._format(rt, values)
        self._n_active += 1
        events.append(self._event(RAISED, rt, t))

    def _clear(self, rt: _Runtime, t: float, events: list[AlarmEvent]) -> None:
        st = rt.status
        st.state, st.since = IDLE, t
        st.acknowledged = False
        self._n_active -= 1
        events.append(self._event(CLEARED, rt, t))

    def _event(self, kind: str, rt: _Runtime, t: float) -> AlarmEvent:
        st = rt.status
        rule = st.rule
        return AlarmEvent(kind, rule.name, rule.severity, rule.priority or 0, t, st.value,
                          st.message, rule.actions, rule.channel, st.unverified)

    def _publish(self, events: Iterable[AlarmEvent]) -> None:
        for event in events:
            for callback in list(self._listeners):
                try:
                    callback(event)
                except Exception:
                    pass

    def _format(self, rt: _Runtime, values: Mapping[str, float | None]) -> str:
        st = rt.status
        rule = st.rule
        label, units = self._labels.get(rule.channel, (rule.channel, ""))
        fields: dict[str, Any] = {
            "name": rule.name, "label": rule.label, "channel": rule.channel,
            "channel_label": label, "units": units, "value": st.value, "rate": st.rate,
            "low": rule.low, "high": rule.high, "severity": rule.severity,
        }
        fields.update({k: v for k, v in values.items() if k not in fields})
        if rule.message:
            return _fill(rule.message, fields)
        if rule.channel and st.value is not None:
            text = f"{label or rule.channel} {st.value:.4g}"
            text += f" {units}" if units else ""
            if rule.high is not None and st.value > rule.high:
                text += f" (limit {rule.high:g})"
            elif rule.low is not None and st.value < rule.low:
                text += f" (limit {rule.low:g})"
            elif st.rate is not None and rule.uses_rate:
                text += f", changing {st.rate:+.4g}{'' if not units else ' ' + units}/s"
            return f"{rule.label}: {text}"
        return rule.label

    # -- operator actions --

    def acknowledge(self, name: str | None = None, t: float | None = None) -> list[AlarmEvent]:
        """Acknowledge one alarm, or all when ``name`` is None.

        A latched alarm whose condition is gone clears now. One that is still
        tripped stays up but is marked acknowledged (the UI stops beeping) and
        clears by itself when the condition goes away.
        """
        events: list[AlarmEvent] = []
        targets = self._rt if name is None else [self._by_name[name]]
        for rt in targets:
            st = rt.status
            when = st.since if t is None else t
            if st.state == LATCHED:
                events.append(self._event(ACKNOWLEDGED, rt, when))
                self._clear(rt, when, events)
            elif st.state == ACTIVE and not st.acknowledged:
                st.acknowledged = True
                events.append(self._event(ACKNOWLEDGED, rt, when))
        if events:
            self._publish(events)
        return events

    def reset(self) -> None:
        """Back to all-idle (new connection or new log). No events are sent."""
        for rt in self._rt:
            unverified = rt.status.unverified
            rt.status = AlarmStatus(rt.status.rule, unverified=unverified)
            rt.clear_since = None
            for expr in (rt.when, rt.clear_when, rt.gate):
                if expr is not None:
                    expr.reset()
            if rt.slope is not None:
                rt.slope.reset()
        self._n_active = 0

    # -- replay --

    def replay(self, t: Any, columns: Mapping[str, Any]) -> list[AlarmEvent]:
        """Run the rules over a whole log; returns every event in order.

        Works on a private copy of the engine, so live alarm state and
        subscribers are not touched. NaN in a column means "no value".
        """
        rules, names, constants, verified, labels = self._args
        engine = AlarmEngine(rules, names, constants, verified, labels)
        t_list = np.asarray(t, dtype=float).tolist()
        needed = sorted(set().union(*(rt.inputs for rt in engine._rt))) if engine._rt else []
        names_in = [n for n in needed if n in columns]
        series = [[None if x != x else x
                   for x in np.asarray(columns[n], dtype=float).tolist()] for n in names_in]
        events: list[AlarmEvent] = []
        for i, ti in enumerate(t_list):
            events += engine.update({n: s[i] for n, s in zip(names_in, series)}, ti)
        return events


def _fill(template: str, fields: Mapping[str, Any]) -> str:
    """Replace {name} and {name:.1f}. Unknown names and bad specs are left as
    written. Only plain names: no attribute or index access like str.format."""
    def sub(m: re.Match[str]) -> str:
        key, spec = m.group(1), m.group(2)
        if key not in fields:
            return m.group(0)
        value = fields[key]
        if value is None:
            return "--"
        if spec and re.fullmatch(r"[<>^+ ]?\d{0,3}(\.\d{1,2})?[dfg%]?", spec):
            try:
                return format(value, spec)
            except (ValueError, TypeError):
                return str(value)
        return f"{value:g}" if isinstance(value, float) else str(value)
    return _FIELD.sub(sub, template)
