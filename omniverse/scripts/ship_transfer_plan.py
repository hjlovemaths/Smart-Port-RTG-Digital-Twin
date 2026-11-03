"""Read-only, measured planning for reversible ship-to-yard visual transfers."""
import builtins,hashlib,json,re
from types import SimpleNamespace
from pxr import Gf,Usd,UsdGeom
from large_vessel_layout import SHIP
from ship_structure_data import read_structure
from sts_control import INITIAL,LIMITS
from sts_unload_task import UnloadTask
from build_live_containers import container_center_y_for_bay,row_center_x_m

PARK='/World/ParkedYardTrucks/Group1_Bay01'
YARD='/World/LiveContainers/YardInventory'
def box(stage,path):
 p=stage.GetPrimAtPath(path)
 if not p or not p.IsActive():raise ValueError('模型不存在: '+str(path))
 return UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(p).ComputeAlignedRange()
def cargo_bounds(stage,prim):
 result=Gf.Range3d(box(stage,prim.GetPath()))
 for sibling in prim.GetParent().GetChildren():
  if sibling.GetName().startswith(prim.GetName()+'_') and UsdGeom.Imageable(sibling).ComputeVisibility()!='invisible':result.UnionWith(box(stage,sibling.GetPath()))
 return result

def solve(stage,c,point):
 helper=UnloadTask.__new__(UnloadTask);helper.c=c;helper.stage=stage
 return helper.pose(Gf.Vec3d(point))
def crane_state(stage,prim):
 pool=getattr(builtins,'_smart_port_sts_controller_pool',None)
 if pool and pool['stage']==stage:
  existing=pool['controllers'].get(prim.GetName())
  if existing and not existing.closed:return existing
 def attr(name,default):
  value=prim.GetAttribute('sts:'+name).Get();return default if value is None else value
 return SimpleNamespace(path=str(prim.GetPath()),state=dict(INITIAL,hoist=INITIAL['hoist']+attr('raisedHomeOffset',0)),limits=dict(LIMITS,gantry=(attr('gantryMin',0),attr('gantryMax',0)),trolley=(-10,17+17.32*(attr('boomLengthScale',1)-1)),hoist=(.3,8+attr('upperOffset',(0,0,0))[2])),stopped=False)

