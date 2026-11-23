"""IECS native Kit console, HTTP command interface and session-only scene adapter.

Execute in Kit Script Editor: exec(open(PATH, encoding='utf-8').read())
Simulation stock is separate from yard_inventory.live.json; handling is a visual
lift/transfer, not PLC feedback or original crane joint actuation.
"""
import builtins
import json
import math
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
# Kit may keep an older launcher and directory cache while the stage is open.
# Resolve sibling helpers by absolute path even when that launcher missed them.
for _name in ('iecs_scene_checks','iecs_quay_clearance'):
    if _name not in sys.modules:
        _spec=importlib.util.spec_from_file_location(_name,Path(__file__).with_name(_name+'.py'))
        _module=importlib.util.module_from_spec(_spec)
        sys.modules[_spec.name]=_module
        try:_spec.loader.exec_module(_module)
        except Exception:
            sys.modules.pop(_spec.name,None)
            raise
from iecs_core import IECS, FLOWS
from iecs_geometry import TRAILER_DECK_Z,QUAY_PICKUP_LANE_X,YARD_PAD_Z,CONTAINER_40_SIZE,truck_load_point
from iecs_geometry import FRONT_20FT_LOAD_FORWARD,YARD_TRUCK_RAIL_Z
import build_parked_yard_trucks as truck_markings

# A new demo can start while Kit still holds the previous demo's mesh helpers.
truck_markings=importlib.reload(truck_markings)

FLOW_LABELS = {'unload':'Ship to Yard', 'load':'Yard to Ship',
               'gate_in':'Gate In / Drop Box', 'gate_out':'Gate Out / Pick Box',
               'transfer':'Yard Transfer'}
STEP_LABELS = ['Drive to Pickup','Pickup / Handover','Loaded Transport',
               'Drop / Handover','Return / Exit','Completed']

def ship_demo_slots(stage):
    """Choose a reachable top 6.6 structural box, with legacy scene support."""
    from pxr import Gf, UsdGeom
    from iecs_ship_pickup import BOX, SHIP
    from ship_transfer_plan import box, crane_state, solve
    from ship_structure_data import read_structure
    if not stage.GetPrimAtPath(SHIP+'/StructureSlots'):
        return ([BOX] if stage.GetPrimAtPath(BOX) else []),40
    prim=stage.GetPrimAtPath('/World/STSControls/STS_01')
    if not prim:return [],20
    crane=crane_state(stage,prim)
    tops={}
    for slot in read_structure()['slots']:
        if slot['zone']=='D':
            key=slot['bay'],slot['row']
            if key not in tops or slot['tier']>tops[key]['tier']:tops[key]=slot
    candidates=[]
    for slot in tops.values():
        path=SHIP+f"/StructureSlots/Bay{slot['bay']:02d}_D/L{slot['location']}"
        model=stage.GetPrimAtPath(path)
        if not model or UsdGeom.Imageable(model).ComputeVisibility()=='invisible':continue
        bounds=box(stage,path);center=bounds.GetMidpoint()
        if abs(center[1]-prim.GetAttribute('sts:homeY').Get())>45.:continue
        top=Gf.Vec3d(center[0],center[1],bounds.GetMax()[2])
        deck=Gf.Vec3d(QUAY_PICKUP_LANE_X,center[1],TRAILER_DECK_Z+bounds.GetSize()[2])
        clear=max(top[2],deck[2])+bounds.GetSize()[2]+1.5
        try:
            for point in (top,deck,Gf.Vec3d(top[0],top[1],clear),Gf.Vec3d(deck[0],deck[1],clear)):
                solve(stage,crane,point)
        except ValueError:continue
        candidates.append((abs(center[1]-prim.GetAttribute('sts:homeY').Get()),-center[0],path))
    return [item[2] for item in sorted(candidates)],20


