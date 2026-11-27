from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
f=lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S')
B=H[0x13]; M=H[0x12]
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
for d in sorted(set(B[:,2].astype(int))):
    s=B[:,2]==d; v=B[s,1]
    print('drive',d,f(B[s,0][0]),'baro raw',dict(zip(*np.unique(v,return_counts=True))))
# key-on engine-off: rpm==0
t=M[:,0]; rh,dr=at(r,t)
k=(rh==0)&(dr<0.3)
print('engine-off MAP samples',k.sum())
for d in sorted(set(M[k,2].astype(int))):
    s=k&(M[:,2]==d); bb,_=at(B,M[s,0])
    print(' drive',d,f(M[s,0][0]),'n',s.sum(),'MAP raw med',np.median(M[s,1]),'range',M[s,1].min(),M[s,1].max(),'baro raw',np.unique(bb))
# changes of baro
ch=np.where(np.diff(B[:,1])!=0)[0]
print('baro changes',len(ch)); 
for i in ch[:20]: print(f(B[i,0]),B[i,1],'->',B[i+1,1],'drive',B[i+1,2])
# WOT max MAP
print('max MAP raw',M[:,1].max())
c=con(); g=np.array(c.execute('select unix_time,lat,lon,sats,hdop from gps_track where lat is not null').fetchall())
print('gps rows',len(g))
