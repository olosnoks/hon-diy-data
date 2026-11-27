import sqlite3,sys
c=sqlite3.connect('hondalog.db')
for r in c.execute("select type,name,sql from sqlite_master"):
    if r[0] in('table','view'): print(r[0],r[1], (r[2] or '')[:300].replace('\n',' '))
