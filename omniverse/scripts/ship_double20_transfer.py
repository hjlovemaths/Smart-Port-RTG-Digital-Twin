"""Reversible twin-20 STS lift, one truck, then two individual RTG placements."""
import hashlib,json
from pxr import Gf,Sdf,Usd,UsdGeom
from ship_transfer_plan import plan_transfer,box,solve,crane_state
from ship_structure_data import read_structure
from ship_to_yard_transfer import ShipToYardTransfer,ROOT,task_op,tr
from transfer_truck_geometry import loading_routes

PAIR=ROOT+'/Twin20Cargo'


def plan_double20(stage,source_bay,source_side,row_from_side,target_bay,target_row,second_target_bay,second_target_row):
    if not second_target_bay or second_target_row is None:raise ValueError('双20需要用户分别指定两只箱的目标贝位和排号，不自动指定落点')
    first=plan_transfer(stage,source_bay,source_side,row_from_side,target_bay,target_row)
    if first['size_ft']!=20 or source_bay%2!=1:raise ValueError('双20须从相邻两个奇数船贝的20尺箱取箱')
    slots=[s for s in read_structure()['slots'] if s['bay']==source_bay+2 and s['zone']=='D']
    rows=sorted({s['row'] for s in slots if s['row']>0 and (s['row']%2==0)==(source_side=='port')},reverse=True)
    if first['source_row'] not in rows:raise ValueError('下一奇数船贝没有同排结构箱位，不能双20抓取')
    second=plan_transfer(stage,source_bay+2,source_side,rows.index(first['source_row'])+1,second_target_bay,second_target_row)
    if first['target_bay'].split('/')[0]!=second['target_bay'].split('/')[0]:raise ValueError('本次逐箱卸车的两个落点须在同一个堆场块内')
    if first['target_bay']==second['target_bay'] and first['target_row']==second['target_row']:raise ValueError('两只20尺箱不能预占同一堆位')
    a,b=(Gf.Vec3d(p['source_pick_m']) for p in (first,second))
    if abs(a[0]-b[0])>.02 or abs(a[2]-b[2])>.02:raise ValueError('两只源箱必须同排、同高且顶部可达')
    if any(p['size_ft']!=20 or p['display_normalization_required'] for p in (first,second)):raise ValueError('双20需使用已与堆场统一比例的20尺箱')
    if (Gf.Vec3d(first['cargo_dimensions_m'])-Gf.Vec3d(second['cargo_dimensions_m'])).GetLength()>.02:raise ValueError('双20源箱尺寸不一致')
    envelope=Gf.Range3d()
    for p in (first,second):envelope.UnionWith(box(stage,p['source_path']))
    dims=envelope.GetSize();pitch=b[1]-a[1];gap=pitch-first['cargo_dimensions_m'][1]
    if not 0<=gap<.30 or dims[1]>5.4:raise ValueError('两只源箱的纵向间距超出双20吊具范围')
    pick=(a+b)/2;stop=Gf.Vec3d(-238.15,pick[1],.6250000066)
    candidates=[]
    for prim in stage.GetPrimAtPath('/World/STSControls').GetChildren():
        c=crane_state(stage,prim)
        if c.stopped or abs(c.state['boom'])>.001:continue
        try:
            for point in (pick,stop+Gf.Vec3d(0,0,dims[2]),Gf.Vec3d(pick[0],pick[1],25),Gf.Vec3d(stop[0],stop[1],25)):solve(stage,c,point)
        except ValueError:continue
        candidates.append((abs(prim.GetAttribute('sts:homeY').Get()-pick[1]),prim.GetName()))
    if not candidates:raise ValueError('没有岸桥能在现有限位内覆盖这组双20和集卡停位')
    yard_y=sum(p['target_center_m'][1] for p in (first,second))/2
    empty,loaded,info=loading_routes(stage,40,dims[1],stop,yard_y)
    fields=('source_path','source_location','source_pick_m','cargo_dimensions_m','target_bay','target_row','target_tier','target_center_m','support_path','support_bounds')
    result=dict(first)
    result.update(double20=True,container_count=2,container_size_ft=20,size_ft=40,rtg_size_ft=20,
                  source_path=PAIR,source_paths=[p['source_path'] for p in (first,second)],
                  source_location=' + '.join(p['source_location'] for p in (first,second)),
                  source_pick_m=list(pick),source_pair_center_m=list(envelope.GetMidpoint()),
                  cargo_dimensions_m=list(dims),pair_pitch_m=pitch,pair_gap_m=gap,
                  containers=[{k:p[k] for k in fields} for p in (first,second)],
                  crane_id=min(candidates)[1],truck_stop_m=list(stop),truck_route_empty=empty,truck_route_loaded=loaded,
                  yard_clear_z=max(first['yard_clear_z'],second['yard_clear_z']),
                  note='双20结构示意箱的可复位运动演示：STS一次吊两箱，RTG使用20尺吊具逐箱放到用户指定的两个堆位；不修改库存或发送PLC命令。')
    result.update(info);result.pop('fingerprint',None);result.pop('spreader_adjustment',None)
    result['fingerprint']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    result['spreader_adjustment']=dict(first['spreader_adjustment'],target_size_ft=20)
    return result


