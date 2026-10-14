"""Real yard -> RTG -> truck -> STS -> ship integration check."""
from pxr import Usd,UsdGeom
from iecs_runtime import Runtime,PROJECT
s=Usd.Stage.Open(str(PROJECT/'omniverse/scenes/smart_port.usda'))
r=Runtime(s,port=0,with_ui=False,subscribe=False,db_path=':memory:')
try:
    r.process({'action':'submit','flow':'load','request_id':'real-load-test'})
    o=next(iter(r.engine.orders.values()))
    print('SOURCE',o['source_prim'],'TARGET',o['target']['point'])
    r.engine.control('speed',20)
    for _ in range(4000):
        r.engine.tick(.25);r.apply_scene()
        if o['status']=='completed':break
    assert o['status']=='completed',o
    assert r.yard_drops[o['id']].finished
    assert r.pickups[o['id']].finished
    actual=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(s.GetPrimAtPath(o['source_prim'])).ComputeAlignedBox().GetMidpoint()
    assert all(abs(actual[i]-o['target']['point'][i])<.003 for i in range(3)),(actual,o['target'])
    assert not s.GetPrimAtPath('/World/IECS/Containers/IECS_DEMO_0001')
    r.process({'action':'reset'})
    print('Yard to Ship PASS: real RTG pickup, lane transport, real STS placement, reset')
finally:r.close()
