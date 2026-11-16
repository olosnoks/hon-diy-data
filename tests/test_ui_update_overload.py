"""Debian trixie's PySide6 binds QAbstractItemView.update(index) so that a bare
``view.update()`` raises TypeError. Repainting every child must go through
QWidget.update(widget). The pip build accepts both, so check the source too."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "hondalog"


def test_no_bare_update_over_all_children():
    bad = []
    for f in UI.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"for (\w+) in [^\n]*findChildren\([^\n]*\n\s+\1\.update\(\)", text):
            bad.append(f"{f.relative_to(UI.parent)}:{text[:m.start()].count(chr(10)) + 1}")
    assert not bad, f"use QWidget.update(w) for arbitrary children: {bad}"
