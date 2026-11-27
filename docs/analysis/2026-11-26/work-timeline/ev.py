import sqlite3,json,collections;c=sqlite3.connect('hondalog.db')
E=1795753477.284756
import datetime
def w(t): return datetime.datetime.fromtimestamp(E+t).strftime('%H:%M:%S.%f')[:-4]
for d in (38,39):
  print('=== drive',d)
  for t,k,tx,da in c.execute("select t,kind,text,data from events where drive_id=? order by t",(d,)):
    print(w(t),round(t,2),k,tx[:90],(da or '')[:160])
  print('--- link stream')
  for r in c.execute("select t,v0,v1,v2,v3 from samples where drive_id=? and stream_id=2 order by t",(d,)):
    pass
  rows=c.execute("select t,v0 from samples where drive_id=? and stream_id=2 order by t",(d,)).fetchall()
  print(collections.Counter(r[1] for r in rows)); print([(w(t),v) for t,v in rows[:10]], [(w(t),v) for t,v in rows[-5:]])
