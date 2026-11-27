import sqlite3;c=sqlite3.connect('hondalog.db')
for n,s in c.execute("select name,sql from sqlite_master where type in ('table','view')"): print(n); print((s or '')[:2500]); print()
