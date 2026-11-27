import pickle, numpy as np, datetime
G = pickle.load(open('grid.pkl','rb'))
d0=datetime.datetime.fromtimestamp(G['t'][0]).date()
ts=lambda h: datetime.datetime.combine(d0, datetime.time.fromisoformat(h)).timestamp()
for a,b in (('21:40:30','21:44:15'),('21:44:40','21:46:50'),('22:18:00','22:27:50')):
    m=(G['t']>=ts(a))&(G['t']<=ts(b))
    print(a,b,'x2F mean %.2f'%np.nanmean(G[0x2F][m]),'MAPraw %.2f MAPkPa %.2f'%(np.nanmean(G['map'][m]+5)/0.716, np.nanmean(G['map'][m])), 'load9C %.1f'%np.nanmean(G[0x9C][m]),'rpm %.0f'%np.nanmean(G['rpm'][m]),'iacv %.1f'%np.nanmean(G[0x28][m]),'stft %.1f'%np.nanmean(G['stft'][m]),'ECTraw %.1f'%np.nanmean(G[0x10][m]),'batt %.2f'%np.nanmean(G['batt'][m]),'ign %.1f'%np.nanmean(G[0x26][m]),'inj', np.nanmean(G[0x24][m]*256+G[0x25][m])/250 if 0x25 in G else '')
