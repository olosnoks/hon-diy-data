import pandas as pd, numpy as np
def prep():
    H=pd.read_pickle('/home/claude/analysis/work/honda.pkl').sort_values('wall').reset_index(drop=True)
    H['trim']=H.stft+H.ltft
    s=H.set_index(pd.to_datetime(H.wall,unit='s'))
    emin=s.ect.rolling('20s',center=True).min().values
    emax=s.ect.rolling('20s',center=True).max().values
    H['ects']=np.select([emin>=74, emax<=45],['warm','cold'],'mixed')
    # restart/settle exclusion: 90 s after rpm<300 or drive start gap>30s
    t=H.wall.values
    starts=t[np.r_[True,np.diff(t)>30] | (H.rpm.values<300)]
    excl=np.zeros(len(H),bool)
    for st in starts: excl|=(t>=st)&(t<st+90)
    H['settle']=excl
    H['idle']=np.select([H.rpm.between(600,740),H.rpm.between(760,860),H.rpm.between(860,960),H.rpm.between(1000,1150)],['670','810','900','1080'],'other')
    H['ac']=H.ac_clutch.fillna(0)>0
    return H
