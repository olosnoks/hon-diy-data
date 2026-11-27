import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from common import *
import datetime as dt
def tt(name,hms):
    d,ep=load(name); h,m,s=hms.split(':')
    return dt.datetime.fromtimestamp(ep).replace(hour=int(h),minute=int(m),second=0,microsecond=0).timestamp()+float(s)-ep
EV=[('2224','22:39:41.3','A1 no throttle, 546'),('2251','22:53:15.9','A2 after aborted bite + IAC kick/unwind, 463'),('2251','23:12:37.5','A3 throttle late, 554'),
    ('2251','23:25:56.2','A4 bite during rev-hang decay, 519'),('2251','23:26:12.5','A5 bite while IAC unwinds, 495'),('2251','23:26:24.2','A6 blip then closed, 440'),
    ('2251','22:54:22.8','B1 throttle at bite, 670'),('2251','22:58:05.5','B2 throttle at bite, 702'),('2251','23:03:01.0','B3 throttle at bite, 906'),
    ('2224','22:35:57.7','C1 ELD +16 A step at idle, 721'),('2251','22:57:58.0','D1 arrival at stop (rev-hang)'),('2251','23:12:31.0','D2 arrival at stop')]
def panel(evs,fn,pre=6,post=4):
    n=len(evs); cols=3; rows=(n+cols-1)//cols
    f,axs=plt.subplots(rows,cols,figsize=(18,4.2*rows)); axs=axs.ravel()
    for ax,(dr,hms,title) in zip(axs,evs):
        name=[x for x in DR if x.endswith(dr)][0]; d,ep=load(name); t0=tt(name,hms)
        g=lambda ch: win(d,ch,t0-pre,t0+post)
        r=g('rpm'); ax.plot(r[:,0]-t0,r[:,1],'k',lw=1.2,label='rpm')
        tg=g('idle_target'); ax.step(tg[:,0]-t0,tg[:,1],'r--',lw=1,where='post',label='idle target')
        ax.axhline(400,c='grey',lw=.6,ls=':'); ax.set_ylim(350,1600); ax.set_ylabel('rpm')
        ax2=ax.twinx()
        ic=g('iac_cmd'); ax2.step(ic[:,0]-t0,ic[:,1],'C0',where='post',lw=1.5,label='iac_cmd')
        m=g('map'); ax2.plot(m[:,0]-t0,m[:,1]+50,'C2',lw=.8,label='MAP+50 kPa')
        tp=g('tps'); ax2.plot(tp[:,0]-t0,(tp[:,1]-7.84)*10+40,'C3',lw=.8,label='(tps-7.8)x10+40')
        vs=g('vss'); ax2.plot(vs[:,0]-t0,vs[:,1]*3+40,'C4',lw=.8,label='vss x3 +40')
        ax2.set_ylim(30,200)
        ax.set_title(f'{dr} {hms}  {title}',fontsize=9); ax.grid(alpha=.3)
    axs[0].legend(loc='upper left',fontsize=7); ax2.legend(loc='upper right',fontsize=7)
    for ax in axs[n:]: ax.axis('off')
    plt.tight_layout(); plt.savefig(fn,dpi=75)
panel(EV,'events_takeups.png')
