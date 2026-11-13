"""A stalled encoder must not stall Kit or accumulate old frames."""
import sys,time,threading,unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from contextlib import nullcontext
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
import role_viewport


class StreamWorkerTests(unittest.TestCase):
    def test_slow_encoder_has_bounded_queue_and_nonblocking_shutdown(self):
        entered=threading.Event();release=threading.Event();closed=threading.Event();seen=[];threads=[];configs=[]
        class Server:
            def __init__(self,*args):pass
            def start(self,config):configs.append(config);return True
            def stream_video_cuda_buffer(self,pointer,*args):
                threads.append(threading.get_ident());seen.append(pointer);entered.set();release.wait(5)
            def close(self):closed.set()
        class Upload:
            def upload(self,pixels):return pixels.number
            def scope(self):return nullcontext()
            def close(self):pass
        module=ModuleType('omni.kit.livestream.core');module.Server=Server;module.ServerConfig=lambda **kw:kw
        class Frame:
            shape=(1080,1920,4)
            def __init__(self,n):self.number=n
        with patch.dict(sys.modules,{'omni.kit.livestream.core':module}),patch.object(role_viewport,'CudaFrames',Upload):
            worker=role_viewport.FrameStreamer(fps=30)
            try:
                worker.submit(Frame(0));self.assertTrue(entered.wait(3))
                for i in range(1,100):worker.submit(Frame(i))
                self.assertEqual(worker.queue.qsize(),1)
                self.assertEqual(worker.queue.queue[0].number,99)
                start=time.monotonic();worker.close()
                self.assertLess(time.monotonic()-start,.1)
                self.assertNotEqual(threads[0],threading.get_ident())
                self.assertEqual(seen,[0])
                self.assertEqual((configs[0]['width'],configs[0]['height'],configs[0]['target_fps']),(1920,1080,30))
            finally:
                release.set();self.assertTrue(worker.finished.wait(3))
            self.assertTrue(closed.is_set())

    def test_rgba_is_converted_on_worker_before_upload(self):
        import numpy as np
        done=threading.Event();seen=[]
        class Server:
            def __init__(self,*args):pass
            def start(self,*args):return True
            def stream_video_cuda_buffer(self,*args):done.set()
            def close(self):pass
        class Upload:
            def upload(self,pixels):seen.append(pixels.copy());return 0
            def scope(self):return nullcontext()
            def close(self):pass
        module=ModuleType('omni.kit.livestream.core');module.Server=Server;module.ServerConfig=lambda **kw:kw
        with patch.dict(sys.modules,{'omni.kit.livestream.core':module}),patch.object(role_viewport,'CudaFrames',Upload):
            worker=role_viewport.FrameStreamer()
            try:
                worker.submit(np.array([[[1,2,3,255]]],dtype=np.uint8),rgba=True)
                self.assertTrue(done.wait(3))
                self.assertEqual(seen[0].tolist(),[[[3,2,1,255]]])
            finally:worker.close();self.assertTrue(worker.finished.wait(3))

    def test_rejected_second_webrtc_stream_keeps_driver_picture_alive(self):
        import numpy as np
        class Server:
            def __init__(self,*args):pass
            def start(self,*args):return False
            def close(self):pass
        class Upload:
            def upload(self,pixels):return 0
            def close(self):pass
        module=ModuleType('omni.kit.livestream.core');module.Server=Server;module.ServerConfig=lambda **kw:kw
        with patch.dict(sys.modules,{'omni.kit.livestream.core':module}),patch.object(role_viewport,'CudaFrames',Upload):
            worker=role_viewport.FrameStreamer()
            try:
                worker.submit(np.zeros((8,8,4),dtype=np.uint8))
                deadline=time.monotonic()+3
                while not worker.jpeg_frame and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(worker.transport,'jpeg')
                self.assertTrue(worker.started)
                self.assertTrue(worker.jpeg_frame.startswith(b'\xff\xd8'))
                self.assertFalse(worker.error)
                with patch.object(role_viewport.time,'monotonic',return_value=time.monotonic()+1):
                    self.assertIsNone(worker.jpeg_frame,'a frozen capture must stop supplying frames')
            finally:worker.close();self.assertTrue(worker.finished.wait(3))

if __name__=='__main__':unittest.main()
