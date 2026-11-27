from common import *
import re
d=39
ev=[e for e in events(d) if e[1]=='link_error']
blocks={}
for sid,name in c.execute("select id,name from streams where drive_id=? and name like 'ecu/%'",(d,)).fetchall():
    if name=='ecu/link': continue
    rows=c.execute("select t,raw from samples where drive_id=? and stream_id=? and part=0 order by t",(d,sid)).fetchall()
    blocks[int(name[4:6],16)]=(np.array([r[0] for r in rows]),[bytes(r[1]) for r in rows])
up=dn=0; last=[]; ones_last=[]; allbytes_ones=[]
for e in ev:
    js=json.loads(e[3]); m=js['message']
    if js['kind']=='ShortReply': hx=m.split('got ')[1].split(',')[0]
    elif js['kind']=='BadChecksum': hx=m.split('reply ')[1]
    else: continue
    b=bytes(int(h,16) for h in hx.split())
    data=b[2:2+js['length']]
    tt,raws=blocks[js['addr']]; i=np.argmin(abs(tt-e[0])); ref=raws[i]
    for x,y in zip(data,ref):
        up+=bin(x&~y&0xFF).count('1'); dn+=bin(~x&y&0xFF).count('1')
    if js['kind']=='ShortReply': last.append(b[-1]); 
print('bits 0->1',up,'1->0',dn)
print('last byte of short replies',[hex(x) for x in last],'mean ones',np.mean([bin(x).count('1') for x in last]))
# baseline: mean ones in good raw bytes
allb=b''.join(r for a in blocks.values() for r in a[1][:500])
print('baseline mean ones per good byte',np.mean([bin(x).count('1') for x in allb]).round(2))
