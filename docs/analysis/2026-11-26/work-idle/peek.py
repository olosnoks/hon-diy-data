# print merged timeline around given wall time
import sys
from common import *
name=DR[int(sys.argv[1])]; d,ep=load(name)
import datetime as dt
hh,mm,ss=sys.argv[2].split(':'); 
base=dt.datetime.fromtimestamp(ep).replace(hour=int(hh),minute=int(mm),second=0,microsecond=0).timestamp()+float(ss)-ep
a,b=base+float(sys.argv[3]),base+float(sys.argv[4])
chs=['rpm','vss','map','tps','idle_target','iac_cmd','iacv','ect','eld','brake','alt_fr','ign','inj','stft']
ts=np.unique(np.concatenate([win(d,c,a,b)[:,0] for c in ['rpm','iac_cmd','idle_target']]))
print('time        '+' '.join(f'{c[:7]:>7s}' for c in chs))
for t in ts:
    if len(win(d,'rpm',t,t))==0 and len(win(d,'iac_cmd',t,t))==0: continue
    print(wall(ep,t),' '.join(f'{at(d,c,t):7.1f}' for c in chs))
