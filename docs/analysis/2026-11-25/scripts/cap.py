import pickle,numpy as np,datetime as dt
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb')); tz=ZoneInfo('America/Chicago')
f=lambda t:dt.datetime.fromtimestamp(t,tz).strftime('%H:%M:%S.%f')[:-4]
def ts(h): return dt.datetime.fromisoformat('2026-11-25T'+h).replace(tzinfo=tz).timestamp()
src={4:'obd2'}
for h,d in [('18:17:01.9',4),('18:55:15.5',15),('19:29:07.7',20),('19:35:01.1',21),('20:13:45.7',22),('21:16:10.7',27)]:
    t0=ts(h); s='obd2' if d<6 else 'ecu'
    rp=R[s+':rpm']; mm=rp[:,2]==d
    rp=rp[mm]
    before=rp[(rp[:,0]>t0-15)&(rp[:,0]<=t0+15)]
    last=rp[rp[:,0]<=t0+15][-8:]
    nxt=R[s+':rpm'][R[s+':rpm'][:,0]>t0+0.5][:3]
    print('==',h,'drive',d)
    print('  last rpm samples:',[(f(a)[3:],int(b)) for a,b,_ in last])
    print('  next samples in any drive:',[(f(a),int(b),int(c)) for a,b,c in nxt])
    if s=='ecu':
        L=R['ecu:link']; k=(L[:,0]>t0-40)&(L[:,0]<t0+60); print('  link:',[(f(a)[3:],int(b)) for a,b,_ in L[k]])
        st=R['ecu:starter']; k=(st[:,0]>t0)&(st[:,0]<t0+300)&(st[:,1]>0); print('  starter after:',[f(a) for a in st[k,0]])
        for c in ['map','batt','ign','tps']:
            X=R['ecu:'+c]; k=(X[:,0]>t0-6)&(X[:,0]<=t0+15)&(X[:,2]==d); print('  ',c,np.round(X[k,1][-12:],1))
