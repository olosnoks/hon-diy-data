import pickle, datetime, numpy as np
S, drives = pickle.load(open('series.pkl','rb'))
G = pickle.load(open('grid.pkl','rb'))
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
t,v,_=S[0x10]
hi = v>=70
i=0;n=len(t); out=[]
while i<n:
    if hi[i]:
        j=i
        while j<n and hi[j] and t[j]-t[j-1]<5 if j>i else hi[j]: j+=1
        out.append((t[i],t[j-1],v[i:j].max(),j-i)); i=j
    else: i+=1
for a,b,mx,cnt in out:
    if a < t[0]+0 or True:
        k=np.searchsorted(G['t'],a)
        print(f(a),'-',f(b),'%5.0fs'%(b-a),'max raw',int(mx),'reads',cnt,'IAT %.0f'%G['iat'][min(k,len(G['t'])-1)])
