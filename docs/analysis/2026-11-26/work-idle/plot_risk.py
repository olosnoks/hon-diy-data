import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
# closed-throttle bites (tps closed at fall start) and throttle bites; from dips.csv/launches.csv (hand-checked)
closed=[('A1 22:39:41',546,94,-498),('A1b 22:39:44',590,-25,-297),('pre-A2 22:53:12',720,14,-134),('A2 22:53:15',463,199,-564),('A3 23:12:37',554,51,-759),
        ('A4 23:25:56',519,349,-707),('A5 23:26:12',495,314,-1040),('A6 23:26:24',440,556,-1689)]
roll=[('22:39:48 4 km/h',662,-34,-334),('23:17:12 3 km/h',735,545,-611),('23:05:20 4 km/h',736,416,-1016),('23:19:52 5 km/h',799,564,-1006),('22:59:28 5 km/h',779,464,-802)]
thr=[('B0 22:41:44',792,198,None),('B1 22:54:22',670,331,None),('B2 22:58:05',702,349,None),('B3 23:03:01',906,383,None)]
f,ax=plt.subplots(1,2,figsize=(15,5.5))
for lab,m,e,r in closed: ax[0].scatter(e,m,c='C3',s=40); ax[0].annotate(lab,(e,m),fontsize=7,xytext=(3,3),textcoords='offset points')
for lab,m,e,r in thr: ax[0].scatter(e,m,c='C0',s=40,marker='s'); ax[0].annotate(lab,(e,m),fontsize=7,xytext=(3,3),textcoords='offset points')
for lab,m,e,r in roll:
    ax[0].scatter(e,m,c='C2',s=40,marker='^'); ax[0].annotate(lab,(e,m),fontsize=7,xytext=(3,3),textcoords='offset points')
    ax[1].scatter(-r,m,c='C2',s=40,marker='^'); ax[1].annotate(lab,(-r,m),fontsize=7,xytext=(3,3),textcoords='offset points')
ax[0].scatter([],[],c='C2',marker='^',label='still rolling 3-5 km/h, throttle closed at bite')
ax[0].scatter([],[],c='C3',label='from standstill, throttle closed at bite'); ax[0].scatter([],[],c='C0',marker='s',label='from standstill, throttle open at bite')
ax[0].axhline(400,c='grey',ls=':'); ax[0].set_xlabel('rpm above idle target when the fall started'); ax[0].set_ylabel('minimum rpm'); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
ax[0].set_title('clutch take-ups: minimum rpm')
for lab,m,e,r in closed: ax[1].scatter(-r,m,c='C3',s=40); ax[1].annotate(lab,(-r,m),fontsize=7,xytext=(3,3),textcoords='offset points')
ax[1].axhline(400,c='grey',ls=':'); ax[1].set_xlabel('steepest rpm fall (rpm/s, over ~0.35 s)'); ax[1].set_ylabel('minimum rpm'); ax[1].grid(alpha=.3)
ax[1].set_title('closed-throttle take-ups: fall rate vs minimum')
plt.tight_layout(); plt.savefig('risk_scatter.png',dpi=80)
