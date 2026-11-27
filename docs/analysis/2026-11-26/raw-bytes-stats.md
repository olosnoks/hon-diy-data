# Unknown P2P RAM bytes: statistics over whole drives (2026-11-26)

**Data.** I took a read-only copy of `hondalog.db` at 21:18 and put it in `work-stats/`. I used two drives:

- `drive_2026_11_26_2045` (id 33): 1715 s of idle. It reads 0x00–0x3C in full. The slow blocks come about 1630 times each.
- `drive_2026_11_26_2114` (id 34): only the first 245 s, because that is all the copy held. It has about 233 reads per byte.

**Method.** Each byte comes from the raw `ecu_reads` bytes, not from decoded channels. I put everything on a 1 Hz grid, using the mean within each second for the ~9 Hz channels. For every byte I computed:

- range, distinct values, change count and run lengths;
- per-bit duty and toggle counts;
- Pearson and Spearman correlation against rpm, map, tps, o2, ect, iat, batt, alt_fr, eld, stft, ltft, inj (16-bit 0x24:0x25), ign, 0x27, 0x28, 0x3C and 0x08;
- lagged cross-correlation over ±10 s (a positive lag means the unknown byte trails the channel);
- the 16-bit pairings with each neighbour, both big- and little-endian.

The scripts are in `work-stats/` (`load.py`, `basic.py`, `corr.py`, `dig*.py`, `plots.py`). The plots are `work-stats/01_overview_2045.png`, `02_0x29_vs_0x28.png` and `03_corr_heatmap_2045.png`.

**Headline.** Of the 37 target bytes, **31 never changed in either drive**. Only five moved: 0x1C, 0x29, 0x2A, 0x32 and 0x37. Of those five, only 0x1C and 0x29 carry real signal.

## Table

| byte | behaviour (drive 33 / drive 34) | best correlate (r, lag) | best guess | formula / units | conf. |
|---|---|---|---|---|---|
| 0x03 | constant 0 / 0 | – | unused, or a flag never set at idle | – | high (that it is constant) |
| 0x04 | constant 0 / 0 | – | same | – | high |
| 0x05 | constant 0 / 0 | – | same | – | high |
| 0x06 | constant 0 / 0 | – | same | – | high |
| 0x07 | constant 0 / 0 | – | same | – | high |
| 0x0D | constant 0 / 0 | – | same | – | high |
| 0x0E | constant 0 / 0 | – | same | – | high |
| 0x16 | constant 0 / 0 | – | same | – | high |
| 0x1A | constant 0 / 0 | – | same | – | high |
| 0x1B | constant 0 / 0 | – | same | – | high |
| **0x1C** | 153–179, 21 values, continuous | **eld 0x19: r = +1.000, lag 0** | **exact copy of 0x19 (ELD), byte for byte at every read** (1640/1640 and 235/235) | same formula as `eld` | **high** |
| 0x1D | constant 0 / 0 | – | – | – | high |
| 0x1E | constant 0 / 0 | – | – | – | high |
| 0x1F | constant 0 / 0 | – | – | – | high |
| **0x29** | 36–103, 56 values, continuous; flat at ~39 when 0x28 = 0 | **iacv 0x28: r = +0.95 (Spearman +0.93), lag 0/+1** (drive 34: +0.84) | **a fixed monotone, concave function of 0x28**: an IAC command in different units. Most likely the actual valve duty after a linearising table, with 0x28 as the air demand or correction. | lookup, not linear (see evidence). If it is a duty: duty ≈ 0x29/255, i.e. ~15 % at rest and ~40 % at the 0x28 maximum (unverified) | high (relationship); low (meaning) |
| **0x2A** | 6 → 5 → 4: one step per drive, never back up | iat r = +0.83 (drive 33), +0.41 (drive 34). This is just two trends lining up, not a real relationship. | slowly falling learned or timer value. Not a sensor copy. | none | low |
| 0x2B | constant 0 / 0 | – | – | – | high |
| 0x2C | constant 0 / 0 | – | – | – | high |
| 0x2D | constant 0 / 0 | – | – | – | high |
| 0x2E | constant 0 / 0 | – | – | – | high |
| 0x2F | constant 0 / 0 | – | – | – | high |
| 0x30 | constant 0 / 0 | – | – | – | high |
| 0x31 | constant 0 / 0 | – | – | – | high |
| **0x32** | 45, with single-read dips to 44 in 4.3 % / 4.7 % of reads (only bit 0 toggles) | nothing: \|r\| ≤ 0.12 at any lag; every Mann-Whitney p > 0.07 | software counter or timer that reloads to 45 and is sometimes caught one tick down | – | low |
| 0x33 | constant 55 / 55 | – | calibration constant copied to RAM, or a reload value | – | high (that it is constant) |
| 0x34 | constant 0 / 0 | – | – | – | high |
| 0x35 | constant 45 / 45 (**never** dips) | – | constant, same value as 0x32 and 0x37 | – | high (that it is constant) |
| 0x36 | constant 7 / 7 | – | constant | – | high |
| **0x37** | 45, with single-read dips to 44 in 7.4 % / 7.3 % of reads (only bit 0 toggles) | nothing continuous (\|r\| ≤ 0.10). Its dips coincide with 0x3C = 2 in the same read 13 times against 4.4 expected (p ≈ 5e-4); in drive 34, 1 against 0.5. Slightly higher rpm in drive 33 (824 vs 805, p = 0.005). | same kind as 0x32: a counter or timer caught mid-update | – | low |
| 0x38 | constant 25 / 25 | – | constant | – | high |
| 0x39 | constant 25 / 25 | – | constant | – | high |
| 0x3A | constant 25 / 25 | – | constant | – | high |
| 0x3B | constant 0 / 0 | – | – | – | high |
| *0x27 (known, unidentified)* | 0–4, a step function of rpm | rpm r = +0.83, lag 0 | **quantised rpm band above ~800 rpm, saturating at 4**: idle speed error or overspeed index (it rises with ign advance too) | 0x27 ≈ clamp(round((rpm − 790)/60), 0, 4) | medium |
| *0x3C (mapped "knock")* | 1, with single-read 2s in 3.7 % of reads (bits 0 and 1 swap) | dips come with higher rpm (866 vs 804, p = 1e-7) and higher ECT raw | probably not a knock-sensor voltage. Same family as 0x32 and 0x37. | – | low |

