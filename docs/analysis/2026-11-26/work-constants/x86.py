"""0x86 (-3 / -7) against ECT state, STFT and O2 sweep by sweep. Read only."""
import csv
from pathlib import Path

rows = list(csv.DictReader((Path(__file__).resolve().parent.parent / "work-sweep" / "car-214932-wide.csv").open()))
g = lambda r, a: int(r[f"b{a:02X}"])
cold = [g(r, 0x10) > 100 for r in rows]
x86 = [g(r, 0x86) == 249 for r in rows]
n_cold = sum(cold)
print(f"sweeps {len(rows)}, ECT raw>100 (false-cold) {n_cold}")
print(f"0x86=-7 while false-cold: {sum(a and b for a, b in zip(x86, cold))}/{n_cold}; "
      f"while warm: {sum(a and not b for a, b in zip(x86, cold))}/{len(rows) - n_cold}")
for i, r in enumerate(rows):
    if x86[i] or (i and x86[i - 1]):
        print(r["wall"][11:19], "x86", g(r, 0x86) - 256, "ect", g(r, 0x10), "stft", g(r, 0x20), "o2", g(r, 0x15),
              "rpm", (g(r, 0) << 8 | g(r, 1)) // 4, "tgt", (g(r, 0x76) << 8 | g(r, 0x77)) // 4, "x29", g(r, 0x29),
              "x2A", g(r, 0x2A))
