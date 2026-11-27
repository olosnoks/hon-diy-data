from lib import *
import pickle
H=pickle.load(open('H.pkl','rb'))
P=obd('obd2/14'); C=obd('obd2/0C'); T=obd('obd2/05')
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
O=H[0x15]; rp,dr=at(r,O[:,0]); cl,dc=at(H[0x0F],O[:,0]); ec,_=at(H[0x10],O[:,0])
s=(O[:,2]!=1)&(dr<0.3)&(rp>550)&(rp<760)&(dc<2)&((cl.astype(int)&1)==1)&(ectc(ec)>75)
ro,_=at(C,P[:,0]); eo,_=at(T,P[:,0]); so=(ro<760)&(eo>75)
q=[1,5,10,25,50,75,90,95,99]
print('Honda 0x15/51.3 n=%d'%s.sum(), np.round(np.percentile(O[s,1]/51.3,q),3), 'frac>0.45 %.3f'%(O[s,1]/51.3>0.45).mean())
print('PID 14        n=%d'%so.sum(), np.round(np.percentile(P[so,1],q),3), 'frac>0.45 %.3f'%(P[so,1]>0.45).mean())
k=O[(O[:,2]!=1)&(dr<0.3)&(rp<550)]; print('O2 raw at rpm<550', np.percentile(k[:,1],[5,50,95]) if len(k) else None)
