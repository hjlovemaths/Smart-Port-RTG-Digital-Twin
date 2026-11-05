"""Reversible kinematic STS -> parked truck -> RTG transfer, without PLC writes."""
import builtins,math
from pxr import Gf,Sdf,Usd,UsdGeom
from ship_transfer_plan import box,solve,PARK
from sts_yard_delivery import rounded_route
from sts_unload_task import UnloadTask
from large_vessel_layout import SHIP
from rtg_transfer_motion import VisualRTG
ROOT='/World/ShipToYardTransfer'
def tr(v):return Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(v))
def task_op(prim,name='shipTransfer'):
 xf=UsdGeom.Xformable(prim);op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,name)
 return xf,op

def active_task(stage):
 t=getattr(builtins,'_smartport_ship_transfer_task',None)
 return t if t and t.stage==stage and not t.closed else None

class ShipToYardTransfer:
 def __init__(self,stage,controller,plan,allow_display_normalization=True):
  driver=getattr(builtins,'_smartport_truck_driver',None)
  if driver and not driver.closed:raise ValueError('驾驶员正在使用01号集卡，请先退出驾驶模式')
  rtg_driver=getattr(builtins,'_smartport_rtg_driver',None)
  if rtg_driver and not rtg_driver.closed:raise ValueError('RTG司机正在操作设备，请先退出RTG驾驶模式')
  sts_driver=getattr(builtins,'_smartport_sts_driver',None)
  if sts_driver and not sts_driver.closed and sts_driver.controller.path==controller.path:raise ValueError('STS司机正在操作岸桥，请先退出驾驶模式')
  sts_held=stage.GetPrimAtPath(controller.path+'/Gantry/Trolley/Hoist').GetAttribute('smartport:stsDriverHeldCargo')
  if sts_held and sts_held.Get():raise ValueError('岸桥仍有闭锁箱子，请先落稳开锁')
  from rtg_live_control import HOIST_PATH
  held=stage.GetPrimAtPath(HOIST_PATH).GetAttribute('smartport:rtgDriverHeldCargo')
  if held and held.Get():raise ValueError('RTG仍有闭锁箱子，请由RTG司机落稳开锁后再启动搬运演示')
  if active_task(stage):raise ValueError('已有船舶与堆场搬运演示，请先查询或复位')
  if plan['display_normalization_required'] and not allow_display_normalization:raise ValueError('源箱与堆场比例不一致，需允许任务层统一比例')
  self.stage=stage;self.c=controller;self.plan=plan;self.layer=Sdf.Layer.CreateAnonymous('ship_to_yard_transfer.usda')
  self.home=dict(controller.state);self.home_size=controller.spreader_size_ft
  self.closed=False;self.finished=False;self.paused=False;self.error=None;self.elapsed=0.;self.wait=0.;self.attached=None;self.cargo_on_truck=False
  self.phase=getattr(self,'initial_phase','停车场集卡出车');self.history=[self.phase];self.status='running'
  self.sea=getattr(builtins,'_smart_port_sea_motion',None);self.held_by_us=False
  stage.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
  try:
   if self.sea and not self.sea.closed and self.sea.stage==stage and SHIP not in self.sea.held:self.sea.hold(SHIP);self.held_by_us=True
   with Usd.EditContext(stage,self.layer):self.prepare()
   self.spreader_initial_size_ft=self.rtg.current_size_ft
   self.spreader_preparing=not self.rtg.spreader_ready
   self.transport_initial_phase=self.phase
   if self.spreader_preparing:self.change('RTG吊具切换至'+str(plan.get('rtg_size_ft',plan['size_ft']))+'尺')
   builtins._smartport_ship_transfer_task=self
  except Exception:self.close();raise
 def prepare(self):
  p=self.plan;dims=Gf.Vec3d(p['cargo_dimensions_m']);self.height=dims[2];self.c.set_spreader_size(p['size_ft'])
  UsdGeom.Xform.Define(self.stage,ROOT)
  for i,path in enumerate(p['column_paths'] if p['display_normalization_required'] else []):
   prim=self.stage.GetPrimAtPath(path);b=box(self.stage,path);world=UsdGeom.XformCache().GetLocalToWorldTransform(prim)
   center=Gf.Vec3d(b.GetMidpoint()[0],b.GetMidpoint()[1],p['source_column_floor_z']+(i+.5)*self.height)
   delta=tr(-b.GetMidpoint())*Gf.Matrix4d(1).SetScale(Gf.Vec3d(*(dims[j]/b.GetSize()[j] for j in range(3))))*tr(center)
   xf,op=task_op(prim);xf.SetXformOpOrder([op],resetXformStack=True);op.Set(world*delta)
  cargo=self.stage.GetPrimAtPath(p['source_path'])
  if not p['display_normalization_required']:
   world=UsdGeom.XformCache().GetLocalToWorldTransform(cargo);xf,op=task_op(cargo);xf.SetXformOpOrder([op],resetXformStack=True);op.Set(world)
  self.cargo_op=UsdGeom.Xformable(cargo).GetOrderedXformOps()[0]
  self.cargo_world=UsdGeom.XformCache().GetLocalToWorldTransform(cargo);self.cargo_origin=box(self.stage,cargo.GetPath()).GetMidpoint()
  self.truck=UsdGeom.Xform.Define(self.stage,ROOT+'/Truck').GetPrim();self.truck.GetReferences().AddInternalReference(PARK);self.truck.SetInstanceable(False)
  UsdGeom.Imageable(self.truck).CreateVisibilityAttr().Set('inherited')
  park=self.stage.GetPrimAtPath(PARK);self.truck_world=UsdGeom.XformCache().GetLocalToWorldTransform(park)
  self.deck=Gf.Range3d()
  for q in Usd.PrimRange(park,Usd.TraverseInstanceProxies()):
   if q.IsA(UsdGeom.Boundable) and 'trailer_side_rail' in q.GetName():self.deck.UnionWith(box(self.stage,q.GetPath()))
  if self.deck.IsEmpty():raise ValueError('停车场集卡缺少可测量承载面')
  self.pivot=Gf.Vec3d(self.deck.GetMidpoint()[0],self.deck.GetMidpoint()[1],self.deck.GetMax()[2])
  xf,self.truck_op=task_op(self.truck);xf.SetXformOpOrder([self.truck_op],resetXformStack=True)
  UsdGeom.Imageable(park).CreateVisibilityAttr().Set('invisible')
  label=self.stage.GetPrimAtPath('/World/ParkedTruckNumbers/Truck01')
  if label:UsdGeom.Imageable(label).CreateVisibilityAttr().Set('invisible')
  self.truck.GetPrim().CreateAttribute('vehicle:number',Sdf.ValueTypeNames.String).Set('01')
  self.truck_point=Gf.Vec3d(self.pivot);self.truck_heading=0.;self.set_route(p['truck_route_empty']);self.route[0]=Gf.Vec3d(self.pivot)
  self.set_truck(self.pivot,0)
  self.rtg=VisualRTG(self.stage,p.get('rtg_size_ft',p['size_ft']));self.rtg_high=max(p['yard_clear_z'],self.rtg.origin[2])
  self.pick=Gf.Vec3d(p['source_pick_m']);self.stop=Gf.Vec3d(p['truck_stop_m']);self.stop[2]=self.pivot[2]
  self.place=self.stop+Gf.Vec3d(0,0,self.height);self.target=Gf.Vec3d(p['target_center_m'])+Gf.Vec3d(0,0,self.height/2)
  self.yard_stop=Gf.Vec3d(p['truck_route_loaded'][-1]);self.yard_stop[1]+=p['truck_load_offset_m'][0];self.yard_stop[2]=self.pivot[2]
  self.yard_pick=self.yard_stop+Gf.Vec3d(0,0,self.height)
  # Validate every upcoming pose before the first simulation tick.
  for point in (self.pick,self.place,Gf.Vec3d(self.pick[0],self.pick[1],25),Gf.Vec3d(self.place[0],self.place[1],25)):solve(self.stage,self.c,point)
  for point in (self.yard_pick,self.target,Gf.Vec3d(self.yard_pick[0],self.yard_pick[1],self.rtg_high),Gf.Vec3d(self.target[0],self.target[1],self.rtg_high)):self.rtg.command(point)
  self.rtg.command(self.rtg.origin);self.go_sts(Gf.Vec3d(self.pick[0],self.pick[1],25))
 def set_route(self,points):self.route=rounded_route(points,radius=3);self.route_index=1
 def set_truck(self,point,heading):
  delta=tr(-self.pivot)*Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),heading))*tr(point)
  self.truck_op.Set(self.truck_world*delta);self.truck_point=Gf.Vec3d(point);self.truck_heading=heading
 def drive(self,dt,reverse=False):
  remaining=dt*(2. if reverse else 6.)
  while remaining>1e-9 and self.route_index<len(self.route):
   d=self.route[self.route_index]-self.truck_point;length=d.GetLength()
   if length<1e-7:self.route_index+=1;continue
   step=min(length,remaining);heading=math.degrees(math.atan2(d[1],d[0]))-(0 if reverse else 180)
   self.set_truck(self.truck_point+d/length*step,heading);remaining-=step
   if length-step<1e-7:self.route_index+=1
  return self.route_index==len(self.route)
 def truck_load_center(self):
  offset=Gf.Rotation(Gf.Vec3d(0,0,1),self.truck_heading).TransformDir(Gf.Vec3d(self.plan['truck_load_offset_m']))
  return self.truck_point+offset+Gf.Vec3d(0,0,self.height/2)
 def begin_return(self):
  self.delivery_done=True;self.change('集卡空车返回停车场');self.set_route(self.plan['truck_route_return'])
 def tick_return(self,dt):
  if self.phase=='集卡空车返回停车场':
   if self.drive(dt):self.change('集卡倒车归位');self.set_route(self.plan['truck_route_parking'])
  elif self.phase=='集卡倒车归位' and self.drive(dt,reverse=True):
   self.set_truck(self.pivot,0);self.finished=True;self.status='completed'
   text=('装船至'+self.plan['target_location'] if self.plan.get('direction')=='yard_to_ship' else self.plan['target_bay']+' 第'+str(self.plan['target_row'])+'排第'+str(self.plan['target_tier'])+'层')
   self.change('完成：'+text+'；集卡已返回原停车位')
 def set_cargo(self,center,heading=0):
  self.cargo_op.Set(self.cargo_world*tr(-self.cargo_origin)*Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),heading))*tr(center))
 def sts_contact(self):
  helper=UnloadTask.__new__(UnloadTask);helper.c=self.c;helper.stage=self.stage;return helper.contact()
 def command_sts(self,pose):
  self.active_sts_target=dict(pose)
  self._commanding=True
  try:self.c.command(**pose)
  finally:self._commanding=False
 def go_sts(self,point):self.command_sts(solve(self.stage,self.c,point))
 def change(self,phase):self.phase=phase;self.wait=0.;self.history.append(phase)
 def spreader_gate(self,dt):
  if self.spreader_preparing:
   if self.rtg.tick_spreader(dt):self.spreader_preparing=False;self.change(self.transport_initial_phase)
   elif self.wait>10:raise ValueError('RTG吊具实际姿态未到位，拒绝下降取箱')
   return False
  if not self.rtg.spreader_ready:raise ValueError('RTG吊具尺型或实际姿态发生变化，拒绝继续取箱')
  return True
 def check_grip_size(self,owner):
  if owner=='rtg' and not self.rtg.spreader_ready:raise ValueError('RTG吊具尚未匹配源箱尺型，不能闭锁')
  if owner=='sts' and self.c.spreader_size_ft!=self.plan['size_ft']:raise ValueError('岸桥吊具尺型不匹配，不能闭锁')
 def lock(self,owner):
  self.check_grip_size(owner)
  self.attached=owner
  self.stage.GetPrimAtPath(self.plan['source_path']).CreateAttribute('transfer:lockedTo',Sdf.ValueTypeNames.String).Set(owner or '')
 def tick(self,dt):
  if self.closed or self.finished:return
  if self.c.stopped:
   self.pause();return
  if self.paused:return
  dt=max(0,min(float(dt),.1));self.elapsed+=dt;self.wait+=dt
  with Usd.EditContext(self.stage,self.layer):
   if not self.spreader_gate(dt):return
   self.c.tick(dt);rtg_ready=self.rtg.tick(dt);sts_ready=all(abs(self.c.state[a]-v)<1e-4 for a,v in self.c.targets.items())
   if self.phase=='停车场集卡出车':
    arrived=self.drive(dt)
    if arrived and sts_ready:self.change('岸桥吊具下降到船箱');self.go_sts(self.pick)
   elif self.phase=='岸桥吊具下降到船箱' and sts_ready:self.change('岸桥对位闭锁')
   elif self.phase=='岸桥对位闭锁' and self.wait>=1:
    self.lock('sts');self.change('岸桥起升船箱');self.go_sts(Gf.Vec3d(self.pick[0],self.pick[1],25))
   elif self.phase=='岸桥起升船箱' and sts_ready:self.change('岸桥小车移向集卡');self.go_sts(Gf.Vec3d(self.place[0],self.place[1],25))
   elif self.phase=='岸桥小车移向集卡' and sts_ready:self.change('岸桥落箱到集卡');self.go_sts(self.place)
   elif self.phase=='岸桥落箱到集卡' and sts_ready:self.change('岸桥开锁交接')
   elif self.phase=='岸桥开锁交接' and self.wait>=1:
    self.lock(None);self.cargo_on_truck=True;self.change('岸桥吊具抬离集卡');self.go_sts(Gf.Vec3d(self.place[0],self.place[1],25))
   elif self.phase=='岸桥吊具抬离集卡' and sts_ready:
    self.change('集卡经引桥运往堆场');self.set_route(self.plan['truck_route_loaded']);self.route[0]=Gf.Vec3d(self.truck_point)
    self.rtg.command(Gf.Vec3d(self.yard_pick[0],self.yard_pick[1],self.rtg_high))
   elif self.phase=='集卡经引桥运往堆场':
    if self.drive(dt) and rtg_ready:self.change('RTG吊具下降到集卡');self.rtg.command(self.yard_pick)
   elif self.phase=='RTG吊具下降到集卡' and rtg_ready:self.change('RTG对位闭锁')
   elif self.phase=='RTG对位闭锁' and self.wait>=1:
    self.cargo_on_truck=False;self.lock('rtg');self.change('RTG起升');self.rtg.command(Gf.Vec3d(self.yard_pick[0],self.yard_pick[1],self.rtg_high))
   elif self.phase=='RTG起升' and rtg_ready:self.change('RTG小车移向第'+str(self.plan['target_row'])+'排');self.rtg.command(Gf.Vec3d(self.target[0],self.target[1],self.rtg_high))
   elif self.phase.startswith('RTG小车移向第') and rtg_ready:self.change('RTG叠箱下降');self.rtg.command(self.target)
   elif self.phase=='RTG叠箱下降' and rtg_ready:self.change('RTG开锁落箱')
   elif self.phase=='RTG开锁落箱' and self.wait>=1:
    self.set_cargo(self.target-Gf.Vec3d(0,0,self.height/2));self.lock(None);self.change('RTG吊具抬离箱顶');self.rtg.command(Gf.Vec3d(self.target[0],self.target[1],self.rtg_high))
   elif self.phase=='RTG吊具抬离箱顶' and rtg_ready:
    self.begin_return()
   elif self.phase in ('集卡空车返回停车场','集卡倒车归位'):self.tick_return(dt)
   if self.attached=='sts':self.set_cargo(self.sts_contact()-Gf.Vec3d(0,0,self.height/2))
   elif self.attached=='rtg':self.set_cargo(self.rtg.contact()-Gf.Vec3d(0,0,self.height/2))
   elif self.cargo_on_truck:self.set_cargo(self.truck_load_center(),self.truck_heading-90)
 def pause(self):
  if self.finished or self.closed:return
  if not self.paused:self.saved_targets=dict(getattr(self,'active_sts_target',self.c.targets));self.c.hold();self.paused=True;self.status='paused'
 def resume(self):
  if self.finished or self.closed:return
  if self.error:raise ValueError('任务异常，请复位后重新预览')
  if self.c.stopped:raise ValueError('岸桥急停尚未解除，请先恢复岸桥')
  if self.paused:self.command_sts(self.saved_targets);self.paused=False;self.status='running'
 def fail(self,error):self.pause();self.error=str(error);self.status='failed'
 def summary(self):return {'status':self.status,'phase':self.phase,'paused':self.paused,'finished':self.finished,'elapsed_seconds':round(self.elapsed,1),'crane_id':self.plan['crane_id'],'source_location':self.plan['source_location'],'target_bay':self.plan['target_bay'],'target_row':self.plan['target_row'],'target_tier':self.plan['target_tier'],'cargo_owner':self.attached or ('truck' if self.cargo_on_truck else 'landed'),'rtg_spreader_size_ft':self.rtg.current_size_ft,'required_size_ft':self.plan['size_ft'],'spreader_ready':self.rtg.spreader_ready,'error':self.error,'simulation_only':True,'reset_required_for_new_task':self.finished}
 def close(self):
  if self.closed:return
  self.c.hold();layers=self.stage.GetSessionLayer().subLayerPaths
  if getattr(self,'rtg',None):self.rtg.close()
  if self.layer.identifier in layers:layers.remove(self.layer.identifier)
  self.c.state=dict(self.home);self.c.targets=dict(self.home);self.c.spreader_size_ft=self.home_size;self.c._apply();self.closed=True;self.status='reset'
  if self.held_by_us:self.sea.release(SHIP)
  if getattr(builtins,'_smartport_ship_transfer_task',None) is self:builtins._smartport_ship_transfer_task=None
