"""One truck with two reserved 20ft slots, filled by successive RTG lifts."""
import hashlib,json
from pxr import Gf,Sdf,Usd,UsdGeom
from ship_transfer_plan import crane_state,solve
from transfer_truck_geometry import truck_loading,loading_routes
from yard_to_ship_transfer import YardToShipTransfer,ROOT


def plan_cumulative_double20(stage,source_bay,source_row,target_location,source_tier,crane_id,
                             second_source_bay,second_source_row,second_source_tier,second_target_location,delivery_mode='ship'):
    from yard_to_ship_plan import plan_yard_to_ship
    if delivery_mode not in ('ship','quay'):raise ValueError('交付方式须为 ship 或 quay')
    quay_only=delivery_mode=='quay'
    if not second_source_bay or second_source_row is None:
        raise ValueError('一车两箱须分别指定两个源贝位和排号')
    if quay_only:
        if not crane_id:raise ValueError('仅运到岸桥须指定 crane_id，不自动选择交接岸桥')
        if target_location is not None or second_target_location is not None:raise ValueError('仅运到岸桥的计划不接受船上目标箱位，请使用 ship 装船方式')
    elif not target_location or not second_target_location:raise ValueError('装船须分别指定两个船上目标箱位')
    first=plan_yard_to_ship(stage,source_bay,source_row,target_location,source_tier,crane_id,_yard_only=quay_only)
    # Plan the second lift against the yard after the first lift, without USD edits.
    second=plan_yard_to_ship(stage,second_source_bay,second_source_row,second_target_location,
                             second_source_tier,crane_id,_excluded_source_paths=(first['source_path'],),_yard_only=quay_only)
    items=[dict(first,request_index=0),dict(second,request_index=1)]
    # The work lane runs in -Y. Keep each source's ship destination attached
    # while ordering the visits upstream first, independent of request order.
    # Stable sorting preserves top-before-bottom lifts at the same stack.
    items.sort(key=lambda p:-p['source_pick_m'][1])
    first,second=items
    if any(p['size_ft']!=20 for p in items):raise ValueError('一车两箱位只支持两只20尺箱，不能装两只40尺或混装')
    if first['resolved_source_bay'].split('/')[0]!=second['resolved_source_bay'].split('/')[0]:
        raise ValueError('连续装车的两个源箱须在同一堆场块内')
    if not quay_only and set(first['target_placeholder_paths'])&set(second['target_placeholder_paths']):
        raise ValueError('两只箱不能预占同一个船上目标箱位')
    if (Gf.Vec3d(first['cargo_dimensions_m'])-Gf.Vec3d(second['cargo_dimensions_m'])).GetLength()>.02:
        raise ValueError('两只20尺源箱的实际尺寸不一致')
    lengths=[p['cargo_dimensions_m'][1] for p in items];gap=.08
    span=sum(lengths)+gap
    deck,park,_=truck_loading(stage,40,span)
    # The model's rails are slightly shorter than two native boxes. Limit the
    # end overhang to 6 cm, matching the existing twin-20 trailer presentation.
    if span>deck.GetSize()[0]+.12:raise ValueError('两只箱超出当前集卡拖板两箱位的承载长度')
    offsets=[-span/2+lengths[0]/2,span/2-lengths[1]/2]
    yard_ys=[p['source_pick_m'][1]-offsets[i] for i,p in enumerate(items)]
    if yard_ys[1]>=yard_ys[0]-1e-5:raise ValueError('两个装箱停位无法按车道前进方向衔接，拒绝倒车装箱')
    control=stage.GetPrimAtPath('/World/STSControls/'+str(crane_id)) if quay_only else None
    if quay_only and not control:raise ValueError('指定岸桥不存在: '+str(crane_id))
    quay=Gf.Vec3d(-238.15,control.GetAttribute('sts:homeY').Get() if quay_only else sum(p['target_center_m'][1] for p in items)/2,park[2])
    if quay_only:
        for i,p in enumerate(items):
            p.update(direction='yard_to_ship',simulation_only=True,display_normalization_required=False,
                     target_location=crane_id+'集卡交接区',target_bay=None,target_row=None,target_tier=None,
                     target_center_m=list(quay+Gf.Vec3d(0,offsets[i],p['cargo_dimensions_m'][2]/2)),
                     target_placeholder_paths=[],target_supports=[])
    candidates=[]
    for prim in stage.GetPrimAtPath('/World/STSControls').GetChildren():
        if crane_id and prim.GetName()!=crane_id:continue
        c=crane_state(stage,prim)
        if c.stopped or abs(c.state['boom'])>.001:continue
        try:
            for i,p in enumerate(items):
                pick=quay+Gf.Vec3d(0,offsets[i],p['cargo_dimensions_m'][2])
                target=Gf.Vec3d(p['target_center_m'])+Gf.Vec3d(0,0,p['cargo_dimensions_m'][2]/2)
                for point in (pick,target,Gf.Vec3d(pick[0],pick[1],25),Gf.Vec3d(target[0],target[1],25)):
                    solve(stage,c,point)
        except ValueError:continue
        candidates.append((abs(prim.GetAttribute('sts:homeY').Get()-quay[1]),prim.GetName()))
    if not candidates:raise ValueError('没有同一台岸桥能覆盖两只箱的车上箱位和船上目标')
    empty,_,first_info=loading_routes(stage,40,span,quay,yard_ys[0],reverse=True)
    _,loaded,info=loading_routes(stage,40,span,quay,yard_ys[1],reverse=True)
    slots=[dict(slot_id=name,container_index=i,load_offset_m=[offsets[i],0,0],
                yard_contact_m=[-3.75,p['source_pick_m'][1],park[2]],
                yard_vehicle_stop_m=[-3.75,yard_ys[i],park[2]],
                quay_contact_m=list(quay+Gf.Vec3d(0,offsets[i],0)))
           for i,(name,p) in enumerate(zip(('front','rear'),items))]
    info.update(yard_vehicle_stop_m=slots[0]['yard_vehicle_stop_m'],
                yard_entry_crossroad_y=first_info['yard_entry_crossroad_y'])
    result=dict(first)
    result.update(plan_type='cumulative_double20',plan_label='一车两箱位、累积装车',container_count=2,
                  delivery_mode=delivery_mode,
                  container_size_ft=20,rtg_size_ft=20,sts_lift_mode='none' if quay_only else 'single20',containers=items,
                  truck_slots=slots,departure_required_count=2,
                  loading_order=[p['request_index'] for p in items],yard_reverse_allowed=False,parking_reverse_allowed=True,
                  rtg_loaded_gantry_motion_allowed=False,
                  yard_loading_stops_m=[s['yard_vehicle_stop_m'] for s in slots],
                  truck_route_between_loads=[s['yard_vehicle_stop_m'] for s in slots],
                  source_paths=[p['source_path'] for p in items],
                  source_location=' + '.join(p['source_location'] for p in items),
                  target_location=crane_id+'集卡交接区' if quay_only else ' + '.join(p['target_location'] for p in items),
                  target_placeholder_paths=[path for p in items for path in p['target_placeholder_paths']],
                  target_supports=[support for p in items for support in p['target_supports']],
                  crane_id=min(candidates)[1],yard_clear_z=max(p['yard_clear_z'] for p in items),
                  yard_stop_m=slots[0]['yard_contact_m'],truck_stop_m=slots[0]['quay_contact_m'],
                  truck_route_empty=empty,truck_route_loaded=loaded,
                  note='一车两箱位、累积装车的可复位演示：按车道负Y方向依次到两个源贝位，先前箱位后后箱位；RTG载箱时大车禁止移动，仅小车横移落车；开锁抬离后集卡向前到下一停位，两箱装满才驶往岸桥；堆场内禁止倒车。'+('到指定岸桥后保留两箱在车上等待交接。' if quay_only else '岸桥逐箱装船后空车返场，允许倒车进入原停车位。')+'不修改库存或发送PLC命令。')
    result.update(info)
    result['truck_load_offset_m']=slots[0]['load_offset_m']
    result.pop('fingerprint',None);result.pop('spreader_adjustment',None)
    result['fingerprint']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    result['spreader_adjustment']=first['spreader_adjustment']
    return result


