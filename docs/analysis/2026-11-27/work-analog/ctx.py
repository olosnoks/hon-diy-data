import sys,re
L=open('manual_pages.txt',encoding='utf-8',errors='replace').read().split('\n')
page=[0]*len(L);p=0
for i,l in enumerate(L):
    m=re.match(r'=====PAGE (\d+)=====',l)
    if m:p=int(m.group(1))
    page[i]=p
for arg in sys.argv[1:]:
    a,b=map(int,arg.split(':'))
    print(f'--- lines {a}-{b} (pdf pages {page[a-1]}-{page[b-1]})')
    for i in range(a-1,b): print(f'{page[i]}|{L[i]}')
