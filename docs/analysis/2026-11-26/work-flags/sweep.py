import csv, numpy as np
P=r'C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-sweep/car-214932-wide.csv'
R=list(csv.DictReader(open(P)))
print(len(R), R[0]['wall'], R[-1]['wall'])
def col(a): return np.array([int(r[f'b{a:02X}']) if r[f'b{a:02X}'] not in ('',None) else -1 for r in R])
def ect(x):
    k=[155.04149,-3.0414878,0.03952185,-0.00029383913,0.0000010792568,-0.0000000015618437]
    return sum(ki*x**i for i,ki in enumerate(k))
rpm=(col(0)*256+col(1))/4; it=(col(0x76)*256+col(0x77))/4; E=np.array([ect(x) for x in col(0x10)])
for a in range(256):
    v=col(a); v=v[v>=0]
    orv=np.bitwise_or.reduce(v); andv=np.bitwise_and.reduce(v); vb=orv&~andv
    nb=bin(vb).count('1')
    if 0<nb<=3 or (0x08<=a<=0x0F) or a in (0x9A,0xA4,0xAD):
        u,c=np.unique(v,return_counts=True)
        print(f"0x{a:02X} varbits {vb:08b} values", dict(zip(u.tolist(),c.tolist()))[:0] if False else dict(zip(u.tolist(),c.tolist())))
w=[r['wall'] for r in R]
a4=col(0xA4)&1
print("0xA4 b0 transitions:")
for i in range(1,len(R)):
    if a4[i]!=a4[i-1]: print(' ', w[i], a4[i-1],'->',a4[i], f"rpm {rpm[i]:.0f} itgt {it[i]:.0f} ect {E[i]:.1f} (prev {E[i-1]:.1f}) eld {col(0x19)[i]} 0x27 {col(0x27)[i]} iacv {col(0x28)[i]} 0x86 {col(0x86)[i]}")
print("0xA4 b0 vs rpm/itgt/ect: set mean rpm", rpm[a4==1].mean(), "itgt", it[a4==1].min(), it[a4==1].max(), "; clear itgt range", it[a4==0].min(), it[a4==0].max())
for lo,hi in [(0,900),(900,1000),(1000,1040),(1040,1200)]:
    m=(it>=lo)&(it<hi); print(f"  itgt {lo}-{hi}: n {m.sum()} a4 set {a4[m].sum()}")
    m=(rpm>=lo)&(rpm<hi); print(f"  rpm  {lo}-{hi}: n {m.sum()} a4 set {a4[m].sum()}")
# ECT glitch timeline + itgt changes
print("ECT jumps >15C and idle-target steps:")
for i in range(1,len(R)):
    s=[]
    if abs(E[i]-E[i-1])>15: s.append(f"ECT {E[i-1]:.0f}->{E[i]:.0f}")
    if it[i]!=it[i-1]: s.append(f"itgt {it[i-1]:.0f}->{it[i]:.0f}")
    if s: print(' ', w[i], ', '.join(s), f"rpm {rpm[i]:.0f} a4 {a4[i]} 08..0F {[int(col(b)[i]) for b in range(8,16)]}")
print("== rpm - itgt by A4 state")
dif=rpm-it
for st in (0,1):
    m=a4==st; print(st, np.percentile(dif[m],[0,10,50,90,100]).round(0))
m=(rpm>=1015); print("rpm>=1015 sweeps", m.sum(), "a4 set among them", a4[m].sum())
for i in range(len(R)):
    if '21:50:4' in w[i] or '21:50:5' in w[i] or '21:51:3' in w[i] or '21:51:4' in w[i] or '21:52:0' in w[i]:
        print(' ', w[i][11:19], f"rpm {rpm[i]:.0f} itgt {it[i]:.0f} a4 {a4[i]} x27 {col(0x27)[i]} iacv {col(0x28)[i]} x29 {col(0x29)[i]} ect {E[i]:.0f} stft {col(0x20)[i]} o2 {col(0x15)[i]} 86 {col(0x86)[i]}")
b86=(col(0x86)>>2)&1
print("0x86 b2 clear (249) sweeps:", [w[i][11:19] for i in range(len(R)) if b86[i]==0])
