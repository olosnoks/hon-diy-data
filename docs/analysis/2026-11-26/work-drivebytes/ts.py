import pickle, sys, numpy as np, pandas as pd
pd.set_option('display.width',300); pd.set_option('display.max_columns',50); pd.set_option('display.max_rows',1000)
S,F=pickle.load(open('SF2.pkl','rb'))
a,b=sys.argv[1],sys.argv[2]
cols=sys.argv[3].split(',') if len(sys.argv)>3 else ['rpm','vss','gear','tps_raw','map','inj','ign','b08','b0b','b0f','b9a','b27','b28','b29','b2a','b2f','b3c','b10','b18','b17','b9c','ba0','b15','b3a','b32','b37']
w=S.wall.dt.strftime('%H:%M:%S')
m=(w>=a)&(w<=b)
x=S[m][cols].copy(); x.index=w[m]
print(x.round(2).to_string())
