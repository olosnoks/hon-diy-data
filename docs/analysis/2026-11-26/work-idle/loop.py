from common import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
allx=[]
for name in DR:
    d,ep=load(name); ic=d['iac_cmd']; iv=d['iacv']; r=d['rpm']
    dt_=np.diff(ic[:,0]); print(name,'iac read interval median',np.median(dt_), 'p95',np.percentile(dt_,95))
    for k in range(1,len(ic)):
        t1=ic[k,0]; t0=ic[k-1,0]
        if t1-t0>2: continue
        vss=at(d,'vss',t1); tps=win(d,'tps',t1-3,t1+3)[:,1]
        if vss>0 or len(tps)==0 or tps.max()>8.7: continue
        rr=win(d,'rpm',t0-0.0,t1)[:,1]; tg=at(d,'idle_target',t0)
        mp=win(d,'map',t0,t1)[:,1]
        e=tg-np.mean(rr); emin=tg-rr.min()
        allx.append((name[-4:],t1,ic[k-1,1],ic[k,1],ic[k,1]-ic[k-1,1],e,emin,tg,np.mean(mp),at(d,'eld',t1),iv[k,1]))
A=np.array([x[1:] for x in allx],float)
dI=A[:,3]; e=A[:,4]; emin=A[:,5]
print('n standstill closed-throttle iac reads',len(A))
for lo,hi in [(-400,-150),(-150,-80),(-80,-40),(-40,-20),(-20,0),(0,20),(20,40),(40,80),(80,150),(150,500)]:
    m=(e>=lo)&(e<hi)
    if m.sum(): print(f' rpm error (target-rpm mean) {lo:5d}..{hi:4d}: n={m.sum():4d} dI mean {dI[m].mean():6.2f} median {np.median(dI[m]):5.1f}  frac changed>=|2|: {np.mean(np.abs(dI[m])>=2):.2f}')
# slope
m=np.abs(e)<150; p=np.polyfit(e[m],dI[m],1); print('dI per read vs error slope',p)
# iacv vs iac mapping
for name in DR:
    d,ep=load(name); ic=d['iac_cmd']; iv=d['iacv']
    # join same-time
    n=min(len(ic),len(iv)); same=np.abs(ic[:n,0]-iv[:n,0])<0.05
    print(name,'same-time pairs',same.mean())
f,ax=plt.subplots(1,3,figsize=(18,5))
for name,c in zip(DR,['C0','C1']):
    d,ep=load(name); ic=d['iac_cmd']; iv=d['iacv']; n=min(len(ic),len(iv))
    ax[0].scatter(ic[:n,1],iv[:n,1],s=3,c=c,label=name[-4:])
ax[0].set_xlabel('iac_cmd (0x29)'); ax[0].set_ylabel('iacv (0x28)'); ax[0].legend(); ax[0].grid(alpha=.3)
ax[1].scatter(e,dI,s=4,alpha=.5); ax[1].set_xlabel('target - mean rpm over previous read interval'); ax[1].set_ylabel('iac_cmd change at this read'); ax[1].grid(alpha=.3); ax[1].set_xlim(-400,450)
ax[1].set_title('standstill, throttle closed')
np.save('loopA.npy',A)
# rpm vs iac at steady standing idle: mean rpm-target vs iac, per 10s windows
plt.tight_layout(); plt.savefig('loop_scatter.png',dpi=80)
