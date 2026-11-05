"""Real transfer phases and camera poses; fake only the viewport handle."""
import math,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from pxr import Gf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'));sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from smartport.agent_panel.scene_runtime import SceneRuntime
from smartport.agent_panel.viewport_navigation import ViewportNavigation,CAMERA_PATH as MANUAL_CAMERA
from transfer_cinema import CAMERA_PATH


class CinemaTests(unittest.TestCase):
    def setUp(self):
        self.stage=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        self.viewport=SimpleNamespace(camera_path='/OmniverseKit_Persp')
        self.events=[];self.runtime=SceneRuntime(ROOT,notify=self.events.append)
        self.runtime._viewport=lambda:self.viewport
        self.root_before=self.stage.GetRootLayer().ExportToString()
        preview=self.runtime.execute(self.stage,'preview_ship_to_yard_transfer',{'source_bay':1,'source_side':'port','row_from_side':1,'target_bay':'3C/001','target_row':6})
        self.result=self.runtime.execute(self.stage,'start_ship_to_yard_transfer',{'plan_id':preview['plan_id']})
        self.task=self.runtime.transfer_task;self.director=self.runtime.transfer_camera
    def tearDown(self):
        self.runtime.close();self.task.c.close()
        self.assertEqual(self.stage.GetRootLayer().ExportToString(),self.root_before)
    def pose(self):
        return UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(CAMERA_PATH))
    def test_default_start_frames_task_without_changing_equipment(self):
        self.assertEqual(self.result['cinematic']['status'],'running')
        self.assertEqual(self.viewport.camera_path,CAMERA_PATH)
        before=(dict(self.task.c.state),list(self.task.rtg.offset),Gf.Vec3d(self.task.truck_point))
        self.director.tick(.1)
        self.assertEqual(before,(dict(self.task.c.state),list(self.task.rtg.offset),self.task.truck_point))
        self.assertEqual(self.director.state()['step'],1)
        self.assertEqual(self.stage.GetPrimAtPath(CAMERA_PATH).GetAttribute('visibility').Get(),'invisible')
    def test_complete_event_shots_follow_real_task_and_restore_previous_view(self):
        for i in range(12000):
            self.runtime.tick(self.stage,.1)
            if not self.director.closed and i%5==0:
                pose=self.pose();eye=pose.ExtractTranslation()
                self.assertTrue(all(math.isfinite(v) for v in eye));self.assertGreaterEqual(eye[2],2.5)
                self.assertAlmostEqual(pose.GetDeterminant(),1.,places=5)
                self.assertAlmostEqual(pose.TransformDir(Gf.Vec3d(1,0,0))[2],0.,places=5)
            if self.task.finished and self.director.closed:break
        self.assertTrue(self.task.finished,self.task.summary())
        self.assertEqual(self.director.state()['status'],'completed')
        self.assertEqual(self.viewport.camera_path,'/OmniverseKit_Persp')
        for key in ('overview','truck_out','locate','ship_pick','sts_cross','truck_handoff','truck_loaded','causeway','rtg_pick','yard_place','complete'):
            self.assertIn(key,self.director.history)
        self.assertEqual(len([e for e in self.events if e['kind']=='simulation_task_complete']),1)
        self.assertEqual(self.director.camera_history.count('rtg_work'),1)

    def test_rtg_phases_keep_one_fitted_camera_without_restarting_transitions(self):
        self.director.elapsed=4.;self.task.phase='RTG吊具下降到集卡'
        for _ in range(30):self.director.tick(.1)
        pose=self.pose();horizontal=math.tan(math.radians(55)/2);vertical=horizontal/(16/9)
        for phase in ('RTG对位闭锁','RTG起升','RTG小车移向第6排','RTG叠箱下降','RTG开锁落箱','RTG吊具抬离箱顶'):
            self.task.phase=phase;self.director.tick(.1)
            self.assertEqual(self.director.camera_group,'rtg_work')
            self.assertEqual(self.pose(),pose,'RTG phase changes must not trigger another camera move')
            self.assertGreaterEqual(self.director.transition,self.director.transition_duration)
            self.assertEqual(self.director.state()['detail'],phase)
        for corner in range(8):
            b=self.director.rtg_work.bounds
            point=pose.GetInverse().Transform(Gf.Vec3d(*(b.GetMax()[a] if corner&(1<<a) else b.GetMin()[a] for a in range(3))))
            self.assertGreater(-point[2],0)
            self.assertLess(abs(point[0])/-point[2],horizontal)
            self.assertLess(abs(point[1])/-point[2],vertical)
        self.task.delivery_done=True;self.task.phase='集卡空车返回停车场';self.director.tick(.1)
        self.assertEqual(self.pose(),pose,'Keep the RTG result view during empty-truck return')
        self.assertEqual(self.director.camera_history.count('rtg_work'),1)

    def test_manual_input_adopts_subject_and_pauses_until_explicit_resume(self):
        self.director.elapsed=3.1;self.director.tick(.1)
        self.runtime.pause_transfer_camera('manual')
        nav=ViewportNavigation();nav._context=lambda:(self.stage,self.viewport)
        nav.execute({'orbit_x':10})
        self.assertEqual(nav.focus_path,str(self.task.truck.GetPath()))
        self.assertEqual(self.viewport.camera_path,MANUAL_CAMERA)
        for _ in range(10):self.runtime.tick(self.stage,.1)
        self.assertEqual(self.viewport.camera_path,MANUAL_CAMERA)
        self.assertEqual(self.director.state()['status'],'paused')
        self.runtime.execute(self.stage,'control_transfer_camera',{'action':'resume'})
        self.runtime.tick(self.stage,.1)
        self.assertEqual(self.viewport.camera_path,CAMERA_PATH)
        self.runtime.execute(self.stage,'control_transfer_camera',{'action':'stop'})
        self.assertEqual(self.viewport.camera_path,MANUAL_CAMERA)
        self.assertFalse(self.task.paused)
    def test_task_pause_freezes_camera_and_reset_cleans_camera_layer(self):
        self.task.pause();before=self.pose()
        self.runtime.tick(self.stage,.1);self.assertEqual(self.pose(),before)
        self.assertEqual(self.director.state()['status'],'task_paused')
        self.runtime.execute(self.stage,'control_ship_to_yard_transfer',{'action':'reset'})
        self.assertTrue(self.director.closed);self.assertEqual(self.viewport.camera_path,'/OmniverseKit_Persp')
        self.assertFalse(self.stage.GetPrimAtPath(CAMERA_PATH))

    def test_camera_failure_stops_only_the_director(self):
        def unavailable(_key):raise RuntimeError('viewport closed')
        self.director.framing=unavailable
        self.runtime.tick(self.stage,.1)
        self.assertTrue(self.director.closed);self.assertFalse(self.task.closed)
        self.assertFalse(self.task.paused);self.assertEqual(self.task.status,'running')
        self.assertEqual(self.runtime.transfer_camera_error,'viewport closed')

