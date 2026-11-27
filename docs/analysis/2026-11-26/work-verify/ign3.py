from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
E=obd('obd2/0E'); C=obd('obd2/0C'); T=obd('obd2/05'); M=obd('obd2/0B')
rpm_o,dc=at(C,E[:,0]); ect_o,de=at(T,E[:,0]); map_o,dm=at(M,E[:,0])
for i in np.where((E[:,1]>=14)|(rpm_o>850))[0]:
    print(dt.datetime.fromtimestamp(E[i,0]).strftime('%H:%M:%S'),'0E',E[i,1],'rpm',rpm_o[i],round(dc[i],1),'ect',ect_o[i],round(de[i],1),'map',map_o[i],round(dm[i],1))
