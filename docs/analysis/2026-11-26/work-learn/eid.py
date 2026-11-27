import pickle, numpy as np, sqlite3, datetime
S, drives = pickle.load(open('series.pkl','rb'))
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
t,v,_=S[0x10]
for did,name,st,en,eid in c.execute("select id,name,started_at,ended_at,ecu_id from drives where id>=28"):
    import re; hx=re.findall(r'[0-9A-Fa-f]{2}', eid or ''); b=bytes(int(h,16) for h in hx)
    tgt=(b[0]*256+b[1])/4 if len(b)>=2 else None
    k=np.searchsorted(t,st); raw = v[k:k+5] if k<len(t) and abs(t[min(k,len(t)-1)]-st)<30 else []
    print(did,name,eid,tgt,'ECT raw first reads',list(np.array(raw).astype(int)))