class CumulativeDouble20YardToShipTransfer(YardToShipTransfer):
    def prepare_cargo(self,p,path):
        super().prepare_cargo(self.plan['containers'][0] if p is self.plan else p,path)

    def prepare(self):
        self.active_index=0;self.loaded=[False,False];self.landed=[False,False];self.cargo_records=[];self.loaded_gantry_y=None
        super().prepare()
        self.remember_cargo()
        self.prepare_cargo(self.plan['containers'][1],ROOT+'/Cargo2')
        self.remember_cargo()
        self.select_box(0)
        # Validate every RTG pose before any vehicle/equipment movement.
        for i,item in enumerate(self.plan['containers']):
            place=Gf.Vec3d(self.plan['truck_slots'][i]['yard_contact_m'])+Gf.Vec3d(0,0,item['cargo_dimensions_m'][2])
            pick=Gf.Vec3d(item['source_pick_m'])
            for point in (pick,place,Gf.Vec3d(pick[0],pick[1],self.rtg_high),Gf.Vec3d(place[0],place[1],self.rtg_high)):
                self.rtg.command(point)
        self.rtg.command(Gf.Vec3d(self.pick[0],self.pick[1],self.rtg_high))
        self.update_slots()

    def remember_cargo(self):
        self.cargo_records.append((self.cargo,self.cargo_op,self.cargo_world,self.cargo_origin))

    def select_box(self,index):
        self.active_index=index;p=self.plan['containers'][index];slot=self.plan['truck_slots'][index]
        self.cargo,self.cargo_op,self.cargo_world,self.cargo_origin=self.cargo_records[index]
        self.height=p['cargo_dimensions_m'][2];self.pick=Gf.Vec3d(p['source_pick_m'])
        self.yard_stop=Gf.Vec3d(slot['yard_contact_m'])
        self.yard_place=Gf.Vec3d(slot['yard_contact_m'])+Gf.Vec3d(0,0,self.height)
        self.quay_pick=Gf.Vec3d(slot['quay_contact_m'])+Gf.Vec3d(0,0,self.height)
        self.target=Gf.Vec3d(p['target_center_m'])+Gf.Vec3d(0,0,self.height/2)
        self.cargo_on_truck=self.loaded[index] and not self.landed[index]

    def truck_load_center(self):
        offset=Gf.Vec3d(self.plan['truck_slots'][self.active_index]['load_offset_m'])
        return self.truck_point+Gf.Rotation(Gf.Vec3d(0,0,1),self.truck_heading).TransformDir(offset)+Gf.Vec3d(0,0,self.height/2)

    def lock(self,owner):
        previous=self.attached
        super().lock(owner)
        self.loaded_gantry_y=self.rtg.contact()[1] if owner=='rtg' else None
        i=self.active_index
        if owner=='sts':self.loaded[i]=False
        elif owner is None and previous=='rtg':self.loaded[i]=True
        elif owner is None and previous=='sts':self.landed[i]=True
        self.update_slots()

    def update_slots(self):
        for i,slot in enumerate(self.plan['truck_slots']):
            self.truck.CreateAttribute('transfer:slot:'+slot['slot_id'],Sdf.ValueTypeNames.String).Set(
                str(self.cargo_records[i][0].GetPath()) if self.loaded[i] else '')
        self.truck.CreateAttribute('transfer:loadedCount',Sdf.ValueTypeNames.Int).Set(sum(self.loaded))

    def advance_yard_load(self):
        if self.active_index==0:
            self.select_box(1);self.change('集卡前往下一装箱贝位')
            self.set_route(self.plan['truck_route_between_loads']);self.route[0]=Gf.Vec3d(self.truck_point)
            self.rtg.command(Gf.Vec3d(self.pick[0],self.pick[1],self.rtg_high))
            return True
        if not all(self.loaded):raise ValueError('两只箱尚未全部落稳开锁，禁止集卡发车')
        self.select_box(0)
        return False

    def command_rtg(self,point):
        if self.attached=='rtg' and abs(point[1]-self.loaded_gantry_y)>1e-5:
            raise ValueError('RTG载箱时禁止大车行走，须由集卡前进对准源贝位装箱')
        super().command_rtg(point)

    def drive(self,dt,reverse=False):
        if reverse and self.phase!='集卡倒车归位':raise ValueError('堆场内禁止倒车，仅允许返场时倒车进入停车位')
        if self.attached=='rtg':raise ValueError('吊具尚未与箱子解锁，集卡禁止移动')
        return super().drive(dt,reverse=reverse)

    def advance_quay_load(self):
        if self.active_index==0:
            self.select_box(1);self.change('岸桥移向下一只集卡箱')
            self.go_sts(Gf.Vec3d(self.quay_pick[0],self.quay_pick[1],25))
            return True
        if not all(self.landed):raise ValueError('两只箱尚未全部装船，禁止空车返场')
        return False

    def go_sts(self,point):
        if self.plan.get('delivery_mode')!='quay':super().go_sts(point)

    def arrive_at_quay(self):
        if self.plan.get('delivery_mode')!='quay':return super().arrive_at_quay()
        if not all(self.loaded):raise ValueError('两箱未全部随车到达，不能报告交接完成')
        self.finished=True;self.status='completed';self.delivery_done=True
        self.change('完成：两箱已运到'+self.plan['crane_id']+'，集卡等待岸桥接箱')

    def tick(self,dt):
        if self.closed or self.finished:return
        if self.attached=='rtg' and abs(self.rtg.contact()[1]-self.loaded_gantry_y)>1e-5:
            raise ValueError('RTG载箱大车位置发生变化，任务暂停')
        if self.phase=='集卡前往下一装箱贝位' and not self.closed and not self.finished:
            if self.c.stopped:self.pause()
            if self.paused:return
            dt=max(0,min(float(dt),.1));self.elapsed+=dt;self.wait+=dt
            with Usd.EditContext(self.stage,self.layer):
                if not self.spreader_gate(dt):return
                self.c.tick(dt);ready=self.rtg.tick(dt);arrived=self.drive(dt)
                if arrived and ready:
                    self.change('RTG下降到源箱');self.command_rtg(self.pick)
        else:super().tick(dt)
        if self.closed or self.paused:return
        # Each occupied slot follows the truck independently, even while the
        # other box is in the spreader or has already landed on the ship.
        from ship_to_yard_transfer import tr
        with Usd.EditContext(self.stage,self.layer):
            rotation=Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),self.truck_heading-90))
            for i,(_,op,world,origin) in enumerate(self.cargo_records):
                if not self.loaded[i]:continue
                offset=Gf.Vec3d(self.plan['truck_slots'][i]['load_offset_m'])
                center=self.truck_point+Gf.Rotation(Gf.Vec3d(0,0,1),self.truck_heading).TransformDir(offset)
                center+=Gf.Vec3d(0,0,self.plan['containers'][i]['cargo_dimensions_m'][2]/2)
                op.Set(world*tr(-origin)*rotation*tr(center))

    @property
    def cinema_cargo_path(self):return str(self.cargo.GetPath())

    @property
    def cinema_detail(self):
        return f"{self.phase} · 第{self.active_index+1}/2只 · 车上{sum(self.loaded)}/2只"

    def summary(self):
        result=super().summary()
        result.update(plan_type='cumulative_double20',delivery_mode=self.plan['delivery_mode'],container_count=2,current_box=self.active_index+1,
                      loading_order=self.plan['loading_order'],yard_reverse_allowed=False,parking_reverse_allowed=True,rtg_loaded_gantry_motion_allowed=False,
                      loaded_count=sum(self.loaded),landed_count=sum(self.landed),departure_required_count=2,
                      containers=[dict(source_location=p['source_location'],target_location=p['target_location'],request_index=p['request_index'],
                                       slot_id=self.plan['truck_slots'][i]['slot_id'],on_truck=self.loaded[i],landed=self.landed[i])
                                  for i,p in enumerate(self.plan['containers'])])
        return result
