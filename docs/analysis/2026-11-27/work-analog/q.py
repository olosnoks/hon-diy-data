import sqlite3,sys
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
for r in c.execute("select name,sql from sqlite_master where type in ('table','index')"): print(r[0]); print(r[1])
for t in [r[0] for r in c.execute("select name from sqlite_master where type='table'")]:
    print(t, c.execute(f"select count(*) from {t}").fetchone())
