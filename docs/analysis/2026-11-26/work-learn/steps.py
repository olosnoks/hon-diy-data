import pickle, datetime, numpy as np
S, drives = pickle.load(open('series.pkl','rb'))
G = pickle.load(open('grid.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
t,v,d = S['rpm']
for did,name,st,en in drives:
    m = d==did
    if m.sum(): print(did, name, 'rpm min', v[m].min(), 'first 5', v[m][:5], 'last', v[m][-3:])
tg=G['t']; ect=G[0x10]
gl = np.zeros(len(tg),bool); gl[1:] = np.abs(np.diff(ect))>8
gl2 = gl.copy(); gl2[2:] |= np.abs(ect[2:]-ect[:-2])>8
last=-1e9; tsl=np.full(len(tg),np.nan)
for i in range(len(tg)):
    if gl2[i]: last=tg[i]
    tsl[i]=tg[i]-last
G['tsl']=tsl
pickle.dump(G, open('grid.pkl','wb'))
x=G[0x2A]; i=np.nonzero(np.diff(x)!=0)[0]+1
print('time  from->to ectraw ect°C ectmin/max60 tsl  x29m30 iacv rpm  tgt stft30 ltft iat batt eld x2F drive')
for k in i:
    if np.isnan(x[k-1]) or np.isnan(x[k]): continue
    w=slice(max(0,k-30),k); w60=slice(max(0,k-60),k)
    print(f(tg[k]), int(x[k-1]),'->',int(x[k]), int(ect[k]), round(G['ect'][k]), int(np.nanmin(ect[w60])), int(np.nanmax(ect[w60])), int(tsl[k]) if tsl[k]<1e6 else -1,
          round(np.nanmean(G[0x29][w]),1), round(np.nanmean(G[0x28][w]),1), round(np.nanmean(G['rpm'][w])), G['tgt'][k], round(np.nanmean(G['stft'][w]),1), round(G['ltft'][k],1), round(G['iat'][k]), round(G['batt'][k],2), round(np.nanmean(G['eld'][w]),1), G[0x2F][k], G['drive'][k])
