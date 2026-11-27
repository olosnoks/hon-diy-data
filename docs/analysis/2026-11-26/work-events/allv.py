import numpy as np, sqlite3, collections
c=sqlite3.connect('file:db2/hondalog.db?mode=ro',uri=True)
vals=collections.defaultdict(collections.Counter); drives=collections.defaultdict(set)
for did,addr,raw in c.execute('select drive_id,addr,raw from ecu_reads where addr is not null'):
    for i,b in enumerate(bytes(raw)):
        a=addr+i
        if a in (0x03,0x04,0x05,0x06,0x07,0x09,0x0C,0x0D,0x0E,0x16,0x1A,0x1B,0x1C,0x1D,0x1E,0x1F,0x27)or a>=0x29:
            vals[a][b]+=1; drives[a].add(did)
for a in sorted(vals):
    print('%02X drives=%s n=%d values=%s'%(a,sorted(drives[a]) if len(drives[a])<6 else '%d drives'%len(drives[a]),sum(vals[a].values()),dict(vals[a].most_common(8))))
