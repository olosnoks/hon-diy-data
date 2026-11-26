from prep import *
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
w=W(); o=pd.read_pickle('obd2.pkl'); o['loc']=pd.to_datetime(o.unix,unit='s')-pd.Timedelta(hours=6)
P='/home/claude/analysis/plots-sensors/'
f,ax=plt.subplots(8,1,figsize=(16,20),sharex=True)
def pid(p): s=o[o.pid=='obd2/'+p]; return s['loc'],s.v0
ax[0].plot(w['loc'],w.rpm,',',c='C0'); ax[0].plot(*pid('0C'),',',c='C1'); ax[0].set_ylabel('rpm'); ax[0].set_ylim(0,1600)
ax[1].plot(w['loc'],w.ect,',',c='C0',label='Honda 0x10'); ax[1].plot(*pid('05'),'.',ms=2,c='C1',label='PID 05'); ax[1].plot(w['loc'],w['iat'],',',c='C2'); ax[1].plot(*pid('0F'),'.',ms=2,c='C3'); ax[1].set_ylabel('ECT / IAT C'); ax[1].legend(loc='lower left')
ax[2].plot(w['loc'],(w.a26-46)/4,',',c='C0'); ax[2].plot(*pid('0E'),'.',ms=2,c='C1'); ax[2].axhspan(10,14,color='g',alpha=.1); ax[2].set_ylabel('ign deg (x-46)/4 vs PID0E')
ax[3].plot(w['loc'],w.a28,',',c='C0'); ax[3].set_ylabel('0x28 raw (iacv?)')
ax[4].plot(w['loc'],w.a17/10.45,',',c='C0'); ax[4].set_ylabel('batt V (x/10.45)'); ax[4].set_ylim(10,15.5)
ax[5].plot(w['loc'],w.a18,',',c='C0',label='0x18 alt_fr raw'); ax[5].plot(w['loc'],w.a19,',',c='C3',label='0x19 eld raw'); ax[5].legend(); ax[5].set_ylabel('raw')
ax[6].plot(w['loc'],w.a15/51.3,',',c='C0'); ax[6].plot(*pid('14'),'.',ms=2,c='C1'); ax[6].set_ylabel('O2 V')
ax[7].plot(w['loc'],w.a12*0.716-5,',',c='C0'); ax[7].plot(*pid('0B'),'.',ms=2,c='C1'); ax[7].set_ylabel('MAP kPa'); ax[7].set_ylim(15,45)
for a in ax: a.grid(alpha=.3)
ax[0].set_title('2026-11-25 idle session: Honda RAM reads (blue) vs OBD2 PIDs (orange), local time')
plt.tight_layout(); plt.savefig(P+'overview.png',dpi=80)
