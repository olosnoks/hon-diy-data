import sys,re
txt=open('manual.txt',encoding='utf-8').read().replace('\x00','')
pages=re.split(r'\n===== PDF PAGE (\d+) =====\n',txt)
d={int(pages[i]):pages[i+1] for i in range(1,len(pages),2)}
import pickle; pickle.dump(d,open('pages.pkl','wb'))
