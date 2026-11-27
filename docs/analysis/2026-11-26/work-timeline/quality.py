from common import *
S=json.load(open('segments.json'))
Q={}
for k,d in DRIVES.items():
    st=stream_times(d)
    blocks={n:v for n,v in st.items() if n.startswith('ecu/') and n!='ecu/link'}
    allt=np.sort(np.concatenate(list(blocks.values())))
    t,rpm=chan(d,'rpm'); _,vss=chan(d,'vss')
    ev=[e for e in events(d) if e[1]=='link_error']
    evt=np.array([e[0] for e in ev]); evk=[json.loads(e[3])['kind'] for e in ev]
    span=allt[-1]-allt[0]
    print(f'=== {k}: ECU reads total {len(allt)} over {span:.0f}s = {len(allt)/span:.2f}/s')
    for n,v in sorted(blocks.items(),key=lambda z:-len(z[1])):
        print(f'   {n:10s} {len(v):6d} {len(v)/span:6.2f}/s')
    dd=np.diff(allt); gi=np.where(dd>0.5)[0]
    gaps=[]
    for i in gi:
        a,b=allt[i],allt[i+1]
        kinds=[evk[j] for j in range(len(evt)) if a-0.5<=evt[j]<=b+0.2]
        ib=np.searchsorted(t,a,side='right')-1; ia=np.searchsorted(t,b)
        gaps.append(dict(start=wall(a),dur=round(b-a,2),rpm0=float(rpm[ib]),rpm1=float(rpm[min(ia,len(t)-1)]),v0=float(vss[ib]),v1=float(vss[min(ia,len(t)-1)]),kinds=kinds,t=a))
    print('  gaps>0.5s:',len(gaps))
    for g_ in gaps: print(f"   {g_['start']} {g_['dur']:6.2f}s rpm {g_['rpm0']:.0f}->{g_['rpm1']:.0f} vss {g_['v0']:.0f}->{g_['v1']:.0f} errors {dict((x,g_['kinds'].count(x)) for x in set(g_['kinds']))}")
    # gap duration distribution all ECU inter-read intervals
    print('  inter-read pct',np.percentile(dd,[50,90,99,99.9]).round(3),'n>0.3',(dd>0.3).sum())
    from collections import Counter
    print('  error kinds',Counter(evk))
    Q[k]=dict(gaps=gaps,nerr=len(evt),kinds=Counter(evk))
    # per-10s read counts for plotting
    np.savez(f'rates_{k}.npz',**{n.replace('/','_').replace('+','p'):v for n,v in blocks.items()},evt=evt,evk=np.array(evk))
json.dump(Q,open('quality.json','w'),default=str)
