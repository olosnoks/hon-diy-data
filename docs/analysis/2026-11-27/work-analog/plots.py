import pickle,sqlite3,numpy as np,matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
from load import *
F=pickle.load(open('frame.pkl','rb'))
C={0x32:'#1f77b4',0x37:'#d62728'}
def rate(k,col,bins):
    d=F[k];x=d[col];y=d['v']==44;out=[]
    for lo,hi in zip(bins[:-1],bins[1:]):
        m=(x>=lo)&(x<hi)
        if m.sum()>=30:
            p=y[m].mean();out.append(((lo+hi)/2,100*p,100*1.96*np.sqrt(p*(1-p)/m.sum()+1e-9)))
    return np.array(out)
fig,ax=plt.subplots(2,3,figsize=(14,7.5))
for a,(col,bins,lab) in zip(ax.flat,[('rpm',[400,600,700,800,900,1000,1200,1500,1800,2200,2600,3200,4500],'rpm'),
    ('map',[20,35,40,45,50,60,80,100,120,160],'MAP raw (x*0.716-5 kPa)'),
    ('batt',[140,145,146,147,148,149,150,152],'battery raw (x/10.45 V)'),
    ('ect',[30,40,45,50,55,60,70,90,120,200],'ECT raw (higher = colder)'),
    ('iat',[80,90,100,110,120,130,140,150,160,170],'IAT raw (higher = colder)'),
    ('vss',[0,1,20,40,60,80,100,140],'vss km/h')]):
    for k in (0x32,0x37):
        r=rate(k,col,bins)
        a.errorbar(r[:,0],r[:,1],yerr=r[:,2],marker='o',ms=4,capsize=2,color=C[k],label=f'{k:#04x}')
    a.set_xlabel(lab);a.set_ylabel('% of reads at 44');a.set_ylim(0,16);a.grid(alpha=.3)
ax[0,0].legend()
fig.suptitle('0x32 / 0x37: share of reads at 44 (else 45), all 11-26 drives 20:45-23:27, 95% CI')
fig.tight_layout();fig.savefig('01_flicker_vs_channels.png',dpi=110)

# timeline: 1-min dip rate and battery
fig,ax=plt.subplots(3,1,figsize=(14,8),sharex=True)
for k in (0x32,0x37):
    d=F[k];w=d['wall'];y=(d['v']==44).astype(float)
    b=np.arange(w.min(),w.max()+300,300);idx=np.digitize(w,b)
    xs=[];ys=[]
    for i in np.unique(idx):
        m=idx==i
        if m.sum()>=60: xs.append((w[m].mean()-w.min())/60);ys.append(100*y[m].mean())
    ax[0].plot(xs,ys,'o-',color=C[k],label=f'{k:#04x} % at 44 (5-min bins)')
    ax[1].plot((w-w.min())/60,d['v']+(0.1 if k==0x37 else 0),'.',ms=1.5,color=C[k],label=f'{k:#04x} raw')
d=F[0x32];w=(d['wall']-d['wall'].min())/60
ax[2].plot(w,d['batt']/10.45,'.',ms=1,color='k',label='battery V');a2=ax[2].twinx()
a2.plot(w,ECT(d['ect']),'.',ms=1,color='tab:orange',label='ECT °C');a2.plot(w,ECT(d['iat']),'.',ms=1,color='tab:green',label='IAT °C')
ax[0].legend();ax[1].legend();ax[2].legend(loc='lower left');a2.legend(loc='lower right');ax[1].set_ylim(43,46)
ax[2].set_ylim(10,15.5);a2.set_ylim(0,120);ax[2].set_xlabel('minutes since 20:45:18 (wall time; gaps = no logging)')
for a in ax: a.grid(alpha=.3)
fig.suptitle('0x32 / 0x37 over the evening: no drift with time, battery, ECT or IAT')
fig.tight_layout();fig.savefig('02_timeline.png',dpi=110)

# start of drive 39
c=sqlite3.connect(DB,uri=True);r=load(c,39)
fig,ax=plt.subplots(4,1,figsize=(11,8),sharex=True)
t0=1591
def s(a):
    t,v,_=r[a];m=(t>t0)&(t<t0+40);return t[m]-t0,v[m]
t,hi=s(0);_,lo=s(1);ax[0].plot(t,(hi.astype(int)*256+lo)/4,'k.-',ms=3);ax[0].set_ylabel('rpm')
for a,cl in [(0x3A,'tab:purple'),(0x32,C[0x32]),(0x37,C[0x37])]:
    t,v=s(a);ax[1].step(t,v,where='post',color=cl,label=f'{a:#04x}')
ax[1].set_ylabel('raw');ax[1].legend(ncol=3);ax[1].set_ylim(20,48)
t,v=s(0x15);ax[2].plot(t,v/51.3,'g.-',ms=3,label='front O2 V (0x15)');ax[2].legend();ax[2].set_ylabel('V')
t,v=s(0x9A);ax[3].step(t,v,where='post',label='0x9A fuel status (1 open, 2 closed)');t,v=s(0x17);ax[3].plot(t,v/10.45/7,'k.',ms=3,label='battery V / 7')
ax[3].legend();ax[3].set_xlabel('s after 22:51:09 (first read = cranking, 185 rpm, 10.6 V)')
for a in ax: a.grid(alpha=.3)
fig.suptitle('Engine start 22:51: 0x3A reads 24 for 8 reads, back to 25 one read after the front O2 leaves its 3.9 V rail')
fig.tight_layout();fig.savefig('03_start_2251.png',dpi=110)

# purge vs ECT
z=np.load('purge.npz');E,P,V,EM=z['E'],z['P'],z['V'],z['EM']
fig,ax=plt.subplots(figsize=(9,4.5))
es=np.arange(40,66)
for arr,lab,cl in [(E,'ECT raw at the read','tab:blue'),(EM,'coldest ECT raw in the 20 s before','tab:gray')]:
    fr=[P[(V==0)&(arr==e)].astype(bool).mean() if ((V==0)&(arr==e)).sum()>=15 else np.nan for e in es]
    ax.plot(es,fr,'o-',label=lab,color=cl)
ax.axvline(55,color='tab:red',ls='--',label='0x33 = 55 (67.5 °C)')
ax.axvline(54.3,color='tab:orange',ls=':',label='manual purge enable 68 °C = raw 54.3')
ax.set_xlabel('ECT raw (higher = colder)');ax.set_ylabel('share of stationary reads with 0x2F > 0');ax.legend();ax.grid(alpha=.3)
ax.set_title('Purge ramp 0x2F only runs on the warm side of raw ~57: 0x33 = 55 sits on that edge')
fig.tight_layout();fig.savefig('04_purge_vs_ect.png',dpi=110)
print('ok')
