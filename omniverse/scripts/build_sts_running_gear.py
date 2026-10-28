"""Build side sill beams and eight rail wheels per side for all controlled STS cranes.

Reversible visual layer. Run with Kit's USD Python after the gauge/elevation
layers. The assembly is a child of Gantry so it follows existing travel control.
"""
from pathlib import Path
import math
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
from build_sts_dimensions import prepend

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'omniverse/scenes/sts_running_gear.usda'
WHEEL_RADIUS = .48
WHEEL_PITCH = 1.10
WHEELS_PER_SIDE = 8
LEG_ROW_OUTWARD_M = 2.0


def build():
    source = Usd.Stage.Open(str(ROOT / 'omniverse/scenes/smart_port.usda'), Usd.Stage.LoadNone)
    if OUTPUT.exists():
        source.MuteLayer(str(OUTPUT))
    bbox = UsdGeom.BBoxCache(0, ['default', 'render'])
    xforms = UsdGeom.XformCache(0)
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    stage.GetRootLayer().documentation = 'STS lower side beams, widened column shoes, 8 rail wheels per side. Visual geometry follows Gantry.'
    looks = '/World/STSRunningGearLooks'

    def material(name, color, metallic, roughness):
        m = UsdShade.Material.Define(stage, looks + '/' + name)
        shader = UsdShade.Shader.Define(stage, str(m.GetPath()) + '/Surface')
        shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(metallic)
        shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(roughness)
        m.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
        return m

    blue = material('BlueSteel', (.035, .30, .40), .35, .4)
    dark = material('RailWheelSteel', (.035, .045, .05), .8, .34)
    rim = material('MachinedSteel', (.28, .32, .34), .85, .28)

    def bind(prim, mat):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)

    def override_world_transform(part, delta, suffix):
        target = stage.OverridePrim(part.GetPath())
        for op in UsdGeom.Xformable(part).GetOrderedXformOps():
            target.CreateAttribute(op.GetOpName(),op.GetAttr().GetTypeName()).Set(op.Get())
        UsdGeom.Xformable(target).CreateXformOpOrderAttr().Set(part.GetAttribute('xformOpOrder').Get())
        parent = xforms.GetLocalToWorldTransform(part.GetParent())
        prepend(target,parent*delta*parent.GetInverse(),suffix)

    def box(path, center, size, mat):
        shape = UsdGeom.Cube.Define(stage, path)
        shape.CreateSizeAttr(1)
        shape.AddTranslateOp().Set(Gf.Vec3d(*center))
        shape.AddScaleOp().Set(Gf.Vec3f(*size))
        bind(shape.GetPrim(), mat)

    def cylinder(path, center, radius, width, mat):
        # Explicit smooth mesh avoids the viewport's coarse primitive tessellation.
        n = 48
        ring = [(math.cos(i*math.tau/n),math.sin(i*math.tau/n)) for i in range(n)]
        pts = [(center[0]+sign*width/2, center[1]+radius*c, center[2]+radius*s)
               for sign in (-1,1) for c,s in ring]
        faces = [[i,(i+1)%n,(i+1)%n+n,i+n] for i in range(n)]
        faces += [list(reversed(range(n))),list(range(n,2*n))]
        normals = [(0,*ring[k%n]) for face in faces[:-2] for k in face]
        normals += [(-1,0,0)]*n+[(1,0,0)]*n
        shape = UsdGeom.Mesh.Define(stage,path)
        shape.CreatePointsAttr(pts)
        shape.CreateFaceVertexCountsAttr([len(f) for f in faces])
        shape.CreateFaceVertexIndicesAttr([k for f in faces for k in f])
        shape.CreateNormalsAttr(normals)
        shape.SetNormalsInterpolation('faceVarying')
        shape.CreateSubdivisionSchemeAttr('none')
        bind(shape.GetPrim(),mat)

    def shoe(path, x, y, bottom, top):
        # Flared lower column: wide support at the beam, original leg width above.
        pts = [(x+sx*.69, y+sy*1.13, bottom) for sx, sy in [(-1,-1),(1,-1),(1,1),(-1,1)]]
        pts += [(x+sx*.61, y+sy*.45, top) for sx, sy in [(-1,-1),(1,-1),(1,1),(-1,1)]]
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr(pts)
        mesh.CreateFaceVertexCountsAttr([4]*6)
        mesh.CreateFaceVertexIndicesAttr([0,3,2,1,4,5,6,7,0,1,5,4,1,2,6,5,2,3,7,6,3,0,4,7])
        mesh.CreateSubdivisionSchemeAttr('none')
        bind(mesh.GetPrim(), blue)

    count = 0
    for crane in source.GetPrimAtPath('/World/STSControls').GetChildren():
        gantry_path = str(crane.GetPath()) + '/Gantry'
        fixed = source.GetPrimAtPath(gantry_path + '/Fixed')
        legs = [p for p in fixed.GetChildren() if '_orange_leg_' in p.GetName()]
        if len(legs) != 4:
            raise ValueError(f'Expected four legs at {crane.GetPath()}, got {len(legs)}')
        centers = sorted([bbox.ComputeWorldBound(p).ComputeAlignedRange().GetMidpoint() for p in legs], key=lambda v:(v[0],v[1]))
        wheel = next(p for p in fixed.GetChildren() if p.GetName().startswith('SMART_PORT_quay_sts_crane_0_wheel_'))
        wheel_z = bbox.ComputeWorldBound(wheel).ComputeAlignedRange().GetMidpoint()[2]
        row_center = sum(p[1] for p in centers)/4
        leg_top = max(bbox.ComputeWorldBound(p).ComputeAlignedRange().GetMax()[2] for p in legs)
        # Widen the actual opening, translating the column rows and their
        # ladders/braces rigidly. Upper machinery and spreader keep their size.
        for part in fixed.GetChildren():
            b = bbox.ComputeWorldBound(part).ComputeAlignedRange()
            if b.GetMin()[2] >= leg_top-.08:
                continue
            dy = LEG_ROW_OUTWARD_M if b.GetMidpoint()[1] > row_center else -LEG_ROW_OUTWARD_M
            delta = Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0,dy,0))
            if part in legs:
                bottom,top = b.GetMin()[2],b.GetMax()[2]
                factor = (top-(wheel_z+.90))/(top-bottom)
                delta = Gf.Matrix4d(1).SetScale(Gf.Vec3d(1,1,factor))
                delta.SetTranslateOnly(Gf.Vec3d(0,dy,top*(1-factor)))
            override_world_transform(part,delta,'stsPortalOpening')
        # Carry the wider portal through the upper deck, its edging and rails.
        # Apply matching edits across the fixed/hinged deck seam. The trolley
        # track gauge and stay anchor locations continue to match their controls.
        boom = source.GetPrimAtPath(gantry_path+'/Boom')
        for part in list(fixed.GetChildren())+list(boom.GetChildren()):
            b = bbox.ComputeWorldBound(part).ComputeAlignedRange()
            if b.GetMin()[2] < leg_top-.08 and part.GetParent() == fixed:
                continue
            name = part.GetName().lower()
            if any(tag in name for tag in ('machinery_house','main_mast','cable_anchor','trolley_rail')):
                continue
            width = b.GetSize()[1]
            middle = b.GetMidpoint()[1]
            if width > 7.0 and b.GetMin()[1] < row_center < b.GetMax()[1]:
                factor = (width+2*LEG_ROW_OUTWARD_M)/width
                delta = Gf.Matrix4d(1).SetScale(Gf.Vec3d(1,factor,1))
                delta.SetTranslateOnly(Gf.Vec3d(0,middle*(1-factor),0))
            elif abs(middle-row_center)>3.8:
                shift = LEG_ROW_OUTWARD_M if middle>row_center else -LEG_ROW_OUTWARD_M
                delta = Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0,shift,0))
            else:
                continue
            override_world_transform(part,delta,'stsUpperDeckWidth')
        centers = [Gf.Vec3d(p[0],p[1]+(LEG_ROW_OUTWARD_M if p[1]>row_center else -LEG_ROW_OUTWARD_M),p[2]) for p in centers]
        for p in fixed.GetChildren():
            if any(tag in p.GetName().lower() for tag in ('wheel_', '_hub_', '_axle_', '_bogie_', 'foot_gusset')):
                UsdGeom.Imageable(stage.OverridePrim(p.GetPath())).CreateVisibilityAttr().Set('invisible')
        root = gantry_path + '/RunningGear'
        group = UsdGeom.Xform.Define(stage, root)
        group.AddTransformOp().Set(xforms.GetLocalToWorldTransform(source.GetPrimAtPath(gantry_path)).GetInverse())
        group.GetPrim().CreateAttribute('sts:wheelsPerSide', Sdf.ValueTypeNames.Int).Set(WHEELS_PER_SIDE)
        group.GetPrim().CreateAttribute('sts:legRowOutwardMeters', Sdf.ValueTypeNames.Double).Set(LEG_ROW_OUTWARD_M)
        group.GetPrim().CreateAttribute('sts:upperDeckAddedWidthMeters', Sdf.ValueTypeNames.Double).Set(2*LEG_ROW_OUTWARD_M)
        for side_index in range(2):
            pair = sorted(centers[side_index*2:side_index*2+2], key=lambda v:v[1])
            x = sum(v[0] for v in pair)/2
            ys = [v[1] for v in pair]
            side = root + f'/Side_{side_index+1}'
            UsdGeom.Xform.Define(stage, side)
            # Continuous sill connects both column feet, above the bogie frames.
            box(side+'/ConnectingBeam', (x, sum(ys)/2, wheel_z+1.25), (1.15, ys[1]-ys[0]+.95, .70), blue)
            box(side+'/UpperPortalHeader', (x,sum(ys)/2,leg_top-.35), (1.25,ys[1]-ys[0]+.95,.90),blue)
            for leg_index, y in enumerate(ys):
                foot = side + f'/Foot_{leg_index+1}'
                UsdGeom.Xform.Define(stage, foot)
                box(foot+'/WideBogieBeam', (x,y,wheel_z+.67), (1.18,4.55,.46), blue)
                shoe(foot+'/FlaredColumnShoe', x,y,wheel_z+.90,wheel_z+2.45)
                for axle_index in range(4):
                    wy = y+(axle_index-1.5)*WHEEL_PITCH
                    base = foot + f'/Wheel_{axle_index+1}'
                    UsdGeom.Xform.Define(stage, base)
                    cylinder(base+'/Tread', (x,wy,wheel_z), WHEEL_RADIUS,.62,dark)
                    for sign, face in [(-1,'Outer'),(1,'Inner')]:
                        cylinder(base+'/'+face+'Flange', (x+sign*.305,wy,wheel_z), WHEEL_RADIUS+.035,.055,dark)
                        cylinder(base+'/'+face+'Rim', (x+sign*.34,wy,wheel_z), .33,.025,rim)
                        cylinder(base+'/'+face+'Hub', (x+sign*.37,wy,wheel_z), .16,.07,blue)
            count += WHEELS_PER_SIDE
    stage.GetRootLayer().Export(str(OUTPUT))
    print(f'Built {OUTPUT.name}: {count//16} cranes, {count} wheels; 8 per rail side.')


if __name__ == '__main__':
    build()
