"""Change an address map file without wrecking the user's comments.

``MapFile`` holds the file's text and its parsed form side by side. Every
edit is applied to the parsed data (which is always right) and, line by line,
to the text (which keeps comments and layout). After each edit the text is
parsed again; if it does not match the data, the file is rewritten from the
data instead and ``rewritten`` is set, so the worst case is lost comments,
never a wrong map. ``save`` validates the result as a real AddressMap, writes
atomically and keeps the previous file as ``.bak``.

Also here: map versions, the diff between two maps, and re-decoding raw bytes
with a newer map.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tomllib
from dataclasses import MISSING as _MISSING
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .. import addressmap
from ..addressmap import AddressMap, Channel, Evidence, MapError, Obd2Channel
from ..alarms import AlarmRule
from ..mathch import MathChannel

_HEADER = re.compile(r"^\s*(\[\[?)\s*([^\[\]]+?)\s*\]\]?\s*(#.*)?$")
_KEY_LINE = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*)$")
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_HEX_KEYS = {"addr", "pid"}
ARRAYS = ("channel", "obd2", "math", "alarm")


# ---- TOML output ------------------------------------------------------------

def _string(text: str) -> str:
    out = json.dumps(text, ensure_ascii=False)
    return out.replace("\x7f", "\\u007f")


def fmt_value(value: Any, key: str = "") -> str:
    """One TOML value on one line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"0x{value:02X}" if key in _HEX_KEYS and value >= 0 else str(value)
    if isinstance(value, float):
        if value != value:
            return "nan"
        if value in (float("inf"), float("-inf")):
            return "inf" if value > 0 else "-inf"
        text = repr(value)
        return text if any(c in text for c in ".e") else text + ".0"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(fmt_value(v) for v in value) + "]"
    if isinstance(value, Mapping):
        inner = ", ".join(f"{_key(k)} = {fmt_value(v, k)}" for k, v in value.items())
        return "{ " + inner + " }" if inner else "{}"
    raise MapError(f"cannot write a {type(value).__name__} to a map file")


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _string(key)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, Mapping) for v in value)


def _emit(lines: list[str], table: Mapping[str, Any], path: list[str]) -> None:
    for key, value in table.items():
        if not isinstance(value, Mapping) and not _is_table_array(value):
            lines.append(f"{_key(key)} = {fmt_value(value, key)}")
    for key, value in table.items():
        if isinstance(value, Mapping):
            # A table with only sub-tables needs no header of its own.
            if any(not isinstance(v, Mapping) for v in value.values()) or not value:
                lines += ["", "[" + ".".join(_key(p) for p in [*path, key]) + "]"]
            _emit(lines, value, [*path, key])
    for key, value in table.items():
        if _is_table_array(value):
            for item in value:
                lines += ["", "[[" + ".".join(_key(p) for p in [*path, key]) + "]]"]
                _emit(lines, item, [*path, key])


_ORDER = ("schema", "version", "ecu", "ecu_variant", "base_variant", "variants", "ecu_id",
          "protocol", "constants", "channel", "obd2", "math", "alarm")


def dumps(doc: Mapping[str, Any]) -> str:
    """Write a parsed map document back out as TOML (no comments)."""
    ordered = {k: doc[k] for k in _ORDER if k in doc}
    ordered.update({k: v for k, v in doc.items() if k not in ordered})
    lines = ["# hondalog address map. Field help: see the shipped default map."]
    _emit(lines, ordered, [])
    return "\n".join(lines) + "\n"


# ---- model -> tables --------------------------------------------------------

def _table_of(obj: Any, required: Sequence[str]) -> dict[str, Any]:
    """Dataclass -> TOML table holding the required fields and anything not default."""
    out: dict[str, Any] = {}
    for f in fields(obj):
        if f.name.startswith("_"):
            continue
        value = getattr(obj, f.name)
        default: Any = f.default
        if f.default_factory is not _MISSING:                 # type: ignore[misc]
            default = f.default_factory()                     # type: ignore[misc]
        if f.name not in required and (value == default or value is None):
            continue
        if isinstance(value, Evidence):
            value = value.to_table()
        elif isinstance(value, tuple):
            value = list(value)
        elif isinstance(value, Mapping):
            value = copy.deepcopy(dict(value))
        out[f.name] = value
    return out


