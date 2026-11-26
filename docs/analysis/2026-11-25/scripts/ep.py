import pickle,numpy as np,datetime as dt
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb'))
tz=ZoneInfo('America/Chicago')
f=lambda t:dt.datetime.fromtimestamp(t,tz).strftime('%H:%M:%S')
e=R['ecu:ect']; t=e[:,0]; v=e[:,1]; d=e[:,2]
# baseline: rolling 85th pct of values within +-90s
base=np.array([np.percentile(v[(t>x-90)&(t<x+90)],85) for x in t])
cold=v<55
part=(~cold)&(base-v>=8)
state=np.where(cold,'C',np.where(part,'P','W'))
eps=[]
i=0;n=len(t)
while i<n:
    if state[i]=='W': i+=1; continue
    j=i
    while j+1<n and state[j+1]!='W' and t[j+1]-t[j]<=4 and d[j+1]==d[i]: j+=1
    s=state[i:j+1]
    eps.append(dict(t0=t[i],t1=t[j],dur=t[j]-t[i]+1,drive=int(d[i]),kind='C' if 'C' in s else 'P',
        vmin=v[i:j+1].min(),base=base[i],nC=int((s=='C').sum()),n=j-i+1,
        prev=(t[i]-t[i-1]) if i>0 else 99, nxt=(t[j+1]-t[j]) if j+1<n else 99))
    i=j+1
pickle.dump((eps,base,state),open('eps.pkl','wb'))
print(len(eps),'episodes; C:',sum(x['kind']=='C' for x in eps),'P:',sum(x['kind']=='P' for x in eps))
for x in eps:
    print(f(x['t0']),x['drive'],x['kind'],'dur%5.0f'%x['dur'],'min%3.0f base%3.0f nC%3d/%3d gapbefore%.1f'%(x['vmin'],x['base'],x['nC'],x['n'],x['prev']))
