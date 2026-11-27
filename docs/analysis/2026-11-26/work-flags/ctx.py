import pickle, numpy as np, datetime as dt
exec(open(r'C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-flags/trans.py').read().split("BASE=")[0])
def dump(did, t0, t1, extra=()):
    s=D[did]['ser']; arr=s[8]
    for i in np.where((arr[:,0]>=t0)&(arr[:,0]<=t1))[0]:
        t=arr[i,0]
        fl=' '.join(f"{int(near(s,b,t,0.01)):3d}" for b in range(8,16))
        ex=' '.join(f"{a:02X}={near(s,a,t)}" for a in extra)
        print(f"  d{did} {ts(t)} | {fl} | {ctx(s,t)} alt {near(s,0x18,t)} {ex}")
print("== first 4 / last 3 slow reads of each drive")
for did,d in sorted(D.items()):
    arr=d['ser'][8]
    print(d['name'])
    dump(did, arr[0,0], arr[min(3,len(arr)-1),0]); print('   ...'); dump(did, arr[max(0,len(arr)-3),0], arr[-1,0])
print("== 0x0C b0 events")
for did,tt in [(22,'11-25 19:48:08'),(22,'11-25 19:56:12'),(35,'11-26 21:48:01')]:
    t=dt.datetime.strptime('2026-'+tt,'%Y-%m-%d %H:%M:%S').timestamp()
    dump(did,t-4,t+5)
print("== 0x08 b2 event"); t=dt.datetime(2026,11,25,21,12,8).timestamp(); dump(27,t-5,t+5)
print("== closed-loop exits: inj (0x24-25) around")
for did,(h,m,sec) in [(19,(19,10,45)),(20,(19,13,6)),(22,(20,0,45))]:
    t=dt.datetime(2026,11,25,h,m,sec).timestamp(); s=D[did]['ser']
    a24=s[0x24]; a25=s[0x25]; idx=np.where((a24[:,0]>t-3)&(a24[:,0]<t+4))[0]
    print(' ', did, ' '.join(f"{ts(a24[i,0])[9:]}:{rpm(s,a24[i,0]) or 0:.0f}rpm/{(a24[i,1]*256+a25[i,1])/250:.2f}ms/tps{near(s,0x14,a24[i,0],0.3)}" for i in idx[::3]))
