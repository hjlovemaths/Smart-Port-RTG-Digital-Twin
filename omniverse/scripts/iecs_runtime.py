"""IECS native Kit console, HTTP command interface and session-only scene adapter.

Execute in Kit Script Editor: exec(open(PATH, encoding='utf-8').read())
Simulation stock is separate from yard_inventory.live.json; handling is a visual
lift/transfer, not PLC feedback or original crane joint actuation.
"""
import builtins
import json
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import importlib.util

PROJECT = Path(__file__).resolve().parents[2]
for _name in ('iecs_collision','iecs_inventory'):
    if _name not in sys.modules:
        _s=importlib.util.spec_from_file_location(_name,PROJECT/'services'/(_name+'.py'))
        _m=importlib.util.module_from_spec(_s);sys.modules[_name]=_m
        try:_s.loader.exec_module(_m)
        except Exception:
            sys.modules.pop(_name,None)
            raise
if str(PROJECT/'services') not in sys.path:sys.path.insert(0,str(PROJECT/'services'))
# The diagnostic module is also new; register by absolute source path so a
# running Kit session need not restart its extension import machinery.
if 'iecs_scene_checks' not in sys.modules:
    _spec=importlib.util.spec_from_file_location('iecs_scene_checks',Path(__file__).with_name('iecs_scene_checks.py'))
    _module=importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name]=_module
    try:_spec.loader.exec_module(_module)
    except Exception:
        sys.modules.pop(_spec.name,None)
        raise
from iecs_core import IECS, FLOWS
from iecs_geometry import TRAILER_DECK_Z,QUAY_PICKUP_LANE_X,YARD_PAD_Z,CONTAINER_40_SIZE,GATE_STOP
FLOW_LABELS = {'unload':'Ship to Yard', 'load':'Yard to Ship',
               'gate_in':'Gate In / Drop Box', 'gate_out':'Gate Out / Pick Box',
               'transfer':'Yard Transfer'}
STEP_LABELS = ['Drive to Pickup','Pickup / Handover','Loaded Transport',
               'Drop / Handover','Return / Exit','Completed']


