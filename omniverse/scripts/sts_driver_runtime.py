"""Independent STS_01 operator, private camera and main-thread axis control."""
import builtins,math,time
from pxr import Gf,Usd,UsdGeom
from sts_control import shared_sts_controller
from sts_driver_cargo import STSCargo

CRANE='STS_01'
ROOT='/World/STSControls/'+CRANE
TROLLEY=ROOT+'/Gantry/Trolley'
HOIST=TROLLEY+'/Hoist'
CAMERA='/World/STSDriverDemo/Camera'
VIEWS=('under','side','end')

def active_sts_driver(stage=None):
    item=getattr(builtins,'_smartport_sts_driver',None)
    return item if item and not item.closed and (stage is None or item.stage==stage) else None

class STSDriver:
    def __init__(self,stage,owner,session,clock=time.monotonic,view=None):
        if active_sts_driver():raise ValueError('STS_01已由另一驾驶会话占用')
        self.stage=stage;self.owner=owner;self.session=session;self.clock=clock;self.closed=False
        self.last_seen=self.last_input=clock();self.last_seq=-1
        self.axes=dict(gantry=0.,trolley=0.,hoist=0.);self.view='under';self.fov=140.;self.notice='';self.notice_until=0.
        self.controller=shared_sts_controller(stage,CRANE)
        if self.controller.stopped:raise ValueError('岸桥处于停止状态，请管理员先恢复')
        if any(abs(self.controller.targets[k]-v)>.001 for k,v in self.controller.state.items()):raise ValueError('岸桥正在移动，请等待到位')
        self.cargo=STSCargo(stage,self.controller,self.contact_points)
        self.private_view=view
        try:
            if view is None:
                from role_viewport import DriverViewport
                self.private_view=DriverViewport(stage,CAMERA,'smartport_sts_driver','SmartPortSTSDriverTexture',49250,48010)
            self.camera_stage=self.private_view.stage
            with Usd.EditContext(self.camera_stage,self.camera_stage.GetSessionLayer()):
                self.camera=UsdGeom.Camera.Define(self.camera_stage,CAMERA)
                self.camera.CreateClippingRangeAttr(Gf.Vec2f(.025,3000))
                self.camera.CreateHorizontalApertureAttr(36);self.camera.CreateVerticalApertureAttr(20.25)
                self.camera_op=self.camera.AddTransformOp();self.apply_camera()
            builtins._smartport_sts_driver=self
        except Exception:self.close();raise

    def command(self,owner,payload):
        if owner!=self.owner:raise ValueError('岸桥已由另一驾驶会话占用')
        if set(payload)-{'gantry','trolley','hoist','brake','view','fov','seq','size_ft','lock'}:raise ValueError('STS驾驶指令包含未知参数')
        seq=payload.get('seq',self.last_seq+1)
        if type(seq) is not int or not 0<=seq<2**53:raise ValueError('驾驶指令序号无效')
        if seq<=self.last_seq:return self.state()
        axes={k:payload.get(k,0.) for k in self.axes}
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not -1<=v<=1 for v in axes.values()):raise ValueError('岸桥轴输入须为-1到1的有限数字')
        brake=payload.get('brake',False)
        if type(brake) is not bool:raise ValueError('制动参数须为布尔值')
        view=payload.get('view',self.view);fov=payload.get('fov',self.fov)
        if view not in VIEWS:raise ValueError('无效STS视角')
        if isinstance(fov,bool) or not isinstance(fov,(float,int)) or not math.isfinite(fov) or not 10<=fov<=140:raise ValueError('视野角度须在10°到140°之间')
        if 'size_ft' in payload and 'lock' in payload:raise ValueError('请逐项操作尺型和开闭锁')
        if 'size_ft' in payload:
            size=payload['size_ft']
            if type(size) is not int or size not in (20,40):raise ValueError('吊具尺型只能为20或40尺')
            if self.cargo.locked:raise ValueError('请先落箱开锁，再切换20/40尺型')
            if any(axes.values()):raise ValueError('请先停止岸桥再切换尺型')
            try:self.cargo.change_size(size)
            except ValueError as error:
                self.axes=dict.fromkeys(self.axes,0.);self.controller.hold();self.notice=str(error);self.notice_until=self.clock()+8;raise
        if 'lock' in payload:
            if type(payload['lock']) is not bool:raise ValueError('开闭锁参数须为布尔值')
            if brake or any(axes.values()):raise ValueError('请先停止岸桥，再操作开闭锁')
            try:self.cargo.lock(payload['lock'])
            except ValueError as error:
                self.axes=dict.fromkeys(self.axes,0.);self.controller.hold();self.notice=str(error);self.notice_until=self.clock()+8;raise
            self.notice='已闭锁，箱子随吊具移动' if self.cargo.locked else '已开锁，箱子保持落稳位置';self.notice_until=self.clock()+8
        self.axes={k:0. if brake else float(v) for k,v in axes.items()}
        self.view=view;self.fov=float(fov);self.last_seq=seq
        self.last_input=self.last_seen=self.clock();self.apply_camera();return self.state()

    def contact_points(self):
        cache=UsdGeom.XformCache();points=[]
        for end in ('Front','Rear'):
            for side in ('Front','Rear'):
                prim=self.stage.GetPrimAtPath(HOIST+f'/STS_SpreaderEnd{end}/CornerLock{side}')
                if not prim:raise ValueError('岸桥吊具锁点不存在')
                points.append(cache.GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(0,0,-.5)))
        return points

    def contact(self):return sum(self.contact_points(),Gf.Vec3d(0))/4

    def apply_camera(self):
        trolley=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(TROLLEY))
        contact=self.contact();up=trolley.TransformDir(Gf.Vec3d(1,0,0));up=Gf.Vec3d(up[0],up[1],0).GetNormalized()
        if self.view=='under':
            up=Gf.Vec3d(-up[1],up[0],0)
            trolley_origin=trolley.ExtractTranslation();eye=Gf.Vec3d(contact[0],contact[1],trolley_origin[2]+8*abs(trolley[2][2]));target=eye-Gf.Vec3d(0,0,10)
        else:
            offset=trolley.TransformDir(Gf.Vec3d(1,0,0) if self.view=='side' else Gf.Vec3d(0,1,0));offset=Gf.Vec3d(offset[0],offset[1],0).GetNormalized()
            eye=contact+offset*10+Gf.Vec3d(0,0,2);target=contact-Gf.Vec3d(0,0,.3);up=Gf.Vec3d(0,0,1)
        with Usd.EditContext(self.camera_stage,self.camera_stage.GetSessionLayer()):
            self.camera.CreateFocalLengthAttr(36/(2*math.tan(math.radians(self.fov)/2)))
            self.camera_op.Set(Gf.Matrix4d(1).SetLookAt(eye,target,up).GetInverse())
        self.private_view.camera_path=CAMERA

    def tick(self,dt):
        if self.closed:return
        now=self.clock()
        if self.session.get('driver_released') or self.session.get('expires',0)<=time.time() or now-self.last_seen>10:self.close();return
        if now-self.last_input>.35:self.axes=dict.fromkeys(self.axes,0.)
        dt=max(0.,min(.1,float(dt)));c=self.controller
        values={}
        for axis in self.axes:
            low,high=c.limits[axis];values[axis]=max(low,min(high,c.state[axis]+self.axes[axis]*{'gantry':1.,'trolley':.8,'hoist':.6}[axis]*dt))
        try:
            root=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(ROOT))
            gantry=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(ROOT+'/Gantry'))
            vectors=(root.TransformDir(Gf.Vec3d(-1,0,0)),gantry.TransformDir(Gf.Vec3d(0,-1,0)),gantry.TransformDir(Gf.Vec3d(0,0,1)))
            delta=sum((vectors[i]*(values[axis]-c.state[axis]) for i,axis in enumerate(('gantry','trolley','hoist'))),Gf.Vec3d(0))
            fraction,notice=self.cargo.limit_move(delta,None)
            if notice:self.notice=notice;self.notice_until=now+8
            if fraction<1:
                values={axis:c.state[axis]+(value-c.state[axis])*fraction for axis,value in values.items()}
            c.command(**values);c.tick(dt);self.cargo.update()
        except ValueError as error:c.hold();self.axes=dict.fromkeys(self.axes,0.);self.notice=str(error);self.notice_until=now+8
        self.apply_camera();self.private_view.tick()

    def state(self):
        c=self.controller
        return dict(status='closed' if self.closed else 'driving',vehicle_id=CRANE,view=self.view,fov=self.fov,
                    gantry_m=c.state['gantry'],trolley_m=c.state['trolley'],hoist_m=c.state['hoist'],
                    spreader_size_ft=c.spreader_size_ft,locked=self.cargo.locked,carried_container=self.cargo.held,
                    input_seq=self.last_seq,notice=self.notice if self.clock()<self.notice_until else '',
                    stream_ready=self.private_view.ready(),simulation_only=True)

    def close(self):
        if self.closed:return
        self.closed=True;self.axes=dict.fromkeys(self.axes,0.)
        self.controller.hold()
        self.cargo.close()
        if self.private_view:self.private_view.close()
        if getattr(builtins,'_smartport_sts_driver',None) is self:builtins._smartport_sts_driver=None

