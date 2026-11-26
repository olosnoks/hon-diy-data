import pickle,numpy as np,datetime as dt,sys
from zoneinfo import ZoneInfo
R=pickle.load(open('all.pkl','rb'))
tz=ZoneInfo('America/Chicago')
f=lambda t:dt.datetime.fromtimestamp(t,tz).strftime('%H:%M:%S')
def ts(h): return dt.datetime.fromisoformat('2026-11-25T'+h).replace(tzinfo=tz).timestamp()
t0=ts(sys.argv[1]); a=float(sys.argv[2]); b=float(sys.argv[3]); step=float(sys.argv[4]) if len(sys.argv)>4 else 1
ch=['rpm','map','tps','ign','inj','stft','iacv','ect','batt','eld','alt_fr','fan','ac_clutch','closed_loop','o2']
print('time     '+' '.join('%6s'%c[:6] for c in ch))
for x in np.arange(t0+a,t0+b,step):
    row=[]
    for c in ch:
        A=R['ecu:'+c]; m=(A[:,0]>=x)&(A[:,0]<x+step)
        if c=='rpm' and m.any(): row.append('%6.0f'%A[m,1].min())
        else: row.append('%6.1f'%A[m,1].mean() if m.any() else '     -')
    print(f(x),' '.join(row))
