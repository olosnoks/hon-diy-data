import sqlite3, numpy as np, pandas as pd
DB='file:/home/claude/analysis/hondalog.db?mode=ro'
def conn(): return sqlite3.connect(DB, uri=True)
def drives():
    return pd.read_sql('select id,name,status,end_reason,epoch_unix,started_at,t_first,t_last,read_plan,link_stats,meta from drives', conn())
def ecu_bytes(drive_ids=None):
    c=conn()
    q='''select s.drive_id d, st.name block, st.addr addr, s.t t, s.n n, s.raw raw, s.latency_us lat, d.epoch_unix ep
         from samples s join streams st on st.drive_id=s.drive_id and st.id=s.stream_id join drives d on d.id=s.drive_id
         where s.part=0 and s.raw is not null and st.name like 'ecu/%' and s.drive_id>=6'''
    rows=c.execute(q).fetchall()
    out=[]
    for d,blk,a,t,n,raw,lat,ep in rows:
        for i,b in enumerate(raw):
            out.append((d,blk,a+i,t,ep+t,b,lat))
    return pd.DataFrame(out,columns=['d','block','addr','t','unix','v','lat'])
def reads(drive_ids=None):
    c=conn()
    q='''select s.drive_id d, st.name block, s.t t, length(s.raw) L, s.latency_us lat, d.epoch_unix ep, hex(s.raw) hx
         from samples s join streams st on st.drive_id=s.drive_id and st.id=s.stream_id join drives d on d.id=s.drive_id
         where s.part=0 and st.name like 'ecu/%' '''
    df=pd.read_sql(q,c); df['unix']=df.ep+df.t; return df
def obd2():
    c=conn()
    q='''select s.drive_id d, st.name pid, s.t t, s.raw raw, s.v0 v0, d.epoch_unix ep from samples s
         join streams st on st.drive_id=s.drive_id and st.id=s.stream_id join drives d on d.id=s.drive_id
         where st.name like 'obd2/%' and s.part=0'''
    df=pd.read_sql(q,c); df['unix']=df.ep+df.t; return df
