from common import *
import sqlite3
DEAD=1.0; FLOW=240/60  # cc/s per injector at rated pressure
z=load(39); t=z['t']
vs=z['vss']; dv=np.gradient(np.nan_to_num(vs),t)
print('== steady cruise segments drive 39 (closed or open loop, |dv/dt|<1 km/h/s, 10-s windows)')
print(' vss_band | n_win | rpm | MAP | inj ms | duty % | fuel L/h (dead 1.0) | L/100km | US mpg | L/h if dead 0.8/1.2 | air-based L/h (VE .80) | total trim')
for lo,hi in [(40,60),(60,80),(80,95),(95,110)]:
    W_=[]
    for s in np.arange(t[0],t[-1]-10,10):
        k=(t>=s)&(t<s+10)
        if k.sum()<80: continue
        v=vs[k]
        vm=np.nanmean(v)
        if not (lo<=vm<hi) or np.nanstd(v)>2.5 or np.nanmin(z["inj"][k])<0.5: continue
        W_.append(np.where(k)[0])
    if not W_: continue
    idx=np.concatenate(W_)
    rpm=z['rpm'][idx]; inj=z['inj'][idx]; mp=z['map'][idx]; T=z['iat'][idx]+273.15
    def lph(d): return np.nanmean(4*rpm/120*np.clip(inj-d,0,None)/1000*FLOW*3.6)
    duty=np.nanmean(inj/(120000/rpm))*100
    L=lph(DEAD); v=np.nanmean(vs[idx]); l100=L/v*100
    air=np.nanmean(mp*1000*1.590e-3*rpm/120*0.80/(287*T))  # kg/s
    fuel_air=air/14.7/0.745*1000*3600/1000  # L/h
    print(f' {lo}-{hi} | {len(W_)} | {np.nanmean(rpm):.0f} | {np.nanmean(mp):.1f} | {np.nanmean(inj):.2f} | {duty:.1f} | {L:.2f} | {l100:.1f} | {235.2/l100:.1f} | {lph(0.8):.2f}/{lph(1.2):.2f} | {fuel_air:.2f} | {np.nanmean(z["total"][idx]):+.1f}  (v={v:.0f}, rpm/kmh={np.nanmean(rpm)/v:.1f})')
# whole-drive fuel
for did in (38,39):
    z=load(did); t=z['t']; dt=np.gradient(t); dt=np.clip(dt,0,0.5)
    q=4*z['rpm']/120*np.clip(np.nan_to_num(z['inj'])-DEAD,0,None)/1000*FLOW  # cc/s
    dist=np.nansum(np.nan_to_num(z['vss'])/3.6*dt)/1000
    fuel=np.nansum(q*dt)/1000
    print(f'drive {did}: distance {dist:.1f} km (VSS), fuel est {fuel:.2f} L -> {fuel/dist*100 if dist>0 else 0:.1f} L/100km = {235.2/(fuel/dist*100) if dist>0 else 0:.1f} US mpg (dead 1.0 ms, 240 cc/min); max duty {np.nanmax(z["inj"]/(120000/z["rpm"]))*100:.0f}%')
    # gps distance check
    g=z['raw_gps_speed']; dg=np.sum(g[1:,1]/3.6*np.diff(g[:,0]))/1000; print('   GPS distance', round(dg,1),'km')
# LTFT history
print('== LTFT history (value changes) across drives')
for did in range(6,40):
    try: z=np.load(f'{W}/d{did}.npz')
    except: continue
    r=z['raw_ltft']; 
    if len(r)==0: continue
    ch=np.where(np.abs(np.diff(r[:,1]))>0.01)[0]
    print(f' drive {did} {str(z["name"])[6:]}: start {r[0,1]:.2f} end {r[-1,1]:.2f} changes {len(ch)}', ' '.join(f'{r[i+1,0]/60:.1f}m:{r[i+1,1]:.2f}' for i in ch[:12]))
