"""Apply the 2026-11-26 findings to the user's address map (p2p-guess-5 -> p2p-guess-6).

Sources: channel-verification.md, sweep-40-ff.md, README.md in this folder.
Run once: .venv\\Scripts\\python.exe docs\\analysis\\2026-11-26\\apply_map_guess6.py
"""
import re
import shutil
from pathlib import Path

MAP = Path.home() / ".config" / "hondalog" / "addressmap.toml"
shutil.copy(MAP, MAP.with_name("addressmap.p2p-guess-5.toml"))
text = MAP.read_text(encoding="utf-8").replace("\r\n", "\n")

head, *blocks = re.split(r"(?m)^(?=\[\[channel\]\]\n)", text)
# the last channel block also holds everything after it ([[math]], [[alarm]] ...)
tail = ""
m = re.search(r"(?m)^(?=\[\[(math|alarm)\]\])", blocks[-1])
if m:
    blocks[-1], tail = blocks[-1][: m.start()], blocks[-1][m.start():]


def name_of(block):
    return re.search(r'(?m)^name = "([^"]+)"', block).group(1)


def setkey(block, key, value):
    line = f"{key} = {value}"
    if re.search(rf"(?m)^{key} = ", block):
        return re.sub(rf"(?m)^{key} = .*$", lambda _: line, block, count=1)
    # insert before a [channel.*] sub-table, or at the end of the block
    sub = re.search(r"(?m)^\[channel\.", block)
    if sub:
        return block[: sub.start()] + line + "\n" + block[sub.start():]
    return block.rstrip("\n") + "\n" + line + "\n\n"


def q(s):
    return '"' + s.replace('"', "'") + '"'


EDITS = {
    "baro": {"status": q("verified"),
             "notes": q("2026-11-26: equals key-on engine-off MAP raw-for-raw (147 = 100.3 kPa); max MAP ever 147")},
    "o2": {"status": q("verified"),
           "notes": q("2026-11-26: warm closed-loop idle matches OBD2 PID 14 within ~0.02 V. 185-200 raw for ~10 s after start is an ECU start-up value")},
    "ign": {"formula": q("(x - 46) / 4"),
            "notes": q("2026-11-26: offset fitted to OBD2 PID 0E (raw 94 = 12.0 deg at warm idle; KOEO raw 43 = ~0). Was (x - 24) / 4, ~5.5 deg high")},
    "ign_limit": {"status": q("suspect")},
    "knock": {"label": q("Raw 0x3C"), "formula": q("x"), "units": q(""), "min": "0", "max": "4", "decimals": "0",
              "status": q("suspect"),
              "notes": q("Only 1, with single-read 2s, on the P2P: not a knock voltage")},
    "fan": {"status": q("suspect"),
            "notes": q("Never set over 53 radiator-fan cycles (seen in ELD); the fan is thermoswitch-controlled")},
    "batt": {"notes": q("Plausible: 14.0-14.5 V running, 12.6-13.1 V key-on, 10.9 V cranking. Confirm once with a meter")},
    "alt_fr": {"notes": q("Behaves like field duty: +16 counts per ~7.5 A load step at constant rpm. Scale unproven")},
    "eld": {"notes": q("Fan steps -19 counts (+7.5 A), A/C -32..-35 (+13 A): slope plausible, zero point unproven")},
    "inj": {"notes": q("Physics fits: 2.44 ms warm idle = ~1.0 ms dead time + 1.44 ms; 0 in decel cut; 9-11 ms cranking")},
    "iacv": {"notes": q("Lagged fixed curve of iac_cmd (0x29, r 0.95). Reads 0 at warm idle: scale unproven")},
    "brake": {"notes": q("Set in 3 reads 2026-11-26 21:09:18-23, each with an rpm dip and ALT FR jump. Press-test to verify")},
}
# constants that never changed in ~1 h of logs: dropped to make room on the CYD (their
# addresses stay inside read blocks, so the raw bytes are still logged)
DROP = {"raw_33", "raw_35", "raw_36", "raw_38", "raw_39", "raw_3a"}

out, seen = [], set()
for b in blocks:
    n = name_of(b)
    seen.add(n)
    if n in DROP:
        continue
    for k, v in EDITS.get(n, {}).items():
        b = setkey(b, k, v)
    out.append(b)
missing = (set(EDITS) | DROP) - seen
assert not missing, f"not in the map: {missing}"

NEW = '''
# ---- found in the 0x40-0xFF sweep, 2026-11-26 (docs/analysis/2026-11-26/sweep-40-ff.md) ----

[[channel]]
name = "idle_target"
label = "Idle target"
addr = 0x76
length = 2
formula = "x / 4"
units = "rpm"
min = 500
max = 1500
decimals = 0
status = "unverified"
rate = "medium"
source = "sweep 2026-11-26: rpm follows it 2-3 s later (r 0.95), steps of ~13.6 rpm"

[[channel]]
name = "load_ecu"
label = "Load (ECU)"
addr = 0x9C
length = 1
formula = "x / 2.55"
units = "%"
min = 0
max = 100
decimals = 0
status = "unverified"
rate = "slow"
source = "sweep 2026-11-26: OBD2 calculated-load copy, 21-24 % at idle (PID 04 read 24.3 %)"

[[channel]]
name = "fuel_status"
label = "Fuel system"
addr = 0x9A
length = 1
formula = "x"
units = ""
min = 0
max = 16
decimals = 0
status = "unverified"
rate = "slow"
source = "sweep 2026-11-26: OBD2 fuel-system-status copy; 2 = closed loop"

[[channel]]
name = "o2_2"
label = "O2 rear"
addr = 0xA0
length = 1
formula = "x / 51.3"
units = "V"
min = 0
max = 1.1
decimals = 2
status = "suspect"
rate = "slow"
source = "sweep 2026-11-26: 0.92-0.97 V, but follows the front O2 in the same second (r 0.72): rear sensor or derived value"

'''
text = head + "".join(out).rstrip("\n") + "\n" + NEW + tail
text = re.sub(r"(?m)^\[ecu_id\]\naddr = 0x76\nlength = 10",
              "[ecu_id]\naddr = 0x78                 # 0x76-0x77 is the live idle target (sweep 2026-11-26)\nlength = 8", text)
text = re.sub(r'(?m)^version = "[^"]*"', 'version = "p2p-guess-6 (2026-11-26 findings: ign offset, idle target, OBD2 copies)"', text)
MAP.write_text(text, encoding="utf-8", newline="\n")
print("written", MAP)
