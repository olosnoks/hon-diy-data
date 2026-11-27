"""Radiator fan cycles from ELD steps (+4.5..+11 A held >=12 s), on every logged drive 6-39.
ECT reading at fan-on = reference for the ECT offset (thermoswitch closes at a fixed coolant temperature)."""
import numpy as np, lib, pickle
from common import T, DAY
w = lib.wall
out = []
for did in range(6, 40):
    idx = np.nonzero(T['drive'] == did)[0]
    if len(idx) < 40: continue
    t = T['t'][idx]; el = T['eld'][idx]
    if np.isnan(el).all(): continue
    elf = np.array([np.nanmedian(el[max(0, i - 2):i + 3]) for i in range(len(el))])
    i = 10
    while i < len(t) - 20:
        base = np.nanmedian(elf[i - 10:i - 2])
        if 4.5 < elf[i] - base < 11 and np.all(elf[i:i + 12] - base > 3.5) and np.all(np.diff(t[i - 10:i + 12]) < 3):
            j = i
            while j < len(t) - 1 and elf[j] - base > 3: j += 1
            # exact on: first raw sample above base+3.5 near i
            k = i
            while k > i - 3 and el[k - 1] - base > 3.5: k -= 1
            g = idx[k]
            ect_on = np.nanmedian(T['ect'][max(idx[0], g - 3):g + 1])
            raw_on = np.nanmedian(T['ect_raw'][max(idx[0], g - 3):g + 1])
            ect_min = np.nanmin(T['ect'][g:min(idx[-1], idx[j] + 30)])
            rpm_pre = np.nanmedian(T['rpm'][max(idx[0], g - 30):g - 3])
            rpm_on = np.nanmedian(T['rpm'][g + 3:idx[j]])
            rpm_post = np.nanmedian(T['rpm'][idx[j] + 10:min(idx[-1], idx[j] + 60)])
            v = np.nanmax(np.nan_to_num(T['vss'][max(idx[0], g - 5):idx[j]]))
            stable = np.nanmax(T['ect_raw'][max(idx[0], g - 20):g + 1]) - np.nanmin(T['ect_raw'][max(idx[0], g - 20):g + 1])
            out.append(dict(did=did, t=t[k], day=DAY[g], dur=t[j] - t[k], step=elf[i] - base, ect_on=ect_on, raw_on=raw_on, ect_min=ect_min,
                            rpm_pre=rpm_pre, rpm_on=rpm_on, rpm_post=rpm_post, vss=v, raw_range20=stable))
            i = j + 5
        else:
            i += 1
pickle.dump(out, open('fan.pkl', 'wb'))
for o in out:
    print('%s d%d %s +%4.1f A %3.0f s  ECT on %5.1f (raw %3.0f, 20s range %3.0f) min after %5.1f  rpm pre %4.0f during %4.0f post %4.0f vss %2.0f' % (
        o['day'], o['did'], w(o['t']), o['step'], o['dur'], o['ect_on'], o['raw_on'], o['raw_range20'], o['ect_min'], o['rpm_pre'], o['rpm_on'], o['rpm_post'], o['vss']))
for dy in ('11-25', '11-26'):
    a = np.array([o['ect_on'] for o in out if o['day'] == dy and o['raw_range20'] <= 3 and o['ect_on'] > 50])
    print(dy, 'n', len(a), 'ECT at fan-on median %.1f  IQR %.1f-%.1f  min %.1f max %.1f' % (np.median(a), *np.percentile(a, [25, 75]), a.min(), a.max()))
