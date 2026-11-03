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

if __name__=='__main__':unittest.main()
