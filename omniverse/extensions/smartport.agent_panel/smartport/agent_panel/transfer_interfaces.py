"""Allowlisted Agent adapter for the reversible ship/truck/yard demonstration."""
import secrets,time
from .scene_runtime import sts_controllers

def execute_transfer(owner,name,args):
 from ship_to_yard_transfer import active_task,ShipToYardTransfer
 from ship_transfer_plan import plan_transfer
 from sts_control import shared_sts_controller
 reverse=name.endswith('yard_to_ship_transfer')
 direction='yard_to_ship' if reverse else 'ship_to_yard'
 preview_attribute='_yard_transfer_preview' if reverse else '_ship_transfer_preview'
 if reverse:
  from yard_to_ship_plan import plan_yard_to_ship as planner
  from yard_to_ship_transfer import YardToShipTransfer as task_class
  preview=getattr(owner,preview_attribute,None)
  if args.get('plan_type')=='cumulative_double20' or (name.startswith('start_') and preview and preview['plan'].get('plan_type')=='cumulative_double20'):
   from yard_double20_transfer import CumulativeDouble20YardToShipTransfer as task_class
 else:planner=plan_transfer;task_class=ShipToYardTransfer
 if not reverse and (args.get('double20') or (name.startswith('start_') and getattr(owner,preview_attribute,None) and getattr(owner,preview_attribute)['plan'].get('double20'))):
  from ship_double20_transfer import Double20ShipToYardTransfer
  task_class=Double20ShipToYardTransfer
 task=active_task(owner.stage)
 if name.startswith('preview_'):
  if task:raise ValueError('已有搬运演示，请先查询状态或复位')
  plan=planner(owner.stage,**args)
  identifier=secrets.token_urlsafe(18)
  summary=(f"仿真装船：{plan['source_location']} → RTG → 停车场01集卡经引桥运输 → {plan['crane_id']} → 船上 {plan['target_location']}。目标顶部结构占位箱将在演示中隐藏替换，复位恢复。" if reverse else
   f"仿真搬运：结构示意箱 {plan['source_location']} → {plan['crane_id']} → 停车场01集卡经引桥运输 → RTG → {plan['target_bay']} 第{plan['target_row']}排第{plan['target_tier']}层。")
  if plan.get('plan_type')=='cumulative_double20':
   quay=plan['delivery_mode']=='quay'
   sources='；'.join(p['source_location']+('' if quay else ' → 船上 '+p['target_location']) for p in plan['containers'])
   destination=f"同一集卡经引桥到{plan['crane_id']}后载着两箱等待交接" if quay else f"同一集卡经引桥到{plan['crane_id']}逐箱装船，空车返场"
   summary=f"一车两箱位、累积装车（按车道前进方向排序）：{sources}。集卡依次前进到两个源贝位，先装前箱位再装后箱位；RTG载箱时大车不动，仅小车横移落车，开锁抬离后才允许集卡前进。两箱装满后{destination}。堆场内不倒车，返场时允许倒车入库。不修改库存，复位恢复原场景。"
  elif plan.get('double20'):
   targets='；'.join(f"{p['target_bay']} 第{p['target_row']}排第{p['target_tier']}层" for p in plan['containers'])
   summary=f"双20可复位仿真：{plan['source_location']} → {plan['crane_id']} 双箱装车 → 集卡运输 → RTG使用20尺吊具逐箱放到：{targets}。不修改库存。"
  else:summary+=f"取箱前先确认源箱为{plan['size_ft']}尺，并将RTG吊具匹配到{plan['size_ft']}尺、确认到位。"
  setattr(owner,preview_attribute,{'id':identifier,'args':dict(args),'plan':plan,'expires':time.monotonic()+600,'summary':summary})
  return {'plan_id':identifier,**plan}
 if name.startswith('start_'):
  if task:raise ValueError('已有任务，请先查询状态或复位')
  preview=getattr(owner,preview_attribute,None)
  if not preview or preview['id']!=args['plan_id'] or time.monotonic()>preview['expires']:raise ValueError('预览已失效，请重新预览')
  from .project_interfaces import require_idle
  require_idle(owner)
  owner._guard_manual('sts',preview['plan']['crane_id'])
  current=planner(owner.stage,**preview['args'])
  if current['fingerprint']!=preview['plan']['fingerprint']:raise ValueError('源箱、目标堆位或场景发生变化，请重新预览后执行')
  controller=shared_sts_controller(owner.stage,current['crane_id'])
  task=task_class(owner.stage,controller,current,args.get('allow_display_normalization',False))
  owner.transfer_task=task
  setattr(owner,preview_attribute,None)
  result=task.summary()
  if args.get('cinematic',True):
   try:result['cinematic']=owner.control_transfer_camera('start')
   except Exception as error:
    owner.transfer_camera_error=str(error)
    result['cinematic']={'status':'unavailable','error':str(error)}
  return result
 action=args['action']
 if not task:return {'status':'idle'}
 if task.plan.get('direction','ship_to_yard')!=direction:raise ValueError('当前搬运任务方向不同，请先用get_current_tasks查询正在运行的任务')
 if action=='pause':task.pause()
 elif action=='resume':task.resume()
 elif action=='reset':
  if owner.transfer_camera:owner.transfer_camera.close()
  task.close();owner.transfer_task=None
 return task.summary()
