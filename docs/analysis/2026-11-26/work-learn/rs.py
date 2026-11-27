import pickle, datetime, numpy as np, sys
S, drives = pickle.load(open('series.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S.%f')[:-4]
d0 = datetime.datetime.fromtimestamp(S[0x10][0][0]).date()
for h in sys.argv[1:]:
    c = datetime.datetime.combine(d0, datetime.time.fromisoformat(h)).timestamp()
    t,v,_=S[0x10]; t2,v2,_=S[0x2F]; t3,v3,_=S[0x11]
    m=(t>c-12)&(t<c+4)
    print('---', h)
    print(' '.join('%s:%d/%d' % (f(a)[3:], b, v2[np.searchsorted(t2,a,side='right')-1]) for a,b in zip(t[m],v[m])))
