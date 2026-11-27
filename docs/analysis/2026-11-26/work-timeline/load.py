"""Read-only loader for the DB copy in this folder."""
import sqlite3, datetime, numpy as np
DB='hondalog.db'
E=1795753477.284756
DRIVES={'A':38,'B':39}
NAMES={38:'drive_2026_11_26_2224',39:'drive_2026_11_26_2251'}
c=sqlite3.connect(DB)
def wall(t): return datetime.datetime.fromtimestamp(E+t).strftime('%H:%M:%S')
def t_of(hms):
    h,m,s=[float(x) for x in hms.split(':')]
    d=datetime.datetime.fromtimestamp(E).replace(hour=int(h),minute=int(m),second=0,microsecond=0)
    return d.timestamp()+s-E
def chan(d,name):
    rows=c.execute("select c.stream_id,c.part,c.slot,s.rows from channels c join streams s on s.drive_id=c.drive_id and s.id=c.stream_id where c.drive_id=? and c.name=? order by s.rows desc",(d,name)).fetchall()
    if not rows: return None
    sid,part,slot,_=rows[0]
    r=np.array(c.execute(f"select t,v{slot} from samples where drive_id=? and stream_id=? and part=? order by t",(d,sid,part)).fetchall(),dtype=float)
    return r[:,0],r[:,1]
def stream_times(d):
    out={}
    for sid,name in c.execute("select id,name from streams where drive_id=?",(d,)):
        out[name]=np.array([r[0] for r in c.execute("select t from samples where drive_id=? and stream_id=? and part=0 order by t",(d,sid))])
    return out
def events(d):
    return c.execute("select t,kind,text,data from events where drive_id=? order by t",(d,)).fetchall()
def gps(d):
    r=np.array(c.execute("select t,lat,lon,speed,heading,fix,sats,hdop from gps_track where drive_id=? order by t",(d,)).fetchall(),dtype=float)
    return r
def utc(d):
    return chan(d,'gps_utc')
