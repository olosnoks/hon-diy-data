import numpy as np, load, datetime, sqlite3
c=load.con()
names=[r[0] for r in c.execute("select name from drives where id>=6 order by id")]
for d in names:
    B,R,L,ep=load.load(d)
    if not B or 0x27 not in B: continue
    rt,rv=R
    if len(rv)==0: continue
    # find crank/start: rpm 0 samples followed by running
    off=np.where(rv<300)[0]
    if len(off)==0: continue
    st=[i for i in range(1,len(rv)) if rv[i]>=300 and rv[i-1]<300]
    for i in st[:2]:
        ts=rt[i]
        wall=datetime.datetime.fromtimestamp(ep+ts).strftime('%H:%M:%S')
        seq=[]
        for dt in [-5,-2,0,2,5,10,20,30,45,60,90,120,180,300]:
            tq=ts+dt
            if tq>rt[-1]: break
            m=(rt>tq-1)&(rt<=tq)
            seq.append('%+d:%s/%s/%s'%(dt, '%.0f'%rv[m].mean() if m.any() else '-', '%.0f'%load.at(B[0x27],np.array([tq]))[0], '%.0f'%load.at(B[0x28],np.array([tq]))[0]))
        ect=load.ect_c(load.at(B[0x10],np.array([ts]))[0]) if 0x10 in B else np.nan
        print(d, wall, 'ECT %.0fC'%ect, ' '.join(seq))
