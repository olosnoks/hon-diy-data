import numpy as np, load, datetime
d='drive_2026_11_26_2045'
B,R,L,ep=load.load(d)
G=load.grid(B,R)
wall=lambda t: datetime.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S')
t=G['t']
# ELD spikes
tt,v=B[0x19]
from numpy.lib.stride_tricks import sliding_window_view
med=np.array([np.median(v[max(0,i-30):i+1]) for i in range(len(v))])
sp=(med-v)>=6
st=tt[1:][np.diff(sp.astype(int))==1]
print('ELD spikes',len(st),'intervals',np.round(np.diff(st)))
for s in st:
    i=np.searchsorted(tt,s); print(wall(s),'%.1f'%s,'eld raw %d->%d'%(med[i],v[i:i+4].min()), 'A=%.1f'%(77.06-v[i:i+4].min()/2.5371))
# link drop times
lo=L[L[:,1]==0][:,0]
print('link drops',len(lo))
# distance from each link drop to nearest ELD spike
dd=np.array([np.min(np.abs(st-x)) for x in lo])
print('link drop to nearest ELD spike: frac within 3s %.2f; expected by chance %.2f'%((dd<3).mean(), min(1,len(st)*6/1715)))
iv=np.diff(lo); print('link drop intervals median %.1f, quantiles'%np.median(iv), np.percentile(iv,[10,25,50,75,90]))
# IACV kicks: 0x28 jump >=5 within 1 read
tt,v=B[0x28]; k=np.where(np.diff(v)>=5)[0]
print('IACV up-steps>=5:',len(k),[ (wall(tt[i+1]),int(v[i]),int(v[i+1])) for i in k][:40])
