"""Read-only loader: raw bytes per address from samples.raw of the K-line block streams."""
import sqlite3, numpy as np, json
DB='file:hondalog.db?mode=ro'
def drives(c):
    return c.execute("select id,name,started_iso,epoch_unix from drives where started_iso>='2026-11-26T20:30' order by id").fetchall()
def load(c, did):
    """returns dict addr -> (t array, value array, stream name array)"""
    out={}
    for sid,name,a,l in c.execute("select id,name,addr,raw_len from streams where drive_id=? and addr is not null",(did,)).fetchall():
        rows=c.execute("select t,raw from samples where drive_id=? and stream_id=? and raw is not null order by t",(did,sid)).fetchall()
        for t,raw in rows:
            if raw is None or len(raw)!=l: continue
            for i,b in enumerate(raw):
                out.setdefault(a+i,[]).append((t,b,name))
    res={}
    for a,v in out.items():
        v.sort()
        res[a]=(np.array([x[0] for x in v]),np.array([x[1] for x in v]),np.array([x[2] for x in v]))
    return res
def at(res,a,t):
    """nearest-in-time value of addr a at times t"""
    ts,vs,_=res[a]
    i=np.searchsorted(ts,t).clip(1,len(ts)-1)
    j=np.where(np.abs(ts[i-1]-t)<np.abs(ts[i]-t),i-1,i)
    return vs[j].astype(float), np.abs(ts[j]-t)
ECT=lambda x: 155.04149-3.0414878*x+0.03952185*x**2-0.00029383913*x**3+0.0000010792568*x**4-0.0000000015618437*x**5
