"""The trigger engine: rules that watch live values and open capture windows.

It replaces the hard-wired stall catcher. A rule is a condition plus timing:

    TriggerRule("overtemp", when="ect > 105", hold_s=2, rearm_when="ect < 100")

The engine is fed every time something new is known (one K-line read, one
black box frame, a link state change). It keeps the latest value of every
channel, evaluates the rules, and reports:

  * ``Fired``   a rule's condition has held long enough, right now
  * ``Capture`` the window around one or more firings is complete and can be
                saved (pre_s before the first firing to post_s after the last)

What "save", "mark" and "notify" mean is up to the caller: the recorder cuts
the window out of the running log, the UI shows a banner. The engine itself
does no I/O and has no Qt, so it also runs over a finished log (``scan``).

Arming. A rule only fires when it is armed:

  * with ``arm_when``: armed once that condition has been true (stall rules
    arm when the engine has been seen running), disarmed by firing;
  * without: armed from the start, and after firing it re-arms when
    ``rearm_when`` becomes true, or if that is empty, when ``when`` goes false.

Rules that share a ``group`` disarm together. The two stall rules do: after
"rpm dropped to 0" the driver turns the key off, the ECU goes silent, and
that must not produce a second capture.

A value of None means "unknown" (ECU silent). Comparisons with unknown are
false, so ``rpm < 50`` cannot fire while the link is down.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..analysis.expr import Expr

ACTIONS = ("capture", "mark", "notify")
Condition = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True)
class TriggerRule:
    name: str
    when: str                           # fires when this has been true for hold_s
    arm_when: str = ""                  # must have been true since the last firing
    rearm_when: str = ""                # re-arms a rule without arm_when
    hold_s: float = 0.0
    pre_s: float = 30.0
    post_s: float = 10.0
    actions: tuple[str, ...] = ACTIONS
    severity: str = "critical"          # "warn" or "critical"
    label: str = ""                     # marker text, e.g. "STALL (rpm 0)"
    message: str = ""                   # one line for people, e.g. "rpm dropped to 0"
    kind: str = ""                      # capture file prefix, e.g. "stall"
    reason: str = ""                    # short code for the UI (defaults to the name)
    group: str = ""                     # rules in a group disarm together when one fires
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a trigger rule needs a name")
        for text in (self.when, self.arm_when, self.rearm_when):
            if text:
                Expr(text)              # raises ExprError now, not in the car
        if not self.when:
            raise ValueError(f"rule {self.name!r}: 'when' is empty")
        unknown = [a for a in self.actions if a not in ACTIONS]
        if unknown:
            raise ValueError(f"rule {self.name!r}: unknown action {unknown[0]!r}")
        if self.hold_s < 0 or self.pre_s < 0 or self.post_s < 0:
            raise ValueError(f"rule {self.name!r}: times cannot be negative")
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(self, "label", self.label or self.name.upper())
        object.__setattr__(self, "kind", self.kind or self.name)
        object.__setattr__(self, "reason", self.reason or self.name)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["actions"] = list(self.actions)
        return out

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "TriggerRule":
        known = {k: v for k, v in doc.items() if k in cls.__dataclass_fields__}
        if "actions" in known:
            known["actions"] = tuple(known["actions"])
        return cls(**known)


@dataclass(frozen=True)
class Fired:
    """A rule fired at time t."""

    rule: TriggerRule
    t: float
    values: Mapping[str, Any] = field(default_factory=dict)     # channel values at that moment

    @property
    def reason(self) -> str:
        return self.rule.reason


@dataclass
class Capture:
    """A window to save: from ``t_start`` to ``t_end`` around its firings."""

    fired: list[Fired]
    t_start: float
    t_end: float
    complete: bool = False              # False: cut short because recording stopped

    @property
    def first(self) -> Fired:
        return self.fired[0]

    @property
    def t(self) -> float:
        """Time of the first firing."""
        return self.fired[0].t


class _RuleState:
    def __init__(self, rule: TriggerRule):
        self.rule = rule
        self.when: Condition = Expr(rule.when).test
        self.arm: Condition | None = Expr(rule.arm_when).test if rule.arm_when else None
        self.rearm: Condition | None = Expr(rule.rearm_when).test if rule.rearm_when else None
        self.armed = self.arm is None
        self.true_since: float | None = None
        self.fired = 0


class TriggerEngine:
    """Evaluates rules on live values. Not thread-safe: feed it from one thread."""

    def __init__(self, rules: Iterable[TriggerRule] = (), max_capture_s: float = 300.0):
        self._states: dict[str, _RuleState] = {}
        self.values: dict[str, Any] = {}
        self.capture: Capture | None = None     # the window that is still open
        self.max_capture_s = max_capture_s
        self.on_fired: Callable[[Fired], None] | None = None
        self.on_capture: Callable[[Capture], None] | None = None
        for rule in rules:
            self.add(rule)

    # -- rules --

    @property
    def rules(self) -> list[TriggerRule]:
        return [s.rule for s in self._states.values()]

    def add(self, rule: TriggerRule) -> None:
        """Add a rule, or replace the one with the same name."""
        self._states[rule.name] = _RuleState(rule)

    def remove(self, name: str) -> None:
        self._states.pop(name, None)

    def enable(self, name: str, on: bool = True) -> None:
        state = self._states[name]
        self.add(replace(state.rule, enabled=on))

    def armed(self, name: str) -> bool:
        return self._states[name].armed

    def fired_count(self, name: str) -> int:
        return self._states[name].fired

    # -- running --

    @property
    def capturing(self) -> bool:
        return self.capture is not None

    def feed(self, t: float, values: Mapping[str, Any] | None = None) -> tuple[list[Fired], Capture | None]:
        """Take new values (None = unknown) and evaluate every rule at time t.

        Returns (rules that fired now, capture that completed now or None).
        """
        if values:
            self.values.update(values)
        env = self.values
        fired: list[Fired] = []
        for state in self._states.values():
            rule = state.rule
            if not rule.enabled:
                continue
            if state.arm is not None and not state.armed and state.arm(env):
                state.armed = True
            if state.when(env):
                if state.true_since is None:
                    state.true_since = t
                if state.armed and t - state.true_since >= rule.hold_s:
                    state.fired += 1
                    fired.append(Fired(rule, t, dict(env)))
                    for other in self._states.values():     # one stall, one capture
                        if other is state or (rule.group and other.rule.group == rule.group):
                            other.armed = False
            else:
                state.true_since = None
                if state.arm is None and not state.armed and state.rearm is None:
                    state.armed = True
            if state.arm is None and not state.armed and state.rearm is not None and state.rearm(env):
                state.armed = True
        for event in fired:
            self._opened(event)
        done = self._close_if_due(t)
        return fired, done

    def fire(self, rule: TriggerRule | str, t: float) -> Fired:
        """Fire a rule by hand: a button, an alarm (W3), a black box trigger (W2).

        ``rule`` may be the name of a known rule or a one-off TriggerRule.
        """
        if isinstance(rule, str):
            rule = self._states[rule].rule
        event = Fired(rule, t, dict(self.values))
        self._opened(event)
        return event

    def finish(self) -> Capture | None:
        """Close the open window now (recording is stopping)."""
        capture, self.capture = self.capture, None
        if capture is not None and self.on_capture is not None:
            self.on_capture(capture)
        return capture

    def _opened(self, event: Fired) -> None:
        if self.on_fired is not None:
            self.on_fired(event)
        if "capture" not in event.rule.actions:
            return
        start, end = event.t - event.rule.pre_s, event.t + event.rule.post_s
        if self.capture is None:
            self.capture = Capture([event], start, end)
        else:                           # a second trigger inside the window: one bundle
            cap = self.capture
            cap.fired.append(event)
            cap.t_start = min(cap.t_start, start)
            cap.t_end = min(max(cap.t_end, end), cap.t + self.max_capture_s)

    def _close_if_due(self, t: float) -> Capture | None:
        capture = self.capture
        if capture is None or t < capture.t_end:
            return None
        capture.complete = True
        capture.t_end = t               # keep the sample that closed it: "at least post_s after"
        self.capture = None
        if self.on_capture is not None:
            self.on_capture(capture)
        return capture


def scan(rules: Sequence[TriggerRule], t: Sequence[float], columns: Mapping[str, Any]) -> list[Fired]:
    """Run rules over a finished log: ``t`` and ``columns`` are a Table's arrays.
    NaN counts as unknown. Returns every firing, for "where would this rule
    have triggered" previews."""
    engine = TriggerEngine(rules)
    names = list(columns)
    arrays = [columns[n] for n in names]
    out: list[Fired] = []
    for i in range(len(t)):
        row = {n: (None if a[i] != a[i] else float(a[i])) for n, a in zip(names, arrays)}
        fired, _ = engine.feed(float(t[i]), row)
        out.extend(fired)
    return out
