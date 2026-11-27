from load import *
import math
o=np.load('origin.npy')
R=6371008.8
for k in DRIVES:
    g=np.load(f'grid_{k}.npz')
    ok=np.isfinite(g['glat'])
    x=np.radians(g['glon']-o[1])*R*math.cos(math.radians(o[0])); y=np.radians(g['glat']-o[0])*R
    print(k,'start',round(x[0]),round(y[0]),'end',round(x[-1]),round(y[-1]),'max dist',round(np.hypot(x,y).max()))
# rpm/vss from fast data
allr=[]
for k,d in DRIVES.items():
    t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss')
    s=vss>=12; allr.append(rpm[s]/vss[s])
r=np.concatenate(allr)
h,e=np.histogram(np.log(r),bins=200,range=(np.log(15),np.log(200)))
for i in range(1,199):
    if h[i]>=h[i-1] and h[i]>=h[i+1] and h[i]>150: print('peak ratio',round(math.exp((e[i]+e[i+1])/2),1),h[i])
circ=math.pi*(14*25.4/1000+2*0.185*0.65)
for gname,gr in zip('12345',[3.25,1.761,1.172,0.909,0.702]):
    print('theory',gname, round(gr*4.25*1000/60/circ,1))
