"""Paint the connected quay/yard/parking/gate circulation overlay."""
from pathlib import Path
import re
from build_quay_yard_traffic_layout import rect, mesh_usda, segments_without

ROOT = Path(__file__).resolve().parents[2]
CROSS = (-5.375, 126.05, 256.30, 386.55, 496.30)
GATE_Y = 386.55
WHITE, YELLOW = [], []


def line(x1, y1, x2, y2, width=0.16, yellow=False, z=0.245):
    import math
    length = math.hypot(x2-x1, y2-y1)
    if length < 0.001:
        return
    dx, dy = -(y2-y1)/length*width/2, (x2-x1)/length*width/2
    (YELLOW if yellow else WHITE).append(((x1+dx,y1+dy,z), (x2+dx,y2+dy,z),
                                         (x2-dx,y2-dy,z), (x1-dx,y1-dy,z)))


def arrow(x, y, dx, dy, z=0.245):
    line(x-dx*1.8,y-dy*1.8,x+dx*1.8,y+dy*1.8,0.28,z=z)
    for side in (-1,1):
        line(x+dx*1.8,y+dy*1.8,x+dx*0.5-dy*side*0.8,
             y+dy*0.5+dx*side*0.8,0.28,z=z)


def vertical(x, start, end, yellow=False, dashed=False):
    gaps = tuple((y-6,y+6) for y in CROSS)
    for a,b in segments_without(start,end,gaps):
        if dashed:
            t=a
            while t<b:
                line(x,t,x,min(t+4,b),yellow=yellow)
                t+=10
        else:
            line(x,a,x,b,yellow=yellow)


def build():
    WHITE.clear(); YELLOW.clear()
    # Four 3.5 m lanes inside the crane rails, with two directions each.
    for x in (-239.9,-225.9):
        vertical(x,-9,505)
    for x in (-236.4,-229.4):
        vertical(x,-9,505,dashed=True)
    for x in (-233.08,-232.72):
        vertical(x,-9,505,yellow=True)
    for y in (40,90,175,220,300,345,425,470):
        for x,dy in ((-238.15,-1),(-234.65,-1),(-231.15,1),(-227.65,1)):
            arrow(x,y,0,dy)
    # Two-way collector on the crane landside apron and two-way outer yard road.
    for x,lo,hi in ((-208,-211.5,-204.5),(118,110.8,125.2),(-78.5,-85.4,-71.6)):
        for edge in (lo,hi):
            vertical(edge,-9,499)
        for offset in (-0.18,0.18):
            vertical(x+offset,-9,499,yellow=True)
        for y in (30,95,165,230,300,360,440,480):
            arrow(x-1.8,y,0,-1)
            arrow(x+1.8,y,0,1)
    # Crossroads connect crane lanes, causeways, all yard entrances and gate.
    junctions = ((-241,-224),(-213,-202),(-86,-71),(109,126))
    yard_x = (-66.75,-45.75,-24.75,-3.75,17.25,38.25,59.25,80.25)
    junctions += tuple((x-3,x+3) for x in yard_x)
    for y in CROSS:
        start=-239.9 if y!=496.30 else -101.75
        end=150 if y in (GATE_Y,496.30) else 125.2
        for a,b in segments_without(start,end,junctions):
            for offset in (-0.18,0.18):
                line(a,y+offset,b,y+offset,yellow=True)
        if y != 496.30:
            for edge in (-3.7,3.7):
                for a,b in segments_without(-201.5,-101.5,()):
                    line(a,y+edge,b,y+edge)
        for x in (-175,-130,-95,-36,48,100):
            if x>=start:
                arrow(x,y-1.85,1,0)
                arrow(x,y+1.85,-1,0)
    # Existing truck work lanes under RTGs: one direction; cross-roads return.
    # Bay numbers increase with scene Y: all work lanes travel towards -Y.
    for x in yard_x:
        for y in (35,90,165,220,300,350,415):
            arrow(x,y,0,-1,z=0.18)
    # Bays open directly onto the 15 m collector; do not imply that the
    # two-metre residual strip is a usable truck lane.
    for y in (72,108,167,200):
        arrow(126.8,y,-1,0)
    # Gate entry/exit approaches and stop bar before joining the outer road.
    for edge in (-3.7,3.7):
        line(126,GATE_Y+edge,149.7,GATE_Y+edge)
    arrow(141,GATE_Y+1.85,-1,0)
    arrow(141,GATE_Y-1.85,1,0)
    line(127,GATE_Y+0.4,127,GATE_Y+3.3,0.35)
    # Hide obsolete shoreline stripes and the earlier uninterrupted divider.
    source=(ROOT/'omniverse/scenes/offshore_quay_layout.usda').read_text(encoding='utf-8')
    old=re.findall(r'def Cube "((?:WatersideLane_|LandsideLane_)[^"]+)"',source)
    # Additional historic dash opinions live in the dimensions layer.
    old += [f'LandsideLane_CenterDash_{i:02d}' for i in range(1,44)]
    hide='\n'.join('            over "'+name+'" {\n                token visibility = "invisible"\n            }' for name in old)
    meshes='\n'.join((mesh_usda('WhiteTrafficLines',(0.88,0.90,0.87),WHITE),
                       mesh_usda('YellowTrafficDividers',(0.96,0.76,0.04),YELLOW)))
    text='''#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
    subLayers = [@./parked_yard_trucks.usda@]
)
over "World" {
    over "SMART_PORT_OFFSHORE_QUAY" {
        over "Markings" {
HIDE
        }
    }
    over "SMART_PORT_QUAY_YARD_TRAFFIC_LAYOUT" {
        over "SMART_PORT_QUAY_YARD_TRAFFIC_YELLOW_LANE_CENTERLINES" {
            token visibility = "invisible"
        }
    }
    def Xform "GROUND_TRAFFIC_NETWORK" {
        custom string circulation = "Right-hand traffic; quay four lanes 2+2; bidirectional causeways and collectors; all RTG work lanes one-way from higher to lower bay numbers (negative Y); parking exits to outer collector; loaded and empty external trucks use 4C approach gate"
        custom double gateCenterY = 386.55
MESHES
    }
}
'''.replace('HIDE',hide).replace('MESHES',meshes)
    output=ROOT/'omniverse/scenes/ground_traffic_network.usda'
    output.write_text(text,encoding='utf-8')
    print(f'{output}: {len(WHITE)} white quads, {len(YELLOW)} yellow quads, {len(old)} obsolete markings hidden')


if __name__ == '__main__':
    build()
