"""Fit a divider + beta-thermistor model to the ECU's ECT curve, then express readings as added series resistance."""
import numpy as np, lib
raw = np.arange(25, 200); T = lib.ect_poly(raw) + 273.15
best = None
for B in np.arange(3000, 4200, 10):
    for la in np.arange(-1.5, 1.5, 0.005):
        a = 10**la; x = a * np.exp(B * (1 / T - 1 / 298.15)); r = 256 * x / (1 + x)
        err = np.sqrt(np.mean((r - raw)**2))
        if best is None or err < best[0]: best = (err, B, a)
err, B, a = best
print('fit rms %.2f counts, B=%d K, R25/Rpullup=%.3f' % (err, B, a))
def Rrel(Tc): return a * np.exp(B * (1 / (Tc + 273.15) - 1 / 298.15))  # sensor R in units of Rpullup
def Rapp(r): return r / (256 - r)  # apparent total R/Rp from raw
for Tc in (20, 40, 60, 80, 85, 90): print('  R(%d C)/Rp = %.3f' % (Tc, Rrel(Tc)))
# series resistance needed if the true coolant is Ttrue
for Ttrue in (84, 88):
    print('true %d C:' % Ttrue)
    for rr in (38, 42, 44, 47, 50, 54, 58, 63, 66, 72, 90, 110, 130, 150):
        print('   raw %3d (%.1f C) -> added R = %.3f Rp = %.2f x R_sensor' % (rr, lib.ect_poly(rr), Rapp(rr) - Rrel(Ttrue), (Rapp(rr) - Rrel(Ttrue)) / Rrel(Ttrue)))
