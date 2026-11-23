"""Exercise the documented HTTP handshakes against the actual port scene."""

import json
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'services'), str(ROOT/'omniverse/scripts'),
                str(ROOT/'omniverse/extensions/smartport.agent_panel')]


class ExternalUnloadTests(unittest.TestCase):
    def test_complete_exchange(self):
        self.run_unload(allow=True)

    def test_landing_refusal_holds_the_box(self):
        self.run_unload(allow=False)

    def run_unload(self, allow):
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            self.skipTest('Requires USD runtime')
        from iecs_external_http import ExternalConfig
        from iecs_core import route
        from iecs_quay_clearance import quay_route_obstacle
        from iecs_runtime import Runtime
        from iecs_test_peer import TestPeer, unload
        from smartport.agent_panel.project_interfaces import external_iecs

        stage = Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        layers = list(stage.GetSessionLayer().subLayerPaths)
        peer = TestPeer(0, allow_landing=allow)
        self.addCleanup(peer.close)
        config = ExternalConfig(port=0,callback=f'http://127.0.0.1:{peer.port}',token='unit-test-token')
        runtime = Runtime(stage,port=None,with_ui=False,subscribe=False,db_path=':memory:',
                          external_config=config)
        self.addCleanup(runtime.close)
        def set_speed(speed):
            with patch('smartport.agent_panel.project_interfaces.iecs_runtime',return_value=runtime):
                external_iecs(SimpleNamespace(stage=stage),{'action':'speed','speed':speed})

        with self.assertRaisesRegex(ValueError,'数字场景速度'):
            set_speed(30)
        set_speed(20)
        self.assertEqual(runtime.cached['speed'],20)
        self.assertEqual(runtime.engine.events[-1]['kind'],'speed')
        with self.assertRaisesRegex(ValueError,'真实 IECS'):
            runtime.process({'action':'submit','flow':'unload'})
        checked_trailer = set()
        carried_y = set()
        checked_approach = False
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(unload,peer,runtime.external.snapshot()['listen'],config.token,22,
                              log=lambda _: None)
            deadline = time.monotonic()+26
            while time.monotonic()<deadline and (not job.done() or not allow and not runtime.external.fault):
                runtime.advance(.1)
                order = runtime.external.order
                if order:
                    truck = runtime.engine.vehicles['ITT-001']
                    cargo = runtime.engine.containers[order['container']]
                    if order['step']==0 and order['path'] and not checked_approach:
                        source = order['source']
                        direct = route(truck['home'],source['stop'],target=source,quay_cross=256.30)
                        self.assertEqual(quay_route_obstacle(stage,direct),'STS_04')
                        self.assertIsNone(quay_route_obstacle(stage,order['path']))
                        self.assertTrue(any(x<-215 and abs(y-127.90)<.01 for x,y,_ in order['path']))
                        checked_approach = True
                    if cargo['owner']=='ITT-001':
                        checkpoint = ('quay' if cargo['position'][0]<-200 else
                                      'turn' if abs(abs(truck['heading'])-90)>30 else 'yard')
                        if checkpoint not in checked_trailer:
                            self.assert_front_slot(stage,runtime,truck,cargo)
                            checked_trailer.add(checkpoint)
                        if checkpoint=='turn' and runtime.engine.speed==20:
                            set_speed(10)
                            self.assertEqual(runtime.cached['speed'],10)
                    yard_rig = runtime.yard_drops.get(order['id'])
                    if yard_rig and yard_rig.attached:
                        carried_y.add(yard_rig.contact()[1])
                time.sleep(.003)
            job.result(timeout=1)

        traffic = runtime.external.transport.traffic
        inbound = traffic.read(direction='received')
        self.assertTrue(all(entry['direction']=='received' and 'request' not in entry for entry in inbound))
        dispatches = [traffic.read(entry['id']) for entry in inbound if entry['pid']==114]
        self.assertEqual(len(dispatches),2)
        self.assertEqual(json.loads(dispatches[0]['request'])['taskList'][0]['soaCntrnoHb'],'TWIN000001')
        self.assertTrue(json.loads(dispatches[0]['response'])['success'])
        self.assertTrue(any(json.loads(entry['response'])['duplicate'] for entry in dispatches))
        outbound = traffic.read(direction='sent')
        permission = traffic.read(next(entry['id'] for entry in outbound if entry['pid']==173))
        self.assertEqual(permission['path'],'/landBoxApply')
        self.assertEqual(permission['status'],200)
        self.assertEqual(json.loads(permission['request'])['cntrNo'],'TWIN000001')
        self.assertEqual(json.loads(permission['response'])['cntrReleaseFlag'],'1' if allow else '0')
        self.assertFalse(any(entry['pid'] in (129,215) for entry in outbound))
        self.assertTrue(any(entry['pid'] in (129,215) for entry in traffic.read(telemetry=True)))

        self.assertEqual(len(runtime.engine.orders),1)
        self.assertTrue(checked_approach)
        order = next(iter(runtime.engine.orders.values()))
        cargo = runtime.engine.containers['TWIN000001']
        rig = runtime.pickups[order['id']]
        if allow:
            self.assertEqual(order['status'],'completed')
            self.assertEqual(cargo['location'],order['target']['id'])
            self.assertEqual(order['vehicle'],'ITT-001')
            self.assertEqual(runtime.engine.vehicles['ITT-001']['status'],'idle')
            original = stage.GetPrimAtPath(rig.box_path)
            visual = stage.GetPrimAtPath(cargo['visual_prim'])
            self.assertEqual(UsdGeom.Imageable(original).ComputeVisibility(),'invisible')
            self.assertNotEqual(UsdGeom.Imageable(visual).ComputeVisibility(),'invisible')
            center = UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(
                visual).ComputeAlignedRange().GetMidpoint()
            for actual,expected in zip(center,order['target']['point']):
                self.assertAlmostEqual(actual,expected,places=2)
            self.assertTrue(runtime.yard_drops[order['id']].finished)
            self.assertEqual(checked_trailer,{'quay','turn','yard'})
            self.assertAlmostEqual(rig.place[1],rig.source[1],places=4)
            self.assertAlmostEqual(runtime.yard_drops[order['id']].pick[1],order['target']['point'][1],places=4)
            self.assertTrue(carried_y)
            for y in carried_y:
                self.assertAlmostEqual(y,order['target']['point'][1],places=4)
            packets = [m['pid'] for m in peer.messages]
            self.assertEqual(packets.count(115),1)
            self.assertEqual(packets.count(225),1)
            self.assertLess(packets.index(173),packets.index(212))
            self.assertLess(packets.index(234),packets.index(213))
        else:
            self.assertIn('未准许',runtime.external.fault)
            self.assertTrue(runtime.engine.paused)
            self.assertEqual(cargo['location'],'SPREADER/STS_01')
            self.assertTrue(rig.attached)
            self.assertEqual(order['step'],1)
            self.assertNotIn(225,[m['pid'] for m in peer.messages])
        runtime.close()
        self.assertEqual(list(stage.GetSessionLayer().subLayerPaths),layers)

    def assert_front_slot(self,stage,runtime,vehicle,cargo):
        from pxr import Gf, Usd, UsdGeom
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        truck = stage.GetPrimAtPath(runtime.vehicle_path(vehicle['id']))
        deck = Gf.Range3d()
        for part in Usd.PrimRange(truck,Usd.TraverseInstanceProxies()):
            if part.IsA(UsdGeom.Boundable) and 'trailer_side_rail' in part.GetName():
                deck.UnionWith(cache.ComputeRelativeBound(part,truck).ComputeAlignedRange())
        visual = stage.GetPrimAtPath(cargo['visual_prim'])
        box_center = cache.ComputeWorldBound(visual).ComputeAlignedRange().GetMidpoint()
        world_to_truck = UsdGeom.XformCache().GetLocalToWorldTransform(truck).GetInverse()
        local = world_to_truck.Transform(box_center)
        # The asset's cab is toward -Y. Check the front edge and support height
        # in truck coordinates, independent of the world-space travel heading.
        length_scale = UsdGeom.XformCache().GetLocalToWorldTransform(truck).TransformDir(Gf.Vec3d(0,1,0)).GetLength()
        front_gap = (local[1]-deck.GetMin()[1])*length_scale-cargo['visual_size'][1]/2
        self.assertAlmostEqual(front_gap,.08,places=3)
        self.assertAlmostEqual(local[2]-cargo['visual_size'][2]/2,deck.GetMax()[2],places=3)


if __name__ == '__main__':
    unittest.main()
