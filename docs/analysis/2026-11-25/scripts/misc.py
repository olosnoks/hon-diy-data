import pickle,numpy as np,datetime as dt
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb')); tz=ZoneInfo('America/Chicago')
f=lambda t:dt.datetime.fromtimestamp(t,tz).strftime('%H:%M:%S')
rows=pickle.load(open('eps2.pkl','rb'))
def W(c,a,b):
    X=R['ecu:'+c]; m=(X[:,0]>=a)&(X[:,0]<b); return X[m,1]
print('--- SG2 check: entry and exit of each false-cold episode (mean before -> after)')
print('time      dir   ECT        TPS%          MAPkPa        IAT     O2V')
for r in rows:
    for lab,tt in (('in',r['t0']),('out',r['t1']+1)):
        out=[]
        for c in ['ect','tps','map','iat','o2']:
            a=W(c,tt-4,tt-0.01); b=W(c,tt,tt+2.5)
            out.append('%5.1f>%5.1f'%(a.mean() if len(a) else np.nan, b.mean() if len(b) else np.nan))
        print(f(tt),lab.ljust(4),' '.join(out))
tps=R['ecu:tps']; m=tps[:,2]>=12
print('TPS distribution at idle (rpm<1200), drives>=12:',{round(k,1):int(v) for k,v in zip(*np.unique(np.round(tps[m,1],1),return_counts=True)) if v>50})
iat=R['ecu:iat']; print('IAT range drives>=6: %.0f-%.0f'%(iat[:,1].min(),iat[:,1].max()))
print('--- revs: rpm>1400 events, return undershoot')
rp=R['ecu:rpm']; hi=rp[:,1]>1400
i=0
while i<len(rp):
    if not hi[i]: i+=1; continue
    j=i
    while j+1<len(rp) and (hi[j+1] or rp[j+1,0]-rp[j,0]<0.5 and rp[j+1,1]>1100): j+=1
    if rp[i,0]-rp[max(i-1,0),0]<2 and rp[i,2]==rp[j,2]:
        pk=rp[i:j+1,1].max(); t1=rp[j,0]
        post=rp[(rp[:,0]>t1)&(rp[:,0]<t1+10)]
        pre=rp[(rp[:,0]>rp[i,0]-8)&(rp[:,0]<rp[i,0]-1),1]
        if len(post) and len(pre) and rp[i,0]-rp[0,0]>0:
            k=np.argmin(post[:,1]); ia=W('iacv',t1,t1+10)
            print(f(rp[i,0]),'drv%d peak %4.0f pre-idle %4.0f -> min %4.0f (%.1fs after) undershoot %4.0f  iacv min %.1f'%(rp[i,2],pk,np.median(pre),post[k,1],post[k,0]-t1,np.median(pre)-post[k,1],ia.min() if len(ia) else np.nan))
    i=j+1
print('--- batt / eld / alt_fr overall (drives>=6)')
for c in ['batt','eld','alt_fr']:
    X=R['ecu:'+c][:,1]; print(c,'p1 %.1f med %.1f p99 %.1f min %.1f max %.1f'%(np.percentile(X,1),np.median(X),np.percentile(X,99),X.min(),X.max()))
b5=R['math:batt_min_5s'][:,1]; print('batt_min_5s min',np.nanmin(b5))
