"""HANDHELD.CFG as text: ``key = value   # comment`` lines. The Handheld tab edits it as a form
and as raw text; both are views of the same text, so comments and unknown lines survive."""

from __future__ import annotations

import re
from dataclasses import dataclass

LINE = re.compile(r"^(?P<lead>\s*#?\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[^#]*?)\s*(?:#\s*(?P<comment>.*))?$")


@dataclass
class Entry:
    key: str
    value: str
    comment: str
    enabled: bool       # False: the line is commented out (``# rotate = 180``)
    line: int


def parse(text: str) -> list[Entry]:
    out: list[Entry] = []
    for n, raw in enumerate(text.splitlines()):
        m = LINE.match(raw)
        if not m:
            continue
        lead = m.group("lead")
        enabled = "#" not in lead
        # a commented-out line is a setting only when it reads like one (``# rotate = 180``): prose with an
        # equals sign somewhere has spaces in its key position
        if not enabled and not re.fullmatch(r"\s*#\s*", lead):
            continue
        out.append(Entry(m.group("key"), m.group("value").strip(), (m.group("comment") or "").strip(), enabled, n))
    return out


def set_value(text: str, key: str, value: str, enable: bool = True) -> str:
    """The text with ``key`` set to ``value`` (its line kept, with its comment); added at the end if there is none."""
    lines = text.splitlines()
    for e in parse(text):
        if e.key == key:
            comment = f"   # {e.comment}" if e.comment else ""
            lines[e.line] = ("" if enable else "# ") + f"{key} = {value}{comment}"
            return "\n".join(lines) + "\n"
    lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def values(text: str) -> dict[str, str]:
    """The settings that are in force (not commented out), last one wins."""
    return {e.key: e.value for e in parse(text) if e.enabled}


def problems(text: str, known: set[str]) -> list[str]:
    """Lines the handheld will skip: a key it does not know, or a line that is not ``key = value``."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.split("#", 1)[0].strip()
        if not s:
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\S", s)
        if not m:
            out.append(f"line {n}: not 'key = value'")
        elif m.group(1) not in known:
            out.append(f"line {n}: unknown setting {m.group(1)!r}")
    return out
