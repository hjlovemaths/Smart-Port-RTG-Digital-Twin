"""Fill the twenty marked waiting bays with the existing yard truck asset."""
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'omniverse/scenes/parked_yard_trucks.usda'
ASSET = '../assets/resource/truck/RTG_Model_Lane_Truck.usdc'


def add_roof_number(stage, truck, number):
    """Black geometric digits on the white cab roof, readable from overhead."""
    label = UsdGeom.Xform.Define(stage, f'/World/ParkedTruckNumbers/Truck{number:02d}')
    label.AddTranslateOp().Set(truck.GetPrim().GetAttribute('xformOp:translate').Get())
    label.AddRotateZOp().Set(-90)
    label.AddScaleOp().Set(Gf.Vec3f(1, 1.22, 1))
    label.GetPrim().CreateAttribute('vehicle:number', Sdf.ValueTypeNames.String).Set(f'{number:02d}')
    segments = {
        'a': (0, 0, .24, .045), 'g': (0, .22, .24, .265),
        'd': (0, .44, .24, .485), 'f': (0, 0, .045, .265),
        'b': (.195, 0, .24, .265), 'e': (0, .22, .045, .485),
        'c': (.195, .22, .24, .485),
    }
    patterns = ('abcdef', 'bc', 'abged', 'abgcd', 'fgbc', 'afgcd',
                'afgecd', 'abc', 'abcdefg', 'abfgcd')
    points = []
    for index, digit in enumerate(f'{number:02d}'):
        for segment in patterns[int(digit)]:
            x1,y1,x2,y2 = segments[segment]
            xoff, yoff = -.285 + index*.33, -2.90
            # Glyph coordinates run downward; USD roof coordinates run upward.
            # Reflect glyph Y so right/up form a right-handed top-facing basis.
            points.extend((Gf.Vec3f(x1+xoff,yoff+.485-y2,1.066),
                           Gf.Vec3f(x2+xoff,yoff+.485-y2,1.066),
                           Gf.Vec3f(x2+xoff,yoff+.485-y1,1.066),
                           Gf.Vec3f(x1+xoff,yoff+.485-y1,1.066)))
    mesh = UsdGeom.Mesh.Define(stage, str(label.GetPath())+'/Digits')
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr([4]*(len(points)//4))
    mesh.CreateFaceVertexIndicesAttr(list(range(len(points))))
    mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreateDisplayColorAttr([Gf.Vec3f(.012,.018,.025)])


def build():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    world = UsdGeom.Xform.Define(stage, '/World')
    stage.SetDefaultPrim(world.GetPrim())
    root = UsdGeom.Xform.Define(stage, '/World/ParkedYardTrucks').GetPrim()
    root.CreateAttribute('parking:vehicleCount', Sdf.ValueTypeNames.Int).Set(20)
    root.CreateAttribute('parking:role', Sdf.ValueTypeNames.String).Set(
        'Empty trucks matching the live yard vehicle; cab faces collector road')
    asset_stage = Usd.Stage.Open(str(ROOT / 'omniverse/assets/resource/truck/RTG_Model_Lane_Truck.usdc'))
    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default','render']).ComputeWorldBound(
        asset_stage.GetDefaultPrim()).ComputeAlignedRange()
    center_y = bounds.GetMidpoint()[1]
    for group,start_y in enumerate((65.0,160.0),1):
        for bay in range(10):
            truck = UsdGeom.Xform.Define(stage, f'/World/ParkedYardTrucks/Group{group}_Bay{bay+1:02d}')
            truck.GetPrim().GetReferences().AddReference(
                str(ROOT / 'omniverse/assets/resource/truck/RTG_Model_Lane_Truck.usdc'), '/root')
            # Referenced asset includes a studio light; parking adds geometry only.
            stage.OverridePrim(str(truck.GetPath())+'/env_light').SetActive(False)
            truck.AddTranslateOp().Set(Gf.Vec3d(138-center_y*1.22, start_y+bay*5.4+2.2,
                                               0.085-bounds.GetMin()[2]))
            truck.AddRotateZOp().Set(-90)
            truck.AddScaleOp().Set(Gf.Vec3f(1,1.22,1))
            truck.GetPrim().SetInstanceable(True)
            add_roof_number(stage, truck, (group-1)*10+bay+1)
    # Bounds must stay inside the painted rectangles without crossing neighbours.
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default','render'])
    for truck in root.GetChildren():
        box = cache.ComputeWorldBound(truck).ComputeAlignedRange()
        assert not box.IsEmpty(), truck.GetPath()
        pos = truck.GetAttribute('xformOp:translate').Get()
        assert box.GetMin()[0] > 129.22 and box.GetMax()[0] < 146.78, box
        assert box.GetMin()[1] > pos[1]-1.98 and box.GetMax()[1] < pos[1]+1.98, box
    # Store portable paths only after validating the resolved geometry.
    with Sdf.ChangeBlock():
        for truck in root.GetChildren():
            spec = stage.GetRootLayer().GetPrimAtPath(truck.GetPath())
            spec.referenceList.prependedItems = [Sdf.Reference(ASSET, '/root')]
    stage.GetRootLayer().Export(str(OUTPUT))
    print(f'Wrote {OUTPUT}; 20 matching empty trucks; all bounds inside bays')


if __name__ == '__main__':
    build()
