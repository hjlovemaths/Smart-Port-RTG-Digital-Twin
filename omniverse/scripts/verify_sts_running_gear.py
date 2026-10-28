"""Check wheel count, clearance, rail alignment and inherited gantry travel."""
from pathlib import Path
from pxr import Gf, Usd, UsdGeom

ROOT = Path(__file__).resolve().parents[2]


def verify():
    stage = Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'), Usd.Stage.LoadNone)
    baseline = Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'), Usd.Stage.LoadNone)
    baseline.MuteLayer(str(ROOT/'omniverse/scenes/sts_running_gear.usda'))
    base_cache = UsdGeom.BBoxCache(0, ['default','render'])
    stage.SetEditTarget(stage.GetSessionLayer())
    for crane in stage.GetPrimAtPath('/World/STSControls').GetChildren():
        cache = UsdGeom.BBoxCache(0, ['default','render'])
        gantry = crane.GetChild('Gantry')
        gear = gantry.GetChild('RunningGear')
        assert gear.IsValid(), crane.GetPath()
        sides = list(gear.GetChildren())
        assert len(sides) == 2
        legs = [p for p in gantry.GetChild('Fixed').GetChildren() if '_orange_leg_' in p.GetName()]
        leg_ranges = sorted([cache.ComputeWorldBound(p).ComputeAlignedRange() for p in legs],key=lambda b:(b.GetMidpoint()[0],b.GetMidpoint()[1]))
        for i in (0,2):
            opening = leg_ranges[i+1].GetMin()[1]-leg_ranges[i].GetMax()[1]
            assert abs(opening-11.04)<1e-4, (crane.GetName(),opening)
        spreader = cache.ComputeWorldBound(gantry.GetChild('Trolley').GetChild('Hoist')).ComputeAlignedRange()
        assert abs(spreader.GetSize()[1]-5.28)<1e-4, 'spreader width changed'
        for parent_name in ('Fixed','Boom'):
            for part in gantry.GetChild(parent_name).GetChildren():
                old = baseline.GetPrimAtPath(part.GetPath())
                original = base_cache.ComputeWorldBound(old).ComputeAlignedRange()
                current = cache.ComputeWorldBound(part).ComputeAlignedRange()
                names = [op.GetOpName() for op in UsdGeom.Xformable(part).GetOrderedXformOps()]
                if 'xformOp:transform:stsUpperDeckWidth' in names:
                    for axis in (0,2):
                        assert abs(current.GetMin()[axis]-original.GetMin()[axis])<1e-4
                        assert abs(current.GetMax()[axis]-original.GetMax()[axis])<1e-4
                    if original.GetSize()[1]>7:
                        assert abs(current.GetSize()[1]-original.GetSize()[1]-4)<1e-4
                    else:
                        assert abs(abs(current.GetMidpoint()[1]-original.GetMidpoint()[1])-2)<1e-4
                if 'trolley_rail' in part.GetName():
                    assert (current.GetMidpoint()-original.GetMidpoint()).GetLength()<1e-4
        # Both sides of the fixed/hinged seam must retain a common deck edge.
        seam_parts = [p for parent in ('Fixed','Boom') for p in gantry.GetChild(parent).GetChildren()
                      if 'orange_mid_girder' in p.GetName()]
        assert len(seam_parts)==2
        a,b = [cache.ComputeWorldBound(p).ComputeAlignedRange() for p in seam_parts]
        assert abs(a.GetMin()[1]-b.GetMin()[1])<1e-4
        assert abs(a.GetMax()[1]-b.GetMax()[1])<1e-4
        for side in sides:
            assert side.GetChild('ConnectingBeam').IsValid()
            assert side.GetChild('UpperPortalHeader').IsValid()
            feet = [p for p in side.GetChildren() if p.GetName().startswith('Foot_')]
            wheels = [w for f in feet for w in f.GetChildren() if w.GetName().startswith('Wheel_')]
            assert len(feet) == 2 and len(wheels) == 8
            ranges = sorted([cache.ComputeWorldBound(w).ComputeAlignedRange() for w in wheels],key=lambda b:b.GetMin()[1])
            assert all(a.GetMax()[1]<b.GetMin()[1] for a,b in zip(ranges,ranges[1:])), 'wheel overlap'
            rail_x = ranges[0].GetMidpoint()[0]
            assert all(abs(b.GetMidpoint()[0]-rail_x)<1e-5 for b in ranges)
            for foot in feet:
                beam = cache.ComputeWorldBound(foot.GetChild('WideBogieBeam')).ComputeAlignedRange()
                for w in foot.GetChildren():
                    if not w.GetName().startswith('Wheel_'): continue
                    wb = cache.ComputeWorldBound(w).ComputeAlignedRange()
                    assert beam.GetMin()[1]<wb.GetMin()[1]<wb.GetMax()[1]<beam.GetMax()[1]
            for leg in gantry.GetChild('Fixed').GetChildren():
                if '_orange_leg_' in leg.GetName():
                    assert cache.ComputeWorldBound(leg).ComputeAlignedRange().GetMin()[2] > ranges[0].GetMax()[2]
        before = UsdGeom.XformCache(0).GetLocalToWorldTransform(gear).ExtractTranslation()
        UsdGeom.Xformable(gantry).AddTranslateOp(opSuffix='verifyTravel').Set(Gf.Vec3d(1,0,0))
        after = UsdGeom.XformCache(0).GetLocalToWorldTransform(gear).ExtractTranslation()
        assert (after-before).GetLength() > .5, 'running gear did not follow gantry'
        print(f'PASS {crane.GetName()}: 11.04 m opening, deck +4 m, aligned hinge seam, unchanged trolley rails, 8 wheels per side')


if __name__ == '__main__':
    verify()
