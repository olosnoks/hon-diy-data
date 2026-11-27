import warnings; warnings.filterwarnings("ignore")
import pickle, numpy as np, pandas as pd, datetime as dt
L = pickle.load(open('L.pkl', 'rb'))
def ectc(x):
    p = [155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437]
    return sum(c * x**i for i, c in enumerate(p))
def bit(s, b): return pd.Series((s.fillna(0).to_numpy().astype(int) >> b) & 1, index=s.index).where(s.notna())
def decode(df):
    df = df.copy()
    g = lambda k: df[k] if k in df else pd.Series(np.nan, index=df.index)
    df['rpm'] = (g('b00') * 256 + g('b01')) / 4
    df['vss'] = g('b02'); df['kpa'] = g('b12') * 0.716 - 5; df['tps'] = g('b14') / 2.55
    df['o2'] = g('b15') / 51.3; df['ect'] = ectc(g('b10')); df['ect_raw'] = g('b10'); df['iat'] = ectc(g('b11'))
    df['batt'] = g('b17') / 10.45; df['altfr'] = g('b18') / 2.55; df['eld'] = 77.06 - g('b19') / 2.5371
    df['stft'] = (g('b20') / 128 - 1) * 100; df['ltft'] = (g('b22') / 128 - 1) * 100
    df['inj'] = (g('b24') * 256 + g('b25')) / 250; df['ign'] = (g('b26') - 46) / 4
    df['iacv'] = g('b28'); df['iac_cmd'] = g('b29'); df['x2a'] = g('b2a'); df['x2f'] = g('b2f')
    df['fs'] = g('b9a'); df['load'] = g('b9c'); df['o2_2'] = g('ba0') / 51.3
    df['tgt'] = (g('b76') * 256 + g('b77')) / 4
    df['cl'] = bit(g('b0f'), 0); df['b6'] = bit(g('b0b'), 6); df['c0'] = bit(g('b0c'), 0); df['c3'] = bit(g('b0c'), 3)
    df['vtec'] = bit(g('b0a'), 2); df['starter'] = bit(g('b08'), 0); df['brake'] = bit(g('b08'), 3); df['ac'] = bit(g('b08'), 1)
    df['wall'] = pd.to_datetime(df['t'], unit='s', utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None) if False else pd.to_datetime(df['t'] + LOCALOFF, unit='s')
    return df
_e = 1795755069.4
LOCALOFF = (dt.datetime.fromtimestamp(_e) - dt.datetime.fromtimestamp(_e, dt.UTC).replace(tzinfo=None)).total_seconds()
def slow_all(ids=None):
    fs = []
    for did, d in L.items():
        if d['slow'] is None or (ids and did not in ids): continue
        s = d['slow'].copy(); s['did'] = did; fs.append(s)
    df = pd.concat(fs, ignore_index=True)
    return decode(df.copy())
def fast(did):
    B = L[did]['blocks']
    def fr(name):
        ts, a, ad = B[name]; f = pd.DataFrame({'t': ts})
        for k in range(a.shape[1]): f[f'b{ad+k:02x}'] = a[:, k]
        return f.sort_values('t')
    f = fr('ecu/00+3')
    for nm in ['ecu/12+4', 'ecu/20+7', 'ecu/76+2']:
        if nm in B:
            o = fr(nm); o = o.drop(columns=[c for c in o.columns if c != 't' and c in f.columns])
            f = pd.merge_asof(f, o, on='t', direction='nearest', tolerance=0.3)
    return decode(f)
def hms(t): return (dt.datetime.utcfromtimestamp(t + LOCALOFF)).strftime('%m-%d %H:%M:%S.%f')[:-5]
