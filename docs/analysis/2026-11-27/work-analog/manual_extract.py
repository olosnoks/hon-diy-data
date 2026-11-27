import pypdf,sys
r=pypdf.PdfReader(r'C:\Users\skons\hondalog\manual\Honda Civic 1996-2000 Service Manual.pdf')
print(len(r.pages))
with open('manual_pages.txt','w',encoding='utf-8') as f:
    for i,p in enumerate(r.pages):
        try: t=p.extract_text() or ''
        except Exception as e: t='ERR '+str(e)
        f.write(f'\n=====PAGE {i+1}=====\n'+t)
