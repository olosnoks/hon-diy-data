"""Which block precedes each failure class, against its share of reads; time structure."""
import sys, collections, statistics as st, bisect
from load import load
did = int(sys.argv[1]) if len(sys.argv) > 1 else 33
reads, flags, epoch = load(did)
T = [r[0] for r in reads]
downs = [t for t, v in flags if v == 0]
def cls(x):
    if 0.110 <= x < 0.180: return "badframe+retry (110-180 ms)"
    if 0.210 <= x < 0.300: return "noreply+retry (210-300 ms)"
    if 0.330 <= x: return "drop (>=330 ms)"
    if 0.050 <= x < 0.110: return "slow exchange (50-110 ms)"
    return None
share = collections.Counter(r[1] for r in reads)
tot = sum(share.values())
ev = collections.defaultdict(list)
for a, b in zip(reads, reads[1:]):
    c = cls(b[0]-a[0])
    if c: ev[c].append((a, b))
for c in sorted(ev):
    prev = collections.Counter(a[1] for a, b in ev[c])
    nxt = collections.Counter(b[1] for a, b in ev[c])
    n = len(ev[c])
    print(f"\n{c}: n={n}  ({n/(T[-1]-T[0])*60:.1f}/min)")
    print("   block   prev%  next%  read%")
    for k in sorted(share):
        print(f"   {k:10s} {100*prev[k]/n:5.1f} {100*nxt[k]/n:6.1f} {100*share[k]/tot:6.1f}")
    ts = [b[0] for a, b in ev[c]]
    iv = [y-x for x, y in zip(ts, ts[1:])]
    if len(iv) > 5:
        m = st.mean(iv); sd = st.pstdev(iv)
        print(f"   interval mean {m:.1f}s sd {sd:.1f}s (cv {sd/m:.2f}; Poisson ~1)")
# do single failures cluster around drops?
dr = [b[0] for a, b in ev["drop (>=330 ms)"]]
for c in ("badframe+retry (110-180 ms)", "noreply+retry (210-300 ms)"):
    ts = [b[0] for a, b in ev[c]]
    near = sum(1 for t in ts if any(abs(t-d) < 3 for d in dr))
    exp = len(ts) * len(dr) * 6 / (T[-1]-T[0])
    print(f"{c}: within 3 s of a drop {near} (chance {exp:.1f})")
# rpm dependence: per-minute counts vs mean rpm
