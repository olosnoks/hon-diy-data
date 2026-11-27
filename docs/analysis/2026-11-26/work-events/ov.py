import numpy as np, load
c=load.con()
for r in c.execute("select id,name,duration_s,read_plan,stall_count,end_reason,status from drives where id>=27"): print(r)
for d in ['drive_2026_11_26_2045','drive_2026_11_26_2114']:
    B,R,L,ep=load.load(d)
    print(d, 'rpm n',len(R[0]))
    for a in sorted(B):
        t,v=B[a]
        u=np.unique(v)
        ch=int((np.diff(v)!=0).sum())
        print('%02X %-7s n=%5d uniq=%3d min=%3d max=%3d med=%5.1f changes=%5d  top=%s'%(a,load.name(a),len(v),len(u),v.min(),v.max(),np.median(v),ch, ' '.join('%d:%d'%(x,(v==x).sum()) for x in u[np.argsort(-np.array([(v==x).sum() for x in u]))][:6])))
