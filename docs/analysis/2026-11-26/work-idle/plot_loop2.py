import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from common import *
import csv
A=np.load('loopA.npy')
f,ax=plt.subplots(1,3,figsize=(19,5.2))
allic=[];alliv=[];st=[]
for name in DR:
    d,ep=load(name); ic=d['iac_cmd']; iv=d['iacv']; n=min(len(ic),len(iv))
    allic.append(ic[:n,1]); alliv.append(iv[:n,1])
    for k in range(n):
        t1=ic[k,0]
        if at(d,'vss',t1)==0 and at(d,'tps',t1)<8.7 and at(d,'rpm',t1)>600: st.append(ic[k,1])
ic=np.concatenate(allic); iv=np.concatenate(alliv)
p=np.polyfit(ic[ic<200],iv[ic<200],1); print('iacv = %.3f*iac %+.1f -> zero at %.1f'%(p[0],p[1],-p[1]/p[0]))
ax[0].scatter(ic,iv,s=3,alpha=.4); xx=np.linspace(65,220); ax[0].plot(xx,np.polyval(p,xx),'r',lw=.8,label=f'iacv = {p[0]:.2f}*(iac - {-p[1]/p[0]:.0f})')
ax[0].set_xlabel('iac_cmd (0x29)'); ax[0].set_ylabel('iacv output (0x28)'); ax[0].legend(); ax[0].grid(alpha=.3); ax[0].set_title('command -> valve output, both drives')
ax[1].scatter(A[:,4],A[:,3],s=5,alpha=.5); ax[1].set_xlim(-420,450); ax[1].grid(alpha=.3)
ax[1].set_xlabel('idle target - mean rpm over the preceding ~1 s'); ax[1].set_ylabel('iac_cmd change at this read (per ~1.04 s)')
ax[1].set_title('idle loop law at standstill, throttle closed (n=%d)'%len(A))
ax[2].hist(st,bins=np.arange(65,225,2),label='standstill, throttle closed, rpm>600',color='C1')
ax[2].hist(ic,bins=np.arange(65,225,2),histtype='step',color='k',label='all reads (incl. driving)')
ax[2].axvline(-p[1]/p[0],c='r',lw=.8,label='valve output ~0')
ax[2].set_xlabel('iac_cmd'); ax[2].legend(); ax[2].set_title('where the idle sits in the valve range'); ax[2].grid(alpha=.3)
plt.tight_layout(); plt.savefig('loop_scatter.png',dpi=75)
st=np.array(st); print('standstill iac pctiles',np.percentile(st,[1,5,50,95,99]), 'all max',ic.max(),'min',ic.min())
