import pickle,numpy as np,datetime as dt
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb'))
tz=ZoneInfo('America/Chicago')
f=lambda t:dt.datetime.fromtimestamp(t,tz).strftime('%H:%M:%S')
e=R['ecu:ect']; t=e[:,0]; v=e[:,1]; d=e[:,2]
cls=np.where(v<55,'C',np.where(v<76,'P','W'))
# episodes of non-W, merging gaps <=5s of W within same drive  (only C-containing kept as false-cold episodes)
eps=[];i=0;n=len(t)
while i<n:
    if cls[i]!='C': i+=1; continue
    j=i
    while True:
        k=j+1
        # extend over up to 5 s of non-C if C resumes
        while k<n and cls[k]!='C' and t[k]-t[j]<=5 and d[k]==d[j]: k+=1
        if k<n and cls[k]=='C' and d[k]==d[j] and t[k]-t[j]<=6: j=k
        else: break
    eps.append((i,j))
    i=j+1
rows=[]
def win(ch,a,b):
    A=R['ecu:'+ch]; m=(A[:,0]>=a)&(A[:,0]<b); return A[m,1]
for (i,j) in eps:
    t0,t1=t[i],t[j]
    # sample before / after
    pre=v[i-1] if i>0 and d[i-1]==d[i] and t[i]-t[i-1]<5 else np.nan
    post=v[j+1] if j+1<n and d[j+1]==d[j] and t[j+1]-t[j]<5 else np.nan
    r=dict(start=f(t0),end=f(t1),dur=t1-t0+1,drive=int(d[i]),nC=int((cls[i:j+1]=='C').sum()),
           vmin=v[i:j+1].min(),pre=pre,post=post,t0=t0,t1=t1)
    # entry response: rpm before (-15..-2) vs during (+3..+10 or until end)
    r['rpm_pre']=np.median(win('rpm',t0-15,t0-1)) if len(win('rpm',t0-15,t0-1)) else np.nan
    r['iac_pre']=np.median(win('iacv',t0-15,t0-1)) if len(win('iacv',t0-15,t0-1)) else np.nan
    dd=win('rpm',t0+2,min(t1+1,t0+30)); r['rpm_in']=np.median(dd) if len(dd) else np.nan
    dd=win('iacv',t0+1,t1+1); r['iac_in']=np.max(dd) if len(dd) else np.nan
    # exit response within 10 s after last cold sample
    for ch in ['rpm','iacv','ign','inj','stft','map']:
        w=win(ch,t1,t1+10)
        r[ch+'_x']=(w.min() if ch in('rpm','iacv','ign') else (w.max() if ch in ('map',) else w.mean())) if len(w) else np.nan
    r['rpm_end']=np.median(win('rpm',t1-5,t1)) if len(win('rpm',t1-5,t1)) else np.nan
    r['link_end']=post!=post
    rows.append(r)
pickle.dump(rows,open('eps2.pkl','wb'))
print('n episodes',len(rows))
print('start    end      dur  drv nC  vmin  pre  post | rpm_pre iac_pre -> rpm_in iac_in | rpm_end exit10s: rpm_min iac_min ign_min inj stft map_max')
for r in rows:
    print('%s %s %5.0f %3d %3d %4.0f %4.0f %5.0f | %5.0f %5.1f -> %5.0f %5.1f | %5.0f  %5.0f %5.1f %5.1f %5.2f %5.1f %5.1f'%(r['start'],r['end'],r['dur'],r['drive'],r['nC'],r['vmin'],r['pre'],r['post'],r['rpm_pre'],r['iac_pre'],r['rpm_in'],r['iac_in'],r['rpm_end'],r['rpm_x'],r['iacv_x'],r['ign_x'],r['inj_x'],r['stft_x'],r['map_x']))
tot=sum(r['nC'] for r in rows); print('total cold samples',tot,'of',n, '%.1f%%'%(100*tot/n))
for dv in np.unique(d):
    m=d==dv; print(int(dv),'n',m.sum(),'cold%%=%.1f part%%=%.1f'%(100*np.mean(cls[m]=='C'),100*np.mean(cls[m]=='P')),end='; ')
