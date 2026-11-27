from lib import *
import pickle
H=honda()
pickle.dump(H,open('H.pkl','wb'))
for a in sorted(H): 
    d=H[a]; print(hex(a), len(d), sorted(set(d[:,2].astype(int)))[:3], '..', int(d[:,1].min()), int(d[:,1].max()))