def channel_table(ch: Channel) -> dict[str, Any]:
    table = _table_of(ch, ("name", "addr", "length", "formula", "status"))
    if table.get("label") == ch.name:
        del table["label"]
    for variant in table.get("variants", {}).values():
        if isinstance(variant.get("evidence"), Evidence):
            variant["evidence"] = variant["evidence"].to_table()
    return table


def obd2_table(ch: Obd2Channel) -> dict[str, Any]:
    table = _table_of(ch, ("name", "pid"))
    if table.get("label") == ch.name:
        del table["label"]
    return table


def math_table(ch: MathChannel) -> dict[str, Any]:
    table = _table_of(ch, ("name", "expr"))
    if table.get("label") == ch.name:
        del table["label"]
    return table


def alarm_table(rule: AlarmRule) -> dict[str, Any]:
    table = _table_of(rule, ("name",))
    default_priority = {"warn": 50, "critical": 100}[rule.severity]
    if table.get("priority") == default_priority:
        del table["priority"]
    if table.get("label") == rule.name.replace("_", " "):
        del table["label"]
    return table


def to_doc(amap: AddressMap) -> dict[str, Any]:
    """A map built in code -> document that ``dumps`` can write. Covers every
    variant, not just the one that is loaded."""
    doc: dict[str, Any] = {"schema": addressmap.SCHEMA, "version": amap.version, "ecu": amap.ecu}
    if amap.variant:
        doc["ecu_variant"] = amap.variant
    if amap.variants:
        doc["variants"] = copy.deepcopy(amap.variants)
    if amap.ecu_id_addr is not None:
        doc["ecu_id"] = {"addr": amap.ecu_id_addr, "length": amap.ecu_id_len}
    if amap.constants:
        doc["constants"] = dict(amap.constants)
    doc["channel"] = [channel_table(c) for c in amap.base_channels]
    if amap.obd2:
        doc["obd2"] = [obd2_table(c) for c in amap.obd2]
    if amap.math:
        doc["math"] = [math_table(m) for m in amap.math]
    if amap.alarms:
        doc["alarm"] = [alarm_table(a) for a in amap.alarms]
    return doc


# ---- text patching ----------------------------------------------------------

def _split_comment(rest: str) -> tuple[str, str]:
    """'value   # note' -> ('value', '   # note'). A '#' inside a string stays."""
    pos = rest.find("#")
    while pos >= 0:
        try:
            tomllib.loads("k = " + rest[:pos])
            return rest[:pos].rstrip(), rest[len(rest[:pos].rstrip()):]
        except tomllib.TOMLDecodeError:
            pos = rest.find("#", pos + 1)
    return rest.rstrip(), ""


@dataclass
class _Section:
    header: int          # line index of the [header] line (-1 for the file's top)
    path: tuple[str, ...]
    is_array: bool
    end: int             # one past the last line belonging to the section


def _sections(lines: Sequence[str]) -> list[_Section]:
    out = [_Section(-1, (), False, len(lines))]
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        path = tuple(p.strip().strip('"') for p in m.group(2).split("."))
        out[-1].end = i
        out.append(_Section(i, path, m.group(1) == "[[", len(lines)))
    return out


def _content_end(lines: Sequence[str], start: int, end: int) -> int:
    """One past the last line in [start, end) that is not blank or a comment.
    Comments just above the next header belong to that header, not to us."""
    last = start
    for i in range(start, end):
        text = lines[i].strip()
        if text and not text.startswith("#"):
            last = i + 1
    return last


def _set_keys(lines: list[str], start: int, end: int, updates: Mapping[str, Any]) -> int:
    """Set/replace/delete plain keys among lines[start:end]. Returns the new end."""
    for key, value in updates.items():
        found = None
        for i in range(start, end):
            m = _KEY_LINE.match(lines[i])
            if m and m.group(2) == key:
                found = (i, m)
                break
        if value is None:
            if found:
                del lines[found[0]]
                end -= 1
            continue
        text = fmt_value(value, key)
        if found:
            i, m = found
            _, comment = _split_comment(m.group(4))
            lines[i] = f"{m.group(1)}{key}{m.group(3)}{text}{comment}"
        else:
            at = _content_end(lines, start, end)
            lines.insert(at, f"{_key(key)} = {text}")
            end += 1
    return end


