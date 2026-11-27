from load import *
per=load(33); secs,g=grid(per); k=known(g)
r=per[0][:,0]; dt=np.diff(r); i=np.nonzero(dt>2)[0]
print("gaps in fast reads:",[(round(r[j]),round(dt[j],1)) for j in i])
for t in list(range(1525,1545))+list(range(1700,1716)):
    j=t-secs[0]
    print(t, *(f"{n}={k[n][j]:.0f}" for n in ['rpm','ect','eld','iacv28','b27']), f"29={g[0x29][j]:.0f} 2A={g[0x2A][j]:.0f} 32={g[0x32][j]:.0f} 37={g[0x37][j]:.0f} 3C={g[0x3C][j]:.0f}")
