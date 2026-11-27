import pickle, numpy as np, pandas as pd, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
S,F=pickle.load(open('SF2.pkl','rb'))
s=S[S.drive=='2251']; f=F[F.drive=='2251']
rows=[('rpm',f.wall,f.rpm),('vss km/h',f.wall,f.vss),('TPS raw',f.wall,f.b14),('0x27',s.wall,s.b27),('0x28 iacv',s.wall,s.b28),
      ('0x2F (purge?)',s.wall,s.b2f),('STFT raw',s.wall,s.b20),('0x3C',s.wall,s.b3c),('0xA0 rear O2',s.wall,s.ba0),
      ('0x0B b6',s.wall,(s.b0b.astype(int)//64)%2),('0x0F b0 / 0x9A',s.wall,s.b0f)]
fig,ax=plt.subplots(len(rows),1,figsize=(16,20),sharex=True)
for a,(n,x,y) in zip(ax,rows):
    a.plot(x,y,lw=0.6); a.set_ylabel(n,rotation=0,ha='right',fontsize=8); a.grid(alpha=.3)
ax[-1].plot(s.wall,s.b9a/4,lw=0.6,color='r')
plt.tight_layout(); plt.savefig('01_drive2251_overview.png',dpi=90)
# 0x3C vs rpm, 0x2F vs STFT
fig,ax=plt.subplots(1,3,figsize=(16,5))
ax[0].scatter(S.rpm,S.b3c+np.random.uniform(-.2,.2,len(S)),s=3,alpha=.4); ax[0].set_xlabel('rpm'); ax[0].set_ylabel('0x3C')
o=S[(S.rpm>950)&(S.inj>0)]
sc=ax[1].scatter(o['map'],o.b27+np.random.uniform(-.2,.2,len(o)),s=3,c=o.rpm,cmap='viridis',alpha=.6); ax[1].set_xlabel('MAP kPa (off idle)'); ax[1].set_ylabel('0x27'); plt.colorbar(sc,ax=ax[1],label='rpm')
c=S[(S.vss>20)&(S.b0f==1)&(S.inj>0)]
sc=ax[2].scatter(c.b2f,(c.b20/128-1)*100,s=4,c=c['map'],cmap='plasma',alpha=.6); ax[2].set_xlabel('0x2F'); ax[2].set_ylabel('STFT %'); plt.colorbar(sc,ax=ax[2],label='MAP kPa')
plt.tight_layout(); plt.savefig('02_0x3C_0x27_0x2F.png',dpi=90)
