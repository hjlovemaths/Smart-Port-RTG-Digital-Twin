"""Deterministic IECS scheduling kernel; independent of Kit and USD.

Conservative demo: one moving vehicle owns the road network, while independent
equipment transfers may run concurrently. This is not a swept-volume planner.
"""
import copy
import json
import math
import sqlite3
from collections import deque
# Kit may cache the services directory before a newly added sibling exists.
# Resolve the calibration file explicitly instead of depending on that cache.
import importlib.util
import sys
from pathlib import Path
if 'iecs_geometry' not in sys.modules:
    _geometry_path=Path(__file__).resolve().with_name('iecs_geometry.py')
    _geometry_spec=importlib.util.spec_from_file_location('iecs_geometry',_geometry_path)
    _geometry_module=importlib.util.module_from_spec(_geometry_spec)
    sys.modules['iecs_geometry']=_geometry_module
    try:
        _geometry_spec.loader.exec_module(_geometry_module)
    except Exception:
        sys.modules.pop('iecs_geometry',None)
        raise
from iecs_geometry import TRUCK_LANE_X,YARD_ROW_ONE_X,GATE_STOP,GATE_EXIT_DISTANCE

FLOWS = {'unload': '卸船入场', 'load': '堆场装船', 'gate_in': '外车进场落箱',
         'gate_out': '外车提箱出场', 'transfer': '场内转栈'}
BLOCKS = {'1C': (-5.375,126.05), '2C': (126.05,256.30),
          '3C': (256.30,386.55), '4C': (386.55,496.30)}


def distance(a,b):
    return math.dist(a,b)


def yard(block='2C', bay=40):
    lower,upper=BLOCKS[block]
    if bay % 2 or not 2 <= bay <= (40 if block=='4C' else 90):
        raise ValueError('40FT 演示箱需要有效偶数贝位')
    y=lower+3.7+((bay-2)/2)*2.675+2.475
    return {'id':f'{block}{bay:03d}/R1/T1','kind':'yard', 'block':block,
            'bay':bay,'point':[YARD_ROW_ONE_X,y,.57], 'stop':[TRUCK_LANE_X,y,.085]}


def quay(index=0):
    y=170+index*10
    return {'id':f'SHIP/DEMO/{index:03d}','kind':'quay',
            'point':[-246,y,1.1], 'stop':[-231.15,y,.085]}


def gate(index=0):
    return {'id':f'GATE/{index:03d}','kind':'gate',
            'point':[GATE_STOP[0],GATE_STOP[1],.57], 'stop':list(GATE_STOP)}


def route(start,end,source=None,target=None):
    """Work lanes always decrease Y. Reach an upper entrance before the stop."""
    if target and target['kind']=='exit':
        # Beyond the gate, keep driving east. Never re-enter the collector.
        if end[0]<start[0] or abs(end[1]-start[1])>1e-6:
            raise ValueError('Invalid outward gate exit segment')
        return [list(start),list(end)]
    if (source and target and source['kind']==target['kind']=='yard'
            and source.get('block')==target.get('block')
            and abs(start[0]-end[0])<1e-6
            and abs(start[0]-source['stop'][0])<1e-6
            and abs(end[0]-target['stop'][0])<1e-6
            and end[1]<=start[1]):
        # Same work lane, downstream destination: no exit/re-entry loop.
        # The scheduler still reserves the road before executing this segment.
        return [list(start),list(end)]
    points=[list(start)]
    def add(x,y):
        p=[x,y,.085]
        if distance(points[-1],p)>1e-6: points.append(p)
    if source and source['kind']=='yard':
        lower,_=BLOCKS[source['block']]
        add(-3.75,lower-1.85); add(116.15,lower-1.85)
    elif source and source['kind']=='quay':
        # Continue in the lane's painted direction to the next intersection.
        southbound=start[0]<-232.9
        crossings=(-5.375,126.05,256.30,386.55)
        candidates=[y for y in crossings if y<start[1]-.5] if southbound else [y for y in crossings if y>start[1]+.5]
        crossing=max(candidates) if southbound and candidates else min(candidates) if candidates else None
        if crossing is None:raise ValueError('No legal quay exit in current travel direction')
        add(start[0],crossing-1.85);add(119.85,crossing-1.85)
    elif source and source['kind']=='gate':
        add(116.15,388.4)
    if target and target['kind']=='yard':
        _,upper=BLOCKS[target['block']]
        approach=upper+1.85
        collector=119.85 if approach>=points[-1][1] else 116.15
        add(collector,points[-1][1]); add(collector,approach)
        add(-3.75,approach); add(end[0],end[1])
    elif target and target['kind']=='quay':
        collector=119.85 if 256.30>=points[-1][1] else 116.15
        add(collector,points[-1][1]); add(collector,258.15)
        add(-238.15,258.15); add(-238.15,end[1])
        add(end[0],end[1])
    else:
        collector=119.85 if end[1]>=points[-1][1] else 116.15
        add(collector,points[-1][1]); add(collector,end[1]); add(end[0],end[1])
    return rounded_path(points)


