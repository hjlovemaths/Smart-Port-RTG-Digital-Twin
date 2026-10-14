"""Regenerate only static rope positions; leave RTG assets and placement intact."""
from pathlib import Path
from pxr import Usd,UsdGeom,Gf,Vt
path=Path(__file__).resolve().parents[1]/'scenes/static_rtg_yard_population.usda'
s=Usd.Stage.Open(str(path))
root=s.GetPrimAtPath('/World/PortAndRTG/SMART_PORT_STATIC_RTG_HOIST_ROPES')
for prim in root.GetChildren():
    curves=UsdGeom.BasisCurves(prim);attr=curves.GetPointsAttr()
    times=attr.GetTimeSamples();initial=attr.Get(times[0])
    name=prim.GetName().removesuffix('_HOIST_ROPES')
    spreader=s.GetPrimAtPath('/World/PortAndRTG/'+name+'/Model/AnimatedSpreader')
    box=UsdGeom.BBoxCache(Usd.TimeCode(times[0]),['default','render']).ComputeWorldBound(spreader).ComputeAlignedBox()
    center=box.GetMidpoint()
    lower_center=Gf.Vec3d(*(sum(initial[i][j] for i in range(1,len(initial),2))/(len(initial)//2) for j in range(3)))
    correction=Gf.Vec3d(center[0]-lower_center[0],center[1]-lower_center[1],box.GetMax()[2]-.0025-lower_center[2])
    for time in times:
        points=list(attr.Get(time))
        for i in range(0,len(points),2):
            # Keep the calibrated reeving offset; both ends move with trolley.
            points[i]=Gf.Vec3f(initial[i][0]+points[i+1][0]-initial[i+1][0],initial[i][1],initial[i][2])
            points[i]=Gf.Vec3f(Gf.Vec3d(points[i])+Gf.Vec3d(correction[0],correction[1],0))
            points[i+1]=Gf.Vec3f(Gf.Vec3d(points[i+1])+correction)
        attr.Set(Vt.Vec3fArray(points),time)
    attr.Set(attr.Get(times[0]))
s.GetRootLayer().Save()
print('Static RTG ropes repaired: upper/lower trolley motion and default points')
