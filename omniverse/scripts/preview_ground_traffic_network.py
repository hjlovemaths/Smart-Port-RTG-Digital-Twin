"""Save a plan-view review of the authored road paint."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import build_ground_traffic_network as network

network.build()
fig, ax = plt.subplots(figsize=(12,14),facecolor='#112b40')
ax.set_facecolor('#112b40')
for x,y,w,h in ((-241.5,-10,40,520),(-101.75,-10.25,251.75,510.25)):
    ax.add_patch(plt.Rectangle((x,y),w,h,color='#38434b'))
for y in network.CROSS[:4]:
    ax.add_patch(plt.Rectangle((-201.5,y-4.5),100,9,color='#38434b'))
for x in (-65,-44,-23,-2,19,40,61,82):
    for y,h in ((-0.5,122.85),(129.75,122.85),(260,122.85),(390.25,55.975)):
        ax.add_patch(plt.Rectangle((x-0.1,y),10,h,color='#5a6972'))
for start in (65,160):
    for i in range(10):
        ax.add_patch(plt.Rectangle((129,start+i*5.4),18,4.4,fill=False,edgecolor='#d6b849',linewidth=.6))
ax.add_collection(PolyCollection([[(x,y) for x,y,z in quad] for quad in network.WHITE],facecolors='#f1f3ed',linewidths=0))
ax.add_collection(PolyCollection([[(x,y) for x,y,z in quad] for quad in network.YELLOW],facecolors='#efd335',linewidths=0))
for text,x,y in [('QUAY 4 LANES / 2+2',-234,505),('PARKING',138,235),('EXTERNAL GATE',148,386.55),('1C',22,58),('2C',22,190),('3C',22,320),('4C',22,428)]:
    ax.annotate(text,(x,y),color='white',fontsize=9,ha='center',rotation=90 if text=='PARKING' else 0)
ax.set(xlim=(-253,172),ylim=(-22,530),aspect='equal',title='Ground traffic circulation / right-hand traffic')
ax.title.set_color('white')
ax.tick_params(colors='white')
ax.set_xlabel('X / m',color='white'); ax.set_ylabel('Y / m',color='white')
out=network.ROOT/'outputs/ground_traffic_network'
out.mkdir(parents=True,exist_ok=True)
fig.savefig(out/'traffic_plan.png',dpi=160,bbox_inches='tight')
print(out/'traffic_plan.png')
