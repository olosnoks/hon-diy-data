import sqlite3, numpy as np, os
W=os.path.dirname(os.path.abspath(__file__)); c=sqlite3.connect(f'{W}/hondalog.db')
for did in (38,39):
  rows=c.execute("select t,raw from ecu_reads where drive_id=? and block like '%03+15'",(did,)).fetchall()
  t=np.array([r[0] for r in rows]); R=np.array([list(r[1]) for r in rows])
  print(did, R.shape)
  # raw reply framing unknown: show per-column unique counts
  for j in range(R.shape[1]):
    u=np.unique(R[:,j]); 
    if 1<len(u)<=40:
      bits=[(b,round(float(((R[:,j]>>b)&1).mean()),3)) for b in range(8) if 0<((R[:,j]>>b)&1).mean()<1]
      print(' col',j,'n_unique',len(u),'bits',bits)
