import sqlite3,json;c=sqlite3.connect('hondalog.db')
for r in c.execute("select id,name,status,end_reason,started_iso,ended_iso,duration_s,epoch_unix,t_first,t_last,rows,link_stats from drives where id in (38,39)"): print(r)
for d in (38,39):
  print('--- streams',d)
  for r in c.execute("select id,name,source,storage,raw_len,addr,rows,t_first,t_last from streams where drive_id=? order by id",(d,)): print(r)
  print('--- channels')
  print([ (r[0],r[1],r[2],r[3],r[4]) for r in c.execute("select stream_id,part,slot,name,units from channels where drive_id=? order by stream_id,position",(d,))])
  print('--- events kinds')
  for r in c.execute("select kind,count(*),min(t),max(t) from events where drive_id=? group by kind",(d,)): print(r)
for r in c.execute("select * from captures limit 0"): pass
print([x[1] for x in c.execute("pragma table_info(captures)")])
for r in c.execute("select * from captures where name like '%20261126_22%' or name like '%20261126_23%'"): print(str(r)[:300])
