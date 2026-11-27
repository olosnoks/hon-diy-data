from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
I=H[0x26]; rp,dr=at(r,I[:,0])
for lo,hi in ((0,1),(1,400),(400,600)):
    s=(dr<0.3)&(rp>=lo)&(rp<hi)&(I[:,2]!=1)
    if s.any(): print('rpm',lo,hi,'n',s.sum(),'ign raw',np.unique(I[s,1],return_counts=True))
