"""Manual RTG cargo attachment and truck handoff, using measured lock contacts.

Only session-layer kinematics are used. PLC/PhysX/timeline ownership is never
changed. Poses and attachments survive an operator logout at their last pose.
"""
import itertools,math
from pxr import Gf,Sdf,Tf,Usd,UsdGeom
from rtg_live_control import (HOIST_PATH,TROLLEY_PATH,HOIST_SOFT_LIMITS,HOIST_USD_PER_ENGINEERING_M,
                              trolley_actual_to_usd,trolley_usd_to_actual)
from rtg_plc_contact import sweep_box
from rtg_plc_interaction import PlcInteraction,rigid_pose,ALIGNMENT_TOLERANCE

ROOT='/World/LiveContainers'
POSE_OP='xformOp:transform:rtgDriverCargo'
MANUAL='smartport:driverCargo'
HELD='smartport:rtgDriverHeldCargo'
TRUCK_OWNER='smartport:driverTruckOwner'
TRUCK_REL='smartport:driverTruckRelative'
TRUCK='/World/TruckDriverDemo/Truck'
BODIES='smartport:driverCargoBodies'


def attr(prim,name,default=None):
    value=prim.GetAttribute(name) if prim else None
    return value.Get() if value and value.Get() is not None else default


def body_paths(stage):
    root=stage.GetPrimAtPath(ROOT);rel=root.GetRelationship(BODIES) if root else None
    return [str(p) for p in rel.GetTargets()] if rel else []


def members(stage,path):
    body=stage.GetPrimAtPath(path)
    if not body:return []
    return [body]+[p for p in body.GetParent().GetChildren()
                   if p.GetName().startswith(body.GetName()+'_') and p.IsA(UsdGeom.Xformable)]


def world(prim):return UsdGeom.XformCache().GetLocalToWorldTransform(prim)


def write_world(stage,prim,desired):
    xf=UsdGeom.Xformable(prim);current=world(prim);old=prim.GetAttribute(POSE_OP)
    neutral=old.Get().GetInverse()*current if old and old.Get() is not None else current
    matrix=desired*neutral.GetInverse()
    with Usd.EditContext(stage,stage.GetSessionLayer()):
        if not old:old=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'rtgDriverCargo').GetAttr()
        if old.Get()!=matrix:old.Set(matrix)


def bind_truck(stage,path,truck_path):
    truck=stage.GetPrimAtPath(truck_path) if truck_path else None
    inverse=world(truck).GetInverse() if truck else None
    with Usd.EditContext(stage,stage.GetSessionLayer()):
        for prim in members(stage,path):
            prim.CreateAttribute(TRUCK_OWNER,Sdf.ValueTypeNames.String).Set(truck_path or '')
            if inverse is not None:prim.CreateAttribute(TRUCK_REL,Sdf.ValueTypeNames.Matrix4d).Set(world(prim)*inverse)


def sync_truck_cargo(stage,truck_path,move_to=None):
    """Called by the truck's own update; works even with no RTG login active."""
    target=stage.GetPrimAtPath(move_to or truck_path)
    if not target:return
    matrix=world(target)
    for path in body_paths(stage):
        body=stage.GetPrimAtPath(path)
        if attr(body,TRUCK_OWNER)!=truck_path:continue
        for prim in members(stage,path):
            relative=attr(prim,TRUCK_REL)
            if relative is None:continue
            if move_to:
                with Usd.EditContext(stage,stage.GetSessionLayer()):prim.GetAttribute(TRUCK_OWNER).Set(move_to)
            write_world(stage,prim,relative*matrix)


