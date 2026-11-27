"""Gap classes between consecutive good reads, and what precedes drops."""
import sys, bisect, collections, statistics as st
from load import load
did = int(sys.argv[1]) if len(sys.argv) > 1 else 33
reads, flags, epoch = load(did)
T = [r[0] for r in reads]
g = [(b[0]-a[0], a, b) for a, b in zip(reads, reads[1:])]
h = collections.Counter(min(int(x*1000)//20*20, 1600) for x, _, _ in g if x > 0.05)
print("gap histogram (20 ms bins, >50 ms):")
for k in sorted(h): print(f"  {k:5d}-{k+20:<5d} {h[k]:5d} " + "#"*min(80, h[k]//4))
# retries: same block twice in a row with a long gap
same = [x for x, a, b in g if a[1] == b[1] and x > 0.05]
print(f"\nsame block repeated after >50 ms (a retry): {len(same)}, gap med {1e3*st.median(same):.0f} ms")
by = collections.Counter(b[1] for x, a, b in g if a[1] == b[1] and x > 0.05)
n = collections.Counter(r[1] for r in reads)
print("retry rate per block (retried / reads):")
for k in sorted(n): print(f"  {k:10s} {by[k]:4d}/{n[k]:6d} = {100*by[k]/n[k]:.2f} %")
# downs
downs = [t for t, v in flags if v == 0]
first = []
for t in downs:
    if not first or t - first[-1] > 2.5: first.append(t)
pre_retry = []; pre_int = []
retry_t = [b[0] for x, a, b in g if a[1] == b[1] and x > 0.05]
for t in first:
    i = bisect.bisect_left(T, t) - 1
    w = [T[k+1]-T[k] for k in range(max(0,i-60), i)]
    pre_int.append(st.median(w) if w else 0)
    pre_retry.append(sum(1 for r in retry_t if t-5 <= r < t-0.5))
base_rate = len(retry_t) / (T[-1]-T[0]) * 4.5
print(f"\nretries in the 5 s before a drop: mean {st.mean(pre_retry):.2f} (baseline for 4.5 s {base_rate:.2f})")
print(f"median exchange interval in the 60 reads before a drop: {1e3*st.median(pre_int):.1f} ms "
      f"(drive median {1e3*st.median([x for x,_,_ in g]):.1f})")
