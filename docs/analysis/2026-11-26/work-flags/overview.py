import pickle, numpy as np, collections
D = pickle.load(open(r'C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-flags/F.pkl','rb'))
allv = collections.defaultdict(collections.Counter)
for did,d in D.items():
    for a,arr in d['ser'].items():
        allv[a].update(arr[:,1].astype(int).tolist())
for a in sorted(allv):
    cnt = allv[a]; vals = sorted(cnt)
    n = sum(cnt.values())
    orv = 0; andv = 255
    for v in vals: orv |= v; andv &= v
    varbits = orv & ~andv
    nb = bin(varbits).count('1')
    if len(vals) <= 12 or nb <= 3:
        print(f"0x{a:02X} n={n} varbits={varbits:08b} ({nb}) vals:", ', '.join(f'{v}:{cnt[v]}' for v in vals[:14]))
    else:
        print(f"0x{a:02X} n={n} varbits={varbits:08b} ({nb}) {len(vals)} distinct (analog)")
