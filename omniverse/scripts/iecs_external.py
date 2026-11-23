"""The documented single-box unloading handshake, executed on the Kit thread."""

import json
import math
import queue
import re
import time

from iecs_external_http import ExternalHTTP


def _id(value, name):
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip() or len(str(value)) > 64:
        raise ValueError(f'缺少或无效的 {name}')
    return str(value).strip()


def _yard_id(value):
    text = _id(value, '场箱位').upper()
    match = re.fullmatch(r'([1-4]C)(\d{2,3})([1-6])(\d)', text)
    if not match:
        match = re.fullmatch(r'([1-4]C)/?(\d{2,3})/R([1-6])/T(\d)', text)
    if not match:
        raise ValueError('场箱位格式应为 3C0111 或 3C/001/R1/T1')
    block, bay, row, tier = match.groups()
    return f'{block}{int(bay):03d}/R{row}/T{tier}'


class ExternalDispatch:
    QC_IN = {114, 120, 123, 127, 146, 156}
    RTG_IN = {211, 219, 220, 222, 226, 229, 235, 243}

    def __init__(self, runtime, config, guard=None):
        self.runtime = runtime
        self.config = config
        self.guard = guard
        self.vehicle_id = f'ITT-{config.truck_number:03d}'
        self.sources = {p.rsplit('/L', 1)[1]: p for p in runtime.ship_slots
                        if '/StructureSlots/' in p and int(p.rsplit('/L', 1)[1][:-4]) % 2}
        if runtime.size_ft != 20 or not self.sources:
            raise ValueError('真实 IECS 联调需要 6.6 船图和 STS_01 可达的 20 尺顶层箱位')
        from iecs_ship_pickup import bounds
        height = bounds(runtime.stage, next(iter(self.sources.values()))).GetSize()[2]
        target = next(runtime.free_yard_slots(height), None)
        self.sample = {'ship_slot': next(iter(self.sources)), 'yard_slot': target['id'] if target else ''}
        self.order_id = ''
        self.qc = self.rtg = None
        self.vehicle_task = None
        self.rtg_vehicle_task = None
        self.qc_dispatched = self.rtg_dispatched = False
        self.qc_precise = self.rtg_precise = False
        self.land_requested = self.land_allowed = False
        self.qc_released = False
        self.bay_requested = self.bay_allowed = self.bay_arrived = False
        self.rtg_released = False
        self.fault = ''
        self.last_seen = None
        self.last_callback = None
        self.callback_error = ''
        self.telemetry_pending = set()
        self.telemetry_at = {129:0,215:0}
        self.wait_started = None
        self.received = set()
        self.sent = set()
        self.transport = ExternalHTTP(config, self.health)

    @property
    def engine(self):
        return self.runtime.engine

    @property
    def order(self):
        return self.engine.orders.get(self.order_id)

    def snapshot(self):
        order = self.order
        box = self.engine.containers[order['container']] if order else None
        return {
            'listen': f'http://{self.config.bind}:{self.transport.port}',
            'peer': self.config.callback, 'last_seen': self.last_seen,
            'last_callback': self.last_callback, 'callback_error': self.callback_error,
            'qc': self.config.qc, 'rtg': self.config.rtg, 'vehicle': self.config.vehicle,
            'truck_number': self.config.truck_number, 'fault': self.fault, 'sample': self.sample,
            'phase': (order['wait_reason'] or order['step_name']) if order else '等待 IECS 下发桥吊任务',
            'status': order['status'] if order else 'waiting',
            'step': order['step'] if order else None, 'qc_ready': bool(order and order['step']==1),
            'rtg_ready': bool(order and order['step']==3), 'box': box['id'] if box else '',
            'location': box['location'] if box else '', 'ship_slot': self.qc['oisVlocation'] if self.qc else '',
            'yard_slot': self.rtg['soaTcntrPos'] if self.rtg else '',
            'qc_task': self.qc['taskId'] if self.qc else '', 'rtg_task': self.rtg['taskId'] if self.rtg else '',
            'tos_task': self.tos,
        }

    def health(self):
        with self.runtime.snapshot_lock:
            data = self.runtime.cached.get('external')
            return {'ready': bool(data), **(data or {})}

    @property
    def tos(self):
        return (self.qc or {}).get('tosTaskId', '')

    def event(self, direction, pid, message, **fields):
        self.engine.event('iecs_message', direction=direction, pid=pid, message=message, **fields)

    def send(self, pid, kind, message='', *, path='/process', once=None, **fields):
        if once is not None and once in self.sent:
            return
        body = {
            'pid': pid, 'machNo': self.config.qc if kind == 'QC' else self.config.rtg,
            'machType': kind, 'resultHost': self.config.bind, 'resultPort': str(self.transport.port),
            'curTime': str(time.time_ns() // 1_000_000), **fields,
        }
        self.transport.send(path, body)
        if once is not None:
            self.sent.add(once)
        if message:
            self.event('发出', pid, message)

    def fail(self, message):
        if not self.fault:
            self.fault = message
            self.engine.paused = True
            self.engine.event('iecs_fault', error=message)

    def poll(self):
        for _ in range(16):
            try:
                body, response, error = self.transport.results.get_nowait()
            except queue.Empty:
                break
            pid = body['pid']
            if not error:self.last_callback=time.time()
            if pid in (129,215):
                self.telemetry_pending.discard(pid)
                self.callback_error = error or ''
            elif error:
                self.fail(f'PID {pid} 回调失败：{error}')
            elif pid == 173:
                accepted = isinstance(response, dict) and str(response.get('cntrReleaseFlag')) == '1'
                if accepted:
                    self.land_allowed = True
                    self.event('响应', pid, 'IECS 准许桥吊放箱')
                else:
                    reason = response.get('cntrReleaseMsg', '') if isinstance(response, dict) else ''
                    self.fail('IECS 未准许桥吊放箱，箱子保留在吊具上。'+str(reason))
            elif pid == 116 and body.get('taskStatus')=='2':
                self.qc_released = True
        for _ in range(16):
            try:
                payload, result = self.transport.incoming.get_nowait()
            except queue.Empty:
                break
            if not result.set_running_or_notify_cancel():
                continue
            try:
                duplicate = self.receive(payload)
                result.set_result((200, {'success': True, 'duplicate': duplicate}))
            except (ValueError, KeyError, TypeError) as exc:
                message = str(exc)
                self.event('拒绝', payload.get('pid'), message)
                result.set_result((400, {'success': False, 'message': message}))
            except Exception as exc:
                self.fail(str(exc))
                result.set_result((500, {'success': False, 'message': 'Twin 执行失败，请查看工作台'}))

    def receive(self, payload):
        pid = payload.get('pid')
        kind = payload.get('machType')
        if kind not in ('QC','RTG'):
            raise ValueError('machType 必须为 QC 或 RTG')
        permitted, machine = (self.QC_IN,self.config.qc) if kind=='QC' else (self.RTG_IN,self.config.rtg)
        if type(pid) is not int or pid not in permitted or str(payload.get('machNo')) != machine:
            raise ValueError('接口 PID 或设备编号与本端配置不符')
        self.last_seen = time.time()
        if pid in (156,243):
            return False
        key = json.dumps({k:v for k,v in payload.items() if k not in ('curTime','resultHost','resultPort')},
                         sort_keys=True, ensure_ascii=False)
        if key in self.received:
            return True
        if self.fault:
            raise ValueError('当前联调已冻结，请查看工作台')
        if pid == 114:
            self.accept_qc(payload)
        elif not self.order:
            raise ValueError('请先下发桥吊卸船任务 114')
        elif pid == 211:
            self.accept_rtg(payload)
        elif pid in (120,219):
            self.vehicle_report(pid, payload)
        elif pid in (123,220):
            self.align(pid, payload)
        elif pid in (146,229):
            task = self.qc if pid == 146 else self.rtg
            if not task or _id(payload.get('taskId'),'taskId') != task['taskId']:
                raise ValueError('回执设备任务不匹配')
            if str(payload.get('updateStatus')) == '0':
                self.fail(f'IECS 拒绝任务回报 {pid}')
        elif pid == 235:
            self.bay_reply(payload)
        elif pid == 226:
            if 'rtg-release' not in self.sent or not self.rtg or _id(payload.get('taskId'),'taskId') != self.rtg['taskId']:
                raise ValueError('RTG 可驶离回执任务不匹配')
            self.check_vehicle(payload)
            if _id(payload.get('cntrNo'),'cntrNo') != self.order['container']:
                raise ValueError('RTG 可驶离回执箱号不匹配')
            self.rtg_released = True
        # 127/222 acknowledge receipt of the guide; only 123/220 establish position.
        elif pid in (127,222):
            self.check_vehicle(payload)
            expected = self.vehicle_task if pid==127 else self.rtg_vehicle_task
            if _id(payload.get('taskId'),'taskId') != expected:
                raise ValueError('车辆引导回执任务不匹配')
        self.received.add(key)
        self.event('收到', pid, '已处理')
        return False

    def task(self, payload, pid):
        tasks = payload.get('taskList')
        if not isinstance(tasks, list) or len(tasks)!=1 or not isinstance(tasks[0],dict):
            raise ValueError(f'PID {pid} 首版只支持单条任务')
        task = dict(tasks[0])
        task['taskId'] = _id(task.get('taskId'),'taskId')
        if task.get('soaWorkType') != 'DSCH' or str(task.get('soaTaskType',1 if pid==114 else 0)) != ('1' if pid==114 else '0'):
            raise ValueError('首版仅接收新增卸船任务 DSCH')
        return task

    def accept_qc(self, payload):
        task = self.task(payload, 114)
        if self.qc:
            if task['taskId'] == self.qc['taskId'] and task == self.qc:
                return
            raise ValueError('每次联调仅执行一车一箱，请结束本轮后重新开启')
        if str(task.get('taskType',1))!='1' or str(task.get('soaWorkflow',1))!='1' or task.get('soaIsplatform') in (1,'1'):
            raise ValueError('首版只支持单吊、桥吊直落集卡')
        if task.get('soaHandlearea','TRUCK') not in ('TRUCK','',None):
            raise ValueError('首版仅支持桥吊集卡交接区')
        if task.get('soaCntrnoLb') or task.get('soaCntrnoWs') or task.get('soaCntrnoLs'):
            raise ValueError('首版只支持一个集装箱')
        box = _id(task.get('soaCntrnoHb'),'soaCntrnoHb')
        if not re.fullmatch(r'[A-Z0-9]{1,24}', box):
            raise ValueError('箱号应为大写字母或数字')
        source = _id(task.get('oisVlocation'),'oisVlocation')
        if source not in self.sources:
            raise ValueError('船箱位不在 STS_01 可达的 20 尺顶层箱位内')
        if task.get('vehicleType','ITK') != 'ITK':
            raise ValueError('首版仅支持一辆内部集卡 ITK')
        if task.get('soaTruckHb') and str(task['soaTruckHb']) != self.config.vehicle:
            raise ValueError('任务车号与联调映射不符')
        if self.guard:
            self.guard()
        self.order_id = self.runtime.submit_order('unload', 'QC:'+task['taskId'],
                            source_path=self.sources[source], container_id=box, vehicle_id=self.vehicle_id)
        self.qc = task
        self.order['external_ship_slot'] = source
        self.runtime.pickups[self.order_id].allow_place = self.allow_landing
        self.send(115,'QC','桥吊接单',taskId=task['taskId'],operationStatus=2,
                  reportTime=payload.get('curTime',''),checkResultMsg='')

    def accept_rtg(self, payload):
        task = self.task(payload, 211)
        if self.rtg:
            if task == self.rtg:return
            raise ValueError('已绑定一条 RTG 任务')
        if str(task.get('taskType',0))!='0' or task.get('soaCntrnoHb')!=self.order['container']:
            raise ValueError('RTG 任务必须对应本轮卸船箱号')
        if task.get('tosTaskId') and self.tos and str(task['tosTaskId'])!=str(self.tos):
            raise ValueError('RTG 与桥吊的 TOS 任务不匹配')
        if task.get('qcNo') and str(task['qcNo'])!=self.config.qc:
            raise ValueError('RTG 任务的桥吊号不匹配')
        for field in ('vehicleNo','soaTruckHb'):
            if task.get(field) and str(task[field])!=self.config.vehicle:
                raise ValueError('RTG 任务车号不匹配')
        if task.get('soaCntrSize') not in (None,'',20,'20') or task.get('soaTruckType','ITK')!='ITK':
            raise ValueError('RTG 任务须为 20 尺箱和内集卡')
        target_id = _yard_id(task.get('soaTcntrPos'))
        bay_map = task.get('soaTgtbayMap')
        if bay_map is not None:
            if not isinstance(bay_map,str) or not re.fullmatch(r'[0-9]{6}',bay_map):
                raise ValueError('目标贝位箱层图应为六位数字字符串')
            if bay_map[0]!='0':
                raise ValueError('IECS 箱层图显示目标首排有箱')
        height = self.runtime.pickups[self.order_id].height
        target = next((slot for slot in self.runtime.free_yard_slots(height,self.order)
                       if slot['id']==target_id),None)
        if target is None:
            raise ValueError('指定场箱位已占用或超出本轮可达范围（20 尺、首排、第 1 层）')
        # Align the trailer's front slot with the destination bay. The gantry
        # then stays over that same bay from truck pickup through yard placement.
        target['stop'][1] += self.engine.containers[self.order['container']]['load_forward_m']
        reservations = self.engine.reserved
        reservations.pop(self.order['target']['id'],None)
        self.order['target'] = target
        reservations[target_id] = self.order_id
        self.rtg = task
        self.send(212,'RTG','RTG 接单',taskId=task['taskId'],tosTaskId=task.get('tosTaskId',self.tos),
                  operationStatus=2,reportTime=payload.get('curTime',''),checkResultMsg='')

    def check_vehicle(self, payload):
        if _id(payload.get('vehicleNo'),'vehicleNo')!=self.config.vehicle:
            raise ValueError('车辆编号与本轮联调映射不符')

    def vehicle_report(self, pid, payload):
        self.check_vehicle(payload)
        report = str(payload.get('reportStatus'))
        if report not in (('0','1','2') if pid==120 else ('0','1')):
            raise ValueError('无效的车辆报到状态')
        if pid == 120:
            vehicle_task = _id(payload.get('taskId'),'taskId')
            if self.vehicle_task and self.vehicle_task!=vehicle_task:
                raise ValueError('桥吊车辆任务 ID 发生变化')
            if str(payload.get('reportType',1))!='1':
                raise ValueError('首版暂不支持车辆报到取消')
            self.vehicle_task = vehicle_task
            self.qc_dispatched = True
        else:
            if not self.qc_dispatched:
                raise ValueError('集卡须先向桥吊报到')
            self.rtg_dispatched = True

    def align(self, pid, payload):
        qc = pid == 123
        kind = 'QC' if qc else 'RTG'
        self.check_vehicle(payload)
        task = _id(payload.get('taskId'),'taskId')
        if qc and task!=self.vehicle_task:
            raise ValueError('桥吊车辆任务 ID 不匹配')
        if not qc and (not self.rtg or _id(payload.get('rtgTaskId'),'rtgTaskId')!=self.rtg['taskId']):
            raise ValueError('RTG 车辆对位必须关联本轮 RTG 设备任务')
        if not qc:
            if self.rtg_vehicle_task and task!=self.rtg_vehicle_task:
                raise ValueError('RTG 车辆任务 ID 不匹配')
            position = payload.get('vehicleYlocation')
            if position and position not in (self.bay_position(),self.order['target']['id'].split('/')[0]):
                if _yard_id(position)!=self.order['target']['id']:
                    raise ValueError('RTG 车辆对位贝位与任务不符')
        if self.order['step'] != (1 if qc else 3):
            raise ValueError('集卡尚未到达此设备的虚拟交接点')
        idx, status = str(payload.get('cmdIdx')), str(payload.get('cmdStatus'))
        if (idx,status) not in (('1','1'),('2','3')):
            raise ValueError('首版仅接受粗停 (1,1) 和精停 (2,3)')
        guide = (kind,'guide')
        if status == '3' and guide not in self.sent:
            raise ValueError('先完成粗停确认和精停引导')
        if not qc:self.rtg_vehicle_task = task
        common = {'taskId':task,'vehicleNo':self.config.vehicle,
                  'tosTaskId':payload.get('tosTaskId','' if qc else self.tos)}
        self.send(126 if qc else 221,kind,'已接收车辆到位信息',cmdIdx=int(idx),
                  reportTime=payload.get('curTime',payload.get('sotReqTm','')),**common)
        if status == '1':
            guide_fields = ({'soiWorkflow':1,'cntrSize':1,'workCntrNum':1} if qc else {'sotTruckCmd2':'1'})
            self.send(117 if qc else 214,kind,'虚拟交接点已对准，等待集卡精停确认',once=guide,
                      sotTruckCmdIdx=2,sotTruckCmd='1',sotTruckDist=0,bootType=1,
                      sotReqTm=str(time.time_ns()//1_000_000),**common,**guide_fields)
        elif qc:
            self.qc_precise = True
        else:
            self.rtg_precise = True

    def qc_result(self, status, message, depart=0):
        self.send(116,'QC',message,once=('qc',status),taskId='-1' if status=='1' else self.qc['taskId'],
                  soiId=self.tos,soiCntrno=self.order['container'],soiWorkType='DSCH',
                  taskStatus=status,departureStatus=depart,soaTruck=self.config.vehicle,
                  soiWorkflow=1,cntrSize=1,workCntrNum=1,vehicleTaskId=self.vehicle_task,
                  soiWorkStat='1' if status=='3' else None)

    def allow_landing(self):
        if not self.land_requested:
            self.qc_result('97','桥吊已识别准确箱号，IECS 可提前分配场箱位')
            self.send(173,'QC','申请将箱子放到集卡',path='/landBoxApply',
                      cntrNo=self.order['container'],vehicleNo=self.config.vehicle,
                      laneNum=self.qc.get('soaWorklane'),workCntrNum=1)
            self.land_requested = True
        return self.land_allowed

    def bay_position(self):
        target = self.order['target']
        return target['block'] + f"{target['bay']:02d}"

    def bay_reply(self, payload):
        if not self.bay_requested:
            raise ValueError('本轮尚未申请 RTG 换贝')
        status = str(payload.get('rtgState'))
        if status in ('0','1','4','5'):
            return
        if status != '2':
            self.fail('IECS 未批准 RTG 换贝：'+str(payload.get('applyResultMsg','')))
            return
        dest = str(payload.get('usableRtgTargetBay',''))
        if dest not in (self.bay_position(), self.order['target']['id'].split('/')[0]):
            self.fail('IECS 批准的 RTG 贝位与任务不一致')
            return
        self.bay_allowed = True

    def step_gate(self, order):
        if self.fault:return self.fault
        step = order['step']
        if step==0 and not self.qc_dispatched:return '等待 IECS 向桥吊报到（120）'
        if step==1 and not self.qc_precise:return '等待桥吊精停确认（123）'
        if step==2:
            if not self.qc_released:return '等待 IECS 接收桥吊放行（116）'
            if not (self.rtg and self.rtg_dispatched):return '等待 RTG 派单和车辆报到（211 / 219）'
        if step==3:
            if not self.rtg_precise:return '等待 RTG 精停确认（220）'
            if not self.bay_allowed:return '等待 IECS 批准 RTG 换贝（235）'
        if step==4 and not self.rtg_released:return '等待 IECS 接收 RTG 放行（226）'
        return ''

    def heartbeat(self):
        now = time.monotonic()
        for pid,interval in ((129,3),(215,.2)):
            if (now < self.telemetry_at[pid]+interval or pid in self.telemetry_pending or
                    not self.transport.outgoing.empty()):
                continue
            self.telemetry_at[pid] = now
            if pid == 129:
                self.send(129,'QC',qcWorkType=1,deviceState=2 if self.fault else 1)
            else:
                self.send(215,'RTG',**self.rtg_telemetry())
            self.telemetry_pending.add(pid)

    def rtg_telemetry(self):
        from sts_yard_delivery import GANTRY
        rt = self.runtime
        gantry = rt.stage.GetPrimAtPath(GANTRY)
        bounds = rt.UsdGeom.BBoxCache(rt.Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(gantry).ComputeAlignedRange()
        min_pt, max_pt = bounds.GetMin(), bounds.GetMax()
        # The same east and north axes as the demo device map. Coordinates are virtual.
        def coordinate(x,y):
            return f'{math.degrees(y/6378137):.9f},{math.degrees(-x/6335439.327):.9f}'
        corners = ' '.join(coordinate(x,y) for x,y in (
            (min_pt[0]-.2,min_pt[1]-.2),(max_pt[0]+.2,min_pt[1]-.2),
            (max_pt[0]+.2,max_pt[1]+.2),(min_pt[0]-.2,max_pt[1]+.2)))
        step = self.order['step'] if self.order else None
        rig = rt.yard_drops.get(self.order_id)
        moving = bool(rig and rig.phase=='RTG align with truck')
        center = bounds.GetMidpoint()
        return {'sosRtgId':self.config.rtg, 'sosWorkMode':'1','sosOnlineStatus':'1',
                'sosWorkStatus':'3' if self.fault else '1' if step==3 and self.bay_allowed else '2',
                'coordinateState':'1','sosposition':self.bay_position() if self.bay_arrived else '',
                'rtgCenterCoordinate':coordinate(center[0],center[1]), 'rtgScopeCoordinate':corners,
                'sosUpdateTm':str(time.time_ns()//1_000_000),'mgRunning':'1' if moving else '0',
                'handleControl':'off','efficiencyMode':'1','energyMode':'1'}

    def observe(self):
        order = self.order
        if not order or self.fault:
            self.heartbeat()
            return
        step = order['step']
        if step==1 and self.qc_precise:self.qc_result('1','桥吊开始卸船')
        if step>=2:
            self.qc_result('2','箱子已交给集卡，桥吊允许驶离',depart=1)
            self.qc_result('3','桥吊卸船任务完成',depart=1)
        if step==3 and self.rtg_precise and not self.bay_requested:
            self.send(234,'RTG','申请移动空吊具 RTG 至交接贝位',rtgState=2,moveType=0,
                      rtgTargetPosition=self.bay_position())
            self.bay_requested = True
        if step==3 and self.bay_allowed:
            self.send(234,'RTG','RTG 换贝中',once='bay-moving',rtgState=0,moveType=0,
                      rtgTargetPosition=self.bay_position())
            self.rtg_result('1','RTG 开始进箱')
            rig = self.runtime.yard_drops.get(self.order_id)
            if rig and rig.phase not in ('RTG raise clear','RTG align with truck'):
                self.bay_arrived = True
                self.send(234,'RTG','RTG 已到交接贝位',once='bay-arrived',rtgState=1,moveType=0,
                          rtgTargetPosition=self.bay_position())
        if step>=4 and ('rtg','3') not in self.sent:
            cargo = self.engine.containers[order['container']]
            rt = self.runtime
            prim = rt.stage.GetPrimAtPath(cargo.get('visual_prim','/'))
            if not prim or rt.UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':
                self.fail('实际落箱模型不可见，暂停 RTG 完工回报')
                return
            point = rt.UsdGeom.BBoxCache(rt.Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(
                prim).ComputeAlignedRange().GetMidpoint()
            if cargo['location']!=order['target']['id'] or math.dist(point,order['target']['point'])>.02:
                self.fail('实际落箱位置与 IECS 任务不符，暂停 RTG 完工回报')
                return
            self.rtg_result('3','RTG 已落箱并抬起吊具',realOisYlocation=self.rtg['soaTcntrPos'])
            self.send(225,'RTG','RTG 交接完成，集卡可驶离',once='rtg-release',
                      taskId=self.rtg['taskId'],tosTaskId=self.rtg.get('tosTaskId',self.tos),
                      vehicleNo=self.config.vehicle,cntrNo=order['container'],workType='I')
        waiting = self.step_gate(order)
        if step==1 and self.land_requested and not self.land_allowed:
            waiting = '等待 IECS 放箱许可'
        if waiting:
            now = time.monotonic()
            if not self.wait_started or self.wait_started[0]!=waiting:
                self.wait_started = (waiting,now)
            elif now-self.wait_started[1]>120:
                self.fail(waiting+'超时')
        else:
            self.wait_started = None
        self.heartbeat()

    def rtg_result(self, status, message, **fields):
        self.send(213,'RTG',message,once=('rtg',status),taskId=self.rtg['taskId'],taskType=0,
                  taskStatus=status,soiCntrno=self.order['container'],soiTruck=self.config.vehicle,
                  soiWorkType='DSCH',soiWorkflow=1,grabBoxNum=int(status=='3'),**fields)

    def close(self):
        self.transport.close()
