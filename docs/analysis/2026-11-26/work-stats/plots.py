from load import *
import json, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
per=load(33); secs,g=grid(per); k=known(g); t=secs-secs[0]
# bits
for d in (33,34):
    p=load(d)
    for a in (0x1C,0x27,0x29,0x2A,0x32,0x37,0x3C):
        v=p[a][:,1].astype(int)
        s=[f"b{b}:{np.mean((v>>b)&1):.2f}/{np.sum(np.diff((v>>b)&1)!=0)}" for b in range(8)]
        print(d,f"{a:02X}"," ".join(s))
# fig1 overview
fig,ax=plt.subplots(6,1,figsize=(13,13),sharex=True)
ax[0].plot(t,k['rpm'],lw=.6,color='#333'); ax[0].set_ylabel('rpm')
ax[1].plot(t,k['ect'],lw=.8,color='#c0392b'); ax[1].set_ylabel('ECT raw 0x10\n(high = reads cold)')
ax[2].plot(t,g[0x28],lw=.8,label='0x28 (iacv)',color='#2471a3'); ax[2].plot(t,g[0x29],lw=.8,label='0x29',color='#e67e22'); ax[2].legend(loc='upper left'); ax[2].set_ylabel('raw')
ax[3].plot(t,k['eld'],lw=.8,label='0x19 eld',color='#7d3c98'); ax[3].plot(t,g[0x1C]+3,lw=.6,label='0x1C (+3 offset for visibility)',color='#27ae60'); ax[3].legend(loc='upper left'); ax[3].set_ylabel('raw')
ax[4].step(t,g[0x2A],where='post',color='#117a65',label='0x2A'); ax[4].plot(t,k['iat']/20,lw=.8,color='#999',label='IAT raw /20'); ax[4].legend(loc='upper right'); ax[4].set_ylabel('raw')
for i,(a,c) in enumerate(((0x32,'#2471a3'),(0x37,'#e67e22'))):
    y=g[a]; ax[5].plot(t[y<45],np.full((y<45).sum(),i),'|',ms=10,color=c)
y=g[0x3C]; ax[5].plot(t[y>1.5],np.full((y>1.5).sum(),2),'|',ms=10,color='#c0392b')
y=g[0x27]; ax[5].plot(t[y>0.5],np.full((y>0.5).sum(),3),'|',ms=10,color='#555')
ax[5].set_yticks([0,1,2,3]); ax[5].set_yticklabels(['0x32=44','0x37=44','0x3C=2','0x27>0']); ax[5].set_ylim(-.5,3.5)
ax[5].set_xlabel('s since 20:45:18 (drive_2026_11_26_2045)')
fig.suptitle('drive_2026_11_26_2045: unknown bytes that move (all others constant)'); fig.tight_layout(); fig.savefig('01_overview_2045.png',dpi=110)
# fig2 29 vs 28
fig,ax=plt.subplots(1,2,figsize=(12,5))
for d,c in ((33,'#2471a3'),(34,'#e67e22')):
    p=load(d); x=p[0x28][:,1]; y=p[0x29][:,1]
    ax[0].plot(x+np.random.uniform(-.2,.2,len(x)),y+np.random.uniform(-.2,.2,len(y)),'.',ms=3,alpha=.4,color=c,label=f'drive {d}')
xs=np.arange(0,36); ax[0].set_xlabel('0x28 (iacv raw)'); ax[0].set_ylabel('0x29 raw'); ax[0].legend(); ax[0].grid(alpha=.3)
ax[0].set_title('0x29 is a fixed monotone function of 0x28 (resid sd 1.1)')
seg=(t>470)&(t<640)
ax[1].plot(t[seg],g[0x28][seg],label='0x28'); ax[1].plot(t[seg],g[0x29][seg],label='0x29'); ax[1].plot(t[seg],k['ect'][seg],label='ECT raw',lw=.7); ax[1].plot(t[seg],k['rpm'][seg]/10,label='rpm/10',lw=.7); ax[1].legend(); ax[1].set_title('zoom t=470-640 s'); ax[1].grid(alpha=.3)
fig.tight_layout(); fig.savefig('02_0x29_vs_0x28.png',dpi=110)
# fig3 heatmap of correlations
res=json.load(open('corr.json'))
keys=[x for x in res if x.startswith('33_') and x[3:] not in ('27',)]
names=[n for n,*_ in res[keys[0]]]; names=sorted(set(n for kk in keys for n,*_ in res[kk]))
M=np.full((len(keys),len(names)),np.nan); L=np.zeros_like(M)
for i,kk in enumerate(keys):
    for n,rp,rs,rl,lag in res[kk]: M[i,names.index(n)]=rp; L[i,names.index(n)]=lag
fig,ax=plt.subplots(figsize=(12,3.5)); im=ax.imshow(M,cmap='RdBu_r',vmin=-1,vmax=1,aspect='auto')
ax.set_xticks(range(len(names))); ax.set_xticklabels(names,rotation=45,ha='right'); ax.set_yticks(range(len(keys))); ax.set_yticklabels(['0x'+x[3:] for x in keys])
for i in range(M.shape[0]):
    for j in range(M.shape[1]):
        if not np.isnan(M[i,j]): ax.text(j,i,f"{M[i,j]:.2f}",ha='center',va='center',fontsize=7)
plt.colorbar(im); ax.set_title('Pearson r (lag 0), drive_2026_11_26_2045, 1 Hz grid'); fig.tight_layout(); fig.savefig('03_corr_heatmap_2045.png',dpi=110)
