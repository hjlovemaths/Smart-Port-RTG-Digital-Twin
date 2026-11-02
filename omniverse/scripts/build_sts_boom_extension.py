"""Extend STS01-03 over the large vessel, preserving axis paths and cross sections."""
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom
from build_sts_dimensions import bounds
from build_sts_elevation import apply_world_delta, translate
from sts_control import PIVOT, stay_endpoints, cylinder_matrix
ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'omniverse/scenes/sts_boom_extension.usda'
FACTOR = 2.5
CRANES = tuple(f'STS_{n:02d}' for n in range(1,8))

def build():
    stage = Usd.Stage.Open(str(ROOT / 'omniverse/scenes/smart_port.usda'))
    if OUTPUT.exists(): stage.MuteLayer(str(OUTPUT))
    cache = UsdGeom.XformCache()
    plans = []
    for name in CRANES:
        crane = stage.GetPrimAtPath('/World/STSControls/' + name)
        boom = stage.GetPrimAtPath(str(crane.GetPath()) + '/Gantry/Boom')
        offset = Gf.Vec3d(crane.GetAttribute('sts:upperOffset').Get())
        anchor = cache.GetLocalToWorldTransform(boom.GetParent()).Transform(PIVOT + offset)[0]
        for child in boom.GetChildren():
            if not UsdGeom.Xformable(child): continue
            if child.GetName() == "NightWorkLights":
                for lamp in child.GetChildren():
                    x = cache.GetLocalToWorldTransform(lamp).ExtractTranslation()[0]
                    plans.append((lamp, translate(((x-anchor)*(FACTOR-1),0,0))))
                continue
            b = bounds(stage, child.GetPath())
            if b.IsEmpty(): continue
            # Longitudinal girders, rails, handrails and walkways grow in length.
            # End caps, bolts, stanchions and transverse members move rigidly.
            if b.GetSize()[0] > 8:
                matrix = Gf.Matrix4d(1).SetScale(Gf.Vec3d(FACTOR,1,1))
                matrix.SetTranslateOnly(Gf.Vec3d(anchor*(1-FACTOR),0,0))
            else:
                matrix = translate(((b.GetMidpoint()[0]-anchor)*(FACTOR-1),0,0))
            plans.append((child,matrix))
    layer = Sdf.Layer.CreateAnonymous('sts_boom_extension.usda')
    stage.GetSessionLayer().subLayerPaths.insert(0,layer.identifier)
    with Usd.EditContext(stage,layer):
        for child,matrix in plans:
            apply_world_delta(child,matrix,cache,'stsBoomLength')
        # Retain safe visual railing spacing after lengthening the walkways.
        post_cache = UsdGeom.XformCache()
        for name in CRANES:
            boom_path = '/World/STSControls/' + name + '/Gantry/Boom'
            for side in ('left','right'):
                for index in range(5):
                    source = boom_path + f'/CODEx_STS_Crane0_{side}_boom_rail_post_{index}_data'
                    for subdivision in (1,2):
                        post = stage.DefinePrim(boom_path + f'/ExtensionPost_{side}_{index}_{subdivision}', 'Xform')
                        post.GetReferences().AddInternalReference(source)
                        apply_world_delta(post, translate((2.86*FACTOR*subdivision/3,0,0)),post_cache,'stsExtraPost')
        for name in CRANES:
            path='/World/STSControls/'+name
            crane=stage.GetPrimAtPath(path)
            crane.CreateAttribute('sts:boomLengthScale',Sdf.ValueTypeNames.Double).Set(FACTOR)
            ends=stay_endpoints(0,crane.GetAttribute('sts:fixedWidthScale').Get(),
                crane.GetAttribute('sts:fixedWidthAnchor').Get(),crane.GetAttribute('sts:upperOffset').Get(),
                crane.GetAttribute('sts:towerOffset').Get(),FACTOR)
            for i,(a,b) in enumerate(ends,4):
                p=stage.GetPrimAtPath(path+f'/Gantry/DynamicCables/Cable_{i}')
                UsdGeom.Xformable(p).GetOrderedXformOps()[0].Set(cylinder_matrix(a,b,.017))
    layer.Export(str(OUTPUT))
    print('Extended',CRANES,'factor',FACTOR)

if __name__=='__main__': build()
