import numpy as np, load
for d in ['drive_2026_11_26_2045','drive_2026_11_26_2114']:
    B,R,L,ep=load.load(d); G=load.grid(B,R)
    for val in np.unique(G[0x2A][~np.isnan(G[0x2A])]):
        m=G[0x2A]==val
        print(d[-4:],'x2A=%d n=%d  ECT med %.1fC  IAT med %.1fC  rpm %.0f  iacv %.1f x29 %.1f eld %.1fA batt %.1f altfr %.0f'%(val,m.sum(),load.ect_c(np.median(G[0x10][m])),load.ect_c(np.median(G[0x11][m])),np.nanmedian(G['rpm'][m]),np.median(G[0x28][m]),np.median(G[0x29][m]),77.06-np.median(G[0x19][m])/2.5371,np.median(G[0x17][m])/10.45,np.median(G[0x18][m])))