class Runtime:
    def __init__(self,stage,port=48730,with_ui=True,subscribe=True,db_path=None,
                 external_config=None,external_guard=None):
        from pxr import Gf,Sdf,Usd,UsdGeom
        self.Gf,self.Usd,self.UsdGeom=Gf,Usd,UsdGeom
        self.stage=stage;self.closed=False;self.commands=queue.Queue();self.last_error=''
        self.external=None
        from iecs_scene_checks import inspect_scene
        self.scene_report=inspect_scene(stage)
        self.pickups={};self.ship_bindings={}
        self.yard_drops={}
        from iecs_ship_pickup import bounds
        self.ship_slots,self.size_ft=ship_demo_slots(stage)
        self.ship_box=next(iter(self.ship_slots),None)
        self.ship_home=list(bounds(stage,self.ship_box).GetMidpoint()) if self.ship_box else None
        self.visual_rtg=None;self.shared_sts=None;self.held_sea=None
        self.snapshot_lock=threading.Lock();self.cached={};self.models={};self.last_ui=0
        self.layer=Sdf.Layer.CreateAnonymous('iecs_demo.usda')
        missing=[f'/World/ParkedYardTrucks/Group{g}_Bay{b:02d}' for g in (1,2) for b in range(1,11)
                 if not stage.GetPrimAtPath(f'/World/ParkedYardTrucks/Group{g}_Bay{b:02d}')]
        if missing:raise ValueError('请先加载 parked_yard_trucks.usda：'+missing[0])
        (PROJECT/'outputs/iecs').mkdir(parents=True,exist_ok=True)
        database='external.sqlite3' if external_config else 'iecs.sqlite3'
        self.engine=IECS(db_path or PROJECT/'outputs/iecs'/database)
        self.engine.transfer_adapter=self.real_ship_transfer
        from iecs_collision import swept_conflict
        from iecs_quay_clearance import quay_route_obstacle
        self.engine.motion_guard=lambda v,end,heading:swept_conflict(v,end,heading,self.engine.vehicles.values())
        self.engine.quay_clearance=lambda path,cargo:quay_route_obstacle(self.stage,path,cargo)
        self.stage.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        self.server=None;self.subscription=None;self.window=None
        try:
            if external_config:
                from iecs_external import ExternalDispatch
                self.external=ExternalDispatch(self,external_config,external_guard)
                self.engine.step_gate=self.external.step_gate
            if port is not None:self.start_http(port)
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
            self.cached['size_ft']=self.size_ft
            self.cached['ship_demo_available']=self.ship_box is not None
            if self.external:
                self.cached.update(mode='external',external=self.external.snapshot())

    @property
    def mixed_pending(self):
        return [o['flow'] for o in self.engine.orders.values() if o.get('mixed') and o['status']=='queued']

    def reserve_ship_slot(self,order,loading=False,path=None):
        from large_vessel_layout import SHIP
        used={o.get('ship_slot') for o in self.engine.orders.values()}
        if path is not None:
            if path not in self.ship_slots or path in used:
                raise ValueError('指定船箱位不可用')
        else:
            path=next((p for p in self.ship_slots if p not in used),None)
        if path is None and loading and self.size_ft==40:
            used_for_loading=any(o['flow']=='load' and o.get('ship_slot') for o in self.engine.orders.values())
            if not used_for_loading and any(o['flow']=='unload' and o['status']=='completed' for o in self.engine.orders.values()):
                path=self.ship_box
        if path is None:raise ValueError('无空闲可达的船上演示箱位，请复位后重试')
        sea=getattr(builtins,'_smart_port_sea_motion',None)
        if sea and not sea.closed and sea.stage==self.stage and SHIP not in sea.held:
            sea.hold(SHIP);self.held_sea=sea
        order['ship_slot']=path
        return path

    def release_ship(self):
        from large_vessel_layout import SHIP
        if self.held_sea and not self.held_sea.closed:self.held_sea.release(SHIP)
        self.held_sea=None

    def recent_history(self, before=None):
        clause='WHERE id<?' if before is not None else ''
        rows=self.engine.db.execute(
            f'SELECT id,time,kind,payload FROM events {clause} ORDER BY id DESC LIMIT 30',
            (before,) if before is not None else ())
        return [{'id':i,'time':t,'kind':kind,'payload':json.loads(payload)} for i,t,kind,payload in rows]

    def yard_slot(self,block,bay):
        from build_live_containers import container_center_y_for_bay
        from iecs_core import yard
        if self.size_ft!=20:return yard(block,bay)
        from iecs_geometry import TRUCK_LANE_X,YARD_ROW_ONE_X
        y=container_center_y_for_bay(block,bay,20)
        return {'id':f'{block}{bay:03d}/R1/T1','kind':'yard','block':block,'bay':bay,
                'point':[YARD_ROW_ONE_X,y,.57],'stop':[TRUCK_LANE_X,y,.085]}

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

    def update_truck_marker(self,v,hidden,now):
        path='/World/IECS/TruckMarkers/'+v['id'].replace('-','_')
        marker=self.stage.GetPrimAtPath(path)
        if not marker:
            if hidden:return
            marker=self.UsdGeom.Xform.Define(self.stage,path).GetPrim()
            lamp_scale=.75
            cab_y,roof_z=-2.29,1.06
            mount=self.UsdGeom.Cube.Define(self.stage,path+'/Mount')
            mount.CreateSizeAttr(2)
            mount.AddTranslateOp().Set(self.Gf.Vec3d(0.,cab_y,roof_z+.016*lamp_scale))
            mount.AddScaleOp().Set(self.Gf.Vec3f(.41,.105,.016)*lamp_scale)
            mount.CreateDisplayColorAttr([self.Gf.Vec3f(.12,.14,.16)])

            lens=self.UsdGeom.Mesh.Define(self.stage,path+'/Lens')
            # Fit the tapered light behind the painted fleet number.
            lens.CreatePointsAttr([
                self.Gf.Vec3f(x*lamp_scale,cab_y+y*lamp_scale,roof_z+h*lamp_scale)
                for w,d,h in ((.39,.10,.032),(.31,.076,.19))
                for x,y in ((-w,-d),(w,-d),(w,d),(-w,d))
            ])
            lens.CreateFaceVertexCountsAttr([4]*5)
            lens.CreateFaceVertexIndicesAttr([0,1,5,4, 1,2,6,5, 2,3,7,6, 3,0,4,7, 4,5,6,7])
            lens.CreateSubdivisionSchemeAttr('none')
            lens.CreateDisplayOpacityAttr([.80])
        self.UsdGeom.Imageable(marker).CreateVisibilityAttr().Set('invisible' if hidden else 'inherited')
        if not hidden:
            base=v['position'][:];base[2]-=.14
            self.pose(path,base,v['heading'],(1,1.22,1))
            order=self.engine.orders.get(v['order'])
            color=self.Gf.Vec3f(.47,.47,.47)
            if order:
                waiting=self.engine.paused or v['status']=='waiting_gate' or bool(order['wait_reason'])
                color=self.Gf.Vec3f(1.,.59,.13) if waiting else self.Gf.Vec3f(.12,.92,.78)
                period=1.8 if waiting else 1.2
                pulse=(1-math.cos(math.tau*now/period))/2
                color*=.65+.35*pulse
            lens=self.UsdGeom.Mesh.Get(self.stage,path+'/Lens')
            lens.CreateDisplayColorAttr().Set([color])

    def apply_scene(self):
        from pxr import Sdf
        from iecs_collision import swept_conflict
        vehicles=self.engine.vehicles.values()
        waiting=next((v for v in vehicles if v['status']=='waiting_gate'),None)
        # The simulator has one shared entry pose. Show the lead empty truck
        # only once that pose is clear, so the two outside rigs never overlap.
        waiting_visible=waiting and not waiting['load'] and not swept_conflict(
            waiting,waiting['position'],waiting['heading'],vehicles)
        now=time.monotonic()
        with self.Usd.EditContext(self.stage,self.layer):
            for v in vehicles:
                path=self.vehicle_path(v['id'])
                if v['id'].startswith('EXT') and not self.stage.GetPrimAtPath(path):
                    prim=self.UsdGeom.Xform.Define(self.stage,path).GetPrim()
                    prim.GetReferences().AddReference(str(PROJECT/'omniverse/assets/resource/truck/RTG_Model_Lane_Truck.usdc'),'/root')
                    self.stage.OverridePrim(path+'/env_light').SetActive(False)
                    truck_markings.add_roof_digits(self.stage,path+'/RoofNumber',v['number'])
                base=v['position'][:];base[2]-=.14
                self.pose(path,base,v['heading'],(1,1.22,1))
                hidden=v['status']=='departed' or (
                    v['status']=='waiting_gate' and not (waiting_visible and v is waiting))
                self.UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set(
                    'invisible' if hidden else 'inherited')
                if not self.external:self.update_truck_marker(v,hidden,now)
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
            'invisible' if c['location'].startswith('EXIT/') or c.get('waiting_gate') else 'inherited')

    def prepare_ship_order(self,oid,source_path=None):
        from iecs_ship_pickup import ShipPickup,bounds
        o=self.engine.orders[oid];c=self.engine.containers[o['container']]
        path=self.reserve_ship_slot(o,path=source_path)
        deck_top = TRAILER_DECK_Z
        if source_path is not None:
            from transfer_truck_geometry import truck_loading
            vehicle = self.engine.vehicles[o['requested_vehicle']]
            length = bounds(self.stage,path).GetSize()[1]
            _, pivot, offset = truck_loading(self.stage,20,length,self.vehicle_path(vehicle['id']))
            deck_top = pivot[2]
            # Trucks face -X at their parking bays. The stop positions below
            # are approached along -Y, with the front lock point at the lift line.
            c['load_forward_m'] = vehicle['position'][0]-(pivot[0]+offset)
        pickup=ShipPickup(self.stage,self.layer,path,size_ft=self.size_ft,defer_sts=True)
        stop=[QUAY_PICKUP_LANE_X,float(pickup.source[1])+c.get('load_forward_m',0.),.085]
        self.engine.reserved.pop(o['source']['id'],None)
        o['source'].update(id=path,point=list(pickup.source),stop=stop)
        self.engine.reserved[path]=oid
        o['source_prim']=path
        c.update(position=list(pickup.source),location=path,carry_height=deck_top+pickup.height/2,size_ft=self.size_ft)
        o['target']['point'][2]=.12+pickup.height/2
        if source_path is None:self.select_free_yard_slot(o,pickup.height)
        self.pickups[oid]=pickup;self.ship_bindings[c['id']]=pickup
        self.engine.event('ship_demo_box_bound',order=oid,source=path,size_ft=self.size_ft)

    def occupied_yard_bounds(self,exclude=None):
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        occupied=[]
        # Source meshes are hidden during transport. Include authoritative
        # ledger positions so a previously delivered box is not overwritten.
        for c in self.engine.containers.values():
            if c['id']==exclude or c['owner'] or c['location'].startswith('EXIT/'):continue
            size=c.get('visual_size',CONTAINER_40_SIZE)
            center=self.Gf.Vec3d(*c['position']);half=self.Gf.Vec3d(*(v/2 for v in size))
            occupied.append(self.Gf.Range3d(center-half,center+half))
        for p in self.stage.Traverse():
            if str(p.GetPath()).startswith('/World/LiveContainers/') and p.GetName().endswith('_Container'):
                if self.UsdGeom.Imageable(p).ComputeVisibility()!='invisible':
                    box=cache.ComputeWorldBound(p).ComputeAlignedRange()
                    if not box.IsEmpty():occupied.append(box)
        return occupied

    def free_yard_slots(self,height,order=None):
        occupied=self.occupied_yard_bounds(order['container'] if order else None)
        used_blocks=self.planned_yard_blocks(order)
        for block in sorted(('3C','2C','1C','4C'),key=lambda block:block in used_blocks):
            for bay in range(1 if self.size_ft==20 else 2,42 if block=='4C' else 92,2):
                candidate=self.yard_slot(block,bay);candidate['point'][2]=.12+height/2
                center=candidate['point'];half=(.7,1.32 if self.size_ft==20 else 2.64,height/2)
                if any(all(b.GetMin()[i]<center[i]+half[i]-.01 and b.GetMax()[i]>center[i]-half[i]+.01 for i in range(3)) for b in occupied):continue
                if candidate['id'] in self.engine.reserved and (not order or self.engine.reserved[candidate['id']]!=order['id']):continue
                yield candidate

    def select_free_yard_slot(self,o,height):
        candidate=next(self.free_yard_slots(height,o),None)
        if candidate:
            self.engine.reserved.pop(o['target']['id'],None);o['target']=candidate
            self.engine.reserved[candidate['id']]=o['id'];return
        raise ValueError('No free ground-level RTG slot available')

    def planned_yard_blocks(self,exclude):
        return {slot['block'] for order in self.engine.orders.values() if order is not exclude and order['status']!='completed'
                for slot in (order['source'],order['target']) if slot['kind']=='yard'}

    def prepare_load_order(self,oid,yard_only=False):
        from iecs_ship_pickup import ShipPickup,bounds
        import re
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        stock=[]
        for p in self.stage.Traverse():
            match=re.search(r'/Bay_([1234]C)_(\d+)/R01_T(\d+)_Container$',str(p.GetPath()))
            if not match or self.UsdGeom.Imageable(p).ComputeVisibility()=='invisible':continue
            b=cache.ComputeWorldBound(p).ComputeAlignedBox()
            size=p.GetAttribute('live:containerSizeFt').Get()
            if abs(b.GetMidpoint()[0]-6.433333)>.2 or size!=self.size_ft:continue
            stock.append((p,match,b))
        owned_stacks={o['source']['id'].rsplit('_T',1)[0] for o in self.engine.orders.values()
                      if o['status']!='completed' and o['source']['id'].startswith('/')}
        tops=[item for item in stock if str(item[0].GetPath()).rsplit('_T',1)[0] not in owned_stacks and not any(
            abs(other[2].GetMidpoint()[0]-item[2].GetMidpoint()[0])<.2
            and abs(other[2].GetMidpoint()[1]-item[2].GetMidpoint()[1])<.2
            and other[2].GetMax()[2]>item[2].GetMax()[2]+.01 for other in stock)]
        if not tops:raise ValueError(f'找不到可抓取的第 1 排 {self.size_ft} 尺堆场演示箱')
        o=self.engine.orders[oid];c=self.engine.containers[o['container']]
        used_blocks=self.planned_yard_blocks(o)
        p,match,b=min(tops,key=lambda item:(item[1][1] in used_blocks,item[2].GetMax()[2],str(item[0].GetPath())))
        slot=self.reserve_ship_slot(o,loading=True) if not yard_only else None
        pickup=ShipPickup(self.stage,self.layer,str(p.GetPath()),defer_pick=True,size_ft=self.size_ft,defer_sts=True)
        source=self.yard_slot(match[1],int(match[2]))
        source.update(id=str(p.GetPath()),point=list(b.GetMidpoint()))
        source['stop']=[-3.75,float(b.GetMidpoint()[1]),.085]
        target=source['point'][:]
        if not yard_only:
            support=bounds(self.stage,slot)
            target=list(support.GetMidpoint());target[2]=support.GetMax()[2]+pickup.height/2
            if self.size_ft==20:
                target=list(support.GetMidpoint())
                target[2]+=(pickup.height-support.GetSize()[2])/2
            elif any(order['flow']=='unload' and order['status']=='completed' for order in self.engine.orders.values()):
                target=self.ship_home[:]
        stop=[-238.15,target[1],.085]
        for loc in (o['source'],o['target']):self.engine.reserved.pop(loc['id'],None)
        o['source']=source;o['target']={'id':slot or ('IECS/YARD/'+oid),'kind':'quay','point':target,'stop':stop}
        o['source_prim']=str(p.GetPath())
        for loc in (o['source'],o['target']):self.engine.reserved[loc['id']]=oid
        c.update(position=source['point'][:],location=source['id'],carry_height=.685+pickup.height/2,size_ft=self.size_ft)
        c['production_id']=p.GetAttribute('live:containerId').Get()
        self.pickups[oid]=pickup;self.ship_bindings[c['id']]=pickup
        self.engine.event('real_yard_box_bound',order=oid,source=str(p.GetPath()))

    def prepare_ground_order(self,oid):
        from iecs_ship_pickup import ShipPickup
        o=self.engine.orders[oid];c=self.engine.containers[o['container']]
        if o['flow'] in ('gate_in','transfer') and self.size_ft==20:
            c['load_forward_m']=FRONT_20FT_LOAD_FORWARD
        if o['flow']=='gate_in':
            root='/World/IECS/IncomingCargo/'+o['id'].replace('-','_')
            path=root+'/Container'
            with self.Usd.EditContext(self.stage,self.layer):
                wrapper=self.UsdGeom.Xform.Define(self.stage,root)
                prim=self.UsdGeom.Xform.Define(self.stage,path).GetPrim()
                asset=PROJECT/'omniverse/assets/resource/container/Yard_Container_20ft.usda'
                prim.GetReferences().AddReference(str(asset),'/Yard20Container')
                # Match the measured yard size in the current demo scene.
                size=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(prim).ComputeAlignedBox().GetSize()
                if min(size)<=0:raise ValueError('Incoming cargo has invalid geometry bounds')
                desired=(CONTAINER_40_SIZE[0],2.475,CONTAINER_40_SIZE[2]) if self.size_ft==20 else CONTAINER_40_SIZE
                wrapper.AddScaleOp().Set(self.Gf.Vec3f(*(desired[i]/size[i] for i in range(3))))
            pickup=ShipPickup(self.stage,self.layer,path,defer_pick=True,size_ft=self.size_ft,defer_sts=True)
            carry_height=YARD_TRUCK_RAIL_Z+pickup.height/2
            c.update(carry_height=carry_height,position=[*o['source']['stop'][:2],carry_height],size_ft=self.size_ft,waiting_gate=True)
            pickup.set_box_world(self.Gf.Vec3d(*c['position']))
            o['source_prim']=path
            self.pickups[oid]=pickup;self.ship_bindings[c['id']]=pickup
        else:
            # Reuse the accessible real-yard stock selector. No box is spawned.
            self.prepare_load_order(oid,yard_only=True)
        if o['flow']=='gate_out':
            from iecs_core import gate
            c['carry_height']=YARD_TRUCK_RAIL_Z+self.pickups[oid].height/2
            self.engine.reserved.pop(o['target']['id'],None)
            o['target']=gate(self.engine.sequence,outbound=True)
            self.engine.reserved[o['target']['id']]=oid
        else:
            self.select_free_yard_slot(o,self.pickups[oid].height)
            # The cab faces -Y at the RTG. Stop upstream by the load offset
            # so the forward-mounted box still stops on the bay's lift line.
            forward=c.get('load_forward_m',0.)
            o['target']['stop'][1]+=forward
            if o['flow']=='transfer' and forward:
                o['source']['stop'][1]+=forward
                c['carry_height']=YARD_TRUCK_RAIL_Z+self.pickups[oid].height/2

    def ground_transfer(self,o,v,pick,dt):
        pickup=self.pickups[o['id']];c=self.engine.containers[o['container']]
        key=(o['id'],pick)
        if key not in self.yard_drops:
            YardDrop=pickup.load_sibling('iecs_yard_drop').YardDrop
            target={'point':truck_load_point(v,c)} if pick else o['target']
            self.yard_drops[key]=YardDrop(self.stage,self.layer,pickup,v,c,target,motion=self.rtg_motion())
        rig=self.yard_drops[key];done=rig.step(dt)
        if rig.attached:
            c.update(position=list(rig.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/RTG',owner='RTG',heading=0.)
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
                    self.yard_drops[o['id']]=YardDrop(self.stage,self.layer,pickup,v,c,target,motion=self.rtg_motion())
                rig=self.yard_drops[o['id']];done=rig.step(dt)
                if rig.attached:
                    c.update(position=list(rig.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/RTG',owner='RTG',heading=0.)
                elif rig.phase=='RTG unlock and raise' or done:
                    c.update(position=[v['position'][0],v['position'][1],c['carry_height']],heading=v['heading'])
                o['step_name']=rig.phase;return done
            if not pickup.configured:
                pickup.bind_sts(self.sts_motion())
                if self.size_ft==20:
                    with self.Usd.EditContext(self.stage,self.layer):
                        self.UsdGeom.Imageable(self.stage.GetPrimAtPath(o['ship_slot'])).MakeInvisible()
                pickup.configure_loading(v['position'],o['target']['point'])
            done=pickup.step(dt)
            if pickup.attached:
                c.update(position=list(pickup.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/STS_01',owner='STS_01',heading=0.)
            elif pickup.phase=='Unlocked - raise spreader' or done:
                c.update(position=o['target']['point'][:],heading=0.)
            o['step_name']=pickup.phase;return done
        if not pick:
            # Kit's extension importer may cache a directory before a new
            # sibling script is created. Load by the verified absolute path.
            YardDrop=pickup.load_sibling('iecs_yard_drop').YardDrop
            c=self.engine.containers[o['container']]
            if o['id'] not in self.yard_drops:
                self.yard_drops[o['id']]=YardDrop(self.stage,self.layer,pickup,v,c,o['target'],motion=self.rtg_motion())
            rig=self.yard_drops[o['id']];finished=rig.step(dt)
            if rig.attached:
                c.update(position=list(rig.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),location='SPREADER/RTG',owner='RTG',heading=0.)
            elif rig.phase=='RTG unlock and raise' or finished:
                c.update(position=o['target']['point'][:],heading=0.)
            o['step_name']=rig.phase
            return finished
        c=self.engine.containers[o['container']]
        if not pickup.configured:
            pickup.bind_sts(self.sts_motion(),prepare_pick=True)
            pickup.configure(truck_load_point(v,c),c['carry_height']-pickup.height/2)
        finished=pickup.step(dt)
        if pickup.attached:
            c.update(position=list(pickup.contact()-self.Gf.Vec3d(0,0,pickup.height/2)),
                     location='SPREADER/STS_01',owner='STS_01')
        elif finished or (self.external and pickup.phase=='Unlocked - raise spreader'):
            c.update(position=truck_load_point(v,c),location=v['id'],owner=v['id'],heading=v['heading'])
        o['step_name']=pickup.phase
        return finished

    def sts_motion(self):
        if self.shared_sts is None:
            from sts_control import STSController
            self.shared_sts=STSController(self.stage,'STS_01')
        return self.shared_sts

    def rtg_motion(self):
        if self.size_ft==20 and self.visual_rtg is None:
            from rtg_transfer_motion import VisualRTG
            with self.Usd.EditContext(self.stage,self.layer):
                self.visual_rtg=VisualRTG(self.stage,20)
        return self.visual_rtg

    def process(self,cmd):
        action=cmd.get('action')
        if self.external and action not in ('pause','resume','speed','check_scene','inventory_preview'):
            raise ValueError('当前由真实 IECS 派单，请通过联调工作台操作')
        if action=='submit':
            self.submit_order(cmd.get('flow'),cmd.get('request_id'))
        elif action=='mixed':
            if self.engine.orders:raise ValueError('Reset Demo before Mixed Workload.')
            try:
                for flow in ('unload','load','gate_in','gate_out','transfer'):
                    self.process({'action':'submit','flow':flow})
                    self.engine.orders[f'IECS-{self.engine.sequence:05d}']['mixed']=True
                self.engine.checkpoint()
            except Exception:
                self.process({'action':'reset'})
                raise
        elif action=='reset':
            self.engine.control('reset')
            for pickup in self.pickups.values():pickup.close()
            self.pickups.clear();self.ship_bindings.clear()
            self.yard_drops.clear()
            if self.visual_rtg:self.visual_rtg.close();self.visual_rtg=None
            if self.shared_sts:self.shared_sts.close();self.shared_sts=None
            self.release_ship()
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

    def submit_order(self,flow,request_id=None,*,source_path=None,container_id=None,vehicle_id=None):
        if request_id:
            existing=next((o for o in self.engine.orders.values() if o.get('request_id')==request_id),None)
            if existing:return existing['id']
        oid=self.engine.submit(flow,request_id,container_id=container_id,vehicle_id=vehicle_id)
        try:
            if flow=='unload':self.prepare_ship_order(oid,source_path)
            elif flow=='load':self.prepare_load_order(oid)
            else:self.prepare_ground_order(oid)
            self.engine.checkpoint()
            return oid
        except Exception:
            o=self.engine.orders.pop(oid)
            self.engine.containers.pop(o['container'],None)
            pickup=self.pickups.pop(oid,None)
            if pickup:pickup.close()
            self.ship_bindings.pop(o['container'],None)
            for key in [k for k,v in self.engine.reserved.items() if v==oid]:self.engine.reserved.pop(key)
            if not self.engine.orders:self.release_ship()
            self.engine.checkpoint()
            raise

    def check_scene(self):
        from iecs_scene_checks import inspect_scene
        self.scene_report=inspect_scene(self.stage,list(self.engine.containers.values()),list(self.engine.vehicles.values()))
        return self.scene_report

    def inventory_preview(self):
        from iecs_inventory import project_inventory
        return project_inventory(PROJECT/'omniverse/data/yard_inventory.live.json',
                                 list(self.engine.orders.values()),list(self.engine.containers.values()))

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
            self.advance(float(event.payload.get('dt',1/60)))
            if self.window and time.monotonic()-self.last_ui>.25:
                self.check_scene();self.publish()
                self.last_ui=time.monotonic();self.render_ui()
        except Exception as e:
            if self.external:self.external.fail(str(e))
            self.last_error=str(e);self.engine.paused=True;self.publish()
            if self.window:self.render_ui()

    def advance(self,dt):
        if self.external:self.external.poll()
        self.engine.tick(dt)
        self.apply_scene()
        if self.external:self.external.observe()
        self.publish()

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
        rows += ['','Recent Events']
        for event in s['events'][-8:]:
            elapsed=f"  {event['elapsed_s']:.1f}s elapsed, {event['wait_s']:.1f}s waiting" if 'elapsed_s' in event else ''
            rows.append(f"{event['time']:.1f}  {event['kind']}  {event.get('order','')}{elapsed}")
        self.details.text='\n'.join(rows)

    def close(self):
        if self.closed:return
        self.closed=True;self.subscription=None
        if self.external:self.external.close()
        for pickup in self.pickups.values():pickup.close()
        if self.shared_sts:self.shared_sts.close();self.shared_sts=None
        self.release_ship()
        if self.visual_rtg:self.visual_rtg.close();self.visual_rtg=None
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