class ReverseCinemaTests(unittest.TestCase):
    def setUp(self):
        self.stage=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        self.viewport=SimpleNamespace(camera_path='/OmniverseKit_Persp')
        self.runtime=SceneRuntime(ROOT);self.runtime._viewport=lambda:self.viewport
        preview=self.runtime.execute(self.stage,'preview_yard_to_ship_transfer',{'source_bay':'3C/005','source_row':2,'target_location':'011090'})
        self.result=self.runtime.execute(self.stage,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id']})
        self.task=self.runtime.transfer_task;self.director=self.runtime.transfer_camera

    def tearDown(self):
        self.runtime.close();self.task.c.close()

    def test_reverse_shots_follow_cloned_box_and_keep_final_shot_during_return(self):
        self.assertEqual(self.result['cinematic']['status'],'running')
        self.assertEqual(self.director.state()['destination'],'船上 011090')
        self.assertEqual(self.director.state()['direction'],'yard_to_ship')
        returning=False
        for i in range(12000):
            self.runtime.tick(self.stage,.1)
            if not self.director.closed:
                self.assertGreaterEqual(self.director.eye[2],2.5)
                pose=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(CAMERA_PATH))
                self.assertAlmostEqual(pose.TransformDir(Gf.Vec3d(1,0,0))[2],0.,places=5)
                if self.director.shot not in ('overview','causeway','truck_out','truck_loaded'):
                    self.assertEqual(self.stage.GetPrimAtPath(CAMERA_PATH).GetAttribute('smartport:navigationTarget').Get(),str(self.task.cargo.GetPath()))
                if self.task.phase in ('集卡空车返回停车场','集卡倒车归位'):
                    returning=True;self.assertEqual(self.director.shot,'complete')
            if self.task.finished and self.director.closed:break
        self.assertTrue(returning);self.assertTrue(self.task.finished,self.task.summary())
        self.assertTrue(self.director.closed);self.assertEqual(self.viewport.camera_path,'/OmniverseKit_Persp')
        for shot in ('overview','truck_out','yard_locate','yard_pick','rtg_cross','rtg_handoff','truck_loaded','causeway','sts_pick','ship_place','complete'):
            self.assertIn(shot,self.director.history)
        self.assertEqual(self.director.camera_history.count('rtg_work'),1)

    def test_reverse_rtg_descent_lift_transfer_and_handoff_share_one_camera(self):
        self.director.elapsed=4.;self.task.phase='RTG下降到源箱'
        for _ in range(30):self.director.tick(.1)
        pose=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(CAMERA_PATH))
        for phase in ('RTG源箱对位闭锁','RTG起升堆场箱','RTG移箱到集卡','RTG落箱到集卡','RTG开锁交接','RTG抬离集卡'):
            self.task.phase=phase;self.director.tick(.1)
            self.assertEqual(UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(CAMERA_PATH)),pose)
        self.assertEqual(self.director.camera_history.count('rtg_work'),1)

    def test_reverse_manual_camera_adopts_visible_cargo_and_reset_restores_view(self):
        self.director.elapsed=4;self.task.phase='RTG起升堆场箱';self.director.tick(.1)
        self.runtime.pause_transfer_camera('manual')
        nav=ViewportNavigation();nav._context=lambda:(self.stage,self.viewport);nav.execute({'orbit_x':10})
        self.assertEqual(nav.focus_path,str(self.task.cargo.GetPath()))
        self.runtime.execute(self.stage,'control_transfer_camera',{'action':'resume'})
        self.runtime.execute(self.stage,'control_yard_to_ship_transfer',{'action':'reset'})
        self.assertTrue(self.director.closed);self.assertEqual(self.viewport.camera_path,MANUAL_CAMERA)
        self.assertFalse(self.stage.GetPrimAtPath(CAMERA_PATH))

    def test_source_shot_obstacles_include_adjacent_yard_bays(self):
        from ship_transfer_plan import YARD,box
        neighbors=[p for group in self.stage.GetPrimAtPath(YARD).GetChildren() for p in group.GetChildren()
                   if p.GetAttribute('live:bayId').Get()=='3C/002' and p.GetAttribute('live:row').Get()==2]
        self.assertTrue(neighbors)
        center,direction,distance=self.director.framing('yard_pick')
        self.director.obstacles=self.director.loading_obstacles(center)
        expected=[box(self.stage,p.GetPath()) for p in neighbors]
        self.assertTrue(any((b.GetMin()-a.GetMin()).GetLength()<1e-6 and (b.GetMax()-a.GetMax()).GetLength()<1e-6
                            for b in self.director.loading_static for a in expected))
        eye,occluded=self.director.safe_eye(center,direction,distance)
        self.assertFalse(occluded)
        from transfer_cinema import segment_hits
        self.assertFalse(any(segment_hits(eye,center,b) for b in expected))


if __name__=='__main__':unittest.main()
