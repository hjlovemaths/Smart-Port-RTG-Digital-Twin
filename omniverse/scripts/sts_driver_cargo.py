"""Kinematic STS_01 picking and measured contact against rendered cargo/trucks.

Ship structure slots are visual simulation boxes, not an inventory manifest.
"""
import math
from pxr import Gf,Sdf,Tf,Usd,UsdGeom
from large_vessel_layout import SHIP
from rtg_driver_cargo import body_paths,bind_truck,members,world,write_world,TRUCK_OWNER,TRUCK,attr,ROOT as LIVE_ROOT,BODIES,MANUAL
from rtg_plc_contact import box_bounds,sweep_box
from rtg_plc_interaction import corners,rigid_pose
from ship_transfer_plan import PARK

HELD='smartport:stsDriverHeldCargo'

def cargo_bound(stage,prim,cache):
    """Measure a hidden Cube proxy or a visible ship box in world space."""
    if prim.IsA(UsdGeom.Cube):
        size=UsdGeom.Cube(prim).GetSizeAttr().Get()
        if size is None:return Gf.Range3d()
        return Gf.BBox3d(Gf.Range3d(Gf.Vec3d(-size/2),Gf.Vec3d(size/2)),world(prim)).ComputeAlignedRange()
    return cache.ComputeWorldBound(prim).ComputeAlignedRange()