class DriverCargo:
    def __init__(self,stage,controller):
        self.stage=stage;self.controller=controller;self.geometry=PlcInteraction(stage,controller)
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        for prim in Usd.PrimRange(stage.GetPrimAtPath(HOIST_PATH)):
            if prim.IsA(UsdGeom.Boundable) and 'red_corner_lock_foot_' in str(prim.GetPath()):
                bounds=cache.ComputeUntransformedBound(prim).ComputeAlignedBox();mid=bounds.GetMidpoint()
                self.geometry.feet.append((str(prim.GetPath()),Gf.Vec3d(mid[0],mid[1],bounds.GetMin()[2])))
        if len(self.geometry.feet)!=4:raise ValueError('无法测量吊具四角锁点')
        self.held=attr(stage.GetPrimAtPath(HOIST_PATH),HELD,'') or None
        self.relative={};self.last_matrix=None
        self.contact_refresh=-1e9
        self.contacts_dirty=True
        self.truck_geometry={}
        self.subscription=Tf.Notice.Register(Usd.Notice.ObjectsChanged,self.geometry_changed,stage)
        if self.held:self.capture(self.held)

    def geometry_changed(self,notice,sender):
        from ship_transfer_plan import PARK
        for path in [*notice.GetResyncedPaths(),*notice.GetChangedInfoOnlyPaths()]:
            name=str(path.GetPrimPath())
            if name.startswith(ROOT) and not (self.held and (name==self.held or name.startswith(self.held+'_') or name.startswith(self.held+'/'))):
                self.contacts_dirty=True
            if name in ('/','/World') or name in getattr(self.geometry,'records',{}):
                if name!=self.held:self.contacts_dirty=True
            for root in (TRUCK,PARK):
                if name==root and path.IsPropertyPath() and path.name.startswith(('xformOp','visibility')):continue
                if name==root or name.startswith(root+'/') or root.startswith(name+'/'):
                    self.truck_geometry.clear();return

    def close(self):
        if self.subscription:self.subscription.Revoke();self.subscription=None

    def truck_shapes(self,truck):
        """Cache local geometry only; truck pose/visibility is read each step."""
        path=str(truck.GetPath())
        if path not in self.truck_geometry:
            cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
            deck=Gf.Range3d();cabs=[]
            for prim in Usd.PrimRange(truck,Usd.TraverseInstanceProxies()):
                if not prim.IsA(UsdGeom.Boundable):continue
                if 'trailer_side_rail' in prim.GetName():deck.UnionWith(cache.ComputeRelativeBound(prim,truck).ComputeAlignedRange())
                if 'white_cab_body' in prim.GetName():cabs.append(cache.ComputeRelativeBound(prim,truck))
            self.truck_geometry[path]=(deck,cabs)
        return self.truck_geometry[path]

    @property
    def locked(self):return bool(self.held)

    def capture(self,path):
        parts=members(self.stage,path)
        if not parts:raise ValueError('闭锁箱子已不存在，不能恢复驾驶')
        inverse=world(self.stage.GetPrimAtPath(HOIST_PATH)).GetInverse()
        self.relative={str(p.GetPath()):world(p)*inverse for p in parts}
        self.last_matrix=None

    def visual_record(self,prim):
        """Measure a non-Cube ship box in its own composed frame."""
        size_ft=attr(prim,'ship:sizeFt')
        if size_ft not in (20,40):return None
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        local=cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
        if local.IsEmpty():return None
        matrix=world(prim);pose=Gf.Matrix4d(1)
        for i in range(3):
            axis=matrix.TransformDir(Gf.Vec3d(*(1 if i==j else 0 for j in range(3)))).GetNormalized()
            for j in range(3):pose[i,j]=axis[j]
        centre=matrix.Transform(local.GetMidpoint())
        for i in range(3):pose[3,i]=centre[i]
        dims=tuple(matrix.TransformDir(Gf.Vec3d(*(local.GetSize()[i] if i==j else 0 for j in range(3)))).GetLength() for i in range(3))
        return dict(path=str(prim.GetPath()),size=dims,pose=pose,size_ft=size_ft,body=str(prim.GetPath()))

    def refresh_contacts(self):
        # Traverse only container bodies, pruning imported/decorative subtrees.
        g=self.geometry;g.records={};g.decks={};g.carried=self.held
        root=self.stage.GetPrimAtPath(ROOT)
        if root:
            iterator=iter(Usd.PrimRange(root))
            for prim in iterator:
                name=prim.GetName()
                if name=='Looks' or '_Container_' in name or name.endswith('_ImportedModel'):
                    iterator.PruneChildren();continue
                if not prim.IsA(UsdGeom.Cube) or not prim.HasAttribute('live:row'):continue
                if UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':
                    model=self.stage.GetPrimAtPath(str(prim.GetPath())+'_ImportedModel')
                    if not model or UsdGeom.Imageable(model).ComputeVisibility()=='invisible':continue
                record=g._read_record(prim);record['body']=record['path']
                # Keep pitch/roll as well as yaw when evaluating landed corners.
                matrix=world(prim);pose=Gf.Matrix4d(1)
                for i in range(3):
                    axis=matrix.TransformDir(Gf.Vec3d(*(1 if i==j else 0 for j in range(3)))).GetNormalized()
                    for j in range(3):pose[i,j]=axis[j]
                centre=matrix.ExtractTranslation()
                for i in range(3):pose[3,i]=centre[i]
                record['pose']=pose;g.records[record['path']]=record
        # STS can place a ship-structure Xform on the same driven truck. Its
        # visible dimensions, orientation and size remain authoritative.
        for path in body_paths(self.stage):
            if path in g.records:continue
            prim=self.stage.GetPrimAtPath(path)
            if not prim or not prim.IsA(UsdGeom.Xformable):continue
            if UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
            record=self.visual_record(prim)
            if record:g.records[path]=record
        self.add_truck_decks()
        for record in g.records.values():
            record['bounds']=g._bounds(record)
        self.truck_records=[record for record in g.records.values() if attr(self.stage.GetPrimAtPath(record['path']),TRUCK_OWNER)]
        self.contacts_dirty=False

    def add_truck_decks(self):
        from ship_transfer_plan import PARK
        for path in (TRUCK,PARK):
            truck=self.stage.GetPrimAtPath(path)
            if not truck or UsdGeom.Imageable(truck).ComputeVisibility()=='invisible':continue
            deck,_=self.truck_shapes(truck)
            if deck.IsEmpty():continue
            matrix=world(truck);mid=deck.GetMidpoint();mid[2]=deck.GetMax()[2]
            centre=matrix.Transform(mid)-Gf.Vec3d(0,0,.075)
            size=tuple(matrix.TransformDir(Gf.Vec3d(*(deck.GetSize()[i] if j==i else 0 for j in range(3)))).GetLength() for i in range(2))+(.15,)
            self.geometry.decks[path]=dict(path=path,size=size,pose=rigid_pose(matrix,centre),body=path,truck=path)

    def check_truck_stopped(self,truck_path):
        if truck_path!=TRUCK:return
        from truck_driver_runtime import active_driver
        driver=active_driver(self.stage)
        if driver and abs(driver.engine.speed)>.03:raise ValueError('请先让集卡停稳，再进行开锁或闭锁交接')

    def support_error(self,record):
        """Explain the closest truck alignment without weakening the unlock interlock."""
        from rtg_plc_interaction import corners
        g=self.geometry
        points=[record['pose'].Transform(p) for p in corners(record['size'],-record['size'][2]/2)]
        if not g.decks:return '开锁失败：箱子尚未落稳，四个底部承重点均需有支撑'
        centre=sum(points,Gf.Vec3d(0))/4
        deck=min(g.decks.values(),key=lambda d:(d['pose'].ExtractTranslation()-centre).GetLength())
        local=[deck['pose'].GetInverse().Transform(point) for point in points]
        sx,sy,sz=deck['size']
        if any(abs(sum(p[i] for p in local)/4)>deck['size'][i]/2+3 for i in (0,1)):
            return '开锁失败：箱子尚未落稳，四个底部承重点均需有支撑'
        count=sum(abs(p[0])<=sx/2+.015 and abs(p[1])<=sy/2+.015 and -.002<=p[2]-sz/2<=.045 for p in local)
        length=0 if sx>=sy else 1;width=1-length
        over=lambda i:max(0.,max(abs(p[i])-deck['size'][i]/2-.015 for p in local))
        gap=max(p[2]-sz/2 for p in local)
        details=[]
        if over(length)>.001:
            side=''
            truck=self.stage.GetPrimAtPath(deck['truck'])
            if truck:
                cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
                cab=next((p for p in Usd.PrimRange(truck,Usd.TraverseInstanceProxies())
                          if p.IsA(UsdGeom.Boundable) and 'white_cab_body' in p.GetName()),None)
                if cab:
                    cab_centre=cache.ComputeWorldBound(cab).ComputeAlignedRange().GetMidpoint()
                    cab_sign=deck['pose'].GetInverse().Transform(cab_centre)[length]
                    box_sign=sum(p[length] for p in local)/4
                    side='向车头' if box_sign*cab_sign>0 else '向车尾'
            details.append('沿车身%s偏出约%.1f厘米'%(side,over(length)*100))
        if over(width)>.001:details.append('横向偏出约%.1f厘米'%(over(width)*100))
        if gap>.045:details.append('最高承重点距车面约%.1f厘米'%(gap*100))
        return '开锁失败：集卡承重点 %d/4；%s'%(count,'，'.join(details) or '请检查箱子姿态及承载面')

    def refresh_motion_contacts(self,now):
        if self.contacts_dirty:
            self.refresh_contacts();self.contact_refresh=now
        # Truck cargo can move independently of the static yard inventory.
        for record in self.truck_records:
            prim=self.stage.GetPrimAtPath(record['path'])
            if attr(prim,TRUCK_OWNER):
                current=self.geometry._read_record(prim) if prim.IsA(UsdGeom.Cube) else self.visual_record(prim)
                if current:
                    record.update(current)
                    record['bounds']=self.geometry._bounds(record)

    def held_box(self):
        body=self.stage.GetPrimAtPath(self.held)
        if not body:raise ValueError('闭锁箱子不存在，RTG已停止')
        if body.IsA(UsdGeom.Cube):
            half=UsdGeom.Cube(body).GetSizeAttr().Get()/2
            local=Gf.Range3d(Gf.Vec3d(-half),Gf.Vec3d(half))
        else:
            cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
            local=cache.ComputeUntransformedBound(body).ComputeAlignedRange()
        if local.IsEmpty():raise ValueError('闭锁箱子几何不可用，RTG已停止')
        # Read the proposed hoist pose even if the visible box has not yet been updated.
        pose=self.relative[self.held]*world(self.stage.GetPrimAtPath(HOIST_PATH))
        return local,pose

    @staticmethod
    def bounds_for(local,pose):
        box=Gf.BBox3d(local,pose).ComputeAlignedRange()
        return tuple(box.GetMin()),tuple(box.GetMax())

    def limit_container_sweep(self,before,after,now,*,lifting=False):
        self.refresh_motion_contacts(now)
        fraction=1.
        for path,record in self.geometry.records.items():
            if path==self.held:continue
            if 'bounds' not in record:record['bounds']=self.geometry._bounds(record)
            bounds=record['bounds']
            hit=sweep_box(before,after,bounds)
            if hit is None:continue
            # Let the operator lift out of a box side that was already intersected.
            if (lifting and hit==0 and before[0][2]<bounds[1][2]-1e-7
                    and before[0][2]+before[1][2]>=bounds[0][2]+bounds[1][2]):
                continue
            fraction=min(fraction,hit)
        return fraction

    def limit_trolley(self,start,target,now):
        start_usd,target_usd=trolley_actual_to_usd(start),trolley_actual_to_usd(target)
        if abs(target_usd-start_usd)<1e-9:return start,None
        parent=self.stage.GetPrimAtPath(TROLLEY_PATH).GetParent()
        delta=world(parent).TransformDir(Gf.Vec3d(target_usd-start_usd,0,0))
        local,pose=self.held_box()
        before=self.bounds_for(local,pose)
        after=tuple(tuple(edge[i]+delta[i] for i in range(3)) for edge in before)
        fraction=self.limit_container_sweep(before,after,now)
        if fraction==1:return target,None
        return trolley_usd_to_actual(start_usd+(target_usd-start_usd)*fraction),'载箱接触旁边集装箱，小车已停止，请先起升'

    def limit_hoist(self,start,target,now):
        """Limit vertical travel by box volume, with corner supports for the trailer."""
        from ship_transfer_plan import PARK
        target=max(HOIST_SOFT_LIMITS[0],min(HOIST_SOFT_LIMITS[1],target))
        parent=self.stage.GetPrimAtPath(HOIST_PATH).GetParent()
        dz=world(parent).TransformDir(Gf.Vec3d(0,0,(target-start)*HOIST_USD_PER_ENGINEERING_M))[2]
        if abs(dz)<1e-9 or (dz>0 and not self.locked):return target,None
        g=self.geometry
        limit=1.;message=None
        # Individual lock feet let a partially aligned spreader stop on a roof.
        if not self.locked:
            self.refresh_motion_contacts(now)
            feet=g._foot_points()
            for record in g.records.values():
                bounds=record['bounds']
                for point in feet:
                    if (bounds[0][0]-.005<=point[0]<=bounds[1][0]+.005 and
                        bounds[0][1]-.005<=point[1]<=bounds[1][1]+.005):
                        gap=point[2]-bounds[1][2]
                        if -.002<=gap<=-dz and max(0.,gap/-dz)<limit:
                            limit=max(0.,gap/-dz);message='吊具已接触箱顶，下降已停止'
        else:
            local,matrix=self.held_box()
            bounds=self.bounds_for(local,matrix)
            end=tuple((edge[0],edge[1],edge[2]+dz) for edge in bounds)
            limit=self.limit_container_sweep(bounds,end,now,lifting=dz>0)
            if dz>0:
                return start+(target-start)*limit,'载箱上方有集装箱，起升已停止' if limit<1 else None
            if limit<1:message='箱子已接触集装箱，下降已停止'
            g.decks={};self.add_truck_decks()
            lo,hi=local.GetMin(),local.GetMax()
            # Use four inset corner castings for trailer support, matching unlock.
            sx=matrix.TransformDir(Gf.Vec3d(1,0,0)).GetLength();sy=matrix.TransformDir(Gf.Vec3d(0,1,0)).GetLength()
            if sx<1e-9 or sy<1e-9:raise ValueError('闭锁箱子比例无效')
            points=[matrix.Transform(Gf.Vec3d(x,y,lo[2])) for x in (lo[0]+.158/sx,hi[0]-.158/sx) for y in (lo[1]+.17/sy,hi[1]-.17/sy)]
            for other in g.decks.values():
                inv=other['pose'].GetInverse();sx,sy,sz=other['size']
                for point in points:
                    local=inv.Transform(point);gap=local[2]-sz/2
                    if abs(local[0])<=sx/2+.015 and abs(local[1])<=sy/2+.015 and -.002<=gap<=-dz:
                        fraction=max(0.,gap/-dz)
                        if fraction<limit:
                            limit=fraction;message='箱子已接触集卡承载面，下降已停止'
            for path in (TRUCK,PARK):
                truck=self.stage.GetPrimAtPath(path)
                if not truck or UsdGeom.Imageable(truck).ComputeVisibility()=='invisible':continue
                _,cabs=self.truck_shapes(truck);truck_matrix=world(truck)
                for local in cabs:
                    b=Gf.BBox3d(local.GetRange(),local.GetMatrix()*truck_matrix).ComputeAlignedRange()
                    hit=sweep_box(bounds,end,(tuple(b.GetMin()),tuple(b.GetMax())))
                    if hit is not None and hit<limit:limit=hit;message='箱子碰到集卡车头，下降已停止'
        return start+(target-start)*limit,message

    def set_lock(self,locked):
        if locked==self.locked:return
        self.update();self.refresh_contacts();g=self.geometry
        if locked:
            contact=g.contact()
            if not contact or not all(contact['feet']):raise ValueError('闭锁失败：四个锁脚须落在同一只无遮挡箱子的箱顶')
            if not contact['size_matches']:raise ValueError('闭锁失败：吊具20/40尺型与箱子不匹配')
            path=contact['container'];body=self.stage.GetPrimAtPath(path)
            error=contact['alignment_error_m']
            if error>ALIGNMENT_TOLERANCE and body and not body.IsA(UsdGeom.Cube):
                # Ship structure visual boxes are slightly larger than the
                # yard-native boxes. Judge centred lock geometry rather than
                # interpreting that decorative margin as a positioning error.
                inverse=g.records[path]['pose'].GetInverse()
                local=[inverse.Transform(p) for p in g._foot_points()]
                hx=(max(p[0] for p in local)-min(p[0] for p in local))/2
                hy=(max(p[1] for p in local)-min(p[1] for p in local))/2
                targets=[Gf.Vec3d(x*hx,y*hy,0) for x in (-1,1) for y in (-1,1)]
                error=min(max(math.hypot(p[0]-q[0],p[1]-q[1]) for p,q in zip(local,order)) for order in itertools.permutations(targets))
            if error>ALIGNMENT_TOLERANCE:raise ValueError('闭锁失败：锁点未对齐，请微调大车、小车或旋转角度')
            if attr(self.stage.GetPrimAtPath('/World/STSControls/STS_01/Gantry/Trolley/Hoist'),'smartport:stsDriverHeldCargo','')==path:
                raise ValueError('该箱子仍由STS闭锁，请先落稳并开锁交接')
            self.check_truck_stopped(attr(body,TRUCK_OWNER))
            self.capture(path)
            with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):
                root=self.stage.GetPrimAtPath(ROOT);rel=root.CreateRelationship(BODIES)
                if Sdf.Path(path) not in rel.GetTargets():rel.AddTarget(path)
                for prim in members(self.stage,path):prim.CreateAttribute(MANUAL,Sdf.ValueTypeNames.Bool).Set(True)
                self.stage.GetPrimAtPath(HOIST_PATH).CreateAttribute(HELD,Sdf.ValueTypeNames.String).Set(path)
            bind_truck(self.stage,path,None);self.held=path
        else:
            record=g.records.get(self.held)
            # The modeled trailer is narrower than the box's outer walls;
            # support its four inset corner castings, matching the lock points.
            landing=dict(record,size=(record['size'][0]-.316,record['size'][1]-.34,record['size'][2])) if record else None
            supports=g.support(landing) if landing else None
            if not supports:raise ValueError(self.support_error(landing) if landing else '开锁失败：闭锁箱子不存在')
            owners={g.decks[p]['truck'] if p in g.decks else (None if p=='ground' else attr(self.stage.GetPrimAtPath(p),TRUCK_OWNER)) for p in supports}
            truck_path=next(iter(owners)) if len(owners)==1 and None not in owners and '' not in owners and 'ground' not in owners else None
            self.check_truck_stopped(truck_path)
            bind_truck(self.stage,self.held,truck_path)
            with Usd.EditContext(self.stage,self.stage.GetSessionLayer()):self.stage.GetPrimAtPath(HOIST_PATH).GetAttribute(HELD).Set('')
            self.held=None;self.relative={}

    def update(self):
        if not self.held:return
        matrix=world(self.stage.GetPrimAtPath(HOIST_PATH))
        if matrix==self.last_matrix:return
        for path,relative in self.relative.items():
            prim=self.stage.GetPrimAtPath(path)
            if not prim:raise ValueError('闭锁箱体或外观已被重建，RTG停止操作')
            write_world(self.stage,prim,relative*matrix)
        self.last_matrix=matrix
