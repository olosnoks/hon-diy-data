from common import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
f,axs=plt.subplots(3,1,figsize=(15,10))
for ax,did in zip(axs,(37,38,39)):
    z=load(did); fo=z['raw_o2']; ro=z['raw_o2_2']; fo=fo[fo[:,1]<1.25]
    t0=z['t'][0]
    # rolling 60 s windows
    W0=np.arange(ro[0,0],ro[-1,0]-60,30)
    C=[];SR=[];SF=[];V=[];RPM=[]
    for a in W0:
        k=(ro[:,0]>=a)&(ro[:,0]<a+60)&(ro[:,1]<1.25)
        if k.sum()<20: C.append(np.nan);SR.append(np.nan);SF.append(np.nan);V.append(np.nan);RPM.append(np.nan);continue
        fi=np.interp(ro[k,0]-0.07,fo[:,0],fo[:,1])
        C.append(np.corrcoef(fi,ro[k,1])[0,1] if ro[k,1].std()>0.01 else 0); SR.append(ro[k,1].std()); SF.append(fi.std())
        g=(z['t']>=a)&(z['t']<a+60); V.append(np.nanmean(z['vss'][g])); RPM.append(np.nanmean(z['rpm'][g]))
    W0=(W0+30)/60
    ax.plot(W0,C,'k.-',label='corr(rear,front)'); ax.plot(W0,SR,'r.-',label='rear std V'); ax.plot(W0,SF,'b.-',label='front std V')
    a2=ax.twinx(); a2.plot(W0,V,'g-',alpha=.5,label='vss'); a2.set_ylabel('vss km/h (green)')
    ax.set_title(f'drive {did} {z["name"]}: rolling 60 s'); ax.legend(loc='upper left'); ax.set_ylim(-0.3,1.05); ax.grid(alpha=.3)
    # print phase summary
    for lab,a,b in [('first 3 min after rear<1V',None,None)]:
        pass
    print(did, 'windows corr median', np.nanmedian(C), 'by vss<5:', np.nanmedian(np.array(C)[np.array(V)<5]), 'vss>30:', np.nanmedian(np.array(C)[np.array(V)>30]))
axs[-1].set_xlabel('session min'); f.tight_layout(); f.savefig(f'{W}/rear_o2_rolling.png',dpi=80)
# decel cut analysis
print('--- decel fuel cut timing (drive 38,39) ---')
for did in (38,39):
    z=load(did); t=z['t']; inj=z['inj']; fo=z['raw_o2']; ro=z['raw_o2_2']; ro=ro[ro[:,1]<1.25]
    cut=np.nan_to_num(inj,nan=1)<0.1
    # segments of cut >= 1.5 s with rpm>1000
    d=np.diff(cut.astype(int)); st=np.where(d==1)[0]+1; en=np.where(d==-1)[0]+1
    if cut[0]: st=np.r_[0,st]
    en=en[en>st[0]]; st=st[:len(en)]
    out=[]
    for s,e in zip(st,en):
        if t[e]-t[s]<1.5 or z['rpm'][s]<1000: continue
        # front first <0.2 after s, rear first <0.2 after s
        def first(arr,cond,t0,t1):
            k=(arr[:,0]>=t0)&(arr[:,0]<=t1)&cond(arr[:,1]); return arr[k,0][0]-t0 if k.any() else np.nan
        fl=first(fo,lambda v:v<0.2,t[s],t[e]+3); rl=first(ro,lambda v:v<0.2,t[s],t[e]+3)
        fr=first(fo,lambda v:v>0.45,t[e],t[e]+15); rr=first(ro,lambda v:v>0.45,t[e],t[e]+15)
        # rear min during cut
        k=(ro[:,0]>=t[s])&(ro[:,0]<=t[e]); rmin=ro[k,1].min() if k.any() else np.nan
        k2=(fo[:,0]>=t[s])&(fo[:,0]<=t[e]); fmin=fo[k2,1].min()
        out.append((t[s]/60,t[e]-t[s],fl,rl,fr,rr,fmin,rmin))
    out=np.array(out)
    print(f'drive {did}: {len(out)} cuts >=1.5 s. columns: start_min, dur, front->lean s, rear->lean s, front->rich after s, rear->rich after s, front min, rear min')
    print(np.round(out,2))
    print(' medians:', np.round(np.nanmedian(out,axis=0),2))