def execute_sts_driver(panel,stage,action,owner,session,payload):
    driver=active_sts_driver()
    if driver and driver.stage!=stage:driver.close();driver=None
    if action=='acquire':
        if stage is None or not stage.GetPrimAtPath(HOIST):raise ValueError('STS_01场景尚未加载')
        if driver and driver.owner!=owner:raise ValueError('STS_01已由另一驾驶会话占用')
        if not driver:
            from pathlib import Path
            from smartport.agent_panel.scene_runtime import SceneRuntime
            runtime=getattr(panel,'_runtime',None)
            if runtime is None:runtime=SceneRuntime(Path(__file__).resolve().parents[2]);panel._runtime=runtime
            runtime.bind(stage);runtime._guard_manual('sts',CRANE,allow_truck_driver=True,allow_sts_driver=True)
            session['driver_released']=False;driver=STSDriver(stage,owner,session)
        driver.last_seen=driver.clock();return driver.state()
    if not driver:return {'status':'idle'}
    if driver.owner!=owner:raise ValueError('STS_01已由另一驾驶会话占用')
    if action=='release':driver.close();return {'status':'released'}
    if action=='state':driver.last_seen=driver.clock();return driver.state()
    if action=='input':return driver.command(owner,payload)
    raise ValueError('未知STS驾驶操作')
