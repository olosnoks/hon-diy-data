from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
E=obd('obd2/0E'); C=obd('obd2/0C'); T=obd('obd2/05')
f=lambda u: dt.datetime.fromtimestamp(u).strftime('%H:%M:%S')
ig=H[0x26]; rpm=H[0x00]; rpm2=H[0x01]
# boundaries
for d in (2,3,4,5):
    m=E[:,2]==d; tt=E[m,0]
    print('OBD2 drive',d,f(tt[0]),f(tt[-1]),'first 0E',E[m,1][:5],'last 0E',E[m,1][-5:])
    mc=C[:,2]==d; print('   rpm first',C[mc,1][:3],'last',C[mc,1][-3:])
    mt=T[:,2]==d; print('   ect first',T[mt,1][:2],'last',T[mt,1][-2:])
for d in (1,6):
    m=ig[:,2]==d; tt=ig[m,0]
    r=(rpm[rpm[:,2]==d][:,1]*256+rpm2[rpm2[:,2]==d][:,1])/4
    ec=H[0x10]; me=ec[:,2]==d
    print('Honda drive',d,f(tt[0]),f(tt[-1]),'first ign raw',ig[m,1][:8],'last',ig[m,1][-8:])
    print('   rpm first',r[:5],'last',r[-5:],' ect first',ectc(ec[me,1][:3]),'last',ectc(ec[me,1][-3:]))
