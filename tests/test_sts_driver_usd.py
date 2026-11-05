"""STS operator cameras and controls on the composed port stage."""
import builtins,math,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pxr import Gf,Sdf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from role_viewport import private_stage
from sts_driver_runtime import STSDriver,active_sts_driver,CAMERA,TROLLEY,HOIST

class View:
    def __init__(self,stage):self.stage=private_stage(stage);self.closed=False;self.camera_path=''
    def tick(self):pass
    def ready(self):return not self.closed
    def close(self):self.closed=True

class STSDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.base=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    def setUp(self):
        self.stage=Usd.Stage.Open(self.base.GetRootLayer(),Sdf.Layer.CreateAnonymous())
        self.now=[0.];self.session={'expires':time.time()+1000};self.view=View(self.stage)
        self.driver=STSDriver(self.stage,'owner',self.session,lambda:self.now[0],self.view)
    def tearDown(self):
        self.driver.close()
        pool=getattr(builtins,'_smart_port_sts_controller_pool',None)
        if pool and pool['stage']==self.stage:
            for controller in pool['controllers'].values():controller.close()
            builtins._smart_port_sts_controller_pool=None
    def step(self,**payload):
        self.now[0]+=.1;self.driver.command('owner',payload);self.driver.tick(.1)
    def camera_matrix(self):return UsdGeom.XformCache().GetLocalToWorldTransform(self.view.stage.GetPrimAtPath(CAMERA))
    def ship_box(self,gap=.20,size_ft=20):
        from large_vessel_layout import SHIP
        d=self.driver;d.controller.set_spreader_size(size_ft)
        centre=d.contact();size=Gf.Vec3d(1.369287,2.597,.949853) if size_ft==20 else Gf.Vec3d(1.306287,5.15,.904353)
        path=SHIP+'/StructureSlots/DriverTestBay/L009999'
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            slot=UsdGeom.Xform.Define(self.stage,path);slot.SetResetXformStack(True)
            slot.AddTranslateOp().Set(centre-Gf.Vec3d(0,0,size[2]/2+gap))
            slot.GetPrim().CreateAttribute('ship:sizeFt',Sdf.ValueTypeNames.Int).Set(size_ft)
            cube=UsdGeom.Cube.Define(self.stage,path+'/Geometry');cube.CreateSizeAttr(1);cube.AddScaleOp().Set(size)
        return slot,size,centre[2]-gap
    def test_private_vertical_camera_follows_trolley_and_fov(self):
        m=self.camera_matrix();eye=m.ExtractTranslation()
        self.assertFalse(self.stage.GetPrimAtPath(CAMERA))
        self.assertLess((m.TransformDir(Gf.Vec3d(0,0,-1))-Gf.Vec3d(0,0,-1)).GetLength(),1e-7)
        trolley=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(TROLLEY))
        old_up=trolley.TransformDir(Gf.Vec3d(1,0,0)).GetNormalized()
        self.assertLess((m.TransformDir(Gf.Vec3d(0,1,0))-Gf.Vec3d(-old_up[1],old_up[0],0)).GetLength(),1e-7)
        self.step(hoist=1);self.assertLess((self.camera_matrix().ExtractTranslation()-eye).GetLength(),1e-6)
        self.step(trolley=1);self.assertGreater((self.camera_matrix().ExtractTranslation()-eye).GetLength(),.01)
        for view in ('under','side','end'):
            for fov in (140,70,40,10):
                self.step(view=view,fov=fov)
                focal=self.driver.camera.GetFocalLengthAttr().Get()
                self.assertAlmostEqual(math.degrees(2*math.atan(36/(2*focal))),fov,places=4)
    def test_axes_limits_deadman_owner_and_size(self):
        d=self.driver;before=dict(d.controller.state)
        self.step(gantry=1,trolley=1,hoist=1)
        for axis in ('gantry','trolley','hoist'):self.assertGreater(d.controller.state[axis],before[axis])
        self.step(size_ft=20);self.assertEqual(d.controller.spreader_size_ft,20)
        d.command('owner',{'seq':100,'brake':True});d.command('owner',{'seq':99,'hoist':1});self.assertEqual(d.axes['hoist'],0)
        d.command('owner',{'seq':101,'hoist':1});self.now[0]+=.5;position=d.controller.state['hoist'];d.tick(.1)
        self.assertEqual(d.controller.state['hoist'],position)
        for data in ({'hoist':float('nan')},{'fov':9},{'size_ft':30},{'lock':True}):
            with self.assertRaises(ValueError):d.command('owner',data)
        with self.assertRaises(ValueError):d.command('other',{})
        with self.assertRaises(ValueError):STSDriver(self.stage,'other',self.session,view=self.view)
        self.session['driver_released']=True;d.tick(.1)
        self.assertIsNone(active_sts_driver());self.assertTrue(self.view.closed)
    def test_manual_sts_ownership_does_not_stop_other_drivers(self):
        from smartport.agent_panel.scene_runtime import SceneRuntime
        runtime=SceneRuntime(ROOT);runtime.bind(self.stage)
        with self.assertRaises(ValueError):runtime._guard_manual('sts','STS_01',allow_truck_driver=True)
        runtime._guard_manual('rtg',allow_truck_driver=True,allow_rtg_driver=True)
        runtime.close()

    def test_ship_box_contact_pick_and_unsupported_unlock(self):
        from rtg_driver_cargo import world
        d=self.driver;slot,size,roof=self.ship_box();path=str(slot.GetPath())
        for _ in range(4):self.step(hoist=-1)
        self.assertGreaterEqual(d.contact()[2],roof-1e-5)
        self.assertIn('接触集装箱',d.state()['notice'])
        self.step(lock=True);self.assertEqual(d.cargo.held,path)
        before=world(slot.GetPrim());self.step(hoist=1)
        self.assertGreater(world(slot.GetPrim()).ExtractTranslation()[2],before.ExtractTranslation()[2])
        with self.assertRaisesRegex(ValueError,'承重点'):d.command('owner',{'lock':False})
        with self.assertRaisesRegex(ValueError,'落箱开锁'):d.command('owner',{'size_ft':40})
        self.assertTrue(d.cargo.locked)

    def test_lock_rejects_wrong_size_misalignment_and_covered_box(self):
        from large_vessel_layout import SHIP
        d=self.driver;slot,size,_=self.ship_box(0);prim=slot.GetPrim()
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            prim.GetAttribute('ship:sizeFt').Set(40)
        with self.assertRaisesRegex(ValueError,'尺型'):d.command('owner',{'lock':True})
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            prim.GetAttribute('ship:sizeFt').Set(20)
            origin=prim.GetAttribute('xformOp:translate').Get()
            prim.GetAttribute('xformOp:translate').Set(origin+Gf.Vec3d(.12,0,0))
        with self.assertRaises(ValueError):d.command('owner',{'lock':True})
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            prim.GetAttribute('xformOp:translate').Set(origin)
            cover=UsdGeom.Xform.Define(self.stage,SHIP+'/StructureSlots/DriverTestBay/L009998');cover.SetResetXformStack(True)
            cover.AddTranslateOp().Set(origin+Gf.Vec3d(0,0,size[2]))
            cover.GetPrim().CreateAttribute('ship:sizeFt',Sdf.ValueTypeNames.Int).Set(20)
            cube=UsdGeom.Cube.Define(self.stage,str(cover.GetPath())+'/Geometry');cube.CreateSizeAttr(1);cube.AddScaleOp().Set(size)
        with self.assertRaises(ValueError):d.command('owner',{'lock':True})
        self.assertFalse(d.cargo.locked)

    def test_ship_geometry_cache_is_invalidated_when_box_changes(self):
        slot,size,_=self.ship_box(0);cargo=self.driver.cargo;path=str(slot.GetPath())
        cargo.refresh(True);old=cargo.records[path]['bounds'];self.assertIn(path,cargo.local_bounds)
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            self.stage.GetPrimAtPath(path+'/Geometry').GetAttribute('xformOp:scale').Set(Gf.Vec3d(size[0]*1.5,size[1],size[2]))
        self.assertTrue(cargo.dirty)
        cargo.refresh(now=cargo.last_refresh)
        new=cargo.records[path]['bounds']
        self.assertGreater(new[1][0]-new[0][0],old[1][0]-old[0][0]+.5)

    def test_40ft_box_cannot_unlock_on_partial_truck_support(self):
        from rtg_driver_cargo import TRUCK,world
        d=self.driver;slot,size,_=self.ship_box(0,40);self.step(lock=True)
        centre=world(slot.GetPrim()).ExtractTranslation()
        deck={'pose':Gf.Matrix4d(1).SetTranslate(centre+Gf.Vec3d(0,.30,-size[2]/2-.075)),
              'size':(1.03,5.185,.15)}
        def only_partial(*args,**kwargs):d.cargo.decks={TRUCK:deck};d.cargo.records={}
        with patch.object(d.cargo,'refresh',only_partial):
            with self.assertRaisesRegex(ValueError,'承重点'):d.command('owner',{'lock':False})
        self.assertTrue(d.cargo.locked)

    def test_tilted_box_cannot_unlock_when_only_two_corners_are_supported(self):
        from rtg_driver_cargo import TRUCK,world,write_world
        d=self.driver;slot,size,_=self.ship_box(0);self.step(lock=True);prim=slot.GetPrim()
        original=world(prim);centre=original.ExtractTranslation()
        turn=Gf.Matrix4d(1).SetTranslate(-centre)*Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,1,0),5))*Gf.Matrix4d(1).SetTranslate(centre)
        write_world(self.stage,prim,original*turn);d.cargo.capture(str(prim.GetPath()))
        bounds=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(prim).ComputeAlignedRange()
        deck={'pose':Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(centre[0],centre[1],bounds.GetMin()[2]-.075)),
              'size':(2.,5.185,.15)}
        def only_deck(*args,**kwargs):d.cargo.decks={TRUCK:deck};d.cargo.records={}
        with patch.object(d.cargo,'refresh',only_deck):
            with self.assertRaisesRegex(ValueError,'承重点'):d.command('owner',{'lock':False})
        self.assertTrue(d.cargo.locked)

    def test_carried_box_stops_at_neighbour_and_can_move_away(self):
        from large_vessel_layout import SHIP
        from rtg_driver_cargo import world
        d=self.driver;slot,size,_=self.ship_box(0);self.step(lock=True)
        gantry=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(d.controller.path+'/Gantry'))
        direction=gantry.TransformDir(Gf.Vec3d(0,-1,0)).GetNormalized()
        centre=world(slot.GetPrim()).ExtractTranslation();path=SHIP+'/StructureSlots/DriverTestBay/L009998'
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            obstacle=UsdGeom.Xform.Define(self.stage,path);obstacle.SetResetXformStack(True)
            obstacle.AddTranslateOp().Set(centre+direction*(size[0]+.05))
            obstacle.GetPrim().CreateAttribute('ship:sizeFt',Sdf.ValueTypeNames.Int).Set(20)
            cube=UsdGeom.Cube.Define(self.stage,path+'/Geometry');cube.CreateSizeAttr(1);cube.AddScaleOp().Set(size)
        for _ in range(3):self.step(trolley=1)
        self.assertIn('箱子接触集装箱',d.state()['notice'])
        before=d.controller.state['trolley'];self.step(trolley=-1)
        self.assertLess(d.controller.state['trolley'],before)

    def test_suspended_box_survives_sts_logout_and_blocks_automatic_takeover(self):
        from rtg_driver_cargo import world
        from smartport.agent_panel.scene_runtime import SceneRuntime
        slot,_,_=self.ship_box(0);self.step(lock=True);self.step(hoist=1)
        before=world(slot.GetPrim());self.driver.close()
        runtime=SceneRuntime(ROOT);runtime.bind(self.stage)
        with self.assertRaisesRegex(ValueError,'闭锁'):runtime._guard_manual('sts','STS_01',allow_truck_driver=True)
        self.view=View(self.stage);self.driver=STSDriver(self.stage,'owner',self.session,lambda:self.now[0],self.view)
        self.assertTrue(self.driver.cargo.locked);self.step(hoist=1)
        self.assertGreater(world(slot.GetPrim()).ExtractTranslation()[2],before.ExtractTranslation()[2])
        runtime.close()

    def test_carried_box_stops_at_quay_ground(self):
        d=self.driver;slot,size,_=self.ship_box(0);self.step(lock=True)
        gantry=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(d.controller.path+'/Gantry'))
        direction=gantry.TransformDir(Gf.Vec3d(0,-1,0))
        d.controller.state['trolley']+=(-238-d.contact()[0])/direction[0]
        scale=gantry.TransformDir(Gf.Vec3d(0,0,1))[2]
        d.controller.state['hoist']+=(.18+size[2]+.10-d.contact()[2])/scale
        d.controller.targets=dict(d.controller.state);d.controller._apply();d.cargo.update()
        for _ in range(3):self.step(hoist=-1)
        b=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(slot.GetPrim()).ComputeAlignedRange()
        self.assertIn('码头地面',d.state()['notice']);self.assertGreaterEqual(b.GetMin()[2],.18-1e-5)

    def test_real_ship_structure_box_can_be_aligned_and_locked(self):
        from large_vessel_layout import SHIP
        from ship_transfer_plan import solve
        from rtg_driver_cargo import world
        d=self.driver;d.controller.set_spreader_size(20)
        group=self.stage.GetPrimAtPath(SHIP+'/StructureSlots/Bay05_D')
        candidates=[];cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        for prim in group.GetChildren():
            if prim.GetAttribute('ship:sizeFt').Get()!=20:continue
            b=cache.ComputeWorldBound(prim).ComputeAlignedRange();target=Gf.Vec3d(b.GetMidpoint()[0],b.GetMidpoint()[1],b.GetMax()[2])
            try:pose=solve(self.stage,d.controller,target)
            except ValueError:continue
            candidates.append((prim.GetAttribute('ship:tier').Get(),prim,pose))
        self.assertTrue(candidates,'STS_01 must be able to reach a real ship structure slot')
        for _,prim,pose in sorted(candidates,key=lambda x:x[0],reverse=True):
            d.controller.state.update(pose);d.controller.targets=dict(d.controller.state);d.controller._apply()
            try:self.step(lock=True);break
            except ValueError:continue
        self.assertEqual(d.cargo.held,str(prim.GetPath()))
        before=world(prim);self.step(hoist=1)
        self.assertGreater(world(prim).ExtractTranslation()[2],before.ExtractTranslation()[2])

    def test_sts_can_lift_rtg_truck_cargo_with_hidden_cube_proxy(self):
        from rtg_driver_cargo import BODIES,TRUCK,TRUCK_OWNER,MANUAL,world
        d=self.driver;d.controller.set_spreader_size(20);point=d.contact()
        size=Gf.Vec3d(1.306287,2.475,.904353);centre=point-Gf.Vec3d(0,0,size[2]/2)
        path='/World/LiveContainers/STSReceiverTest'
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            cube=UsdGeom.Cube.Define(self.stage,path);cube.CreateSizeAttr(1);cube.AddTranslateOp().Set(centre);cube.AddScaleOp().Set(size)
            prim=cube.GetPrim();prim.CreateAttribute('live:row',Sdf.ValueTypeNames.Int).Set(1)
            prim.CreateAttribute('live:containerSizeFt',Sdf.ValueTypeNames.Int).Set(20)
            prim.CreateAttribute(TRUCK_OWNER,Sdf.ValueTypeNames.String).Set(TRUCK)
            prim.CreateAttribute(MANUAL,Sdf.ValueTypeNames.Bool).Set(True)
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set('invisible')
            model=UsdGeom.Xform.Define(self.stage,path+'_ImportedModel');model.AddTransformOp().Set(world(prim))
            UsdGeom.Cube.Define(self.stage,str(model.GetPath())+'/Mesh').CreateSizeAttr(1)
            self.stage.GetPrimAtPath('/World/LiveContainers').CreateRelationship(BODIES).AddTarget(path)
        d.cargo.refresh(True);self.assertIn(path,d.cargo.records)
        self.step(lock=True);before=world(prim);self.step(hoist=1)
        self.assertGreater(world(prim).ExtractTranslation()[2],before.ExtractTranslation()[2])

    def test_load_stops_on_cab_and_deck_then_transfers_to_truck(self):self.check_truck_handoff(20)

    def test_40ft_box_can_land_and_transfer_to_truck(self):self.check_truck_handoff(40)

    def check_truck_handoff(self,size_ft):
        from rtg_driver_cargo import world,TRUCK,TRUCK_OWNER,attr
        from truck_driver_runtime import TruckDriver
        d=self.driver;slot,size,_=self.ship_box(0,size_ft);self.step(lock=True)
        gantry=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(d.controller.path+'/Gantry'))
        direction=gantry.TransformDir(Gf.Vec3d(0,-1,0))
        d.controller.state['trolley']+=(-238-d.contact()[0])/direction[0]
        d.controller.targets=dict(d.controller.state);d.controller._apply();d.cargo.update()
        truck_view=View(self.stage);truck_view.camera_path='/Admin'
        truck=TruckDriver(self.stage,truck_view,'truck',{'expires':time.time()+1000},lambda:self.now[0],cab_stage=truck_view.stage)
        truck.private_view=truck_view;truck.engine.heading=math.pi/2
        try:
            with Usd.EditContext(self.stage,truck.layer):truck.apply()
            cab=next(p for p in Usd.PrimRange(truck.truck,Usd.TraverseInstanceProxies())
                     if p.IsA(UsdGeom.Boundable) and 'white_cab_body' in p.GetName())
            def bounds(prim):return UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(prim).ComputeAlignedRange()
            centre=world(slot.GetPrim()).ExtractTranslation();cab_centre=bounds(cab).GetMidpoint()
            truck.engine.x+=centre[0]-cab_centre[0];truck.engine.y+=centre[1]-cab_centre[1]
            with Usd.EditContext(self.stage,truck.layer):truck.apply()
            def move_above(z):
                c=d.controller;gantry=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(c.path+'/Gantry'))
                scale=gantry.TransformDir(Gf.Vec3d(0,0,1))[2]
                c.state['hoist']+=(z-d.contact()[2])/scale;c.targets=dict(c.state);c._apply();d.cargo.update()
            move_above(bounds(cab).GetMax()[2]+size[2]+.10)
            for _ in range(3):self.step(hoist=-1)
            self.assertIn('车头',d.state()['notice'])
            self.assertGreaterEqual(bounds(slot.GetPrim()).GetMin()[2],bounds(cab).GetMax()[2]-1e-5)
            self.assertTrue(any(str(slot.GetPath()) in path for _,path in truck.world.near(truck.engine.x,truck.engine.y)),
                            'a suspended box near the cab must remain a truck obstacle')
            move_above(bounds(cab).GetMax()[2]+size[2]+2)
            d.cargo.refresh(True);deck=d.cargo.decks[TRUCK];deck_centre=deck['pose'].ExtractTranslation();point=d.contact()
            truck.engine.x+=point[0]-deck_centre[0];truck.engine.y+=point[1]-deck_centre[1]
            with Usd.EditContext(self.stage,truck.layer):truck.apply()
            d.cargo.refresh(True);deck=d.cargo.decks[TRUCK]
            move_above(deck['pose'].ExtractTranslation()[2]+deck['size'][2]/2+size[2]+.10)
            for _ in range(3):self.step(hoist=-1)
            self.assertIn('集卡承载面',d.state()['notice'])
            truck.engine.speed=.3
            with self.assertRaisesRegex(ValueError,'停稳'):d.command('owner',{'lock':False})
            self.assertEqual(truck.engine.speed,.3);truck.engine.stop()
            self.step(lock=False);self.assertEqual(attr(slot.GetPrim(),TRUCK_OWNER),TRUCK)
            from rtg_driver_cargo import DriverCargo
            from rtg_live_control import RTGController
            rtg_cargo=DriverCargo(self.stage,RTGController(self.stage));rtg_cargo.refresh_contacts()
            self.assertIn(str(slot.GetPath()),rtg_cargo.geometry.records)
            self.assertEqual(rtg_cargo.geometry.records[str(slot.GetPath())]['size_ft'],size_ft)
            relative=world(slot.GetPrim())*world(truck.truck).GetInverse()
            d.close();truck.engine.y-=1
            with Usd.EditContext(self.stage,truck.layer):truck.apply()
            self.assertTrue(Gf.IsClose(world(slot.GetPrim()),relative*world(truck.truck),1e-6))
            self.assertFalse(any(str(slot.GetPath()) in path for _,path in truck.world.near(truck.engine.x,truck.engine.y)))
            if size_ft==20:
                from rtg_driver_runtime import RTGDriver
                rtg=RTGDriver(self.stage,'rtg',{'expires':time.time()+1000},lambda:self.now[0],View(self.stage))
                try:
                    rtg.controller.set_spreader_size(size_ft,smooth=False)
                    rtg.controller.set_positions_safe(gantry_m=10,trolley_m=6,hoist_m=8)
                    rtg.cargo.refresh_contacts();deck=rtg.cargo.geometry.decks[TRUCK]
                    foot=sum(rtg.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
                    deck_centre=deck['pose'].ExtractTranslation()
                    truck.engine.x+=foot[0]-deck_centre[0];truck.engine.y+=foot[1]-deck_centre[1]
                    with Usd.EditContext(self.stage,truck.layer):truck.apply()
                    box=bounds(slot.GetPrim());target=Gf.Vec3d(box.GetMidpoint()[0],box.GetMidpoint()[1],box.GetMax()[2])
                    methods=(rtg.controller.set_gantry_safe,rtg.controller.set_trolley_safe,rtg.controller.set_hoist_safe)
                    for axis,component in ((0,1),(1,0),(2,2)):
                        pos=rtg.controller.get_positions()[axis];point=sum(rtg.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
                        methods[axis](pos+.1);probe=sum(rtg.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
                        methods[axis](pos+(target[component]-point[component])*.1/(probe[component]-point[component]))
                    pos=rtg.controller.get_positions()[1];point=sum(rtg.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
                    rtg.controller.set_trolley_safe(pos+.1);probe=sum(rtg.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
                    rtg.controller.set_trolley_safe(pos+.01*.1/(probe[0]-point[0]))
                    rtg.command('rtg',{'lock':True});self.assertEqual(rtg.cargo.held,str(slot.GetPath()))
                    self.now[0]+=.1;rtg.command('rtg',{'hoist':1});rtg.tick(.1)
                    for _ in range(2):
                        self.now[0]+=.1;rtg.command('rtg',{'hoist':-1});rtg.tick(.1)
                    self.assertIn('集卡承载面',rtg.state()['notice'])
                    rtg.command('rtg',{'lock':False});self.assertFalse(rtg.cargo.locked)
                finally:rtg.close()
                # Return the loaded truck to the quay and pick the same
                # session-owned ship box back up with STS.
                truck.engine.x=-238;truck.engine.y=23
                with Usd.EditContext(self.stage,truck.layer):truck.apply()
                view=View(self.stage);self.driver=STSDriver(self.stage,'owner',self.session,lambda:self.now[0],view)
                from ship_transfer_plan import solve
                box=bounds(slot.GetPrim());point=Gf.Vec3d(box.GetMidpoint()[0],box.GetMidpoint()[1],box.GetMax()[2])
                pose=solve(self.stage,self.driver.controller,point)
                self.driver.controller.state.update(pose);self.driver.controller.targets=dict(self.driver.controller.state);self.driver.controller._apply()
                truck.engine.speed=.3
                with self.assertRaisesRegex(ValueError,'停稳'):self.driver.command('owner',{'lock':True})
                self.assertEqual(truck.engine.speed,.3);truck.engine.stop()
                self.driver.command('owner',{'lock':True})
                self.assertEqual(self.driver.cargo.held,str(slot.GetPath()))
        finally:truck.close()
    def test_all_three_operators_move_and_sts_release_is_scoped(self):
        from rtg_driver_runtime import RTGDriver,active_rtg_driver
        from truck_driver_runtime import TruckDriver,active_driver
        rtg=RTGDriver(self.stage,'rtg',{'expires':time.time()+1000},lambda:self.now[0],View(self.stage))
        truck_view=View(self.stage);truck_view.camera_path='/Admin'
        truck=TruckDriver(self.stage,truck_view,'truck',{'expires':time.time()+1000},lambda:self.now[0],cab_stage=truck_view.stage)
        truck.private_view=truck_view
        try:
            g=self.driver.controller.state['gantry'];r=rtg.controller.get_positions()[0];x=truck.engine.x
            for _ in range(5):
                self.now[0]+=.1
                self.driver.command('owner',{'gantry':1});rtg.command('rtg',{'gantry':1});truck.command('truck',{'throttle':1})
                self.driver.tick(.1);rtg.tick(.1);truck.tick(.1)
            self.assertGreater(self.driver.controller.state['gantry'],g)
            self.assertGreater(rtg.controller.get_positions()[0],r)
            self.assertLess(truck.engine.x,x)
            self.assertFalse(truck.world_dirty)
            self.driver.close();self.assertIs(active_rtg_driver(),rtg);self.assertIs(active_driver(),truck)
            from sts_driver_runtime import execute_sts_driver
            from smartport.agent_panel.scene_runtime import SceneRuntime
            runtime=SceneRuntime(ROOT);runtime.bind(self.stage);panel=SimpleNamespace(_runtime=runtime)
            with patch('role_viewport.DriverViewport',lambda stage,*args:View(stage)):
                execute_sts_driver(panel,self.stage,'acquire','owner',self.session,{})
            self.driver=active_sts_driver();self.assertIsNotNone(self.driver)
            self.assertIs(active_rtg_driver(),rtg);self.assertIs(active_driver(),truck)
            self.driver.close();runtime.close()
            x=truck.engine.x;self.now[0]+=.1;truck.command('truck',{'throttle':1});truck.tick(.1)
            self.assertLess(truck.engine.x,x)
        finally:rtg.close();truck.close()

if __name__=='__main__':unittest.main()
