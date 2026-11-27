import csv, numpy as np
rows=list(csv.DictReader(open(r'..\work-sweep\car-214932-wide.csv')))
B=np.array([[int(r['b%02X'%a]) for a in range(256)] for r in rows]); W=[r['wall'][11:19] for r in rows]
s=lambda x: np.where(x>127,x-256,x)
rpm=(B[:,0]*256+B[:,1])/4; tgt=(B[:,0x76]*256+B[:,0x77])/4
x86=s(B[:,0x86])
print('0x80-0x8F unique per addr:', {hex(a): sorted(set(s(B[:,a]))) for a in range(0x80,0x90)})
print('time     rpm  tgt  map stft ltft o2 ect x29 iacv 0x86 0x2A 0x2F 0x27 0x0F 0x9A 0xA4')
for i in range(len(B)):
    if x86[i]==-7 or (i>0 and x86[i-1]==-7) or (i+1<len(B) and x86[i+1]==-7):
        print(W[i], '%4.0f %4.0f %3d %4d %4d %3d %3d %3d %3d %4d %3d %3d %3d %3d %3d %3d'%(rpm[i],tgt[i],B[i,0x12],B[i,0x20],B[i,0x22],B[i,0x15],B[i,0x10],B[i,0x29],B[i,0x28],x86[i],B[i,0x2A],B[i,0x2F],B[i,0x27],B[i,0x0F],B[i,0x9A],B[i,0xA4]))
m=x86==-7
for nm,col in (('rpm',rpm),('tgt',tgt),('map',B[:,0x12]),('stft',B[:,0x20]),('o2',B[:,0x15]),('ect',B[:,0x10]),('x29',B[:,0x29]),('x27',B[:,0x27]),('x21',B[:,0x21]),('x23',B[:,0x23])):
    print(nm, 'when -7: %.1f   when -3: %.1f'%(col[m].mean(), col[~m].mean()))
# next-sweep stft
st=B[:,0x20].astype(float)
print('stft prev sweep when -7 %.1f vs %.1f'%(st[np.r_[False,m[1:]] .nonzero()[0]-1].mean(), st[np.r_[False,~m[1:]].nonzero()[0]-1].mean()))
