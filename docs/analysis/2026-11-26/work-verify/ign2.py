from lib import *
import pickle
H=pickle.load(open('H.pkl','rb'))
E=obd('obd2/0E'); C=obd('obd2/0C'); T=obd('obd2/05'); M=obd('obd2/0B')
# OBD side
rpm_o,dc=at(C,E[:,0]); ect_o,de=at(T,E[:,0]); map_o,dm=at(M,E[:,0])
ok=(dc<1.5)&(de<6)&(dm<3)
O=np.c_[E[ok,1],rpm_o[ok],ect_o[ok],map_o[ok]]
# Honda side: sample at 0x26 times
ig=H[0x26]; r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
rh,d1=at(r,ig[:,0]); eh,d2=at(H[0x10],ig[:,0]); mh,d3=at(H[0x12],ig[:,0]); vs,_=at(H[0x02],ig[:,0])
ok=(d1<0.3)&(d2<2)&(d3<0.3)&(rh>400)
Hh=np.c_[ig[ok,1],rh[ok],ectc(eh[ok]),mh[ok]*0.716-5]
print('n obd',len(O),'n honda',len(Hh))
rows=[]
for e0,e1 in [(15,40),(40,60),(60,75),(75,84),(84,95)]:
  for r0 in range(600,1500,50):
    mo=(O[:,2]>=e0)&(O[:,2]<e1)&(O[:,1]>=r0)&(O[:,1]<r0+50)&(O[:,3]<35)
    mh=(Hh[:,2]>=e0)&(Hh[:,2]<e1)&(Hh[:,1]>=r0)&(Hh[:,1]<r0+50)&(Hh[:,3]<35)
    if mo.sum()>=5 and mh.sum()>=20:
      rows.append((e0,r0,mo.sum(),np.median(O[mo,0]),mh.sum(),np.median(Hh[mh,0])))
for x in rows: print('ect %d-  rpm %d  obd n=%d med %.2f deg | honda n=%d raw %.1f  -> (x-46)/4=%.2f'%(x[0],x[1],x[2],x[3],x[4],x[5],(x[5]-46)/4))
R=np.array(rows); w=np.sqrt(np.minimum(R[:,2],R[:,4]))
A=np.c_[R[:,5],np.ones(len(R))]
sl,ic=np.linalg.lstsq(A*w[:,None],R[:,3]*w,rcond=None)[0]
print('fit deg = %.4f*raw + %.2f  => 1/slope=%.2f counts/deg, zero at raw %.1f'%(sl,ic,1/sl,-ic/sl))
res=R[:,3]-(R[:,5]-46)/4; print('residual of (x-46)/4: mean %.2f sd %.2f'%(res.mean(),res.std()))
for k in (2,2.844,4,4.266,5):
  b=np.median(R[:,5]-k*R[:,3]); rr=R[:,3]-(R[:,5]-b)/k; print('slope 1/%.3f best offset %.1f rms %.2f'%(k,b,np.sqrt((rr**2).mean())))
# overall warm idle
mo=(O[:,2]>=80)&(O[:,1]<720)&(O[:,3]<30); mh=(Hh[:,2]>=80)&(Hh[:,1]<720)&(Hh[:,3]<30)
print('warm idle obd med %.2f IQR %s n=%d ; honda raw med %.1f IQR %s n=%d'%(np.median(O[mo,0]),np.percentile(O[mo,0],[25,75]),mo.sum(),np.median(Hh[mh,0]),np.percentile(Hh[mh,0],[25,75]),mh.sum()))
# high rpm / range
print('honda raw max',Hh[:,0].max(),'at rpm',Hh[Hh[:,0].argmax(),1],'map',Hh[Hh[:,0].argmax(),3])
print('obd max',O[:,0].max(),'at',O[O[:,0].argmax()])
