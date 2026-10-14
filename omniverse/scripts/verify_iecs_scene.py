"""Headless USD/HTTP integration verification using the Kit Python runtime."""
import json
import urllib.request
from pxr import Usd,UsdGeom
from iecs_runtime import Runtime,PROJECT

stage=Usd.Stage.Open(str(PROJECT/'omniverse/scenes/smart_port.usda'))
session_before=list(stage.GetSessionLayer().subLayerPaths)
r=Runtime(stage,port=0,with_ui=False,subscribe=False,db_path=':memory:')
try:
    port=r.server.server_address[1]
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health') as response:
        assert json.load(response)['ready']
    req=urllib.request.Request(f'http://127.0.0.1:{port}/command',data=json.dumps(
        {'action':'submit','flow':'unload','request_id':'integration-01'}).encode(),
        headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req) as response:assert response.status==202
    r.process(r.commands.get_nowait())
    r.engine.control('speed',20)
    for _ in range(4000):
        r.engine.tick(.25)
        if all(o['status']=='completed' for o in r.engine.orders.values()):break
    r.apply_scene();r.publish()
    o=next(iter(r.engine.orders.values()));assert o['status']=='completed'
    assert r.engine.containers[o['container']]['location']==o['target']['id']
    box=stage.GetPrimAtPath(o['source_prim'])
    assert box
    actual_center=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(box).ComputeAlignedRange().GetMidpoint()
    assert all(abs(actual_center[i]-o['target']['point'][i])<.003 for i in range(3)),(actual_center,o['target'])
    assert r.yard_drops[o['id']].finished
    rig=r.yard_drops[o['id']]
    from yard_coordinate_mapping import RTG_TIRE_TRACK_OFFSETS_X_M,VISUAL_YARD_LAYOUT_CENTER_X_M
    centers=rig.pickup.load_sibling('iecs_yard_drop').wheel_track_centers(stage)
    guides=sorted(VISUAL_YARD_LAYOUT_CENTER_X_M+x for x in RTG_TIRE_TRACK_OFFSETS_X_M)
    assert all(abs(a-b)<.001 for a,b in zip(centers,guides)),(centers,guides)
    from sts_yard_delivery import GANTRY
    bounds=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    right_columns=[bounds.ComputeWorldBound(p).ComputeAlignedBox().GetMin()[0]
        for p in Usd.PrimRange(stage.GetPrimAtPath(GANTRY))
        if p.IsA(UsdGeom.Boundable) and 'portal_vertical_column' in p.GetName()
        and bounds.ComputeWorldBound(p).ComputeAlignedBox().GetMidpoint()[0]>0]
    box_edge=bounds.ComputeWorldBound(box).ComputeAlignedBox().GetMax()[0]
    assert right_columns and min(right_columns)-box_edge>.5,(right_columns,box_edge)
    assert not stage.GetPrimAtPath('/World/IECS/Containers/IECS_DEMO_0001')
    mesh=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    truck=stage.GetPrimAtPath(r.vehicle_path(o['vehicle']))
    assert not mesh.ComputeWorldBound(truck).ComputeAlignedRange().IsEmpty()
    assert abs(mesh.ComputeWorldBound(truck).ComputeAlignedRange().GetMin()[2]-.085)<.001
    r.process({'action':'reset'});r.apply_scene()
    assert not stage.GetPrimAtPath('/World/IECS/Containers/IECS_DEMO_0001')
    assert not r.engine.orders
    print('IECS integration PASS: HTTP queue, USD vehicles/boxes, complete unload, reset')
finally:r.close()
assert list(stage.GetSessionLayer().subLayerPaths)==session_before
