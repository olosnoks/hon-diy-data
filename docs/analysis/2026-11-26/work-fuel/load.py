"""Extract drives from the DB copy into npz: per-channel raw (t,v) plus a common grid on the rpm stream."""
import sqlite3, sys, numpy as np, os
W = os.path.dirname(os.path.abspath(__file__))
c = sqlite3.connect(os.path.join(W, 'hondalog.db'))
WANT = ['rpm','vss','map','baro','tps','o2','stft','ltft','inj','ign','ect','iat','closed_loop','fuel_status',
        'load_ecu','o2_2','batt','eld','iacv','idle_target','ac_clutch','brake','fan','gps_speed','vtec','alt_fr']
AGE = dict(rpm=1.5, vss=1.5, map=1.5, baro=5, tps=1.5, o2=1.5, stft=2.5, ltft=2.5, inj=2.5, ign=2.5)
def load(did):
    name = c.execute('select name from drives where id=?', (did,)).fetchone()[0]
    out = {}
    for ch in WANT:
        rows = c.execute('''select c.stream_id,c.part,c.slot,st.rows from channels c join streams st on st.drive_id=c.drive_id and st.id=c.stream_id
                             where c.drive_id=? and c.name=? order by st.rows desc''', (did, ch)).fetchall()
        if not rows: continue
        sid, part, slot, _ = rows[0]
        d = np.array(c.execute(f'select t,v{slot} from samples where drive_id=? and stream_id=? and part=? order by t,n',
                               (did, sid, part)).fetchall(), float)
        if len(d) == 0: continue
        d = d[~np.isnan(d[:,1])]
        out['raw_'+ch] = d
    # grid: rpm times
    tg = out['raw_rpm'][:,0]
    out['t'] = tg
    for ch in WANT:
        if 'raw_'+ch not in out: continue
        d = out['raw_'+ch]
        i = np.searchsorted(d[:,0], tg, side='right') - 1
        ok = i >= 0
        v = np.full(len(tg), np.nan)
        v[ok] = d[i[ok],1]
        age = np.full(len(tg), np.inf); age[ok] = tg[ok] - d[i[ok],0]
        v[age > AGE.get(ch, 6.0)] = np.nan
        out[ch] = v
    np.savez_compressed(os.path.join(W, f'd{did}.npz'), name=name, **out)
    print(did, name, len(tg), 'ch', [k for k in out if not k.startswith('raw_')])
for did in map(int, sys.argv[1:]): load(did)
