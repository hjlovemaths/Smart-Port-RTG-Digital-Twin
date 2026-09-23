"""Compose seven independently controllable STS cranes at existing locations.

Run in a Python environment with pxr. Does not rebuild the source quay layer.
"""
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / 'omniverse/scenes/sts_controls.usda'
CONTROL_ROOT = '/World/STSControls'


def is_track(name):
    return any(k in name for k in ('_sleeper_', '_rail_front', '_rail_back'))


def build():
    source = Usd.Stage.Open(str(ROOT / 'omniverse/scenes/smart_port.usda'))
    source.MuteLayer(str(LAYER))
    layer = Sdf.Layer.FindOrOpen(str(LAYER)) if LAYER.exists() else Sdf.Layer.CreateNew(str(LAYER))
    layer.Clear()
    stage = Usd.Stage.Open(layer)
    stage.SetMetadata('metersPerUnit', 1.0)
    stage.SetMetadata('upAxis', 'Z')
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, '/World').GetPrim())
    cache = UsdGeom.BBoxCache(Usd.TimeCode(1), ['default', 'render'])
    placements = []
    parent = '/World/PortAndRTG'
    for family in (0, 1, 3):
        nose = source.GetPrimAtPath(f'{parent}/CODEx_STS_Crane{family}_boom_waterside_blue_nose_cap')
        center = cache.ComputeWorldBound(nose).ComputeAlignedRange().GetMidpoint()
        placements.append((f'Legacy_{family}', float(center[1])))
    for index in range(1, 5):
        p = source.GetPrimAtPath(f'{parent}/SMART_PORT_ADDITIONAL_STS_CRANE_{index:02d}')
        center = UsdGeom.XformCache().GetLocalToWorldTransform(p).ExtractTranslation()
        placements.append((f'Additional_{index:02d}', float(center[1])))
        stage.OverridePrim(p.GetPath()).SetActive(False)
    for p in source.GetPrimAtPath(parent).GetChildren():
        name = p.GetName()
        if name.startswith(('CODEx_STS_Crane0_', 'CODEx_STS_Crane1_', 'CODEx_STS_Crane3_',
                            'SMART_PORT_quay_sts_crane_0_', 'SMART_PORT_quay_sts_crane_1_',
                            'SMART_PORT_quay_sts_crane_3_')) and not is_track(name):
            stage.OverridePrim(p.GetPath()).SetActive(False)
    placements.sort(key=lambda row: row[1])
    for index, (source_id, y) in enumerate(placements):
        path = f'{CONTROL_ROOT}/STS_{index+1:02d}'
        root = UsdGeom.Xform.Define(stage, path)
        root.GetPrim().GetReferences().AddReference('../assets/rtg/STS_Crane_Controlled.usdc', '/STSCrane')
        root.AddTranslateOp().Set(Gf.Vec3d(-241.42000000476837, y, 0))
        root.AddRotateZOp().Set(-90.0)
        # Disjoint slots keep neighbouring cranes apart under independent commands.
        low = max(-7.0, (placements[index-1][1]+y)/2+3.0) if index else -7.0
        high = min(507.0, (placements[index+1][1]+y)/2-3.0) if index+1 < len(placements) else 507.0
        for name, value, typ in (
            ('sourceId', source_id, Sdf.ValueTypeNames.String),
            ('gantryMin', low-y, Sdf.ValueTypeNames.Double),
            ('gantryMax', high-y, Sdf.ValueTypeNames.Double),
            ('homeY', y, Sdf.ValueTypeNames.Double),
        ):
            root.GetPrim().CreateAttribute(f'sts:{name}', typ, custom=True).Set(value)
        from sts_control import rope_endpoints, cylinder_matrix
        UsdGeom.Xform.Define(stage, f'{path}/Gantry/DynamicCables')
        ends = rope_endpoints(1.62, 2.78)
        ends += [(Gf.Vec3d(0, 4.06, 13.94), Gf.Vec3d(x, y_tip, 9.86))
                 for y_tip in (-16.38, 12.38) for x in (-2.05, 2.05)]
        for i, (start, end) in enumerate(ends):
            cable = UsdGeom.Cylinder.Define(stage, f'{path}/Gantry/DynamicCables/Cable_{i}')
            cable.CreateRadiusAttr(1.0)
            cable.CreateHeightAttr(1.0)
            cable.CreateAxisAttr('Z')
            cable.CreateDisplayColorAttr([Gf.Vec3f(0.025)])
            cable.AddTransformOp().Set(cylinder_matrix(start, end, 0.012 if i < 4 else 0.017))
    layer.Save()
    print('STS_LAYER_BUILT', placements, LAYER)


if __name__ == '__main__':
    build()
