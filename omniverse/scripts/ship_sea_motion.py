"""Reversible, visual moored-ship response to a shared animated wave field."""
import math
import builtins
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

SEA='/World/PortAndRTG/CODEx_showcase_inside_circular_sea_surface/CODEx_showcase_inside_circular_sea_surface_mesh_001'
SHIPS=('/World/PortAndRTG/SMART_PORT_4C_FEEDER_SHIP_LEFT_IMPORTED',
       '/World/PortAndRTG/SMART_PORT_4C_FEEDER_SHIP_RIGHT_IMPORTED',
       '/World/PortAndRTG/SMART_PORT_LOCAL_SHIP_4c40_shoreline_east_IMPORTED',
       '/World/PortAndRTG/SMART_PORT_LOCAL_SHIP_4c40_shoreline_west_IMPORTED',
       '/World/PortAndRTG/SMART_PORT_REMOTE_CARGO_SHIP_01',
       '/World/PortAndRTG/SMART_PORT_REMOTE_CARGO_SHIP_02')
# The visible double-20 cargo is authored as a separate world-level layer,
# not below the east feeder ship.  It must therefore receive the same motion
# transform explicitly instead of relying on USD parent inheritance.
COMPANION_ROOTS=(('/World/ShipDouble20YardModelLayer', SHIPS[2]),)

def tr(v):
    return Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(*v))

def wave(x,y,t):
    """Shared height field and analytic slopes, in scene units."""
    z=dx=dy=0.
    for a,kx,ky,w,phase in ((.09,.055,.022,.72,0),(.045,-.025,.065,1.03,1.2)):
        p=kx*x+ky*y-w*t+phase
        z+=a*math.sin(p)
        dx+=a*kx*math.cos(p)
        dy+=a*ky*math.cos(p)
    return z,dx,dy

