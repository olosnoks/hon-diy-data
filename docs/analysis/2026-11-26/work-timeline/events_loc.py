from common import *
S=json.load(open('segments.json'))
res={}
for k,d in DRIVES.items():
    t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss')
    g,gx,gy,_=clean_gps(d)
    gd=np.concatenate([[0],np.cumsum(np.hypot(np.diff(gx),np.diff(gy)))])
    G=np.load(f'grid_{k}.npz'); T=G['T']; lab=S[k]['lab']
    res[k]=[]
    for hms,txt in EVENTS[k]:
        te=t_of(hms)
        w=(t>te-4)&(t<te+6)
        if w.any():
            i=np.where(w)[0][np.argmin(rpm[w])]; tmin=t[i]; rmin=rpm[i]; v=vss[i]
        else: tmin=te; rmin=np.nan; v=np.nan
        # last ECU sample before and first after
        jb=np.searchsorted(t,te)
        x=float(np.interp(te,g[:,0],gx)); y=float(np.interp(te,g[:,0],gy)); Vf=G["VSS"].copy(); nn=np.isfinite(Vf); Vf[~nn]=np.interp(T[~nn],T[nn],Vf[nn]); odo=np.concatenate([[0],np.cumsum(Vf[1:]/3.6)])*1.011; od=float(np.interp(te,T,odo))
        gi=np.argmin(abs(g[:,0]-te))
        seg=lab[int(np.clip(round(te-T[0]),0,len(T)-1))]
        # segment containing
        tq=T[int(np.clip(np.floor(te-0.5-T[0]),0,len(T)-1))]; sg=[s for s in S[k]['segs'] if s['t0']<=tq<s['t0']+s['dur']]; seg=sg[0]['label'] if sg else seg
        res[k].append(dict(hms=hms,txt=txt,te=te,x=x,y=y,where=where(x,y),route_km=od/1000,seg=seg,segstart=sg[0]['start'] if sg else '',
             rmin=float(rmin),tmin=wall(tmin),vss=float(v),gspd=float(g[gi,3]),gdt=float(g[gi,0]-te)))
        print(k,hms,txt,'|',where(x,y),f"route {od/1000:.2f} km",'| seg',seg,sg[0]['start'] if sg else '','| min rpm',rmin,'at',wall(tmin),'vss',v,'gps spd',round(g[gi,3],1))
    # ECU coverage around unplug and silences
    for a,b in [('22:36:10','22:37:40'),('23:12:50','23:13:10'),('23:16:20','23:16:40'),('22:50:30','22:51:40'),('23:26:50','23:27:10'),('22:45:15','22:45:30')]:
        ta,tb=t_of(a),t_of(b); s=(t>ta)&(t<tb)
        if s.sum()==0: continue
        tt=t[s]; dd=np.diff(tt); j=np.argmax(dd) if len(dd) else 0
        if len(dd): print('   window',a,b,'largest gap',wall(tt[j]),'->',wall(tt[j+1]),round(dd[j],2),'rpm before/after',rpm[s][j],rpm[s][j+1],'vss',vss[s][j],vss[s][j+1])
json.dump(res,open('events_loc.json','w'))
