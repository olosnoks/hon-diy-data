from common import *
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt, matplotlib.dates as mdates, datetime
S=json.load(open('segments.json')); EL=json.load(open('events_loc.json'))
SEGC={'idle/parked':'#d9d9d9','stop':'#bdbdbd','launch':'#fdd0a2','decel':'#c6dbef','city':'#c7e9c0','arterial':'#fff3b0','interstate':'#fcbba1','no ECU data':'#ff6b6b'}
KC={'NoReply':'#d62728','ShortReply':'#ff7f0e','BadChecksum':'#9467bd','BadFrame':'#8c564b'}
def dt(x): return [datetime.datetime.fromtimestamp(E+v) for v in np.atleast_1d(x)]
plt.rcParams.update({'font.size':9,'axes.grid':True,'grid.alpha':0.3})
for k,d in DRIVES.items():
    t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss'); g,gx,gy,nbad=clean_gps(d)
    ev=[e for e in events(d) if e[1]=='link_error']; evt=np.array([e[0] for e in ev]); evk=[json.loads(e[3])['kind'] for e in ev]
    Q=json.load(open('quality.json'))[k]
    st=stream_times(d); allt=np.sort(np.concatenate([v for n,v in st.items() if n.startswith('ecu/') and n!='ecu/link']))
    fig,ax=plt.subplots(3,1,figsize=(15,9),sharex=True,gridspec_kw=dict(height_ratios=[3,3,1.6]))
    for s in S[k]['segs']:
        for a in ax[:2]: a.axvspan(dt(s['t0'])[0],dt(s['t0']+s['dur'])[0],color=SEGC[s['label']],lw=0,alpha=0.8)
    ax[0].plot(dt(t),vss,'k-',lw=0.8,label='vss (ECU)')
    ax[0].plot(dt(g[:,0]),g[:,3],'.',ms=2,color='#1f77b4',label='GPS speed (as received)')
    ax[0].set_ylabel('km/h'); 
    ax[1].plot(dt(t),rpm,'-',lw=0.5,color='#444')
    ax[1].set_ylabel('rpm'); ax[1].set_ylim(0,max(3500,rpm.max()*1.05))
    for i,e in enumerate(EL[k]):
        for a in ax: a.axvline(dt(e['te'])[0],color='m',lw=0.8,ls='--')
        ax[1].annotate(f"E{i+1}",(dt(e['te'])[0],ax[1].get_ylim()[1]*0.93),color='m',fontsize=9,ha='left',fontweight='bold')
    # read rate 1 s bins
    b=np.arange(np.floor(allt[0]),allt[-1]+1,1.0); h,_=np.histogram(allt,b)
    ax[2].plot(dt(b[:-1]),h,'-',lw=0.6,color='#2ca02c',label='ECU reads/s')
    for gp in Q['gaps']: ax[2].axvspan(dt(gp['t'])[0],dt(gp['t']+gp['dur'])[0],color='red',alpha=0.5,lw=0)
    for kn,col in KC.items():
        m=np.array([x==kn for x in evk],bool)
        if m.any(): ax[2].plot(dt(evt[m]),np.full(m.sum(),5),'|',ms=12,color=col,label=kn)
    gd=np.diff(g[:,0]); 
    for i in np.where(gd>2.5)[0]: ax[2].axvspan(dt(g[i,0])[0],dt(g[i+1,0])[0],ymin=0.85,ymax=1,color='#1f77b4',alpha=0.6,lw=0)
    ax[2].set_ylabel('reads/s'); ax[2].set_ylim(0,60); ax[2].legend(loc='lower left',ncol=6,fontsize=7)
    from matplotlib.patches import Patch
    hs=[Patch(color=c_,label=l) for l,c_ in SEGC.items()]
    l0=ax[0].legend(loc='upper left',fontsize=8); ax[0].add_artist(l0)
    ax[0].legend(handles=hs,loc='upper right',ncol=4,fontsize=7)
    ax[2].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax[0].set_title(f"{NAMES[d]}: speed, rpm, link (red = ECU gap > 0.5 s, blue top band = GPS gap > 2.5 s; E# = events, see drive-timeline.md)")
    fig.tight_layout(); fig.savefig(f'timeline_{k}.png',dpi=110); plt.close(fig)
    # track
    fig,a=plt.subplots(figsize=(10,9))
    ri=np.interp(g[:,0],t,rpm,left=np.nan,right=np.nan)
    a.plot(gx,gy,'-',color='#ccc',lw=0.8,zorder=1)
    sc=a.scatter(gx,gy,c=ri,s=6,cmap='viridis',vmin=600,vmax=4000,zorder=2)
    plt.colorbar(sc,ax=a,label='rpm',shrink=0.8)
    eps=[];
    for x in evt:
        if not eps or x-eps[-1]>1: eps.append(x)
    ex=np.interp(eps,g[:,0],gx); ey=np.interp(eps,g[:,0],gy)
    a.plot(ex,ey,'x',color='red',ms=7,mew=1.5,zorder=3,label='link_error episode')
    for i,e in enumerate(EL[k]):
        a.plot(e['x'],e['y'],'o',mfc='none',mec='m',ms=12,mew=1.5,zorder=4)
        a.annotate(f"E{i+1}",(e['x'],e['y']),xytext=(8,6+8*(i%3)),textcoords='offset points',color='m',fontweight='bold')
    a.plot(0,0,'k*',ms=14,zorder=5,label='S (start point)')
    a.plot(gx[0],gy[0],'g^',ms=10,zorder=5,label='drive start'); a.plot(gx[-1],gy[-1],'rs',ms=9,zorder=5,label='drive end')
    a.set_aspect('equal'); a.set_xlabel('metres east of S'); a.set_ylabel('metres north of S'); a.legend(loc='best',fontsize=8)
    a.set_title(f"{NAMES[d]}: GPS track coloured by rpm (metres from S)")
    fig.tight_layout(); fig.savefig(f'track_{k}.png',dpi=110); plt.close(fig)
    # per-block read rate 10 s bins
    fig,a=plt.subplots(figsize=(15,4.5))
    b=np.arange(np.floor(allt[0]),allt[-1]+10,10.0)
    for n,v in sorted(st.items()):
        if not n.startswith('ecu/') or n=='ecu/link' or len(v)<200: continue
        h,_=np.histogram(v,b); a.plot(dt(b[:-1]),h/10,lw=0.8,label=n)
    for x in eps: a.axvline(dt(x)[0],color='red',lw=0.5,alpha=0.6)
    a.set_ylabel('reads/s (10 s bins)'); a.legend(ncol=9,fontsize=7,loc='lower left'); a.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    a.set_title(f'{NAMES[d]}: read rate per block (red lines = link_error episodes)'); a.set_ylim(0,13)
    fig.tight_layout(); fig.savefig(f'readrate_{k}.png',dpi=110); plt.close(fig)
# vss cal + gear
P=np.load('vsscal.npy')
fig,ax=plt.subplots(1,2,figsize=(12,4.5))
ax[0].plot(P[:,0],P[:,1],'o'); xx=np.array([20,110]); ax[0].plot(xx,xx,'k--',label='1:1'); ax[0].plot(xx,xx*1.0113,'r-',label='gps = 1.011 x vss')
ax[0].set_xlabel('vss (ECU), km/h'); ax[0].set_ylabel('GPS speed, km/h'); ax[0].legend(); ax[0].set_title('steady-cruise windows (10 s)')
r=[]
for k,d in DRIVES.items():
    t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss'); s=vss>=12; r.append(rpm[s]/vss[s])
ax[1].hist(np.concatenate(r),bins=np.geomspace(15,200,200),color='#555'); ax[1].set_xscale('log')
for gname,cen,tol in GEARS: ax[1].axvline(cen,color='r',lw=0.8); ax[1].text(cen,ax[1].get_ylim()[1]*0.9,gname,color='r')
ax[1].set_xlabel('rpm / vss (vss >= 12 km/h)'); ax[1].set_title('gear clusters')
fig.tight_layout(); fig.savefig('vss_gear.png',dpi=110)
print('done')
