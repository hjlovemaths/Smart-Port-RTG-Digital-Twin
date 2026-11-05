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
        entered=threading.Event();release=threading.Event();closed=threading.Event();seen=[];threads=[]
        class Server:
            def __init__(self,*args):pass
            def start(self,*args):return True
            def stream_video_cuda_buffer(self,pointer,*args):
                threads.append(threading.get_ident());seen.append(pointer);entered.set();release.wait(5)
            def close(self):closed.set()
        class Upload:
            def upload(self,pixels):return pixels.number
            def scope(self):return nullcontext()
            def close(self):pass
        module=ModuleType('omni.kit.livestream.core');module.Server=Server;module.ServerConfig=lambda **kw:kw
        class Frame:
            shape=(2,2,4)
            def __init__(self,n):self.number=n
        with patch.dict(sys.modules,{'omni.kit.livestream.core':module}),patch.object(role_viewport,'CudaFrames',Upload):
            worker=role_viewport.FrameStreamer()
            try:
                worker.submit(Frame(0));self.assertTrue(entered.wait(3))
                for i in range(1,100):worker.submit(Frame(i))
                self.assertEqual(worker.queue.qsize(),1)
                self.assertEqual(worker.queue.queue[0].number,99)
                start=time.monotonic();worker.close()
                self.assertLess(time.monotonic()-start,.1)
                self.assertNotEqual(threads[0],threading.get_ident())
                self.assertEqual(seen,[0])
            finally:
                release.set();self.assertTrue(worker.finished.wait(3))
            self.assertTrue(closed.is_set())

if __name__=='__main__':unittest.main()
