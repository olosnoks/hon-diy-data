from common import *
S=json.load(open('segments.json')); EL=json.load(open('events_loc.json')); Q=json.load(open('quality.json'))
out={}
def f(x,fmt='{:.0f}'): return '' if x is None else fmt.format(x)
for k in DRIVES:
    L=['| # | start | dur (s) | segment | vss km/h (mean) | rpm min-max | gears (time share) | from -> to | km |','|---|---|---|---|---|---|---|---|---|']
    for i,s in enumerate(S[k]['segs']):
        if s['label']=='no ECU data':
            L.append(f"| {i+1} | {s['start']} | {s['dur']} | **no ECU data** | | | | {s['where0']} -> {s['where1']} | {s['km']:.2f} |"); continue
        L.append(f"| {i+1} | {s['start']} | {s['dur']} | {s['label']} | {f(s['vmin'])}-{f(s['vmax'])} ({f(s['vmean'])}) | {f(s['rmin'])}-{f(s['rmax'])} | {s['gears']} | {s['where0']} -> {s['where1']} | {s['km']:.2f} |")
    out['seg_'+k]='\n'.join(L)
    L=['| E# | time | event | where (from S) | route km | segment | min rpm (time) | vss | GPS km/h |','|---|---|---|---|---|---|---|---|---|']
    for i,e in enumerate(EL[k]):
        L.append(f"| E{i+1} | {e['hms']} | {e['txt']} | {e['where']} | {e['route_km']:.2f} | {e['seg']} (from {e['segstart']}) | {e['rmin']:.0f} ({e['tmin']}) | {e['vss']:.0f} | {e['gspd']:.0f} |")
    out['ev_'+k]='\n'.join(L)
    L=['| start | length (s) | rpm before -> after | vss before -> after | link_error rows in the gap |','|---|---|---|---|---|']
    for g in Q[k]['gaps']:
        from collections import Counter
        kc=', '.join(f"{a} {b}" for a,b in Counter(g['kinds']).most_common())
        L.append(f"| {g['start']} | {g['dur']:.2f} | {g['rpm0']:.0f} -> {g['rpm1']:.0f} | {g['v0']:.0f} -> {g['v1']:.0f} | {kc} |")
    out['gap_'+k]='\n'.join(L)
json.dump(out,open('frag.json','w'))
print(out['ev_A']); print(out['ev_B'])
