import sys,pickle,numpy as np
sys.path.insert(0,'/home/claude/hon-diy-data')
from hondalog.data.reader import open_log
DB='/home/claude/analysis/hondalog.db'
rows=[]
for d in range(6,30):
    log=open_log(DB,d); ep=log.meta['epoch_unix']
    s=log.streams.get('ecu/08+10')
    if s is None: continue
    raw=np.asarray(s.data['raw']); t=s.data['t']+ep
    rows.append(np.c_[t,raw,np.full(len(t),d)])
A=np.vstack(rows); pickle.dump(A,open('bits.pkl','wb'))
R=pickle.load(open('all.pkl','rb'))
rpm=R['ecu:rpm']
# rpm median within +-1 s
idx=np.searchsorted(rpm[:,0],A[:,0]); idx=np.clip(idx,1,len(rpm)-1)
r=rpm[idx,1]
tps=R['ecu:tps']; ti=np.clip(np.searchsorted(tps[:,0],A[:,0]),1,len(tps)-1); tp=tps[ti,1]
idle=(tp<9)&(r>500)
lo=idle&(r<720); mid=idle&(r>760)&(r<880); hi=idle&(r>1000)
for byte in range(10):
    for b in range(8):
        bit=(A[:,1+byte].astype(int)>>b)&1
        if bit.std()==0: continue
        print('0x%02X b%d'%(8+byte,b),'all%%=%.1f lo%%=%.1f mid%%=%.1f hi%%=%.1f'%(100*bit.mean(),100*bit[lo].mean(),100*bit[mid].mean(),100*bit[hi].mean()))
