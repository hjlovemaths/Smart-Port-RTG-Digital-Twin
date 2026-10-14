from pathlib import Path
from pxr import Usd,UsdGeom
s=Usd.Stage.Open(str(Path(__file__).resolve().parents[1]/'scenes/smart_port.usda'))
root=s.GetPrimAtPath('/World/PortAndRTG/SMART_PORT_STATIC_RTG_HOIST_ROPES')
count=0
for prim in root.GetChildren():
    attr=UsdGeom.BasisCurves(prim).GetPointsAttr();initial=attr.Get()
    assert initial
    name=prim.GetName().removesuffix('_HOIST_ROPES')
    spreader=s.GetPrimAtPath('/World/PortAndRTG/'+name+'/Model/AnimatedSpreader')
    for time in [Usd.TimeCode.Default()]+[Usd.TimeCode(t) for t in attr.GetTimeSamples()]:
        points=attr.Get(time)
        box=UsdGeom.BBoxCache(time,['default','render']).ComputeWorldBound(spreader).ComputeAlignedBox()
        for i in range(0,len(points),2):
            assert abs((points[i][0]-initial[i][0])-(points[i+1][0]-initial[i+1][0]))<.001
            lower=points[i+1]
            assert all(box.GetMin()[j]-.02<=lower[j]<=box.GetMax()[j]+.02 for j in range(3)),(prim.GetPath(),time,lower,box)
    count+=1
assert count==5
print('PASS: five static RTGs, default pose and all rope animation keys aligned')
