"""Every transition of every bit of 0x08-0x0F, with context."""
import pickle, numpy as np, datetime as dt, sys
D = pickle.load(open(r'C:/Users/skons/Documents/hon-diy-data/docs/analysis/2026-11-26/work-flags/F.pkl','rb'))
def ect(x):
    k=[155.04149,-3.0414878,0.03952185,-0.00029383913,0.0000010792568,-0.0000000015618437]
    return sum(ki*x**i for i,ki in enumerate(k))
def near(s, a, t, tol=1.5):
    arr = s.get(a)
    if arr is None or len(arr)==0: return None
    i = np.searchsorted(arr[:,0], t); best=None
    for j in (i-1,i):
        if 0<=j<len(arr) and abs(arr[j,0]-t)<=tol and (best is None or abs(arr[j,0]-t)<abs(arr[best,0]-t)): best=j
    return None if best is None else arr[best,1]
def rpm(s,t):
    h=near(s,0,t,0.5); l=near(s,1,t,0.5)
    return None if h is None or l is None else (h*256+l)/4
def ctx(s,t):
    r=rpm(s,t); e=near(s,0x10,t); eld=near(s,0x19,t); b=near(s,0x17,t); x27=near(s,0x27,t); iac=near(s,0x28,t); it=None
    h=near(s,0x76,t); l=near(s,0x77,t)
    if h is not None and l is not None: it=(h*256+l)/4
    f=lambda v,fmt: '-' if v is None else fmt%v
    return f"rpm {f(r,'%5.0f')} ect {f(None if e is None else ect(e),'%5.1f')} eld {f(eld,'%3.0f')} batt {f(None if b is None else b/10.45,'%4.1f')} x27 {f(x27,'%2.0f')} iacv {f(iac,'%3.0f')} itgt {f(it,'%5.0f')}"
ts=lambda t: dt.datetime.fromtimestamp(t).strftime('%m-%d %H:%M:%S.%f')[:-4]
BASE={0x08:132,0x09:0,0x0A:144,0x0B:65,0x0C:1,0x0D:0,0x0E:0,0x0F:1}
for a in range(0x08,0x10):
  for bit in range(8):
    tot=0; on=0; lines=[]
    for did,d in sorted(D.items()):
        s=d['ser']; arr=s[a]; v=((arr[:,1].astype(int)>>bit)&1)
        tot+=len(v); on+=v.sum()
        for i in range(1,len(v)):
            if v[i]!=v[i-1]:
                t=arr[i,0]
                # other flag bytes at this read
                fl=' '.join(f"{int(near(s,b,t,0.01) or 0):3d}" for b in range(8,16))
                lines.append(f"  d{did} {ts(t)} {v[i-1]}->{v[i]}  gap {t-arr[i-1,0]:5.1f}s | {ctx(s,t)} | 08..0F {fl}")
        # first read value
        lines.insert(len(lines)-0, '') if False else None
    first={did:int((d['ser'][a][0,1].astype(int)>>bit)&1) for did,d in D.items()}
    print(f"0x{a:02X} b{bit}: set {on}/{tot} = {100*on/tot:.2f}%  transitions {len(lines)}  drives starting set: {sum(first.values())}/{len(first)}")
    if 0<len(lines)<=80: print('\n'.join(lines))
