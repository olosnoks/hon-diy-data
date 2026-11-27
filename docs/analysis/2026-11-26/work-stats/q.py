import sqlite3,sys
c=sqlite3.connect(r'file:C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-stats/hondalog.db?mode=ro',uri=True)
for r in c.execute(sys.argv[1]): print(r)
