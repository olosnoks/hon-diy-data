import numpy as np, load, datetime
c=load.con()
caps=list(c.execute('select s.name, s.drive, s.t_trigger from stalls s order by capture_id'))
allon=[];tot=0
for name,drive,tt in caps:
    B,R,L,ep=load.load(drive)
    if not B: continue
    allt=np.sort(np.concatenate([B[a][0] for a in B]))
    w=allt[(allt>tt-10)&(allt<=tt+0.5)]; k=int(np.argmax(np.diff(w))); tl=w[k] if np.diff(w)[k]>1 else w[-1]
    t,v=B[0x19]; b=B[0x17]
    m=(t>tl-60)&(t<=tl+0.01)
    s=[]
    for i in np.where(m)[0]:
        if i<5: continue
        base=np.median(v[i-5:i])
        if base-v[i]>=5 and v[i-1]-v[i]>=4: s.append('ON %+.1fs (%.0f->%.0fA)'%(t[i]-tl,77.06-base/2.5371,77.06-v[i]/2.5371))
        if v[i]-base>=5 and v[i]-v[i-1]>=4: s.append('off %+.1fs'%(t[i]-tl))
    mb=(b[0]>tl-60)&(b[0]<=tl+0.01)
    print('%s: ELD last60s: %s | batt min %.1fV | eld at last read %.1fA'%(name,'; '.join(s) or 'no step',b[1][mb].min()/10.45,77.06-v[m][-1]/2.5371))
    # global rate of ELD ON steps in this drive
    on=[t[i] for i in range(5,len(v)) if np.median(v[i-5:i])-v[i]>=5 and v[i-1]-v[i]>=4]
    allon+=on; tot+=t[-1]-t[0]
print('ELD ON-steps overall: %d over %.0f s of these drives -> one per %.0f s'%(len(allon),tot,tot/len(allon)))
