"""Build lightweight service landscaping in measured, non-operating harbor parcels.
Run with Kit's USD Python. Geometry is batched by site/material; no new lights,
physics, textures or per-frame callbacks. Existing traffic corridors remain open.
"""
from pathlib import Path
from collections import defaultdict
import math
import json
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT/'omniverse/scenes/port_service_areas.usda'
BASE = '/World/PortServiceAreas'
COLORS = {
 'Concrete': ((.43,.46,.45),.87,0), 'Wall': ((.69,.72,.69),.8,0),
 'Roof': ((.075,.24,.32),.58,.3), 'Steel': ((.15,.20,.22),.55,.65),
 'Glass': ((.045,.115,.15),.22,.45), 'Asphalt': ((.085,.10,.105),.93,0),
 'White': ((.72,.75,.71),.7,0), 'Yellow': ((.8,.51,.045),.7,0),
 'Grass': ((.075,.15,.043),.96,0), 'Leaves': ((.065,.19,.055),.92,0),
 'LeavesLight': ((.12,.25,.07),.93,0), 'Bark': ((.19,.115,.065),.95,0),
 'Rubber': ((.018,.022,.025),.92,0), 'Blue': ((.025,.16,.29),.33,.35),
 'Red': ((.38,.055,.035),.4,.25), 'Pavers': ((.31,.34,.33),.9,0),
}
DATA = defaultdict(lambda: [[], [], []])
SOLIDS = []

def face(site,mat,verts):
 p,c,i=DATA[site,mat]; start=len(p); p.extend(verts); c.append(len(verts)); i.extend(range(start,len(p)))

def box(site,mat,x,y,z,w,d,h):
 a,b=x-w/2,x+w/2; c,e=y-d/2,y+d/2; f,g=z,z+h
 v=[(a,c,f),(b,c,f),(b,e,f),(a,e,f),(a,c,g),(b,c,g),(b,e,g),(a,e,g)]
 for ids in ((0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)):
  face(site,mat,[v[k] for k in ids])

def cyl(site,mat,x,y,z,r,h,n=8):
 lo=[(x+r*math.cos(k*math.tau/n),y+r*math.sin(k*math.tau/n),z) for k in range(n)]
 hi=[(a,b,z+h) for a,b,_ in lo]
 face(site,mat,list(reversed(lo))); face(site,mat,hi)
 for k in range(n): face(site,mat,[lo[k],lo[(k+1)%n],hi[(k+1)%n],hi[k]])

def tree(site,x,y,seed=0):
 cyl(site,'Bark',x,y,.25,.13,2.2)
 # Irregular compact broadleaf crowns, 40 triangles per tree, no alpha cards.
 n=10; rings=[]
 for j,(z,r) in enumerate(((1.8,.45),(2.4,1.0),(3.2,.85),(3.75,.25))):
  rings.append([(x+r*(1+.13*math.sin(k*3+seed))*math.cos(k*math.tau/n),y+r*math.sin(k*math.tau/n),z+.12*math.sin(k+seed)) for k in range(n)])
 for j in range(3):
  for k in range(n): face(site,'LeavesLight' if (k+seed)%4==0 else 'Leaves',[rings[j][k],rings[j][(k+1)%n],rings[j+1][(k+1)%n],rings[j+1][k]])
 face(site,'Leaves',list(reversed(rings[0]))); face(site,'LeavesLight',rings[-1])

def planter(site,x,y,w,d,trees=True):
 box(site,'Concrete',x,y,.12,w,d,.22); box(site,'Grass',x,y,.34,w-.3,d-.3,.025)
 if trees:
  count=max(1,int(d/5))
  for i in range(count): tree(site,x,y-d/2+(i+.5)*d/count,i)

def car(site,x,y,color='White'):
 box(site,'Rubber',x,y,.32,1.72,3.75,.28)
 box(site,color,x,y,.48,1.82,4.15,.47)
 # Tapered glazed cabin rather than a rectangular block.
 v=[(x-.81,y-1.4,.95),(x+.81,y-1.4,.95),(x+.81,y+1.15,.95),(x-.81,y+1.15,.95),
    (x-.65,y-.75,1.5),(x+.65,y-.75,1.5),(x+.65,y+.65,1.5),(x-.65,y+.65,1.5)]
 for ids in ((0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)): face(site,'Glass',[v[i] for i in ids])
 face(site,color,[v[i] for i in (4,5,6,7)])
 for dx in (-.91,.91):
  for dy in (-1.25,1.25):
   # Twelve-sided tires aligned to the axle, with inset metal hubs.
   for mat,r,thick in (('Rubber',.31,.18),('Steel',.15,.19)):
    rings=[[(x+dx+side*thick/2,y+dy+r*math.cos(k*math.tau/12),.51+r*math.sin(k*math.tau/12)) for k in range(12)] for side in (-1,1)]
    face(site,mat,list(reversed(rings[0]))); face(site,mat,rings[1])
    for k in range(12): face(site,mat,[rings[0][k],rings[0][(k+1)%12],rings[1][(k+1)%12],rings[1][k]])
 for dx in (-.57,.57):
  box(site,'White',x+dx,y-2.08,.65,.4,.035,.16)
  box(site,'Red',x+dx,y+2.08,.65,.4,.035,.16)

