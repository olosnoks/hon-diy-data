import pandas as pd, numpy as np
def W():
    w=pd.read_pickle('wide.pkl')
    w['loc']=pd.to_datetime(w.unix,unit='s')-pd.Timedelta(hours=6)
    bit=lambda c,n: pd.Series(np.right_shift(w[c].fillna(0).to_numpy().astype(int),n)&1,index=w.index)
    w['ac']=bit('a08',1); w['psp']=bit('a08',2); w['brk']=bit('a08',3); w['acc']=bit('a0B',1); w['fan']=bit('a0C',1); w['cl']=bit('a0F',0); w['vtec']=bit('a0A',2)
    return w
