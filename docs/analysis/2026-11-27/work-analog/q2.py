import sqlite3,json
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
for r in c.execute("select id,name,status,started_iso,duration_s,rows,read_plan,epoch_unix from drives where started_iso>='2026-11-26' order by id"):
    print(r[:6]); print('   plan',r[6])
print()
for r in c.execute("select drive_id,id,name,source,storage,raw_len,addr,rows,t_first,t_last from streams where drive_id in (select id from drives where started_iso>='2026-11-26T20') order by drive_id,id"): print(r)
