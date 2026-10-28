"""Low-cost waterfront apron amenities; preserve truck bays and hatched reserve."""
from pathlib import Path
import sys, math, json
from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_port_service_areas as g
ROOT=Path(__file__).resolve().parents[2]
BASE='/World/WaterfrontAmenities'
LAYER=ROOT/'omniverse/scenes/waterfront_amenities.usda'

def bench(s,x,y):
 g.box(s,'Bark',x,y,.60,.6,2,.09)
 g.box(s,'Bark',x+.26,y,.69,.08,2,.45)
 for dy in (-.7,.7): g.box(s,'Steel',x,y+dy,.15,.45,.1,.45)

def shelter(s,x,y,w=5,d=10):
 g.box(s,'Concrete',x,y,.11,w+.6,d+.6,.16)
 g.box(s,'Roof',x,y,2.85,w,d,.14)
 for dx in (-w/2+.2,w/2-.2):
  for dy in (-d/2+.3,d/2-.3): g.box(s,'Steel',x+dx,y+dy,.27,.09,.09,2.58)
 for yy in (y-2,y+2): bench(s,x+1.5,yy)
 # Back wind screen and open front towards road.
 g.box(s,'Glass',x+w/2-.15,y,.8,.055,d-1,1.6)

def rail(s,y0,y1):
 x=149.5
 for z in (.5,1.12): g.box(s,'Steel',x,(y0+y1)/2,z,.055,y1-y0,.055)
 for k in range(int((y1-y0)/2.5)+1): g.cyl(s,'Steel',x,y0+.06+k*2.5,.12,.045,1.06)
 # Paved perimeter path is outside the existing 18 m truck bay rectangles.
 g.box(s,'Concrete',148.9,(y0+y1)/2,.09,1.6,y1-y0,.035)
 for y in range(math.ceil(y0)+1,math.floor(y1),5): g.box(s,'Pavers',148.9,y,.128,1.6,.035,.004)

def rescue(s,y):
 g.box(s,'Steel',149.15,y,.13,.08,.09,1.6)
 # Vertical orange life ring, axis along X.
 n=16
 for k in range(n):
  a,b=k*math.tau/n,(k+1)*math.tau/n
  for side in (-1,1):
   x=149.08+side*.05
   g.face(s,'Red' if k%4<2 else 'White',[(x,y+r*math.cos(t),1.35+r*math.sin(t)) for r,t in ((.32,a),(.32,b),(.20,b),(.20,a))])
  g.face(s,'Red',[(x,y+.32*math.cos(t),1.35+.32*math.sin(t)) for x,t in ((149.03,a),(149.13,a),(149.13,b),(149.03,b))])
 g.box(s,'Red',148.9,y+1.2,.4,.45,.65,.85)
 g.box(s,'White',148.65,y+1.2,.8,.015,.34,.08)

