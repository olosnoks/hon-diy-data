import sqlite3,sys
c=sqlite3.connect('hondalog.db')
cur=c.execute(sys.argv[1])
print('\t'.join(d[0] for d in cur.description))
for r in cur: print('\t'.join(str(x) for x in r))
