import pickle, numpy as np, pandas as pd
pd.set_option('display.width',250); pd.set_option('display.max_rows',500)
S,F=pickle.load(open('SF2.pkl','rb'))
W=lambda d: d.assign(wall=d.wall.dt.strftime('%H:%M:%S.%f').str[:11])
print('== fast rpm > 4800')
f=F[F.rpm>4800]; print(W(f)[['drive','wall','rpm','vss','tps_raw','map','inj','ign']].to_string())
print('== 0x0B b6 vs batt / alt_fr / ELD at rpm>3000')
h=S[(S.rpm>3000)]
h=h.assign(b6=(h.b0b.astype(int)//64)%2)
print(h.groupby('b6')[['b17','b18','b19','rpm','map','tps_raw']].median(), h.b6.value_counts())
# b6 transitions with surrounding
S['b6']=(S.b0b.astype(int)//64)%2
S['pb6']=S.groupby('drive').b6.shift()
tr=S[(S.b6!=S.pb6)&S.pb6.notna()]
print(W(tr)[['wall','b6','rpm','vss','tps_raw','map','inj','b17','b18','b19','b9a']].to_string())
