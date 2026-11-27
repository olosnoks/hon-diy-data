from common import *
DEAD=1.0
for did in (38,39):
    z=load(did); t=z['t']; inj=z['inj']; fs=z['fuel_status']; cl=z['closed_loop']
    print(f'=========== drive {did}')
    k=fs==1; print('fuel_status==1 at t(min):', np.round(np.unique(np.floor(t[k]/6)/10),1)[:20], 'ECT there', np.nanmean(z['ect'][k]) if k.any() else None)
    # ---- WOT / high load
    hl=(z['map']>85)|(z['tps']>50)
    d=np.diff(hl.astype(int)); st=np.where(d==1)[0]+1; en=np.where(d==-1)[0]+1
    if hl[0]: st=np.r_[0,st]
    en=en[en>st[0]] if len(st) else en; st=st[:len(en)]
    print('high-load events (MAP>85 or TPS>50) >=1 s:')
    print('  start_min dur_s rpm0->rpm1  MAPmax TPSmax | inj ms med | o2 med, frac>0.6, min | fs values | stft med | enrich est')
    # closed-loop reference: k=(inj-dead)/(MAP/T)/(1+total/100) at rpm bins, MAP>55
    T=z['iat']+273.15
    mcl=sel_cl(z,steady=True)&(z['map']>55)
    kcl=(inj-DEAD)/(z['map']/T)/(1+z['total']/100)
    for s,e in zip(st,en):
        if t[e]-t[s]<1.0: continue
        g=slice(s,e)
        o=z['o2'][g]; fsv=np.unique(fs[g][~np.isnan(fs[g])])
        kol=(inj[g]-DEAD)/(z['map'][g]/T[g])/(1+z['ltft'][g]/100)
        # ref at similar rpm (+-400)
        rp=np.nanmedian(z['rpm'][g]); ref=mcl&(np.abs(z['rpm']-rp)<500)
        E=np.nanmedian(kol)/np.nanmedian(kcl[ref]) if ref.sum()>20 else np.nan
        print(f'  {t[s]/60:6.2f} {t[e]-t[s]:5.1f} {z["rpm"][s]:5.0f}->{z["rpm"][e-1]:5.0f} {np.nanmax(z["map"][g]):5.1f} {np.nanmax(z["tps"][g]):5.1f} | {np.nanmedian(inj[g]):5.2f} | {np.nanmedian(o):.2f} {np.nanmean(o>0.6):.2f} {np.nanmin(o):.2f} | {fsv} | {np.nanmedian(z["stft"][g]):+.1f} | {E:.2f} (ref n={ref.sum()})')
    # ---- decel cut
    cut=np.nan_to_num(inj,nan=1)<0.1
    run=z['rpm']>300
    print('decel cut: fraction of running time with inj=0:', (cut&run).sum()/run.sum())
    d=np.diff(cut.astype(int)); st=np.where(d==1)[0]+1; en=np.where(d==-1)[0]+1
    if cut[0]: st=np.r_[0,st]
    en=en[en>st[0]]; st=st[:len(en)]
    ev=[(t[s],t[e]-t[s],z['rpm'][max(s-1,0)],z['rpm'][e],np.nanmax(z['tps'][s:e]),np.nanmean(z['o2'][s:e]),np.nanmin(z['o2'][s:e]),np.nanmedian(z['map'][s:e]), np.nanmedian(fs[s:e]), np.nanmedian(cl[s:e])) for s,e in zip(st,en) if t[e]-t[s]>=0.5]
    ev=np.array(ev)
    print(f'  {len(ev)} cuts >=0.5 s; dur median {np.median(ev[:,1]):.1f}s max {ev[:,1].max():.1f}; entry rpm median {np.median(ev[:,2]):.0f}; exit rpm median {np.median(ev[:,3]):.0f}, min exit rpm {ev[:,3].min():.0f}; TPS max during cut {ev[:,4].max():.1f}; o2 mean {np.median(ev[:,5]):.3f} min {ev[:,6].max():.3f}(max of mins); MAP med {np.median(ev[:,7]):.1f}; fuel_status {np.unique(ev[:,8])}; closed_loop {np.unique(ev[:,9])}')
    # exit rpm distribution and vss: fuel resumes when?
    # ---- ECT effect while driving: trim residual vs ECT bands (rpm model)
    m=sel_cl(z,ect_lo=0,ect_hi=200); z["ect_glitch"][:]=False; m2=sel_cl(z,ect_lo=0,ect_hi=200)
    pred=8.22+6.68*1000/z['rpm']
    res=z['total']-pred
    for lo,hi in [(0,60),(60,65),(65,70),(70,75),(75,80),(80,100)]:
        k=m&(z['ect']>=lo)&(z['ect']<hi)
        if k.sum()>50: print(f'  ECT {lo}-{hi}: n={k.sum():5d}  total {np.nanmean(z["total"][k]):+.1f}  resid vs rpm-model {np.nanmean(res[k]):+.2f}  rpm {np.nanmean(z["rpm"][k]):.0f}')
    # ECT steps
    e=z['raw_ect']; j=np.where(np.abs(np.diff(e[:,1]))>3)[0]
    for i in j:
        ts=e[i+1,0]; b=(t>ts-20)&(t<ts)&m2; a=(t>ts)&(t<ts+20)&m2
        print(f'  ECT step {e[i,1]:.1f}->{e[i+1,1]:.1f} at {ts/60:.2f} min: resid before {np.nanmean(res[b]):+.1f} (n{b.sum()}) after {np.nanmean(res[a]):+.1f} (n{a.sum()}); inj/(MAP) before {np.nanmean(inj[b]/z["map"][b]):.3f} after {np.nanmean(inj[a]/z["map"][a]):.3f}')
    # actual lambda in OL power events vs CL raw fuel/air
    kraw=(inj-DEAD)/(z['map']/T)
    fl=(fs==4)&(z['map']>85)&(z['rpm']>1500)&(inj>1)
    for lo,hi in [(1500,3000),(3000,4500),(4500,6000)]:
        k=fl&(z['rpm']>=lo)&(z['rpm']<hi); ref=mcl&(z['rpm']>=lo-300)&(z['rpm']<hi+300)
        if k.sum()>5 and ref.sum()>20:
            E=np.nanmedian(kraw[k])/np.nanmedian(kraw[ref]); print(f'  OL power fs=4 MAP>85 rpm {lo}-{hi}: n={k.sum()} o2 med {np.nanmedian(z["o2"][k]):.2f} fuel/air vs CL x{E:.2f} -> AFR ~{14.7/E:.1f} (dead {DEAD} ms; CL ref n={ref.sum()}, MAP {np.nanmedian(z["map"][ref]):.0f})')
