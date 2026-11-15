"""TunerPro XDF (XML) definitions: read into, and write from, the native model.

Covers what XDF 1.50/1.60 files in the wild use: header (title, base offset,
defaults, categories), XDFTABLE (X/Y/Z axes with embedded data, labels, math),
XDFFUNCTION (a 2D curve), XDFCONSTANT, XDFFLAG and XDFPATCH.

Checked against public BMW MSS54 XDFs (not Honda ones - none could be fetched):
element and attribute names, ``CATEGORYMEM category`` being index + 1,
``mmedtypeflags`` 0x01 = signed, and the stride rule in ``Storage``.
Taken from TunerPro lore and *not* seen in a sample file: type flags 0x02
(LSB first), 0x04 (column-major), 0x10000 (float); ``VAR type="address"`` and
``type="link"``; the meaning of ``BASEOFFSET subtract``. XDFCHECKSUM blocks
are reported in ``Definition.warnings`` and otherwise ignored.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .definition import (Axis, Category, Defaults, Definition, DefinitionError, Flag,
                         Item, MathVar, Patch, PatchEntry, Scalar, Storage, Table,
                         slugify)
from .mathexpr import Expression, MathError

FLAG_SIGNED = 0x01
FLAG_LSB_FIRST = 0x02
FLAG_COLUMN_MAJOR = 0x04
FLAG_FLOAT = 0x10000


def _int(text: str | None, default: int = 0) -> int:
    if text is None or not text.strip():
        return default
    try:
        return int(text.strip(), 0)
    except ValueError:
        try:
            return int(float(text))
        except ValueError as exc:
            raise DefinitionError(f"bad number {text!r} in XDF") from exc


def _float(text: str | None) -> float | None:
    if text is None or not text.strip():
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _text(el: ET.Element | None, tag: str, default: str = "") -> str:
    if el is None:
        return default
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.replace("\r\n", "\n").replace("\r", "\n")


def _decode(blob: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue
    return blob.decode("latin-1")


# ---- reading ----------------------------------------------------------------

class _Reader:
    def __init__(self, root: ET.Element, source: str):
        self.root = root
        self.d = Definition(source=source)
        self.taken: set[str] = set()
        self.cat_names: dict[int, str] = {}

    def warn(self, text: str) -> None:
        self.d.warnings.append(text)

    def header(self) -> None:
        d, h = self.d, self.root.find("XDFHEADER")
        if h is None:
            self.warn("no XDFHEADER; using defaults")
            return
        d.title = _text(h, "deftitle")
        d.description = _text(h, "description")
        d.author = _text(h, "author")
        d.version = _text(h, "fileversion")
        base = h.find("BASEOFFSET")
        if base is not None:
            offset = _int(base.get("offset"))
            d.base_offset = -offset if _int(base.get("subtract")) else offset
        elif h.find("baseoffset") is not None:
            d.base_offset = _int(_text(h, "baseoffset"))
        df = h.find("DEFAULTS")
        if df is not None:
            d.defaults = Defaults(bits=_int(df.get("datasizeinbits"), 8),
                                  signed=bool(_int(df.get("signed"))),
                                  lsb_first=bool(_int(df.get("lsbfirst"))),
                                  is_float=bool(_int(df.get("float"))),
                                  sigdigits=_int(df.get("sigdigits"), 2))
        region = h.find("REGION")
        if region is not None and _int(region.get("size")) > 0:
            d.rom_size = _int(region.get("size"))
        for c in h.findall("CATEGORY"):
            index = _int(c.get("index"))
            name = c.get("name") or f"Category {index}"
            self.cat_names[index] = name
            d.categories.append(Category(index, name))

    def head(self, el: ET.Element, fallback: str) -> dict:
        title = _text(el, "title").strip() or fallback
        cats = []
        for mem in el.findall("CATEGORYMEM"):
            index = _int(mem.get("category")) - 1
            if index in self.cat_names and self.cat_names[index] not in cats:
                cats.append(self.cat_names[index])
        return {"id": slugify(title, self.taken), "title": title,
                "description": _text(el, "description"), "categories": cats,
                "uniqueid": el.get("uniqueid") or "", "flags": _int(el.get("flags"))}

    def storage(self, emb: ET.Element | None, *, rows: int = 1, cols: int = 1,
                where: str) -> Storage | None:
        if emb is None or emb.get("mmedaddress") is None:
            return None
        flags = _int(emb.get("mmedtypeflags"))
        try:
            return Storage(
                address=_int(emb.get("mmedaddress")),
                bits=_int(emb.get("mmedelementsizebits"), self.d.defaults.bits),
                signed=bool(flags & FLAG_SIGNED), lsb_first=bool(flags & FLAG_LSB_FIRST),
                is_float=bool(flags & FLAG_FLOAT), rows=rows, cols=cols,
                column_major=bool(flags & FLAG_COLUMN_MAJOR),
                major_stride_bits=_int(emb.get("mmedmajorstridebits")),
                minor_stride_bits=_int(emb.get("mmedminorstridebits")))
        except DefinitionError as exc:
            raise DefinitionError(f"{where}: {exc}") from exc

    def math(self, el: ET.Element, where: str, notes: list[str]) -> tuple[str, list[MathVar]]:
        maths = el.findall("MATH")
        general = [m for m in maths if m.get("row") is None and m.get("col") is None]
        if len(general) < len(maths):
            notes.append("per-row or per-cell equations are ignored")
        if not general:
            return "X", []
        m = general[0]
        equation = (m.get("equation") or "X").strip() or "X"
        cell_var, extra = None, []
        for v in m.findall("VAR"):
            name = (v.get("id") or "").strip()
            kind = (v.get("type") or "").lower()
            if kind == "address" or (not kind and v.get("address") is not None):
                bits = _int(v.get("sizeinbits") or v.get("mmedelementsizebits"),
                            self.d.defaults.bits)
                flags = _int(v.get("flags") or v.get("mmedtypeflags"))
                extra.append(MathVar(name.upper(), "address", Storage(
                    _int(v.get("address")), bits, bool(flags & FLAG_SIGNED),
                    bool(flags & FLAG_LSB_FIRST), bool(flags & FLAG_FLOAT))))
            elif kind == "link" or v.get("linkid") is not None:
                extra.append(MathVar(name.upper(), "link", None, v.get("linkid") or ""))
            elif cell_var is None and name:
                cell_var = name
        if cell_var and cell_var.upper() != "X":
            if any(v.name == "X" for v in extra):
                notes.append(f"equation {equation!r} uses X for something else")
            else:
                equation = re.sub(rf"\b{re.escape(cell_var)}\b", "X", equation,
                                  flags=re.I)
        if equation.upper() == "X":
            equation = "X"
        try:
            Expression(equation)
        except MathError as exc:
            notes.append(f"{exc}; values are shown raw")
        return equation, extra

    def axis(self, el: ET.Element | None, where: str, notes: list[str],
             *, rows: int | None = None, cols: int | None = None) -> Axis:
        if el is None:
            return Axis()
        emb = el.find("EMBEDDEDDATA")
        is_z = rows is not None
        count = _int(_text(el, "indexcount"), 0)
        if not is_z:
            if emb is not None:
                emb_count = max(_int(emb.get("mmedcolcount"), 0),
                                _int(emb.get("mmedrowcount"), 0))
                count = count or emb_count
            count = count or 1
            storage = self.storage(emb, rows=1, cols=count, where=where)
        else:
            storage = self.storage(emb, rows=rows or 1, cols=cols or 1, where=where)
        labels_by_index = {_int(l.get("index")): l.get("value") or ""
                           for l in el.findall("LABEL")}
        labels: list[str] = []
        if labels_by_index and any(v.strip() for v in labels_by_index.values()):
            n = max(count if not is_z else 0, max(labels_by_index) + 1)
            labels = [labels_by_index.get(i, "") for i in range(n)]
        embed = el.find("embedinfo")
        link = ""
        if embed is not None and embed.get("linkobjid"):
            link = embed.get("linkobjid") or ""
        equation, extra = self.math(el, where, notes)
        decimals = el.find("decimalpl")
        output = el.find("outputtype")
        return Axis(storage=storage, units=_text(el, "units"),
                    decimals=_int(decimals.text) if decimals is not None else None,
                    math=equation, vars=extra, labels=labels,
                    count=0 if is_z else count, link=link,
                    min=_float(_text(el, "min") or None),
                    max=_float(_text(el, "max") or None),
                    output_type=_int(output.text) if output is not None else None)

    def table(self, el: ET.Element, curve: bool) -> None:
        head = self.head(el, "Function" if curve else "Table")
        where = f"table {head['title']!r}"
        notes: list[str] = []
        axes = {(a.get("id") or "").lower(): a for a in el.findall("XDFAXIS")}
        if curve:
            xa, ya = axes.get("x"), axes.get("y")
            emb = ya.find("EMBEDDEDDATA") if ya is not None else None
            if ya is None or emb is None:
                self.warn(f"{where}: no Y data, skipped")
                return
            x = self.axis(xa, where + " x", notes)
            n = (_int(_text(ya, "indexcount"), 0) or _int(emb.get("mmedcolcount"), 0)
                 or _int(emb.get("mmedrowcount"), 0) or x.count or 1)
            z = self.axis(ya, where + " y", notes, rows=1, cols=n)
            y = Axis(count=1)
        else:
            za = axes.get("z")
            emb = za.find("EMBEDDEDDATA") if za is not None else None
            if za is None or emb is None or emb.get("mmedaddress") is None:
                self.warn(f"{where}: no cell data address, skipped")
                return
            rows = max(1, _int(emb.get("mmedrowcount"), 1))
            cols = max(1, _int(emb.get("mmedcolcount"), 1))
            z = self.axis(za, where + " z", notes, rows=rows, cols=cols)
            x = self.axis(axes.get("x"), where + " x", notes)
            y = self.axis(axes.get("y"), where + " y", notes)
            for name, axis, n in (("x", x, cols), ("y", y, rows)):
                if axis.count == n:
                    continue
                if axis.storage is not None or axis.labels:
                    notes.append(f"{name} axis has {axis.count} points for {n} cells")
                if axis.storage is not None and axis.count > n:
                    axis.storage.cols = n
                else:
                    axis.storage = None
                axis.labels = (axis.labels + [""] * n)[:n] if axis.labels else []
                axis.count = n
        if z.storage is None:
            self.warn(f"{where}: no cell data address, skipped")
            return
        table = Table(**head, x=x, y=y, z=z, curve=curve, notes=notes)
        self._finish(table)
        self.d.tables.append(table)

    def constant(self, el: ET.Element) -> None:
        head = self.head(el, "Constant")
        where = f"constant {head['title']!r}"
        notes: list[str] = []
        storage = self.storage(el.find("EMBEDDEDDATA"), where=where)
        if storage is None:
            self.warn(f"{where}: no address, skipped")
            return
        equation, extra = self.math(el, where, notes)
        decimals = el.find("decimalpl")
        scalar = Scalar(**head, storage=storage, math=equation, vars=extra,
                        units=_text(el, "units"),
                        decimals=_int(decimals.text) if decimals is not None else None,
                        min=_float(_text(el, "rangelow") or _text(el, "min") or None),
                        max=_float(_text(el, "rangehigh") or _text(el, "max") or None),
                        notes=notes)
        self._finish(scalar)
        self.d.scalars.append(scalar)

    def flag(self, el: ET.Element) -> None:
        head = self.head(el, "Flag")
        where = f"flag {head['title']!r}"
        storage = self.storage(el.find("EMBEDDEDDATA"), where=where)
        if storage is None:
            self.warn(f"{where}: no address, skipped")
            return
        self.d.flags.append(Flag(**head, storage=storage,
                                 mask=_int(_text(el, "mask"), 1)))

    def patch(self, el: ET.Element) -> None:
        head = self.head(el, "Patch")
        entries = []
        for e in el.findall("XDFPATCHENTRY"):
            try:
                data = bytes.fromhex(e.get("patchdata") or "")
                base_hex = e.get("basedata")
                base = bytes.fromhex(base_hex) if base_hex else None
            except ValueError:
                self.warn(f"patch {head['title']!r}: bad hex data, entry skipped")
                continue
            size = _int(e.get("datasize"), len(data))
            if size != len(data):
                self.warn(f"patch {head['title']!r}: datasize does not match data")
            if base is not None and len(base) != len(data):
                base = None
            entries.append(PatchEntry(_int(e.get("address")), data, base,
                                      e.get("name") or ""))
        self.d.patches.append(Patch(**head, entries=entries))

    def _finish(self, item: Item) -> None:
        for note in item.notes:
            self.warn(f"{item.title}: {note}")

    def run(self) -> Definition:
        self.header()
        for el in self.root:
            if el.tag == "XDFTABLE":
                self.table(el, curve=False)
            elif el.tag == "XDFFUNCTION":
                self.table(el, curve=True)
            elif el.tag == "XDFCONSTANT":
                self.constant(el)
            elif el.tag == "XDFFLAG":
                self.flag(el)
            elif el.tag == "XDFPATCH":
                self.patch(el)
            elif el.tag == "XDFCHECKSUM":
                self.warn("the XDF has a checksum block; it is not interpreted. "
                          "Describe the checksum in the native definition instead.")
            elif el.tag != "XDFHEADER":
                self.warn(f"unknown element <{el.tag}> ignored")
        return self.d


def loads_xdf(text: str | bytes, source: str = "") -> Definition:
    if isinstance(text, bytes):
        text = _decode(text)
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise DefinitionError("XDF with a DOCTYPE or entities is refused")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DefinitionError(f"{source or 'XDF'}: not valid XML ({exc})") from exc
    if root.tag != "XDFFORMAT":
        raise DefinitionError(
            f"{source or 'XDF'}: root element is <{root.tag}>, expected <XDFFORMAT>. "
            "Old binary XDFs must be re-saved by TunerPro 5 first.")
    return _Reader(root, source).run()


def load_xdf(path: str | Path) -> Definition:
    return loads_xdf(Path(path).read_bytes(), str(path))


# ---- writing ----------------------------------------------------------------

def _embedded(st: Storage | None, *, shape: str) -> str:
    if st is None:
        return ""
    flags = ((FLAG_SIGNED if st.signed else 0) | (FLAG_LSB_FIRST if st.lsb_first else 0)
             | (FLAG_COLUMN_MAJOR if st.column_major else 0)
             | (FLAG_FLOAT if st.is_float else 0))
    parts = []
    if flags:
        parts.append(f'mmedtypeflags="0x{flags:02X}"')
    parts.append(f'mmedaddress="0x{st.address:X}"')
    parts.append(f'mmedelementsizebits="{st.bits}"')
    if shape == "table":
        parts.append(f'mmedrowcount="{st.rows}"')
    if shape in ("table", "axis"):
        parts.append(f'mmedcolcount="{st.cols}"')
    parts.append(f'mmedmajorstridebits="{st.major_stride_bits}"')
    parts.append(f'mmedminorstridebits="{st.minor_stride_bits}"')
    return f"<EMBEDDEDDATA {' '.join(parts)} />"


def _math(equation: str, extra: list[MathVar], indent: str) -> list[str]:
    lines = [f"{indent}<MATH equation={quoteattr(equation)}>",
             f'{indent}  <VAR id="X" />']
    for v in extra:
        if v.kind == "link":
            lines.append(f'{indent}  <VAR id={quoteattr(v.name)} type="link" '
                         f"linkid={quoteattr(v.link)} />")
        elif v.storage is not None:
            st = v.storage
            flags = ((FLAG_SIGNED if st.signed else 0)
                     | (FLAG_LSB_FIRST if st.lsb_first else 0)
                     | (FLAG_FLOAT if st.is_float else 0))
            lines.append(f'{indent}  <VAR id={quoteattr(v.name)} type="address" '
                         f'address="0x{st.address:X}" sizeinbits="{st.bits}" '
                         f'flags="0x{flags:X}" />')
    lines.append(f"{indent}</MATH>")
    return lines


def _head_xml(it: Item, cat_index: dict[str, int]) -> list[str]:
    lines = [f"    <title>{escape(it.title)}</title>"]
    if it.description:
        lines.append(f"    <description>{_escape_text(it.description)}</description>")
    for n, name in enumerate(it.categories):
        if name in cat_index:
            lines.append(f'    <CATEGORYMEM index="{n}" category="{cat_index[name] + 1}" />')
    return lines


def _escape_text(text: str) -> str:
    return escape(text).replace("\n", "&#013;&#010;")


def _axis_xml(axis: Axis, ident: str, *, shape: str) -> list[str]:
    lines = [f'    <XDFAXIS id="{ident}"' + ('' if ident == "z" else ' uniqueid="0x0"') + ">"]
    emb = _embedded(axis.storage, shape=shape)
    if emb:
        lines.append("      " + emb)
    if axis.units:
        lines.append(f"      <units>{escape(axis.units)}</units>")
    if ident != "z":
        lines.append(f"      <indexcount>{axis.count}</indexcount>")
    if axis.decimals is not None:
        lines.append(f"      <decimalpl>{axis.decimals}</decimalpl>")
    if axis.min is not None:
        lines.append(f"      <min>{axis.min:.6f}</min>")
    if axis.max is not None:
        lines.append(f"      <max>{axis.max:.6f}</max>")
    if axis.output_type is not None:
        lines.append(f"      <outputtype>{axis.output_type}</outputtype>")
    if ident != "z":
        if axis.link:
            lines.append(f'      <embedinfo type="3" linkobjid={quoteattr(axis.link)} />')
        elif axis.storage is not None:
            lines.append('      <embedinfo type="1" />')
        lines.append("      <datatype>0</datatype>")
        lines.append("      <unittype>0</unittype>")
        lines.append('      <DALINK index="0" />')
        for i, label in enumerate(axis.labels):
            lines.append(f'      <LABEL index="{i}" value={quoteattr(label)} />')
    lines.extend(_math(axis.math, axis.vars, "      "))
    lines.append("    </XDFAXIS>")
    return lines


def dumps_xdf(d: Definition) -> str:
    """Write a definition as XDF 1.60 XML. Checksum rules and signatures are
    native-only and are left out."""
    cat_index: dict[str, int] = {c.name: c.index for c in d.categories}
    for it in d.items():
        for name in it.categories:
            if name not in cat_index:
                cat_index[name] = max(cat_index.values(), default=-1) + 1
    out = ["<!-- Written by hondalog -->", '<XDFFORMAT version="1.60">', "  <XDFHEADER>",
           "    <flags>0x1</flags>",
           f"    <fileversion>{escape(d.version)}</fileversion>",
           f"    <deftitle>{escape(d.title)}</deftitle>",
           f"    <description>{_escape_text(d.description)}</description>",
           f"    <author>{escape(d.author)}</author>",
           f'    <BASEOFFSET offset="{abs(d.base_offset)}" '
           f'subtract="{1 if d.base_offset < 0 else 0}" />',
           f'    <DEFAULTS datasizeinbits="{d.defaults.bits}" '
           f'sigdigits="{d.defaults.sigdigits}" outputtype="1" '
           f'signed="{int(d.defaults.signed)}" lsbfirst="{int(d.defaults.lsb_first)}" '
           f'float="{int(d.defaults.is_float)}" />',
           f'    <REGION type="0xFFFFFFFF" startaddress="0x0" size="0x{d.rom_size:X}" '
           'regionflags="0x0" name="Binary File" '
           'desc="This region describes the bin file edited by this XDF" />']
    for name, index in sorted(cat_index.items(), key=lambda kv: kv[1]):
        out.append(f'    <CATEGORY index="0x{index:X}" name={quoteattr(name)} />')
    out.append("  </XDFHEADER>")
    used: set[str] = set()

    def uid(it: Item) -> str:
        value = it.uniqueid if it.uniqueid and it.uniqueid not in used else ""
        n = 0x1000
        while not value or value in used:
            value = f"0x{n:X}"
            n += 1
        used.add(value)
        return value

    for t in d.tables:
        tag = "XDFFUNCTION" if t.curve else "XDFTABLE"
        out.append(f'  <{tag} uniqueid="{uid(t)}" flags="0x{t.flags:X}">')
        out.extend(_head_xml(t, cat_index))
        if t.curve:
            out.extend(_axis_xml(t.x, "x", shape="axis"))
            y = Axis(storage=t.z.storage, units=t.z.units, decimals=t.z.decimals,
                     math=t.z.math, vars=t.z.vars, count=t.cols, min=t.z.min,
                     max=t.z.max, output_type=t.z.output_type)
            out.extend(_axis_xml(y, "y", shape="axis"))
        else:
            out.extend(_axis_xml(t.x, "x", shape="axis"))
            out.extend(_axis_xml(t.y, "y", shape="axis"))
            out.extend(_axis_xml(t.z, "z", shape="table"))
        out.append(f"  </{tag}>")
    for s in d.scalars:
        out.append(f'  <XDFCONSTANT uniqueid="{uid(s)}" flags="0x{s.flags:X}">')
        out.extend(_head_xml(s, cat_index))
        out.append("    " + _embedded(s.storage, shape="scalar"))
        if s.units:
            out.append(f"    <units>{escape(s.units)}</units>")
        if s.decimals is not None:
            out.append(f"    <decimalpl>{s.decimals}</decimalpl>")
        if s.min is not None:
            out.append(f"    <rangelow>{s.min:.6f}</rangelow>")
        if s.max is not None:
            out.append(f"    <rangehigh>{s.max:.6f}</rangehigh>")
        out.extend(["    <datatype>0</datatype>", "    <unittype>0</unittype>",
                    '    <DALINK index="0" />'])
        out.extend(_math(s.math, s.vars, "    "))
        out.append("  </XDFCONSTANT>")
    for f in d.flags:
        out.append(f'  <XDFFLAG uniqueid="{uid(f)}" flags="0x{f.flags:X}">')
        out.extend(_head_xml(f, cat_index))
        out.append("    " + _embedded(f.storage, shape="scalar"))
        out.append(f"    <mask>0x{f.mask:02X}</mask>")
        out.append("  </XDFFLAG>")
    for p in d.patches:
        out.append(f'  <XDFPATCH uniqueid="{uid(p)}">')
        out.extend(_head_xml(p, cat_index))
        for e in p.entries:
            base = f' basedata="{e.base.hex().upper()}"' if e.base is not None else ""
            out.append(f"    <XDFPATCHENTRY name={quoteattr(e.name)} "
                       f'address="0x{e.address:X}" datasize="0x{len(e.data):X}" '
                       f'patchdata="{e.data.hex().upper()}"{base} />')
        out.append("  </XDFPATCH>")
    out.append("</XDFFORMAT>")
    return "\r\n".join(out) + "\r\n"


def save_xdf(d: Definition, path: str | Path) -> None:
    Path(path).write_text(dumps_xdf(d), encoding="utf-8", newline="")
