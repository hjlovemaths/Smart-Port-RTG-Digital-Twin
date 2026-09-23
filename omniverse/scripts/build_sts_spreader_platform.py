"""Add a photo-inspired upper headblock to the existing telescopic STS asset.

Run with the project's USD Python runner. Coordinates retain the asset's
existing clearance scale; lower telescoping controllers remain intact.
"""
from pathlib import Path
from pxr import Gf, Usd, UsdGeom, UsdShade
from sts_control import rope_endpoints, cylinder_matrix

ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'omniverse/assets/rtg/STS_Crane_Controlled.usdc'
stage = Usd.Stage.Open(str(ASSET))
base = '/STSCrane/Gantry/Trolley/Hoist'
platform = base + '/STS_TelescopicSpreader/Platform'
materials = {key: UsdShade.Material.Get(stage, '/STSCrane/_materials/' + name)
             for key, name in {
                 'yellow': 'CODEx_crane1_spreader_safety_yellow',
                 'black': 'CODEx_crane1_warning_black',
                 'steel': 'CODEx_crane1_oiled_steel',
             }.items()}

def part(path, center, size, material='yellow', rotation=None):
    shape = UsdGeom.Cube.Define(stage, path)
    shape.CreateSizeAttr(1)
    xf = UsdGeom.Xformable(shape)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(*center))
    if rotation is not None:
        xf.AddRotateXYZOp().Set(Gf.Vec3f(*rotation))
    xf.AddScaleOp().Set(Gf.Vec3f(*size))
    UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(materials[material])

def wheel(path, center, radius, depth):
    shape = UsdGeom.Cylinder.Define(stage, path)
    shape.CreateAxisAttr('Y')
    shape.CreateRadiusAttr(radius)
    shape.CreateHeightAttr(depth)
    xf = UsdGeom.Xformable(shape)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(*center))
    # Cancel only the inherited X clearance stretch for circular pulleys.
    xf.AddScaleOp().Set(Gf.Vec3f(1/2.2, 1, 1/1.2))
    UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(materials['steel'])

# Lower yellow telescopic spreader, upper fixed machinery platform.
part(platform+'/UpperDeck', (0,-0.70,3.65), (.72,.66,.09))
for side,y in enumerate((-.99,-.41)):
    part(platform+f'/DeckGirder{side}', (0,y,3.63), (.76,.07,.19))
    part(platform+f'/Handrail{side}', (0,y,4.02), (.69,.018,.025))
    for j,x in enumerate((-.32,0,.32)):
        part(platform+f'/RailPost{side}_{j}', (x,y,3.85), (.012,.018,.34))
for j,x in enumerate((-.26,.26)):
    part(platform+f'/Support{j}', (x,-.70,3.39), (.09,.46,.44))
    part(platform+f'/Machinery{j}', (x,-.70,3.89), (.16,.38,.33))
    part(platform+f'/MachineryCover{j}', (x,-.70,4.08), (.22,.45,.06))
    for k,y in enumerate((-.92,-.48)):
        part(platform+f'/SuspensionLink{j}_{k}', (x,y,4.12), (.045,.065,.30), 'steel')
        part(platform+f'/RopeSocket{j}_{k}', (x,y,4.24), (.035,.06,.12), 'steel')
        wheel(platform+f'/Pulley{j}_{k}', (x,y,4.06), .10,.07)
        wheel(platform+f'/LowerPin{j}_{k}', (x,y,3.83), .065,.075)

# Walkway grating, access ladder, cabinets and hydraulic lines.
for j in range(15):
    part(platform+f'/Grating{j}', (-.33+j*.047,-.70,3.71), (.013,.52,.016), 'steel')
for j,x in enumerate((-.06,.06)):
    part(platform+f'/LadderRail{j}', (x,-1.06,3.53), (.012,.025,1.00), 'steel')
for j in range(8):
    part(platform+f'/LadderRung{j}', (0,-1.06,3.10+j*.12), (.12,.025,.022), 'steel')
part(platform+'/HydraulicTank', (0,-.70,3.29), (.14,.30,.23), 'steel')
for j,y in enumerate((-.81,-.59)):
    part(platform+f'/HydraulicPipe{j}', (0,y,3.45), (.46,.028,.028), 'black')
for side in ('Front','Rear'):
    end = base + '/STS_SpreaderEnd' + side
    old = stage.GetPrimAtPath(end+'/RopeSeat')
    if old:
        old.SetActive(False)
    part(end+'/EndCrossBeam', (0,-.70,2.98), (.19,1.37,.24))
    part(end+'/ElectricalCabinet', (0,-.70,3.19), (.13,.23,.18), 'steel')
    for j,y in enumerate((-1.435,.035)):
        part(end+f'/Flipper{j}', (0,y,2.76), (.055,.07,.24), rotation=(0,0,0))
    for j in range(7):
        part(end+f'/WarningStripe{j}', (0,-1.30+j*.20,2.99), (.193,.065,.20), 'black')

# Match the actual yard-container corner-casting centers across its width.
half_lock_width = 1.216287 / 2
lower_widths = {'MainSleeve':1.30, 'TopCap':1.33, 'MainBeam':1.30,
                'InnerTelescopicBeam':1.20}
for branch in ('STS_TelescopicSpreader/Center','STS_SpreaderEndFront','STS_SpreaderEndRear'):
    parent = stage.GetPrimAtPath(base+'/'+branch)
    for child in parent.GetChildren():
        name = child.GetName()
        ops = {op.GetOpName():op for op in UsdGeom.Xformable(child).GetOrderedXformOps()}
        if name in lower_widths:
            op = ops['xformOp:scale']
            v = op.Get()
            op.Set(Gf.Vec3f(v[0],lower_widths[name],v[2]))
        if name.startswith(('CornerLock','SideRail','SideSleeve','InnerTelescopicRail')):
            op = ops['xformOp:translate']
            v = op.Get()
            y = -.70 + (-half_lock_width if name.endswith('Front') else half_lock_width)
            op.Set(Gf.Vec3d(v[0],y,v[2]))
stage.GetRootLayer().Save()

# Update the authored resting ropes too, so the model connects before opening UI.
scene = Usd.Stage.Open(str(ROOT/'omniverse/scenes/sts_controls.usda'))
for crane in scene.GetPrimAtPath('/World/STSControls').GetChildren():
    path = str(crane.GetPath())+'/Gantry/DynamicCables'
    endpoints = rope_endpoints(1.62,2.78)
    endpoints += [(Gf.Vec3d(0,4.06,13.94),Gf.Vec3d(x,y,9.86))
                  for y in (-16.38,12.38) for x in (-2.05,2.05)]
    for i,(start,end) in enumerate(endpoints):
        cable = UsdGeom.Cylinder.Define(scene,path+f'/Cable_{i}')
        cable.CreateAxisAttr('Z')
        cable.CreateRadiusAttr(1)
        cable.CreateHeightAttr(1)
        cable.CreateDisplayColorAttr([Gf.Vec3f(.025)])
        xf = UsdGeom.Xformable(cable)
        xf.ClearXformOpOrder()
        xf.AddTransformOp().Set(cylinder_matrix(start,end,.012 if i<4 else .017))
scene.GetRootLayer().Save()
print('Reference-style STS platform and static suspension updated.')
