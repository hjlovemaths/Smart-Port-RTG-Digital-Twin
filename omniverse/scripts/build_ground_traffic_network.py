"""Paint the connected quay/yard/parking/gate circulation overlay."""
from pathlib import Path
import re
from build_quay_yard_traffic_layout import rect, mesh_usda, segments_without

ROOT = Path(__file__).resolve().parents[2]
CROSS = (-5.375, 126.05, 256.30, 386.55, 496.30)
CAUSEWAYS = CROSS[:-1]  # The terminal road is on land; it has no offshore bridge.
CROSS_LANE_OFFSET = 1.85
COLLECTOR_CENTER_X = 118.0
COLLECTOR_LANE_OFFSET = 1.8
QUAY_SOUTHBOUND_X = -238.15
QUAY_LANE_WIDTH = 3.2
QUAY_LANE_CENTERS = tuple(QUAY_SOUTHBOUND_X+i*QUAY_LANE_WIDTH for i in range(5))
QUAY_ROAD_EDGES = (QUAY_LANE_CENTERS[0]-QUAY_LANE_WIDTH/2,QUAY_LANE_CENTERS[-1]+QUAY_LANE_WIDTH/2)
# Return traffic uses the existing northbound collector outside the STS portal.
QUAY_RETURN_NORTHBOUND_X = -208.0+COLLECTOR_LANE_OFFSET
GATE_Y = 386.55
WHITE, YELLOW, YARD_ARROWS = [], [], []


def line(x1, y1, x2, y2, width=0.16, yellow=False, z=0.245, into=None):
    import math
    length = math.hypot(x2-x1, y2-y1)
    if length < 0.001:
        return
    dx, dy = -(y2-y1)/length*width/2, (x2-x1)/length*width/2
    (into if into is not None else YELLOW if yellow else WHITE).append(((x1+dx,y1+dy,z), (x2+dx,y2+dy,z),
                                         (x2-dx,y2-dy,z), (x1-dx,y1-dy,z)))


def arrow(x, y, dx, dy, z=0.245, scale=1., into=None):
    dx*=scale;dy*=scale
    line(x-dx*1.8,y-dy*1.8,x+dx*1.8,y+dy*1.8,0.28*scale,z=z,into=into)
    for side in (-1,1):
        line(x+dx*1.8,y+dy*1.8,x+dx*0.5-dy*side*0.8,
             y+dy*0.5+dx*side*0.8,0.28*scale,z=z,into=into)


def yard_arrow_positions():
    from yard_coordinate_mapping import YARD_BLOCKS,YARD_LAYOUT_START_Y_M,BAY_LENGTH_M,BAY_GAP_M
    pitch=BAY_LENGTH_M+BAY_GAP_M
    # Number panels lie at the odd-bay centres. Put the shortened arrows
    # halfway between neighbouring panels instead of on arbitrary whole metres.
    gaps=[YARD_LAYOUT_START_Y_M+b.start_m+BAY_LENGTH_M/2+(i+.5)*pitch
          for b in YARD_BLOCKS for i in range((b.bay_count+1)//2-1)]
    return tuple(min(gaps,key=lambda y:abs(y-old)) for old in (35,90,165,220,300,350,415))


def surface_faces(faces):
    return [tuple((x,y,(.190 if x < -101.5 else .116) if z>.24 else z) for x,y,z in face) for face in faces]


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
    WHITE.clear(); YELLOW.clear(); YARD_ARROWS.clear()
    # Five 3.2 m lanes, all travelling -Y. Keep the calibrated STS stop lane.
    for x in QUAY_ROAD_EDGES:
        vertical(x,-9,505)
    for i in range(1,5):
        x=QUAY_ROAD_EDGES[0]+i*QUAY_LANE_WIDTH
        vertical(x,-9,505,dashed=True)
    for y in (40,90,175,220,300,345,425,470):
        for x in QUAY_LANE_CENTERS:arrow(x,y,0,-1)
    # Two-way collector on the crane landside apron and two-way outer yard road.
    for x,lo,hi in ((-208,-211.5,-204.5),(COLLECTOR_CENTER_X,110.8,125.2),(-78.5,-85.4,-71.6)):
        for edge in (lo,hi):
            vertical(edge,-9,499)
        for offset in (-0.18,0.18):
            vertical(x+offset,-9,499,yellow=True)
        for y in (30,95,165,230,300,360,440,480):
            arrow(x-COLLECTOR_LANE_OFFSET,y,0,-1)
            arrow(x+COLLECTOR_LANE_OFFSET,y,0,1)
    # Crossroads connect crane lanes, causeways, all yard entrances and gate.
    # Keep the bridge mouth clear of median paint while long trucks turn
    # from the landside collector into the causeway's eastbound lane.
    junctions = ((-241,-222.5),(-213,-196.5),(-86,-71),(109,126))
    yard_x = (-66.75,-45.75,-24.75,-3.75,17.25,38.25,59.25,80.25)
    junctions += tuple((x-3,x+3) for x in yard_x)
    for y in CROSS:
        start=QUAY_ROAD_EDGES[0] if y!=496.30 else -101.75
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
                arrow(x,y-CROSS_LANE_OFFSET,1,0)
                arrow(x,y+CROSS_LANE_OFFSET,-1,0)
    # Existing truck work lanes under RTGs: one direction; cross-roads return.
    # Bay numbers increase with scene Y: all work lanes travel towards -Y.
    for x in yard_x:
        for y in yard_arrow_positions():
            arrow(x+.12,y,0,-1,z=.153,scale=.32,into=YARD_ARROWS)
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
    meshes='\n'.join((mesh_usda('WhiteTrafficLines',(0.88,0.90,0.87),surface_faces(WHITE)),
                       mesh_usda('YellowTrafficDividers',(0.96,0.76,0.04),surface_faces(YELLOW)),
                       mesh_usda('YardWorkLaneArrows',(0.88,0.90,0.87),YARD_ARROWS)))
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
        custom string circulation = "Quay five one-way lanes towards negative Y; northbound return via landside collector; bidirectional causeways and collectors; all RTG work lanes one-way towards negative Y; parking exits to outer collector"
        custom int quayLaneCount = 5
        custom double quayLaneWidth = 3.2
        custom double quayTravelDirectionY = -1
        custom double gateCenterY = 386.55
MESHES
    }
}
'''.replace('HIDE',hide).replace('MESHES',meshes)
    output=ROOT/'omniverse/scenes/ground_traffic_network.usda'
    temporary=output.with_suffix('.building.usda');temporary.write_text(text,encoding='utf-8');temporary.replace(output)
    print(f'{output}: {len(WHITE)} white quads, {len(YELLOW)} yellow quads, {len(old)} obsolete markings hidden')


if __name__ == '__main__':
    build()
