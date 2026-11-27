import numpy as np, load, datetime
c=load.con()
caps=list(c.execute('select s.name, s.drive, s.t_trigger, d.id from stalls s join drives d on d.name=s.drive order by capture_id'))
for name,drive,tt,did in caps:
    B,R,L,ep=load.load(drive)
    if not B: print(name,'OBD2-only'); continue
    allt=np.sort(np.concatenate([B[a][0] for a in B]))
    w=allt[(allt>tt-10)&(allt<=tt+0.5)]; k=int(np.argmax(np.diff(w))); tl=w[k] if np.diff(w)[k]>1 else w[-1]
    rt,rv=R
    m30=(rt>tl-30)&(rt<=tl); m3=(rt>tl-3)&(rt<=tl)
    def last(a,dt=0): return load.at(B[a],np.array([tl-dt]))[0] if a in B else np.nan
    ect=B[0x10]; me=(ect[0]>tl-30)&(ect[0]<=tl)
    s='%s last %s: rpm30 %.0f-%.0f mean %.0f, last3s %.0f-%.0f; ECT30 %.0f-%.0fC; iacv %.0f->%.0f; x27 %.0f; batt %.1fV; eld %.1fA'%(name,datetime.datetime.fromtimestamp(ep+tl).strftime('%H:%M:%S.%f')[:-4],rv[m30].min(),rv[m30].max(),rv[m30].mean(),rv[m3].min(),rv[m3].max(),load.ect_c(ect[1][me].max()),load.ect_c(ect[1][me].min()),last(0x28,10),last(0x28),last(0x27),last(0x17)/10.45,77.06-last(0x19)/2.5371)
    after=allt[allt>tl+1.0]
    if len(after) and after[0]-tl<60:
        s+=' | link back +%.1fs, rpm %.0f'%(after[0]-tl, rv[rt>after[0]][:5].mean())
    else:
        nx=c.execute('select name,started_at from drives where id>? order by id limit 1',(did,)).fetchone()
        B2,R2,L2,ep2=load.load(nx[0])
        if R2[0].size:
            t0=R2[0][0]
            fl=lambda a: load.at(B2[a],np.array([t0+1.5]))[0] if a in B2 else np.nan
            s+=' | next drive %s starts %+.0fs later: rpm first 3s %s, iacv %.0f, x27 %.0f, f08 %.0f, f0B %.0f, batt %.1fV'%(nx[0],ep2+t0-(ep+tl),' '.join('%.0f'%x for x in R2[1][:30:6]),fl(0x28),fl(0x27),fl(0x08),fl(0x0B),fl(0x17)/10.45)
    print(s)
