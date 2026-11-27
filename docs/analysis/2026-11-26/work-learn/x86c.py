import csv, numpy as np
rows=list(csv.DictReader(open(r'..\work-sweep\car-214932-wide.csv')))
B=np.array([[int(r['b%02X'%a]) for a in range(256)] for r in rows])
x86=B[:,0x86]==249
rpm=(B[:,0]*256+B[:,1])/4; tgt=(B[:,0x76]*256+B[:,0x77])/4; err=rpm-tgt
ign=(B[:,0x26]-46)/4
print('ign when -7 %.2f else %.2f'%(ign[x86].mean(), ign[~x86].mean()))
print('rpm-tgt when -7 %.1f else %.1f'%(err[x86].mean(), err[~x86].mean()))
from itertools import product
for lo in (-20,0,10,20,30):
    m=err>lo; print('err>%d: -7 %d of %d'%(lo,np.sum(m&x86),m.sum()))
print('inj when -7', (B[x86,0x24]*256+B[x86,0x25]).mean()/250, 'else', (B[~x86,0x24]*256+B[~x86,0x25]).mean()/250)
