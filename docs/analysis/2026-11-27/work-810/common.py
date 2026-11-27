import pickle, numpy as np, datetime
T = pickle.load(open('T.pkl', 'rb'))
FAST = pickle.load(open('fast.pkl', 'rb'))
def wall(t): return datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
def wday(t): return datetime.datetime.fromtimestamp(t).strftime('%m-%d %H:%M:%S')
DAY = np.array([datetime.datetime.fromtimestamp(x).strftime('%m-%d') for x in T['t']])
N = len(T['t'])
def bit(name, b):
    v = T[name]; return np.where(np.isnan(v), np.nan, (np.nan_to_num(v).astype(int) >> b) & 1)
T['ac_clutch'] = bit('b0b', 1); T['ac_sw'] = bit('b08', 1); T['brake'] = bit('b08', 3); T['x0c0'] = bit('b0c', 0)
T['o2heat_b6'] = bit('b0b', 6)
idle = (np.nan_to_num(T['vss']) < 0.5) & (T['tps'] < 9) & (T['rpm'] > 550) & (T['rpm'] < 1300) & (np.nan_to_num(T['ac_clutch']) == 0)
def runs(mask):
    m = np.r_[False, mask, False].astype(int); d = np.diff(m)
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1))
