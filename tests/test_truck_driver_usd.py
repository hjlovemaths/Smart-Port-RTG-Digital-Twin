"""Driver integration against the real composed truck, yard and causeways."""
import sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from pxr import Gf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from truck_driver_runtime import TruckDriver,SceneObstacles,active_driver,CAMERA
from ship_transfer_plan import box,PARK


class DriverUsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.stage=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    def tearDown(self):
        driver=active_driver()
        if driver:driver.close()

    def test_driving_camera_deadman_single_owner_water_reset_and_exact_cleanup(self):
        s=self.stage;before=s.GetSessionLayer().ExportToString();root_before=s.GetRootLayer().ExportToString()
        now=[0.];viewport=SimpleNamespace(camera_path='/OmniverseKit_Persp')
        driver=TruckDriver(s,viewport,'owner',{'expires':time.time()+1000},lambda:now[0])
        self.assertIs(active_driver(s),driver)
        self.assertEqual(viewport.camera_path,CAMERA)
        transforms=UsdGeom.XformCache()
        eye=transforms.GetLocalToWorldTransform(s.GetPrimAtPath(CAMERA)).ExtractTranslation()
        wheel=transforms.GetLocalToWorldTransform(s.GetPrimAtPath('/World/TruckDriverDemo/CabInterior/SteeringWheel')).ExtractTranslation()
        self.assertAlmostEqual(eye[1],wheel[1],places=5)
        cab_body=next(p for p in Usd.PrimRange(driver.truck,Usd.TraverseInstanceProxies()) if 'white_cab_body' in p.GetName())
        self.assertEqual(UsdGeom.Imageable(cab_body).ComputeVisibility(),'invisible')
        self.assertEqual(UsdGeom.Imageable(s.GetPrimAtPath(PARK)).ComputeVisibility(),'invisible')
        self.assertAlmostEqual(box(s,driver.truck.GetPath()).GetSize()[0],6.4629497,places=4)
        with self.assertRaises(ValueError):TruckDriver(s,viewport,'second',{'expires':time.time()+1000})
        with self.assertRaises(ValueError):driver.command('second',{'throttle':1})
        original=Gf.Vec3d(driver.engine.x,driver.engine.y,0)
        for _ in range(10):
            now[0]+=.1;driver.command('owner',{'throttle':1,'view':'follow'});driver.tick(.1)
        self.assertLess(driver.engine.x,original[0]);self.assertEqual(driver.view,'follow')
        self.assertEqual(UsdGeom.Imageable(cab_body).ComputeVisibility(),'inherited')
        driver.command('owner',{'seq':100,'brake':True})
        driver.command('owner',{'seq':99,'throttle':1})
        self.assertTrue(driver.engine.brake);self.assertEqual(driver.engine.throttle,0)
        before_deadman=driver.engine.x;now[0]+=.5;driver.tick(.1)
        self.assertEqual(driver.engine.speed,0);self.assertEqual(driver.engine.x,before_deadman)
        driver.engine.x=-150;driver.engine.y=50
        now[0]+=.1;driver.command('owner',{});driver.tick(.1)
        self.assertEqual(driver.engine.event,'water_reset');self.assertEqual(driver.engine.reset_count,1)
        self.assertEqual((driver.engine.x,driver.engine.y,driver.engine.heading),driver.engine.spawn)
        driver.close();driver.close()
        self.assertIsNone(active_driver());self.assertEqual(viewport.camera_path,'/OmniverseKit_Persp')
        self.assertEqual(before,s.GetSessionLayer().ExportToString());self.assertEqual(root_before,s.GetRootLayer().ExportToString())

    def test_session_expiry_releases_vehicle_and_camera(self):
        session={'expires':time.time()+1000};viewport=SimpleNamespace(camera_path='/OmniverseKit_Persp')
        driver=TruckDriver(self.stage,viewport,'owner',session)
        session['driver_released']=True;driver.tick(.1)
        self.assertTrue(driver.closed);self.assertIsNone(active_driver());self.assertEqual(viewport.camera_path,'/OmniverseKit_Persp')

    def test_collision_index_can_build_across_frames_with_vehicle_stopped(self):
        now=[0.]
        driver=TruckDriver(self.stage,SimpleNamespace(camera_path='/OmniverseKit_Persp'),'owner',
                           {'expires':time.time()+1000},lambda:now[0],deferred_world=True)
        self.assertFalse(driver.world.ready)
        start=(driver.engine.x,driver.engine.y)
        driver.command('owner',{'throttle':1});driver.tick(.1)
        self.assertEqual((driver.engine.x,driver.engine.y),start)
        self.assertFalse(driver.state()['stream_ready'])
        steps=0
        while not driver.world.ready:
            driver.world.advance(.003);steps+=1
        self.assertGreater(steps,1)
        while not driver.driving_ready:
            driver.command('owner',{});driver.tick(.1)
        self.assertTrue(driver.state()['stream_ready'])
        # Renderer metadata must not invalidate and rebuild physical geometry.
        UsdGeom.Xform.Define(self.stage,'/Render/DriverMetadata')
        self.assertFalse(driver.world_dirty)
        driver.close()

    def test_driving_across_nine_regions_does_not_stop_for_loading(self):
        import math
        now=[0.]
        driver=TruckDriver(self.stage,SimpleNamespace(camera_path='/OmniverseKit_Persp'),'owner',
                           {'expires':time.time()+1000},lambda:now[0],deferred_world=True)
        driver.engine.x=116;driver.engine.y=120;driver.engine.heading=-math.pi/2
        driver.world=SceneObstacles(self.stage,center=(116,120));driver.next_obstacles=0
        while not driver.driving_ready:
            now[0]+=.2;driver.command('owner',{});driver.tick(.1)
        driver.engine.speed=8;swaps=0;previous=driver.world
        for _ in range(400):
            # Five updates per second stresses preparation time relative to
            # travel, instead of finishing each new index in a tight loop.
            now[0]+=.2;driver.command('owner',{'throttle':1});driver.tick(.1)
            self.assertIsNone(driver.engine.hit)
            self.assertGreater(driver.engine.speed,0,'loading interrupted continuous driving')
            if previous is not driver.world:swaps+=1;previous=driver.world
        self.assertGreaterEqual(swaps,5)
        self.assertGreater(driver.engine.y,439)
        driver.close()

    def test_stalled_prefetch_keeps_turns_and_reverse_moving_but_stops_at_real_wall(self):
        import math,unittest.mock
        from truck_driving import TruckDriving
        stage=Usd.Stage.CreateInMemory()
        body=UsdGeom.Cube.Define(stage,PARK+'/Body');body.CreateSizeAttr(1)
        body.AddScaleOp().Set(Gf.Vec3f(6.4,1.3,1))
        body.AddTranslateOp().Set(Gf.Vec3d(0,0,.6))
        wall=UsdGeom.Cube.Define(stage,'/World/SolidWall');wall.CreateSizeAttr(1)
        wall.AddTranslateOp().Set(Gf.Vec3d(75,180,.7));wall.AddScaleOp().Set(Gf.Vec3f(.5,50,1))
        now=[0.];driver=TruckDriver(stage,SimpleNamespace(camera_path='/OmniverseKit_Persp'),'owner',
                                   {'expires':time.time()+1000},lambda:now[0])
        driver.engine.x=30;driver.engine.y=180;driver.engine.heading=-math.pi/2
        expected=TruckDriving((30,180,-math.pi/2),driver.engine.half_length,driver.engine.half_width)
        driver.world=SceneObstacles(stage,center=(30,180),radius=10,snap=False)
        pending=SceneObstacles(stage,center=(30,300),deferred=True);pending.ready=False
        driver.rebuilding_world=pending
        with unittest.mock.patch.object(pending,'advance',lambda *args,**kwargs:None):
            for throttle,steer,count in ((1,0,70),(1,1,120),(-1,-1,100),(1,0,100)):
                for _ in range(count):
                    now[0]+=.5;driver.command('owner',{'throttle':throttle,'steer':steer});driver.tick(.1)
                    expected.controls(throttle,steer);expected.step(.1,[],driver.world.supported)
                    self.assertAlmostEqual(driver.engine.x,expected.x)
                    self.assertAlmostEqual(driver.engine.y,expected.y)
                    self.assertAlmostEqual(driver.engine.speed,expected.speed)
            self.assertGreater(driver.local_checks,3)
            # Put the vehicle on an unprepared approach to a physical obstacle.
            driver.engine.x=70;driver.engine.y=180;driver.engine.heading=math.pi;driver.engine.speed=3
            for _ in range(30):
                now[0]+=.5;driver.command('owner',{'throttle':1});driver.tick(.1)
                if driver.engine.hit:break
            self.assertIn('SolidWall',driver.engine.hit or '')
            self.assertLess(driver.engine.x,72)
        stopped=driver.engine.x
        wall.GetPrim().GetAttribute('xformOp:translate').Set(Gf.Vec3d(85,180,.7))
        self.assertTrue(driver.world_dirty)
        now[0]+=3;driver.command('owner',{'throttle':1});driver.tick(.1)
        self.assertGreater(driver.engine.x,stopped,'the removed wall must not remain in cached regions')
        self.assertIsNone(driver.engine.hit)
        helper=UsdGeom.Xform.Define(stage,'/World/RTG_LIDAR_POSITION_MARKERS/Test')
        helper.AddTranslateOp().Set(Gf.Vec3d(1,2,3))
        self.assertFalse(driver.world_dirty,'visual marker updates must not restart collision indexing')
        driver.close()

    def test_regional_boot_allows_admin_navigation_and_checks_locally_at_unready_region(self):
        from smartport.agent_panel.viewport_navigation import ViewportNavigation,CAMERA_PATH
        now=[0.];admin=SimpleNamespace(camera_path='/OmniverseKit_Persp')
        navigator=ViewportNavigation();navigator._context=lambda:(self.stage,admin)
        driver=TruckDriver(self.stage,SimpleNamespace(camera_path='/OmniverseKit_Persp'),'owner',
                           {'expires':time.time()+1000},lambda:now[0],deferred_world=True)
        steps=0;poses=[]
        while not driver.world.ready:
            # Exercise the real administrator camera adapter between indexed
            # chunks, rather than completing the builder in one tight loop.
            navigator.execute({'orbit_x':1})
            poses.append(self.stage.GetPrimAtPath(CAMERA_PATH).GetAttribute('xformOp:transform').Get())
            now[0]+=.2;driver.command('owner',{});driver.tick(.1);steps+=1
            self.assertLess(steps,150,'birth-region startup must not require hundreds of frames')
        self.assertGreater(len(set(str(p) for p in poses)),1)
        self.assertEqual(admin.camera_path,CAMERA_PATH)
        self.assertFalse(driver.world_dirty,'admin camera navigation must not restart collision loading')
        while not driver.driving_ready:
            now[0]+=.2;driver.command('owner',{});driver.tick(.1)
        import math
        driver.world=SceneObstacles(self.stage,center=(116,150),radius=10,snap=False)
        driver.checked_regions=[]
        driver.rebuilding_world=SceneObstacles(self.stage,center=(116,170),radius=32,deferred=True,dynamic_source=driver.world)
        driver.engine.x=116;driver.engine.y=154;driver.engine.heading=-math.pi/2;driver.engine.speed=3
        start=(driver.engine.x,driver.engine.y)
        # The big tile is deliberately stalled; the narrow exact check must
        # still complete and allow driving without disabling real collisions.
        import unittest.mock
        with unittest.mock.patch.object(driver.rebuilding_world,'advance',lambda *args,**kwargs:None):
            driver.command('owner',{'throttle':1});driver.tick(.1)
        self.assertGreater(driver.engine.y,start[1])
        self.assertGreater(driver.engine.speed,0)
        self.assertEqual(driver.local_checks,1)
        self.assertIsNone(driver.engine.hit)
        self.assertIsNotNone(driver.rebuilding_world)
        self.assertNotIn('保持制动',driver.state()['notice'])
        driver.close()

    def test_private_driver_view_preserves_admin_camera_and_exterior(self):
        from role_viewport import private_stage
        view=private_stage(self.stage)
        admin=SimpleNamespace(camera_path='/World/RTGViewportFollowCamera')
        driver_view=SimpleNamespace(camera_path=CAMERA)
        driver=TruckDriver(self.stage,driver_view,'owner',{'expires':time.time()+1000},cab_stage=view)
        path=next(str(p.GetPath()) for p in Usd.PrimRange(driver.truck,Usd.TraverseInstanceProxies()) if 'white_cab_body' in p.GetName())
        self.assertEqual(UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).ComputeVisibility(),'inherited')
        self.assertEqual(UsdGeom.Imageable(view.GetPrimAtPath(path)).ComputeVisibility(),'invisible')
        self.assertFalse(self.stage.GetPrimAtPath('/World/TruckDriverDemo/CabInterior'))
        self.assertTrue(view.GetPrimAtPath('/World/TruckDriverDemo/CabInterior'))
        driver.command('owner',{'view':'follow','throttle':1});driver.tick(.1)
        self.assertEqual(admin.camera_path,'/World/RTGViewportFollowCamera')
        self.assertEqual(driver_view.camera_path,CAMERA)
        # Both render contexts see authoritative vehicle motion through the
        # shared source session, while visibility edits remain driver-local.
        self.assertEqual(box(view,driver.truck.GetPath()).GetMidpoint(),box(self.stage,driver.truck.GetPath()).GetMidpoint())
        admin.camera_path='/World/AdminInspectionCamera'
        driver.command('owner',{'view':'cabin'});driver.tick(.1);driver.close()
        self.assertEqual(admin.camera_path,'/World/AdminInspectionCamera')

    def test_regional_index_keeps_yard_cargo_and_rtg_wheels_solid(self):
        from truck_driving import TruckDriving,intersects
        from yard_to_ship_plan import plan_yard_to_ship
        plan=plan_yard_to_ship(self.stage,'3C/005',1,_yard_only=True)
        x,y,_=plan['source_pick_m']
        cargo=SceneObstacles(self.stage,center=(x,y))
        body=TruckDriving((x,y,0),3.23,.67).footprint()
        self.assertTrue(any('YardInventory' in path and intersects(body,rect) for rect,path in cargo.near(x,y)))
        lane=SceneObstacles(self.stage,center=(-3.75,263))
        body=TruckDriving((-3.75,263,1.57079632679),3.23,.67).footprint()
        self.assertFalse(any(intersects(body,rect) for rect,path in lane.near(-3.75,263)))
        body=TruckDriving((-6.3,263,1.57079632679),3.23,.67).footprint()
        self.assertTrue(any('RTG_PRIMARY_DYNAMIC' in path and intersects(body,rect) for rect,path in lane.near(-6.3,263)))

    def test_actual_yard_container_is_solid_and_causeway_gap_is_water(self):
        world=SceneObstacles(self.stage)
        self.assertTrue(world.supported(138,67));self.assertTrue(world.supported(-150,126.05))
        self.assertFalse(world.supported(-150,50));self.assertFalse(world.supported(-260,50))
        from yard_to_ship_plan import plan_yard_to_ship
        p=plan_yard_to_ship(self.stage,'3C/005',1,_yard_only=True)
        center=Gf.Vec3d(p['source_pick_m'])
        boxes=world.near(center[0],center[1])
        self.assertTrue(any('YardInventory' in path for rect,path in boxes))
        self.assertAlmostEqual(world.ground_z(-150,126.05),.18,places=5)
        # A merged rail mesh must not make the open bridge lane solid.
        from truck_driving import TruckDriving,intersects
        truck=TruckDriving((-150,126.05,0),3.23,.67)
        self.assertFalse(any(intersects(truck.footprint(),rect) for rect,path in world.near(truck.x,truck.y)))

    def test_camera_markers_do_not_block_reverse_and_all_four_bridge_mouths_are_open(self):
        from truck_driving import TruckDriving
        from truck_driver_runtime import helper_geometry
        from rtg_live_control import GANTRY_PATH
        world=SceneObstacles(self.stage)
        marker=self.stage.GetPrimAtPath(GANTRY_PATH+'/RTG_CAMERA_MARKERS_ROOT/C19/C19_green_sphere_mesh')
        self.assertTrue(marker);self.assertTrue(helper_geometry(marker))
        truck=TruckDriving((-2.61,258.7,0),3.2315,.67);truck.controls(-1)
        nearby=world.near(truck.x,truck.y)
        self.assertFalse(any('CAMERA_MARKERS_ROOT' in path for rect,path in nearby))
        truck.step(.1,nearby,world.supported)
        self.assertIsNone(truck.hit);self.assertGreater(truck.x,-2.61)
        edge=self.stage.GetPrimAtPath('/World/SMART_PORT_OFFSHORE_QUAY/Edges/LandsideEdge')
        self.assertEqual(UsdGeom.Imageable(edge).ComputeVisibility(),'invisible')
        import math
        for y in (-5.375,126.05,256.3,386.55):
            for start,heading in ((-197,0),(-207,math.pi)):
                truck=TruckDriving((start,y,heading),3.2315,.67);truck.speed=3;truck.controls(1)
                for _ in range(60):
                    truck.step(.1,world.near(truck.x,truck.y),world.supported)
                    self.assertIsNone(truck.hit,(y,start,truck.state()))
                    self.assertNotEqual(truck.event,'water_reset',(y,start))
                    if abs(truck.x-start)>9:break
                self.assertGreater(abs(truck.x-start),9)
        # Opening the entrances must not disable the real side barriers.
        truck=TruckDriving((-150,125,-math.pi/2),3.2315,.67);truck.speed=3;truck.controls(1)
        for _ in range(60):
            truck.step(.1,world.near(truck.x,truck.y),world.supported)
            if truck.hit:break
        self.assertIsNotNone(truck.hit);self.assertIn('Causeway',truck.hit)

    def test_rtg_lane_passes_below_braces_but_wheel_bogies_remain_solid(self):
        import math
        from truck_driving import TruckDriving
        world=SceneObstacles(self.stage)
        # These small diagonal meshes previously blocked the entire lane at
        # y=260.19/265.49 by projecting their overhead ends onto the ground.
        for x in (-4.5,-3.75,-3.):
            for start,heading,target in ((250,-math.pi/2,275),(275,math.pi/2,250)):
                truck=TruckDriving((x,start,heading),3.2315,.67);truck.speed=3;truck.controls(1)
                for _ in range(80):
                    truck.step(.1,world.near(truck.x,truck.y),world.supported)
                    self.assertIsNone(truck.hit,(x,start,truck.state()))
                    self.assertNotEqual(truck.event,'water_reset')
                    if (truck.y-target)*(target-start)>=0:break
                self.assertGreaterEqual((truck.y-target)*(target-start),0)
        truck=TruckDriving((-6.3,250,-math.pi/2),3.2315,.67);truck.speed=3;truck.controls(1)
        for _ in range(80):
            truck.step(.1,world.near(truck.x,truck.y),world.supported)
            if truck.hit:break
        self.assertIsNotNone(truck.hit)
        self.assertIn('RTG_PRIMARY_DYNAMIC',truck.hit)

    def test_quay_tracks_can_be_crossed_but_crane_wheels_remain_solid(self):
        import math
        from truck_driving import TruckDriving
        world=SceneObstacles(self.stage)
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        for rail in self.stage.GetPrimAtPath('/World/SMART_PORT_OFFSHORE_QUAY/Rails').GetChildren():
            bounds=cache.ComputeWorldBound(rail).ComputeAlignedRange();x=bounds.GetMidpoint()[0]
            self.assertTrue(world.crossable_track(rail,bounds))
            # This exception must stop applying if a track is raised or enlarged.
            raised=Gf.Range3d(bounds.GetMin()+Gf.Vec3d(0,0,.3),bounds.GetMax()+Gf.Vec3d(0,0,.3))
            self.assertFalse(world.crossable_track(rail,raised))
            for y in ((150,200) if rail.GetName()=='STS_Rail_01' else ()):
                for heading in (0,math.pi,math.pi/4):
                    direction=1 if math.cos(heading)>0 else -1
                    start=x+direction*4;target=x-direction*4
                    truck=TruckDriving((start,y,heading),3.2315,.67);truck.speed=3;truck.controls(1)
                    for _ in range(80):
                        truck.step(.1,world.near(truck.x,truck.y),world.supported)
                        self.assertIsNone(truck.hit,(str(rail.GetPath()),y,heading,truck.state()))
                        self.assertNotEqual(truck.event,'water_reset')
                        if (truck.x-target)*direction<=0:break
                    self.assertLessEqual((truck.x-target)*direction,0)
            if rail.GetName()=='STS_Rail_02':
                # The seaward track is only 60 cm from the quay edge. It is
                # crossable, but the truck must still stop at the real curb.
                truck=TruckDriving((x+4,150,0),3.2315,.67);truck.speed=3;truck.controls(1)
                for _ in range(80):
                    truck.step(.1,world.near(truck.x,truck.y),world.supported)
                    if truck.hit:break
                self.assertIsNotNone(truck.hit)
                self.assertIn('/Edges/WatersideEdge',truck.hit)
            truck=TruckDriving((x+4,30,0),3.2315,.67);truck.speed=3;truck.controls(1)
            for _ in range(80):
                truck.step(.1,world.near(truck.x,truck.y),world.supported)
                if truck.hit:break
            self.assertIsNotNone(truck.hit)
            self.assertIn('/RunningGear/',truck.hit)

    def test_moving_equipment_invalidates_cached_collision_geometry(self):
        from rtg_live_control import GANTRY_PATH
        from truck_driving import TruckDriving,intersects
        stage=Usd.Stage.CreateInMemory();source=UsdGeom.Xform.Define(stage,PARK)
        source.AddTranslateOp().Set(Gf.Vec3d(138,67,.6))
        body=UsdGeom.Cube.Define(stage,PARK+'/Body');body.CreateSizeAttr(1);body.AddScaleOp().Set(Gf.Vec3f(6.4,1.3,1))
        gantry=UsdGeom.Xform.Define(stage,GANTRY_PATH)
        move=gantry.AddTranslateOp();move.Set(Gf.Vec3d(0))
        pillar=UsdGeom.Cube.Define(stage,GANTRY_PATH+'/Pillar');pillar.CreateSizeAttr(.5)
        pillar.AddTranslateOp().Set(Gf.Vec3d(0,0,.7))
        driver=TruckDriver(stage,SimpleNamespace(camera_path='/OmniverseKit_Persp'),'owner',{'expires':time.time()+1000})
        truck=TruckDriving((0,0,0),3,.7)
        self.assertTrue(any(intersects(truck.footprint(),rect) for rect,path in driver.world.near(0,0)))
        with Usd.EditContext(stage,driver.layer):move.Set(Gf.Vec3d(12,0,0))
        self.assertFalse(any(intersects(truck.footprint(),rect) for rect,path in driver.world.near(0,0)))
        driver.close()


if __name__=='__main__':unittest.main()