class MapFile:
    """An address map file open for editing. See the module docstring."""

    def __init__(self, text: str, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.text = text
        self.rewritten = False           # True once comments had to be dropped
        try:
            self.doc: dict[str, Any] = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise MapError(f"{path or 'address map'}: {exc}") from exc

    @classmethod
    def open(cls, path: str | Path) -> "MapFile":
        path = Path(path)
        try:
            return cls(path.read_text(encoding="utf-8"), path)
        except OSError as exc:
            raise MapError(f"{path}: {exc}") from exc

    # -- lookups --

    def _entry(self, kind: str, name: str) -> dict[str, Any]:
        for entry in self.doc.get(kind, []):
            if entry.get("name") == name:
                return entry                                  # type: ignore[no-any-return]
        raise MapError(f"no {kind} named {name!r} in the map")

    def names(self, kind: str = "channel") -> list[str]:
        return [e.get("name", "") for e in self.doc.get(kind, [])]

    # -- edits --

    def set_top(self, key: str, value: Any) -> None:
        """Set a top-level key such as version or ecu_variant."""
        want = copy.deepcopy(self.doc)
        want[key] = value
        lines = self.text.split("\n")
        _set_keys(lines, 0, _sections(lines)[0].end, {key: value})
        self._commit(lines, want)

    def set_constant(self, name: str, value: float | None) -> None:
        want = copy.deepcopy(self.doc)
        table = want.setdefault("constants", {})
        if value is None:
            table.pop(name, None)
        else:
            table[name] = value
        lines = self.text.split("\n")
        section = next((s for s in _sections(lines) if s.path == ("constants",)), None)
        if section is None:
            lines += ["", "[constants]", f"{name} = {fmt_value(value)}"] if value is not None else []
        else:
            _set_keys(lines, section.header + 1, section.end, {name: value})
        self._commit(lines, want)

    def set_item(self, kind: str, name: str, variant: str | None = None, **changes: Any) -> None:
        """Change fields of one [[channel]], [[math]] or [[alarm]] entry.

        A value of ``None`` removes the key. A dict value (``evidence=...``)
        becomes a sub-table. ``variant`` edits ``[channel.variants.<variant>]``
        instead of the entry itself.
        """
        if kind not in ARRAYS:
            raise MapError(f"unknown section {kind!r}")
        want = copy.deepcopy(self.doc)
        entry = next((e for e in want.get(kind, []) if e.get("name") == name), None)
        if entry is None:
            raise MapError(f"no {kind} named {name!r} in the map")
        sub: tuple[str, ...] = ()
        target = entry
        if variant:
            sub = ("variants", variant)
            target = entry.setdefault("variants", {}).setdefault(variant, {})
        for key, value in changes.items():
            if isinstance(value, Evidence):
                changes[key] = value = value.to_table()
            if value is None:
                target.pop(key, None)
            else:
                target[key] = copy.deepcopy(value)
        lines = self.text.split("\n")
        try:
            self._patch_item(lines, kind, name, sub, changes)
        except _CannotPatch:
            lines = []
        self._commit(lines, want)

    def set_channel(self, name: str, variant: str | None = None, **changes: Any) -> None:
        self.set_item("channel", name, variant, **changes)

    def add_item(self, kind: str, table: Mapping[str, Any]) -> None:
        """Append a new [[channel]], [[math]] or [[alarm]] entry."""
        if kind not in ARRAYS:
            raise MapError(f"unknown section {kind!r}")
        name = table.get("name")
        if name in self.names(kind):
            raise MapError(f"there is already a {kind} named {name!r}")
        want = copy.deepcopy(self.doc)
        want.setdefault(kind, []).append(copy.deepcopy(dict(table)))
        block: list[str] = [f"[[{kind}]]"]
        _emit(block, table, [kind])
        lines = self.text.split("\n")
        sections = _sections(lines)
        own = [s for s in sections if s.path and s.path[0] == kind]
        if own:
            start = max(s.header for s in own if s.path == (kind,))
            end = max(s.end for s in own if s.header >= start)
            at = _content_end(lines, start, end)
            lines[at:at] = ["", *block]
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", *block, ""]
        self._commit(lines, want)

    def remove_item(self, kind: str, name: str) -> None:
        self._entry(kind, name)
        want = copy.deepcopy(self.doc)
        want[kind] = [e for e in want[kind] if e.get("name") != name]
        if not want[kind]:
            del want[kind]
        lines = self.text.split("\n")
        try:
            start, end = self._item_span(lines, kind, name)
            del lines[start:_content_end(lines, start, end)]
        except _CannotPatch:
            lines = []
        self._commit(lines, want)

    # -- text machinery --

    def _item_span(self, lines: Sequence[str], kind: str, name: str) -> tuple[int, int]:
        """Line range of one array entry, sub-tables included."""
        sections = _sections(lines)
        for idx, sec in enumerate(sections):
            if not (sec.is_array and sec.path == (kind,)):
                continue
            end = sec.end
            for later in sections[idx + 1:]:
                if later.path[:1] == (kind,) and len(later.path) > 1 and not later.is_array:
                    end = later.end
                else:
                    break
            for i in range(sec.header + 1, sec.end):
                m = _KEY_LINE.match(lines[i])
                if m and m.group(2) == "name":
                    value, _ = _split_comment(m.group(4))
                    try:
                        if tomllib.loads("k = " + value)["k"] == name:
                            return sec.header, end
                    except tomllib.TOMLDecodeError:
                        pass
                    break
        raise _CannotPatch

    def _patch_item(self, lines: list[str], kind: str, name: str, sub: tuple[str, ...],
                    changes: Mapping[str, Any]) -> None:
        start, end = self._item_span(lines, kind, name)
        plain = {k: v for k, v in changes.items() if not isinstance(v, Mapping)}
        tables = {k: v for k, v in changes.items() if isinstance(v, Mapping)}

        def find(path: tuple[str, ...]) -> _Section | None:
            for sec in _sections(lines):
                if start <= sec.header < end and sec.path == (kind, *path) \
                        and (sec.header == start) == (not path):
                    return sec
            return None

        # keys that may currently exist as a sub-table (evidence) and are being removed
        for key, value in list(plain.items()):
            old = find((*sub, key))
            if old is not None:
                del lines[old.header:_content_end(lines, old.header, min(old.end, end))]
                start, end = self._item_span(lines, kind, name)
                if value is None:
                    del plain[key]
        if plain:
            sec = find(sub)
            if sec is None:                       # new [kind.variants.X] table
                at = _content_end(lines, start, end)
                new = ["", "[" + ".".join((kind, *sub)) + "]"]
                new += [f"{_key(k)} = {fmt_value(v, k)}" for k, v in plain.items() if v is not None]
                lines[at:at] = new
            else:
                _set_keys(lines, sec.header + 1, min(sec.end, end), plain)
            start, end = self._item_span(lines, kind, name)
        for key, table in tables.items():
            path = (*sub, key)
            block = ["[" + ".".join((kind, *path)) + "]"]
            block += [f"{_key(k)} = {fmt_value(v, k)}" for k, v in table.items()]
            old = find(path)
            if old is not None:
                stop = _content_end(lines, old.header, min(old.end, end))
                lines[old.header:stop] = block
            else:
                if any(_KEY_LINE.match(lines[i]) and _KEY_LINE.match(lines[i]).group(2) == key  # type: ignore[union-attr]
                       for i in range(start, end)):
                    raise _CannotPatch          # written as an inline table: rewrite instead
                if sub and find(sub) is None:
                    raise _CannotPatch
                # a sub-table of a variant goes right after that variant's keys
                anchor = find(sub) if sub else None
                at = (_content_end(lines, anchor.header, min(anchor.end, end)) if anchor
                      else _content_end(lines, start, end))
                lines[at:at] = ["", *block]
            start, end = self._item_span(lines, kind, name)

    def _commit(self, lines: list[str], want: dict[str, Any]) -> None:
        text = "\n".join(lines)
        try:
            ok = bool(lines) and tomllib.loads(text) == want
        except tomllib.TOMLDecodeError:
            ok = False
        if not ok:
            text = dumps(want)
            self.rewritten = True
        self.text, self.doc = text, want

    # -- results --

    def load(self, variant: str | None = None) -> AddressMap:
        """The edited map as an AddressMap. Raises MapError if it is not valid."""
        return addressmap.parse(self.doc, str(self.path or ""), variant)

    def save(self, path: str | Path | None = None, backup: bool = True) -> Path:
        """Validate, then write atomically. The old file is kept as ``.bak``."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise MapError("no file name to save the map to")
        self.load()
        target.parent.mkdir(parents=True, exist_ok=True)
        if backup and target.exists():
            target.with_name(target.name + ".bak").write_bytes(target.read_bytes())
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(self.text if self.text.endswith("\n") else self.text + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        self.path = target
        return target


class _CannotPatch(Exception):
    """The text could not be edited in place; fall back to a full rewrite."""


# ---- one-call operations ----------------------------------------------------

def next_version(version: str) -> str:
    """'p2p-guess-2 (kerpz)' -> 'p2p-guess-3 (kerpz)'; no number -> append ' r2'.

    The last number before any bracketed note is bumped, so model names such
    as "p2p" are left alone.
    """
    cut = version.find("(")
    head, tail = (version, "") if cut < 0 else (version[:cut], version[cut:])
    nums = list(re.finditer(r"\d+", head))
    if nums and not head[nums[-1].end():].strip(" -_.").isalpha():
        m = nums[-1]
        return f"{head[:m.start()]}{int(m.group()) + 1}{head[m.end():]}{tail}"
    return f"{version} r2" if version else "r2"


def now_stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def mark_channel(path: str | Path, channel: str, evidence: Evidence,
                 status: str = addressmap.VERIFIED, variant: str | None = None) -> AddressMap:
    """Record a verification result in the map file and return the reloaded map.

    Sets the channel's status and attaches the evidence. For a variant other
    than the map's base one the mark goes into ``[channel.variants.<variant>]``.
    The map's version hash does not change: nothing about decoding did.
    """
    if status not in addressmap.STATUSES:
        raise MapError(f"status must be one of {', '.join(addressmap.STATUSES)}")
    mf = MapFile.open(path)
    current = mf.load(variant)
    if current.channel(channel) is None:
        raise MapError(f"no channel named {channel!r} in {path}")
    target = current.variant if current.variant != current.base_variant else None
    filled = Evidence(**{**evidence.to_table()})
    filled.date = filled.date or now_stamp()
    filled.map_version = filled.map_version or current.version
    filled.map_hash = filled.map_hash or current.version_hash
    filled.result = filled.result or ("pass" if status == addressmap.VERIFIED else "fail")
    mf.set_channel(channel, variant=target, status=status, evidence=filled.to_table())
    mf.save()
    return mf.load(variant)


def update_channel(path: str | Path, channel: str, variant: str | None = None,
                   bump: bool = True, **changes: Any) -> AddressMap:
    """Change a channel's address, length, formula ... in the map file.

    Anything that changes decoding resets the channel to unverified and drops
    its evidence (it described the old definition), and bumps the version.
    """
    mf = MapFile.open(path)
    current = mf.load(variant)
    ch = current.channel(channel)
    if ch is None:
        raise MapError(f"no channel named {channel!r} in {path}")
    target = current.variant if current.variant != current.base_variant else None
    decoding = {"addr", "length", "formula", "endian", "units", "kind"}
    if any(k in decoding and getattr(ch, k) != v for k, v in changes.items()):
        changes.setdefault("status", addressmap.UNVERIFIED)
        if ch.evidence is not None:
            changes.setdefault("evidence", None)
    mf.set_channel(channel, variant=target, **changes)
    if bump and mf.load(variant).version_hash != current.version_hash:
        mf.set_top("version", next_version(str(mf.doc.get("version", ""))))
    mf.save()
    return mf.load(variant)


def upgrade_to_v2(path: str | Path) -> tuple[AddressMap, list[str]]:
    """Bring a schema 1 map file up to schema 2 in place.

    Adds ``schema = 2`` and the shipped constants, OBD2-only channels, math channels and alarms
    that fit the file's channels. Returns the map and the names that were
    skipped because an input channel is missing. A schema 2 file is left alone.
    """
    mf = MapFile.open(path)
    if int(mf.doc.get("schema", 1)) >= 2:
        return mf.load(), []
    default = tomllib.loads(addressmap.DEFAULT_MAP.read_text(encoding="utf-8"))
    mf.set_top("schema", addressmap.SCHEMA)
    for name, value in default.get("constants", {}).items():
        if name not in mf.doc.get("constants", {}) and name not in mf.names("channel"):
            mf.set_constant(name, value)
    skipped: list[str] = []
    for kind in ("obd2", "math", "alarm"):
        for table in default.get(kind, []):
            if table["name"] in mf.names(kind):
                continue
            trial = copy.deepcopy(mf)
            try:
                trial.add_item(kind, table)
                trial.load()
            except MapError:
                skipped.append(table["name"])
                continue
            mf.text, mf.doc, mf.rewritten = trial.text, trial.doc, trial.rewritten
    mf.save()
    return mf.load(), skipped


# ---- versions and diff ------------------------------------------------------

@dataclass(frozen=True)
class MapChange:
    section: str          # "channel" | "math" | "alarm" | "constant" | "map"
    name: str
    field: str            # "" for added/removed entries
    old: Any
    new: Any

    @property
    def kind(self) -> str:
        if not self.field:
            return "added" if self.old is None else "removed"
        return "changed"

    def __str__(self) -> str:
        where = f"{self.section} {self.name}".strip()
        if self.kind == "added":
            return f"+ {where}"
        if self.kind == "removed":
            return f"- {where}"
        return f"~ {where}: {self.field} {_show(self.old)} -> {_show(self.new)}"


def _show(value: Any) -> str:
    if isinstance(value, Evidence):
        return f"<{value.method or 'evidence'} {value.date}>"
    return "(none)" if value is None or value == "" else repr(value)


@dataclass
class MapDiff:
    changes: list[MapChange]
    old_hash: str
    new_hash: str

    @property
    def decode_changed(self) -> bool:
        """True when old logs would decode differently with the new map."""
        return self.old_hash != self.new_hash

    def __bool__(self) -> bool:
        return bool(self.changes)

    def text(self) -> str:
        head = (f"decoding changed: {self.old_hash} -> {self.new_hash}" if self.decode_changed
                else f"decoding unchanged ({self.new_hash})")
        return "\n".join([head, *(str(c) for c in self.changes)] if self.changes
                         else [head, "no differences"])


def _diff_items(section: str, old: Mapping[str, Any], new: Mapping[str, Any],
                out: list[MapChange]) -> None:
    for name in old:
        if name not in new:
            out.append(MapChange(section, name, "", old[name], None))
    for name, item in new.items():
        if name not in old:
            out.append(MapChange(section, name, "", None, item))
            continue
        for f in fields(item):
            if f.name.startswith("_") or f.name in ("name", "variants"):
                continue
            a, b = getattr(old[name], f.name), getattr(item, f.name)
            if a != b:
                out.append(MapChange(section, name, f.name, a, b))


def diff_maps(old: AddressMap, new: AddressMap) -> MapDiff:
    """What changed between two maps, as loaded (one ECU variant each)."""
    out: list[MapChange] = []
    for attr in ("version", "ecu", "variant", "ecu_id_addr", "ecu_id_len"):
        a, b = getattr(old, attr), getattr(new, attr)
        if a != b:
            out.append(MapChange("map", "", attr, a, b))
    if old.framing != new.framing:
        for f in fields(old.framing):
            a, b = getattr(old.framing, f.name), getattr(new.framing, f.name)
            if a != b:
                out.append(MapChange("protocol", "", f.name, a, b))
    for name in sorted(set(old.constants) | set(new.constants)):
        a, b = old.constants.get(name), new.constants.get(name)
        if a != b:
            out.append(MapChange("constant", name, "value" if None not in (a, b) else "", a, b))
    _diff_items("channel", {c.name: c for c in old.channels},
                {c.name: c for c in new.channels}, out)
    _diff_items("math", {m.name: m for m in old.math}, {m.name: m for m in new.math}, out)
    _diff_items("alarm", {a.name: a for a in old.alarms}, {a.name: a for a in new.alarms}, out)
    return MapDiff(out, old.version_hash, new.version_hash)


def redecode(amap: AddressMap, frames: Iterable[tuple[float, Mapping[int, int]]],
             with_math: bool = True) -> dict[str, np.ndarray]:
    """Decode stored raw RAM bytes again with (another version of) a map.

    ``frames`` yields ``(time_s, {address: byte})`` for each stored read or
    sweep. Returns ``{"t": ..., channel: ...}`` columns with NaN where a
    channel's bytes were not in the frame, math channels included.
    """
    times: list[float] = []
    rows: list[dict[str, float | None]] = []
    for t, mem in frames:
        times.append(float(t))
        rows.append(amap.decode(mem))
    out: dict[str, np.ndarray] = {"t": np.asarray(times, dtype=float)}
    for name in amap.names:
        out[name] = np.array([np.nan if r[name] is None else r[name] for r in rows], dtype=float)
    if with_math and amap.math:
        out.update(amap.math_engine().compute_series(out["t"], out))
    return out
