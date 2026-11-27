import numpy as np, pickle, sys
from common import wall, wday
T = pickle.load(open('T2.pkl', 'rb'))
t = T['t']
COLS = [('rpm', '%4.0f'), ('target', '%4.0f'), ('b10', '%3.0f'), ('iacv', '%4.1f'), ('iac_cmd', '%3.0f'), ('x2a', '%2.0f'), ('x2f', '%3.0f'),
        ('stft', '%5.1f'), ('ltft', '%5.1f'), ('o2', '%4.2f'), ('map', '%4.1f'), ('eld', '%5.1f'), ('b18', '%3.0f'), ('batt', '%4.1f'), ('b11', '%3.0f'),
        ('b08', '%3.0f'), ('b0b', '%3.0f'), ('b0c', '%2.0f'), ('b27', '%3.0f'), ('b26', '%3.0f'), ('inj', '%4.2f'), ('b3c', '%2.0f'), ('ba0', '%3.0f')]
def dump(tc, a=-60, b=60, step=1):
    m = (t >= tc + a) & (t <= tc + b)
    print('time     ' + ' '.join('%5s' % c[0][:5] for c in COLS))
    for i in np.nonzero(m)[0][::step]:
        print(wall(t[i]) + ' ' + ' '.join(('%5s' % (f % T[c][i])) if np.isfinite(T[c][i]) else '    .' for c, f in COLS))
if __name__ == '__main__':
    import datetime
    tc = datetime.datetime.strptime(sys.argv[1], '%Y-%m-%d %H:%M:%S').timestamp()
    dump(tc, int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 1)