def rounded_path(points,radius=3.):
    result=[points[0]]
    for a,b,c in zip(points,points[1:],points[2:]):
        u=[b[i]-a[i] for i in range(3)];v=[c[i]-b[i] for i in range(3)]
        lu=math.sqrt(sum(x*x for x in u));lv=math.sqrt(sum(x*x for x in v))
        if lu<1e-6 or lv<1e-6:continue
        cut=min(radius,lu*.3,lv*.3)
        entry=[b[i]-u[i]*cut/lu for i in range(3)]
        leave=[b[i]+v[i]*cut/lv for i in range(3)]
        result.append(entry)
        for n in range(1,17):
            t=n/16
            result.append([(1-t)**2*entry[i]+2*t*(1-t)*b[i]+t*t*leave[i] for i in range(3)])
    result.append(points[-1]);return result


class IECS:
    def __init__(self,db=':memory:'):
        self.db=sqlite3.connect(str(db))
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time REAL, kind TEXT, payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT)')
        self.events=deque(maxlen=100)
        self.speed=1.; self.paused=False; self.time=0.; self.road_owner=None
        self.vehicles={}; self.orders={}; self.containers={}; self.reserved={}; self.equipment={}
        self.sequence=0
        self.transfer_adapter=None
        self.motion_guard=None
        for i in range(1,21):
            home=[138.639, (65 if i<=10 else 160)+((i-1)%10)*5.4+2.2,.085]
            self.vehicles[f'ITT-{i:03d}']={'id':f'ITT-{i:03d}','number':f'{i:02d}',
               'home':home,'position':home[:],'heading':-90.,'status':'idle','order':None,'load':None}
        self.checkpoint()

    def event(self,kind,**payload):
        item={'time':round(self.time,2),'kind':kind,**payload}; self.events.append(item)
        self.db.execute('INSERT INTO events(time,kind,payload) VALUES(?,?,?)',
                        (self.time,kind,json.dumps(payload,ensure_ascii=False)))
        self.db.commit()

    def checkpoint(self):
        self.db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?)',
                        (json.dumps(self.snapshot(),ensure_ascii=False),))
        self.db.commit()

    def submit(self,flow,request_id=None):
        if flow not in FLOWS: raise ValueError('未知流程')
        if request_id:
            existing=next((o for o in self.orders.values() if o.get('request_id')==request_id),None)
            if existing:return existing['id']
        self.sequence+=1; n=self.sequence
        source=quay(n) if flow=='unload' else gate(n) if flow=='gate_in' else yard('2C',2+((n-1)%40)*2)
        target=yard('3C',2+((n-1)%40)*2) if flow in ('unload','gate_in','transfer') else quay(n) if flow=='load' else gate(n)
        if source['id'] in self.reserved or target['id'] in self.reserved:
            raise ValueError('源箱位或目标箱位已被预约')
        if any(c['location']==target['id'] for c in self.containers.values()):
            raise ValueError('目标箱位已占用')
        # Demonstration stock is explicit and isolated from production inventory.
        box=f'IECS-DEMO-{n:04d}'; oid=f'IECS-{n:05d}'
        self.containers[box]={'id':box,'size_ft':40,'location':source['id'],
            'position':source['point'][:],'heading':0.,'owner':None,'version':0}
        self.orders[oid]={'id':oid,'request_id':request_id,'flow':flow,'name':FLOWS[flow],
            'status':'queued','vehicle':None,'container':box,'source':source,'target':target,
            'step':0,'step_name':'等待派车','progress':0.,'path':[],'path_index':0,
            'transfer_start':None,'transfer_end':None,'wait_reason':'','error':None}
        self.reserved[source['id']]=oid; self.reserved[target['id']]=oid
        self.event('order_created',order=oid,flow=flow,box=box); self.checkpoint()
        return oid

    def control(self,action,value=None):
        if action=='pause':self.paused=True
        elif action=='resume':self.paused=False
        elif action=='speed':
            value=float(value)
            if not math.isfinite(value) or not .1<=value<=20:raise ValueError('倍率范围 0.1–20')
            self.speed=value
        elif action=='reset':
            for v in self.vehicles.values():
                if v['id'].startswith('ITT'):
                    v.update(position=v['home'][:],heading=-90.,status='idle',order=None,load=None)
            self.vehicles={k:v for k,v in self.vehicles.items() if k.startswith('ITT')}
            self.orders.clear();self.containers.clear();self.reserved.clear();self.equipment.clear()
            self.road_owner=None;self.paused=False;self.time=0.;self.sequence=0
        else:raise ValueError('未知控制')
        self.event(action,value=value);self.checkpoint()

    def assign(self,o):
        if o['flow'] in ('gate_in','gate_out'):
            vid=f'EXT-{o["id"][-5:]}'
            home=[151,388.4,.085]
            self.vehicles[vid]={'id':vid,'number':vid,'home':home,'position':home[:],
                    'heading':-90.,'status':'idle','order':None,'load':None}
            v=self.vehicles[vid]
        else:
            idle=[v for v in self.vehicles.values() if v['status']=='idle' and v['id'].startswith('ITT')]
            if not idle:o['wait_reason']='等待空闲集卡';return
            v=min(idle,key=lambda v:(distance(v['position'],o['source']['stop']),v['id']))
        v.update(status='working',order=o['id']);o.update(vehicle=v['id'],status='running',wait_reason='')
        if o['flow']=='gate_in':
            v['load']=o['container'];c=self.containers[o['container']]
            c.update(location=v['id'],owner=v['id'],position=[*v['position'][:2],1.],version=1)
            o['step']=2
        self.event('assigned',order=o['id'],vehicle=v['id'])

    def _move(self,o,v,end,dt,source=None,target=None):
        if self.road_owner not in (None,o['id']):
            o['wait_reason']='等待道路预约';return False
        self.road_owner=o['id'];o['wait_reason']=''
        if not o['path']:
            o['path']=route(v['position'],end,source,target);o['path_index']=1
        remaining=dt*8
        while remaining>0 and o['path_index']<len(o['path']):
            dest=o['path'][o['path_index']];length=distance(v['position'],dest)
            if length<1e-8:o['path_index']+=1;continue
            delta=[dest[i]-v['position'][i] for i in range(3)]
            amount=min(remaining,length,.25) if self.motion_guard else min(remaining,length)
            proposed=[v['position'][i]+delta[i]*amount/length for i in range(3)]
            heading=math.degrees(math.atan2(delta[0],-delta[1]))
            conflict=self.motion_guard(v,proposed,heading) if self.motion_guard else None
            if conflict:
                o['wait_reason']='Collision wait: '+str(conflict)
                break
            v['position']=proposed
            v['heading']=heading
            remaining-=amount
            if amount>=length-1e-8:o['path_index']+=1
        if v['load']:
            c=self.containers[v['load']];c['position']=[*v['position'][:2],c.get('carry_height',1.0)];c['heading']=v['heading']
        if o['path_index']>=len(o['path']):
            self.road_owner=None;o['path']=[];return True
        return False

    def _transfer(self,o,v,pick,dt):
        location=o['source'] if pick else o['target']
        eq='QC-01' if location['kind']=='quay' else 'RTG-'+location.get('block','GATE')
        if self.equipment.get(eq) not in (None,o['id']):
            o['wait_reason']='等待 '+eq;return False
        self.equipment[eq]=o['id'];o['wait_reason']='';c=self.containers[o['container']]
        if self.transfer_adapter and o.get('source_prim'):
            done=self.transfer_adapter(o,v,pick,dt)
            if not done:return False
            c.update(location=v['id'] if pick else location['id'],owner=v['id'] if pick else None,
                     heading=v['heading'] if pick else 0.,version=c['version']+1)
            if not pick:c['position']=location['point'][:]
            v['load']=c['id'] if pick else None;self.equipment.pop(eq,None)
            self.event('transfer_completed',order=o['id'],box=c['id'],location=c['location'])
            return True
        if o['transfer_start'] is None:
            o['transfer_start']=c['position'][:]
            o['transfer_end']=[*v['position'][:2],1.] if pick else location['point'][:]
            self.event('transfer_started',order=o['id'],equipment=eq,pick=pick)
        o['progress']=min(1.,o['progress']+dt/8.)
        t=o['progress'];a=o['transfer_start'];b=o['transfer_end'];high=max(a[2],b[2])+4
        if t<.3:
            p=[a[0],a[1],a[2]+(high-a[2])*t/.3]
        elif t<.7:
            q=(t-.3)/.4;p=[a[i]+(b[i]-a[i])*q for i in range(2)]+[high]
        else:p=[b[0],b[1],high+(b[2]-high)*(t-.7)/.3]
        c.update(position=p,location='SPREADER/'+eq,owner=eq)
        if t>=1:
            c.update(position=b[:],location=v['id'] if pick else location['id'],
                     owner=v['id'] if pick else None,heading=v['heading'] if pick else 0.,version=c['version']+1)
            v['load']=c['id'] if pick else None;self.equipment.pop(eq,None)
            self.event('transfer_completed',order=o['id'],box=c['id'],location=c['location'])
            o['transfer_start']=None;o['transfer_end']=None;o['progress']=0.;return True
        return False

    def tick(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('Invalid dt')
        if self.paused:return
        dt=min(dt,.25)*self.speed;self.time+=dt
        for o in self.orders.values():
            if o['status']=='queued':self.assign(o)
            if o['status']!='running':continue
            v=self.vehicles[o['vehicle']];step=o['step'];done=False
            if step==0:
                o['step_name']='驶往源交接点';done=self._move(o,v,o['source']['stop'],dt,target=o['source'])
            elif step==1:
                o['step_name']='取箱交接';done=self._transfer(o,v,True,dt)
            elif step==2:
                o['step_name']='载箱运输';done=self._move(o,v,o['target']['stop'],dt,o['source'],o['target'])
            elif step==3:
                if o['flow']=='gate_out':
                    o['step_name']='载箱闸口交接';done=True
                else:
                    o['step_name']='目标放箱';done=self._transfer(o,v,False,dt)
            elif step==4:
                if o['flow']=='gate_out':
                    o['step_name']='载箱直行出闸'
                    end=[o['target']['stop'][0]+GATE_EXIT_DISTANCE,o['target']['stop'][1],.085]
                    done=self._move(o,v,end,dt,source=o['target'],target={'kind':'exit'})
                else:
                    o['step_name']='空车归位 / 出闸';done=self._move(o,v,v['home'],dt,source=o['target'])
            if done:
                o['step']+=1;self.event('step_completed',order=o['id'],step=step)
                if o['step']==5:
                    o.update(status='completed',step_name='完成',wait_reason='')
                    v.update(status='idle' if v['id'].startswith('ITT') else 'departed',order=None)
                    if o['flow']=='gate_out':
                        v['load']=None
                        self.containers[o['container']].update(owner=None,version=2)
                    if v['id'].startswith('ITT'):v['heading']=-90.
                    self.reserved.pop(o['source']['id'],None);self.reserved.pop(o['target']['id'],None)
                    if o['target']['kind']=='gate':self.containers[o['container']]['location']='EXIT/'+o['container']
                    self.event('order_completed',order=o['id'])
        if int(self.time)!=int(self.time-dt):self.checkpoint()

    def snapshot(self):
        return copy.deepcopy({'name':'IECS','simulation_time':self.time,'paused':self.paused,
            'speed':self.speed,'road_owner':self.road_owner,'vehicles':list(self.vehicles.values()),
            'orders':list(self.orders.values()),'containers':list(self.containers.values()),
            'reservations':self.reserved,'equipment':self.equipment,'events':list(self.events),
            'mode':'kinematic_demo','traffic_policy':'whole_network_mutex'})

    def close(self):
        self.checkpoint();self.db.close()
