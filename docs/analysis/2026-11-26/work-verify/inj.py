from lib import *
import pickle
H=pickle.load(open('H.pkl','rb'))
a=H[0x24]; b=H[0x25]
assert (a[:,0]==b[:,0]).all()
inj=a.copy(); inj[:,1]=(a[:,1]*256+b[:,1])/250
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
t=inj[:,0]
rh,d1=at(r,t); mh,d2=at(H[0x12],t); ih,d3=at(H[0x11],t); bh,d4=at(H[0x17],t); eh,_=at(H[0x10],t)
sh,_=at(H[0x20],t); lh,_=at(H[0x22],t); cl,_=at(H[0x0F],t); tp,_=at(H[0x14],t)
ok=(d1<0.3)&(d2<0.3)&(rh>400)
x=inj[ok,1]; R=rh[ok]; MAP=mh[ok]*0.716-5; IAT=ectc(ih[ok]); V=bh[ok]/10.45; ECT=ectc(eh[ok]); trim=((sh[ok]+lh[ok])/128-2); CL=cl[ok].astype(int)&1; TPS=tp[ok]/2.55
print('n',len(x),'inj ms pct',np.percentile(x,[1,5,25,50,75,95,99]))
print('zeros (fuel cut?)',(x==0).sum(),' with rpm>1200&tps<9:',((x==0)&(R>1200)&(TPS<9)).sum())
w=(ECT>80)&(R<720)&(MAP<30)&(CL==1)
print('warm idle inj med %.3f ms, MAP %.1f kPa, rpm %.0f, IAT %.1f C, batt %.2f V, trims %.1f %%'%(np.median(x[w]),np.median(MAP[w]),np.median(R[w]),np.median(IAT[w]),np.median(V[w]),100*np.median(trim[w])))
# physics
Vd=1.590e-3/4; rho_air=lambda p,T: p*1000/(287*(T+273.15))
for ve in (0.6,0.7,0.8):
    air=rho_air(np.median(MAP[w]),np.median(IAT[w]))*Vd*ve*1000  # g per cyl per cycle
    fuel=air/14.7*(1+np.median(trim[w]))  # g
    flow=240*0.745/60  # g/s -> mg/ms = g/s
    eff_ms=fuel*1000/flow/1000*1000/1000
    eff_ms=fuel/flow*1000  # ms
    print('VE %.1f: air %.1f mg/cyl, fuel %.2f mg -> effective %.2f ms (+0.6-1.0 ms dead time = %.2f-%.2f)'%(ve,air*1000,fuel*1000,eff_ms,eff_ms+0.6,eff_ms+1.0))
# regression inj vs MAP/(IAT) in closed loop warm, steady
s=(ECT>75)&(CL==1)&(x>0)
dens=MAP/(IAT+273.15)*300
A=np.c_[dens[s]*(1+trim[s]),np.ones(s.sum())]
k,c0=np.linalg.lstsq(A,x[s],rcond=None)[0]
print('CL warm: inj = %.4f*MAP_norm*(1+trim) + %.3f ms ; corr %.3f; MAP range %.1f-%.1f'%(k,c0,np.corrcoef(dens[s],x[s])[0,1],MAP[s].min(),MAP[s].max()))
for m0 in range(12,40,3):
    q=s&(MAP>=m0)&(MAP<m0+3)
    if q.sum()>20: print('  MAP %d-%d n=%d inj med %.2f rpm med %.0f'%(m0,m0+3,q.sum(),np.median(x[q]),np.median(R[q])))
# batt dependence at same MAP (dead time comp)
q=w
for v0 in np.arange(12.5,15,0.3):
    qq=q&(V>=v0)&(V<v0+0.3)
    if qq.sum()>20: print('  batt %.1f n=%d inj med %.3f'%(v0,qq.sum(),np.median(x[qq])))
# cranking / key-on
q=(rh>50)&(rh<400)&(d1<0.3)
print('cranking inj ms pct',np.percentile(inj[q,1],[5,50,95]) if q.sum() else None, q.sum())
print('fuel flow warm idle L/h (no dead time):', np.median(x[w])*np.median(R[w])/120000*240*4*60/1000)
