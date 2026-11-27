import sys, pypdf, os
r = pypdf.PdfReader(sys.argv[1]); out=sys.argv[2]; os.makedirs(out, exist_ok=True)
for pn in [int(x) for x in sys.argv[3:]]:
    p = r.pages[pn-1]
    for k, im in enumerate(p.images):
        fn = os.path.join(out, f'p{pn}_{k}_{im.name}')
        img = im.image
        print(pn, k, im.name, img.size, img.mode)
        if img.mode not in ('RGB','L'): img = img.convert('L')
        img.save(os.path.splitext(fn)[0] + '.png')
