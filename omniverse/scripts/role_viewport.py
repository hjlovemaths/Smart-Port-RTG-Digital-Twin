"""A private driver render context; never changes the administrator viewport."""
import ctypes,time,queue,threading
from contextlib import contextmanager
from pxr import Sdf,Usd,UsdUtils

DRIVER_SIGNAL_PORT=49230
DRIVER_MEDIA_PORT=48006
DRIVER_RESOLUTION=(1280,720)
DRIVER_FPS=20


def copy_bgra_frame(data,width,height,rgba=False):
    """Own exactly one packed frame; capture memory expires after the callback."""
    import numpy as np
    pixels=data.reshape(height,-1)[:,:width*4].reshape(height,width,4)
    result=np.empty((height,width,4),dtype=np.uint8)
    if rgba:
        result[:,:,0]=pixels[:,:,2];result[:,:,1]=pixels[:,:,1]
        result[:,:,2]=pixels[:,:,0];result[:,:,3]=pixels[:,:,3]
    else:np.copyto(result,pixels)
    return result


class CudaFrames:
    """Small BGRA upload ring using the installed NVIDIA driver, no Python ABI dependency."""
    def __init__(self):
        self.api=ctypes.WinDLL('nvcuda.dll');self.context=ctypes.c_void_p();self.buffers=[];self.index=0;self.size=0
        signatures={'cuInit':[ctypes.c_uint],
            'cuDevicePrimaryCtxRetain':[ctypes.POINTER(ctypes.c_void_p),ctypes.c_int],
            'cuDevicePrimaryCtxRelease_v2':[ctypes.c_int],
            'cuCtxPushCurrent_v2':[ctypes.c_void_p],
            'cuCtxPopCurrent_v2':[ctypes.POINTER(ctypes.c_void_p)],
            'cuMemAlloc_v2':[ctypes.POINTER(ctypes.c_uint64),ctypes.c_size_t],
            'cuMemFree_v2':[ctypes.c_uint64],
            'cuMemcpyHtoD_v2':[ctypes.c_uint64,ctypes.c_void_p,ctypes.c_size_t]}
        for name,args in signatures.items():
            fn=getattr(self.api,name);fn.argtypes=args;fn.restype=ctypes.c_int
        self.call('cuInit',0);self.call('cuDevicePrimaryCtxRetain',ctypes.byref(self.context),0)

    def call(self,name,*args):
        code=getattr(self.api,name)(*args)
        if code:raise RuntimeError('驾驶画面CUDA错误 '+name+': '+str(code))

    @contextmanager
    def scope(self):
        self.call('cuCtxPushCurrent_v2',self.context)
        try:yield
        finally:
            previous=ctypes.c_void_p();self.call('cuCtxPopCurrent_v2',ctypes.byref(previous))

    def upload(self,pixels):
        size=pixels.nbytes
        with self.scope():
            if size!=self.size:
                for pointer in self.buffers:self.call('cuMemFree_v2',pointer)
                self.buffers=[];self.size=size
                for _ in range(3):
                    pointer=ctypes.c_uint64();self.call('cuMemAlloc_v2',ctypes.byref(pointer),size);self.buffers.append(pointer.value)
            pointer=self.buffers[self.index%3];self.index+=1
            self.call('cuMemcpyHtoD_v2',pointer,ctypes.c_void_p(pixels.ctypes.data),size)
            return pointer

    def close(self):
        with self.scope():
            for pointer in self.buffers:self.call('cuMemFree_v2',pointer)
            self.buffers=[]
        self.call('cuDevicePrimaryCtxRelease_v2',0)


def private_stage(source):
    # Share authoritative scene/session changes, with camera/cab appearance
    # overrides in a separate session layer that no other role sees.
    layer=Sdf.Layer.CreateAnonymous('truck_driver_view.usda')
    layer.subLayerPaths=[source.GetSessionLayer().identifier]
    view=Usd.Stage.Open(source.GetRootLayer(),layer)
    view.SetEditTarget(layer)
    return view


class FrameStreamer:
    """Bounded latest-frame queue. GPU/NVENC waits never run on Kit's thread."""
    def __init__(self,signal_port=None,media_port=None):
        self.queue=queue.Queue(maxsize=1);self.stopping=threading.Event();self.finished=threading.Event()
        self.signal_port=DRIVER_SIGNAL_PORT if signal_port is None else signal_port
        self.media_port=DRIVER_MEDIA_PORT if media_port is None else media_port
        self.error='';self.frames=0;self.started=False
        self.thread=threading.Thread(target=self.run,name='smartport-driver-encoder',daemon=True)
        self.thread.start()

    def submit(self,pixels):
        if self.stopping.is_set():return
        try:self.queue.get_nowait()
        except queue.Empty:pass
        try:self.queue.put_nowait(pixels)
        except queue.Full:pass

    def run(self):
        upload=server=None
        try:
            from omni.kit.livestream.core import Server,ServerConfig
            while not self.stopping.is_set():
                try:pixels=self.queue.get(timeout=.1)
                except queue.Empty:continue
                if self.stopping.is_set():break
                height,width,_=pixels.shape
                if upload is None:upload=CudaFrames()
                pointer=upload.upload(pixels)
                if server is None:
                    server=Server('webrtc')
                    if not server.start(ServerConfig(width=width,height=height,target_fps=DRIVER_FPS,
                            signal_port=self.signal_port,stream_port=self.media_port,
                            allow_dynamic_resize=False,allow_static_resize=False)):
                        raise RuntimeError('无法启动设备司机独立画面流')
                    self.started=True
                with upload.scope():server.stream_video_cuda_buffer(pointer,width*4,width,height)
                self.frames+=1
        except Exception as error:
            self.error=str(error);print('[Driver stream '+str(self.signal_port)+'] '+self.error,flush=True)
        finally:
            try:
                if server:server.close()
                if upload:upload.close()
            finally:self.started=False;self.finished.set()

    def close(self):
        self.stopping.set()
        # No join here: encoder/GPU shutdown must not freeze the admin stream.


