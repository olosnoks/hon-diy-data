import csv
from common import *
def first_rpm(d):
    r=d['rpm']; i=np.where(r[:,1]>400)[0][0]; return r[i,0]
rows=[]; arr=[]
for k,name in enumerate(DR):
    d,ep=load(name); r=d['rpm']; t=r[:,0]; rp=r[:,1]
    t_on=first_rpm(d)
    tg=np.array([at(d,'idle_target',x) for x in t])
    tps=np.array([at(d,'tps',x) for x in t]); vs=d['vss'][:,1]
    mp=np.array([at(d,'map',x) for x in t])
    # tps closed baseline: min tps over drive within +-60s
    closed= tps < 8.7
    # --- dips
    cand=[]
    for i in range(len(t)):
        if rp[i]<=0 or rp[i] > tg[i]-60: continue
        if not np.any(closed[(t>=t[i]-1.5)&(t<=t[i])]): continue
        m=(t>t[i]-1.5)&(t<t[i]+1.5)
        if rp[i]==rp[m].min(): cand.append(i)
    # merge candidates within 1.5s
    dips=[]
    for i in cand:
        if dips and t[i]-t[dips[-1]]<1.5:
            if rp[i]<rp[dips[-1]]: dips[-1]=i
        else: dips.append(i)
    for i in dips:
        tm=t[i]
        pm=(t>tm-2.5)&(t<=tm); j=np.where(pm)[0]; jmax=j[np.argmax(rp[j])]
        tf=t[jmax]; rfall=rp[jmax]
        # fall rate: max drop over ~0.35s
        seg=np.where((t>=tf)&(t<=tm))[0]
        rate=0
        for a in seg:
            b=np.where((t>t[a]+0.25)&(t<=t[a]+0.45)&(t<=tm+0.01))[0]
            if len(b): rate=min(rate,(rp[b[0]]-rp[a])/(t[b[0]]-t[a]))
        # pre-fall trend: rpm slope over 1s before tf
        pre=np.where((t>=tf-1.0)&(t<=tf))[0]
        slope_pre=np.polyfit(t[pre],rp[pre],1)[0] if len(pre)>3 else np.nan
        ov=np.where((t>=tm-5)&(t<=tm))[0]; overs=rp[ov].max()-tg[i]
        hunt=rp[np.where((t>=tf-6)&(t<tf))[0]]; hunt_pp=hunt.max()-hunt.min() if len(hunt) else np.nan
        map0=mp[jmax]; mapmx=mp[np.where((t>=tf)&(t<=tm+0.3))[0]].max()
        # below target / 600 contiguous
        def below(th):
            a=i
            while a>0 and rp[a-1]<th and rp[a-1]>0: a-=1
            b=i
            while b<len(t)-1 and rp[b+1]<th: b+=1
            return t[b]-t[a]
        ic=d['iac_cmd']; ia=d['iacv']
        ic0=at(d,'iac_cmd',tf); iv0=at(d,'iacv',tf); icm2=at(d,'iac_cmd',tf-2.5)
        post=win(d,'iac_cmd',tf,tf+3.0); first_up=None
        for tt,vv in post:
            if vv>=ic0+3: first_up=(tt-tf,vv); break
        icpk=post[:,1].max() if len(post) else np.nan
        # throttle catch: first tps > closed after tm-1
        tw=win(d,'tps',tm-1.0,tm+3); op=[x for x in tw if x[1]>9.5]
        t_thr=(op[0][0]-tm) if op else np.nan
        tps_at=at(d,'tps',tm)
        # launch within 10s?
        vss_m=at(d,'vss',tm)
        fut=win(d,'vss',tm,tm+10); launch=(vss_m<=1) and (fut[:,1].max()>=3 if len(fut) else False)
        # min of rpm in recovery after first min within 3s (double dip)
        rows.append(dict(drive=name[-4:], t=round(tm,2), wall=wall(ep,tm), vss=vss_m, rpm_min=rp[i], target=tg[i],
            under=round(tg[i]-rp[i]), fall_from=rfall, fall_s=round(tm-tf,2), rate=round(rate), slope_pre=round(slope_pre),
            overshoot=round(overs), hunt_pp=round(hunt_pp), map0=round(map0,1), map_max=round(mapmx,1), dmap=round(mapmx-map0,1),
            tps=round(tps_at,2), t_throttle=round(t_thr,2) if t_thr==t_thr else '', below_tgt_s=round(below(tg[i]),2), below600_s=round(below(600),2) if rp[i]<600 else 0,
            iac0=ic0, iac_d2=ic0-icm2, iacv0=round(iv0,1), exc_tf=round(rfall-tg[i]), tps_tf=round(at(d,'tps',tf),2), iac_up_lat=round(first_up[0],2) if first_up else '', iac_up_to=first_up[1] if first_up else '', iac_pk3=icpk,
            ect=round(at(d,'ect',tm),1), eld=round(at(d,'eld',tm),1), batt=round(at(d,'batt',tm),2), alt_fr=round(at(d,'alt_fr',tm),1),
            brake_now=at(d,'brake',tm), brake_2s=at(d,'brake',tm-2), ign=at(d,'ign',tm), since_on=round(tm-t_on), launch=int(launch)))
    # --- arrivals at idle
    i=0; n=len(t)
    while i<n:
        if rp[i]>1200:
            # find first subsequent time rpm<tg+150 with tps closed, within 15s, with no rpm>1200 re-rise
            j=i
            while j<n and t[j]-t[i]<15 and not (rp[j]<tg[j]+150 and closed[j]): 
                if rp[j]>1200: i=j
                j+=1
            if j<n and t[j]-t[i]<15 and rp[j]<tg[j]+150:
                te=t[j]
                nx=np.where((t>=te)&(t<=te+6))[0]
                kmin=nx[np.argmin(rp[nx])]
                # settle: first time after te where |rpm-tg|<60 for 2s
                st=''
                for q in np.where((t>=te)&(t<=te+30))[0]:
                    w=np.where((t>=t[q])&(t<=t[q]+2))[0]
                    if np.all(np.abs(rp[w]-tg[w])<60): st=round(t[q]-te,1); break
                ics=win(d,'iac_cmd',te-1.5,te+12)
                ic_e=at(d,'iac_cmd',te); 
                # decay: iac at te+0, +3, +6, +10
                icx=[at(d,'iac_cmd',te+s) for s in (0,3,6,10)]
                # fall rate entering
                pre=np.where((t>=te-1.0)&(t<=te))[0]
                sl=np.polyfit(t[pre],rp[pre],1)[0] if len(pre)>3 else np.nan
                tw=win(d,'tps',te,te+6); thr=np.any(tw[:,1]>9.5)
                arr.append(dict(drive=name[-4:], t=round(te,2), wall=wall(ep,te), vss=at(d,'vss',te), vss6=at(d,'vss',te+6), rpm_entry=rp[j], slope_in=round(sl), target=tg[j],
                  rpm_min6=rp[kmin], under6=round(tg[kmin]-rp[kmin]), t_min=round(t[kmin]-te,2), map_min_t=round(mp[kmin],1), tps_open6=int(thr), settle_s=st,
                  iac_e=icx[0], iac3=icx[1], iac6=icx[2], iac10=icx[3], ect=round(at(d,'ect',te),1), eld=round(at(d,'eld',te),1), since_on=round(te-t_on)))
                i=j
                while i<n and rp[i]<1200: i+=1
                continue
        i+=1
print(len(rows),len(arr))
for fn,L in (('dips.csv',rows),('arrivals.csv',arr)):
    with open(fn,'w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(L[0].keys())); w.writeheader(); w.writerows(L)
print(len(rows),'dips', len(arr),'arrivals')
