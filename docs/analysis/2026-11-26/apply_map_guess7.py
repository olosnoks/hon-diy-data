"""Apply the 2026-11-26 drive findings to the user's address map (p2p-guess-6 -> p2p-guess-7).

Sources: drive-bytes.md, drive-ect-electrical.md, drive-fueling.md, flag-bits.md in this folder.
Run once: .venv\\Scripts\\python.exe docs\\analysis\\2026-11-26\\apply_map_guess7.py
"""
import re
import shutil
from pathlib import Path

MAP = Path.home() / ".config" / "hondalog" / "addressmap.toml"
shutil.copy(MAP, MAP.with_name("addressmap.p2p-guess-6.toml"))
text = MAP.read_text(encoding="utf-8").replace("\r\n", "\n")

head, *blocks = re.split(r"(?m)^(?=\[\[channel\]\]\n)", text)
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
    sub = re.search(r"(?m)^\[channel\.", block)
    if sub:
        return block[: sub.start()] + line + "\n" + block[sub.start():]
    return block.rstrip("\n") + "\n" + line + "\n\n"


def q(s):
    return '"' + s.replace('"', "'") + '"'


EDITS = {
    "brake": {"status": q("verified"),
              "notes": q("Set in 97 % of reads when slowing faster than 4 km/h/s (drives 2026-11-26)")},
    "vtec": {"notes": q("Seen on 2026-11-26 23:03:11.7-12.7 at ~5,400 rpm WOT 2nd gear (with 0x0C b3)")},
    "ign_limit": {"label": q("RPM x MAP index"),
                  "notes": q("Not an ignition value. Idle: 0 below ~845 rpm, +1 per ~50 rpm to 4; 5 key-on, 10 cranking. Off idle: 4 below ~42 kPa, then ~0.85 per kPa (drive-bytes.md)")},
    "knock": {"label": q("RPM index 0x3C"), "max": "12",
              "notes": q("Steps 1-11 with rpm only; maybe knock-sensor noise level, not knock retard (drive-bytes.md)")},
    "raw_2a": {"name": q("iac_learn"), "label": q("IAC learn"), "max": "8",
               "notes": q("Learned idle air 0-6: follows iac_cmd at fully warm idle (~40 s), frozen off idle (slow-bytes.md, drive-bytes.md)")},
    "raw_2f": {"name": q("purge_duty"), "label": q("Purge duty?"),
               "notes": q("Probably EVAP purge duty: restarts ~20 when the throttle settles, climbs with airflow to 255, cleared by throttle moves and decel cut; STFT falls as it rises. At idle it ran only above ~68 °C ECT (drive-bytes.md)")},
    "o2_2": {"status": q("unverified"),
             "notes": q("Real rear O2: own warm-up after start, 0.02 V in fuel cut, 0.74 V median at cruise; tracks the front at hard driving (weak cat?) (drive-fueling.md, drive-bytes.md)")},
}
DROP = {"fan"}  # 0x0C b1 never set in ~58 fan cycles; the fan is thermoswitch-controlled

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
# ---- flag bits confirmed or found on the 2026-11-26 drives (flag-bits.md, drive-bytes.md) ----

[[channel]]
name = "starter"
label = "Starter"
addr = 0x08
length = 1
formula = "bit(x, 0)"
kind = "flag"
status = "verified"
rate = "slow"
source = "flag-bits.md: set only in the first crank read of all 3 starts"

[[channel]]
name = "psp"
label = "Steering load"
addr = 0x08
length = 1
formula = "1 - bit(x, 2)"
kind = "flag"
status = "verified"
rate = "slow"
source = "drive-bytes.md: bit 2 reads 0 in 18 reads, all at junction turns or parking; 1 here = PS pressure switch on"

[[channel]]
name = "vtec_psw"
label = "VTEC press. sw"
addr = 0x0C
length = 1
formula = "bit(x, 3)"
kind = "flag"
status = "unverified"
rate = "slow"
source = "drive-bytes.md: set only with vtec at 5,400 rpm WOT (2026-11-26 23:03:11)"

[[channel]]
name = "o2_heater"
label = "O2 heater?"
addr = 0x0B
length = 1
formula = "bit(x, 6)"
kind = "flag"
status = "unverified"
rate = "slow"
source = "drive-bytes.md: off above ~3,500 rpm at MAP >= 84 kPa, on otherwise; not 'engine turning'. Alternative: alternator control"

'''
# keep channels before the sweep section, then the new flags
text = head + "".join(out).rstrip("\n") + "\n" + NEW + tail
text = re.sub(r'(?m)^version = "[^"]*"', 'version = "p2p-guess-7 (2026-11-26 drive findings: flags, purge, rpm indexes)"', text)
MAP.write_text(text, encoding="utf-8", newline="\n")
print("written", MAP)
