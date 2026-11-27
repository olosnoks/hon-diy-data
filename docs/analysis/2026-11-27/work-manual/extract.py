import sys, pypdf
r = pypdf.PdfReader(sys.argv[1])
with open(sys.argv[2], 'w', encoding='utf-8') as f:
    for i, p in enumerate(r.pages):
        try: t = p.extract_text() or ''
        except Exception as e: t = f'[ERR {e}]'
        f.write(f'\n===== PDF PAGE {i+1} =====\n{t}\n')
print(len(r.pages))