class Runtime:
    def __init__(self,stage,port=48730,with_ui=True,subscribe=True,db_path=None):
        from pxr import Gf,Sdf,Usd,UsdGeom
        self.Gf,self.Usd,self.UsdGeom=Gf,Usd,UsdGeom
        self.stage=stage;self.closed=False;self.commands=queue.Queue();self.last_error=''
        from iecs_scene_checks import inspect_scene
        self.scene_report=inspect_scene(stage)
        self.pickups={};self.ship_bindings={}
        self.yard_drops={}
        self.mixed_pending=[]
        from iecs_ship_pickup import BOX,bounds
        self.ship_home=list(bounds(stage,BOX).GetMidpoint())
        self.snapshot_lock=threading.Lock();self.cached={};self.models={};self.last_ui=0
        self.layer=Sdf.Layer.CreateAnonymous('iecs_demo.usda')
        missing=[f'/World/ParkedYardTrucks/Group{g}_Bay{b:02d}' for g in (1,2) for b in range(1,11)
                 if not stage.GetPrimAtPath(f'/World/ParkedYardTrucks/Group{g}_Bay{b:02d}')]
        if missing:raise ValueError('请先加载 parked_yard_trucks.usda：'+missing[0])
        (PROJECT/'outputs/iecs').mkdir(parents=True,exist_ok=True)
        self.engine=IECS(db_path or PROJECT/'outputs/iecs/iecs.sqlite3')
        self.engine.transfer_adapter=self.real_ship_transfer
        from iecs_collision import swept_conflict
        self.engine.motion_guard=lambda v,end,heading:swept_conflict(v,end,heading,self.engine.vehicles.values())
        self.stage.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        self.server=None;self.subscription=None;self.window=None
        try:
            self.start_http(port)
            if with_ui:self.make_ui()
            if subscribe:
                import omni.kit.app
                self.subscription=omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                    self.on_update,name='IECS scheduler')
                import omni.timeline
                omni.timeline.get_timeline_interface().pause()
            self.apply_scene();self.publish()
        except Exception:
            self.close();raise

    def publish(self):
        with self.snapshot_lock:
            self.cached=self.engine.snapshot();self.cached['error']=self.last_error
            self.cached['mixed_pending']=list(self.mixed_pending)
            self.cached['scene_checks']=self.scene_report

    def start_http(self,port):
        runtime=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def answer(self,code,data):
                payload=json.dumps(data,ensure_ascii=False).encode()
                self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            def do_GET(self):
                if self.path not in ('/state','/health'):return self.answer(404,{'error':'not found'})
                with runtime.snapshot_lock:snapshot=dict(runtime.cached)
                self.answer(200,snapshot if self.path=='/state' else {'name':'IECS','ready':not runtime.closed})
            def do_POST(self):
                if self.path!='/command':return self.answer(404,{'error':'not found'})
                try:
                    n=int(self.headers.get('Content-Length','0'))
                    if not 0<n<=8192:raise ValueError('invalid body size')
                    command=json.loads(self.rfile.read(n))
                    if not isinstance(command,dict):raise ValueError('command must be object')
                    runtime.commands.put(command)
                    self.answer(202,{'queued':True,'request_id':command.get('request_id')})
                except (ValueError,TypeError) as e:self.answer(400,{'error':str(e)})
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,name='IECS HTTP',daemon=True).start()

    def enqueue(self,action,**kwargs):self.commands.put({'action':action,**kwargs})

    def make_ui(self):
        import omni.ui as ui
        self.window=ui.Window('IECS - Fleet and Container Dispatch',width=760,height=680)
        with self.window.frame:
            with ui.VStack(spacing=8):
                ui.Label('IECS  |  Automated Standard Workflows',height=30)
                ui.Label('20 Internal Trucks | External Trucks | Slot Reservations | Road Mutex',height=24)
                ui.Label('Ship / Yard: real containers + STS_01 + aligned primary RTG.',height=24)
                with ui.HStack(height=32,spacing=4):
                    for flow in ('unload','load','gate_in'):
                        ui.Button(FLOW_LABELS[flow],clicked_fn=lambda f=flow:self.enqueue('submit',flow=f))
                with ui.HStack(height=32,spacing=4):
                    for flow in ('gate_out','transfer'):
                        ui.Button(FLOW_LABELS[flow],clicked_fn=lambda f=flow:self.enqueue('submit',flow=f))
                    ui.Button('Mixed Workload (5 Orders)',clicked_fn=lambda:self.enqueue('mixed'))
                with ui.HStack(height=32,spacing=4):
                    ui.Button('Pause',clicked_fn=lambda:self.enqueue('pause'))
                    ui.Button('Resume',clicked_fn=lambda:self.enqueue('resume'))
                    ui.Button('Reset Demo',clicked_fn=lambda:self.enqueue('reset'))
                    ui.Button('Focus Task',clicked_fn=self.focus)
                with ui.HStack(height=28,spacing=4):
                    ui.Button('Check Scene',clicked_fn=lambda:self.enqueue('check_scene'))
                    ui.Label('Resume checks scene consistency; Reset Demo restores original models.')
                with ui.HStack(height=28):
                    ui.Label('Speed',width=80)
                    for speed in (1,5,10,20):
                        ui.Button(f'{speed}×',clicked_fn=lambda s=speed:self.enqueue('speed',value=s))
                self.summary=ui.Label('',height=45,word_wrap=True)
                with ui.ScrollingFrame():
                    self.details=ui.Label('',word_wrap=True,alignment=ui.Alignment.LEFT_TOP)

    def focus(self):
        try:
            import omni.usd
            import omni.kit.viewport.utility as viewport
            orders=[o for o in self.engine.orders.values() if o['vehicle'] and o['status']=='running']
            path=self.vehicle_path(orders[0]['vehicle']) if orders else '/World/ParkedYardTrucks'
            omni.usd.get_context().get_selection().set_selected_prim_paths([path],False)
            viewport.frame_viewport_selection(viewport.get_active_viewport())
        except Exception as e:self.last_error=str(e)

    def vehicle_path(self,vid):
        if vid.startswith('ITT'):
            n=int(vid[-3:]);return f'/World/ParkedYardTrucks/Group{1 if n<=10 else 2}_Bay{(n-1)%10+1:02d}'
        return '/World/IECS/ExternalTrucks/'+vid.replace('-','_')

    def pose(self,path,position,heading=0,scale=None):
        xf=self.UsdGeom.Xformable(self.stage.GetPrimAtPath(path))
        if not xf:xf=self.UsdGeom.Xform.Define(self.stage,path)
        attrs={op.GetOpName():op for op in xf.GetOrderedXformOps()}
        t=attrs.get('xformOp:translate') or xf.AddTranslateOp()
        r=attrs.get('xformOp:rotateZ') or xf.AddRotateZOp()
        t.Set(self.Gf.Vec3d(*position));r.Set(heading)
        if scale is not None:
            s=attrs.get('xformOp:scale') or xf.AddScaleOp();s.Set(self.Gf.Vec3f(*scale))
            xf.SetXformOpOrder([t,r,s])
        else:xf.SetXformOpOrder([t,r])

    def apply_scene(self):
        from pxr import Sdf
        with self.Usd.EditContext(self.stage,self.layer):
            for v in self.engine.vehicles.values():
                path=self.vehicle_path(v['id'])
                if v['id'].startswith('EXT') and not self.stage.GetPrimAtPath(path):
                    prim=self.UsdGeom.Xform.Define(self.stage,path).GetPrim()
                    prim.GetReferences().AddReference(str(PROJECT/'omniverse/assets/resource/truck/RTG_Model_Lane_Truck.usdc'),'/root')
                    self.stage.OverridePrim(path+'/env_light').SetActive(False)
                base=v['position'][:];base[2]-=.14
                self.pose(path,base,v['heading'],(1,1.22,1))
                self.UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set(
                    'invisible' if v['status']=='departed' else 'inherited')
                if v['id'].startswith('ITT'):
                    label=f'/World/ParkedTruckNumbers/Truck{int(v["number"]):02d}'
                    if self.stage.GetPrimAtPath(label):self.pose(label,base,v['heading'],(1,1.22,1))
            for c in self.engine.containers.values():
                if c['id'] in self.ship_bindings:
                    pickup=self.ship_bindings[c['id']]
                    self.UsdGeom.Imageable(self.stage.GetPrimAtPath(pickup.box_path)).CreateVisibilityAttr().Set(
                        'invisible')
                    # Yard inventory separates the logical box and its textured
                    # sibling. Hide both while the unified visual follows cargo.
                    sibling=self.stage.GetPrimAtPath(pickup.box_path+'_ImportedModel')
                    if sibling:self.UsdGeom.Imageable(sibling).CreateVisibilityAttr().Set('invisible')
                    source=self.stage.GetPrimAtPath(pickup.box_path)
                    for detail in source.GetParent().GetChildren():
                        if detail.GetName().startswith(source.GetName()+'_'):
                            self.UsdGeom.Imageable(detail).CreateVisibilityAttr().Set('invisible')
                    self.apply_container_visual(c,pickup)
                    o=next(o for o in self.engine.orders.values() if o['container']==c['id'])
                    if o['flow']=='load' or o['step']>=2:
                        pickup.set_box_world(self.Gf.Vec3d(*c['position']),c['heading'])
                    continue
                path='/World/IECS/Containers/'+c['id'].replace('-','_')
                if not self.stage.GetPrimAtPath(path):
                    box=self.UsdGeom.Cube.Define(self.stage,path);box.CreateSizeAttr(1)
                    box.CreateDisplayColorAttr([self.Gf.Vec3f(.92,.48,.12)])
                    box.GetPrim().CreateAttribute('iecs:containerId',Sdf.ValueTypeNames.String).Set(c['id'])
                self.pose(path,c['position'],c['heading'],(1.05,5.2,.9))
                self.UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set(
                    'invisible' if c['location'].startswith('EXIT/') else 'inherited')
                prim=self.stage.GetPrimAtPath(path)
                prim.CreateAttribute('iecs:location',Sdf.ValueTypeNames.String).Set(c['location'])
            for o in self.engine.orders.values():
                path='/World/IECS/Handling/'+o['id'].replace('-','_')
                if not self.stage.GetPrimAtPath(path):
                    marker=self.UsdGeom.Cube.Define(self.stage,path);marker.CreateSizeAttr(1)
                    marker.CreateDisplayColorAttr([self.Gf.Vec3f(.95,.8,.08)])
                c=self.engine.containers[o['container']];point=c['position'][:];point[2]+=.55
                self.pose(path,point,c['heading'],(1.1,5.25,.12))
                self.UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set(
                    'inherited' if c['location'].startswith('SPREADER/') and not o.get('source_prim') else 'invisible')

    def apply_container_visual(self,c,pickup):
        path='/World/IECS/CargoVisuals/'+c['id'].replace('-','_')
        if not self.stage.GetPrimAtPath(path):
            self.UsdGeom.Xform.Define(self.stage,path)
            original=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(
                self.stage.GetPrimAtPath(pickup.box_path)).ComputeAlignedBox().GetSize()
            width=min(original[0],original[1]);length=max(original[0],original[1])
            c['visual_size']=[float(width),float(length),float(pickup.height)]
            c['source_prim']=pickup.box_path
            c['visual_prim']=path
            c['visual_asset']='Yard_Container_20ft.usda'
            model=self.UsdGeom.Xform.Define(self.stage,path+'/Asset').GetPrim()
            model.GetReferences().AddReference(str(PROJECT/'omniverse/assets/resource/container/Yard_Container_20ft.usda'),'/Yard20Container')
            box=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(model).ComputeAlignedBox()
            size=box.GetSize();center=box.GetMidpoint()
            if min(size)<=0:raise ValueError('Detailed yard container asset is unavailable')
            scale=self.Gf.Vec3f(width/size[0],length/size[1],pickup.height/size[2])
            self.pose(str(model.GetPath()),[-center[i]*scale[i] for i in range(3)],scale=scale)
            self.UsdGeom.Imageable(self.stage.GetPrimAtPath(path+'/Asset/R01_T01_Container')).CreateVisibilityAttr().Set('inherited')
        self.pose(path,c['position'],c['heading'])
        from pxr import Sdf
        entity=self.stage.GetPrimAtPath(path)
        for key,value in (('containerId',c['id']),('sourcePrim',c['source_prim']),
                          ('location',c['location']),('owner',c['owner'] or ''),('asset',c['visual_asset'])):
            entity.CreateAttribute('iecs:'+key,Sdf.ValueTypeNames.String).Set(value)
        entity.CreateAttribute('iecs:version',Sdf.ValueTypeNames.Int).Set(c['version'])
        self.UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set(
            'invisible' if c['location'].startswith('EXIT/') else 'inherited')

    def prepare_ship_order(self,oid):
        from iecs_ship_pickup import ShipPickup,BOX
        if self.ship_bindings:raise ValueError('Ship box already assigned. Reset Demo before another real pickup.')
        pickup=ShipPickup(self.stage,self.layer)
        o=self.engine.orders[oid];c=self.engine.containers[o['container']]
        stop=[QUAY_PICKUP_LANE_X,float(pickup.source[1]),.085]
        pickup.configure(stop,TRAILER_DECK_Z)
        # Keep crane still until the allocated truck reaches its stopping point.
        pickup.c.hold();pickup.phase='Ready';pickup.configured=False
        self.engine.reserved.pop(o['source']['id'],None)
        o['source'].update(id=BOX,point=list(pickup.source),stop=stop)
        self.engine.reserved[BOX]=oid
        o['source_prim']=BOX
        c.update(position=list(pickup.source),location=BOX,carry_height=.685+pickup.height/2)
        o['target']['point'][2]=.12+pickup.height/2
        self.select_free_yard_slot(o,pickup.height)
        self.pickups[oid]=pickup;self.ship_bindings[c['id']]=pickup
        self.engine.event('real_ship_box_bound',order=oid,source=BOX)

    def select_free_yard_slot(self,o,height):
        from iecs_core import yard
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        occupied=[]
        # Source meshes are hidden during transport. Include authoritative
        # ledger positions so a previously delivered box is not overwritten.
        for c in self.engine.containers.values():
            if c['id']==o['container'] or c['owner'] or c['location'].startswith('EXIT/'):continue
            size=c.get('visual_size',CONTAINER_40_SIZE)
            center=self.Gf.Vec3d(*c['position']);half=self.Gf.Vec3d(*(v/2 for v in size))
            occupied.append(self.Gf.Range3d(center-half,center+half))
        for p in self.stage.Traverse():
            if str(p.GetPath()).startswith('/World/LiveContainers/') and p.GetName().endswith('_Container'):
                if self.UsdGeom.Imageable(p).ComputeVisibility()!='invisible':
                    box=cache.ComputeWorldBound(p).ComputeAlignedRange()
                    if not box.IsEmpty():occupied.append(box)
        for block in ('3C','2C','1C','4C'):
            for bay in range(2,42 if block=='4C' else 92,2):
                candidate=yard(block,bay);candidate['point'][2]=.12+height/2
                center=candidate['point'];half=(.7,2.64,height/2)
                if any(all(b.GetMin()[i]<center[i]+half[i]-.01 and b.GetMax()[i]>center[i]-half[i]+.01 for i in range(3)) for b in occupied):continue
                if candidate['id'] in self.engine.reserved and self.engine.reserved[candidate['id']]!=o['id']:continue
                self.engine.reserved.pop(o['target']['id'],None);o['target']=candidate
                self.engine.reserved[candidate['id']]=o['id'];return
        raise ValueError('No free ground-level RTG slot available')

    def prepare_load_order(self,oid,yard_only=False):
        from iecs_ship_pickup import ShipPickup,BOX,bounds
        from iecs_core import yard
        import re
        if not yard_only and any(o['flow']=='load' for o in self.engine.orders.values() if o['id']!=oid):
            raise ValueError('Only one real ship loading slot is available per demo.')
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        stock=[]
        for p in self.stage.Traverse():
            match=re.search(r'/Bay_([1234]C)_(\d+)/R01_T(\d+)_Container$',str(p.GetPath()))
            if not match or self.UsdGeom.Imageable(p).ComputeVisibility()=='invisible':continue
            b=cache.ComputeWorldBound(p).ComputeAlignedBox()
            if abs(b.GetMidpoint()[0]-6.433333)>.2 or b.GetSize()[1]<4.:continue
            if str(p.GetPath()) not in self.engine.reserved:stock.append((p,match,b))
        tops=[item for item in stock if not any(
            abs(other[2].GetMidpoint()[0]-item[2].GetMidpoint()[0])<.2
            and abs(other[2].GetMidpoint()[1]-item[2].GetMidpoint()[1])<.2
            and other[2].GetMax()[2]>item[2].GetMax()[2]+.01 for other in stock)]
        if not tops:raise ValueError('No accessible row-1 40ft yard container found')
        p,match,b=min(tops,key=lambda item:(item[2].GetMax()[2],str(item[0].GetPath())))
        pickup=ShipPickup(self.stage,self.layer,str(p.GetPath()),defer_pick=True)
        o=self.engine.orders[oid];c=self.engine.containers[o['container']]
        source=yard(match[1],int(match[2]))
        source.update(id=str(p.GetPath()),point=list(b.GetMidpoint()))
        source['stop']=[-3.75,float(b.GetMidpoint()[1]),.085]
        support=bounds(self.stage,BOX)
        target=list(support.GetMidpoint());target[2]=support.GetMax()[2]+pickup.height/2
        if any(order['flow']=='unload' and order['status']=='completed' for order in self.engine.orders.values()):
            target=self.ship_home[:]
        stop=[-238.15,target[1],.085]
        # Validate all STS targets now, not after a truck has departed.
        if not yard_only:pickup.configure_loading(stop,target)
        pickup.c.hold();pickup.configured=False;pickup.phase='Ready'
        for loc in (o['source'],o['target']):self.engine.reserved.pop(loc['id'],None)
        o['source']=source;o['target']={'id':'SHIP/LOAD/STS01','kind':'quay','point':target,'stop':stop}
        o['source_prim']=str(p.GetPath())
        for loc in (o['source'],o['target']):self.engine.reserved[loc['id']]=oid
        c.update(position=source['point'][:],location=source['id'],carry_height=.685+pickup.height/2)
        c['production_id']=p.GetAttribute('live:containerId').Get()
        self.pickups[oid]=pickup;self.ship_bindings[c['id']]=pickup
        self.engine.event('real_yard_box_bound',order=oid,source=str(p.GetPath()))

    def prepare_ground_order(self,oid):
        from iecs_ship_pickup import ShipPickup,BOX
        o=self.engine.orders[oid];c=self.engine.containers[o['container']]
        if o['flow']=='gate_in':
            root='/World/IECS/IncomingCargo/'+o['id'].replace('-','_')
            path=root+'/Container'
            with self.Usd.EditContext(self.stage,self.layer):
                wrapper=self.UsdGeom.Xform.Define(self.stage,root)
                prim=self.UsdGeom.Xform.Define(self.stage,path).GetPrim()
                prim.GetReferences().AddReference(self.stage.GetRootLayer().identifier,BOX)
                # Referencing the box alone omits the ship parent's scale.
                # Normalize incoming cargo to the same visual 40ft yard size.
                from build_live_containers import CONTAINER_WIDTH_X_M,CONTAINER_VISUAL_HEIGHT_Z_M
                size=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(prim).ComputeAlignedBox().GetSize()
                if min(size)<=0:raise ValueError('Incoming cargo has invalid geometry bounds')
                desired=CONTAINER_40_SIZE
                wrapper.AddScaleOp().Set(self.Gf.Vec3f(*(desired[i]/size[i] for i in range(3))))
            pickup=ShipPickup(self.stage,self.layer,path,defer_pick=True)
            c.update(carry_height=.685+pickup.height/2,position=[151,388.4,.685+pickup.height/2])
            pickup.set_box_world(self.Gf.Vec3d(*c['position']))
            o['source_prim']=path
            self.pickups[oid]=pickup;self.ship_bindings[c['id']]=pickup
        else:
            # Reuse the accessible real-yard stock selector. No box is spawned.
            self.prepare_load_order(oid,yard_only=True)
        if o['flow']=='gate_out':
            from iecs_core import gate
            self.engine.reserved.pop(o['target']['id'],None)
            o['target']=gate(self.engine.sequence)
            self.engine.reserved[o['target']['id']]=oid
        else:self.select_free_yard_slot(o,self.pickups[oid].height)

    def ground_transfer(self,o,v,pick,dt):
        pickup=self.pickups[o['id']];c=self.engine.containers[o['container']]
        key=(o['id'],pick)
        if key not in self.yard_drops:
            YardDrop=pickup.load_sibling('iecs_yard_drop').YardDrop
            target={'point':[v['position'][0],v['position'][1],c['carry_height']]} if pick else o['target']
            self.yard_drops[key]=YardDrop(self.stage,self.layer,pickup,v,c,target)
        rig=self.yard_drops[key];done=rig.step(dt)
        if rig.attached:
            c.update(position=list(rig.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/RTG',heading=0.)
        elif rig.phase=='RTG unlock and raise' or done:
            c.update(position=rig.target-self.Gf.Vec3d(0,0,pickup.height/2),heading=0.)
            c['position']=list(c['position'])
        o['step_name']=rig.phase;return done

    def real_ship_transfer(self,o,v,pick,dt):
        if o['flow'] in ('gate_in','gate_out','transfer'):
            return self.ground_transfer(o,v,pick,dt)
        pickup=self.pickups[o['id']]
        if o['flow']=='load':
            c=self.engine.containers[o['container']]
            if pick:
                if o['id'] not in self.yard_drops:
                    YardDrop=pickup.load_sibling('iecs_yard_drop').YardDrop
                    target={'point':[v['position'][0],v['position'][1],c['carry_height']]}
                    self.yard_drops[o['id']]=YardDrop(self.stage,self.layer,pickup,v,c,target)
                rig=self.yard_drops[o['id']];done=rig.step(dt)
                if rig.attached:
                    c.update(position=list(rig.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/RTG-'+o['source']['block'],heading=0.)
                elif rig.phase=='RTG unlock and raise' or done:
                    c.update(position=[v['position'][0],v['position'][1],c['carry_height']],heading=v['heading'])
                o['step_name']=rig.phase;return done
            if not pickup.configured:pickup.configure_loading(v['position'],o['target']['point'])
            done=pickup.step(dt)
            if pickup.attached:
                c.update(position=list(pickup.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/QC-01',heading=0.)
            elif pickup.phase=='Unlocked - raise spreader' or done:
                c.update(position=o['target']['point'][:],heading=0.)
            o['step_name']=pickup.phase;return done
        if not pick:
            # Kit's extension importer may cache a directory before a new
            # sibling script is created. Load by the verified absolute path.
            YardDrop=pickup.load_sibling('iecs_yard_drop').YardDrop
            c=self.engine.containers[o['container']]
            if o['id'] not in self.yard_drops:
                self.yard_drops[o['id']]=YardDrop(self.stage,self.layer,pickup,v,c,o['target'])
            rig=self.yard_drops[o['id']];finished=rig.step(dt)
            if rig.attached:
                c.update(position=list(rig.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/RTG-'+o['target']['block'],owner='RTG-'+o['target']['block'],heading=0.)
            elif rig.phase=='RTG unlock and raise' or finished:
                c.update(position=o['target']['point'][:],heading=0.)
            o['step_name']=rig.phase
            return finished
        if not pickup.configured:
            pickup.configure(v['position'],.685)
        finished=pickup.step(dt)
        c=self.engine.containers[o['container']]
        if pickup.attached or finished:
            p=pickup.contact()-self.Gf.Vec3d(0,0,pickup.height/2)
            if finished:p=self.Gf.Vec3d(v['position'][0],v['position'][1],.685+pickup.height/2)
            c.update(position=list(p),location='SPREADER/QC-01',owner='QC-01')
        o['step_name']=pickup.phase
        return finished

    def process(self,cmd):
        action=cmd.get('action')
        if action=='submit':
            if cmd.get('request_id') and any(o.get('request_id')==cmd['request_id'] for o in self.engine.orders.values()):return
            if any(o['status']!='completed' for o in self.engine.orders.values()):
                raise ValueError('Primary RTG demo is busy. Wait for completion or use Mixed Workload.')
            for pickup in self.pickups.values():pickup.close()
            if cmd.get('flow')=='unload' and any(o['flow']=='unload' for o in self.engine.orders.values()):
                raise ValueError('Ship box already assigned. Reset Demo before another real pickup.')
            oid=self.engine.submit(cmd.get('flow'),cmd.get('request_id'))
            try:
                if cmd.get('flow')=='unload':self.prepare_ship_order(oid)
                elif cmd.get('flow')=='load':self.prepare_load_order(oid)
                elif cmd.get('flow') in ('gate_in','gate_out','transfer'):self.prepare_ground_order(oid)
                self.engine.checkpoint()
            except Exception:
                o=self.engine.orders.pop(oid)
                self.engine.containers.pop(o['container'],None)
                pickup=self.pickups.pop(oid,None)
                if pickup:pickup.close()
                self.ship_bindings.pop(o['container'],None)
                for key in [k for k,v in self.engine.reserved.items() if v==oid]:self.engine.reserved.pop(key)
                raise
        elif action=='mixed':
            if self.mixed_pending or any(o['status']!='completed' for o in self.engine.orders.values()):
                raise ValueError('Finish or reset current tasks before Mixed Workload.')
            if self.engine.orders:raise ValueError('Reset Demo before Mixed Workload.')
            self.mixed_pending=['unload','load','gate_in','gate_out','transfer']
            self.advance_mixed()
        elif action=='reset':
            self.engine.control('reset')
            for pickup in self.pickups.values():pickup.close()
            self.pickups.clear();self.ship_bindings.clear()
            self.yard_drops.clear()
            self.mixed_pending.clear()
            # Clear only our anonymous opinions. Source inventory is untouched.
            self.layer.Clear();self.models.clear()
        elif action=='check_scene':
            self.check_scene()
        elif action=='inventory_preview':
            self.inventory_preview()
        elif action=='resume':
            report=self.check_scene()
            if not report['ok']:raise ValueError('Resume blocked: '+report['errors'][0])
            self.engine.control('resume')
        else:self.engine.control(action,cmd.get('value'))
        self.last_error=''

    def check_scene(self):
        from iecs_scene_checks import inspect_scene
        self.scene_report=inspect_scene(self.stage,list(self.engine.containers.values()),list(self.engine.vehicles.values()))
        return self.scene_report

    def inventory_preview(self):
        from iecs_inventory import project_inventory
        return project_inventory(PROJECT/'omniverse/data/yard_inventory.live.json',
                                 list(self.engine.orders.values()),list(self.engine.containers.values()))

    def advance_mixed(self):
        if self.mixed_pending and all(o['status']=='completed' for o in self.engine.orders.values()):
            flow=self.mixed_pending[0]
            self.process({'action':'submit','flow':flow})
            self.mixed_pending.pop(0)

    def on_update(self,event):
        if self.closed:return
        try:
            import omni.usd
            if omni.usd.get_context().get_stage()!=self.stage:
                self.close();return
            for _ in range(100):
                try:cmd=self.commands.get_nowait()
                except queue.Empty:break
                try:self.process(cmd)
                except Exception as e:
                    self.last_error=str(e);self.engine.event('command_rejected',error=str(e),command=cmd)
            self.engine.tick(float(event.payload.get('dt',1/60)))
            self.advance_mixed()
            self.apply_scene();self.publish()
            if self.window and time.monotonic()-self.last_ui>.25:
                self.check_scene();self.publish()
                self.last_ui=time.monotonic();self.render_ui()
        except Exception as e:
            self.last_error=str(e);self.engine.paused=True;self.publish()
            if self.window:self.render_ui()

    def render_ui(self):
        s=self.cached;orders=s['orders']
        error=self.last_error if self.last_error.isascii() else 'Command rejected. See /state error for details.'
        self.summary.text=f"Time {s['simulation_time']:.1f}s | {s['speed']}x | {'Paused' if s['paused'] else 'Running'} | Completed {sum(o['status']=='completed' for o in orders)}/{len(orders)}\n{error}"
        rows=['Order / Workflow / Vehicle / Step / Wait Reason']
        if not self.scene_report['ok']:rows+=['Scene checks:']+self.scene_report['errors'][:6]
        if self.mixed_pending:rows+=['Next: '+' -> '.join(FLOW_LABELS[f] for f in self.mixed_pending)]
        for o in orders:
            wait=o['wait_reason']
            if wait:
                wait=wait if wait.startswith('Collision wait:') else 'Waiting for Vehicle' if o['status']=='queued' else 'Waiting for Road' if '道路' in wait else 'Waiting for Equipment'
            step='Queued' if o['status']=='queued' else STEP_LABELS[min(o['step'],5)]
            if o.get('source_prim') and o['step'] in (1,3):step=o['step_name']
            rows.append(f"{o['id']}  {FLOW_LABELS[o['flow']]}  {o['vehicle'] or 'Unassigned'}  {step}  {wait}")
        rows+=['','Vehicle / Status / Container']
        rows += [f"{v['id']}  {v['status']}  {v['load'] or 'Empty'}" for v in s['vehicles']]
        rows += ['','Recent Events']+[f"{e['time']:.1f}  {e['kind']}  {e.get('order','')}" for e in s['events'][-8:]]
        self.details.text='\n'.join(rows)

    def close(self):
        if self.closed:return
        self.closed=True;self.subscription=None
        for pickup in self.pickups.values():pickup.close()
        if self.server:self.server.shutdown();self.server.server_close()
        if self.layer.identifier in self.stage.GetSessionLayer().subLayerPaths:
            paths=list(self.stage.GetSessionLayer().subLayerPaths);paths.remove(self.layer.identifier)
            self.stage.GetSessionLayer().subLayerPaths=paths
        if self.window:self.window.visible=False
        self.engine.close()


def start_iecs():
    import omni.usd
    old=getattr(builtins,'_iecs_runtime',None)
    if old:old.close()
    stage=omni.usd.get_context().get_stage()
    if not stage:raise ValueError('请先打开 smart_port.usda')
    runtime=Runtime(stage);builtins._iecs_runtime=runtime
    runtime.render_ui();return runtime


if __name__=='__main__':start_iecs()
