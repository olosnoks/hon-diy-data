from load import *
for sid in (1,3,4):
  for r in c.execute("select t,hex(raw),latency_us,v0,v1,v2,v3 from samples where drive_id=39 and stream_id=? and part=0 and t between 2720 and 2721.5",(sid,)): print(sid,r)
t,u=utc(39); 
print(np.c_[t[:5],u[:5]], (E+t[:5])%86400)
d=(E+t)-u; print('utc minus? sample', d[:5], np.median(d%86400))
for name in ['load','load_ecu','map','tps','ect','iat','ac_sw','vtec','ac_clutch','fan','brake','eld','batt','alt_fr']:
  x=chan(39,name)
  if x: print(name,len(x[0]),np.nanpercentile(x[1],[0,5,50,95,100]).round(2))
