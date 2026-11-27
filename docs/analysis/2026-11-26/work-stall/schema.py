import sqlite3,sys
c=sqlite3.connect('hondalog.db')
for t,n,s in c.execute("select type,name,sql from sqlite_master where type in ('table','view') order by type,name"):
    print(t,n,'|',(s or '')[:600].replace('\n',' '))
