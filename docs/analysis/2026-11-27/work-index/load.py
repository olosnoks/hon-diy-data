"""Load 0x27 and 0x3C raw reads plus fast channels from the DB copy; save D.pkl."""
import sqlite3, pickle, numpy as np
c = sqlite3.connect('hondalog.db')
drives = [r for r in c.execute("select id,name from drives order by id")]
FAST = ['rpm','map','tps','vss','ect','iacv','ign','inj','idle_target','baro','iat']
D = {}
for did, name in drives:
    st = list(c.execute("select id,name,addr,raw_len from streams where drive_id=? and addr is not null", (did,)))
    rec = {}
    for addr_want, key in [(0x27,'b27'),(0x3C,'b3c'),(0x28,'b28'),(0x08,'b08'),(0x0B,'b0b'),(0x0C,'b0c'),(0x0D,'b0d')]:
        ts, vs, lat = [], [], []
        for sid, sn, a, L in st:
            if a <= addr_want < a + L:
                for t, raw, l in c.execute("select t,raw,latency_us from samples where drive_id=? and stream_id=? and part=0 and raw is not null", (did, sid)):
                    if raw is not None and len(raw) > addr_want - a:
                        ts.append(t); vs.append(raw[addr_want - a]); lat.append((l or 0)/1e6)
        if ts:
            o = np.argsort(ts)
            rec[key] = (np.array(ts)[o], np.array(vs)[o], np.array(lat)[o])
    if 'b27' not in rec and 'b3c' not in rec:
        continue
    for ch in FAST:
        rows = list(c.execute("select t,value from readings where drive_id=? and channel=? and value is not null order by t", (did, ch)))
        if rows:
            a = np.array(rows, float)
            rec[ch] = (a[:,0], a[:,1])
    D[name] = rec
    print(name, {k: len(v[0]) for k, v in rec.items()})
pickle.dump(D, open('D.pkl','wb'))
