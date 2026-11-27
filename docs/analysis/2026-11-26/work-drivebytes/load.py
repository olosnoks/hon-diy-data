"""Build per-drive tables of raw ECU bytes from the DB copy (read only).

Outputs D.pkl: dict drive_name -> {'fast': DataFrame (00+3 rate, other fast
blocks merged asof), 'slow': DataFrame (03+15 rate, everything merged asof)}.
Columns bXX = raw byte at address XX (hex, lower case).
"""
import sqlite3, pickle
import numpy as np, pandas as pd

DB = 'hondalog.db'
DRIVES = ['drive_2026_11_26_2224', 'drive_2026_11_26_2251']

c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)


def block_frame(did, sid, addr):
    rows = c.execute('select t, raw from samples where drive_id=? and stream_id=? and part=0 '
                     'and raw is not null order by t', (did, sid)).fetchall()
    if not rows:
        return None
    n = max(len(r[1]) for r in rows)
    t = np.array([r[0] for r in rows])
    arr = np.full((len(rows), n), np.nan)
    for i, (_, raw) in enumerate(rows):
        arr[i, :len(raw)] = list(raw)
    cols = [f'b{addr + k:02x}' for k in range(n)]
    df = pd.DataFrame(arr, columns=cols)
    df.insert(0, 't', t)
    return df


out = {}
for name in DRIVES:
    did, epoch = c.execute('select id, coalesce(epoch_unix, started_at) from drives where name=?',
                           (name,)).fetchone()
    blocks = {}
    for sid, sname, addr, rl in c.execute(
            "select id, name, addr, raw_len from streams where drive_id=? and source='ecu' and addr is not null",
            (did,)).fetchall():
        f = block_frame(did, sid, addr)
        if f is not None and len(f) > 200:
            blocks[sname] = f
    # gps speed
    g = pd.DataFrame(c.execute(
        "select g.t, g.v2 from samples g join streams st on st.drive_id=g.drive_id and st.id=g.stream_id "
        "where g.drive_id=? and st.name='gps' and g.part=0 order by g.t", (did,)).fetchall(),
        columns=['t', 'gps_speed'])
    print(name, {k: len(v) for k, v in blocks.items()})

    def merge(base, others, tol):
        df = base.sort_values('t').reset_index(drop=True)
        for o in others:
            o = o.sort_values('t')
            dup = [col for col in o.columns if col != 't' and col in df.columns]
            o = o.drop(columns=dup)
            o = o.rename(columns={'t': 't_' + o.columns[1]})
            o['t'] = o[o.columns[0]]
            df = pd.merge_asof(df, o, on='t', direction='nearest', tolerance=tol)
        return df

    fastb = blocks['ecu/00+3']
    fast_others = [blocks[k] for k in ('ecu/12+4', 'ecu/20+7', 'ecu/76+2') if k in blocks]
    fast = merge(fastb, fast_others, 0.3)
    slowb = blocks['ecu/03+15']
    slow_others = [blocks[k] for k in blocks if k not in ('ecu/03+15',)]
    slow = merge(slowb, slow_others + [g], 1.0)
    for df in (fast, slow):
        df['rpm'] = (df['b00'] * 256 + df['b01']) / 4
        df['vss'] = df['b02']
        df['map'] = df['b12'] * 0.716 - 5
        df['tps_raw'] = df['b14']
        df['inj'] = (df['b24'] * 256 + df['b25']) / 250
        df['ign'] = (df['b26'] - 46) / 4
        df['wall'] = pd.to_datetime(epoch + df['t'], unit='s') + pd.Timedelta(seconds=(__import__('datetime').datetime.fromtimestamp(epoch) - __import__('datetime').datetime.fromtimestamp(epoch, __import__('datetime').UTC).replace(tzinfo=None)).total_seconds())
    if 'b76' in slow:
        slow['idle_target'] = (slow['b76'] * 256 + slow['b77']) / 4
    if 'b76' in fast:
        fast['idle_target'] = (fast['b76'] * 256 + fast['b77']) / 4
    out[name] = {'fast': fast, 'slow': slow, 'epoch': epoch, 'did': did}

# sanity: compare rpm decode with the app's decoded value
did = out[DRIVES[1]]['did']
v = c.execute("select s.t, s.v0 from samples s join streams st on st.drive_id=s.drive_id and st.id=s.stream_id "
              "where s.drive_id=? and st.name='ecu/00+3' and s.part=0 limit 5", (did,)).fetchall()
print('app rpm', v)
print('mine   ', out[DRIVES[1]]['fast'][['t', 'rpm', 'vss']].head().values.tolist())
pickle.dump(out, open('D.pkl', 'wb'))
