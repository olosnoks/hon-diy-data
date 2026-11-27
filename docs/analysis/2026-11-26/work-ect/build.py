import lib, pickle, numpy as np
c = lib.con()
D = {}
for did in (33, 34, 35, 36, 37, 38, 39):
    D[did] = lib.load(did, c=c)
pickle.dump(D, open('drives.pkl', 'wb'))
