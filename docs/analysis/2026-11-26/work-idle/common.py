import numpy as np, datetime as dt
def load(name):
    z=np.load(name+'.npz'); ep=float(z['epoch'])
    d={k:z[k] for k in z.files if k!='epoch'}
    return d,ep
def wall(ep,t): return dt.datetime.fromtimestamp(ep+t).strftime('%H:%M:%S.%f')[:-5]
def at(d,ch,t,mode='prev'):
    a=d[ch]; i=np.searchsorted(a[:,0],t,side='right')-1
    return a[i,1] if i>=0 else np.nan
def win(d,ch,t0,t1):
    a=d[ch]; m=(a[:,0]>=t0)&(a[:,0]<=t1); return a[m]
DR=['drive_2026_11_26_2224','drive_2026_11_26_2251']