def build():
 g.DATA.clear();g.SOLIDS.clear()
 # Continuous detail at the seawall, with crossroad and external-gate breaks.
 for a,b in ((10,118),(134,248),(264,378),(395,426)):
  rail('ShoreSafety',a,b)
 for y in (38,95,145,190,235,277,319,363,414): rescue('ShoreSafety',y)
 s='DriverServices'
 g.box(s,'Asphalt',137,35,.10,21,39,.045)
 g.building(s,139,26,9,11,'DriverRest')
 # Flat-roof extension veranda and shaded tables.
 shelter(s,138,42,8,9)
 for y in (39.5,44.5):
  g.box(s,'Bark',136.7,y,.85,1.5,1.4,.07)
  g.box(s,'Steel',136.7,y,.15,.12,.12,.7)
 for y in (18,51): g.planter(s,137,y,19,2,False)
 for x in (130,136,143):
  g.tree(s,x,18,int(x)); g.tree(s,x,51,int(x+1))
 for y in (34,37,40,43,46): g.box(s,'White',128,y,.15,1.8,.35,.012)
 s='WaitingShelters'
 for cy in (144,232):
  shelter(s,141,cy,6,12)
  g.planter(s,130,cy,3,14)
  g.box(s,'Concrete',136,cy,.11,4,14,.08)
  # Paired waste/recycling bins and information board.
  for y in (cy-2,cy-1):
   g.box(s,'Steel',137,y,.19,.6,.65,.85)
   g.box(s,'Roof',137,y,1.04,.65,.7,.07)
  g.box(s,'Steel',134,cy+4,.12,.08,.08,1.8)
  g.box(s,'Blue',134,cy+4,1.35,.12,1.8,.7)
  for z in (1.5,1.7,1.9): g.box(s,'White',133.93,cy+4,z,.015,1.4,.04)
 s='UtilityStation'
 g.building(s,141,274,7,10,'Utility')
 g.planter(s,129.5,274,3,12)
 for y in (270.5,274,277.5):
  g.box(s,'Concrete',134.5,y,.12,2.2,2.4,.22)
  g.box(s,'Steel',134.5,y,.34,1.6,1.8,1.8)
  for z in range(7): g.box(s,'Concrete',133.68,y,.65+z*.18,.04,1.4,.035)
 s='PatrolParking'
 for cy,count in ((365,6),(413,5)):
  g.box(s,'Asphalt',137,cy,.10,21,22,.045)
  g.parking_row(s,128,cy+5,count,(1,3))
  g.planter(s,145.7,cy,2,20)
  # Small charger cabinets at the far end of parked vehicles, with simple cable loops.
  for i in (1,3):
   x=128+(i+.5)*2.8;y=cy+8.4
   g.box(s,'White',x,y,.15,.55,.45,1.25)
   g.box(s,'Glass',x,y-.23,.95,.34,.02,.27)
   g.box(s,'Roof',x,y,1.4,.6,.5,.08)
   g.cyl(s,'Rubber',x+.34,y,.3,.035,.9)
  shelter(s,132,cy-6,5,6)
 # Fewer meshes than individual objects: batch every site/material combination.
 stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
 stage.OverridePrim('/World'); root=UsdGeom.Xform.Define(stage,BASE).GetPrim()
 root.CreateAttribute('designNotes',Sdf.ValueTypeNames.String).Set('Landside waterfront apron. Keep X<=125.5 collector, Y65..118 and160..213 truck bays, Y290..346 hatched reserve and crossroad/gate gaps clear. Static, opaque meshes; no new lights or physics.')
 mats={}
 for name,(color,rough,metal) in g.COLORS.items():
  m=UsdShade.Material.Define(stage,BASE+'/Looks/'+name);sh=UsdShade.Shader.Define(stage,str(m.GetPath())+'/Surface');sh.CreateIdAttr('UsdPreviewSurface')
  sh.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(color);sh.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough);sh.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
  m.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(),'surface');mats[name]=m
 for (site,mat),(p,c,i) in g.DATA.items():
  mesh=UsdGeom.Mesh.Define(stage,BASE+'/'+site+'/'+mat);mesh.CreatePointsAttr(p);mesh.CreateFaceVertexCountsAttr(c);mesh.CreateFaceVertexIndicesAttr(i);mesh.CreateSubdivisionSchemeAttr('none')
  mesh.CreateExtentAttr([Gf.Vec3f(*(min(v[j] for v in p) for j in range(3))),Gf.Vec3f(*(max(v[j] for v in p) for j in range(3)))])
  UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mats[mat])
 stage.GetRootLayer().Export(str(LAYER))
 report={'meshes':len(g.DATA),'triangles':sum(sum(n-2 for n in a[1]) for a in g.DATA.values()),'new_lights':0}
 out=ROOT/'outputs/waterfront_amenities';out.mkdir(parents=True,exist_ok=True);(out/'build.json').write_text(json.dumps(report,indent=2));print(report)
if __name__=='__main__':build()