## Evidence

### 0x1C = 0x19 (ELD)

- Both bytes come back in the same 16+10 block. In every read of both drives, 0x1C equals 0x19 (`np.array_equal` is True: 1640/1640 and 235/235).
- The range, distinct values and number of changes are identical.
- This is a mirror. Most likely a "filtered ELD" or "ELD for the alternator FR logic" variable whose filter does nothing at this read rate.
- It adds no information. The evening of 25 Nov already showed the ELD spikes and fan cycles in 0x19.

### 0x29: a lookup of the IACV byte 0x28

Median 0x29 for each 0x28 value. The two drives agree to within one count wherever both have data.

| 0x28 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 12 | 13 | 14 | 15 | 16 | 18 | 20 | 26 | 32 | 35 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0x29 (drive 33) | 39 | 46 | 51.5 | 59 | 65 | 70 | 76 | 79 | 80 | 82 | 82 | 84 | 85 | 86 | 88 | 88 | 90 | 91 | 97 | 103 | 103 |
| 0x29 (drive 34) | – | – | – | – | – | – | – | – | 80 | 81 | 82 | 85 | 85 | 86 | 88 | – | – | – | – | – | – |

- **The curve has a knee.** Below 0x28 ≈ 7 it climbs about 5.8 counts of 0x29 per count of 0x28. Above that it climbs only about 1 count per count. With 0x28 = 0, 0x29 sits at 36–44.
- **0x28 explains almost all of it.** After removing this table, the residual has sd 1.1 counts. The residual does not correlate with batt, ECT, IAT, rpm, MAP, ELD, alt_fr, ign or stft (all \|r\| ≤ 0.12; IAT −0.21 in drive 34). So there is no battery-voltage compensation and no temperature term.
- **The two bytes move together.** Second-to-second changes correlate best at lag 0 (r = 0.51). Both bytes come from the same read, so I cannot tell which one the ECU computes first.
- **It is not a 16-bit pair.**
  - 0x28:0x29 never carries or wraps. 0x29 never comes near 0 or 255.
  - 0x29:0x2A has no carry either. 0x2A stepped while 0x29 sat at 81–83.
  - 0x32:0x33, 0x1B:0x1C and 0x1C:0x1D just repeat the varying byte, because the neighbour is constant.
- **It is not an ADC copy.** The best sensor is ECT raw (r = +0.62 / +0.70 at +4 s). That link runs through 0x28: the false-cold ECT episodes open the IAC. See `02_0x29_vs_0x28.png` and the t = 1708–1715 s restart: ECT raw 45 → 108, 0x28 0 → 35, 0x29 39 → 103.
- **Interpretation (low confidence).** The rotary IACV passes little air below a threshold duty. A table that spends a large duty step on the first counts of demand, then small steps after, is what a "linearise the valve" lookup would look like. So the best guess is: 0x28 = air demand or idle correction (what the app shows as `iacv`), and 0x29 = output duty (or the reverse).
- **A puzzle.** In drive 33, rpm was about 805 both with 0x28 = 0 / 0x29 = 39 (t = 0–480 s) and with 0x28 ≈ 10 / 0x29 ≈ 82 (t = 1050–1250 s), at the same MAP of ~40 raw. So 0x29 = 39 → 82 bought no extra airflow there. Either the valve sits in a dead band, or neither byte is the final valve output.
- **Suggested channel.** Add `raw_29` as "iacv_duty?" (x/2.55 %, unverified). It is a clean, less-quantised version of 0x28 when 0x28 is small.

