"""Independent RTG operator lease, private cameras and bounded manual axes."""
import builtins,math,time
from pxr import Gf,Sdf,Usd,UsdGeom
from rtg_live_control import RTGController,TROLLEY_PATH,HOIST_PATH
from rtg_spreader_trim import SpreaderTrim,neutral_world_transform
from rtg_driver_cargo import DriverCargo
from rtg_transfer_motion import read_spreader_size_ft

CAMERA='/World/RTGDriverDemo/Camera'
ROLL_OP='xformOp:transform:rtgDriverRoll'
ROLL_ATTR='smartport:rtgDriverRollDeg'
VIEWS=('under','side','end')
HOIST_SPEED_M_S=5.0


def active_rtg_driver(stage=None):
    item=getattr(builtins,'_smartport_rtg_driver',None)
    return item if item and not item.closed and (stage is None or item.stage==stage) else None


class RTGDriver:
    def __init__(self,stage,owner,session,clock=time.monotonic,view=None):
        if active_rtg_driver():raise ValueError('RTG已由另一驾驶会话占用')
        self.stage=stage;self.owner=owner;self.session=session;self.clock=clock;self.closed=False
        self.last_seen=self.last_input=clock();self.last_seq=-1
        self.axes=dict(gantry=0.,trolley=0.,hoist=0.,roll=0.,yaw=0.)
        self.braking=False;self.returning=False;self.size_target=None;self.notice='';self.notice_until=0.
        self.view='under';self.fov=140.;self.controller=RTGController(stage)
        self.trim=SpreaderTrim(stage,HOIST_PATH)
        self.hoist=stage.GetPrimAtPath(HOIST_PATH)
        attr=self.hoist.GetAttribute(ROLL_ATTR);self.roll=float(attr.Get() or 0) if attr else 0.
        self.cargo=DriverCargo(stage,self.controller)
        self.private_view=view
        try:
            if view is None:
                from role_viewport import DriverViewport
                self.private_view=DriverViewport(stage,CAMERA,'smartport_rtg_driver','SmartPortRTGDriverTexture',49240,48008)
            self.camera_stage=self.private_view.stage
            with Usd.EditContext(self.camera_stage,self.camera_stage.GetSessionLayer()):
                self.camera=UsdGeom.Camera.Define(self.camera_stage,CAMERA)
                self.camera.CreateClippingRangeAttr(Gf.Vec2f(.025,3000))
                self.camera.CreateHorizontalApertureAttr(36)
                self.camera.CreateVerticalApertureAttr(20.25)
                self.camera_op=self.camera.AddTransformOp()
                self.apply_camera()
            builtins._smartport_rtg_driver=self
        except Exception:self.close();raise

    def command(self,owner,payload):
        if owner!=self.owner:raise ValueError('RTG已由另一驾驶会话占用')
        if set(payload)-{'gantry','trolley','hoist','roll','yaw','brake','view','fov','seq','size_ft','lock','zero'}:raise ValueError('RTG驾驶指令包含未知参数')
        seq=payload.get('seq',self.last_seq+1)
        if type(seq) is not int or not 0<=seq<2**53:raise ValueError('驾驶指令序号无效')
        if seq<=self.last_seq:return self.state()
        axes={k:payload.get(k,0.) for k in self.axes}
        for value in axes.values():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -1<=value<=1:
                raise ValueError('RTG轴输入须为-1到1的有限数字')
        if type(payload.get('brake',False)) is not bool:raise ValueError('制动参数须为布尔值')
        view=payload.get('view',self.view);fov=payload.get('fov',self.fov)
        if view not in VIEWS:raise ValueError('无效RTG视角')
        if isinstance(fov,bool) or not isinstance(fov,(float,int)) or not math.isfinite(fov) or not 40<=fov<=140:
            raise ValueError('视野角度须在40°到140°之间')
        if 'size_ft' in payload and (type(payload['size_ft']) is not int or payload['size_ft'] not in (20,40)):raise ValueError('吊具尺型只能为20或40尺')
        for name in ('lock','zero'):
            if name in payload and type(payload[name]) is not bool:raise ValueError('锁具和回零指令须为布尔值')
        if sum(name in payload for name in ('size_ft','lock','zero'))>1:raise ValueError('请逐项操作尺型、锁具或回零')
        self.axes={k:0. if payload.get('brake') else float(v) for k,v in axes.items()}
        if self.axes['yaw'] or self.axes['roll']:self.returning=False
        self.view=view;self.fov=float(fov);self.last_seq=seq
        self.last_input=self.last_seen=self.clock();self.braking=payload.get('brake',False)
        if self.braking:self.returning=False;self.size_target=None
        else:
            try:
                if 'size_ft' in payload:
                    if self.cargo.locked:raise ValueError('请先落箱开锁，再切换20/40尺型')
                    if any(self.axes.values()) or self.returning:raise ValueError('请先停止RTG，再切换吊具尺型')
                    self.size_target=payload['size_ft']
                if 'lock' in payload:
                    if any(self.axes.values()):raise ValueError('请先停止RTG，再操作开锁或闭锁')
                    if not self.spreader_ready():raise ValueError('吊具伸缩尚未到位，不能操作锁具')
                    self.cargo.set_lock(payload['lock']);self.say('已闭锁，箱子随吊具移动' if self.cargo.locked else '已开锁，箱子保持落稳位置')
                if payload.get('zero'):self.returning=True
            except ValueError as error:
                self.axes=dict.fromkeys(self.axes,0.);self.say(str(error));raise
        if self.cargo.locked and self.axes['gantry']:
            self.axes['gantry']=0.;self.say('载箱闭锁时禁止大车移动；可操作小车和起升')
        self.apply_camera()
        return self.state()

    def say(self,message):self.notice=message;self.notice_until=self.clock()+8

    def spreader_ready(self):
        size=read_spreader_size_ft(self.stage)
        return size is not None and any(abs(size-end)<1e-4 for end in (20,40)) and (self.size_target is None or abs(size-self.size_target)<1e-4)

    def apply_roll(self):
        # Bind the original rope endpoints before introducing the first tilt.
        if self.controller._rope_binding is None:self.controller.update_ropes()
        neutral=neutral_world_transform(self.hoist,ROLL_OP)
        pivot=neutral.Transform(self.trim.pivot)
        axis=neutral.TransformDir(Gf.Vec3d(0,1,0)).GetNormalized()
        rigid=(Gf.Matrix4d(1).SetTranslate(-pivot)*Gf.Matrix4d(1).SetRotate(Gf.Rotation(axis,self.roll))
               *Gf.Matrix4d(1).SetTranslate(pivot))
        with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
            attr=self.hoist.GetAttribute(ROLL_OP)
            if not attr:attr=UsdGeom.Xformable(self.hoist).AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'rtgDriverRoll').GetAttr()
            attr.Set(neutral*rigid*neutral.GetInverse())
            self.hoist.CreateAttribute(ROLL_ATTR,Sdf.ValueTypeNames.Double).Set(self.roll)
        self.controller.update_ropes()

    def apply_camera(self):
        transforms=UsdGeom.XformCache()
        trolley=transforms.GetLocalToWorldTransform(self.stage.GetPrimAtPath(TROLLEY_PATH))
        up=trolley.TransformDir(Gf.Vec3d(1,0,0));up=Gf.Vec3d(up[0],up[1],0).GetNormalized()
        if self.view=='under':
            eye=trolley.Transform(Gf.Vec3d(2.55,-5.,8.))
            target=eye-Gf.Vec3d(0,0,10)
        else:
            contact=transforms.GetLocalToWorldTransform(self.hoist).Transform(self.trim.pivot)
            offset=trolley.TransformDir(Gf.Vec3d(1,0,0) if self.view=='side' else Gf.Vec3d(0,1,0))
            offset=Gf.Vec3d(offset[0],offset[1],0).GetNormalized()
            eye=contact+offset*7+Gf.Vec3d(0,0,1.8);target=contact-Gf.Vec3d(0,0,.3);up=Gf.Vec3d(0,0,1)
        lens=self.fov
        with Usd.EditContext(self.camera_stage,self.camera_stage.GetSessionLayer()):
            self.camera.CreateFocalLengthAttr(36/(2*math.tan(math.radians(lens)/2)))
            self.camera_op.Set(Gf.Matrix4d(1).SetLookAt(eye,target,up).GetInverse())
        self.private_view.camera_path=CAMERA

    def tick(self,dt):
        if self.closed:return
        now=self.clock()
        if self.session.get('driver_released') or self.session.get('expires',0)<=time.time() or now-self.last_seen>10:
            self.close();return
        if now-self.last_input>.35:
            self.axes=dict.fromkeys(self.axes,0.);self.returning=False;self.size_target=None
        dt=max(0.,min(.1,float(dt)));gantry,trolley,hoist=self.controller.get_positions()
        if self.size_target is not None and not self.braking:
            self.controller.set_spreader_size(self.size_target,dt_s=dt)
            if not self.spreader_ready():self.axes=dict.fromkeys(self.axes,0.)
            else:self.size_target=None
        moved=any(self.axes.values())
        if self.cargo.locked:self.axes['gantry']=0.
        if self.axes['gantry']:self.controller.set_gantry_safe(gantry+self.axes['gantry']*2*dt)
        if self.axes['trolley']:self.controller.set_trolley_safe(trolley+self.axes['trolley']*1.2*dt)
        if self.axes['hoist']:
            target=hoist+self.axes['hoist']*HOIST_SPEED_M_S*dt
            if self.axes['hoist']<0:
                target,contact=self.cargo.limit_descent(hoist,target,now)
                if contact:self.say(contact)
            self.controller.set_hoist_safe(target)
        if self.axes['roll']:self.roll=max(-5.,min(5.,self.roll+self.axes['roll']*1.5*dt))
        yaw,shift=self.controller.get_spreader_trim()
        next_yaw=max(-5.,min(5.,yaw+self.axes['yaw']*dt))
        if self.returning:
            next_yaw=yaw-max(-dt,min(dt,yaw));self.roll-=max(-1.5*dt,min(1.5*dt,self.roll))
            moved=True
            if abs(next_yaw)<1e-6 and abs(self.roll)<1e-6:self.returning=False
        if next_yaw!=yaw:self.controller.set_spreader_trim(next_yaw,shift)
        if moved and (self.roll or self.hoist.GetAttribute(ROLL_OP)):self.apply_roll()
        self.cargo.update()
        self.apply_camera();self.private_view.tick()

    def state(self):
        g,t,h=self.controller.get_positions()
        return dict(status='closed' if self.closed else 'driving',vehicle_id='RTG_PRIMARY',view=self.view,
                    fov=self.fov,gantry_m=g,trolley_m=t,hoist_m=h,roll_deg=self.roll,input_seq=self.last_seq,
                    yaw_deg=self.controller.get_spreader_trim()[0],returning_zero=self.returning,
                    spreader_size_ft=read_spreader_size_ft(self.stage),spreader_target_ft=self.size_target,
                    spreader_ready=self.spreader_ready(),locked=self.cargo.locked,carried_container=self.cargo.held,
                    notice=self.notice if self.clock()<self.notice_until else '',
                    stream_ready=self.private_view.ready(),simulation_only=True)

    def close(self):
        if self.closed:return
        self.closed=True;self.axes=dict.fromkeys(self.axes,0.)
        if self.private_view:self.private_view.close()
        if getattr(builtins,'_smartport_rtg_driver',None) is self:builtins._smartport_rtg_driver=None


