import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from common import *
for name in DR:
    d,ep=load(name)
    f,ax=plt.subplots(6,1,figsize=(22,16),sharex=True)
    m=lambda ch: (d[ch][:,0]/60, d[ch][:,1])
    ax[0].plot(*m('rpm'),lw=.4); ax[0].plot(*m('idle_target'),lw=.8,c='r'); ax[0].set_ylim(300,2000); ax[0].set_ylabel('rpm/target'); ax[0].axhline(600,c='k',lw=.3)
    ax[1].plot(*m('vss'),lw=.5); ax[1].set_ylabel('vss')
    ax[2].plot(*m('iac_cmd'),lw=.6,label='iac_cmd'); ax[2].plot(*m('iacv'),lw=.6,label='iacv'); ax[2].legend(); 
    ax[3].plot(*m('ect'),lw=.6); ax[3].set_ylabel('ect')
    ax[4].plot(*m('tps'),lw=.4); ax[4].set_ylabel('tps'); ax[4].set_ylim(5,30)
    ax[5].plot(*m('eld'),lw=.6,label='eld'); ax[5].plot(*m('map'),lw=.3,label='map'); ax[5].legend()
    for a in ax: a.grid(alpha=.3)
    plt.tight_layout(); plt.savefig(f'overview_{name[-4:]}.png',dpi=70)
