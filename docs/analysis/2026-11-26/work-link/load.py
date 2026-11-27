"""Load per-read rows and link flags for one drive from the read-only DB copy."""
import sqlite3, sys
from pathlib import Path
DB = Path(__file__).with_name("hondalog.db")

def load(drive_id):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    streams = {sid: (name, raw_len) for sid, name, raw_len in
               c.execute("select id,name,raw_len from streams where drive_id=?", (drive_id,))}
    reads = [(t, streams[sid][0], len(raw), lat) for sid, t, raw, lat in c.execute(
        "select stream_id,t,raw,latency_us from samples where drive_id=? and part=0 and raw is not null order by t",
        (drive_id,))]
    link_sid = [s for s, (n, _) in streams.items() if n == "ecu/link"]
    flags = list(c.execute("select t,v0 from samples where drive_id=? and stream_id=? order by t,n",
                           (drive_id, link_sid[0]))) if link_sid else []
    epoch = c.execute("select epoch_unix from drives where id=?", (drive_id,)).fetchone()[0]
    return reads, flags, epoch
