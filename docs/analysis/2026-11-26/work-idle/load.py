import sqlite3, numpy as np, json
c=sqlite3.connect('hondalog.db')
for name in ['drive_2026_11_26_2224','drive_2026_11_26_2251']:
    did,ep=c.execute("select id, coalesce(epoch_unix,started_at) from drives where name=?",(name,)).fetchone()
    print(name,did,ep, c.execute("select status,end_reason,duration_s from drives where id=?",(did,)).fetchone())
    chans=[r[0] for r in c.execute("select distinct channel from readings where drive_id=?",(did,))]
    d={}
    for ch in chans:
        a=np.array(c.execute("select t,value from readings where drive_id=? and channel=? and value is not null order by t",(did,ch)).fetchall(),float)
        if len(a): d[ch]=a
    print(' ', {k:len(v) for k,v in d.items()})
    np.savez(name+'.npz', epoch=ep, **d)
    ev=c.execute("select t,kind,text from events where drive_id=? order by t",(did,)).fetchall()
    for e in ev[:80]: print('   ev',round(e[0],1),e[1],e[2][:100])
