"""Export the sparse bay/row/tier locations in the STS 6.6 snapshot."""
from pathlib import Path
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Patch
from matplotlib.font_manager import FontProperties
from ship_structure_data import DEFAULT_SOURCE,read_structure


def render(source=DEFAULT_SOURCE,output=None):
    data=read_structure(source)
    output=Path(output or Path(__file__).resolve().parents[2]/'outputs/vessel-structure-plan.png')
    output.parent.mkdir(parents=True,exist_ok=True)
    font=FontProperties(fname='C:/Windows/Fonts/simhei.ttf')
    plt.rcParams['font.family']=font.get_name()
    plt.rcParams['axes.unicode_minus']=False
    colors={'C':'#347fa4','D':'#329a8c','1':'#d89b36'}
    bays=sorted({s['bay'] for s in data['slots']})
    all_rows={seq:row for g in data['groups'] for seq,row in g['rows'].items()}
    deck=sorted({t for g in data['groups'] if g['zone']=='D' for t in g['tiers'].values()})
    hold=sorted({t for g in data['groups'] if g['zone']=='H' for t in g['tiers'].values()})
    tier_y={t:i for i,t in enumerate(hold)}
    tier_y.update({t:len(hold)+2+i for i,t in enumerate(deck)})
    fig,axes=plt.subplots(1,len(bays),figsize=(22,10),sharey=True)
    fig.patch.set_facecolor('#f6f8fb')
    fig.suptitle('6.6 船图结构箱位',fontproperties=font,fontsize=27,color='#173b59',y=.965)
    fig.text(.5,.905,'接口快照：507 个有效箱位  |  舱内 165  ·  甲板 342  |  只绘制 locationList 中存在的位置',
             ha='center',fontproperties=font,fontsize=13,color='#496276')
    for ax,bay in zip(axes,bays):
        slots=[s for s in data['slots'] if s['bay']==bay]
        ax.set_facecolor('white')
        for s in slots:
            x=s['row_seq']-1;y=tier_y[s['tier']]
            ax.add_patch(Rectangle((x-.46,y-.44),.92,.88,facecolor=colors.get(s['size_code'],'#888888'),edgecolor='white',linewidth=.5))
            ax.text(x,y,s['size_code'],color='white',ha='center',va='center',fontsize=7)
        ax.axhline(len(hold)+.55,color='#8c9faf',linestyle='--',linewidth=1)
        ax.set_title(f'Bay {bay:02d}\n{len(slots)} 个箱位',fontproperties=font,fontsize=15,color='#173b59',pad=13)
        seqs=sorted(all_rows)
        ax.set_xticks([s-1 for s in seqs],[f'{all_rows[s]:02d}' for s in seqs],fontsize=8)
        ax.set_yticks([tier_y[t] for t in hold+deck],[f'{t:02d}' for t in hold+deck],fontsize=9)
        ax.tick_params(axis='both',length=0,pad=5)
        ax.set_xlim(-.7,max(seqs)-.3);ax.set_ylim(-.8,max(tier_y.values())+.8)
        ax.set_aspect('equal')
        ax.set_xlabel('列号',fontproperties=font,fontsize=11,labelpad=9)
        for spine in ax.spines.values():spine.set_color('#dce4eb')
    axes[0].set_ylabel('层号  ·  舱内 / 甲板',fontproperties=font,fontsize=12,labelpad=12)
    fig.legend(handles=[Patch(facecolor=v,label='size = '+k) for k,v in colors.items()],loc='lower center',
               ncol=3,bbox_to_anchor=(.5,.07),frameon=False,fontsize=12)
    fig.text(.5,.045,'颜色仅表示原始 C / D / 1 编码；空白位置未提供，不补绘。此图表示箱位结构，不代表实箱在位。',
             ha='center',fontproperties=font,fontsize=11,color='#64788a')
    fig.subplots_adjust(left=.045,right=.985,top=.83,bottom=.16,wspace=.13)
    fig.savefig(output,dpi=150,facecolor=fig.get_facecolor());plt.close(fig)
    return str(output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--output',type=Path)
    args=p.parse_args();print(render(args.source,args.output))