def parking_row(site,x0,y,count,occupied=()):
 for i in range(count+1): box(site,'White',x0+i*2.8,y,.165,.10,5.1,.012)
 box(site,'White',x0+count*1.4,y+2.55,.165,count*2.8,.1,.012)
 for i in range(count):
  box(site,'Concrete',x0+(i+.5)*2.8,y+1.95,.18,1.55,.18,.14)
  if i in occupied: car(site,x0+(i+.5)*2.8,y,('White','Blue','Red','Steel')[i%4])

def building(site,x,y,w,d,kind='Workshop'):
 SOLIDS.append((site,kind,x-w/2,y-d/2,x+w/2,y+d/2))
 box(site,'Concrete',x,y,.12,w+.3,d+.3,.22)
 box(site,'Wall',x,y,.34,w,d,3.5)
 box(site,'Roof',x,y,3.84,w+.6,d+.6,.22)
 box(site,'Roof',x,y,.34,w+.04,d+.04,.48)
 # Ribbed metal roof and narrow clerestory glazing.
 for dx in range(int(w/.55)):
  box(site,'Steel',x-w/2+.25+dx*.55,y,4.06,.035,d+.4,.035)
 for dy in (-d/2-.025,d/2+.025):
  box(site,'Glass',x,y+dy,2.65,w-1.2,.055,.65)
 for dx in (-w/2-.02,w/2+.02):
  for yy in (-d*.30,d*.05,d*.32): box(site,'Glass',x+dx,y+yy,1.65,.055,1.4,.95)
 # Service opening on south end, with frame, shutter ribs and protective posts.
 box(site,'Steel',x,y-d/2-.045,.35,w*.68,.06,2.15)
 for z in range(10): box(site,'Concrete',x,y-d/2-.083,.45+z*.19,w*.66,.025,.027)
 for dx in (-w*.43,w*.43): cyl(site,'Yellow',x+dx,y-d/2-.65,.15,.11,.8)
 box(site,'Blue',x,y-d/2-.09,2.85,w*.65,.05,.48)
 for dx in (-.9,.9):
  box(site,'Steel',x+dx,y+.8,4.07,1.1,1.5,.45)
  for k in range(4): box(site,'Concrete',x+dx,y+.3+k*.24,4.53,.85,.045,.025)

def fence(site,x,y,length,axis='y'):
 n=int(length/2)
 for i in range(n+1):
  xx=x if axis=='y' else x-length/2+i*length/n
  yy=y-length/2+i*length/n if axis=='y' else y
  box(site,'Steel',xx,yy,.13,.075,.075,1.55)
 for z in (.45,1.55): box(site,'Steel',x,y,z,.055 if axis=='y' else length,length if axis=='y' else .055,.055)
 # Sparse vertical palisade, cheap opaque geometry.
 for i in range(int(length/.4)):
  t=-length/2+(i+.5)*.4
  box(site,'Steel',x+(t if axis=='x' else 0),y+(t if axis=='y' else 0),.35,.035,.035,1.12)

