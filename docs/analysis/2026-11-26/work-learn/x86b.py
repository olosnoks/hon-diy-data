import csv, numpy as np
rows=list(csv.DictReader(open(r'..\work-sweep\car-214932-wide.csv')))
B=np.array([[int(r['b%02X'%a]) for a in range(256)] for r in rows])
x86=B[:,0x86]==249
o2=B[:,0x15]; st=B[:,0x20].astype(int)
for th in (25,30,35,40):
    r=o2>=th
    print('o2>=%d'%th, 'rich&-7',np.sum(r&x86),'rich&-3',np.sum(r&~x86),'lean&-7',np.sum(~r&x86),'lean&-3',np.sum(~r&~x86))
dst=np.r_[0,np.diff(st)]
print('stft change this sweep when -7', dst[x86].mean(), 'else', dst[~x86].mean())
for th in (136,138,140):
    h=st>=th; print('stft>=%d'%th,'h&-7',np.sum(h&x86),'h&-3',np.sum(h&~x86),'l&-7',np.sum(~h&x86))
both=(o2>=35)&(st>=136)
print('o2>=35 & stft>=136: -7',np.sum(both&x86),' -3',np.sum(both&~x86),' ; not both & -7', np.sum(~both&x86))
# o2 rising edge (lean->rich since previous sweep)
re=np.r_[False,(o2[1:]>=30)&(o2[:-1]<30)]
print('rising edge & -7',np.sum(re&x86),'rising & -3',np.sum(re&~x86),'not rising & -7',np.sum(~re&x86))
