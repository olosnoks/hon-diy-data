import numpy as np, os, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
W=os.path.dirname(os.path.abspath(__file__))
for did in (38,39):
    z=np.load(f'{W}/d{did}.npz'); t=z['t']/60
    f,ax=plt.subplots(7,1,figsize=(16,16),sharex=True)
    ax[0].plot(t,z['rpm'],lw=.5); ax[0].set_ylabel('rpm'); a2=ax[0].twinx(); a2.plot(t,z['vss'],'r',lw=.5); a2.set_ylabel('vss',color='r')
    ax[1].plot(t,z['map'],lw=.5); ax[1].set_ylabel('MAP kPa'); a2=ax[1].twinx(); a2.plot(t,z['tps'],'g',lw=.5); a2.set_ylabel('tps',color='g')
    ax[2].plot(t,np.clip(z['o2'],0,1.2),lw=.3); ax[2].plot(z['raw_o2_2'][:,0]/60,np.clip(z['raw_o2_2'][:,1],0,1.2),'r.',ms=2); ax[2].set_ylabel('o2 / o2_2(red)')
    ax[3].plot(t,z['stft'],lw=.5,label='stft'); ax[3].plot(t,z['ltft'],lw=1,label='ltft'); ax[3].plot(t,z['stft']+z['ltft'],lw=.5,label='total'); ax[3].legend(); ax[3].set_ylabel('%'); ax[3].set_ylim(-40,30)
    ax[4].plot(t,z['inj'],lw=.5); ax[4].set_ylabel('inj ms')
    ax[5].plot(z['raw_ect'][:,0]/60,z['raw_ect'][:,1],'.',ms=2,label='ect'); ax[5].plot(z['raw_iat'][:,0]/60,z['raw_iat'][:,1],'.',ms=2,label='iat'); ax[5].legend(); ax[5].set_ylabel('°C')
    ax[6].plot(z['raw_closed_loop'][:,0]/60,z['raw_closed_loop'][:,1],lw=.7,label='closed_loop'); ax[6].plot(z['raw_fuel_status'][:,0]/60,z['raw_fuel_status'][:,1]/2,lw=.7,label='fuel_status/2'); ax[6].legend()
    ax[-1].set_xlabel('min (session)'); f.suptitle(str(z['name'])); f.tight_layout(); f.savefig(f'{W}/overview_{did}.png',dpi=80); plt.close(f)
