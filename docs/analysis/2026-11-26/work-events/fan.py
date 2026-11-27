import numpy as np, load, datetime
c=load.con()
caps=list(c.execute('select s.name, s.drive, s.t_trigger from stalls s order by capture_id'))
capd={}
for n,d,t in caps: capd.setdefault(d,[]).append((n,t))
drives=[r[0] for r in c.execute("select name from drives where id between 6 and 34 order by id")]
eps=[]; tot=0
for d in drives:
    B,R,L,ep=load.load(d)
    if not B or 0x19 not in B: continue
    t,v=B[0x19]; A=77.06-v/2.5371
    base=np.array([np.median(A[max(0,i-60):i+1]) for i in range(len(A))])
    hi=(A-base)>=8
    st=np.where(np.diff(hi.astype(int))==1)[0]+1; en=np.where(np.diff(hi.astype(int))==-1)[0]+1
    tot+=t[-1]-t[0]
    for s in st:
        e=en[en>s]; e=e[0] if len(e) else len(t)-1
        eps.append((d,t[s],t[e]-t[s],A[s:e+1].max(),ep))
    for n,tt in capd.get(d,[]):
        near=[(t[s]-tt, t[(en[en>s][0] if (en>s).any() else len(t)-1)]-tt) for s in st if -120<t[s]-tt<5]
        print('%s: big-load episodes (start,end rel. to trigger) %s'%(n,[('%+.1f'%a,'%+.1f'%b) for a,b in near]))
du=np.array([e[2] for e in eps]); print('big-load (>=+8A) episodes: %d in %.0f s, one per %.0f s; duration median %.1f s, p10 %.1f, p90 %.1f; peak A median %.0f'%(len(eps),tot,tot/len(eps),np.median(du),np.percentile(du,10),np.percentile(du,90),np.median([e[3] for e in eps])))
