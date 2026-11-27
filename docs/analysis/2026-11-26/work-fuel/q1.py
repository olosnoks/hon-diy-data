import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
for d in (38,39):
  print('drive',d)
  for r in c.execute("select st.id,st.name,st.addr,st.rows,st.storage from streams st where drive_id=?",(d,)): print(' S',r)
  for r in c.execute("select stream_id,part,slot,name,units,source from channels where drive_id=? order by stream_id,position",(d,)): print('  C',r)
for r in c.execute("select drive_id,t,kind,text from events where drive_id in (38,39) limit 60"): print(r)
