import sqlite3
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
print([r[0] for r in c.execute("select name from sqlite_master where type in ('table','view') and name not like 'drive_%'")])
for r in c.execute("select id,name,status,started_iso,duration_s,rows,map_version from drives order by id"): print(r)
print(c.execute("select distinct name from streams").fetchall())
