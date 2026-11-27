import pickle, numpy as np
exec(open('fit1.py').read().split('best=[]')[0])
for name,dx,c,s in (('x28',x28,0,2),('x28',x28,0,2.5),('x29',x29,36,7),('x29',x29,39,7)):
  for mode in ('round','floor'):
    for tsl in (1000,2000,3000,4000,6000,8000,12000,16000,1e12):
        p=sim(dx,c,s,40,tsl,38,mode); a,e=score(p)
        nst = np.sum(np.diff(p[valid])!=0)
        print(name,c,s,mode,tsl,round(a,3),round(e,3),'pred steps',nst)