class DriverViewport:
    def __init__(self,stage,camera,context_name='smartport_truck_driver',texture_name='SmartPortTruckDriverTexture',signal_port=None,media_port=None):
        import carb.settings
        import omni.kit.app
        import omni.usd
        self.closed=False;self.texture=None;self.error='';self.encoder=None
        self.pending=False;self.next_frame=0.
        self.settings=carb.settings.get_settings();self._camera=camera
        manager=omni.kit.app.get_app().get_extension_manager()
        if not manager.is_extension_enabled('omni.kit.livestream.webrtc'):
            raise ValueError('独立驾驶画面尚未就绪，请启用 WebRTC 后重启 Kit')
        self.stage=private_stage(stage)
        self.context_name=context_name;self.texture_name=texture_name
        if omni.usd.get_context(self.context_name):
            raise ValueError('独立驾驶视口正在释放，请稍后重试')
        self.context=omni.usd.create_context(self.context_name)
        self.cache_id=UsdUtils.StageCache.Get().Insert(self.stage)
        def attached(ok,error):
            if not ok:self.error=error or '无法创建独立驾驶场景'
        self.context.attach_stage_with_callback(self.cache_id.ToLongInt(),attached)
        self.encoder=FrameStreamer(signal_port,media_port)

    @property
    def frames(self):return self.encoder.frames if self.encoder else 0

    @property
    def camera_path(self):return self._camera

    @camera_path.setter
    def camera_path(self,value):
        self._camera=str(value)
        if self.texture:self.texture.set_camera_path(str(value))

    def tick(self):
        if self.closed:return
        if self.error:raise RuntimeError(self.error)
        if self.encoder.error:raise RuntimeError(self.encoder.error)
        if not self.texture and self.context.get_stage():
            import omni.usd
            from omni.kit.hydra_texture import create_hydra_texture
            omni.usd.create_hydra_engine('rtx',self.context)
            self.texture=create_hydra_texture(self.texture_name,*DRIVER_RESOLUTION,
                self.context_name,self._camera,'rtx',is_async=True,is_async_low_latency=False,hydra_tick_rate=DRIVER_FPS)
        if not self.texture or self.pending or time.monotonic()<self.next_frame:return
        try:aovs=self.texture.get_aov_info(aov_name='LdrColor',include_texture=True)
        except RuntimeError:return
        if not aovs or 'texture' not in aovs[0]:return
        from omni.kit.renderer_capture import acquire_renderer_capture_interface
        self.pending=True;self.next_frame=time.monotonic()+1/DRIVER_FPS
        acquire_renderer_capture_interface().capture_next_frame_rp_resource_callback(
            self.captured,aovs[0]['texture']['rp_resource'])

    def captured(self,capsule,size,width,height,format):
        self.pending=False
        if self.closed:return
        try:
            import numpy as np
            pointer=ctypes.pythonapi.PyCapsule_GetPointer
            pointer.restype=ctypes.c_void_p;pointer.argtypes=[ctypes.py_object,ctypes.c_char_p]
            data=np.ctypeslib.as_array(ctypes.cast(pointer(capsule,None),ctypes.POINTER(ctypes.c_uint8)),shape=(size,))
            if format not in (format.RGBA8_UNORM,format.BGRA8_UNORM):raise ValueError('驾驶画面像素格式不受支持')
            # Core's WebRTC API consumes BGRA CUDA memory. Explicit conversion
            # also avoids the AOV plugin's red/blue swap on Windows DirectX.
            self.encoder.submit(copy_bgra_frame(data,width,height,format==format.RGBA8_UNORM))
        except Exception as error:self.error=str(error)

    def ready(self):
        return bool(not self.closed and not self.error and self.encoder and not self.encoder.error and self.encoder.started and self.frames>0)

    def close(self):
        if self.closed:return
        self.closed=True
        if self.encoder:self.encoder.close()
        self.texture=None
        import omni.usd
        self.context.close_stage()
        omni.usd.destroy_context(self.context_name)
        UsdUtils.StageCache.Get().Erase(self.cache_id)
        self.context=None;self.stage=None
