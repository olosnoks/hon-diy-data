import numpy as np, pickle, datetime
def ect_c(x):
    k = [155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437]
    return sum(c * x ** i for i, c in enumerate(k))
for r in (34,35,36,38,40,41,42,45,48,50,52,53,55,56,57,58,60,70,83,100,120,131): print(r, round(ect_c(r),1))
S, drives = pickle.load(open('series.pkl','rb'))
t,v,d=S[0x10]; r=S['rpm']
m=d==31
f = lambda t: datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')
print([ (f(a),int(b)) for a,b in zip(t[m][:40:3],v[m][:40:3])])
rt,rv,rd=S['rpm']; mm=rd==31; idx=np.nonzero(rv[mm]>300)[0][0]; print('engine start', f(rt[mm][idx]))