class SeaMotion:
    def __init__(self,stage):
        self.stage=stage
        self.layer=Sdf.Layer.CreateAnonymous('moored_ship_waves.usda')
        self.time=0.
        self.strength=1.
        self._enabled=True
        # Three batches: each ship is written at 12 Hz, not every render frame.
        self.batch_count=3
        self.batch_index=0
        self.update_interval=1./36.
        self.uv_interval=1./6.
        self._ship_factors={}
        self._update_elapsed=0.
        self._uv_elapsed=0.
        self._neutral=False
        self.held=set()
        self.ships={}
        self.companions={}
        self._companions_by_source={}
        self.closed=False
        self.sub=None
        self._subscribe=None
        self.stage_sub=None
        self.window=None
        stage.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        try:
            with Usd.EditContext(stage,self.layer):
                sea=stage.GetPrimAtPath(SEA)
                self.mesh=UsdGeom.Mesh(sea) if sea else None
                self.water_world=[]
                self.uv_translation=None
                uv=UsdShade.Shader(stage.GetPrimAtPath('/World/Environment/Looks/HarborWater/UVTransform'))
                if uv:
                    self.uv_translation=uv.CreateInput('translation',Sdf.ValueTypeNames.Float2)
                    self.uv_origin=self.uv_translation.Get() or Gf.Vec2f(0)
                cache=UsdGeom.XformCache()
                if self.mesh:
                    self.water_matrix=cache.GetLocalToWorldTransform(sea)
                    self.water_inv=self.water_matrix.GetInverse()
                    self.water_world=[self.water_matrix.Transform(Gf.Vec3d(p)) for p in self.mesh.GetPointsAttr().Get()]
                sea_z=self.water_world[0][2] if self.water_world else -4.9478
                for path in SHIPS:
                    prim=stage.GetPrimAtPath(path)
                    if not prim:
                        continue
                    xf=UsdGeom.Xformable(prim)
                    world=cache.GetLocalToWorldTransform(prim)
                    parent=cache.GetLocalToWorldTransform(prim.GetParent())
                    center=world.Transform(Gf.Vec3d(0,0,0))
                    pivot=Gf.Vec3d(center[0],center[1],sea_z)
                    ops=xf.GetOrderedXformOps()
                    op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'seaResponse')
                    op.Set(Gf.Matrix4d(1))
                    xf.SetXformOpOrder([op]+ops)
                    self.ships[path]=(op,parent,pivot)
                    self._ship_factors[path]=(parent*tr(-pivot),parent.GetInverse())
                for companion_path,source_path in COMPANION_ROOTS:
                    prim=stage.GetPrimAtPath(companion_path)
                    source=self.ships.get(source_path)
                    if not prim or source is None:
                        continue
                    xf=UsdGeom.Xformable(prim)
                    parent=cache.GetLocalToWorldTransform(prim.GetParent())
                    pivot=source[2]
                    ops=xf.GetOrderedXformOps()
                    op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'seaResponse')
                    op.Set(Gf.Matrix4d(1))
                    xf.SetXformOpOrder([op]+ops)
                    factor=(parent*tr(-pivot),parent.GetInverse())
                    self.companions[companion_path]=(op,parent,pivot)
                    self._companions_by_source.setdefault(source_path,[]).append(
                        (op,factor,pivot)
                    )
        except Exception:
            self.close()
            raise

    def hold(self,path):
        self.held.add(path)
        if path in self.ships and self.enabled:
            with Usd.EditContext(self.stage,self.layer):
                self.ships[path][0].Set(Gf.Matrix4d(1))

    def release(self,path):
        self.held.discard(path)

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self,value):
        value=bool(value)
        if value==self._enabled or self.closed:
            return
        self._enabled=value
        layers=self.stage.GetSessionLayer().subLayerPaths
        if not value:
            self.sub=None
            # Identity matrices still leave authored dynamic prims/material
            # opinions in the composed scene. Remove the entire override layer.
            if self.layer.identifier in layers:
                layers.remove(self.layer.identifier)
            self._neutral=True
            return
        # Clear stale poses while detached, then restore the neutral layer.
        detached=Usd.Stage.Open(self.layer)
        for path in (*self.ships, *self.companions):
            detached.GetPrimAtPath(path).GetAttribute('xformOp:transform:seaResponse').Set(Gf.Matrix4d(1))
        if self.uv_translation:
            attr=detached.GetAttributeAtPath(self.uv_translation.GetAttr().GetPath())
            if attr:
                attr.Set(self.uv_origin)
        if self.layer.identifier not in layers:
            layers.insert(0,self.layer.identifier)
        self.time=0.
        self._update_elapsed=0.
        self._uv_elapsed=0.
        self._neutral=False
        if self._subscribe is not None:
            self.sub=self._subscribe()

    def tick(self,dt):
        if self.closed or not self.enabled:
            return
        dt=max(0.,min(float(dt),.1))
        active=self.enabled and self.strength>0.
        if not active and self._neutral:
            return
        self.time+=dt
        self._update_elapsed+=dt
        self._uv_elapsed+=dt
        if active and self._update_elapsed+1e-9<self.update_interval:
            return
        self._update_elapsed%=self.update_interval
        # Ease in from neutral when enabled, avoiding an initial teleport.
        gain=self.strength*min(1.,self.time/3.) if self.enabled else 0.
        with Usd.EditContext(self.stage,self.layer):
            if self.uv_translation and (self._uv_elapsed>=self.uv_interval or not active):
                drift=self.time*self.strength if self.enabled else 0.
                self.uv_translation.Set(self.uv_origin+Gf.Vec2f(.007*drift,.0025*drift))
                self._uv_elapsed=0.
            for index,(path,(op,parent,pivot)) in enumerate(self.ships.items()):
                if path in self.held:
                    continue
                if active and index%self.batch_count!=self.batch_index:
                    continue
                z,dx,dy=wave(pivot[0],pivot[1],self.time)
                rx=math.degrees(math.atan(dy))*gain
                ry=-math.degrees(math.atan(dx))*gain
                rot=(Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(1,0,0),rx))*
                     Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,1,0),ry)))
                left,right=self._ship_factors[path]
                op.Set(left*rot*tr(pivot+Gf.Vec3d(0,0,z*gain))*right)
                for companion_op,(comp_left,comp_right),comp_pivot in self._companions_by_source.get(path,[]):
                    companion_op.Set(
                        comp_left*rot*tr(comp_pivot+Gf.Vec3d(0,0,z*gain))*comp_right
                    )
            # Keep the large sea mesh static. Scrolling normal/roughness maps
            # convey ripples without per-frame geometry/RTX invalidation.
        self._neutral=not active
        self.batch_index=(self.batch_index+1)%self.batch_count

    def set_quality(self, smooth):
        self.update_interval=1./((30. if smooth else 12.)*self.batch_count)
        self.uv_interval=1./(15. if smooth else 6.)

    def close(self):
        self.closed=True
        self.sub=None
        self._subscribe=None
        self.stage_sub=None
        layers=self.stage.GetSessionLayer().subLayerPaths
        if self.layer.identifier in layers:
            layers.remove(self.layer.identifier)

