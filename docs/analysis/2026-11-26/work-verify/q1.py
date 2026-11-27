import sqlite3
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
for n in ('ecu_reads','gps_track','readings','blackbox_1s'):
    print(c.execute("select sql from sqlite_master where name=?",(n,)).fetchone()[0][:1500]); print('---')
for r in c.execute("select st.name, s.drive_id, count(*), min(s.v0), max(s.v0), hex(min(s.raw)) from samples s join streams st on st.drive_id=s.drive_id and st.id=s.stream_id where st.name like 'obd2/%' group by st.name, s.drive_id"): print(r)
