"""Whole-drive timelines and scatter plots for the varying unknown bytes."""
import numpy as np
import load, qplot

for d in ['drive_2026_11_26_2045', 'drive_2026_11_26_2114']:
    B, R, L, ep = load.load(d)
    G = load.grid(B, R)
    t = G['t']
    F = qplot.Fig(6, '%s: whole drive (x = drive seconds)' % d, height_per=150)
    F.line(0, t, G['rpm'], 'rpm (1 s mean)')
    F.line(1, t, load.ect_c(G[0x10]), 'ECT degC (map poly)')
    F.line(1, t, 77.06 - G[0x19] / 2.5371, 'ELD A')
    F.line(2, t, G[0x29], 'x29', step=True)
    F.line(2, t, G[0x28], 'iacv raw', step=True)
    F.line(3, t, G[0x27], 'x27', step=True)
    F.line(3, t, G[0x2A], 'x2A', step=True)
    if 0x2F in G:
        F.line(3, t, G[0x2F], 'x2F', step=True)
    F.line(4, t, G[0x32], 'x32', step=True)
    F.line(4, t, G[0x37], 'x37', step=True)
    F.line(4, t, G[0x3C] + 40, 'knock+40', step=True)
    lk = L[L[:, 1] == 0][:, 0]
    F.scatter(5, lk, np.ones_like(lk), 'link drop', size=5)
    F.line(5, t, (G[0x08].astype(int) & 8) / 8 * 0.5, 'brake bit 0x08.3 (x0.5)', step=True)
    F.save('timeline_%s.png' % d[-4:])

# scatter: x29 vs iacv, x27 vs rpm (drive 2045 + 2114 pooled)
xs = {k: [] for k in ('x29', 'iacv', 'x27', 'rpm')}
for d in ['drive_2026_11_26_2045', 'drive_2026_11_26_2114']:
    B, R, L, ep = load.load(d)
    G = load.grid(B, R)
    xs['x29'].append(G[0x29]); xs['iacv'].append(G[0x28]); xs['x27'].append(G[0x27]); xs['rpm'].append(G['rpm'])
xs = {k: np.concatenate(v) for k, v in xs.items()}
ok = ~np.isnan(xs['rpm'])
xs = {k: v[ok] for k, v in xs.items()}
F = qplot.Fig(2, 'top: iacv raw (0x28) vs x29; bottom: x27 vs rpm (1 s), drives 2045+2114', height_per=300, link=False)
jit = np.random.default_rng(0).uniform(-0.25, 0.25, len(xs['x29']))
F.scatter(0, xs['x29'] + jit, xs['iacv'] + jit, 'samples', size=3)
xx = np.arange(30, 110)
F.line(0, xx, np.clip(xx - 72, 0, None), 'iacv = max(0, x29 - 72)', color='#d62728')
F.labels(0, 'iacv raw', 'x29')
F.scatter(1, xs['rpm'], xs['x27'] + jit, 'samples', size=3)
F.labels(1, 'x27', 'rpm')
F.save('scatter_x29_iacv_x27_rpm.png')


