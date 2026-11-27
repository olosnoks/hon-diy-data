from lib import *
import pickle, datetime as dt, sys
H=pickle.load(open('H.pkl','rb'))
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
def trace(t0s,t1s,step=1.0):
    base=dt.datetime.strptime(t0s,'%Y-%m-%d %H:%M:%S').timestamp(); end=dt.datetime.strptime(t1s,'%Y-%m-%d %H:%M:%S').timestamp()
    tg=np.arange(base,end,step)
    cols={'rpm':r,'eld':H[0x19],'altfr':H[0x18],'batt':H[0x17],'b0B':H[0x0B],'b08':H[0x08],'b0C':H[0x0C],'ect':H[0x10],'iac':H[0x28],'ign':H[0x26]}
    v={k:at(a,tg)[0] for k,a in cols.items()}
    print('time      '+' '.join('%6s'%k for k in cols))
    for i,u in enumerate(tg):
        print(dt.datetime.fromtimestamp(u).strftime('%H:%M:%S'),' '.join(('%6s'%format(int(v[k][i]),'08b')[-6:]) if k in ('b0B','b08','b0C') else '%6.0f'%v[k][i] for k in cols))
trace(*sys.argv[1:3], float(sys.argv[3]) if len(sys.argv)>3 else 1.0)
