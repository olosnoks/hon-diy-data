import pickle, datetime, numpy as np
G = pickle.load(open('grid.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
t=G['t']; x=G[0x2F]; e=G[0x10]; iat=G['iat']
for thr in (53,55,57):
  print('== thr raw <=',thr)
  ok = (e<=thr)
  i=0; n=len(t)
  while i<n:
    if ok[i]:
        j=i
        while j<n and ok[j]: j+=1
        if t[j-1]-t[i]>=45:
            seg=slice(i,j); xs=x[seg]
            first = np.nonzero(xs>0)[0]
            print(f(t[i]),'-',f(t[j-1]), '%4ds'%(t[j-1]-t[i]), 'ECT raw %d-%d'%(np.nanmin(e[seg]),np.nanmax(e[seg])), 'IAT %.0f'%np.nanmean(iat[seg]), 'x2F max', np.nanmax(xs), 'first>0 after %s s'%(first[0] if len(first) else '-'), 'x2F at start', xs[0], 'drive', G['drive'][i])
        i=j
    else: i+=1
