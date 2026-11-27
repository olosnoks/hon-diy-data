import sqlite3, numpy as np
DB=r'file:C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-stats/hondalog.db?mode=ro'
def load(drive_id):
    c=sqlite3.connect(DB,uri=True)
    rows=c.execute("select t,addr,raw from ecu_reads where drive_id=? and addr is not null order by t",(drive_id,)).fetchall()
    per={}  # addr -> list of (t,val)
    for t,a,raw in rows:
        for i,b in enumerate(raw):
            per.setdefault(a+i,[]).append((t,b))
    per={a:np.array(v,float) for a,v in per.items()}
    return per
def grid(per, t0=None, t1=None):
    tmin=min(v[0,0] for v in per.values()); tmax=max(v[-1,0] for v in per.values())
    secs=np.arange(int(np.floor(tmin)), int(np.ceil(tmax))+1)
    out={}
    for a,v in per.items():
        idx=np.floor(v[:,0]).astype(int)-secs[0]
        s=np.zeros(len(secs)); n=np.zeros(len(secs))
        np.add.at(s,idx,v[:,1]); np.add.at(n,idx,1)
        with np.errstate(invalid='ignore'): m=s/n
        m[n==0]=np.nan
        out[a]=m
    return secs,out
def ffill(x):
    x=x.copy(); last=np.nan
    for i in range(len(x)):
        if np.isnan(x[i]): x[i]=last
        else: last=x[i]
    return x

def known(g):
    k={}
    k['rpm']=(g[0]*256+g[1])/4; k['map']=g[0x12]; k['tps']=g[0x14]; k['o2']=g[0x15]
    k['ect']=g[0x10]; k['iat']=g[0x11]; k['batt']=g[0x17]; k['alt_fr']=g[0x18]; k['eld']=g[0x19]
    k['stft']=g[0x20]; k['ltft']=g[0x22]; k['inj']=g[0x24]*256+g[0x25]; k['ign']=g[0x26]
    k['b27']=g[0x27]; k['iacv28']=g[0x28]; k['knock3c']=g[0x3C]; k['b08']=g[0x08]
    return k
