"""Constants pass (2026-11-26): confirm the fixed bytes in the 0x00-0xFF sweep and
convert each through every formula in the map plus the J1979 ones.

Read only: reads ../work-sweep/car-214932-wide.csv, prints to stdout.
Run: .venv\\Scripts\\python.exe docs\\analysis\\2026-11-26\\work-constants\\constants.py
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
WIDE = HERE.parent / "work-sweep" / "car-214932-wide.csv"


def ect_poly(x: float) -> float:
    c = (155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437)
    return sum(k * x ** i for i, k in enumerate(c))


FORMULAS = {
    "ECT/IAT poly degC": ect_poly,
    "OBD temp x-40 degC": lambda x: x - 40,
    "MAP x*0.716-5 kPa": lambda x: x * 0.716 - 5,
    "x/2.55 %": lambda x: x / 2.55,
    "x/51 V (ADC 0-5V)": lambda x: x / 51,
    "x/51.3 V (O2)": lambda x: x / 51.3,
    "x/200 V (OBD O2)": lambda x: x / 200,
    "x/10.45 V (batt)": lambda x: x / 10.45,
    "77.06-x/2.5371 A (ELD)": lambda x: 77.06 - x / 2.5371,
    "(x/128-1)*100 % (trim)": lambda x: (x / 128 - 1) * 100,
    "(x-24)/4 deg (kerpz ign)": lambda x: (x - 24) / 4,
    "(x-46)/4 deg (guess-6 ign)": lambda x: (x - 46) / 4,
    "(x-128)/2 deg (OBD ign)": lambda x: (x - 128) / 2,
    "x*4 rpm": lambda x: x * 4,
    "x*25 rpm": lambda x: x * 25,
    "x*0.1 s": lambda x: x * 0.1,
}

TARGETS = [0x32, 0x33, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x8F, 0x94, 0x95, 0x96, 0x99, 0xAD]


def main() -> None:
    rows = list(csv.DictReader(WIDE.open(newline="")))
    n = len(rows)
    col = {a: [int(r[f"b{a:02X}"]) for r in rows] for a in range(256)}
    print(f"{n} sweeps")
    print("\n# every byte in 0x30-0x3F and 0x76-0xFF that is ever non-zero")
    for a in list(range(0x30, 0x40)) + list(range(0x76, 0x100)):
        c = Counter(col[a])
        if set(c) != {0}:
            s = ", ".join(f"{v}(0x{v:02X},s{v - 256 if v > 127 else v})x{k}" for v, k in c.most_common())
            print(f"0x{a:02X}: {s}")

    print("\n# conversions")
    for a in TARGETS:
        v = Counter(col[a]).most_common(1)[0][0]
        print(f"0x{a:02X} = {v} (0x{v:02X}, bin {v:08b}, BCD-ok={'yes' if (v >> 4) < 10 and (v & 15) < 10 else 'no'})")
        for name, f in FORMULAS.items():
            print(f"    {name:28s} {f(v):9.2f}")

    print("\n# 0x78-0x7F ID block")
    idb = [Counter(col[a]).most_common(1)[0][0] for a in range(0x78, 0x80)]
    print("hex  ", " ".join(f"{b:02X}" for b in idb))
    print("dec  ", idb)
    print("ascii", "".join(chr(b) if 32 <= b < 127 else "." for b in idb))
    print("sum8 ", hex(sum(idb) & 0xFF), " xor", hex(__import__("functools").reduce(lambda p, q: p ^ q, idb)))
    print("sum16", hex(sum((idb[i] << 8) | idb[i + 1] for i in range(0, 8, 2)) & 0xFFFF))
    print("words BE", [hex((idb[i] << 8) | idb[i + 1]) for i in range(0, 8, 2)],
          [(idb[i] << 8) | idb[i + 1] for i in range(0, 8, 2)])
    print("nibbles", "".join(f"{b:02X}" for b in idb))

    print("\n# 0x80-0x8F learned-table view (signed)")
    tab = [Counter(col[a]).most_common(1)[0][0] for a in range(0x80, 0x90)]
    print(" ".join(f"{(v - 256 if v > 127 else v):4d}" for v in tab))
    print("  /4 :", " ".join(f"{(v - 256 if v > 127 else v) / 4:5.2f}" for v in tab))
    print("  as 4x4 :")
    for i in range(0, 16, 4):
        print("   ", [(v - 256 if v > 127 else v) for v in tab[i:i + 4]])

    # 0x86 vs live channels
    print("\n# 0x86 state vs live channels (mean)")
    keys = {"stft": 0x20, "o2": 0x15, "ect": 0x10, "iacv": 0x28, "x29": 0x29, "map": 0x12, "load9C": 0x9C,
            "o2_2_A0": 0xA0, "x32": 0x32, "x37": 0x37, "xA4": 0xA4}
    rpm = [(int(r["b00"]) << 8 | int(r["b01"])) / 4 for r in rows]
    tgt = [(int(r["b76"]) << 8 | int(r["b77"])) / 4 for r in rows]
    for state in (253, 249):
        idx = [i for i in range(n) if col[0x86][i] == state]
        line = f"0x86={state} n={len(idx):3d}"
        line += f" rpm {sum(rpm[i] for i in idx) / len(idx):6.0f} tgt {sum(tgt[i] for i in idx) / len(idx):6.0f}"
        for k, a in keys.items():
            line += f" {k} {sum(col[a][i] for i in idx) / len(idx):6.1f}"
        print(line)
    # previous-sweep STFT (0x86 lags STFT by one sweep in the sweep report)
    for state in (253, 249):
        idx = [i for i in range(1, n) if col[0x86][i] == state]
        print(f"0x86={state}: STFT one sweep earlier mean {sum(col[0x20][i - 1] for i in idx) / len(idx):.1f}, "
              f"O2 one sweep earlier {sum(col[0x15][i - 1] for i in idx) / len(idx):.1f}")
    print("0x86 runs:", end=" ")
    prev, run = None, 0
    runs = []
    for v in col[0x86]:
        if v == prev:
            run += 1
        else:
            if prev is not None:
                runs.append((prev, run))
            prev, run = v, 1
    runs.append((prev, run))
    print([(v - 256, k) for v, k in runs])

    # 0x9C vs OBD PID 04 scaling and 0xA0 vs PID 15
    print("\n# mirror checks against first-contact PIDs (11-25 17:38, different minute)")
    print("PID 04 = 0x3E = 62 -> 24.3 %;  0x9C range", min(col[0x9C]), max(col[0x9C]))
    print("PID 15 A = 0xC3 = 195 -> 0.975 V; 195/4 =", 195 / 4, "; 0xA0 range", min(col[0xA0]), max(col[0xA0]),
          "-> x*4/200 V =", min(col[0xA0]) * 4 / 200, "-", max(col[0xA0]) * 4 / 200)
    print("PID 03 = 02 00; 0x9A:0x9B =", Counter(zip(col[0x9A], col[0x9B])).most_common())
    print("PID 02 (freeze DTC) = 00 00; 0x98:0x99 =", Counter(zip(col[0x98], col[0x99])).most_common())
    print("PID 01 = 00 07 65 25; 0x94..0x97 =", Counter(zip(*(col[a] for a in range(0x94, 0x98)))).most_common())


if __name__ == "__main__":
    main()
