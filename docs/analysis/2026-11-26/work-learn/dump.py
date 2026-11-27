import pickle, datetime, sys, numpy as np
G = pickle.load(open('grid.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
def ts(hms):
    d = datetime.datetime.fromtimestamp(G['t'][0]).date()
    return datetime.datetime.combine(d, datetime.time.fromisoformat(hms)).timestamp()
a,b,step = ts(sys.argv[1]), ts(sys.argv[2]), int(sys.argv[3]) if len(sys.argv)>3 else 1
m = np.nonzero((G['t']>=a)&(G['t']<=b))[0][::step]
cols = ['rpm','tgt',0x29,0x28,0x27,0x2A,0x2F,0x10,0x11,'stft',0x22,'eld',0x08,0x0A,0x0B,0x0C,0x0F,0x26,0x12]
print('time    '+' '.join('%6s'%(c if isinstance(c,str) else hex(c)) for c in cols))
for k in m: print(f(G['t'][k]), ' '.join('%6.0f'%G[c][k] if not np.isnan(G[c][k]) else '   nan' for c in cols))
