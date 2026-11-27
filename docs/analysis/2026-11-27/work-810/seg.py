import numpy as np, pickle
from common import wall, wday, runs, idle
T = pickle.load(open('T2.pkl', 'rb')); C = pickle.load(open('ctx.pkl', 'rb'))
t = T['t']; N = len(t)
S = np.full(N, 'X', dtype='<U1')
ok = idle & T['ect_ok']
S[ok & (T['rpm9'] < 740)] = 'L'
S[ok & (T['rpm9'] >= 775) & (T['rpm9'] <= 860)] = 'M'
S[ok & (S == 'X')] = '?'
S[~idle] = 'n'    # not idle (moving / revving / A/C)
S[idle & ~T['ect_ok']] = 'c'   # ECT not believable / glitch
T['S'] = S
pickle.dump(T, open('T3.pkl', 'wb'))
if __name__ == '__main__':
    # compress into segments
    i = 0
    while i < N:
        j = i
        while j + 1 < N and S[j + 1] == S[i] and T['drive'][j + 1] == T['drive'][i] and t[j + 1] - t[j] < 5: j += 1
        dur = t[j] - t[i]
        if dur >= 5 or S[i] in 'LM':
            print('d%-2d %s-%s %5.0fs %s  rpm %4.0f raw %3.0f-%3.0f iacv %4.1f eld %4.1f fan %d' % (T['drive'][i], wday(t[i]), wall(t[j]), dur, S[i],
                  np.nanmedian(T['rpm'][i:j + 1]), np.nanmin(T['b10'][i:j + 1]), np.nanmax(T['b10'][i:j + 1]), np.nanmedian(T['iacv'][i:j + 1]), np.nanmedian(T['eld'][i:j + 1]), T['fan_on'][i:j + 1].any()))
        i = j + 1
