from pathlib import Path
from pxr import Gf, Usd, UsdGeom
from sts_control import STSController

root = Path(__file__).resolve().parents[1]
stage = Usd.Stage.Open(str(root/'omniverse/scenes/sts_controls.usda'))
def verify(crane):
    cache = UsdGeom.XformCache()
    base = str(crane.GetPath())+'/Gantry'
    for i,(j,k) in enumerate(((0,0),(0,1),(1,0),(1,1))):
        socket = stage.GetPrimAtPath(base+f'/Trolley/Hoist/STS_TelescopicSpreader/Platform/RopeSocket{j}_{k}')
        cable = stage.GetPrimAtPath(base+f'/DynamicCables/Cable_{i}')
        a = cache.GetLocalToWorldTransform(socket).Transform(Gf.Vec3d(0))
        b = cache.GetLocalToWorldTransform(cable).Transform(Gf.Vec3d(0,0,.5))
        assert (a-b).GetLength()<1e-6, (socket.GetPath(),a,b)
cranes = list(stage.GetPrimAtPath('/World/STSControls').GetChildren())
for crane in cranes:
    verify(crane)
c = STSController(stage,cranes[0].GetName())
for size in (20,40):
    c.set_spreader_size(size)
    for trolley,hoist in ((1.62,2.78),(8,5),(-4,1)):
        c.state.update(trolley=trolley,hoist=hoist)
        c._apply()
        verify(cranes[0])
c.close()
print(f'PASS: {len(cranes)} resting cranes; 20/40 ft and three moving poses, all four rope endpoints match sockets.')
