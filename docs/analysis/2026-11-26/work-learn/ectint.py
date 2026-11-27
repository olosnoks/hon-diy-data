import csv, numpy as np
rows=list(csv.DictReader(open(r'..\work-sweep\car-214932-wide.csv')))
B=np.array([[int(r['b%02X'%a]) for a in range(256)] for r in rows]).astype(float)
e=B[:,0x10]; ok=e<100
tgt=(B[:,0x76]*256+B[:,0x77])/4
print('ECT raw ok-range', e[ok].min(), e[ok].max(), 'n ok', ok.sum())
for a in range(256):
    x=B[:,a]
    if x.std()==0 or a==0x10: continue
    r_all=np.corrcoef(x,e)[0,1]; r_ok=np.corrcoef(x[ok],e[ok])[0,1] if x[ok].std()>0 else 0
    r_t=np.corrcoef(x,tgt)[0,1]
    if abs(r_ok)>0.4 or abs(r_t)>0.5: print(hex(a),'r(ECT all) %.2f r(ECT ok) %.2f r(target) %.2f range %d-%d'%(r_all,r_ok,r_t,x.min(),x.max()))
