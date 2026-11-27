import pickle,numpy as np
F=pickle.load(open('frame.pkl','rb'))
for k in [0x32,0x37,0x3A]:
    d=F[k]
    for did in np.unique(d['drive']):
        m=d['drive']==did
        idx=np.where(m)[0][:12]
        print(hex(k),int(did),' '.join(f"{d['t'][i]:.0f}:{int(d['v'][i])}/{d['rpm'][i]:.0f}/e{d['ect'][i]:.0f}/b{d['batt'][i]:.0f}" for i in idx))
d=F[0x32]
print('rpm<400 reads:',(d['rpm']<400).sum())
# per batt value
for k in [0x32,0x37]:
    d=F[k]
    for b in np.unique(d['batt']):
        m=d['batt']==b
        print(hex(k),'batt',b,f'{b/10.45:.2f}V',m.sum(),round((d['v'][m]==44).mean()*100,1))
