"""Manual RTG cargo attachment and truck handoff, using measured lock contacts.

Only session-layer kinematics are used. PLC/PhysX/timeline ownership is never
changed. Poses and attachments survive an operator logout at their last pose.
"""
import itertools,math
from pxr import Gf,Sdf,Usd,UsdGeom
from rtg_live_control import HOIST_PATH
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
        if self.held:self.capture(self.held)

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

    def add_truck_decks(self):
        from ship_transfer_plan import PARK
        for path in (TRUCK,PARK):
            truck=self.stage.GetPrimAtPath(path)
            if not truck or UsdGeom.Imageable(truck).ComputeVisibility()=='invisible':continue
            cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']);deck=Gf.Range3d()
            for prim in Usd.PrimRange(truck,Usd.TraverseInstanceProxies()):
                if prim.IsA(UsdGeom.Boundable) and 'trailer_side_rail' in prim.GetName():
                    deck.UnionWith(cache.ComputeRelativeBound(prim,truck).ComputeAlignedRange())
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

    def limit_descent(self,start,target,now):
        """Stop at the first downward contact, allowing upward escape.

        Inventory stays fixed during manual control. Refresh it periodically;
        truck bounds are measured on each step because the driver may move it.
        """
        from rtg_plc_contact import box_bounds,sweep_box
        from rtg_live_control import HOIST_USD_PER_ENGINEERING_M
        from rtg_plc_interaction import corners
        from ship_transfer_plan import PARK
        if now-self.contact_refresh>.3:
            self.refresh_contacts();self.contact_refresh=now
        else:
            self.geometry.decks={};self.add_truck_decks()
        g=self.geometry
        feet=g._foot_points()
        parent=self.stage.GetPrimAtPath(HOIST_PATH).GetParent()
        dz=world(parent).TransformDir(Gf.Vec3d(0,0,(target-start)*HOIST_USD_PER_ENGINEERING_M))[2]
        if dz>=-1e-9:return target,None
        limit=1.;message=None
        # Individual lock feet let a partially aligned spreader stop on a roof.
        if not self.locked:
            for record in g.records.values():
                bounds=g._bounds(record)
                for point in feet:
                    if (bounds[0][0]-.005<=point[0]<=bounds[1][0]+.005 and
                        bounds[0][1]-.005<=point[1]<=bounds[1][1]+.005):
                        gap=point[2]-bounds[1][2]
                        if -.002<=gap<=-dz and max(0.,gap/-dz)<limit:
                            limit=max(0.,gap/-dz);message='吊具已接触箱顶，下降已停止'
        else:
            body=self.stage.GetPrimAtPath(self.held)
            if not body:raise ValueError('闭锁箱子不存在，RTG已停止')
            record=g._read_record(body) if body.IsA(UsdGeom.Cube) else self.visual_record(body)
            if not record:raise ValueError('闭锁箱子几何不可用，RTG停止下降')
            matrix=world(body)
            if body.IsA(UsdGeom.Cube):
                unit=UsdGeom.Cube(body).GetSizeAttr().Get();local=Gf.Range3d(Gf.Vec3d(-unit/2),Gf.Vec3d(unit/2))
            else:local=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeUntransformedBound(body).ComputeAlignedRange()
            lo,hi=local.GetMin(),local.GetMax()
            bounds=box_bounds([matrix.Transform(Gf.Vec3d(x,y,z)) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
            # Use four inset corner castings for trailer support, matching unlock.
            sx=matrix.TransformDir(Gf.Vec3d(1,0,0)).GetLength();sy=matrix.TransformDir(Gf.Vec3d(0,1,0)).GetLength()
            if sx<1e-9 or sy<1e-9:raise ValueError('闭锁箱子比例无效')
            points=[matrix.Transform(Gf.Vec3d(x,y,lo[2])) for x in (lo[0]+.158/sx,hi[0]-.158/sx) for y in (lo[1]+.17/sy,hi[1]-.17/sy)]
            for other in list(g.records.values())+list(g.decks.values()):
                if other['path']==self.held:continue
                inv=other['pose'].GetInverse();sx,sy,sz=other['size']
                for point in points:
                    local=inv.Transform(point);gap=local[2]-sz/2
                    if abs(local[0])<=sx/2+.015 and abs(local[1])<=sy/2+.015 and -.002<=gap<=-dz:
                        fraction=max(0.,gap/-dz)
                        if fraction<limit:
                            limit=fraction;message='箱子已接触集卡承载面，下降已停止' if other['path'] in g.decks else '箱子已接触箱顶，下降已停止'
            end=(tuple(bounds[0][i]+(dz if i==2 else 0) for i in range(3)),tuple(bounds[1][i]+(dz if i==2 else 0) for i in range(3)))
            for path in (TRUCK,PARK):
                truck=self.stage.GetPrimAtPath(path)
                if not truck or UsdGeom.Imageable(truck).ComputeVisibility()=='invisible':continue
                cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
                for prim in Usd.PrimRange(truck,Usd.TraverseInstanceProxies()):
                    if not prim.IsA(UsdGeom.Boundable) or 'white_cab_body' not in prim.GetName():continue
                    b=cache.ComputeWorldBound(prim).ComputeAlignedRange()
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
