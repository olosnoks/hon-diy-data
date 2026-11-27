"""Characterise link drops: python drops.py <drive_id>"""
import sys, statistics as st, collections, datetime
from load import load
did = int(sys.argv[1]) if len(sys.argv) > 1 else 33
reads, flags, epoch = load(did)
T = [r[0] for r in reads]
print(f"drive {did}: {len(reads)} reads, {T[-1]-T[0]:.0f} s, {len(flags)} link flag rows")
# per-block exchange gap distribution (t_done - previous t_done)
gaps = collections.defaultdict(list)
for a, b in zip(reads, reads[1:]):
    gaps[b[1]].append(b[0] - a[0])
print("\nexchange interval (prev t_done -> this t_done), ms:")
for k in sorted(gaps):
    g = sorted(gaps[k]); n = len(g)
    print(f"  {k:10s} n={n:6d} p10={1e3*g[n//10]:6.1f} med={1e3*g[n//2]:6.1f} p90={1e3*g[9*n//10]:6.1f} "
          f"p99={1e3*g[99*n//100]:6.1f} max={1e3*g[-1]:7.1f}  >100ms:{sum(x>0.1 for x in g)}")
# link-down episodes
eps = []
down = None
for t, v in flags:
    if v == 0 and down is None: down = t
    elif v == 1 and down is not None: eps.append((down, t)); down = None
print(f"\nlink-down episodes: {len(eps)}")
import bisect
rows = []
for d, u in eps:
    i = bisect.bisect_left(T, d) - 1          # last good read before the down flag
    j = bisect.bisect_left(T, u)              # first read after link up
    last = reads[i]; nxt = reads[j] if j < len(reads) else None
    prev3 = [reads[k][1] for k in range(max(0, i-3), i+1)]
    silent = (nxt[0] - last[0]) if nxt else None
    rows.append((d, u, u - d, last, nxt, silent, prev3))
durs = sorted(r[2] for r in rows); sil = sorted(r[5] for r in rows if r[5])
print(f"  flag-down duration s: med={st.median(durs):.3f} p90={durs[9*len(durs)//10]:.3f} max={durs[-1]:.3f}")
print(f"  read silence (last good -> first good) s: min={sil[0]:.3f} med={st.median(sil):.3f} p90={sil[9*len(sil)//10]:.3f} max={sil[-1]:.3f}")
print("  hist silence:", collections.Counter(round(s, 1) for s in sil).most_common())
print("  last good block before drop:", collections.Counter(r[3][1] for r in rows).most_common())
print("  block expected next (=after last good, by schedule) unknown; first block after:", collections.Counter(r[4][1] for r in rows if r[4]).most_common())
iv = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
print(f"  interval between drops s: med={st.median(iv):.1f} min={min(iv):.1f} max={max(iv):.1f}")
print("  interval hist (10 s bins):", sorted(collections.Counter(int(x//10)*10 for x in iv).items()))
for r in rows:
    if r[5] and r[5] > 0.5:
        w = datetime.datetime.fromtimestamp(epoch + r[3][0]).strftime('%H:%M:%S.%f')[:-4]
        print(f"  long: last good {w} t={r[3][0]:.2f} {r[3][1]} silence {r[5]:.2f}s flagdown {r[2]:.2f}s prev={r[6]}")