def start_sea_motion(stage=None):
    """Start the sea controller without opening a control window.

    The motion is intentionally a runtime/session-layer effect rather than a
    destructive edit to the authored USD scene.  Keeping this small startup
    entry point separate from :func:`show_sea_motion` lets the project
    launcher restore the previously demonstrated motion automatically while
    leaving the optional controls available in Script Editor.
    """
    import omni.usd
    import omni.kit.app

    old=getattr(builtins,'_smart_port_sea_motion',None)
    if old:
        old.close()
        if old.window:
            old.window.destroy()
    stage=stage or omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError('Open smart_port.usda first')
    motion=SeaMotion(stage)
    builtins._smart_port_sea_motion=motion
    panel=getattr(builtins,'_smart_port_sts_panel',None)
    if panel and panel.unload_task:
        motion.hold(SHIPS[0])

    def update(event):
        if omni.usd.get_context().get_stage()!=stage:
            motion.close()
            return
        motion.tick(event.payload.get('dt',0.))

    stream=omni.kit.app.get_app().get_update_event_stream()
    motion._subscribe=lambda:stream.create_subscription_to_pop(update)
    motion.sub=motion._subscribe()
    return motion

def show_sea_motion():
    import omni.ui as ui

    motion=start_sea_motion()
    motion.window=ui.Window('Harbor Sea Motion',width=390,height=235)
    with motion.window.frame:
        with ui.VStack(spacing=8):
            quality=ui.Label('Eco: ships 12 Hz / ripples 6 Hz')
            def set_quality(smooth):
                motion.set_quality(smooth)
                quality.text=('Smooth: ships up to 30 Hz / ripples 15 Hz' if smooth
                              else 'Eco: ships 12 Hz / ripples 6 Hz')
            with ui.HStack(height=26):
                ui.Button('Eco',clicked_fn=lambda:set_quality(False))
                ui.Button('Smooth',clicked_fn=lambda:set_quality(True))
            with ui.HStack(height=25):
                ui.Label('Wave strength',width=130)
                slider=ui.FloatSlider(min=0.,max=2.)
                slider.model.set_value(1.)
                slider.model.add_value_changed_fn(lambda m:setattr(motion,'strength',m.as_float))
            def toggle():
                motion.enabled=not motion.enabled
                toggle_button.text='Waves OFF - original scene restored' if not motion.enabled else 'Waves ON - click to disable'
            toggle_button=ui.Button('Waves ON - click to disable',clicked_fn=toggle,height=28)
            ui.Label('Unload task: working ship held steady.\nClose window: restore original sea and ships.',word_wrap=True)
    motion.window.set_visibility_changed_fn(lambda visible:None if visible else motion.close())
    return motion

def stop_sea_motion():
    """Completely tear down the controller and only its own session layers."""
    import omni.usd
    old=getattr(builtins,'_smart_port_sea_motion',None)
    if old:
        old.close()
        if old.window:
            old.window.destroy()
            old.window=None
        builtins._smart_port_sea_motion=None
    stage=omni.usd.get_context().get_stage()
    removed=[]
    if stage:
        session=stage.GetSessionLayer()
        for identifier in list(session.subLayerPaths):
            if identifier.startswith('anon:') and identifier.endswith(':moored_ship_waves.usda'):
                session.subLayerPaths.remove(identifier)
                removed.append(identifier)
    print('Sea motion fully stopped: controller, update callback and window removed; extra sea layers:',len(removed))

if __name__=='__main__':
    show_sea_motion()
