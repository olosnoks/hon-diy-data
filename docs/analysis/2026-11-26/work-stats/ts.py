from load import *
for d in (33,34):
    per=load(d)
    print("drive",d,"1C==19 at every read:", np.array_equal(per[0x1C][:,1],per[0x19][:,1]))
    t=per[0x29][:,0]; v29=per[0x29][:,1]; v2a=per[0x2A][:,1]; v28=per[0x28][:,1]; v27=per[0x27][:,1]
    i=np.nonzero(np.diff(v2a))[0]; print(" 2A change at t=",t[i+1], v2a[i], "->", v2a[i+1])
    # 29 segments
    jumps=np.nonzero(np.abs(np.diff(v29))>8)[0]
    for j in jumps: print(f"  29 jump t={t[j+1]:.0f} {v29[j]:.0f}->{v29[j+1]:.0f}  28 {v28[j]:.0f}->{v28[j+1]:.0f}")
    # print downsampled every 30 s
    for k in range(0,len(t),60): print(f"  t={t[k]:6.0f} 27={v27[k]:.0f} 28={v28[k]:.0f} 29={v29[k]:.0f} 2A={v2a[k]:.0f} 32={per[0x32][k,1]:.0f} ect={per[0x10][min(k,len(per[0x10])-1),1]:.0f} iat={per[0x11][min(k,len(per[0x11])-1),1]:.0f}")
