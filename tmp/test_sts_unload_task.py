from pxr import Usd, UsdGeom
import sys
from pathlib import Path
from sts_control import STSController, load_unload_task_class
script_dir=Path('omniverse/scripts').resolve()
sys.path[:]=[p for p in sys.path if Path(p).resolve()!=script_dir]
sys.modules.pop('sts_unload_task',None)
UnloadTask=load_unload_task_class()
task_globals=UnloadTask.prepare.__globals__
bounds,BOX,TASK=(task_globals[n] for n in ('bounds','BOX','TASK'))
s=Usd.Stage.Open('omniverse/scenes/smart_port.usda')
original=bounds(s,BOX).GetMidpoint()
rtg_path='/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL'
rtg_original=UsdGeom.Xformable(s.GetPrimAtPath(rtg_path)).GetLocalTransformation()
start_cache=UsdGeom.XformCache(Usd.TimeCode(s.GetStartTimeCode()))
wheel_reference={str(p.GetPath()):start_cache.GetLocalToWorldTransform(p).ExtractTranslation()
    for p in Usd.PrimRange(s.GetPrimAtPath(rtg_path))
    if p.GetName().startswith('Y_WHEEL_') and 'sidewall_ring' in p.GetName()}
assert wheel_reference
c=STSController(s,'STS_01')
t=UnloadTask(c)
task_cache=UsdGeom.XformCache()
for path,expected in wheel_reference.items():
    actual=task_cache.GetLocalToWorldTransform(s.GetPrimAtPath(path)).ExtractTranslation()
    assert (actual-expected).GetLength()<1e-6, ('RTG shifted from authored pose',path,actual,expected)
print('PASS RTG wheel positions match authored start/live reference')
print('PICK',t.pick,'PLACE',t.place,'POSE',t.pick_pose,t.place_pose)
last=''
assert t.start[1]<t.stop[1]
cab=bounds(s,TASK+'/Truck/CabBody').GetMidpoint()
deck=bounds(s,TASK+'/Truck/TrailerDeck').GetMidpoint()
assert cab[1]>deck[1], 'Truck cab must face forward along +Y'
pause_checked=False
for i in range(10000):
    c.tick(.1)
    t.tick(.1)
    if t.phase!=last:
        print(i,t.phase)
        last=t.phase
    if t.attached:
        b=bounds(s,BOX)
        assert abs(b.GetMax()[2]-t.contact()[2])<1e-5
    if t.delivering and t.delivery.loaded and i>500:
        b=bounds(s,BOX)
        d=bounds(s,TASK+'/Truck/TrailerDeck')
        assert abs(b.GetMin()[2]-d.GetMax()[2]-.02)<1e-5
        # Asymmetric door fittings change an axis-aligned bounding-box center
        # slightly while rotating; the load's rigid center stays on the deck.
        assert abs(b.GetMidpoint()[0]-d.GetMidpoint()[0])<.05, (i,b,d)
        assert abs(b.GetMidpoint()[1]-d.GetMidpoint()[1])<.05, (i,b,d)
        if not pause_checked:
            before=b.GetMidpoint()
            t.paused=True
            for _ in range(10):
                t.tick(.1)
            assert (bounds(s,BOX).GetMidpoint()-before).GetLength()<1e-8
            t.paused=False
            pause_checked=True
    if t.delivering and t.delivery.attached:
        assert abs(bounds(s,BOX).GetMax()[2]-t.delivery.contact()[2])<1e-5
    if t.finished:
        break
assert t.finished,'timeout'
b=bounds(s,BOX)
assert abs(b.GetMin()[2]-.12)<1e-5
assert abs(b.GetMidpoint()[0]-t.delivery.target[0])<1e-5
assert abs(b.GetMidpoint()[1]-135.)<1e-5
t.close()
assert (bounds(s,BOX).GetMidpoint()-original).GetLength()<1e-6
assert UsdGeom.Xformable(s.GetPrimAtPath(rtg_path)).GetLocalTransformation()==rtg_original
c.close()
print('PASS reversed truck, STS pickup, road transport, RTG handoff, 2C004 row1 tier1, pause and reset')
