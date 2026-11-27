from lib import *
S = slow_all([38, 39]).sort_values('t').reset_index(drop=True)
S = S[S.rpm > 400].copy(); S['dt'] = S.t.diff()
for c in ['eld','altfr','batt','rpm','kpa']: S['d'+c] = S[c].diff()
S['db6'] = S.b6.diff(); S['pb6'] = S.b6.shift()
ok = S.dt < 1.6
up = ok & (S.drpm > 150) & (S.kpa > 80) & (S.rpm > 3000) & (S.rpm < 4800)
dn = ok & (S.drpm < -300) & (S.rpm > 2500) & (S.rpm.shift() > 3300)
for nm, m in [('rpm up into >3000/MAP>80, b6 stays 1', up & (S.db6 == 0) & (S.b6 == 1)), ('rpm up, b6 1->0', up & (S.db6 == -1)),
              ('rpm drop from >3300, b6 stays 1', dn & (S.db6 == 0) & (S.b6 == 1)), ('rpm drop from >3300, b6 0->1', dn & (S.db6 == 1)),
              ('b6 stays 0', ok & (S.db6 == 0) & (S.b6 == 0))]:
    g = S[m]; print(f'{nm:40s} n {len(g):3d}  d altfr med {g.daltfr.median():+.2f} mean {g.daltfr.mean():+.2f}  d eld {g.deld.mean():+.2f}  d batt {g.dbatt.mean():+.3f}  d rpm {g.drpm.mean():+.0f}')
# alt_fr per amp at cruise: altfr vs eld in 2500-4000 rpm closed loop
import numpy as np
C = S[(S.rpm > 2500) & (S.rpm < 4000) & (S.b6 == 1)].dropna(subset=['altfr','eld'])
print('altfr~eld slope at 2500-4000 rpm', np.polyfit(C.eld, C.altfr, 1), 'eld range', C.eld.min(), C.eld.max())
