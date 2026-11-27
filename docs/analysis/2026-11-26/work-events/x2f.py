import sqlite3, datetime
c=sqlite3.connect('file:db2/hondalog.db?mode=ro',uri=True)
print(list(c.execute("select drive_id,count(*),min(t),max(t) from ecu_reads where addr=8 and substr(raw,6,1)=x'DD' group by drive_id")))
print(list(c.execute("select id,name from drives where id in (select distinct drive_id from ecu_reads where addr=8 and substr(raw,6,1)=x'DD')")))
ep=c.execute("select epoch_unix from drives where id=34").fetchone()[0]
rows=list(c.execute("select t,raw from ecu_reads where drive_id=34 and addr=39 order by t"))
prev=None
for t,raw in rows:
    raw=bytes(raw)
    if raw[8]!=0 or (prev is not None and prev[8]!=0):
        print('%.1f %s x27..x36: %s'%(t,datetime.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S'),' '.join('%02X'%b for b in raw)))
    prev=raw
print('drive34 last t',rows[-1][0])
