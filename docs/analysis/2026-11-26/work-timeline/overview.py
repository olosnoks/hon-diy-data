from load import *
for k,d in DRIVES.items():
    t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss'); g=gps(d)
    print('drive',k,d,wall(t[0]),wall(t[-1]),len(t),'gps',len(g), 'fix vals',np.unique(g[:,5]))
    for m0 in np.arange(np.floor(t[0]/60)*60,t[-1],60):
        s=(t>=m0)&(t<m0+60); gs=(g[:,0]>=m0)&(g[:,0]<m0+60)
        if s.sum()==0: print(wall(m0),'no ecu', 'gps',gs.sum()); continue
        print(wall(m0),f"n={s.sum():4d} vss {vss[s].min():5.0f}-{vss[s].max():5.0f} mean {vss[s].mean():5.1f} rpm {rpm[s].min():5.0f}-{rpm[s].max():5.0f} gps n={gs.sum()} gspd {np.nanmean(g[gs,3]) if gs.any() else np.nan:5.1f}")
