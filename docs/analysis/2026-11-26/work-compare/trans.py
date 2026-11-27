import numpy as np, lib, datetime
c = lib.con()
def tt(s): return datetime.datetime.strptime('2026-11-25 ' + s, '%Y-%m-%d %H:%M:%S').timestamp()
LM = ['18:45:26', '19:04:26', '19:19:44', '19:41:20', '20:07:59', '20:25:53', '20:36:00', '21:28:35', '22:27:55']
ML = ['20:00:48', '20:29:41', '21:26:53', '21:53:44', '22:07:32', '22:41:07']
cache = {}
def get(did):
    if did not in cache:
        ep = c.execute('select coalesce(epoch_unix, started_at) from drives where id=?', (did,)).fetchone()[0]
        out = {}
        for sid, addr, rl in c.execute('select id,addr,raw_len from streams where drive_id=? and addr is not null', (did,)).fetchall():
            rows = c.execute('select t, raw from samples where drive_id=? and stream_id=? and part=0 and raw is not null order by t', (did, sid)).fetchall()
            out[addr] = (np.array([r[0] + ep for r in rows]), [bytes(r[1]) for r in rows], rl)
        cache[did] = out
    return cache[did]
D = c.execute('select id, epoch_unix, t_first, t_last from drives').fetchall()
def did_at(t):
    for i, ep, a, b in D:
        if ep and ep + a <= t <= ep + b and i >= 6: return i
for lab, L in (('L->M', LM), ('M->L', ML)):
    for s in L:
        t0 = tt(s); did = did_at(t0)
        g = get(did)
        print('==', lab, s, 'd', did)
        for dt in (-20, -8, -3, 0, 3, 8, 20):
            parts = []
            for addr in sorted(g):
                ts, raws, rl = g[addr]
                i = np.searchsorted(ts, t0 + dt) - 1
                if i < 0: continue
                rw = raws[i]
                if addr == 0:
                    parts.append('rpm%4d' % ((rw[0] << 8 | rw[1]) / 4 if len(rw) > 1 else 0))
                elif addr in (8, 23, 32, 39, 60):
                    parts.append('%02X:' % addr + ' '.join('%02X' % b for b in rw))
            print('  %+3d %s' % (dt, '  '.join(parts)))
