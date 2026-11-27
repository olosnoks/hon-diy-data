import sqlite3, numpy as np
DB='file:hondalog.db?mode=ro'
def con(): return sqlite3.connect(DB, uri=True)
def honda(drive_ids=None):
    """{addr: (unix, val)} over the given drives, from every block read."""
    c=con(); acc={}
    q='select drive_id, unix_time, addr, raw from ecu_reads where addr is not null'
    for d,u,a,raw in c.execute(q):
        if drive_ids and d not in drive_ids: continue
        raw=bytes(raw)
        for i,b in enumerate(raw):
            acc.setdefault(a+i,[]).append((u,b,d))
    out={}
    for a,l in acc.items():
        l.sort(); arr=np.array(l); out[a]=arr  # columns unix,val,drive
    return out
def obd(pid):
    c=con()
    r=np.array(c.execute("select s.t+coalesce(d.epoch_unix,d.started_at), s.v0, s.drive_id from samples s join streams st on st.drive_id=s.drive_id and st.id=s.stream_id join drives d on d.id=s.drive_id where st.name=? and s.part=0 order by 1",(pid,)).fetchall())
    return r
def at(arr, tq):
    t=arr[:,0]; v=arr[:,1]
    i=np.searchsorted(t,tq,side='right')-1
    return np.where(i>=0, v[np.clip(i,0,None)], np.nan), np.where(i>=0, tq-t[np.clip(i,0,None)], np.inf)
def ectc(x):
    k=[155.04149,-3.0414878,0.03952185,-0.00029383913,0.0000010792568,-0.0000000015618437]
    x=np.asarray(x,float); return sum(c*x**i for i,c in enumerate(k))
