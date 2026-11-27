from lib import *
import pickle
H=pickle.load(open('H.pkl','rb'))
ig=H[0x26]; r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
rh,d1=at(r,ig[:,0]); eh,d2=at(H[0x10],ig[:,0]); mh,d3=at(H[0x12],ig[:,0])
ok=(d1<0.3)&(d2<2)&(d3<0.3); e=ectc(eh); m=mh*0.716-5
def q(r0,r1,m0,m1,e0=75):
    s=ok&(rh>=r0)&(rh<r1)&(m>=m0)&(m<m1)&(e>=e0)
    x=ig[s,1]; 
    if len(x)==0: print(r0,r1,m0,m1,'none'); return
    med=np.median(x); print('rpm %d-%d map %d-%d n=%d raw med %.1f IQR %s  (x-46)/4=%.1f (x-48)/4=%.1f (x-45)/4.27=%.1f (x-38)/5=%.1f'%(r0,r1,m0,m1,len(x),med,np.percentile(x,[25,75]),(med-46)/4,(med-48)/4,(med-45.3)/4.266,(med-38)/5))
q(1150,1220,18,23); q(1000,1060,24,30); q(640,700,23,28)
q(1500,1700,12,20); q(2000,2200,12,20); q(2500,2700,12,20)
for lo in range(600,2800,200):
    q(lo,lo+200,0,200,0)
