import numpy as np, load, datetime, sys
d=sys.argv[1]; t0=float(sys.argv[2]); t1=float(sys.argv[3]); step=float(sys.argv[4]) if len(sys.argv)>4 else 1
B,R,L,ep=load.load(d)
cols=[0x2F,0x08,0x0A,0x0B,0x0F,0x10,0x11,0x12,0x15,0x17,0x18,0x19,0x1C,0x20,0x22,0x26,0x27,0x28,0x29,0x2A,0x32,0x37,0x3C]
print('t       wall      rpm   '+' '.join('%4s'%load.name(a)[:4] for a in cols))
for t in np.arange(t0,t1,step):
    r=load.at(R,np.array([t]))[0]
    vals=[load.at(B[a],np.array([t]))[0] for a in cols]
    print('%7.1f %s %5.0f '%(t,datetime.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S'),r)+' '.join('%4.0f'%v for v in vals))

