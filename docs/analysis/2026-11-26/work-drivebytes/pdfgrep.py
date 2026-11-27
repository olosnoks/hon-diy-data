import pypdf, re, pickle, os
P=r'C:\Users\skons\hondalog\manual\Honda Civic 1996-2000 Service Manual.pdf'
if not os.path.exists('manual_text.pkl'):
    r=pypdf.PdfReader(P); T=[]
    for i,p in enumerate(r.pages):
        try: T.append(p.extract_text() or '')
        except Exception: T.append('')
    pickle.dump(T,open('manual_text.pkl','wb'))
T=pickle.load(open('manual_text.pkl','rb'))
print('pages',len(T), 'with text', sum(1 for t in T if len(t)>50))
import sys
for pat in sys.argv[1:]:
    hits=[i+1 for i,t in enumerate(T) if re.search(pat,t)]
    print(pat, len(hits), hits[:60])
