import numpy as np, load, datetime
for d in ['drive_2026_11_26_2031','drive_2026_11_26_2034']:
    B,R,L,ep=load.load(d)
    wall=lambda t: datetime.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S')
    print(d, sorted('%02X'%a for a in B))
    rt,rv=R
    for t in list(np.arange(rt[0],rt[0]+40,2))+[rt[-1]-1]:
        vals={a:load.at(B[a],np.array([t]))[0] for a in (0x08,0x09,0x0A,0x0B,0x0C,0x0D,0x0E,0x0F,0x10,0x17,0x27,0x28) if a in B}
        m=(rt>t-1)&(rt<=t)
        print('%7.1f %s rpm=%5.0f '%(t,wall(t),rv[m].mean() if m.any() else np.nan)+' '.join('%s=%.0f'%(load.name(a),v) for a,v in vals.items()))