def execute_rtg_driver(panel,stage,action,owner,session,payload):
    driver=active_rtg_driver()
    if driver and driver.stage!=stage:driver.close();driver=None
    if action=='acquire':
        if stage is None or not stage.GetPrimAtPath(HOIST_PATH):raise ValueError('RTG场景尚未加载，请稍后重新进入')
        if driver and driver.owner!=owner:raise ValueError('RTG已由另一驾驶会话占用')
        if not driver:
            from pathlib import Path
            from smartport.agent_panel.scene_runtime import SceneRuntime
            runtime=getattr(panel,'_runtime',None)
            if runtime is None:runtime=SceneRuntime(Path(__file__).resolve().parents[2]);panel._runtime=runtime
            runtime.bind(stage)
            if runtime.motion:raise ValueError('请先结束正在执行的RTG自动移动')
            runtime._guard_manual('rtg',allow_truck_driver=True)
            session['driver_released']=False;driver=RTGDriver(stage,owner,session)
        driver.last_seen=driver.clock();return driver.state()
    if not driver:return {'status':'idle'}
    if driver.owner!=owner:raise ValueError('RTG已由另一驾驶会话占用')
    if action=='release':driver.close();return {'status':'released'}
    if action=='state':driver.last_seen=driver.clock();return driver.state()
    if action=='input':return driver.command(owner,payload)
    raise ValueError('未知RTG驾驶操作')
