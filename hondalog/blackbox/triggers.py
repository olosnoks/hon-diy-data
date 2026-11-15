"""The black box configuration and trigger engine, in Python.

A line-for-line mirror of the firmware's ``bb_util.c`` (config text, edge
timing) and ``bb_trigger.c`` (rules). The simulator uses it, and the tests
run it against the compiled C on the same input to prove they agree. Integer
arithmetic throughout, like the firmware.

Config text is what lives in ``BLACKBOX.CFG`` on the SD card::

    pre_s = 30
    rule.4 = ecub < 6000 1000 10 ig1 capture
    #        source op level hyst hold_ms qualifiers action

A rule arms once its qualifiers hold and the source is on the good side of
the level by ``hyst``; it fires when the condition has held ``hold_ms``
samples in a row and must arm again before it can fire again. The slot
number is the reason code that ends up in the event file.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from . import format as F

SOURCES = ("vbat", "ecub", "pump", "ig1", "alt",
           "slew_vbat", "slew_ecub", "slew_pump", "slew_ig1", "slew_alt",
           "tach_age", "ign_age", "inj_age", "rpm_tach", "rpm_ign", "inj_pw", "inj_duty", "button")
QUALS = ("ig1", "running", "ign_running", "tach_alive")
Q_IG1, Q_RUNNING, Q_IGN_RUNNING, Q_TACH_ALIVE = 1, 2, 4, 8
ACTIONS = ("off", "mark", "capture")
ACT_OFF, ACT_MARK, ACT_CAPTURE = range(3)
MAX_RULES = 32
AGE_MAX_MS = 60000

# key, default, lowest, highest  (same order as the firmware's table)
KEYS = (
    ("pre_s", 30, 1, 120), ("post_s", 10, 1, 60),
    ("vbat_scale", 7912, 1, 100000), ("ecub_scale", 7912, 1, 100000),
    ("pump_scale", 7912, 1, 100000), ("ig1_scale", 7912, 1, 100000),
    ("alt_scale", 7912, 1, 100000),
    ("vbat_offset_mv", 0, -5000, 5000), ("ecub_offset_mv", 0, -5000, 5000),
    ("pump_offset_mv", 0, -5000, 5000), ("ig1_offset_mv", 0, -5000, 5000),
    ("alt_offset_mv", 0, -5000, 5000),
    ("tach_invert", 1, 0, 1), ("ign_invert", 1, 0, 1), ("inj_invert", 1, 0, 1),
    ("tach_ppr", 2, 1, 60), ("ign_ppr", 2, 1, 60),
    ("edge_min_us", 300, 0, 100000),
    ("run_rpm", 400, 50, 5000), ("run_ms", 500, 1, 60000),
    ("pulse_timeout_ms", 300, 20, 5000),
    ("ig1_on_mv", 8000, 0, 60000), ("ig1_off_mv", 6000, 0, 60000),
    ("ig1_debounce_ms", 10, 1, 5000),
    ("holdoff_s", 5, 0, 3600),
    ("host_mark_triggers", 0, 0, 1),
    ("live_hz", 50, 1, 500), ("summary_s", 1, 1, 3600), ("csv_export", 1, 0, 1),
    ("sleep_after_s", 300, 0, 86400), ("gps_baud", 0, 0, 115200), ("alt_enable", 0, 0, 1),
    ("edge_ring", 32768, 1024, 1000000),
)
_LIMITS = {k: (lo, hi) for k, _, lo, hi in KEYS}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    source: str
    op: str                 # "<" or ">"
    level: int
    hyst: int = 0
    hold_ms: int = 1
    qual: tuple = ()        # names from QUALS
    action: str = "capture"

    @property
    def qual_mask(self) -> int:
        return sum(1 << QUALS.index(q) for q in self.qual)

    def text(self) -> str:
        quals = "+".join(q for q in QUALS if q in self.qual) or "-"
        return f"{self.source} {self.op} {self.level} {self.hyst} {self.hold_ms} {quals} {self.action}"

    @classmethod
    def parse(cls, text: str) -> "Rule":
        tok = text.split()
        if len(tok) != 7:
            raise ConfigError(f"rule needs 7 fields: {text!r}")
        src, op, level, hyst, hold, quals, action = tok
        qual = () if quals == "-" else tuple(quals.split("+"))
        try:
            level, hyst, hold = int(level, 0), int(hyst, 0), int(hold, 0)
        except ValueError as exc:
            raise ConfigError(f"bad number in rule: {text!r}") from exc
        if (src not in SOURCES or op not in "<>" or len(op) != 1 or action not in ACTIONS
                or any(q not in QUALS for q in qual) or hyst < 0 or not 1 <= hold <= 60000):
            raise ConfigError(f"bad rule: {text!r}")
        return cls(src, op, level, hyst, hold, qual, action)


DEFAULT_RULES = {
    F.R_TACH_STOP: Rule("tach_age", ">", 250, 150, 1, ("ig1", "running")),
    F.R_IGN_STOP: Rule("ign_age", ">", 250, 150, 1, ("ig1", "ign_running")),
    F.R_IG1_DROP: Rule("ig1", "<", 6000, 2000, 10, ("running",)),
    F.R_ECUB_DROP: Rule("ecub", "<", 6000, 1000, 10, ("ig1",)),
    F.R_PUMP_DROP: Rule("pump", "<", 6000, 1000, 20, ("ig1", "running")),
    F.R_VBAT_DIP: Rule("vbat", "<", 9000, 500, 20, ("running",)),
    F.R_BUTTON: Rule("button", ">", 0, 0, 30, ()),
    F.R_BROWNOUT: Rule("vbat", "<", 6500, 1000, 3, ()),
    F.R_INJ_STOP: Rule("inj_age", ">", 2000, 1500, 1, ("ig1", "running", "tach_alive"), "mark"),
    F.R_ECUB_GLITCH: Rule("slew_ecub", "<", -3000, 2000, 1, ("ig1", "running")),
}


@dataclass
class Config:
    """Black box settings. ``values`` holds the plain integer keys."""

    values: dict = field(default_factory=lambda: {k: d for k, d, _, _ in KEYS})
    rules: dict = field(default_factory=lambda: dict(DEFAULT_RULES))    # code -> Rule

    def __getitem__(self, key: str) -> int:
        return self.values[key]

    def set(self, key: str, value) -> None:
        """Set one key from text or a number. Raises ConfigError."""
        if key.startswith("rule."):
            try:
                code = int(key[5:])
            except ValueError as exc:
                raise ConfigError(f"unknown key {key!r}") from exc
            if not 1 <= code < MAX_RULES or code == F.R_HOST:
                raise ConfigError(f"rule number must be 1..{MAX_RULES - 1}, not {F.R_HOST}")
            text = str(value).strip()
            if text == "off":
                if code in self.rules:
                    self.rules[code] = replace(self.rules[code], action="off")
            else:
                self.rules[code] = value if isinstance(value, Rule) else Rule.parse(text)
            return
        if key not in _LIMITS:
            raise ConfigError(f"unknown key {key!r}")
        try:
            number = int(str(value).strip(), 0)
        except ValueError as exc:
            raise ConfigError(f"{key}: not a number: {value!r}") from exc
        lo, hi = _LIMITS[key]
        if not lo <= number <= hi:
            raise ConfigError(f"{key}: {number} is outside {lo}..{hi}")
        self.values[key] = number

    @classmethod
    def parse(cls, text: str, strict: bool = True) -> "Config":
        """Parse config text. With strict=False bad lines are skipped, as the
        firmware does; see ``errors`` on the result."""
        cfg = cls()
        cfg.errors = []
        for no, line in enumerate(text.splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            key, sep, value = line.partition("=")
            try:
                if not sep:
                    raise ConfigError(f"no '=' in {line!r}")
                cfg.set(key.strip(), value.strip())
            except ConfigError as exc:
                if strict:
                    raise ConfigError(f"line {no}: {exc}") from exc
                cfg.errors.append((no, str(exc)))
        return cfg

    def dump(self) -> str:
        lines = [f"{k} = {self.values[k]}" for k, *_ in KEYS]
        lines += [f"rule.{code} = {self.rules[code].text()}" for code in sorted(self.rules)]
        return "\n".join(lines) + "\n"


# ---- edge timing (bb_edge_*) ------------------------------------------------

class EdgeTracker:
    """Period and pulse width of one pulse input. Levels are 1 = active."""

    def __init__(self):
        self.last_act_us = 0
        self.period_us = 0
        self.pw_us = 0
        self.level = 0
        self.seen = False

    def feed(self, t_us: int, level: int, min_us: int) -> None:
        level = 1 if level else 0
        if level and not self.level:
            if self.seen:
                d = t_us - self.last_act_us
                if d < min_us:
                    return
                self.period_us = 0 if d > 0xFFFFFFFF else d
            self.last_act_us = t_us
            self.seen = True
        elif not level and self.level and self.seen:
            self.pw_us = min(t_us - self.last_act_us, 0xFFFFFFFF)
        self.level = level

    def rpm(self, now_us: int, ppr: int, timeout_us: int) -> int:
        if not self.seen or not self.period_us or not ppr:
            return 0
        age = max(0, now_us - self.last_act_us)
        if age >= timeout_us:
            return 0
        return min(65535, 60_000_000 // (max(age, self.period_us) * ppr))

    def duty200(self, now_us: int, timeout_us: int) -> int:
        if not self.seen:
            return 0
        age = max(0, now_us - self.last_act_us)
        if age >= timeout_us:
            return 200 if self.level else 0
        if not self.period_us:
            return 0
        return min(200, self.pw_us * 200 // self.period_us)


# ---- trigger engine (bb_trigger_step) ---------------------------------------

@dataclass
class TrigInput:
    """One millisecond of data, as the rules see it."""
    t_us: int
    mv: tuple               # mean, 5 channels
    min_mv: tuple
    max_mv: tuple
    rpm_tach: int = 0
    rpm_ign: int = 0
    inj_pw_us: int = 0
    inj_duty: int = 0
    age_ms: tuple = (AGE_MAX_MS,) * 3
    button: int = 0


@dataclass(frozen=True)
class Fired:
    t_us: int
    code: int
    src: int
    value: int
    action: int = 0         # ACT_* for rule triggers, 0 for plain events

    @property
    def name(self) -> str:
        return F.reason_name(self.code)


class TriggerEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        v = cfg.values
        self._rules = [(code, SOURCES.index(r.source), r.op == "<", r.level, r.hyst, r.hold_ms,
                        r.qual_mask, ACTIONS.index(r.action))
                       for code, r in sorted(cfg.rules.items()) if r.action != "off"]
        self._cnt = [0] * MAX_RULES
        self._armed = [False] * MAX_RULES
        self._ig1_on, self._ig1_cnt = False, 0
        self.running = self.ign_running = False
        self._run_cnt = self._ign_run_cnt = 0
        self._qual = 0
        self._prev_mv = None
        self._ig1_on_mv, self._ig1_off_mv = v["ig1_on_mv"], v["ig1_off_mv"]
        self._ig1_deb = max(1, v["ig1_debounce_ms"])
        self._run_rpm, self._run_ms, self._timeout = v["run_rpm"], v["run_ms"], v["pulse_timeout_ms"]

    @property
    def ig1_on(self) -> bool:
        return self._ig1_on

    def _value(self, i: TrigInput, src: int, below: bool) -> int:
        if src <= 4:
            return i.min_mv[src] if below else i.max_mv[src]
        if src <= 9:
            return i.mv[src - 5] - self._prev_mv[src - 5] if self._prev_mv is not None else 0
        if src <= 12:
            return i.age_ms[src - 10]
        return (i.rpm_tach, i.rpm_ign, i.inj_pw_us, i.inj_duty, 1 if i.button else 0)[src - 13]

    def step(self, i: TrigInput) -> list[Fired]:
        out: list[Fired] = []
        t = i.t_us
        for code, src, below, level, hyst, hold, qual, action in self._rules:
            if (self._qual & qual) != qual or (qual & Q_IG1 and i.min_mv[F.A_IG1] < self._ig1_on_mv):
                self._armed[code], self._cnt[code] = False, 0
                continue
            v = self._value(i, src, below)
            if (v >= level + hyst) if below else (v <= level - hyst):
                self._armed[code] = True
            if self._armed[code] and ((v < level) if below else (v > level)):
                self._cnt[code] += 1
                if self._cnt[code] >= hold:
                    self._armed[code], self._cnt[code] = False, 0
                    out.append(Fired(t, code, src, v, action))
            else:
                self._cnt[code] = 0

        ig1_mv = i.mv[F.A_IG1]
        cond = ig1_mv > self._ig1_off_mv if self._ig1_on else ig1_mv >= self._ig1_on_mv
        if cond == self._ig1_on:
            self._ig1_cnt = 0
        else:
            self._ig1_cnt += 1
            if self._ig1_cnt >= self._ig1_deb:
                self._ig1_on, self._ig1_cnt = cond, 0
                out.append(Fired(t, F.EV_IG1_ON if cond else F.EV_IG1_OFF, SOURCES.index("ig1"), ig1_mv))

        if i.rpm_tach >= self._run_rpm:
            if not self.running:
                self._run_cnt += 1
                if self._run_cnt >= self._run_ms:
                    self.running = True
                    out.append(Fired(t, F.EV_RUNNING, SOURCES.index("rpm_tach"), i.rpm_tach))
        elif not self.running:
            self._run_cnt = 0
        if self.running and i.age_ms[F.E_TACH] >= self._timeout:
            self.running, self._run_cnt = False, 0

        if i.rpm_ign >= self._run_rpm:
            if not self.ign_running:
                self._ign_run_cnt += 1
                if self._ign_run_cnt >= self._run_ms:
                    self.ign_running = True
        elif not self.ign_running:
            self._ign_run_cnt = 0
        if self.ign_running and i.age_ms[F.E_IGN] >= self._timeout:
            self.ign_running, self._ign_run_cnt = False, 0

        self._qual = ((Q_IG1 if self._ig1_on and ig1_mv >= self._ig1_on_mv else 0)
                      | (Q_RUNNING if self.running else 0)
                      | (Q_IGN_RUNNING if self.ign_running else 0)
                      | (Q_TACH_ALIVE if i.age_ms[F.E_TACH] < self._timeout else 0))
        self._prev_mv = i.mv
        return out
