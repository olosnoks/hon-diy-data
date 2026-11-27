from common import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
A=dict(np.load(f'{W}/cl_sel.npz'))
mapb=[10,20,24,28,32,38,45,55,65,80,105]; rpmb=[500,1000,1250,1500,1750,2000,2250,2500,2750,3000,3500,4000,6000]
H=np.full((len(mapb)-1,len(rpmb)-1),np.nan); N=np.zeros_like(H); Md=np.full_like(H,np.nan)
for i in range(len(mapb)-1):
  for j in range(len(rpmb)-1):
    s=(A['map']>=mapb[i])&(A['map']<mapb[i+1])&(A['rpm']>=rpmb[j])&(A['rpm']<rpmb[j+1])
    N[i,j]=s.sum()/11.3
    if s.sum()>=23: H[i,j]=A['total'][s].mean(); Md[i,j]=np.median(A['total'][s])
f,ax=plt.subplots(figsize=(14,7))
im=ax.imshow(H,origin='lower',cmap='RdYlGn_r',vmin=0,vmax=20,aspect='auto')
for i in range(H.shape[0]):
  for j in range(H.shape[1]):
    if np.isfinite(H[i,j]): ax.text(j,i,f'{H[i,j]:+.1f}\n{N[i,j]:.0f}s',ha='center',va='center',fontsize=8)
ax.set_xticks(range(len(rpmb)-1)); ax.set_xticklabels([f'{a}-{b}' for a,b in zip(rpmb[:-1],rpmb[1:])],rotation=30)
ax.set_yticks(range(len(mapb)-1)); ax.set_yticklabels([f'{a}-{b}' for a,b in zip(mapb[:-1],mapb[1:])])
ax.set_xlabel('rpm'); ax.set_ylabel('MAP kPa'); plt.colorbar(im,label='mean total trim % (stft+ltft)')
ax.set_title('Closed-loop total fuel trim, drives 2224 + 2251 (steady, ECT 55-100 no glitch, >3 s after OL/cut); cells >= 2 s')
f.tight_layout(); f.savefig(f'{W}/trim_map.png',dpi=85)
# markdown table
print('| MAP kPa \ rpm | '+' | '.join(f'{a}-{b}' for a,b in zip(rpmb[:-1],rpmb[1:]))+' |')
print('|'+'---|'*(len(rpmb)))
for i in range(H.shape[0]):
  print(f'| {mapb[i]}-{mapb[i+1]} | '+' | '.join((f'{H[i,j]:+.1f} ({N[i,j]:.0f}s)' if np.isfinite(H[i,j]) else '') for j in range(H.shape[1]))+' |')
# rpm marginal
print('rpm marginal (all MAP):')
for j in range(len(rpmb)-1):
  s=(A['rpm']>=rpmb[j])&(A['rpm']<rpmb[j+1])
  if s.sum()>20: print(f'  {rpmb[j]}-{rpmb[j+1]}: {A["total"][s].mean():+.1f} median {np.median(A["total"][s]):+.1f}  ({s.sum()/11.3:.0f}s)  MAP med {np.median(A["map"][s]):.0f}')
print('MAP marginal (rpm 1250-3000):')
for i in range(len(mapb)-1):
  s=(A['map']>=mapb[i])&(A['map']<mapb[i+1])&(A['rpm']>=1250)&(A['rpm']<3000)
  if s.sum()>20: print(f'  {mapb[i]}-{mapb[i+1]}: {A["total"][s].mean():+.1f}  ({s.sum()/11.3:.0f}s) rpm med {np.median(A["rpm"][s]):.0f}')
# leak sizing
gam=1.4; R=287; p0=100.25e3; T0=293; Cd=0.7
G=p0*np.sqrt(gam/(R*T0))*(2/(gam+1))**((gam+1)/(2*(gam-1)))
for frac,lab in [(0.056,'air-model a term (5.6 % of idle airflow)'),(0.17,'pure-leak fit (17 %)')]:
  for mair in (1.5,1.9):
    m=frac*mair/1000; A_=m/(Cd*G); d=np.sqrt(4*A_/np.pi)*1000
    print(f'leak {lab}, idle air {mair} g/s: {m*1000:.3f} g/s -> choked orifice d = {d:.2f} mm (Cd {Cd})')
# idle air estimate
z=load(38); k=(z['rpm']<900)&(z['vss']<1)&(z['closed_loop']>0.5)
rpm=np.nanmedian(z['rpm'][k]); mp=np.nanmedian(z['map'][k]); inj=np.nanmedian(z['inj'][k]); T=np.nanmedian(z['iat'][k])+273.15
for VE in (0.6,0.7): print(f'idle SD air: rpm {rpm:.0f} MAP {mp:.1f} T {T:.0f}K VE {VE}: {mp*1000*1.590e-3*rpm/120*VE/(R*T)*1000:.2f} g/s')
for d in (0.8,1.0): print(f'idle fuel-based air (inj {inj:.2f}, dead {d}): {4*rpm/120*(inj-d)/1000*4*0.745*14.7:.2f} g/s')