### 0x2A: slow, one-way steps

- Drive 33: the value was 6 until t = 1032 s (21:02:30), then 5.
- Drive 34: the value was 5 until t = 176 s (21:17:47), then 4. The 21:xx snapshot showing 5 fits this.
- Bits: 6 = 110, 5 = 101, 4 = 100. It counts down; it is not a set of flags.
- **Not a fixed-period timer.** It held 6 for at least 1031 s, but went from 5 to 4 after 917 s.
- **Not a single IAT threshold.** IAT raw was about 121 at the first step and about 112 at the second.
- The IAT and rpm correlations (+0.83, +0.64) come from both series drifting over the drive. Inside drive 34 they fall to +0.41 / −0.34.
- **Candidates:**
  - a slow countdown kept in battery-backed RAM (it carried across the key cycle, since drive 34 started at 5);
  - an adaptation or learned step;
  - a heavily filtered temperature index with hysteresis.
- **Test:** log it across a cold start. If it resets high after a long soak, it is temperature- or warm-up-related.

### Do 0x32, 0x35 and 0x37 (all 45) move together? Do 0x38, 0x39 and 0x3A (all 25)?

**No.** 0x35, 0x38, 0x39 and 0x3A never moved: 1626 and 233 reads, a single value each.

0x32 and 0x37 sit at 45 and drop to 44 for a single read, rarely two or three:

- run lengths for 0x32: 62×1 and 4×2;
- run lengths for 0x37: 97×1, 10×2 and 1×3.

They dip independently of each other. Matching each 0x32 read to the nearest 0x37 read (~70 ms later), they dipped together 4 times against 5.2 expected (drive 34: 0 against 0.8).

The dips do not track any engine channel (`03_corr_heatmap_2045.png`, bottom rows). The best lagged \|r\| is 0.12 for 0x32 and 0.10 for 0x37.

**Pattern.** A value that is almost always 45 but is sometimes caught at 44 for one read is what a down-counter would look like if it is decremented and then reloaded to 45 shortly after. Another possibility is a constant that a routine briefly works on in place. These are probably task or timer counters, not engine quantities.

The one weak link: 0x37 dips coincide with 0x3C = 2 in the same 35+8 read about 3× more often than chance in drive 33 (p ≈ 5e-4). 0x3C = 2 itself goes with higher rpm and ECT raw. This hints that the dips happen more often when some periodic task runs more often, for example at higher rpm. The evidence is weak, and not repeated in the short drive 34.

0x33 = 55, 0x36 = 7 and the 25s are best read as calibration values copied into RAM, or timer reload values. Statistics over an idle-only drive cannot say more. A drive with real load, A/C and decel is the next thing to check.

### 0x27 (bonus: listed as known but unidentified)

Mean 0x27 in each rpm bin, drive 33:

| rpm | < 825 | 825–850 | 850–900 | 900–950 | 950–1000 | ≥ 1000 |
|---|---|---|---|---|---|---|
| mean 0x27 | 0.00 | 0.33 | 1.0 | 1.9–2.0 | 2.8–3.0 | 3.9–4.0 |

- It is 0 at every rpm below 825 (n = 981), and saturates at 4 from about 1000 rpm.
- This fits **0x27 ≈ clamp(round((rpm − 790)/60), 0, 4)**: a quantised "rpm above idle target" or rpm-band index.
- In drive 34 rpm stayed at about 790, so 0x27 stayed at 0. That is consistent.
- It is not an ignition limit. Logged ign rises with it, by about +1.5° raw per step.

### Side note on the "stall" at 21:10:54

The 1536.7 s `STALL (link lost)` trigger in drive 33 is not an engine stall in the data:

- The ~9 Hz rpm reads have no gap longer than 2 s anywhere in the drive.
- rpm reads 697–732 continuously from 1531 to 1544 s.
- Only one 1 Hz slow-block read went missing at 1535 s and one at 1537 s.
- The engine kept idling until the recording ended at 1715 s, where a false-cold ECT spike (raw 45 → 108) gave a fast-idle flare to 1142 rpm.

### Caveats

- Both drives are idle-only. vss was always 0 and TPS moved once. Byte 0x08 left its idle value (0x84 → 0x8C, bit 3, the `brake` bit) for only 3 reads. Bytes that only move under load, during decel fuel cut, with VTEC or with A/C cannot show up here. "Constant" means constant at a warm idle.
- Drive 34 contributes only 245 s, because the database copy was taken at 21:18.
- The 1 Hz grid hides anything faster than about 1 s in the slow blocks.
