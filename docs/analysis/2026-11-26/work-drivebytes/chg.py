import pickle, sys, pandas as pd
pd.set_option('display.width',250); pd.set_option('display.max_rows',2000)
S,F=pickle.load(open('SF2.pkl','rb'))
b=sys.argv[1]; extra=sys.argv[2].split(',') if len(sys.argv)>2 else []
S['ectr']=S.b10
x=S[['drive','wall',b]+['rpm','vss','gear','tps_raw','map','inj','ectr','b11']+extra].copy()
x['prev']=x.groupby('drive')[b].shift()
c=x[(x[b]!=x.prev)]
c=c.assign(wall=c.wall.dt.strftime('%H:%M:%S'))
print(c.round(1).to_string())
