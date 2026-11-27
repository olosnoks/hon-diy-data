import numpy as np, datetime as dt
for name in ['drive_2026_11_26_2224','drive_2026_11_26_2251']:
    z=np.load(name+'.npz'); ep=float(z['epoch'])
    print(name, 't range', z['rpm'][0,0], z['rpm'][-1,0], dt.datetime.fromtimestamp(ep+z['rpm'][0,0]), dt.datetime.fromtimestamp(ep+z['rpm'][-1,0]))
    for ch in ['rpm','vss','map','tps','iac_cmd','iacv','idle_target','ect','eld','batt','alt_fr','brake','fan','ac_clutch','closed_loop','ign','ign_limit','load_ecu','stft','ltft','inj','iat']:
        v=z[ch][:,1]; u=np.unique(v)
        print(f'  {ch:12s} n={len(v):6d} min={v.min():8.2f} p5={np.percentile(v,5):8.2f} med={np.median(v):8.2f} p95={np.percentile(v,95):8.2f} max={v.max():8.2f} nuniq={len(u)}', u[:12] if len(u)<12 else '')
