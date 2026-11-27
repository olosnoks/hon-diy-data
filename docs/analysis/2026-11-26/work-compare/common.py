import pickle, numpy as np, lib, datetime
T = pickle.load(open('t1hz.pkl', 'rb'))
def day(t): return datetime.datetime.fromtimestamp(t).strftime('%m-%d')
DAY = np.array([day(x) for x in T['t']])
def sel(**kw):
    m = np.ones(len(T['t']), bool)
    return m
def runs(mask):
    """list of (i0, i1) inclusive runs of True"""
    m = np.r_[False, mask, False].astype(int)
    d = np.diff(m)
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1))
