"""Exercise RTG axes/private cameras on the real composed port, without Kit."""
import builtins,math,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pxr import Gf,Sdf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from rtg_driver_runtime import RTGDriver,active_rtg_driver,CAMERA
from rtg_live_control import RTGController,HOIST_PATH,TROLLEY_PATH
from role_viewport import private_stage
from rtg_driver_cargo import world,TRUCK_OWNER,TRUCK,attr


class PrivateView:
    def __init__(self,stage):self.stage=private_stage(stage);self.closed=False;self.ticks=0;self.camera_path='/AdminCamera'
    def tick(self):self.ticks+=1
    def ready(self):return not self.closed
    def close(self):self.closed=True


class RTGDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'));cls.root=cls.base.GetRootLayer()
    def setUp(self):
        self.stage=Usd.Stage.Open(self.root,Sdf.Layer.CreateAnonymous())
        RTGController(self.stage).set_positions_safe(gantry_m=10,trolley_m=6,hoist_m=8)
        self.now=[0.];self.session={'expires':time.time()+1000}
        self.view=PrivateView(self.stage)
        self.driver=RTGDriver(self.stage,'rtg-page',self.session,lambda:self.now[0],self.view)
    def tearDown(self):
        self.driver.close()
        truck=getattr(builtins,'_smartport_truck_driver',None)
        if truck:truck.close()
    def camera_matrix(self):return UsdGeom.XformCache().GetLocalToWorldTransform(self.view.stage.GetPrimAtPath(CAMERA))
    def step(self,**payload):
        self.now[0]+=.1;self.driver.command('rtg-page',payload);self.driver.tick(.1)
    def contact_box(self,size_ft=40):
        self.driver.controller.set_spreader_size(size_ft,smooth=False)
        points=self.driver.cargo.geometry._foot_points();centre=sum(points,Gf.Vec3d(0))/4
        dims=Gf.Vec3d(max(p[0] for p in points)-min(p[0] for p in points)+.316,
                      max(p[1] for p in points)-min(p[1] for p in points)+.34,.904353)
        path='/World/LiveContainers/DriverCargoTest'
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            cube=UsdGeom.Cube.Define(self.stage,path);cube.CreateSizeAttr(1)
            cube.AddTranslateOp().Set(centre-Gf.Vec3d(0,0,dims[2]/2));cube.AddScaleOp().Set(dims)
            cube.GetPrim().CreateAttribute('live:row',Sdf.ValueTypeNames.Int).Set(1)
            cube.GetPrim().CreateAttribute('live:containerSizeFt',Sdf.ValueTypeNames.Int).Set(size_ft)
            # A decorative sibling must follow as part of the same box.
            door=UsdGeom.Cube.Define(self.stage,path+'_Door');door.CreateSizeAttr(.1)
            door.AddTranslateOp().Set(centre-Gf.Vec3d(0,0,dims[2]/2))
        return cube.GetPrim(),door.GetPrim(),dims
    def test_camera_stays_vertical_follows_trolley_and_fov_range(self):
        root_before=self.root.ExportToString();m=self.camera_matrix();eye=m.ExtractTranslation()
        self.assertLess((m.TransformDir(Gf.Vec3d(0,0,-1))-Gf.Vec3d(0,0,-1)).GetLength(),1e-9)
        self.assertFalse(self.stage.GetPrimAtPath(CAMERA),'operator camera must remain private')
        self.assertEqual(self.driver.fov,140)
        self.step(hoist=1)
        self.assertLess((self.camera_matrix().ExtractTranslation()-eye).GetLength(),1e-8)
        self.step(trolley=1,gantry=1)
        next_eye=self.camera_matrix().ExtractTranslation()
        self.assertGreater((next_eye-eye).GetLength(),.01);self.assertAlmostEqual(next_eye[2],eye[2])
        for fov in (140,110,70,40):
            self.step(fov=fov)
            focal=self.driver.camera.GetFocalLengthAttr().Get()
            self.assertAlmostEqual(math.degrees(2*math.atan(36/(2*focal))),fov,places=4)
        for view in ('side','end','under'):
            for fov in (140,70,40):
                self.step(view=view,fov=fov);self.assertEqual(self.driver.state()['view'],view)
                focal=self.driver.camera.GetFocalLengthAttr().Get()
                self.assertAlmostEqual(math.degrees(2*math.atan(36/(2*focal))),fov,places=4)
        self.assertEqual(self.root.ExportToString(),root_before,'operator must not edit source layers')

    def test_spreader_stops_on_box_roof_and_can_raise(self):
        body,_,_=self.contact_box();d=self.driver
        before=d.controller.get_positions()[2]
        self.step(hoist=-1)
        self.assertAlmostEqual(d.controller.get_positions()[2],before,places=4)
        self.assertIn('接触箱顶',d.state()['notice'])
        self.step(hoist=1)
        self.assertAlmostEqual(d.controller.get_positions()[2],before+.5,places=5)
        self.step(hoist=-1)
        self.step(hoist=-1)
        self.assertAlmostEqual(d.controller.get_positions()[2],before,places=4)
        self.assertIn('接触箱顶',d.state()['notice'])

    def test_descending_cargo_stops_on_truck_cab(self):
        from truck_driver_runtime import TruckDriver
        body,_,dims=self.contact_box(20);self.step(lock=True);d=self.driver
        truck=TruckDriver(self.stage,PrivateView(self.stage),'truck-page',{'expires':time.time()+1000},lambda:self.now[0])
        cab=next(p for p in Usd.PrimRange(truck.truck,Usd.TraverseInstanceProxies())
                 if p.IsA(UsdGeom.Boundable) and 'white_cab_body' in p.GetName())
        def cab_bounds():return UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(cab).ComputeAlignedRange()
        centre=world(body).ExtractTranslation();cab_centre=cab_bounds().GetMidpoint()
        truck.engine.x+=centre[0]-cab_centre[0];truck.engine.y+=centre[1]-cab_centre[1]
        with Usd.EditContext(self.stage,truck.layer):truck.apply()
        roof=sum(d.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
        target=cab_bounds().GetMax()[2]+dims[2]+.01
        h=d.controller.get_positions()[2];d.controller.set_hoist_safe(h+.1)
        probe=sum(d.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
        d.controller.set_hoist_safe(h+(target-roof[2])*.1/(probe[2]-roof[2]));d.cargo.update()
        for _ in range(5):self.step(hoist=-1)
        self.assertIn('车头',d.state()['notice'])
        self.assertGreaterEqual(world(body).ExtractTranslation()[2]-dims[2]/2,cab_bounds().GetMax()[2]-1e-5)
        truck.close()
    def test_axes_roll_deadman_sequence_and_release(self):
        d=self.driver;before=d.controller.get_positions()
        # A rigid tilt preserves dimensions and rotates about the lock-foot centre.
        prim=self.stage.GetPrimAtPath(HOIST_PATH);cache=UsdGeom.XformCache()
        points=[d.trim.pivot,d.trim.pivot+Gf.Vec3d(1,0,0),d.trim.pivot+Gf.Vec3d(0,1,0)]
        original=[cache.GetLocalToWorldTransform(prim).Transform(p) for p in points]
        self.step(roll=1)
        tilted=[UsdGeom.XformCache().GetLocalToWorldTransform(prim).Transform(p) for p in points]
        self.assertLess((tilted[0]-original[0]).GetLength(),1e-6)
        for i in (1,2):self.assertAlmostEqual((original[i]-original[0]).GetLength(),(tilted[i]-tilted[0]).GetLength(),places=6)
        self.step(gantry=1,trolley=1,hoist=1)
        self.assertTrue(all(a>b for a,b in zip(d.controller.get_positions(),before)))
        d.command('rtg-page',{'seq':100,'brake':True});d.command('rtg-page',{'seq':99,'gantry':1})
        self.assertEqual(d.axes['gantry'],0)
        d.command('rtg-page',{'seq':101,'gantry':1});self.now[0]+=.5
        stopped=d.controller.get_positions();d.tick(.1);self.assertEqual(d.controller.get_positions(),stopped)
        for payload in ({'gantry':float('nan')},{'fov':39},{'fov':141},{'roll':True},{'view':'admin'},{'seq':1.2}):
            with self.assertRaises(ValueError):d.command('rtg-page',payload)
        with self.assertRaises(ValueError):d.command('other-page',{})
        with self.assertRaises(ValueError):RTGDriver(self.stage,'other',self.session,view=self.view)
        self.session['driver_released']=True;d.tick(.1)
        self.assertIsNone(active_rtg_driver());self.assertTrue(self.view.closed)
    def test_truck_and_rtg_move_and_release_independently(self):
        from truck_driver_runtime import TruckDriver,active_driver
        truck_view=PrivateView(self.stage)
        truck=TruckDriver(self.stage,truck_view,'truck-page',{'expires':time.time()+1000},lambda:self.now[0],cab_stage=truck_view.stage)
        truck.private_view=truck_view
        start_x=truck.engine.x;start_g=self.driver.controller.get_positions()[0]
        for _ in range(8):
            self.now[0]+=.1
            truck.command('truck-page',{'throttle':1})
            self.driver.command('rtg-page',{'gantry':1})
            truck.tick(.1);self.driver.tick(.1)
        self.assertLess(truck.engine.x,start_x)
        self.assertGreater(self.driver.controller.get_positions()[0],start_g)
        self.assertFalse(truck.world_dirty,'RTG movement must not rebuild the entire truck collision index')
        self.assertNotEqual(truck_view.camera_path,self.view.camera_path)
        self.driver.close();self.assertIs(active_driver(),truck);self.assertFalse(truck_view.closed)
        before=truck.engine.x;self.now[0]+=.1;truck.command('truck-page',{'throttle':1});truck.tick(.1)
        self.assertLess(truck.engine.x,before)
        truck.close()

    def test_acquire_allows_both_orders_but_blocks_automatic_rtg_takeover(self):
        from rtg_driver_runtime import execute_rtg_driver
        from truck_driver_runtime import execute_driver,active_driver
        from smartport.agent_panel.scene_runtime import SceneRuntime
        runtime=SceneRuntime(ROOT);runtime.bind(self.stage);panel=SimpleNamespace(_runtime=runtime)
        # The real acquisition guards must allow truck-after-RTG.
        with patch('role_viewport.DriverViewport',lambda stage,camera:PrivateView(stage)):
            execute_driver(panel,self.stage,None,'acquire','truck-page',{'expires':time.time()+1000},{})
        truck=active_driver();self.assertIsNotNone(truck)
        self.driver.close()
        # And RTG-after-truck, without a global stop or release.
        with patch('rtg_driver_runtime.RTGDriver',lambda stage,owner,session:RTGDriver(stage,owner,session,view=PrivateView(stage))):
            execute_rtg_driver(panel,self.stage,'acquire','rtg-new',{'expires':time.time()+1000},{})
        self.driver=active_rtg_driver();self.assertIsNotNone(self.driver);self.assertIs(active_driver(),truck)
        with self.assertRaises(ValueError):runtime._guard_manual('rtg',allow_truck_driver=True)
        with self.assertRaises(ValueError):runtime.rtg_move({'gantry_m':20})
        execute_rtg_driver(panel,self.stage,'release','rtg-new',{},{});self.assertIs(active_driver(),truck)
        truck.close();runtime.close()

    def test_rotation_zero_and_continuous_size_change_with_stop(self):
        d=self.driver
        self.step(yaw=1,roll=1)
        self.assertGreater(d.state()['yaw_deg'],0);self.assertGreater(d.roll,0)
        self.step(zero=True)
        self.assertLess(d.state()['yaw_deg'],.1)
        for _ in range(12):self.step()
        self.assertAlmostEqual(d.state()['yaw_deg'],0);self.assertAlmostEqual(d.roll,0)
        self.assertFalse(d.returning)
        self.step(size_ft=20)
        partial=d.state()['spreader_size_ft'];self.assertTrue(20<partial<40);self.assertFalse(d.spreader_ready())
        with self.assertRaisesRegex(ValueError,'伸缩'):d.command('rtg-page',{'lock':True})
        self.step(brake=True);stopped=d.state()['spreader_size_ft']
        for _ in range(4):self.step()
        self.assertEqual(d.state()['spreader_size_ft'],stopped)
        self.step(size_ft=20)
        for _ in range(100):
            if d.spreader_ready():break
            self.step()
        self.assertTrue(d.spreader_ready());self.assertAlmostEqual(d.state()['spreader_size_ft'],20)
        self.step(size_ft=40)
        for _ in range(100):
            if d.spreader_ready():break
            self.step()
        self.assertAlmostEqual(d.state()['spreader_size_ft'],40)
        for payload in ({'size_ft':30},{'size_ft':20.},{'lock':1},{'zero':'yes'},{'yaw':float('nan')},{'lock':True,'size_ft':20}):
            with self.assertRaises(ValueError):d.command('rtg-page',payload)

    def test_measured_lock_lift_details_and_landed_unlock(self):
        body,door,dims=self.contact_box();d=self.driver
        start=world(body);door_start=world(door)
        self.step(lock=True);self.assertTrue(d.cargo.locked)
        self.assertLess((world(body).ExtractTranslation()-start.ExtractTranslation()).GetLength(),1e-8,'locking must not snap cargo')
        g=d.controller.get_positions()[0];self.step(gantry=1,hoist=1,yaw=1)
        self.assertEqual(d.controller.get_positions()[0],g,'gantry cannot travel with a locked box')
        self.assertGreater(world(body).ExtractTranslation()[2],start.ExtractTranslation()[2])
        delta=start.GetInverse()*world(body)
        self.assertTrue(Gf.IsClose(world(door),door_start*delta,1e-6))
        for i in range(3):
            unit=Gf.Vec3d(*(1 if i==j else 0 for j in range(3)))
            self.assertAlmostEqual(world(body).TransformDir(unit).GetLength(),start.TransformDir(unit).GetLength(),places=6)
        with self.assertRaisesRegex(ValueError,'落稳'):d.command('rtg-page',{'lock':False})
        with self.assertRaisesRegex(ValueError,'尺型'):d.command('rtg-page',{'size_ft':20})
        self.step(zero=True)
        for _ in range(12):self.step()
        centre=world(body).ExtractTranslation()
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            support=UsdGeom.Cube.Define(self.stage,'/World/LiveContainers/DriverSupport');support.CreateSizeAttr(1)
            support.AddTranslateOp().Set(centre-Gf.Vec3d(0,0,dims[2]))
            support.AddScaleOp().Set(dims+Gf.Vec3d(.2,.2,0))
            support.GetPrim().CreateAttribute('live:row',Sdf.ValueTypeNames.Int).Set(0)
        self.step(lock=False);self.assertFalse(d.cargo.locked)
        landed=world(body);self.step(hoist=1);self.assertTrue(Gf.IsClose(world(body),landed,1e-8))

    def test_wrong_size_and_misaligned_or_buried_box_cannot_lock(self):
        body,door,dims=self.contact_box(20);d=self.driver
        d.controller.set_spreader_size(40,smooth=False)
        with self.assertRaises(ValueError):d.command('rtg-page',{'lock':True})
        d.controller.set_spreader_size(20,smooth=False)
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):body.GetAttribute('xformOp:translate').Set(world(body).ExtractTranslation()+Gf.Vec3d(.1,0,0))
        with self.assertRaises(ValueError):d.command('rtg-page',{'lock':True})
        self.assertFalse(d.cargo.locked)
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            centre=body.GetAttribute('xformOp:translate').Get()-Gf.Vec3d(.1,0,0)
            body.GetAttribute('xformOp:translate').Set(centre)
            cover=UsdGeom.Cube.Define(self.stage,'/World/LiveContainers/DriverCover');cover.CreateSizeAttr(1)
            cover.AddTranslateOp().Set(centre+Gf.Vec3d(0,0,dims[2]/2+.15))
            cover.AddScaleOp().Set(Gf.Vec3d(dims[0],dims[1],.3))
            cover.GetPrim().CreateAttribute('live:row',Sdf.ValueTypeNames.Int).Set(1)
        with self.assertRaises(ValueError):d.command('rtg-page',{'lock':True})

    def test_locked_cargo_survives_logout_and_can_be_resumed(self):
        body,_,_=self.contact_box();d=self.driver;self.step(lock=True);self.step(hoist=1)
        held=world(body);d.close()
        self.assertTrue(Gf.IsClose(world(body),held,1e-8))
        from smartport.agent_panel.scene_runtime import SceneRuntime
        runtime=SceneRuntime(ROOT);runtime.bind(self.stage)
        with self.assertRaisesRegex(ValueError,'闭锁'):runtime._guard_manual('rtg')
        runtime.close()
        self.view=PrivateView(self.stage)
        self.driver=RTGDriver(self.stage,'rtg-page',self.session,lambda:self.now[0],self.view)
        self.assertTrue(self.driver.cargo.locked);self.step(hoist=1)
        self.assertGreater(world(body).ExtractTranslation()[2],held.ExtractTranslation()[2])

    def test_truck_handoff_follows_truck_after_rtg_logout_without_self_collision(self):
        self.check_truck_handoff(20,0)

    def test_40ft_handoff_reports_misalignment_and_accepts_corrected_position(self):
        self.check_truck_handoff(40,-.30)

    def check_truck_handoff(self,size_ft,offset):
        from truck_driver_runtime import TruckDriver
        from ship_transfer_plan import PARK
        body,door,dims=self.contact_box(size_ft);self.step(lock=True);d=self.driver
        truck_view=PrivateView(self.stage)
        truck=TruckDriver(self.stage,truck_view,'truck-page',{'expires':time.time()+1000},lambda:self.now[0],cab_stage=truck_view.stage)
        truck.private_view=truck_view;truck.engine.heading=math.pi/2
        with Usd.EditContext(self.stage,truck.layer):truck.apply()
        d.cargo.refresh_contacts();deck=d.cargo.geometry.decks[TRUCK]
        roof=sum(d.cargo.geometry._foot_points(),Gf.Vec3d(0))/4;deck_centre=deck['pose'].ExtractTranslation()
        truck.engine.x+=roof[0]-deck_centre[0];truck.engine.y+=roof[1]-deck_centre[1]+offset
        with Usd.EditContext(self.stage,truck.layer):truck.apply()
        d.cargo.refresh_contacts();deck=d.cargo.geometry.decks[TRUCK]
        target=deck['pose'].ExtractTranslation()[2]+deck['size'][2]/2+dims[2]
        h=d.controller.get_positions()[2];d.controller.set_hoist_safe(h+.1)
        next_roof=sum(d.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
        d.controller.set_hoist_safe(h+(target-roof[2])*.1/(next_roof[2]-roof[2]));d.cargo.update()
        landing=d.controller.get_positions()[2]
        self.step(hoist=-1)
        self.assertAlmostEqual(d.controller.get_positions()[2],landing,places=4)
        self.assertIn('集卡承载面',d.state()['notice'])
        if offset:
            with self.assertRaisesRegex(ValueError,'承重点 2/4.*沿车身.*偏出'):d.command('rtg-page',{'lock':False})
            self.assertTrue(d.cargo.locked)
            truck.engine.y-=offset
            with Usd.EditContext(self.stage,truck.layer):truck.apply()
        truck.engine.speed=.3
        with self.assertRaisesRegex(ValueError,'停稳'):d.command('rtg-page',{'lock':False})
        self.assertEqual(truck.engine.speed,.3,'RTG must not stop the truck to take a handoff')
        truck.engine.stop()
        self.step(lock=False);self.assertEqual(attr(body,TRUCK_OWNER),TRUCK)
        relative=world(body)*world(truck.truck).GetInverse();d.close()
        old=world(body)
        for _ in range(4):
            self.now[0]+=.1;truck.command('truck-page',{'throttle':1});truck.tick(.1)
        self.assertFalse(Gf.IsClose(world(body),old,1e-8))
        self.assertTrue(Gf.IsClose(world(body),relative*world(truck.truck),1e-6))
        self.assertFalse(truck.world_dirty)
        self.assertFalse(any('DriverCargoTest' in path for _,path in truck.world.near(truck.engine.x,truck.engine.y)))
        truck.close();self.assertEqual(attr(body,TRUCK_OWNER),PARK)

    def test_real_yard_stack_top_and_details_can_be_locked_and_lifted(self):
        d=self.driver;root=self.stage.GetPrimAtPath('/World/LiveContainers/YardInventory/Bay_3C_005')
        body=max((p for p in root.GetChildren() if p.IsA(UsdGeom.Cube) and p.GetName().startswith('R01_') and p.HasAttribute('live:row')),
                 key=lambda p:p.GetAttribute('live:tier').Get())
        record=d.cargo.geometry._read_record(body);d.controller.set_spreader_size(record['size_ft'],smooth=False)
        target=record['pose'].ExtractTranslation()+Gf.Vec3d(0,0,record['size'][2]/2)
        methods=(d.controller.set_gantry_safe,d.controller.set_trolley_safe,d.controller.set_hoist_safe)
        for axis,component in ((0,1),(1,0),(2,2)):
            position=d.controller.get_positions()[axis];point=sum(d.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
            methods[axis](position+.1);probe=sum(d.cargo.geometry._foot_points(),Gf.Vec3d(0))/4
            methods[axis](position+(target[component]-point[component])*.1/(probe[component]-point[component]))
        from rtg_driver_cargo import members
        parts=members(self.stage,str(body.GetPath()));self.assertGreater(len(parts),1)
        before={str(p.GetPath()):world(p) for p in parts}
        start=world(body);self.step(lock=True);self.assertEqual(d.cargo.held,str(body.GetPath()))
        self.step(hoist=1);self.assertGreater(world(body).ExtractTranslation()[2],start.ExtractTranslation()[2])
        delta=start.GetInverse()*world(body)
        for part in parts:self.assertTrue(Gf.IsClose(world(part),before[str(part.GetPath())]*delta,1e-6))

    def test_hidden_proxy_and_imported_model_follow_as_one_box(self):
        body,_,_=self.contact_box()
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            model=UsdGeom.Xform.Define(self.stage,str(body.GetPath())+'_ImportedModel')
            model.AddTransformOp().Set(world(body));UsdGeom.Cube.Define(self.stage,str(model.GetPath())+'/Mesh').CreateSizeAttr(1)
            UsdGeom.Imageable(body).CreateVisibilityAttr().Set('invisible')
        before=world(body);model_before=world(model.GetPrim())
        self.step(lock=True);self.step(hoist=1,yaw=1,roll=1)
        self.assertTrue(Gf.IsClose(world(model.GetPrim()),model_before*before.GetInverse()*world(body),1e-6))


if __name__=='__main__':unittest.main()
