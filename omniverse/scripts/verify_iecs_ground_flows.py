"""Verify gate flows, yard transfer and five real-equipment mixed orders."""
from pxr import Usd,UsdGeom
from iecs_runtime import Runtime,PROJECT
for flow in ('gate_in','gate_out','transfer','mixed'):
    stage=Usd.Stage.Open(str(PROJECT/'omniverse/scenes/smart_port.usda'))
    r=Runtime(stage,port=0,with_ui=False,subscribe=False,db_path=':memory:')
    try:
        r.process({'action':'mixed'} if flow=='mixed' else {'action':'submit','flow':flow})
        if flow=='gate_in':
            order=next(iter(r.engine.orders.values()))
            size=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(stage.GetPrimAtPath(order['source_prim'])).ComputeAlignedBox().GetSize()
            assert all(abs(size[i]-value)<.001 for i,value in enumerate((1.306287,5.15,.904353))),size
        r.engine.control('speed',20)
        for _ in range(12000):
            r.engine.tick(.25);r.apply_scene();r.advance_mixed()
            if not r.mixed_pending and all(o['status']=='completed' for o in r.engine.orders.values()):break
        assert len(r.engine.orders)==(5 if flow=='mixed' else 1)
        assert all(o['status']=='completed' for o in r.engine.orders.values())
        assert not r.engine.reserved and not r.engine.equipment
        for o in r.engine.orders.values():
            assert o.get('source_prim'),o
            p=stage.GetPrimAtPath(o['source_prim']);c=r.engine.containers[o['container']]
            visual=stage.GetPrimAtPath('/World/IECS/CargoVisuals/'+c['id'].replace('-','_'))
            assert visual and visual.GetChildren()
            model=stage.GetPrimAtPath(str(visual.GetPath())+'/Asset')
            assert model and model.HasAuthoredReferences()
            assert stage.GetPrimAtPath(str(model.GetPath())+'/R01_T01_Container_Front_LockingBars')
            if o['flow']!='gate_out':
                vb=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(visual).ComputeAlignedBox()
                assert all(abs(vb.GetMidpoint()[i]-c['position'][i])<.02 for i in range(3)),(vb,c)
            if o['flow']=='gate_out':assert UsdGeom.Imageable(p).ComputeVisibility()=='invisible'
            else:
                center=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(p).ComputeAlignedBox().GetMidpoint()
                assert all(abs(center[i]-c['position'][i])<.003 for i in range(3)),(o['flow'],center,c)
        print(flow,'PASS')
        preview=r.inventory_preview()
        assert not preview['production_write'] and len(preview['orders'])==len(r.engine.orders)
        assert r.check_scene()['ok'],r.scene_report
        if flow=='gate_in':
            first_target=next(iter(r.engine.orders.values()))['target']['id']
            r.process({'action':'submit','flow':'gate_in'})
            second=list(r.engine.orders.values())[-1]
            assert second['target']['id']!=first_target
            for _ in range(4000):
                r.engine.tick(.25);r.apply_scene()
                cargo=r.engine.containers[second['container']]
                size=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(stage.GetPrimAtPath(second['source_prim'])).ComputeAlignedBox().GetSize()
                if cargo['location'].startswith('SPREADER/'):
                    assert size[1]>size[0]*3,(cargo,size)
                if second['status']=='completed':break
            assert second['status']=='completed'
            assert size[1]>size[0]*3
            assert r.check_scene()['ok'],r.scene_report
            print('second consecutive gate_in heading PASS')
        r.process({'action':'reset'})
    finally:r.close()
