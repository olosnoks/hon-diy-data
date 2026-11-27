from common import *
import re
from collections import Counter
S=json.load(open('segments.json'))
d=39;k='B'
t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss')
ev=[e for e in events(d) if e[1]=='link_error']
evt=np.array([e[0] for e in ev]); kinds=[json.loads(e[3])['kind'] for e in ev]; att=[json.loads(e[3])['attempt'] for e in ev]
# episodes: errors within 1.0 s of each other
ep=[];cur=[evt[0]]
for x in evt[1:]:
    if x-cur[-1]<=1.0: cur.append(x)
    else: ep.append(cur); cur=[x]
ep.append(cur); eps=np.array([c[0] for c in ep])
print('B link_error rows',len(evt),'episodes',len(ep))
t0=t[0]; t1=t[-1]
W=10
edges=np.arange(t0,t1,W)
feat=[]
tb,eld=chan(d,'eld'); _,batt=chan(d,'batt'); tbr,brk=chan(d,'brake'); tt,tps=chan(d,'tps'); te,ect=chan(d,'ect'); ti,iat=chan(d,'iat')
for a in edges:
    s=(t>=a)&(t<a+W)
    if s.sum()<20: continue
    v=vss[s]; r=rpm[s]
    vhp=np.std(v-np.convolve(v,np.ones(11)/11,'same')) if len(v)>22 else np.nan
    rj=np.std(np.diff(r,2))
    n=np.sum((eps>=a)&(eps<a+W)); nr=np.sum((evt>=a)&(evt<a+W))
    sb=(tb>=a)&(tb<a+W); sbr=(tbr>=a)&(tbr<a+W); stp=(tt>=a)&(tt<a+W)
    feat.append([a,n,nr,np.mean(v),np.mean(r),vhp,rj,np.mean(eld[sb]) if sb.any() else np.nan,np.mean(batt[sb]) if sb.any() else np.nan,np.mean(brk[sbr]) if sbr.any() else np.nan,np.std(np.diff(tps[stp])) if stp.sum()>3 else np.nan, np.abs(np.diff(v)).sum()])
F=np.array(feat)
cols=['t','ep','rows','vss','rpm','vss_hp','rpm_jit','eld','batt','brake','tps_d','vss_abs']
np.save('corr_B.npy',F)
def rate(mask,name):
    n=F[mask,1].sum(); T=mask.sum()*W/60
    print(f'  {name:28s} windows {mask.sum():4d} ({T:5.1f} min) episodes {int(n):3d} rate {n/T if T else np.nan:5.2f}/min')
tsplit=t_of('23:10:00')
print('--- by period')
rate(F[:,0]<tsplit,'B before 23:10'); rate(F[:,0]>=tsplit,'B from 23:10')
late=F[:,0]>=tsplit
print('--- by speed band, from 23:10 only')
for lo,hi in [(0,1),(1,40),(40,70),(70,90),(90,200)]: rate(late&(F[:,3]>=lo)&(F[:,3]<hi),f'vss {lo}-{hi}')
print('--- by speed band, before 23:10')
for lo,hi in [(0,1),(1,40),(40,70),(70,90),(90,200)]: rate((~late)&(F[:,3]>=lo)&(F[:,3]<hi),f'vss {lo}-{hi}')
print('--- by rpm, from 23:10')
for lo,hi in [(0,1500),(1500,2500),(2500,9000)]: rate(late&(F[:,4]>=lo)&(F[:,4]<hi),f'rpm {lo}-{hi}')
# feature comparison windows with errors vs without, late moving
m=late&(F[:,3]>5)
def rank_corr(x,y):
    ok=np.isfinite(x)&np.isfinite(y); x=x[ok]; y=y[ok]
    rx=np.argsort(np.argsort(x)); ry=np.argsort(np.argsort(y)); return np.corrcoef(rx,ry)[0,1], ok.sum()
print('--- late moving windows: mean feature with vs without error episode; spearman vs episode count')
for j,cn in enumerate(cols):
    if j<3: continue
    w=m&(F[:,1]>0); wo=m&(F[:,1]==0)
    rc,nn=rank_corr(F[m,j],F[m,1])
    print(f'  {cn:8s} with {np.nanmean(F[w,j]):8.3f} without {np.nanmean(F[wo,j]):8.3f} spearman {rc:+.2f} (n={nn})')
# permutation test for vss_hp and rpm_jit
rng=np.random.default_rng(1)
for j in (5,6,11,3):
    x=F[m,j]; y=F[m,1]>0; obs=np.nanmean(x[y])-np.nanmean(x[~y]); cc=0
    cc=0
    for _ in range(5000):
        yy=rng.permutation(y); cc+= (np.nanmean(x[yy])-np.nanmean(x[~yy]))>=obs
    print(f'  perm p (one-sided, higher with errors) {cols[j]}: {cc/5000:.3f}')
# ECT / IAT trend at error onset
print('ect at 23:00, 23:10, 23:20', [round(float(np.interp(t_of(h),te,ect)),1) for h in ('23:00:00','23:10:00','23:20:00')], 'iat', [round(float(np.interp(t_of(h),ti,iat)),1) for h in ('23:00:00','23:10:00','23:20:00')])
# Drive A errors while moving
ea=[(wall(e[0]),json.loads(e[3])['kind']) for e in events(38) if e[1]=='link_error']
print('A errors',ea)
# attempt-2 success rates
print('attempt counts', Counter(att))
# first-attempt errors whose retry succeeded: an error row with attempt1 not followed by attempt2 of same addr within 0.3s
# bit analysis
blocks={}
for sid,name in c.execute("select id,name from streams where drive_id=? and name like 'ecu/%'",(d,)):
    if name=='ecu/link': continue
    addr=int(name[4:6],16); rows=c.execute("select t,raw from samples where drive_id=? and stream_id=? and part=0 order by t",(d,sid)).fetchall()
    blocks[addr]=(np.array([r[0] for r in rows]),[bytes(r[1]) for r in rows])
up=dn=0; flips=[]
for e in ev:
    js=json.loads(e[3])
    if js['kind'] not in ('BadChecksum','ShortReply'): continue
    hx=re.findall(r'\b[0-9A-F]{2}\b',js['message'].split(':')[-1] if 'got' in js['message'] else js['message'])
    bts=bytes(int(h,16) for h in hx)
    data=bts[2:2+js['length']]
    if js['kind']=='ShortReply': data=bts[2:]
    tt_,raws=blocks[js['addr']]; i=np.argmin(abs(tt_-e[0])); ref=raws[i]
    u=dd_=0
    for x,y in zip(data,ref):
        u+=bin(x & ~y & 0xFF).count('1'); dd_+=bin(~x & y & 0xFF).count('1')
    up+=u; dn+=dd_
    flips.append((wall(e[0]),js['kind'],hex(js['addr']),bts.hex(' '),ref.hex(' '),round(abs(tt_[i]-e[0]),2),u,dd_))
for f in flips: print(' ',f)
print('bits 0->1',up,'1->0',dn)
