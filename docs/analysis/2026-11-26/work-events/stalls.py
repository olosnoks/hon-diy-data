"""Align every byte on the last good read before each stall/link-loss capture; table + plot."""
import sys, datetime
import numpy as np
import qplot
import load

c = load.con()
caps = list(c.execute('select name, drive, t_trigger from stalls order by capture_id'))
extra = sys.argv[1:]  # optional "drive:t" pseudo-events
for e in extra:
    d, t = e.split(':')
    caps.append(('event_%s_%s' % (d[-4:], t), d, float(t)))

SHOW = ['rpm', 0x10, 0x12, 0x17, 0x19, 0x20, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x32, 0x37, 0x3C, 0x08, 0x0A, 0x0B]
out = open('stall_tables.txt', 'w')
summary = []
cache = {}
for name, drive, ttrig in caps:
    if drive not in cache:
        cache[drive] = load.load(drive)
    B, R, L, ep = cache[drive]
    if not B:
        summary.append((name, drive, 'OBD2-only drive', float('nan'), []))
        continue
    allt = np.sort(np.concatenate([B[a][0] for a in B]))
    w = allt[(allt > ttrig - 10) & (allt <= ttrig + 0.5)]
    k = int(np.argmax(np.diff(w))) if len(w) > 1 else -1
    t_last = w[k] if (k >= 0 and np.diff(w)[k] > 1.0) else w[-1]
    after = allt[allt > t_last + 0.5]
    t_back = after[0] if len(after) else np.nan
    gap = t_back - t_last
    wall = lambda t: datetime.datetime.fromtimestamp(ep + t).strftime('%H:%M:%S')
    cols = [k for k in SHOW if k == 'rpm' or k in B]
    hdr = '   dt  wall      ' + ' '.join('%5s' % (k if k == 'rpm' else load.name(k)[:5]) for k in cols)
    out.write('\n=== %s  drive %s  last good read %.2f (%s)  link back %.2f (gap %.1f s)\n' % (name, drive, t_last, wall(t_last), t_back, gap))
    unk = [a for a in load.UNKNOWN if a in B]
    out.write('unknown bytes read in this drive: %s\n' % ' '.join('%02X' % a for a in unk))
    for seg, (a0, a1, ref) in {'before': (-30, 1, t_last), 'after': (0, 31, t_back)}.items():
        if np.isnan(ref):
            continue
        out.write('-- %s (dt relative to %s)\n%s\n' % (seg, 'last good read' if seg == 'before' else 'link return', hdr))
        for dt in range(a0, a1):
            tq = ref + dt + (0.0 if seg == 'before' else 0.99)
            vals = []
            for k in cols:
                if k == 'rpm':
                    rt, rv = R
                    m = (rt > tq - 1) & (rt <= tq)
                    vals.append(rv[m].mean() if m.any() else np.nan)
                else:
                    vals.append(load.at(B[k], np.array([tq]))[0])
            out.write('%5d  %s ' % (dt, wall(tq)) + ' '.join('%5.0f' % v for v in vals) + '\n')
    # precursor test: for each unknown byte that ever varied, compare last 30 s vs the drive's own baseline
    pre = []
    for a in unk:
        t, v = B[a]
        m30 = (t > t_last - 30) & (t <= t_last)
        m5 = (t > t_last - 5) & (t <= t_last)
        base = (t > t_last - 300) & (t <= t_last - 30)
        if m30.sum() == 0:
            continue
        if v[m30].min() != v[m30].max() or (base.any() and (v[m5].mean() < v[base].min() or v[m5].mean() > v[base].max())):
            pre.append('%02X[%s]' % (a, ','.join('%d' % x for x in v[m30][-8:])))
    summary.append((name, drive, wall(t_last), gap, pre))

    # plot
    hi = t_last + 40 + (gap if gap < 60 else 0)
    F = qplot.Fig(5, '%s (%s), gap %.1f s; x = s from last good read %s' % (name, drive, gap, wall(t_last)))
    rt, rv = R
    m = (rt > t_last - 32) & (rt < hi)
    F.line(0, rt[m] - t_last, rv[m], 'rpm')
    groups = [[0x28, 0x29, 0x27], [0x10, 0x19, 0x1C], [0x2A, 0x32, 0x37, 0x3C], [0x12, 0x17, 0x20, 0x26]]
    for i, addrs in enumerate(groups):
        for a in addrs:
            if a in B:
                t, v = B[a]
                mm = (t > t_last - 32) & (t < hi)
                F.line(i + 1, t[mm] - t_last, v[mm], load.name(a), step=True)
    F.vline(0)
    if gap < 60:
        F.span(0, gap)
    F.save('stall_%s.png' % name)
out.write('\n\nSUMMARY\n')
for s in summary:
    out.write('%s %s last=%s gap=%.1f s  unknown bytes moving in final 30 s: %s\n' % (s[0], s[1], s[2], s[3], ' '.join(s[4]) or 'none'))
out.close()
print(open('stall_tables.txt').read().split('SUMMARY')[1])
