from common import *
import csv
L=list(csv.DictReader(open('dips.csv')))
for r in L:
    if float(r['rpm_min'])>600: continue
    name=[x for x in DR if x.endswith(r['drive'])][0]; d,ep=load(name)
    tm=float(r['t']); w=win(d,'rpm',tm-0.6,tm+0.01)
    # slope over last ~0.25s before min
    a=w[w[:,0]>=tm-0.3]; s_last=(a[-1,1]-a[0,1])/(a[-1,0]-a[0,0]) if len(a)>1 and a[-1,0]>a[0,0] else np.nan
    rate=float(r['rate'])
    ign=win(d,'ign',tm-1,tm+1.5)[:,1]; mp=win(d,'map',tm-1,tm+1.5)[:,1]
    tp=win(d,'tps',tm-2,tm+3)[:,1]
    rec=win(d,'rpm',tm,tm+3)[:,1]
    print(f"{r['drive']} {r['wall']} min {r['rpm_min']} maxfall {rate:6.0f} rpm/s, last0.3s {s_last:6.0f} rpm/s, t_to_400_at_maxfall {(float(r['rpm_min'])-400)/abs(rate):.2f}s, ign range {ign.min():.1f}..{ign.max():.1f}, map max {mp.max():.0f}, tps max(+3s) {tp.max():.1f}, rpm +3s max {rec.max():.0f}")
