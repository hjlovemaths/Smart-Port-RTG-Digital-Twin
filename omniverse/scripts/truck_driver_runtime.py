"""Task-owned USD truck, measured scene obstacles and exclusive driver camera."""
import builtins,math,re,time
from pxr import Gf,Sdf,Tf,Usd,UsdGeom
from ship_transfer_plan import PARK,box
from ship_to_yard_transfer import active_task,task_op,tr
from truck_driving import TruckDriving

ROOT='/World/TruckDriverDemo'
CAMERA=ROOT+'/DriverCamera'


def helper_geometry(prim):
    """Survey/preview glyphs are visible, but are not physical obstacles."""
    path=str(prim.GetPath());name=prim.GetName()
    if path.startswith(('/World/RTG_LIDAR_POSITION_MARKERS','/World/ParkedTruckNumbers')):return True
    if '/RTG_CAMERA_MARKERS_ROOT' in path:return True
    if re.search(r'(?:^|_)C\d{1,2}(?:$|_(?:green_sphere|label))',name):return True
    return prim.IsA(UsdGeom.Imageable) and UsdGeom.Imageable(prim).ComputePurpose()=='guide'


def collision_label(path):
    for token,label in (('CausewayEdgeCurbs','引桥路缘石'),('CausewayGuardrails','引桥护栏'),
                        ('QuayEdgeGuardrails','码头护栏'),('/Edges/','码头边沿'),
                        ('YardInventory','集装箱'),('ParkedYardTrucks','停放集卡'),
                        ('STSControls','岸桥构件'),('RTG_','RTG构件')):
        if token in (path or ''):return label
    return '场景物体'


def active_driver(stage=None):
    driver=getattr(builtins,'_smartport_truck_driver',None)
    return driver if driver and not driver.closed and (stage is None or driver.stage==stage) else None


def vector_mesh_rects(points,indices,counts,transform):
    """Clip triangle/quad batches to the same slab as the scalar reference."""
    try:import numpy as np
    except ImportError:return None
    counts=np.asarray(counts)
    if not len(counts) or counts[0] not in (3,4) or not np.all(counts==counts[0]):return None
    matrix=np.asarray(transform,dtype=np.float64)
    vertices=np.asarray(points,dtype=np.float64)@matrix[:3,:3]+matrix[3,:3]
    faces=vertices[np.asarray(indices).reshape(-1,int(counts[0]))]
    z=faces[:,:,2]
    faces=faces[(z.max(axis=1)>=.24)&(z.min(axis=1)<=1.15)]
    if not len(faces):return []
    z=faces[:,:,2];ends=np.roll(faces,-1,axis=1);dz=ends[:,:,2]-z
    candidates=[faces[:,:,:2]];masks=[(z>=.24)&(z<=1.15)]
    for height in (.24,1.15):
        cross=((z<height)&(ends[:,:,2]>height))|((z>height)&(ends[:,:,2]<height))
        t=np.divide(height-z,dz,out=np.zeros_like(z),where=cross)
        candidates.append((faces+(ends-faces)*t[:,:,None])[:,:,:2]);masks.append(cross)
    xy=np.concatenate(candidates,axis=1);valid=np.concatenate(masks,axis=1)
    low=np.where(valid[:,:,None],xy,np.inf).min(axis=1)-.001
    high=np.where(valid[:,:,None],xy,-np.inf).max(axis=1)+.001
    rectangles=np.concatenate((low,high),axis=1)
    return [tuple(r) for r in rectangles[np.isfinite(rectangles).all(axis=1)].tolist()]


