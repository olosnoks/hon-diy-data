from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
f=lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S.%f')[:-4]
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
A=H[0x08]; m=A[:,2]!=1
v=A[:,1].astype(int)
for i in np.where(m&((v&0b1001)!=0))[0]:
    rp,_=at(r,A[i:i+1,0]); 
    print(f(A[i,0]),'d',int(A[i,2]),'0x08=%s'%format(v[i],'08b'),'0x0B=%s'%format(int(H[0x0B][i,1]),'08b'),'0x0A=%s'%format(int(H[0x0A][i,1]),'08b'),'rpm',rp[0])