def build():
 DATA.clear(); SOLIDS.clear()
 s='ControlCenter'
 # Existing building occupies X133.4..142.6,Y462..478. Leave it intact.
 box(s,'Asphalt',138,447,.113,23,25,.035)
 box(s,'Concrete',145,470,.12,3,20,.15)
 box(s,'Concrete',138,460.7,.12,14,1.5,.15)
 box(s,'Concrete',138,479.3,.12,14,1.5,.15)
 for y in (463,465,467,469,471,473,475,477): box(s,'Pavers',145,y,.273,2.7,.035,.006)
 parking_row(s,127.6,454.1,7,(0,1,3,5))
 parking_row(s,130.4,439.1,6,(1,4))
 # Six metres clear between parking rows, with planted end islands.
 planter(s,148.6,448,1.5,25)
 planter(s,128.2,470,2.3,18)
 planter(s,138.1,487,20,3,False)
 for x in (130,135,140,145): tree(s,x,487,int(x))
 # Entrance canopy clear of existing facade and roof solar equipment.
 box(s,'Roof',144.4,470,2.9,3.3,5.3,.12)
 for yy in (467.6,472.4): box(s,'Steel',145.8,yy,.27,.09,.09,2.63)
 for yy in (464.5,475.5):
  box(s,'Bark',145,yy,.63,.55,1.6,.09)
  for sy in (-.55,.55): box(s,'Steel',145,yy+sy,.27,.4,.08,.36)
 # Low security kiosk at entrance, no barrier over public collector.
 building(s,128.6,443,2.8,3.2,'Reception')
 fence(s,149.25,470,30)
 for x in (131,134,137,140,143): box(s,'White',x,460,.16,1.4,.35,.015)
 s='Bay40Services'
 # Grid-aligned parcels preserve all seven inter-yard roads and truck return lanes.
 for index,cx in enumerate((-65,-44,-23,-2,19,40,61,82)):
  x=cx+3.8
  box(s,'Asphalt',x,472,.113,6.4,35,.035)
  if index in (0,1,4):
   building(s,x,478,5.7,16,'Maintenance' if index<2 else 'Stores')
   box(s,'Yellow',x,465,.165,5.7,.12,.012)
   for dx in (-2.6,2.6): box(s,'Yellow',x+dx,462,.165,.1,6,.012)
  elif index==5:
   building(s,x,481,5.7,10,'Electrical')
   for yy in (466,471):
    box(s,'Concrete',x,yy,.13,4.3,3.2,.25)
    box(s,'Steel',x,yy,.38,3.6,2.5,1.7)
    for z in range(8): box(s,'Concrete',x-1.82,yy,.6+z*.16,.04,2,.045)
   fence(s,x+3,474,29)
  elif index in (2,3):
   # Vehicles lie inside the parcel; side access opens onto existing road.
   for j,y in enumerate((460,467,474,481)):
    for yy in (y-2.6,y+2.6): box(s,'White',x,yy,.165,6,.10,.012)
    if (index+j)%3: car(s,x,y,('Blue','White','Steel')[j%3])
   # Open solar/shade canopy with slim columns.
   box(s,'Roof',x,471,3.0,6,28,.12)
   for yy in (458,470,484):
    box(s,'Steel',x+2.75,yy,.15,.12,.12,2.85)
   for yy in range(459,484,3): box(s,'Glass',x,yy,3.13,5.3,2.6,.025)
  else:
   planter(s,x,477,5.7,19)
   for yy in (459,463):
    box(s,'Bark',x,yy,.65,2.0,.55,.1)
    for dx in (-.7,.7): box(s,'Steel',x+dx,yy,.15,.1,.4,.5)
  planter(s,x,489,5.8,2,False)
  tree(s,x,489,index)
  # Small white/blue wayfinding monolith at each parcel entrance.
  box(s,'Steel',x+2.5,455.2,.15,.09,.1,1.4)
  box(s,'Blue',x+2.5,455.2,1.25,.7,.10,.4)
  for k in range(index%3+1): box(s,'White',x+2.3+k*.13,455.14,1.36,.07,.015,.16)
 stage=Usd.Stage.CreateInMemory(); UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z); UsdGeom.SetStageMetersPerUnit(stage,1)
 stage.OverridePrim('/World'); root=UsdGeom.Xform.Define(stage,BASE).GetPrim()
 root.CreateAttribute('designNotes',Sdf.ValueTypeNames.String).Set('Remote control forecourt and 4C bay 40 service parcels; clear Y446.3-454 apron, X110.5-125.5 collector, Y492.6-500 crossroad and all original inter-yard lanes. No new dynamic lights or physics.')
 mats={}
 for name,(color,rough,metal) in COLORS.items():
  m=UsdShade.Material.Define(stage,BASE+'/Looks/'+name); sh=UsdShade.Shader.Define(stage,str(m.GetPath())+'/Surface'); sh.CreateIdAttr('UsdPreviewSurface')
  sh.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(color); sh.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough); sh.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
  m.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(),'surface'); mats[name]=m
 for (site,mat),(p,c,i) in DATA.items():
  mesh=UsdGeom.Mesh.Define(stage,BASE+'/'+site+'/'+mat)
  mesh.CreatePointsAttr(p); mesh.CreateFaceVertexCountsAttr(c); mesh.CreateFaceVertexIndicesAttr(i); mesh.CreateSubdivisionSchemeAttr('none')
  mesh.CreateExtentAttr([Gf.Vec3f(*(min(v[j] for v in p) for j in range(3))),Gf.Vec3f(*(max(v[j] for v in p) for j in range(3)))])
  UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mats[mat])
 stage.GetRootLayer().Export(str(LAYER))
 report={'mesh_count':len(DATA),'triangles':sum(sum(n-2 for n in v[1]) for v in DATA.values()),'buildings':SOLIDS,'new_lights':0,'new_textures':0}
 out=ROOT/'outputs/port_service_areas'; out.mkdir(parents=True,exist_ok=True); (out/'build.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report,indent=2))

if __name__=='__main__': build()
