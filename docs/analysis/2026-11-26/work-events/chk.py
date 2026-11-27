import sqlite3, datetime
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
for r in c.execute("select capture_id,name,drive,wall_time,reason,t_trigger from stalls where capture_id>=10"): print(r)
for r in c.execute("select id,name,status,end_reason,duration_s,t_last,heartbeat_at from drives where id>=34"): print(r)
for r in c.execute("select drive_id,round(t,1),kind,text from events where drive_id>=34"): print(r)
print(list(c.execute("select max(t) from samples where drive_id=34")))
print(list(c.execute("select t,v0 from samples where drive_id=34 and stream_id=2 order by t desc limit 6")))