def collision_rects(prim,cache,bounds=None):
    if bounds is None:bounds=cache.ComputeWorldBound(prim).ComputeAlignedRange()
    if not prim.IsA(UsdGeom.Mesh) or (max(bounds.GetSize()[0],bounds.GetSize()[1])<=4
            and bounds.GetMin()[2]>=.24 and bounds.GetMax()[2]<=1.15):
        return [(bounds.GetMin()[0],bounds.GetMin()[1],bounds.GetMax()[0],bounds.GetMax()[1])]
    # Clip even small meshes that cross the vehicle-height slab: a diagonal
    # RTG brace's full AABB projects its overhead end into the driving lane.
    # Merged rails also need per-face bounds to preserve the gaps between legs.
    mesh=UsdGeom.Mesh(prim);points=mesh.GetPointsAttr().Get()
    indices=mesh.GetFaceVertexIndicesAttr().Get();counts=mesh.GetFaceVertexCountsAttr().Get()
    if points is None or indices is None or counts is None:return []
    transform=UsdGeom.XformCache().GetLocalToWorldTransform(prim)
    if len(counts)>64:
        fast=vector_mesh_rects(points,indices,counts,transform)
        if fast is not None:return fast
    points=[transform.Transform(Gf.Vec3d(p)) for p in points];start=0;result=[]
    for count in counts:
        polygon=[points[i] for i in indices[start:start+count]];start+=count
        if not polygon or max(p[2] for p in polygon)<.24 or min(p[2] for p in polygon)>1.15:continue
        for height,above in ((.24,True),(1.15,False)):
            clipped=[]
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                inside_a=a[2]>=height if above else a[2]<=height
                inside_b=b[2]>=height if above else b[2]<=height
                if inside_a:clipped.append(a)
                if inside_a!=inside_b:clipped.append(a+(b-a)*((height-a[2])/(b[2]-a[2])))
            polygon=clipped
            if not polygon:break
        if polygon:
            result.append((min(p[0] for p in polygon)-.001,min(p[1] for p in polygon)-.001,
                           max(p[0] for p in polygon)+.001,max(p[1] for p in polygon)+.001))
    return result


