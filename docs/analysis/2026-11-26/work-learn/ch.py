import pickle, datetime, numpy as np
S, drives = pickle.load(open('series.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
def changes(a, lim=400):
    t,v,d = S[a]; i = np.nonzero(np.diff(v))[0]+1
    print(hex(a) if isinstance(a,int) else a, 'start', f(t[0]), v[0], 'n changes', len(i))
    for k in i[:lim]: print('  ', f(t[k]), int(v[k-1]), '->', int(v[k]), 'drive', d[k], 'dt prev', round(t[k]-t[k-1],1))
changes(0x2A)
changes(0x2F)
changes(0x22, 60)
