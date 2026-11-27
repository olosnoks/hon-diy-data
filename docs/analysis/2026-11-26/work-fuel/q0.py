import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
for r in c.execute("select type,name,substr(sql,1,700) from sqlite_master where name not like 'drive_%' and type in ('table','view')"): print(r)
for r in c.execute('select id,name,status,duration_s,rows,started_iso,end_reason from drives'): print(r)
print(c.execute("select sql from sqlite_master where name='drive_2026_11_26_2251'").fetchone()[0][:3000])
