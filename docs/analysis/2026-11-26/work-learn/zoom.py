import pickle, datetime, sys, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as md
G = pickle.load(open('grid.pkl','rb'))
def ts(hms):
    d = datetime.datetime.fromtimestamp(G['t'][0]).date()
    return datetime.datetime.combine(d, datetime.time.fromisoformat(hms)).timestamp()
def zoom(a, b, out):
    m = (G['t'] >= ts(a)) & (G['t'] <= ts(b))
    T = [datetime.datetime.fromtimestamp(x) for x in G['t'][m]]
    g = lambda k: G[k][m]
    fig, ax = plt.subplots(7, 1, figsize=(15, 15), sharex=True)
    ax[0].plot(T, g('rpm'), lw=.7, label='rpm'); ax[0].plot(T, g('tgt'), lw=1, label='idle_target'); ax[0].legend(loc='upper left')
    ax[1].plot(T, g(0x10), lw=.8, label='ECT raw 0x10 (low=warm)'); ax[1].legend(loc='upper left')
    ax[2].step(T, g(0x2A), where='post', c='C3', label='0x2A'); ax2=ax[2].twinx(); ax2.step(T, g(0x2F), where='post', c='C4', label='0x2F'); ax[2].legend(loc='upper left'); ax2.legend(loc='upper right')
    ax[3].plot(T, g('stft'), lw=.5, label='stft'); ax[3].plot(T, g('ltft'), lw=1.2, label='ltft'); ax[3].legend(loc='upper left')
    ax[4].plot(T, g(0x29), lw=.7, label='0x29'); ax[4].plot(T, g(0x28), lw=.7, label='0x28'); ax[4].plot(T, g(0x27)*10, lw=.7, label='0x27 x10'); ax[4].legend(loc='upper left')
    ax[5].plot(T, g('map'), lw=.6, label='map kPa'); ax[5].plot(T, g('o2')*50, lw=.4, label='o2 x50'); ax[5].legend(loc='upper left')
    ax[6].plot(T, g('eld'), lw=.6, label='eld'); ax[6].plot(T, g(0x26), lw=.6, label='ign raw'); ax[6].plot(T, g(0x0F)*10, lw=.8, label='0x0F x10'); ax[6].legend(loc='upper left')
    for x in ax: x.grid(alpha=.3)
    ax[-1].xaxis.set_major_formatter(md.DateFormatter('%H:%M:%S'))
    plt.tight_layout(); plt.savefig(out, dpi=75); plt.close()
if __name__ == '__main__':
    zoom(sys.argv[1], sys.argv[2], sys.argv[3])
