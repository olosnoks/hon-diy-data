import pickle, numpy as np
S, drives = pickle.load(open('series.pkl','rb'))
for a in (0x08,0x09,0x0A,0x0B,0x0C,0x0F,0x9A,0x98,0x99,0x9B):
    if a in S:
        t,v,d=S[a]; u,c=np.unique(v,return_counts=True); print(hex(a), dict(zip(u.astype(int),c)))
