from load import *
import math, json
o=np.load('origin.npy'); R=6371008.8
GEARS=[('1',136.5,0.10),('2',76.2,0.06),('3',49.7,0.06),('4',36.0,0.05),('5',27.8,0.05)]
def xy(lat,lon):
    return np.radians(lon-o[1])*R*math.cos(math.radians(o[0])), np.radians(lat-o[0])*R
def clean_gps(d):
    g=gps(d); x,y=xy(g[:,1],g[:,2])
    ok=np.hypot(x,y)<50000
    # isolated jumps
    xx,yy=x.copy(),y.copy()
    for i in range(1,len(g)-1):
        if ok[i] and np.hypot(x[i]-x[i-1],y[i]-y[i-1])>300 and np.hypot(x[i]-x[i+1],y[i]-y[i+1])>300: ok[i]=False
    return g[ok],x[ok],y[ok],(~ok).sum()
PTS=['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
def where(x,y):
    r=math.hypot(x,y); b=(math.degrees(math.atan2(x,y))+360)%360
    if r<50: return 'at start point S'
    return f"{r/1000:.2f} km {PTS[int((b+11.25)//22.5)%16]} of S"
def gear_of(rpm,vss):
    if not(np.isfinite(rpm) and np.isfinite(vss)): return 'nodata'
    if vss<1: return 'stopped'
    if vss<8: return 'crawl'
    r=rpm/vss
    for g,cen,tol in GEARS:
        if abs(math.log(r/cen))<tol: return g
    return 'clutch/coast'
EVENTS={'A':[('22:36:17','cable unplugged (owner)'),('22:39:41','pull-away dip to 546 rpm'),('22:45:25','key-off at gas station')],
        'B':[('22:51:09','restart'),('22:53:15','pull-away near-stall 463 rpm'),('23:12:37','dip to 555 rpm'),('23:12:57','K-line silence 5.7 s'),
             ('23:16:26','K-line silence 8 s'),('23:25:56','launch dip (alarm 563 rpm)'),('23:26:24','launch dip (alarm 530 rpm)'),('23:26:58','key-off at home')]}
