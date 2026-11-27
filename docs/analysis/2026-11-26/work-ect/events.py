"""ECT step events in the two driving logs with driving context; driving event detection."""
import pickle, numpy as np, lib
D = pickle.load(open('drives.pkl', 'rb'))
GEARS = np.array([7.2, 13.0, 20.0, 27.6, 36.0])

def fast(d):
    t, rpm = d['rpm']; vss = lib.at(d['vss'], t)
    ratio = np.where(rpm > 500, vss / np.maximum(rpm, 1) * 1000, np.nan)
    gear = np.zeros(len(t), int)
    for i, g in enumerate(GEARS):
        gear[(np.abs(ratio - g) / g < 0.06) & (vss >= 5)] = i + 1
    return t, rpm, vss, gear

def drive_events(d):
    t, rpm, vss, gear = fast(d)
    ev = []
    # gear changes: last engaged gear -> next engaged gear (different), gap < 4 s
    last_g, last_t = 0, None
    for i in range(len(t)):
        g = gear[i]
        if g == 0: continue
        # require 3 consecutive samples in the gear
        if i + 2 >= len(t) or not (gear[i + 1] == g and gear[i + 2] == g): continue
        if last_g and g != last_g and t[i] - last_t < 4:
            ev.append((last_t, t[i], 'shift %d->%d' % (last_g, g)))
        last_g, last_t = g, t[i]
    # launches: vss 0 for >=2 s then >=5
    stopped_since = None; launches = []
    for i in range(len(t)):
        if vss[i] == 0:
            if stopped_since is None: stopped_since = t[i]
        else:
            if stopped_since is not None and t[i] - stopped_since >= 2:
                launches.append(t[i])
            stopped_since = None
    return ev, launches

if __name__ == '__main__':
    for did in (38, 39):
        d = D[did]
        ev, launches = drive_events(d)
        print('=== drive', did, 'shifts', len(ev), 'launches', len(launches))
        te, e = d['ect']; r = d['ect_raw'][1]
        br = d['brake']
        for i in range(1, len(te)):
            if abs(e[i] - e[i - 1]) >= 4 and te[i] - te[i - 1] < 3:
                t0 = te[i]
                near = [(round(a - t0, 1), s) for a, b, s in ev if -6 < a - t0 < 3]
                nl = [round(x - t0, 1) for x in launches if -10 < x - t0 < 5]
                ft, rpm, vss, gear = fast(d)
                w = (ft > te[i - 1] - 3) & (ft <= t0)
                print('%s ECT %5.1f -> %5.1f (raw %3d->%3d) vss %3d-%3d rpm %4d-%4d gear %s brake %s shifts %s launch %s' % (
                    lib.wall(t0), e[i - 1], e[i], r[i - 1], r[i], vss[w].min(), vss[w].max(), rpm[w].min(), rpm[w].max(),
                    sorted(set(gear[w])), lib.at(br, t0), near, nl))
        for a, b, s in ev[:200]: pass
