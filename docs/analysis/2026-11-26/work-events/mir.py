import sqlite3
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
n=0;eq=0
for (raw,) in c.execute("select raw from ecu_reads where addr=22"):
    r=bytes(raw); n+=1; eq+= r[3]==r[6]
print('reads',n,'0x19==0x1C',eq)
