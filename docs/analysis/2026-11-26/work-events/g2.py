import numpy as np, load, datetime
B,R,L,ep=load.load('drive_2026_11_26_2045')
rt,rv=R
m=(rt>1525)&(rt<1560)
for t,v in zip(rt[m],rv[m]): print('%.2f %s %6.0f'%(t,datetime.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S.%f')[:-4],v), end=' | ')
print()
print(L[(L[:,0]>1520)&(L[:,0]<1560)])
