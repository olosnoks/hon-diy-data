from common import *
res={}
for name in DR:
    d,ep=load(name); ic=d['iac_cmd']; r=d['rpm']
    X=[];Y={L:[] for L in (0.5,1,1.5,2,3,4)}
    for k in range(1,len(ic)-4):
        t1=ic[k,0]
        if at(d,'vss',t1)>0: continue
        tp=win(d,'tps',t1-3,t1+5)[:,1]; mp=win(d,'map',t1-3,t1+5)[:,1]
        if len(tp)==0 or tp.max()>8.7 or mp.max()-mp.min()>5: continue
        if win(d,'brake',t1-3,t1+5)[:,1].std()>0: continue
        b=win(d,'rpm',t1-1.0,t1)[:,1].mean()
        dI=ic[k,1]-ic[k-1,1]
        # require no further iac change in the next 2 reads (clean step)
        X.append(dI)
        for L in Y: Y[L].append(win(d,'rpm',t1+L-0.4,t1+L+0.4)[:,1].mean()-b)
    X=np.array(X)
    print(name,'n',len(X))
    for L in Y:
        y=np.array(Y[L]); g=~np.isnan(y); p=np.polyfit(X[g],y[g],1); rr=np.corrcoef(X[g],y[g])[0,1]
        print(f'  lag {L:3.1f}s  rpm per iac count {p[0]:6.2f}  r={rr:.2f}')
