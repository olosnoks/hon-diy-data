import sqlite3,json
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
rows=c.execute("select s.drive_id,d.name,s.name,s.addr,s.raw_len,s.rows from streams s join drives d on d.id=s.drive_id where s.addr is not null order by s.drive_id").fetchall()
for r in rows:
    a,l=r[3],r[4]
    if a<=0x3A and a+l>0x32 or (a<=0x9F and a+l>0x80): print(r)
# raw sample format
print(c.execute("select t,raw,v0 from samples where drive_id=33 and stream_id=4 limit 3").fetchall())
