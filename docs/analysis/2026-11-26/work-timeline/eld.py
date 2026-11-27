from common import *
for nm in ['eld','batt','alt_fr','brake','iat','ect']:
    t,v=chan(39,nm)
    print(nm,' '.join(f"{wall(a)[:5]}:{np.mean(v[(t>=a)&(t<a+60)]):.1f}" for a in np.arange(t_of('23:05:00'),t_of('23:16:00'),60)))
