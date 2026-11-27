from load import *
per=load(33); secs,g=grid(per); k=known(g)
b=g[0x27]; r=k['rpm']; ig=k['ign']
for lo in range(650,1150,25):
    m=(r>=lo)&(r<lo+25)&~np.isnan(b)
    if m.sum()>5: print(f"rpm {lo}-{lo+25}: n={m.sum():4d} mean27={np.mean(b[m]):.2f} P(27>0)={np.mean(b[m]>0):.2f} ign={np.nanmean(ig[m]):.1f}")
for v in range(5):
    m=b==v; print("27=",v,"n",m.sum(),"rpm",np.nanmedian(r[m]),"ign raw",np.nanmedian(ig[m]),"ect",np.nanmedian(k['ect'][m]))