class SceneObstacles:
    def __init__(self,stage,deferred=False,center=None,dynamic_source=None,radius=24,snap=True):
        self.region=None if center is None else tuple(math.floor(v/16)*16+8 if snap else v for v in center)
        self.radius=radius
        self.bounds_cache=dynamic_source.bounds_cache if dynamic_source else UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        self.geometry_cache=dynamic_source.geometry_cache if dynamic_source else {}
        self.static_meta=dynamic_source.static_meta if dynamic_source else {}
        self.dynamic_revisions=dynamic_source.dynamic_revisions if dynamic_source else {}
        self.dynamic_source=dynamic_source
        self.scanned=0
        self.ready=False;self._builder=self._build(stage)
        self.advance(.003 if deferred else float('inf'))

    def covers(self,x,y,margin=8):
        radii=self.radius if isinstance(self.radius,tuple) else (self.radius,self.radius)
        return self.region is None or all(abs(v-c)<=r-min(margin,r*.75) for v,c,r in zip((x,y),self.region,radii))

    def advance(self,budget=.003):
        deadline=time.monotonic()+budget
        while not self.ready:
            try:next(self._builder)
            except StopIteration:self.ready=True;self._builder=None;break
            if time.monotonic()>=deadline:break

    def _build(self,stage):
        self.stage=stage;self.surfaces=[(-101.75,-10.25,150.,500.)]
        self.grounds=[((-101.75,-10.25,108.75,500.),.106),((108.75,-10.25,150.,500.),.085)]
        from rtg_live_control import GANTRY_PATH
        self.dynamic_roots=(GANTRY_PATH,'/World/STSControls')
        self.static={};self.dynamic=[]
        self.dynamic_groups=self.dynamic_source.dynamic_groups if self.dynamic_source else {}
        self.dynamic_cache=self.dynamic_source.dynamic_cache if self.dynamic_source else {}
        self.dynamic_source=None
        cache=self.bounds_cache
        decks=stage.GetPrimAtPath('/World/SMART_PORT_OFFSHORE_QUAY/Decks')
        if decks:
            for prim in decks.GetChildren():
                b=cache.ComputeWorldBound(prim).ComputeAlignedRange()
                if not b.IsEmpty():
                    rect=(b.GetMin()[0],b.GetMin()[1],b.GetMax()[0],b.GetMax()[1])
                    self.surfaces.append(rect);self.grounds.append((rect,b.GetMax()[2]))
        iterator=Usd.PrimRange(stage.GetPseudoRoot(),Usd.TraverseInstanceProxies())
        iterator=iter(iterator)
        for prim in iterator:
            yield
            self.scanned+=1
            path=str(prim.GetPath())
            if path.startswith('/Render'):iterator.PruneChildren();continue
            manual=prim.GetAttribute('smartport:driverCargo') if not prim.IsPseudoRoot() else None
            if manual and manual.Get():iterator.PruneChildren();continue
            if path in (PARK,ROOT,'/World/ParkedTruckNumbers/Truck01') or prim.GetName().startswith(('CODEx_reference_feeder_ship','SMART_PORT_OUTBOUND_CARGO_SHIP','SMART_PORT_LARGE_CONTAINER_SHIP')):
                iterator.PruneChildren();continue
            moving=any(path==p or path.startswith(p+'/') or p.startswith(path+'/') for p in self.dynamic_roots)
            meta=self.static_meta.get(path) if not moving else None
            if meta is None:
                skip=helper_geometry(prim) or (prim.IsA(UsdGeom.Imageable) and UsdGeom.Imageable(prim).ComputeVisibility()=='invisible')
                xform=prim.IsA(UsdGeom.Xformable);boundable=prim.IsA(UsdGeom.Boundable)
                bounds=cache.ComputeWorldBound(prim).ComputeAlignedRange() if not skip and xform and not moving else None
                meta=(skip,xform,boundable,bounds)
                if not moving:self.static_meta[path]=meta
            skip,xform,boundable,bounds=meta
            if skip:iterator.PruneChildren();continue
            if path==GANTRY_PATH or (path.startswith('/World/STSControls/') and prim.GetName()=='Gantry'):
                if path not in self.dynamic_groups:
                    bounds=cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
                    envelope=Gf.Range3d(bounds.GetMin()-Gf.Vec3d(25,10,0),bounds.GetMax()+Gf.Vec3d(25,10,0))
                    self.dynamic_groups[path]=(prim,envelope,None)
                iterator.PruneChildren();continue
            # Cull complete inventory bays / parked trucks / static cranes
            # before visiting their detailed meshes. Moving equipment stays
            # globally registered so it can enter this region later.
            if self.region and xform and not moving:
                b=bounds;x,y=self.region
                rx,ry=self.radius if isinstance(self.radius,tuple) else (self.radius,self.radius)
                if b.IsEmpty() or b.GetMax()[2]<=.24 or b.GetMin()[2]>=1.15 or b.GetMax()[0]<x-rx or b.GetMin()[0]>x+rx or b.GetMax()[1]<y-ry or b.GetMin()[1]>y+ry:
                    iterator.PruneChildren();continue
            if not boundable:continue
            dynamic_root=next((p for p in self.dynamic_roots if path==p or path.startswith(p+'/')),None)
            if dynamic_root:
                root_path=dynamic_root if dynamic_root==GANTRY_PATH else '/'.join(path.split('/')[:4])+'/Gantry'
                root=stage.GetPrimAtPath(root_path)
                if root:
                    if root_path not in self.dynamic_groups:
                        bounds=cache.ComputeUntransformedBound(root).ComputeAlignedRange()
                        envelope=Gf.Range3d(bounds.GetMin()-Gf.Vec3d(25,10,0),bounds.GetMax()+Gf.Vec3d(25,10,0))
                        self.dynamic_groups[root_path]=(root,envelope,[])
                    local=cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
                    self.dynamic_groups[root_path][2].append((prim,local));self.dynamic.append(prim)
                continue
            b=bounds if bounds is not None else cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if b.IsEmpty() or b.GetMax()[2]<=.24 or b.GetMin()[2]>=1.15:continue
            if self.crossable_track(prim,b):continue
            if b.GetMax()[0]<-250 or b.GetMin()[0]>155 or b.GetMax()[1]<-15 or b.GetMin()[1]>515:continue
            # Flat road meshes/markings and water are support, not obstacles.
            if b.GetSize()[2]<.025 and b.GetMin()[2]<.3:continue
            if path not in self.geometry_cache:self.geometry_cache[path]=collision_rects(prim,cache,b)
            for index,rect in enumerate(self.geometry_cache[path]):
                entry=(rect,path+'#'+str(index))
                for x in range(math.floor(rect[0]/10),math.floor(rect[2]/10)+1):
                    for y in range(math.floor(rect[1]/10),math.floor(rect[3]/10)+1):
                        self.static.setdefault((x,y),[]).append(entry)
        # Prepare nearby crane geometry before the truck reaches its wheels.
        # A cold crane must not cause a long synchronous first-contact query.
        if self.region:
            x,y=self.region;rx,ry=self.radius if isinstance(self.radius,tuple) else (self.radius,self.radius)
            transforms=UsdGeom.XformCache()
            for root_path,(root,envelope,_) in list(self.dynamic_groups.items()):
                b=Gf.BBox3d(envelope,transforms.GetLocalToWorldTransform(root)).ComputeAlignedRange()
                if b.GetMax()[0]>=x-rx and b.GetMin()[0]<=x+rx and b.GetMax()[1]>=y-ry and b.GetMin()[1]<=y+ry:
                    yield from self.warm_dynamic(root_path)

    def crossable_track(self,prim,bounds):
        # Only the two authored ground tracks are wheel-crossable. Never match
        # crane running gear, elevated trolley rails, guardrails or raised edges.
        if str(prim.GetPath()) not in (
                '/World/SMART_PORT_OFFSHORE_QUAY/Rails/STS_Rail_01',
                '/World/SMART_PORT_OFFSHORE_QUAY/Rails/STS_Rail_02'):return False
        center=bounds.GetMidpoint();ground=self.ground_z(center[0],center[1])
        return (bounds.GetMin()[2]>=ground-.01 and bounds.GetMax()[2]<=ground+.10
                and bounds.GetSize()[2]<=.10 and bounds.GetSize()[0]<=.20)

    def supported(self,x,y):return any(a<=x<=c and b<=y<=d for a,b,c,d in self.surfaces)

    def ground_z(self,x,y):return max((z for (a,b,c,d),z in self.grounds if a<=x<=c and b<=y<=d),default=.085)

    def invalidate_dynamic(self,path):
        for root in self.dynamic_groups:
            if path.startswith(root) or root.startswith(path.split('.')[0]):
                self.dynamic_cache.pop(root,None);self.dynamic_revisions[root]=self.dynamic_revisions.get(root,0)+1

    def warm_dynamic(self,root_path):
        if root_path in self.dynamic_cache:return
        root,envelope,prims=self.dynamic_groups[root_path]
        revision=self.dynamic_revisions.get(root_path,0)
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']);transforms=UsdGeom.XformCache()
        entries=[];found=[]
        for prim,local in (self.dynamic_prims(root) if prims is None else prims):
            yield
            found.append((prim,local))
            if not prim or UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
            b=Gf.BBox3d(local,transforms.GetLocalToWorldTransform(prim)).ComputeAlignedRange()
            if b.IsEmpty() or b.GetMax()[2]<=.24 or b.GetMin()[2]>=1.15:continue
            entries.extend((rect,str(prim.GetPath())+'#'+str(i)) for i,rect in enumerate(collision_rects(prim,cache,b)))
        if self.dynamic_revisions.get(root_path,0)==revision:
            self.dynamic_groups[root_path]=(root,envelope,found);self.dynamic_cache[root_path]=entries

    def dynamic_prims(self,root):
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        iterator=iter(Usd.PrimRange(root,Usd.TraverseInstanceProxies()))
        for prim in iterator:
            if helper_geometry(prim) or (prim.IsA(UsdGeom.Imageable) and UsdGeom.Imageable(prim).ComputeVisibility()=='invisible'):
                iterator.PruneChildren();continue
            if prim.IsA(UsdGeom.Boundable):yield (prim,cache.ComputeUntransformedBound(prim).ComputeAlignedRange())

    def near(self,x,y):
        from rtg_driver_cargo import body_paths,TRUCK_OWNER,TRUCK,attr
        cargo_paths=body_paths(self.stage)
        result={}
        for a in range(math.floor((x-6)/10),math.floor((x+6)/10)+1):
            for b in range(math.floor((y-6)/10),math.floor((y+6)/10)+1):
                for entry in self.static.get((a,b),[]):result[entry[1]]=entry
        result={path:entry for path,entry in result.items() if not (
            entry[0][0]>x+5 or entry[0][2]<x-5 or entry[0][1]>y+5 or entry[0][3]<y-5)}
        # A manually lifted source may already be in an older static region.
        # Replace those old rectangles with its current canonical box below.
        result={path:entry for path,entry in result.items() if not any(
            path.split('#')[0]==p or path.startswith(p+'_') or path.startswith(p+'/') for p in cargo_paths)}
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        transforms=UsdGeom.XformCache()
        for root_path,(root,envelope,prims) in self.dynamic_groups.items():
            if not root:continue
            bound=Gf.BBox3d(envelope,transforms.GetLocalToWorldTransform(root)).ComputeAlignedRange()
            if bound.GetMin()[0]>x+6 or bound.GetMax()[0]<x-6 or bound.GetMin()[1]>y+6 or bound.GetMax()[1]<y-6:continue
            if root_path not in self.dynamic_cache:
                for _ in self.warm_dynamic(root_path):pass
            for rect,path in self.dynamic_cache.get(root_path,[]):
                if rect[0]>x+5 or rect[2]<x-5 or rect[1]>y+5 or rect[3]<y-5:continue
                result[path]=(rect,path)
        for path in cargo_paths:
            prim=self.stage.GetPrimAtPath(path)
            if not prim or attr(prim,TRUCK_OWNER)==TRUCK:continue
            if UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':
                model=self.stage.GetPrimAtPath(path+'_ImportedModel')
                if not model or UsdGeom.Imageable(model).ComputeVisibility()=='invisible':continue
            if prim.IsA(UsdGeom.Cube):
                size=UsdGeom.Cube(prim).GetSizeAttr().Get()
                if size is None:continue
                bound=Gf.BBox3d(Gf.Range3d(Gf.Vec3d(-size/2),Gf.Vec3d(size/2)),transforms.GetLocalToWorldTransform(prim)).ComputeAlignedRange()
            else:bound=cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if bound.IsEmpty():continue
            if bound.GetMax()[2]<=.24 or bound.GetMin()[2]>=1.15:continue
            rect=(bound.GetMin()[0],bound.GetMin()[1],bound.GetMax()[0],bound.GetMax()[1])
            if rect[0]<=x+5 and rect[2]>=x-5 and rect[1]<=y+5 and rect[3]>=y-5:result[path]=(rect,path)
        return list(result.values())


