import pickle, numpy as np, datetime
G = pickle.load(open('grid.pkl','rb'))
d0=datetime.datetime.fromtimestamp(G['t'][0]).date()
ts=lambda h: datetime.datetime.combine(d0, datetime.time.fromisoformat(h)).timestamp()
pairs=[('21:02:30','21:17:48'),('21:17:48','21:30:10'),('22:04:35','22:18:48'),('20:45:18','21:02:30'),('21:45:01','22:04:35')]
for a,b in pairs:
    m=(G['t']>=ts(a))&(G['t']<ts(b)); r=G['rpm'][m]; cov=np.mean(~np.isnan(r))
    print(a,b,'%.1f min'%((ts(b)-ts(a))/60),'revs (data only) %.0f'%(np.nansum(r)/60),'coverage %.2f'%cov,'revs gap-filled %.0f'%(np.nanmean(r)*(ts(b)-ts(a))/60))
