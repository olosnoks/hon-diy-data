import load
c=load.con()
for ch in ('ect','rpm','iacv','ign','eld'):
    print(ch, list(c.execute("select round(t,1),round(value,1) from readings where drive='drive_2026_11_26_2045' and channel=? and t between 1706 and 1716 order by t",(ch,)))[::(10 if ch=='rpm' or ch=='ign' else 1)])
print(list(c.execute("select round(t,1),round(value,1) from readings where drive='drive_2026_11_26_2045' and channel='rpm' and t<3 order by t"))[:10])
print(list(c.execute("select round(t,1),round(value,1) from readings where drive='drive_2026_11_26_2045' and channel='ect' and t<30 order by t"))[:10])