class STSCargo:
    def __init__(self,stage,controller,contact_points):
        self.stage=stage;self.controller=controller;self.contact_points=contact_points
        self.hoist=stage.GetPrimAtPath(controller.path+'/Gantry/Trolley/Hoist')
        self.held=attr(self.hoist,HELD,'') or None;self.relative={};self.last_matrix=None
        self.records={};self.decks={};self.truck_shapes=[];self.static_shapes=[];self.last_refresh=-1e9;self.ship_world=None;self.local_bounds={}
        self.dirty=True;self.subscription=Tf.Notice.Register(Usd.Notice.ObjectsChanged,self.changed,stage)
        try:
            if self.held:self.capture(self.held)
        except BaseException:self.close();raise

    def changed(self,notice,sender):
        for path in (*notice.GetResyncedPaths(),*notice.GetChangedInfoOnlyPaths()):
            name=str(path.GetPrimPath())
            if self.held and (name==self.held or name.startswith(self.held+'/')):continue
            prim=self.stage.GetPrimAtPath(path.GetPrimPath()) if path.GetPrimPath()!=Sdf.Path.absoluteRootPath else None
            if attr(prim,TRUCK_OWNER)==TRUCK:continue
            prefix=SHIP+'/StructureSlots/'
            if name.startswith(prefix):
                parts=name[len(prefix):].split('/')
                if len(parts)>=2:self.local_bounds.pop(prefix+'/'.join(parts[:2]),None)
                else:self.local_bounds.clear()
            elif name in ('/','/World',SHIP,SHIP+'/StructureSlots'):self.local_bounds.clear()
            if name in ('/','/World') or name.startswith(SHIP+'/StructureSlots') or name.startswith(LIVE_ROOT) or name.startswith('/World/SMART_PORT_OFFSHORE_QUAY/Decks'):
                self.dirty=True;break

    def close(self):
        if self.subscription:self.subscription.Revoke();self.subscription=None

    @property
    def locked(self):return bool(self.held)

    def capture(self,path):
        parts=members(self.stage,path)
        if not parts:raise ValueError('闭锁箱子已不存在，不能恢复操作')
        inverse=world(self.hoist).GetInverse()
        self.relative={str(p.GetPath()):world(p)*inverse for p in parts};self.last_matrix=None

    def refresh(self,force=False,now=None):
        now=__import__('time').monotonic() if now is None else now
        centre=sum(self.contact_points(),Gf.Vec3d(0))/4
        # Cache the static vessel box list briefly; the truck itself is read
        # each time so another driver's movement is reflected immediately.
        ship=self.stage.GetPrimAtPath(SHIP);ship_world=world(ship) if ship else None
        if force or self.dirty or now-self.last_refresh>.3 or ship_world!=self.ship_world:
            self.records={};cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']);xforms=UsdGeom.XformCache()
            root=self.stage.GetPrimAtPath(SHIP+'/StructureSlots')
            if root:
                for bay in root.GetChildren():
                    for prim in bay.GetChildren():
                        size=attr(prim,'ship:sizeFt')
                        if size not in (20,40) or UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
                        point=xforms.GetLocalToWorldTransform(prim).ExtractTranslation()
                        # The crane advances at most a small fraction of a metre
                        # between refreshes; 7 m covers a 40 ft box and the
                        # swept motion while keeping unrelated bays out of the
                        # main-thread collision query.
                        if abs(point[0]-centre[0])>7 or abs(point[1]-centre[1])>7:continue
                        path=str(prim.GetPath());local=self.local_bounds.get(path)
                        if local is None:
                            local=cache.ComputeUntransformedBound(prim).ComputeAlignedRange();self.local_bounds[path]=local
                        bound=Gf.BBox3d(local,xforms.GetLocalToWorldTransform(prim)).ComputeAlignedRange()
                        if not bound.IsEmpty():self.records[str(prim.GetPath())]=dict(path=str(prim.GetPath()),bounds=(tuple(bound.GetMin()),tuple(bound.GetMax())),size_ft=size)
            self.static_shapes=[]
            quay=self.stage.GetPrimAtPath('/World/SMART_PORT_OFFSHORE_QUAY/Decks')
            if quay:
                for prim in quay.GetChildren():
                    if not prim.IsA(UsdGeom.Boundable):continue
                    b=cache.ComputeWorldBound(prim).ComputeAlignedRange()
                    if not b.IsEmpty():self.static_shapes.append(('quay:ground',(tuple(b.GetMin()),tuple(b.GetMax()))))
            bridge=self.stage.GetPrimAtPath(SHIP+'/Bridge')
            if bridge:
                for prim in bridge.GetChildren():
                    if not prim.IsA(UsdGeom.Cube) or not prim.GetName().startswith(('Deckhouse','Accommodation','Overhang')):continue
                    b=cache.ComputeWorldBound(prim).ComputeAlignedRange()
                    if not b.IsEmpty():self.static_shapes.append(('ship:bridge',(tuple(b.GetMin()),tuple(b.GetMax()))))
            self.last_refresh=now
            self.ship_world=ship_world;self.dirty=False
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        # The RTG handoff registry also contains boxes currently on the truck.
        for path in body_paths(self.stage):
            prim=self.stage.GetPrimAtPath(path)
            if not prim or path==self.held:continue
            if UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':
                model=self.stage.GetPrimAtPath(path+'_ImportedModel')
                if not model or UsdGeom.Imageable(model).ComputeVisibility()=='invisible':continue
            b=cargo_bound(self.stage,prim,cache)
            if not b.IsEmpty():
                size=attr(prim,'ship:sizeFt') or attr(prim,'live:containerSizeFt')
                if size in (20,40):self.records[path]=dict(path=path,bounds=(tuple(b.GetMin()),tuple(b.GetMax())),size_ft=size)
        self.decks={};self.truck_shapes=[]
        for path in (TRUCK,PARK):
            truck=self.stage.GetPrimAtPath(path)
            if not truck or UsdGeom.Imageable(truck).ComputeVisibility()=='invisible':continue
            deck=Gf.Range3d()
            for prim in Usd.PrimRange(truck,Usd.TraverseInstanceProxies()):
                if not prim.IsA(UsdGeom.Boundable):continue
                name=prim.GetName()
                if 'trailer_side_rail' in name:deck.UnionWith(cache.ComputeRelativeBound(prim,truck).ComputeAlignedRange())
                if 'white_cab_body' in name:
                    bound=cache.ComputeWorldBound(prim).ComputeAlignedRange()
                    if not bound.IsEmpty():self.truck_shapes.append((path+':cab',(tuple(bound.GetMin()),tuple(bound.GetMax()))))
                elif any(tag in name for tag in ('skeletal_trailer_main_frame','trailer_crossbar','truck_wheel_')):
                    bound=cache.ComputeWorldBound(prim).ComputeAlignedRange()
                    if not bound.IsEmpty():self.truck_shapes.append((path+':body',(tuple(bound.GetMin()),tuple(bound.GetMax()))))
            if not deck.IsEmpty():
                matrix=world(truck);mid=deck.GetMidpoint();mid[2]=deck.GetMax()[2]
                centre=matrix.Transform(mid)-Gf.Vec3d(0,0,.075)
                size=tuple(matrix.TransformDir(Gf.Vec3d(*(deck.GetSize()[i] if j==i else 0 for j in range(3)))).GetLength() for i in range(2))+(.15,)
                self.decks[path]={'pose':rigid_pose(matrix,centre),'size':size}

    def lock(self,locked):
        if locked==self.locked:return
        self.update();self.refresh(True)
        points=self.contact_points();centre=sum(points,Gf.Vec3d(0))/4
        if locked:
            candidates=[]
            for record in self.records.values():
                lo,hi=record['bounds']
                if record['size_ft']!=self.controller.spreader_size_ft:continue
                if not all(lo[0]-.01<=p[0]<=hi[0]+.01 and lo[1]-.01<=p[1]<=hi[1]+.01 and abs(p[2]-hi[2])<=.025 for p in points):continue
                error=math.hypot(centre[0]-(lo[0]+hi[0])/2,centre[1]-(lo[1]+hi[1])/2)
                if error>.10:continue
                if any(other['path']!=record['path'] and other['bounds'][0][2]>=hi[2]-.002 and
                       all(min(other['bounds'][1][i],hi[i])-max(other['bounds'][0][i],lo[i])>.02 for i in (0,1)) for other in self.records.values()):continue
                candidates.append((error,record))
            if not candidates:raise ValueError('闭锁失败：请将匹配尺型的吊具四角对准同一只无遮挡箱子的箱顶')
            record=min(candidates,key=lambda x:x[0])[1];path=record['path']
            from rtg_live_control import HOIST_PATH
            if attr(self.stage.GetPrimAtPath(HOIST_PATH),'smartport:rtgDriverHeldCargo','')==path:
                raise ValueError('该箱子仍由RTG闭锁，请先落稳并开锁交接')
            owner=attr(self.stage.GetPrimAtPath(path),TRUCK_OWNER)
            self.check_truck(owner)
            self.capture(path)
            with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
                rel=self.stage.GetPrimAtPath(LIVE_ROOT).CreateRelationship(BODIES)
                if Sdf.Path(path) not in rel.GetTargets():rel.AddTarget(path)
                for prim in members(self.stage,path):prim.CreateAttribute(MANUAL,Sdf.ValueTypeNames.Bool).Set(True)
                self.hoist.CreateAttribute(HELD,Sdf.ValueTypeNames.String).Set(path)
            bind_truck(self.stage,path,None);self.held=path
        else:
            prim=self.stage.GetPrimAtPath(self.held)
            cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
            if prim.IsA(UsdGeom.Cube):
                value=UsdGeom.Cube(prim).GetSizeAttr().Get()
                if value is None:raise ValueError('闭锁箱子缺少有效几何')
                b=Gf.Range3d(Gf.Vec3d(-value/2),Gf.Vec3d(value/2))
            else:b=cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
            if b.IsEmpty():raise ValueError('闭锁箱子缺少有效几何')
            matrix=world(prim);lo,hi=b.GetMin(),b.GetMax()
            sx=matrix.TransformDir(Gf.Vec3d(1,0,0)).GetLength();sy=matrix.TransformDir(Gf.Vec3d(0,1,0)).GetLength()
            if sx<1e-9 or sy<1e-9:raise ValueError('闭锁箱子比例无效')
            points=[matrix.Transform(Gf.Vec3d(x,y,lo[2])) for x in (lo[0]+.158/sx,hi[0]-.158/sx) for y in (lo[1]+.17/sy,hi[1]-.17/sy)]
            supports=[]
            for point in points:
                hit=None
                for path,deck in self.decks.items():
                    local=deck['pose'].GetInverse().Transform(point);sx,sy,sz=deck['size']
                    if abs(local[0])<=sx/2+.015 and abs(local[1])<=sy/2+.015 and -.002<=local[2]-sz/2<=.045:hit=path;break
                for path,bounds in [(r['path'],r['bounds']) for r in self.records.values() if r['path']!=self.held] if hit is None else []:
                    low,high=bounds
                    if low[0]-.015<=point[0]<=high[0]+.015 and low[1]-.015<=point[1]<=high[1]+.015 and -.002<=point[2]-high[2]<=.045:hit=path;break
                if hit is None:raise ValueError('开锁失败：箱子四个底部承重点须落在集卡或其他箱子上')
                supports.append(hit)
            owners={path if path in self.decks else attr(self.stage.GetPrimAtPath(path),TRUCK_OWNER) for path in supports}
            owner=next(iter(owners)) if len(owners)==1 and None not in owners and '' not in owners else None
            self.check_truck(owner);bind_truck(self.stage,self.held,owner)
            with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):self.hoist.GetAttribute(HELD).Set('')
            self.held=None;self.relative={};self.last_matrix=None

    def change_size(self,size):
        from rtg_plc_contact import box_bounds,sweep_box
        if self.locked:raise ValueError('请先落箱开锁，再切换20/40尺型')
        old=self.controller.spreader_size_ft
        if old==size:return
        self.refresh(True);before=box_bounds(self.contact_points())
        self.controller.set_spreader_size(size)
        try:
            after=box_bounds(self.contact_points())
            obstacles=[record['bounds'] for record in self.records.values()]+[bounds for _,bounds in self.truck_shapes+self.static_shapes]
            if any(sweep_box(before,after,bounds) is not None for bounds in obstacles):
                raise ValueError('吊具伸缩路径有障碍，请先升高或移开')
            for deck in self.decks.values():
                inverse=deck['pose'].GetInverse();size=deck['size']
                def local(bounds):
                    return box_bounds([inverse.Transform(Gf.Vec3d(x,y,z)) for x in (bounds[0][0],bounds[1][0]) for y in (bounds[0][1],bounds[1][1]) for z in (bounds[0][2],bounds[1][2])])
                surface=(tuple(-size[i]/2 for i in range(3)),tuple(size[i]/2 for i in range(3)))
                if sweep_box(local(before),local(after),surface) is not None:raise ValueError('吊具伸缩路径有集卡，请先升高或移开')
        except BaseException:
            self.controller.set_spreader_size(old);raise

    def check_truck(self,path):
        if path==TRUCK:
            from truck_driver_runtime import active_driver
            driver=active_driver(self.stage)
            if driver and abs(driver.engine.speed)>.03:raise ValueError('请先让集卡停稳，再进行开锁或闭锁交接')

    def limit_move(self,delta,now):
        if delta.GetLength()<1e-10:return 1.,None
        self.refresh(now=now);points=self.contact_points();bounds=box_bounds(points)
        # A thin plate above the lock feet approximates the spreader bottom.
        shapes=[('吊具',(bounds[0],(bounds[1][0],bounds[1][1],bounds[1][2]+.12)))]
        if self.held:
            prim=self.stage.GetPrimAtPath(self.held)
            bound=cargo_bound(self.stage,prim,UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']))
            if bound.IsEmpty():raise ValueError('闭锁箱子几何不可用，岸桥已停止')
            shapes.append(('箱子',(tuple(bound.GetMin()),tuple(bound.GetMax()))))
        obstacles=[(r['path'],r['bounds']) for r in self.records.values() if r['path']!=self.held]+self.truck_shapes+self.static_shapes
        limit=1.;notice=None
        for label,start in shapes:
            end=tuple(tuple(start[j][i]+delta[i] for i in range(3)) for j in (0,1))
            for path,bound in obstacles:
                hit=sweep_box(start,end,bound)
                if hit is not None and hit<=limit:
                    limit=max(0.,hit);suffix={'cab':'集卡车头','body':'集卡车身','ground':'码头地面','bridge':'船舶建筑'}.get(path.rsplit(':',1)[-1],'集装箱')
                    notice=label+'接触'+suffix+'，移动已停止'
            for path,deck in self.decks.items():
                inverse=deck['pose'].GetInverse();size=deck['size']
                local=box_bounds([inverse.Transform(Gf.Vec3d(x,y,z)) for x in (start[0][0],start[1][0]) for y in (start[0][1],start[1][1]) for z in (start[0][2],start[1][2])])
                shift=inverse.TransformDir(delta)
                local_end=tuple(tuple(local[j][i]+shift[i] for i in range(3)) for j in (0,1))
                surface=(tuple(-size[i]/2 for i in range(3)),tuple(size[i]/2 for i in range(3)))
                hit=sweep_box(local,local_end,surface)
                if hit is not None and hit<=limit:limit=max(0.,hit);notice=label+'接触集卡承载面，移动已停止'
        return limit,notice

    def update(self):
        if not self.held:return
        matrix=world(self.hoist)
        if matrix==self.last_matrix:return
        for path,relative in self.relative.items():
            prim=self.stage.GetPrimAtPath(path)
            if not prim:raise ValueError('闭锁箱子或外观已不存在，岸桥已停止')
            write_world(self.stage,prim,relative*matrix)
        self.last_matrix=matrix
