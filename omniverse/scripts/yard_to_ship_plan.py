"""Read-only planning for yard -> truck -> STS -> returned vessel deck slots."""
import hashlib,json,re
from pxr import Gf,Usd,UsdGeom
from ship_transfer_plan import YARD,PARK,box,cargo_bounds,crane_state,solve
from ship_structure_data import read_structure
from large_vessel_layout import SHIP


def yard_boxes(stage):
    for group in stage.GetPrimAtPath(YARD).GetChildren():
        for prim in group.GetChildren():
            if prim.GetAttribute('live:containerSizeFt').Get() in (20,40) and UsdGeom.Imageable(prim).ComputeVisibility()!='invisible':
                yield prim


def overlaps_xy(a,b,tolerance=.015):
    return all(a.GetMin()[i]<b.GetMax()[i]-tolerance and a.GetMax()[i]>b.GetMin()[i]+tolerance for i in (0,1))


def plan_yard_to_ship(stage,source_bay,source_row,target_location,source_tier=None,crane_id=None):
    for required in (YARD,PARK,SHIP+'/StructureSlots'):
        if not stage.GetPrimAtPath(required):raise ValueError('场景缺少模型: '+required)
    match=re.fullmatch(r'([1-4])[cC]/?(\d{1,3})',source_bay)
    if not match or type(source_row) is not int or not 1<=source_row<=6:raise ValueError('请指定堆场贝位（如3C/005）和第1–6排')
    if source_tier is not None and (type(source_tier) is not int or not 1<=source_tier<=6):raise ValueError('源层号须为1–6')
    block=match[1]+'C';bay=int(match[2]);source_id=f'{block}/{bay:03d}'
    from yard_coordinate_mapping import bay_id_position_m
    bay_id_position_m(source_id,'center')
    candidates=[];all_yard=list(yard_boxes(stage))
    for prim in all_yard:
        identifier=prim.GetAttribute('live:bayId').Get() or ''
        if not identifier.startswith(block+'/') or prim.GetAttribute('live:row').Get()!=source_row:continue
        actual=int(identifier.split('/')[1]);feet=prim.GetAttribute('live:containerSizeFt').Get()
        if bay not in ((actual-1,actual,actual+1) if feet==40 else (actual,)):continue
        if source_tier is not None and prim.GetAttribute('live:tier').Get()!=source_tier:continue
        candidates.append((prim,cargo_bounds(stage,prim)))
    if not candidates:raise ValueError('指定堆场箱位没有可见箱子，不能推测库存')
    source,bounds=max(candidates,key=lambda pair:pair[1].GetMax()[2])
    for other in all_yard:
        if other==source:continue
        obstacle=cargo_bounds(stage,other)
        if overlaps_xy(bounds,obstacle) and obstacle.GetMax()[2]>bounds.GetMax()[2]+.015:
            raise ValueError('源箱上方还有箱子，须先搬走上层箱')
    feet=source.GetAttribute('live:containerSizeFt').Get();dims=bounds.GetSize()
    if not re.fullmatch(r'\d{6}',target_location):raise ValueError('船上目标箱位须为6位编码，例如011090')
    ship_bay,row,tier=int(target_location[:2]),int(target_location[2:4]),int(target_location[4:])
    if tier<82 or tier%2:raise ValueError('当前装船演示仅支持甲板偶数层号82及以上，不支持穿过舱盖装入船舱')
    if (feet==20)!=(ship_bay%2==1):raise ValueError('尺型不匹配：20尺箱须用奇数贝，40尺箱须用偶数贝')
    bays=[ship_bay] if feet==20 else [ship_bay-1,ship_bay+1]
    slots=read_structure()['slots'];targets=[];target_boxes=[];supports=[]
    for b in bays:
        location=f'{b:02d}{row:02d}{tier:02d}'
        if not any(s['location']==location and s['zone']=='D' for s in slots):raise ValueError('6.6快照未返回目标甲板箱位: '+location)
        p=stage.GetPrimAtPath(SHIP+f'/StructureSlots/Bay{b:02d}_D/L{location}')
        if not p or UsdGeom.Imageable(p).ComputeVisibility()=='invisible':raise ValueError('目标结构占位不可用: '+location)
        footprint=box(stage,p.GetPath())
        for sibling in p.GetParent().GetChildren():
            if sibling.GetAttribute('ship:row').Get()==row and sibling.GetAttribute('ship:tier').Get()>tier and UsdGeom.Imageable(sibling).ComputeVisibility()!='invisible':
                raise ValueError('目标上方仍有箱位占位模型，请选择该列顶部可达层')
        base=p.GetAttribute('ship:displayBaseTier').Get()
        if tier==base:
            ship_world=UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(SHIP))
            support_z=ship_world.Transform(Gf.Vec3d(0,0,p.GetAttribute('ship:supportLocalZ').Get()))[2]
            if abs(footprint.GetMin()[2]-support_z)>.03:raise ValueError('目标甲板支撑高度不一致')
            supports.append({'path':'deck','top_z':support_z})
        else:
            below=stage.GetPrimAtPath(SHIP+f'/StructureSlots/Bay{b:02d}_D/L{b:02d}{row:02d}{tier-2:02d}')
            if not below or UsdGeom.Imageable(below).ComputeVisibility()=='invisible':raise ValueError('目标下方缺少支撑箱位')
            support=box(stage,below.GetPath())
            if abs(support.GetMax()[2]-footprint.GetMin()[2])>.03:raise ValueError('目标下方存在悬空间隔')
            supports.append({'path':str(below.GetPath()),'top_z':support.GetMax()[2]})
        targets.append(str(p.GetPath()));target_boxes.append(footprint)
    if max(b.GetMin()[2] for b in target_boxes)-min(b.GetMin()[2] for b in target_boxes)>.02:raise ValueError('40尺目标跨越的两列箱位高度不同，不能平稳落箱')
    envelope=Gf.Range3d()
    for b in target_boxes:envelope.UnionWith(b)
    if any(abs(envelope.GetSize()[i]-dims[i])>.08 for i in (0,1)):raise ValueError('源箱尺寸与目标船箱位不匹配')
    target=Gf.Vec3d(envelope.GetMidpoint()[0],envelope.GetMidpoint()[1],envelope.GetMin()[2]+dims[2]/2)
    landing=Gf.Range3d(target-dims/2,target+dims/2)
    for group in stage.GetPrimAtPath(SHIP+'/StructureSlots').GetChildren():
        for p in group.GetChildren():
            if str(p.GetPath()) in targets or p.GetAttribute('ship:zone').Get()!='D' or UsdGeom.Imageable(p).ComputeVisibility()=='invisible':continue
            obstacle=box(stage,p.GetPath())
            if overlaps_xy(landing,obstacle) and obstacle.GetMax()[2]>landing.GetMin()[2]+.03:raise ValueError('目标落箱路径被邻箱遮挡')
    quay=Gf.Vec3d(-238.15,target[1],.6250000066);pick=quay+Gf.Vec3d(0,0,dims[2]);drop=target+Gf.Vec3d(0,0,dims[2]/2)
    available=[]
    for prim in stage.GetPrimAtPath('/World/STSControls').GetChildren():
        if crane_id and prim.GetName()!=crane_id:continue
        c=crane_state(stage,prim)
        if c.stopped or abs(c.state['boom'])>.001:continue
        try:
            for point in (pick,drop,Gf.Vec3d(pick[0],pick[1],25),Gf.Vec3d(drop[0],drop[1],25)):solve(stage,c,point)
        except ValueError:continue
        available.append((abs(prim.GetAttribute('sts:homeY').Get()-target[1]),prim.GetName()))
    if not available:raise ValueError('没有岸桥能在现有限位内覆盖目标船箱位及集卡停位')
    entry=next((y for y in (126.05,256.30,386.55,496.30) if y>bounds.GetMidpoint()[1]+6),None)
    if entry is None:raise ValueError('源堆位缺少可用的集卡道路入口')
    deck_z=quay[2];park=box(stage,PARK).GetMidpoint();yard_stop=Gf.Vec3d(-3.75,bounds.GetMidpoint()[1],deck_z)
    nearby=max(cargo_bounds(stage,p).GetMax()[2] for p in all_yard if (p.GetAttribute('live:bayId').Get() or '').startswith(block+'/'))
    clear=max(7.,nearby+dims[2]+.5)
    if clear>9 or bounds.GetMax()[2]>9:raise ValueError('堆场起升净空超出RTG可视行程')
    result={'direction':'yard_to_ship','simulation_only':True,'source_path':str(source.GetPath()),'source_location':f"{source.GetAttribute('live:bayId').Get()} 第{source_row}排第{source.GetAttribute('live:tier').Get()}层",'source_bay':source_id,'resolved_source_bay':source.GetAttribute('live:bayId').Get(),'source_row':source_row,'source_tier':source.GetAttribute('live:tier').Get(),'source_pick_m':list(Gf.Vec3d(bounds.GetMidpoint()[0],bounds.GetMidpoint()[1],bounds.GetMax()[2])),'source_bounds':[list(bounds.GetMin()),list(bounds.GetMax())],'cargo_dimensions_m':list(dims),'size_ft':feet,'target_location':target_location,'target_bay':ship_bay,'target_row':row,'target_tier':tier,'target_center_m':list(target),'target_placeholder_paths':targets,'target_supports':supports,'crane_id':min(available)[1],'yard_clear_z':clear,'yard_stop_m':list(yard_stop),'truck_stop_m':list(quay),'truck_route_empty':[list(park),[118,park[1],deck_z],[118,entry,deck_z],[-3.75,entry,deck_z],list(yard_stop)],'truck_route_loaded':[list(yard_stop),[-3.75,entry,deck_z],[-238.15,entry,deck_z],list(quay)],'display_normalization_required':False,'note':'可复位装船仿真：目标是6.6结构示意箱位，隐藏目标顶部占位模型并由搬运箱替换；不证明真实空舱，不修改库存文件或发送PLC命令。'}
    result['fingerprint']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    from rtg_transfer_motion import read_spreader_size_ft
    current=read_spreader_size_ft(stage)
    result['spreader_adjustment']={'current_size_ft':current,'target_size_ft':feet,'required':current is None or abs(current-feet)>1e-4,'before_pickup':True}
    return result
