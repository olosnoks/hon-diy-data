import numpy as np, load, datetime
B,R,L,ep=load.load('drive_2026_11_26_2114')
rt,rv=R; print('rpm min overall %.0f; last read %s; rpm<600 samples %d'%(rv.min(), datetime.datetime.fromtimestamp(ep+rt[-1]).strftime('%H:%M:%S'), (rv<600).sum()))
allt=np.sort(np.concatenate([B[a][0] for a in B])); d=np.diff(allt); print('read gaps >1.2 s:',[('%.1f'%allt[i],'%.1f'%d[i]) for i in np.where(d>1.2)[0]])
