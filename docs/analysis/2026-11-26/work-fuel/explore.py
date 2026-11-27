import numpy as np, os
W=os.path.dirname(os.path.abspath(__file__))
for did in (38,39):
    z=np.load(f'{W}/d{did}.npz'); print('=====',z['name'], 'dur', z['t'][-1]-z['t'][0])
    for k in ['rpm','vss','map','baro','tps','o2','stft','ltft','inj','ign','ect','iat','closed_loop','fuel_status','load_ecu','o2_2','batt','eld','iacv','gps_speed']:
        v=z[k]; v=v[~np.isnan(v)]
        r=z['raw_'+k]
        print(f'{k:12s} n={len(r):6d} rate={len(r)/(r[-1,0]-r[0,0]):5.2f}Hz  q=', np.round(np.percentile(v,[0,5,25,50,75,95,100]),3))
    cl=z['closed_loop']; fs=z['fuel_status']
    print('closed_loop frac', np.nanmean(cl), ' fuel_status values', np.unique(fs[~np.isnan(fs)],return_counts=True))
    ect=z['raw_ect'][:,1]; print('ect hist', np.histogram(ect,bins=[-50,20,40,50,60,70,75,80,85,90,95,130])[0])
    # cross tab closed_loop vs fuel_status
    m=~np.isnan(cl)&~np.isnan(fs)
    for a in np.unique(cl[m]):
        print(' cl',a, np.unique(fs[m&(cl==a)],return_counts=True))
