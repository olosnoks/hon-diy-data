import sys,re,pickle
d=pickle.load(open(sys.argv[0].replace('g.py','pages.pkl'),'rb'))
pat=re.compile(sys.argv[1],re.I); lo=int(sys.argv[2]) if len(sys.argv)>2 else 1; hi=int(sys.argv[3]) if len(sys.argv)>3 else 9999
for p in sorted(d):
    if lo<=p<=hi:
        m=pat.findall(d[p])
        if m: print(p,len(m),d[p][:60].replace('\n',' | '))