def plan_transfer(stage,source_bay=1,source_side='port',row_from_side=1,target_bay='3C/001',target_row=6):
 for required in (SHIP,YARD,PARK):
  if not stage.GetPrimAtPath(required):raise ValueError('场景缺少模型: '+required)
 if source_side not in ('port','starboard') or type(row_from_side) is not int or row_from_side<1:raise ValueError('请明确左舷/右舷及从外侧数第几排')
 match=re.fullmatch(r'([1-4])[cC]/?(\d{1,3})',target_bay)
 if not match or type(target_row) is not int or not 1<=target_row<=6:raise ValueError('堆场目标格式应为3C/001，排号1–6')
 block=match[1]+'C';bay=int(match[2]);target_id=f'{block}/{bay:03d}'
 slots=[s for s in read_structure()['slots'] if s['bay']==source_bay and s['zone']=='D']
 if not slots:raise ValueError('这份6.6快照没有返回该贝甲板结构，不能推测箱位')
 rows=sorted({s['row'] for s in slots if s['row']>0 and (s['row']%2==0)==(source_side=='port')},reverse=True)
 if row_from_side>len(rows):raise ValueError('该舷没有这么多排')
 row=rows[row_from_side-1];column=sorted([s for s in slots if s['row']==row],key=lambda s:s['tier'])
 source=column[-1];feet=source['size_ft']
 if (feet==20 and bay%2==0) or (feet==40 and bay%2!=0):raise ValueError('20尺箱目标用奇数贝，40尺箱目标用偶数贝')
 from yard_coordinate_mapping import bay_id_position_m
 bay_id_position_m(target_id,'center') # validates the block/bay range
 path=SHIP+f'/StructureSlots/Bay{source_bay:02d}_D/L'+source['location']
 source_box=box(stage,path)
 if UsdGeom.Imageable(stage.GetPrimAtPath(path)).ComputeVisibility()=='invisible':raise ValueError('源箱位当前不可见')
 column_paths=[SHIP+f'/StructureSlots/Bay{source_bay:02d}_D/L'+s['location'] for s in column]
 supports=[];sample=None;nearby_top=0.
 for group in stage.GetPrimAtPath(YARD).GetChildren():
  for prim in group.GetChildren():
   size=prim.GetAttribute('live:containerSizeFt').Get()
   if size not in (20,40):continue
   if sample is None and size==feet:sample=cargo_bounds(stage,prim)
   identifier=prim.GetAttribute('live:bayId').Get() or ''
   if not identifier.startswith(block+'/'):continue
   b=int(identifier.split('/')[1]);covers=(b-1,b+1) if size==40 else (b,)
   if bay not in covers:continue
   bounds=cargo_bounds(stage,prim);nearby_top=max(nearby_top,bounds.GetMax()[2])
   if prim.GetAttribute('live:row').Get()==target_row and UsdGeom.Imageable(prim).ComputeVisibility()!='invisible':supports.append((prim,bounds))
 if sample is None:raise ValueError('没有可复用的对应尺寸堆场箱模型')
 if not supports:raise ValueError('目标排没有已渲染支撑箱；不能假定库存为空或凭空堆叠')
 support,base=max(supports,key=lambda pair:pair[1].GetMax()[2]);tier=int(support.GetAttribute('live:tier').Get())+1
 if tier>6:raise ValueError('目标排已达6层，不能继续堆高')
 dims=source_box.GetSize() if stage.GetPrimAtPath(SHIP).GetAttribute('ship:displayScale').Get()=='yard_native' else sample.GetSize();target=Gf.Vec3d(base.GetMidpoint()[0],container_center_y_for_bay(block,bay,feet),base.GetMax()[2]+dims[2]/2+.005)
 if any(target[i]-dims[i]/2<base.GetMin()[i]-.04 or target[i]+dims[i]/2>base.GetMax()[i]+.04 for i in (0,1)):raise ValueError('目标支撑面积不足')
 # Neighbor clearance is checked at the landing pose, not just the logical row.
 for group in stage.GetPrimAtPath(YARD).GetChildren():
  for prim in group.GetChildren():
   if prim==support or prim.GetAttribute('live:containerSizeFt').Get() not in (20,40):continue
   if not (prim.GetAttribute('live:bayId').Get() or '').startswith(block+'/'):continue
   other=cargo_bounds(stage,prim)
   if all(other.GetMin()[i]<target[i]+dims[i]/2-.015 and other.GetMax()[i]>target[i]-dims[i]/2+.015 for i in range(3)):raise ValueError('目标位置与邻箱冲突: '+str(prim.GetPath()))
 lower=box(stage,column_paths[0]).GetMin()[2]
 compact=stage.GetPrimAtPath(SHIP).GetAttribute('ship:displayScale').Get()=='yard_native'
 pick=Gf.Vec3d(source_box.GetMidpoint()[0],source_box.GetMidpoint()[1],source_box.GetMax()[2] if compact else lower+len(column)*dims[2])
 truck_stop=Gf.Vec3d(-238.15,pick[1],.6250000066)
 place=truck_stop+Gf.Vec3d(0,0,dims[2])
 candidates=[]
 for prim in stage.GetPrimAtPath('/World/STSControls').GetChildren():
  c=crane_state(stage,prim)
  if c.stopped or abs(c.state['boom'])>.001:continue
  try:
   for point in (pick,place,Gf.Vec3d(pick[0],pick[1],25),Gf.Vec3d(place[0],place[1],25)):solve(stage,c,point)
   candidates.append((abs(prim.GetAttribute('sts:homeY').Get()-pick[1]),prim.GetName()))
  except ValueError:continue
 if not candidates:raise ValueError('没有岸桥能在现有限位内覆盖源箱和集卡停位')
 source_park=box(stage,PARK)
 y0=source_park.GetMidpoint()[1]
 # Main roads and causeways are shared with build_ground_traffic_network.py.
 entry=next((y for y in (126.05,256.30,386.55,496.30) if y>target[1]+6),None)
 if entry is None:raise ValueError('目标缺少可用的顺向堆场入口')
 inbound=[[138.639, y0,truck_stop[2]],[118,y0,truck_stop[2]],[118,126.05,truck_stop[2]],[-238.15,126.05,truck_stop[2]],list(truck_stop)]
 outbound=[list(truck_stop),[-238.15,-5.375,truck_stop[2]],[-231.15,-5.375,truck_stop[2]],[-231.15,entry,truck_stop[2]],[-3.75,entry,truck_stop[2]],[-3.75,target[1],truck_stop[2]]]
 result={'simulation_only':True,'source_is_structure_slot':True,'source_location':source['location'],'source_path':path,'source_side':source_side,'source_row':row,'source_tier':source['tier'],'column_paths':column_paths,'size_ft':feet,'cargo_dimensions_m':list(dims),'source_pick_m':list(pick),'source_column_floor_z':lower,'target_bay':target_id,'target_row':target_row,'target_tier':tier,'target_center_m':list(target),'support_path':str(support.GetPath()),'support_bay':support.GetAttribute('live:bayId').Get(),'support_bounds':[list(base.GetMin()),list(base.GetMax())],'crane_id':min(candidates)[1],'truck_path':PARK,'truck_stop_m':list(truck_stop),'truck_route_empty':inbound,'truck_route_loaded':outbound,'yard_clear_z':max(7.,nearby_top+dims[2]+.5),'display_normalization_required':any(abs(source_box.GetSize()[i]-dims[i])>.02 for i in range(3)),'note':'用户指定结构示意箱的可复位运动演示，不代表真实配载；库存文件不修改。源列在任务准备时统一比例，途中不缩放。'}
 if compact:result['note']='船上与堆场已统一紧凑显示比例，按原尺寸搬运，不临时缩放；这是结构示意箱的可复位仿真，不代表真实配载，不修改库存文件。'
 result['fingerprint']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
 from rtg_transfer_motion import read_spreader_size_ft
 current=read_spreader_size_ft(stage)
 result['spreader_adjustment']={'current_size_ft':current,'target_size_ft':feet,'required':current is None or abs(current-feet)>1e-4,'before_pickup':True}
 return result
