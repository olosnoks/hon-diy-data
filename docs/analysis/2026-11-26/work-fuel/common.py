import numpy as np, os
W=os.path.dirname(os.path.abspath(__file__))
def load(did):
    z=dict(np.load(f'{W}/d{did}.npz'))
    t=z['t']
    for k in ('o2','o2_2'):
        if k in z: z[k]=np.where(z[k]>1.25,np.nan,z[k])
    # derivatives on grid (per s), smoothed over ~0.5 s
    def deriv(v):
        dv=np.gradient(v,t); return dv
    z['drpm']=deriv(z['rpm']); z['dmap']=deriv(z['map'])
    # ECT glitch flag: raw ect jump > 4 C between consecutive ~1 s samples, flag +-20 s
    e=z['raw_ect']; bad=np.zeros(len(t),bool)
    j=np.where(np.abs(np.diff(e[:,1]))>4)[0]
    for i in j:
        bad|=(t>e[i,0]-20)&(t<e[i+1,0]+20)
    z['ect_glitch']=bad
    # time since last closed-loop entry / since inj==0 (decel cut)
    cl=np.nan_to_num(z['closed_loop'],nan=0)>0.5
    since=np.full(len(t),1e9); last=-1e9
    cut=(z['inj']<0.3)|~cl
    for i in range(len(t)):
        if cut[i]: last=t[i]
        since[i]=t[i]-last
    z['since_ol']=since
    z['total']=z['stft']+z['ltft']
    return z
def sel_cl(z, ect_lo=55, ect_hi=100, settle=3.0, steady=True):
    m=(z['closed_loop']>0.5)&(z['rpm']>500)&(z['inj']>0.5)&np.isfinite(z['total'])&np.isfinite(z['map'])
    if 'fuel_status' in z: m&=(np.nan_to_num(z['fuel_status'],nan=2)==2)
    m&=(z['ect']>=ect_lo)&(z['ect']<=ect_hi)&~z['ect_glitch']
    m&=z['since_ol']>settle
    if steady: m&=(np.abs(z['dmap'])<15)&(np.abs(z['drpm'])<400)
    return m
