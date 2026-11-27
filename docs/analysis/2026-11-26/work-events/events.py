"""Event-triggered response of every varying byte (drives 2045 + 2114)."""
import datetime
import numpy as np
import load, qplot

DRIVES = ['drive_2026_11_26_2045', 'drive_2026_11_26_2114']
VAR = ['rpm', 0x10, 0x12, 0x17, 0x18, 0x19, 0x1C, 0x20, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2F, 0x32, 0x37, 0x3C]


def find_events(B, R, L):
    ev = {}
    t, v = B[0x19]
    med = np.array([np.median(v[max(0, i - 30):i + 1]) for i in range(len(v))])
    sp = (med - v) >= 6
    ev['ELD load step (>=6 raw, ~+2.4 A)'] = t[1:][np.diff(sp.astype(int)) == 1]
    t, v = B[0x10]
    d = np.diff(v)
    ev['ECT false-cold entry (raw +8)'] = t[1:][d >= 8]
    ev['ECT snap back warm (raw -8)'] = t[1:][d <= -8]
    t, v = B[0x28]
    ev['IACV step up (>=3 raw)'] = t[1:][np.diff(v) >= 3]
    lk = L[L[:, 1] == 0][:, 0]
    keep = np.concatenate([[True], np.diff(lk) > 5])
    ev['link drop (short, engine running)'] = lk[keep]
    t, v = B[0x3C]
    ev['knock byte 1->2'] = t[1:][(v[1:] == 2) & (v[:-1] == 1)]
    t, v = B[0x08]
    ev['0x08 bit3 set'] = t[1:][((v[1:].astype(int) & 8) > 0) & ((v[:-1].astype(int) & 8) == 0)]
    if 0x2F in B:
        t, v = B[0x2F]
        ev['x2F increments'] = t[1:][np.diff(v) > 0]
    return ev


rows = []
traces = {}
for d in DRIVES:
    B, R, L, ep = load.load(d)
    G = load.grid(B, R, step=1.0)
    ev = find_events(B, R, L)
    for name, ts in ev.items():
        for te in ts:
            i0 = np.searchsorted(G['t'], te)
            if i0 < 15 or i0 + 25 >= len(G['t']):
                continue
            seg = {k: G[k][i0 - 15:i0 + 25] for k in VAR if k in G}
            traces.setdefault(name, []).append(seg)
            rows.append((name, d, te, datetime.datetime.fromtimestamp(ep + te).strftime('%H:%M:%S')))

out = open('event_response.txt', 'w')
summary = []
for name, segs in traces.items():
    out.write('\n=== %s  n=%d\n' % (name, len(segs)))
    out.write('%-6s %8s %8s %8s %8s  %s\n' % ('byte', 'pre', 'peak+', 'peak-', 'post10', 'first resp (s, |mean dev|>thr)'))
    for k in VAR:
        arr = np.array([s[k] for s in segs if k in s and len(s[k]) == 40])
        if not len(arr):
            continue
        m = np.nanmean(arr, axis=0)
        pre = np.nanmean(m[:13])
        sd = np.nanstd(arr[:, :13]) + 1e-9
        dev = m - pre
        thr = max(1.0, 2.5 * sd / np.sqrt(len(arr))) if k != 'rpm' else max(20, 2.5 * sd / np.sqrt(len(arr)))
        resp = np.where(np.abs(dev[13:]) > thr)[0]
        first = (resp[0] - 2) if len(resp) else None
        out.write('%-6s %8.1f %8.1f %8.1f %8.1f  %s\n' % (k if k == 'rpm' else load.name(k), pre, dev[13:].max(), dev[13:].min(),
                                                       np.nanmean(m[25:30]) - pre, '-' if first is None else '%+d s' % first))
        if first is not None and first <= 15:
            summary.append((name, k if k == 'rpm' else load.name(k), first, dev[13:].max(), dev[13:].min(), len(arr)))
    # plot
    F = qplot.Fig(4, '%s: mean of %d events (x = s from event)' % (name, len(segs)), height_per=170)
    x = np.arange(-15, 25)
    for k, ax in [('rpm', 0), (0x28, 1), (0x29, 1), (0x27, 1), (0x19, 2), (0x1C, 2), (0x10, 2), (0x2A, 3), (0x2F, 3), (0x32, 3), (0x37, 3), (0x3C, 3)]:
        arr = np.array([s[k] for s in segs if k in s and len(s[k]) == 40])
        if len(arr):
            F.line(ax, x, np.nanmean(arr, axis=0), k if k == 'rpm' else load.name(k))
    F.vline(0)
    F.save('event_%s.png' % name.split(' (')[0].replace(' ', '_').replace('>', '').replace('/', ''))
out.write('\n\nEVENT LIST\n')
for r in rows:
    out.write('%s | %s | %.1f | %s\n' % r)
out.close()
print('responding (first response within 15 s):')
for s in summary:
    print('%-40s %-6s first %+d s  max %+.1f min %+.1f  n=%d' % s)
