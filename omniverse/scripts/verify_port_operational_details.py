"""Check geometry validity, measured road clearances and scene isolation."""
import json, math
from pxr import Usd, UsdGeom, UsdShade, UsdUtils
import build_port_operational_details as b

def verify():
    s=Usd.Stage.Open(str(b.LAYER));triangles=meshes=0
    for p in s.Traverse():
        path=str(p.GetPath())
        assert path==b.BASE or path.startswith(b.BASE+'/') or path.startswith(b.NIGHT+'/') or b.NIGHT.startswith(path)
        assert not p.HasAPI('PhysicsCollisionAPI')
        if p.GetTypeName().endswith('Light'):
            assert path.startswith(b.NIGHT+'/') and not p.GetAttribute('inputs:shadow:enable').Get()
        if not p.IsA(UsdGeom.Mesh):continue
        meshes+=1;m=UsdGeom.Mesh(p);pts=m.GetPointsAttr().Get();ids=m.GetFaceVertexIndicesAttr().Get();counts=m.GetFaceVertexCountsAttr().Get()
        assert sum(counts)==len(ids) and min(ids)>=0 and max(ids)<len(pts)
        assert UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
        triangles+=sum(n-2 for n in counts)
        for x,y,z in pts:
            assert all(math.isfinite(v) for v in (x,y,z))
            if 'BridgeMarkers' in str(p.GetPath()):
                cy=min(b.CROSS,key=lambda v:abs(v-y))
                assert 4.19<abs(y-cy)<4.30 and -201.5<x<-101.5
                assert .99<z<1.25
            else:
                # Existing green verges only, excluding every cross-road opening.
                assert (-101.75<x<-85.875) or (105.25<x<110.5),(p.GetPath(),x)
                assert any(a<y<c for a,c in b.SEGMENTS),(p.GetPath(),y)
                assert .09<z<3.3
    assert meshes<=40 and triangles<32000
    assert not s.GetCompositionErrors() and not UsdUtils.ComputeAllDependencies(str(b.LAYER))[2]
    main=Usd.Stage.Open(str(b.ROOT/'omniverse/scenes/smart_port.usda'))
    # All new elevated equipment stays outside every RTG's full operating column.
    from yard_coordinate_mapping import YARD_COLUMN_CENTERS_X,RTG_PHYSICAL_WIDTH_X_M
    assert min(YARD_COLUMN_CENTERS_X)-RTG_PHYSICAL_WIDTH_X_M/2>-85.875
    assert max(YARD_COLUMN_CENTERS_X)+RTG_PHYSICAL_WIDTH_X_M/2<105.25
    assert not main.GetCompositionErrors()
    assert main.GetPrimAtPath(b.BASE) and main.GetPrimAtPath(b.NIGHT)
    import smart_port_time_of_day as tod
    before=main.GetRootLayer().ExportToString()
    lamps=[p for p in Usd.PrimRange(main.GetPrimAtPath(b.NIGHT)) if p.GetTypeName()=='DiskLight']
    assert len(lamps)==8
    for hour,visibility in ((21,'inherited'),(3,'inherited'),(12,'invisible')):
        tod.apply_time(main,hour,configure=False)
        assert all(UsdGeom.Imageable(p).ComputeVisibility()==visibility for p in lamps)
    assert before==main.GetRootLayer().ExportToString()
    report={'meshes':meshes,'triangles':triangles,'geometry_and_materials':'pass','road_bridge_and_rtg_clearance':'pass','composition':'pass','night_sign_lights':8,'day_night_and_root_isolation':'pass'}
    (b.ROOT/'outputs/port_operational_details/validation_usd.json').write_text(json.dumps(report,indent=2));print(report)
if __name__=='__main__':verify()
