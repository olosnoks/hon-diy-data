import pickle, datetime, numpy as np
G = pickle.load(open('grid.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
t=G['t']; x=G[0x2F]; e=G[0x10]
# glitch list: |d ECT raw| >= 8 within 2 s
gi=[i for i in range(2,len(t)) if not np.isnan(e[i]) and not np.isnan(e[i-2]) and abs(e[i]-e[i-2])>=8 and (i<3 or abs(e[i-1]-e[i-3])<8 or True)]
# compress glitch seconds into events
ev=[]
for i in gi:
    if ev and i-ev[-1][1]<=3: ev[-1][1]=i
    else: ev.append([i,i])
print('n ECT jump events', len(ev))
ch=np.nonzero(np.diff(x)!=0)[0]+1
for k in ch:
    if np.isnan(x[k]) or np.isnan(x[k-1]): 
        print(f(t[k]),'gap'); continue
    prev=[a for a in ev if a[1]<k]
    pj = prev[-1] if prev else None
    s = ''
    if pj:
        a,b=pj; s='last jump %s (%d->%d) %ds ago' % (f(t[a]), e[a-2], e[b], t[k]-t[b])
    print(f(t[k]), int(x[k-1]),'->',int(x[k]),'ECT raw now',int(e[k]),'min/max last 40s',int(np.nanmin(e[k-40:k])),int(np.nanmax(e[k-40:k])), s, 'rpm',round(G['rpm'][k]),'x29',G[0x29][k],'stft',round(G['stft'][k],1),'eld',round(G['eld'][k],1))
