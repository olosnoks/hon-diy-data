from prep import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
P='/home/claude/analysis/plots-sensors/'
w=W(); o=pd.read_pickle('obd2.pkl'); g=pd.read_pickle('pid_ign.pkl')
f,ax=plt.subplots(2,2,figsize=(14,9))
s=g[(g.ect>=75)&(g.rpm.between(640,710))]
h=w[(w.ect.between(75,92))&(w.rpm.between(640,710))&(w.ac==0)]
bins=np.arange(4,24,0.5)
ax[0,0].hist(s.ign,bins=bins,density=True,alpha=.6,label='PID 0E (warm idle 640-710 rpm, n=%d)'%len(s))
for lab,fn in [('(x-46)/4 proposed',lambda x:(x-46)/4),('(x-24)/4 kerpz',lambda x:(x-24)/4),('(x-28)/2.844',lambda x:(x-28)/2.844)]:
    ax[0,0].hist(fn(h.a26),bins=bins,density=True,histtype='step',lw=1.5,label='0x26 '+lab)
ax[0,0].axvspan(10,14,color='g',alpha=.08,label='manual 12±2 (p.356/1575)'); ax[0,0].legend(fontsize=8); ax[0,0].set_xlabel('deg BTDC'); ax[0,0].set_title('Timing at warm idle')
p=o[o.pid=='obd2/14']; hh=w[(w.ect.between(75,92))&(w.rpm.between(640,720))&(w.cl==1)]
b2=np.arange(0,1.2,0.025)
ax[0,1].hist(p.v0,bins=b2,density=True,alpha=.6,label='PID 14 A/200'); ax[0,1].hist(hh.a15/51.3,bins=b2,density=True,histtype='step',lw=1.5,label='0x15 x/51.3')
ax[0,1].legend(); ax[0,1].set_xlabel('V'); ax[0,1].set_title('Primary O2 at warm idle, closed loop')
ww=w[(w.ect.between(75,92))]
ax[1,0].plot(ww.rpm,(ww.a26-46)/4,',',alpha=.3,label='0x26 (x-46)/4, true-warm ECT')
c=w[w.ect<45]; ax[1,0].plot(c.rpm,(c.a26-46)/4,',',c='r',alpha=.3,label='false-cold ECT (<45 C)')
ax[1,0].plot(g.rpm,g.ign,'k.',ms=3,label='PID 0E'); ax[1,0].set_xlim(550,2800); ax[1,0].set_xlabel('rpm'); ax[1,0].set_ylabel('deg'); ax[1,0].legend(markerscale=20,fontsize=8); ax[1,0].set_title('Timing vs rpm')
r=pd.read_pickle('reads.pkl'); r=r[(r.d>=27)&(r.block!='ecu/link')].sort_values('unix'); r['loc']=pd.to_datetime(r.unix,unit='s')-pd.Timedelta(hours=6)
dt=r.groupby('d').unix.diff(); ax[1,1].plot(r['loc'],dt,'.',ms=2); ax[1,1].set_yscale('log'); ax[1,1].axhline(1,c='r',lw=.5); ax[1,1].set_title('Inter-read interval, drives 27-29 (all gaps >0.9 s contain a link-down)'); ax[1,1].set_ylabel('s')
for a in ax.flat: a.grid(alpha=.3)
plt.tight_layout(); plt.savefig(P+'decode_and_gaps.png',dpi=80)