class TruckDriver:
    def __init__(self,stage,viewport,owner,session,clock=time.monotonic,cab_stage=None,deferred_world=False):
        if stage is None or viewport is None:raise ValueError('港区场景或驾驶视口尚未就绪，请稍后重试')
        if active_driver():raise ValueError('01号集卡已由另一驾驶会话占用')
        if active_task(stage):raise ValueError('自动搬运占用01号集卡，请先结束并复位搬运任务')
        if not stage.GetPrimAtPath(PARK):raise ValueError('场景中没有01号集卡')
        self.stage=stage;self.viewport=viewport;self.owner=owner;self.session=session;self.clock=clock
        self.closed=False;self.view='cabin';self.last_input=clock();self.last_seen=clock();self.last_seq=-1
        self.notice='';self.notice_until=0.;self.last_collision_path=None;self.previous_camera=str(viewport.camera_path)
        self.layer=Sdf.Layer.CreateAnonymous('truck_driver_demo.usda');self.world=None
        stage.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        try:
            with Usd.EditContext(stage,self.layer):
                source=stage.GetPrimAtPath(PARK);bounds=box(stage,PARK);self.origin=bounds.GetMidpoint();self.spawn_ground=bounds.GetMin()[2]
                self.original_world=UsdGeom.XformCache().GetLocalToWorldTransform(source)
                self.truck=UsdGeom.Xform.Define(stage,ROOT+'/Truck').GetPrim()
                self.truck.GetReferences().AddInternalReference(PARK);self.truck.SetInstanceable(False)
                UsdGeom.Imageable(self.truck).CreateVisibilityAttr().Set('inherited')
                xf,self.truck_op=task_op(self.truck,'driver');xf.SetXformOpOrder([self.truck_op],resetXformStack=True)
                UsdGeom.Imageable(source).CreateVisibilityAttr().Set('invisible')
                label=stage.GetPrimAtPath('/World/ParkedTruckNumbers/Truck01')
                if label:UsdGeom.Imageable(label).CreateVisibilityAttr().Set('invisible')
                size=bounds.GetSize();self.engine=TruckDriving((self.origin[0],self.origin[1],0.),size[0]/2,size[1]/2)
                self.camera=UsdGeom.Camera.Define(stage,CAMERA)
                self.camera.CreateClippingRangeAttr(Gf.Vec2f(.025,3000))
                self.camera.CreateFocalLengthAttr(18);self.camera.CreateHorizontalApertureAttr(36)
                self.camera.CreateVerticalApertureAttr(20.25);self.camera_op=self.camera.AddTransformOp()
                from truck_driver_cab import DriverCab
                appearance=cab_stage or stage
                with Usd.EditContext(appearance,appearance.GetSessionLayer() if cab_stage else self.layer):
                    self.cab=DriverCab(appearance,appearance.GetPrimAtPath(self.truck.GetPath()),ROOT+'/CabInterior')
                from rtg_driver_cargo import sync_truck_cargo
                sync_truck_cargo(stage,PARK,ROOT+'/Truck')
                self.apply()
            self.regional_world=deferred_world
            self.driving_ready=not deferred_world
            self.world=SceneObstacles(stage,deferred=deferred_world,center=(self.engine.x,self.engine.y) if deferred_world else None);self.obstacles=[] if not self.world.ready else self.world.near(self.engine.x,self.engine.y);self.next_obstacles=clock()+.2
            self.rebuilding_world=None;self.next_index_advance=0.
            self.checked_regions=[];self.local_checks=0;self.local_check_ms=0.;self.index_revision=0;self.last_index_change=''
            self.next_rebuild=clock()+2;self.world_dirty=False
            self.subscription=Tf.Notice.Register(Usd.Notice.ObjectsChanged,self.objects_changed,stage)
            self.last_input=self.last_seen=clock()
            builtins._smartport_truck_driver=self
        except Exception:self.close();raise

    def objects_changed(self,notice,sender):
        for path in [*notice.GetResyncedPaths(),*notice.GetChangedInfoOnlyPaths()]:
            name=str(path)
            if not name.startswith('/World/'):continue
            if name.startswith(ROOT):continue
            prim=self.stage.GetPrimAtPath(path.GetPrimPath())
            manual=prim.GetAttribute('smartport:driverCargo') if prim else None
            if manual and manual.Get():self.next_obstacles=0.;continue
            if prim and helper_geometry(prim):continue
            if any(name.startswith(p) for p in self.world.dynamic_roots):
                if path.IsPrimPath() or path.name.startswith(('xformOp','visibility')):
                    for world in [self.world,self.rebuilding_world,*self.checked_regions]:
                        if world:world.invalidate_dynamic(name)
                continue
            if 'SHIP' in name.upper() or 'SEA' in name.upper() or 'Camera' in name:continue
            if path.IsPrimPath() or path.name.startswith(('xformOp','visibility')):
                self.world_dirty=True;self.checked_regions=[];self.last_index_change=name

    def command(self,owner,payload):
        if owner!=self.owner:raise ValueError('01号集卡已由另一驾驶会话占用')
        if set(payload)-{'throttle','steer','brake','view','reset','seq'}:raise ValueError('驾驶指令包含未知参数')
        seq=payload.get('seq',self.last_seq+1)
        if type(seq) is not int or not 0<=seq<2**53:raise ValueError('驾驶指令序号无效')
        if seq<=self.last_seq:return self.state()
        if payload.get('view',self.view) not in ('cabin','follow'):raise ValueError('视角须为 cabin 或 follow')
        if 'reset' in payload and type(payload['reset']) is not bool:raise ValueError('复位参数须为布尔值')
        self.engine.controls(payload.get('throttle',0),payload.get('steer',0),payload.get('brake',False))
        self.last_seq=seq
        self.view=payload.get('view',self.view);self.last_input=self.clock();self.last_seen=self.clock()
        if payload.get('reset'):
            self.engine.reset();self.notice='车辆已重置';self.notice_until=self.clock()+3;self.last_collision_path=None
        with Usd.EditContext(self.stage,self.layer):self.apply()
        return self.state()

    def apply(self):
        e=self.engine;rotation=Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),math.degrees(e.heading)))
        ground=self.world.ground_z(e.x,e.y) if self.world else self.spawn_ground
        center=Gf.Vec3d(e.x,e.y,self.origin[2]+ground-self.spawn_ground)
        self.truck_op.Set(self.original_world*tr(-self.origin)*rotation*tr(center))
        from rtg_driver_cargo import sync_truck_cargo
        sync_truck_cargo(self.stage,ROOT+'/Truck')
        self.cab.apply(self.view=='cabin',rotation*tr(center),e.steer)
        forward=Gf.Vec3d(-math.cos(e.heading),-math.sin(e.heading),0)
        side=Gf.Vec3d(-forward[1],forward[0],0)
        if self.view=='cabin':
            eye=center+forward*(e.half_length-.55)-side*.23+Gf.Vec3d(0,0,.25)
            target=eye+forward*15+Gf.Vec3d(0,0,-.2)
        else:
            eye=center-forward*10+Gf.Vec3d(0,0,5);target=center+forward*1.5+Gf.Vec3d(0,0,.3)
        self.camera_op.Set(Gf.Matrix4d(1).SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
        self.viewport.camera_path=CAMERA

    def tick(self,dt):
        if self.closed:return
        now=self.clock()
        if self.session.get('driver_released') or self.session.get('expires',0)<=time.time() or now-self.last_seen>10:
            self.close();return
        if getattr(self,'private_view',None):self.private_view.tick()
        if now-self.last_input>.35:self.engine.stop()
        # Prefetch with an overlap while the old region remains safe to drive.
        if not self.world_dirty and self.world.region and self.world.ready and self.rebuilding_world is None and (
                self.world.radius==24 or not self.world.covers(self.engine.x,self.engine.y,margin=48)):
            # Start expanding immediately after boot, with seconds of travel
            # already covered ahead. Reuse unchanged bounds and exact meshes.
            sign=-1 if self.engine.speed<-.1 or self.engine.throttle<0 else 1
            lead=min(16.,max(8.,abs(self.engine.speed)*2))
            center=(self.engine.x-sign*math.cos(self.engine.heading)*lead,
                    self.engine.y-sign*math.sin(self.engine.heading)*lead)
            radii=(32+32*abs(math.cos(self.engine.heading)),32+32*abs(math.sin(self.engine.heading)))
            self.rebuilding_world=SceneObstacles(self.stage,deferred=True,center=center,dynamic_source=self.world,radius=radii)
        if self.world_dirty:
            # Inventory/replaced geometry is reflected during a driving session.
            # Replace a stale in-flight prefetch immediately. Publishing it or
            # retaining its old bounds after a physical edit would be unsafe.
            self.rebuilding_world=SceneObstacles(self.stage,deferred=True,center=(self.engine.x,self.engine.y) if self.regional_world else None)
            self.index_invalid=True
            self.index_revision+=1;self.checked_regions=[]
            print('[Truck driver collision] geometry refresh: '+self.last_index_change,flush=True)
            self.world_dirty=False
        building=self.rebuilding_world or (self.world if not self.world.ready else None)
        if building:
            # Expanding the world collision index is background work. Giving
            # it 10 ms on every Kit update can consume a large fraction of the
            # main thread while the truck is already driving. The active
            # collision query uses a verified region (or a synchronous local
            # exact query at an uncovered edge), so build the larger prefetch
            # in small, spaced batches instead.
            if not self.world.ready or now>=self.next_index_advance:
                building.advance(.0015 if self.world.ready else .003)
                self.next_index_advance=now+(.025 if self.world.ready else 0.)
            if building.ready:
                if self.world.ready and not getattr(self,'index_invalid',False):self.remember_region(self.world)
                self.world=building;self.rebuilding_world=None;self.next_rebuild=now+2;self.next_obstacles=0
                if self.world.radius!=24:self.driving_ready=True
            elif not self.world.ready:
                self.engine.stop()
                with Usd.EditContext(self.stage,self.layer):self.apply()
                return
            if building.ready:self.index_invalid=False
        if not self.driving_ready:
            self.engine.stop()
            with Usd.EditContext(self.stage,self.layer):self.apply()
            return
        query_world=self.world
        if getattr(self,'index_invalid',False) or self.world_dirty or not self.world.covers(self.engine.x,self.engine.y):
            query_world=next((w for w in reversed(self.checked_regions) if w.covers(self.engine.x,self.engine.y)),None)
            if query_world is None:
                # The 16 m radius includes the complete truck footprint and
                # next swept step, with at least 8 m of verified edge margin.
                # Finish this narrow exact query; do not wait for a big tile.
                source=(self.rebuilding_world if getattr(self,'index_invalid',False) else self.world)
                if self.world_dirty:source=None
                started=time.monotonic()
                query_world=SceneObstacles(self.stage,center=(self.engine.x,self.engine.y),
                                           radius=16,snap=False,dynamic_source=source)
                self.local_check_ms=(time.monotonic()-started)*1000
                self.local_checks+=1;self.remember_region(query_world)
            self.next_obstacles=0
        if now>=self.next_obstacles:
            self.obstacles=query_world.near(self.engine.x,self.engine.y);self.next_obstacles=now+.1
        self.engine.step(dt,self.obstacles,query_world.supported)
        if self.engine.event in ('collision','water_reset'):
            self.last_collision_path=self.engine.hit if self.engine.event=='collision' else None
            self.notice=('碰撞停车：'+collision_label(self.engine.hit)+'，请转向或后退脱离') if self.engine.event=='collision' else '车辆落海，已重置到初始位置'
            self.notice_until=now+3
        with Usd.EditContext(self.stage,self.layer):self.apply()

    def remember_region(self,world):
        self.checked_regions=(self.checked_regions+[world])[-8:]

    def state(self):
        return dict(self.engine.state(),vehicle_id='01',view=self.view,status='closed' if self.closed else 'driving',
                    collision_path=self.engine.hit or (self.last_collision_path if self.clock()<self.notice_until else None),
                    input_seq=self.last_seq,
                    collision_strategy='exact_local_with_prefetch_v2',
                    collision_scanned=(self.rebuilding_world or self.world).scanned,
                    collision_local_checks=self.local_checks,collision_index_revision=self.index_revision,
                    collision_local_check_ms=round(self.local_check_ms,1),
                    collision_last_change=self.last_index_change,
                    stream_ready=self.driving_ready and self.world.ready and (self.private_view.ready() if getattr(self,'private_view',None) else True),
                    startup_phase='collision_index' if not self.driving_ready or not self.world.ready else 'video' if getattr(self,'private_view',None) and not self.private_view.ready() else 'ready',
                    notice=self.notice if self.clock()<self.notice_until else '',camera_path=CAMERA)

    def close(self):
        if self.closed:return
        self.closed=True
        if getattr(self,'subscription',None):self.subscription.Revoke();self.subscription=None
        if getattr(self,'overlay',None):
            try:self.overlay.set_visible(self.previous_overlay_visible)
            except Exception:pass
            self.overlay=None
        if hasattr(self,'engine'):self.engine.stop()
        try:
            if str(self.viewport.camera_path)==CAMERA:self.viewport.camera_path=self.previous_camera
        except Exception:pass
        layers=self.stage.GetSessionLayer().subLayerPaths
        from rtg_driver_cargo import sync_truck_cargo
        sync_truck_cargo(self.stage,ROOT+'/Truck',PARK)
        if self.layer.identifier in layers:layers.remove(self.layer.identifier)
        if getattr(builtins,'_smartport_truck_driver',None) is self:builtins._smartport_truck_driver=None
        if getattr(self,'private_view',None):self.private_view.close();self.private_view=None


def execute_driver(panel,stage,viewport,action,owner,session,payload):
    current=active_driver()
    if current and current.stage!=stage:current.close();current=None
    if action=='acquire':
        if current and current.owner!=owner:raise ValueError('01号集卡已由另一驾驶会话占用')
        if not current:
            if getattr(panel,'_busy',False) or getattr(panel,'_pending_approval',None):raise ValueError('请等待管理员当前操作完成后再进入驾驶模式')
            runtime=getattr(panel,'_runtime',None)
            if runtime is None:
                from pathlib import Path
                from smartport.agent_panel.scene_runtime import SceneRuntime
                runtime=SceneRuntime(Path(__file__).resolve().parents[2]);panel._runtime=runtime
            from smartport.agent_panel.project_interfaces import require_idle
            runtime.bind(stage);require_idle(runtime,allow_rtg_driver=True,allow_sts_driver=True)
            session['driver_released']=False
            from role_viewport import DriverViewport
            view=DriverViewport(stage,CAMERA)
            try:
                current=TruckDriver(stage,view,owner,session,cab_stage=view.stage,deferred_world=True)
                current.private_view=view
            except Exception:
                if current:current.close()
                view.close();raise
        return current.state()
    if not current:return {'status':'idle','vehicle_id':'01'}
    if current.owner!=owner:raise ValueError('01号集卡已由另一驾驶会话占用')
    if action=='state':current.last_seen=current.clock();return current.state()
    if action=='release':current.close();return {'status':'released','vehicle_id':'01'}
    if action=='input':return current.command(owner,payload)
    raise ValueError('未知驾驶操作')