class Double20ShipToYardTransfer(ShipToYardTransfer):
    def prepare(self):
        p=self.plan;self.box_prims=[];self.active_index=0;self.landed=[False,False]
        self.pair=UsdGeom.Xform.Define(self.stage,PAIR).GetPrim()
        cache=UsdGeom.XformCache()
        for i,path in enumerate(p['source_paths']):
            original=self.stage.GetPrimAtPath(path);world=cache.GetLocalToWorldTransform(original)
            copied=self.stage.DefinePrim(PAIR+f'/Box{i+1}')
            copied.GetReferences().AddInternalReference(path);copied.SetInstanceable(False)
            xf,op=task_op(copied,'twin20Source');xf.SetXformOpOrder([op]);op.Set(world)
            UsdGeom.Imageable(copied).CreateVisibilityAttr().Set('inherited')
            self.box_prims.append(copied)
        for path in p['source_paths']:UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set('invisible')
        super().prepare()
        # The outer four locks plus four task-owned inner locks form twin-20.
        # Extend only the task's end beams; resetting removes these opinions.
        cache=UsdGeom.XformCache();positions=[]
        for end in ('Front','Rear'):
            prim=self.stage.GetPrimAtPath(self.c.path+'/Gantry/Trolley/Hoist/STS_SpreaderEnd'+end)
            locks=[cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(str(prim.GetPath())+'/CornerLock'+side)).Transform(Gf.Vec3d(0,0,-.5)) for side in ('Front','Rear')]
            positions.append((prim,sum(locks,Gf.Vec3d(0))/2))
        center=sum((position for _,position in positions),Gf.Vec3d(0))/2
        for prim,position in positions:
            sign=1 if position[1]>center[1] else -1
            delta=Gf.Vec3d(0,sign*p['cargo_dimensions_m'][1]/2-(position[1]-center[1]),0)
            local=cache.GetLocalToWorldTransform(prim.GetParent()).GetInverse().TransformDir(delta)
            xf=UsdGeom.Xformable(prim);old=xf.GetOrderedXformOps();op=xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble,'twin20End')
            xf.SetXformOpOrder([op]+old);op.Set(local)
        from sts_twin20_spreader import connect_twin20_frame
        self.spreader_frame_joints=connect_twin20_frame(self.stage,self.c.path+'/Gantry/Trolley/Hoist')
        locks=UsdGeom.Xform.Define(self.stage,ROOT+'/Twin20InnerLocks');self.lock_op=locks.AddTransformOp()
        half_width=p['cargo_dimensions_m'][0]/2-.075
        for i,(x,y) in enumerate(( (x,y) for x in (-half_width,half_width) for y in (-p['pair_gap_m']/2,p['pair_gap_m']/2))):
            pin=UsdGeom.Cube.Define(self.stage,str(locks.GetPath())+f'/Lock{i+1}');pin.CreateSizeAttr(1)
            pin.CreateDisplayColorAttr([Gf.Vec3f(1.,.68,.025)])
            pin.AddTranslateOp().Set(Gf.Vec3d(x,y,.14));pin.AddScaleOp().Set(Gf.Vec3d(.09,.055,.28))
        self.lock_op.Set(tr(self.sts_contact()))
        self.yard_pick+=Gf.Vec3d(0,-p['pair_pitch_m']/2,0)
        self.select_target(0)
        for item in p['containers']:
            self.rtg.command(Gf.Vec3d(item['target_center_m'])+Gf.Vec3d(0,0,self.height/2))
        self.rtg.command(self.rtg.origin)

    @property
    def cinema_cargo_path(self):
        return str(self.box_prims[self.active_index].GetPath()) if self.phase.startswith('RTG') and not self.spreader_preparing else PAIR

    @property
    def cinema_detail(self):
        if self.phase.startswith('RTG') and not self.spreader_preparing:
            item=self.plan['containers'][self.active_index]
            return f"{self.phase} · 第{self.active_index+1}/2只 → {item['target_bay']} 第{item['target_row']}排"
        return self.phase

    def select_target(self,index):
        self.active_index=index;item=self.plan['containers'][index]
        self.target=Gf.Vec3d(item['target_center_m'])+Gf.Vec3d(0,0,self.height/2)
        self.cinema_target_center=item['target_center_m'];self.cinema_support_path=item['support_path']

    def detach_box(self):
        prim=self.box_prims[self.active_index];self.box_world=UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        self.box_origin=box(self.stage,prim.GetPath()).GetMidpoint()
        xf,self.box_op=task_op(prim,'rtgSingle20');xf.SetXformOpOrder([self.box_op],resetXformStack=True);self.box_op.Set(self.box_world)

    def move_box(self,center):self.box_op.Set(self.box_world*tr(-self.box_origin)*tr(center))

    def tick(self,dt):
        if self.closed or self.finished:return
        if not self.phase.startswith('RTG') or self.spreader_preparing:
            super().tick(dt)
        else:
            if self.c.stopped:self.pause();return
            if self.paused:return
            dt=max(0,min(float(dt),.1));self.elapsed+=dt;self.wait+=dt
            with Usd.EditContext(self.stage,self.layer):
                if not self.spreader_gate(dt):return
                self.c.tick(dt);ready=self.rtg.tick(dt)
                if self.phase=='RTG移向下一只箱' and ready:self.change('RTG吊具下降到集卡');self.rtg.command(self.yard_pick)
                elif self.phase=='RTG吊具下降到集卡' and ready:self.change('RTG对位闭锁')
                elif self.phase=='RTG对位闭锁' and self.wait>=1:
                    self.check_grip_size('rtg');self.detach_box();self.cargo_on_truck=False;self.lock('rtg')
                    self.change('RTG起升');self.rtg.command(Gf.Vec3d(self.yard_pick[0],self.yard_pick[1],self.rtg_high))
                elif self.phase=='RTG起升' and ready:
                    self.change('RTG小车移向第'+str(self.plan['containers'][self.active_index]['target_row'])+'排')
                    self.rtg.command(Gf.Vec3d(self.target[0],self.target[1],self.rtg_high))
                elif self.phase.startswith('RTG小车移向第') and ready:self.change('RTG叠箱下降');self.rtg.command(self.target)
                elif self.phase=='RTG叠箱下降' and ready:self.change('RTG开锁落箱')
                elif self.phase=='RTG开锁落箱' and self.wait>=1:
                    self.move_box(self.target-Gf.Vec3d(0,0,self.height/2));self.lock(None);self.landed[self.active_index]=True
                    self.change('RTG吊具抬离箱顶');self.rtg.command(Gf.Vec3d(self.target[0],self.target[1],self.rtg_high))
                elif self.phase=='RTG吊具抬离箱顶' and ready:
                    if self.active_index==0:
                        self.select_target(1);b=box(self.stage,self.box_prims[1].GetPath())
                        self.yard_pick=Gf.Vec3d(b.GetMidpoint()[0],b.GetMidpoint()[1],b.GetMax()[2])
                        self.change('RTG移向下一只箱');self.rtg.command(Gf.Vec3d(self.yard_pick[0],self.yard_pick[1],self.rtg_high))
                    else:self.begin_return()
                if self.attached=='rtg':self.move_box(self.rtg.contact()-Gf.Vec3d(0,0,self.height/2))
        if not self.closed:
            with Usd.EditContext(self.stage,self.layer):self.lock_op.Set(tr(self.sts_contact()))

    def summary(self):
        result=super().summary()
        result.update(double20=True,container_count=2,container_size_ft=20,required_size_ft=20,sts_lift_mode='twin20',
                      current_box=self.active_index+1,containers=[dict(source_location=p['source_location'],target_bay=p['target_bay'],target_row=p['target_row'],target_tier=p['target_tier'],landed=self.landed[i]) for i,p in enumerate(self.plan['containers'])])
        return result
