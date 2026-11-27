import pickle, numpy as np, pandas as pd
pd.set_option('display.width',250); pd.set_option('display.max_columns',40); pd.set_option('display.max_rows',400)
S,F=pickle.load(open('SF2.pkl','rb'))
S=S[S.rpm>300].copy()
S['rb']=(S.rpm//250*250).astype(int)
S['mb']=(S['map']//10*10).astype(int)
for b in ['b3c','b27']:
    print('==',b,'median by rpm band (rows) x MAP band (cols)')
    print(S.pivot_table(index='rb',columns='mb',values=b,aggfunc='median').round(1).to_string())
    print('count'); 
print(S.pivot_table(index='rb',columns='mb',values='b3c',aggfunc='count').fillna(0).astype(int).to_string())
# spearman
for b in ['b3c','b27','b2f','b28','b29']:
    r={c: round(S[[b,c]].corr(method='spearman').iloc[0,1],2) for c in ['rpm','map','tps_raw','inj','ign','vss','b9c','b10']}
    print(b,r)
