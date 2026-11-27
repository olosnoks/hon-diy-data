"""Build per-drive byte series from the DB copy (read-only). Output L.pkl:
{drive_id: {'name', 'blocks': {block: (t_unix array, bytes 2D array, addr)}, 'slow': DataFrame}}
slow = anchored on reads of the block that holds 0x0B; every other address merged nearest
(fast addrs within 0.25 s, slow within 1.2 s). Columns bXX (hex lower)."""
import sqlite3, pickle, numpy as np, pandas as pd
c = sqlite3.connect('file:hondalog.db?mode=ro', uri=True)
drives = c.execute('select id,name,coalesce(epoch_unix,started_at) from drives order by id').fetchall()
FAST = set([0,1,2,0x12,0x13,0x14,0x15,0x20,0x21,0x22,0x23,0x24,0x25,0x26,0x76,0x77])
out = {}
for did, name, ep in drives:
    if did == 1: continue
    rows = c.execute('select st.name, st.addr, s.t, s.raw from samples s join streams st on st.drive_id=s.drive_id and st.id=s.stream_id '
                     'where s.drive_id=? and s.part=0 and s.raw is not null and st.source="ecu" and st.addr is not null order by st.name, s.t', (did,)).fetchall()
    if not rows: continue
    blocks = {}
    for bname, addr, t, raw in rows:
        blocks.setdefault(bname, [addr, [], []])
        blocks[bname][1].append(ep + t); blocks[bname][2].append(raw)
    B = {}
    for bname, (addr, ts, raws) in blocks.items():
        n = int(bname.split('+')[1]) if '+' in bname else max(len(r) for r in raws)
        arr = np.full((len(raws), n), np.nan)
        for i, r in enumerate(raws):
            m = min(len(r), n); arr[i, :m] = list(r[:m])
        # keep only full-length replies
        ok = np.array([len(r) >= n for r in raws])
        B[bname] = (np.array(ts)[ok], arr[ok], addr)
    anchor = [k for k, (ts, a, ad) in B.items() if ad <= 0x0B < ad + a.shape[1]]
    if not anchor:
        out[did] = {'name': name, 'blocks': B, 'slow': None}; print(did, name, 'no flag block', list(B)); continue
    anchor = max(anchor, key=lambda k: len(B[k][0]))
    ts, a, ad = B[anchor]
    slow = pd.DataFrame({'t': ts})
    for k in range(a.shape[1]): slow[f'b{ad+k:02x}'] = a[:, k]
    # per address: best (most frequent) source
    src = {}
    for bname, (bts, arr, bad) in B.items():
        if bname == anchor: continue
        for k in range(arr.shape[1]):
            A = bad + k
            if f'b{A:02x}' in slow.columns: continue
            if A not in src or len(bts) > len(src[A][0]): src[A] = (bts, arr[:, k])
    for A, (bts, v) in sorted(src.items()):
        o = pd.DataFrame({'t': bts, f'b{A:02x}': v}).sort_values('t')
        tol = 0.25 if A in FAST else 1.2
        slow = pd.merge_asof(slow.sort_values('t'), o, on='t', direction='nearest', tolerance=tol)
    out[did] = {'name': name, 'blocks': B, 'slow': slow}
    print(did, name, anchor, len(slow), sorted(B))
pickle.dump(out, open('L.pkl', 'wb'))
