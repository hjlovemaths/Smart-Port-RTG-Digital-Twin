"""Event-driven, Z-up cinematography for the reversible ship-to-yard task.

This owns one anonymous camera layer. It never advances equipment or the task.
"""
import math
from pxr import Gf,Sdf,Usd,UsdGeom
from ship_transfer_plan import box,YARD
from rtg_live_control import GANTRY_PATH,HOIST_PATH
from large_vessel_layout import SHIP

CAMERA_PATH='/World/SmartPortTransferCinemaCamera'
TITLES=('任务全景','集卡出场','船箱定位','岸桥取箱','船到集卡','集卡接箱','运输途中','RTG接箱','堆场落箱','完成全景')
REVERSE_TITLES=('装船任务全景','集卡前往堆场','堆场源箱定位','RTG取箱','RTG移箱','集卡接箱','运往岸桥','岸桥接箱','指定箱位装船','装船完成全景')
QUAY_TITLES=('两箱运输任务全景',*REVERSE_TITLES[1:7],'两箱到达岸桥，等待交接')
RTG_SHOTS={'yard_locate','yard_pick','rtg_cross','rtg_handoff','rtg_pick','yard_place'}


def segment_hits(eye,target,bounds):
    lo,hi=.02,.86
    direction=target-eye
    for axis in range(3):
        if abs(direction[axis])<1e-9:
            if not bounds.GetMin()[axis]<=eye[axis]<=bounds.GetMax()[axis]:return False
        else:
            a=(bounds.GetMin()[axis]-eye[axis])/direction[axis]
            b=(bounds.GetMax()[axis]-eye[axis])/direction[axis]
            lo=max(lo,min(a,b));hi=min(hi,max(a,b))
            if lo>hi:return False
    return True


class TransferCinema:
    def __init__(self,stage,viewport,task):
        if task.closed:raise ValueError('搬运任务已复位，无法开启电影视角')
        self.stage=stage;self.viewport=viewport;self.task=task
        self.reverse=task.plan.get('direction')=='yard_to_ship'
        self.cargo_path=str(task.cargo.GetPath()) if self.reverse else task.plan['source_path']
        self.previous_camera=str(viewport.camera_path);self.layer=Sdf.Layer.CreateAnonymous('transfer_cinema.usda')
        self.closed=False;self.paused=False;self.reason='';self.elapsed=0.;self.finish_elapsed=0.
        self.shot=None;self.shot_index=0;self.transition=0.;self.eye=self.center=None
        self.from_eye=self.from_center=None;self.history=[];self.occluded=False;self.obstacles=[]
        self.causeway_seen=False;self.causeway_elapsed=0.
        self.obstacle_elapsed=0.
        self.rtg_work=None;self.camera_group=None;self.camera_history=[];self.transition_duration=1.2;self.rtg_transition=False
        stage.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        try:
            with Usd.EditContext(stage,self.layer):
                camera=UsdGeom.Camera.Define(stage,CAMERA_PATH);camera.MakeInvisible()
                camera.CreateHorizontalApertureAttr(36);camera.CreateVerticalApertureAttr(20.25)
                camera.CreateFocalLengthAttr(36/(2*math.tan(math.radians(55)/2)))
                camera.CreateClippingRangeAttr(Gf.Vec2f(.1,10000))
                self.op=UsdGeom.Xformable(camera).AddTransformOp()
                prim=camera.GetPrim()
                self.nav_target=prim.CreateAttribute('smartport:navigationTarget',Sdf.ValueTypeNames.String,custom=True)
                self.nav_center=prim.CreateAttribute('smartport:navigationCenter',Sdf.ValueTypeNames.Vector3d,custom=True)
                self.nav_radius=prim.CreateAttribute('smartport:navigationRadius',Sdf.ValueTypeNames.Double,custom=True)
            self.tick(0,initial=True)
        except Exception:self.close();raise

    def selection(self,ignore_intro=False):
        p=self.task.phase
        if self.task.finished or getattr(self.task,'delivery_done',False):return 'complete',9
        if self.elapsed<3 and not ignore_intro:return 'overview',0
        if self.reverse:
            if p.startswith('RTG吊具切换') or p=='停车场集卡前往堆场':return 'truck_out',1
            if p in ('RTG移向下一只源箱','集卡前往下一装箱贝位','RTG下降到源箱'):return 'yard_locate',2
            if p in ('RTG源箱对位闭锁','RTG起升堆场箱'):return 'yard_pick',3
            if p=='RTG移箱到集卡':return 'rtg_cross',4
            if p in ('RTG落箱到集卡','RTG开锁交接','RTG抬离集卡'):return 'rtg_handoff',5
            if p=='集卡经引桥运往岸桥':return self.transport_shot(),6
            if p in ('岸桥移向下一只集卡箱','岸桥下降到集卡','岸桥集卡对位闭锁','岸桥起升集卡箱'):return 'sts_pick',7
            return 'ship_place',8
        if p.startswith('RTG吊具切换') or p=='停车场集卡出车':return 'truck_out',1
        if p=='岸桥吊具下降到船箱':return 'locate',2
        if p in ('岸桥对位闭锁','岸桥起升船箱'):return 'ship_pick',3
        if p=='岸桥小车移向集卡':return 'sts_cross',4
        if p in ('岸桥落箱到集卡','岸桥开锁交接','岸桥吊具抬离集卡'):return 'truck_handoff',5
        if p=='集卡经引桥运往堆场':
            return self.transport_shot(),6
        if p in ('RTG移向下一只箱','RTG吊具下降到集卡','RTG对位闭锁','RTG起升'):return 'rtg_pick',7
        return 'yard_place',8

    def transport_shot(self):
        truck=self.task.truck_point;entry=self.task.plan.get('truck_crossroad_y',self.task.plan['truck_route_loaded'][-2][1])
        if not self.causeway_seen and -215<truck[0]<-16 and abs(truck[1]-entry)<8:return 'causeway'
        return 'truck_loaded'

    def add_contact(self,bounds,point):
        half=Gf.Vec3d(1.,2.8 if self.task.plan['size_ft']==40 else 1.5,1.6)
        bounds.UnionWith(Gf.Range3d(point-half,point+half))

    def framing(self,key):
        if key in RTG_SHOTS or key=='complete' and not self.reverse and self.rtg_work is not None:
            if self.rtg_work is None:
                from transfer_rtg_cinema import RTGWorkShot
                self.rtg_work=RTGWorkShot(self.stage,self.task)
            return self.rtg_work.framing()
        t=self.task;cargo=box(self.stage,getattr(t,'cinema_cargo_path',self.cargo_path));truck=box(self.stage,t.truck.GetPath())
        bounds=Gf.Range3d();direction=Gf.Vec3d(1,-.7,.6);margin=1.12
        if key=='overview':
            for point in (t.cargo_origin,Gf.Vec3d(t.plan['target_center_m']),t.truck_point):bounds.UnionWith(point)
            direction=Gf.Vec3d(-.8,-.6,.85);margin=1.08
        elif key in ('truck_out','truck_loaded') or key=='complete' and t.plan.get('delivery_mode')=='quay':
            bounds.UnionWith(truck)
            if key=='complete':
                for prim,_,_,_ in t.cargo_records:bounds.UnionWith(box(self.stage,prim.GetPath()))
            margin=1.3
            heading=math.radians(t.truck_heading)
            forward=Gf.Vec3d(-math.cos(heading),-math.sin(heading),0)
            side=Gf.Vec3d(-forward[1],forward[0],0)
            direction=-forward*.9+side*.65+Gf.Vec3d(0,0,.7)
        elif key=='causeway':
            entry=t.plan.get('truck_crossroad_y',t.plan['truck_route_loaded'][-2][1])
            bounds.UnionWith(Gf.Vec3d(-231,entry,2));bounds.UnionWith(Gf.Vec3d(-3.75,entry,4))
            direction=Gf.Vec3d(-.35,-.7,.9)
        elif key in ('locate','ship_pick'):
            bounds.UnionWith(cargo);self.add_contact(bounds,t.sts_contact())
            direction=Gf.Vec3d(1,-.7,.6)
        elif key=='sts_cross':
            bounds.UnionWith(cargo);bounds.UnionWith(truck);self.add_contact(bounds,t.sts_contact())
            direction=Gf.Vec3d(.8,-.8,.65)
        elif key in ('truck_handoff','sts_pick'):
            bounds.UnionWith(truck);bounds.UnionWith(cargo)
            contact=t.sts_contact();contact[2]=min(contact[2],truck.GetMax()[2]+5)
            self.add_contact(bounds,contact);direction=Gf.Vec3d(-.9,-.7,.65)
            if self.reverse:direction=Gf.Vec3d(1,-.6,.3)
        elif key in ('rtg_pick','rtg_handoff','rtg_cross'):
            bounds.UnionWith(truck);bounds.UnionWith(cargo)
            contact=t.rtg.contact()
            if self.reverse and key=='rtg_handoff':contact[2]=min(contact[2],truck.GetMax()[2]+3)
            self.add_contact(bounds,contact)
            direction=Gf.Vec3d(-1,-.75,.7)
            if self.reverse:direction=Gf.Vec3d(-1,-.65,.22)
        elif key in ('yard_locate','yard_pick'):
            bounds.UnionWith(cargo);self.add_contact(bounds,t.rtg.contact())
            direction=Gf.Vec3d(0,-1,.18)
        elif key=='ship_place' or key=='complete' and self.reverse:
            bounds.UnionWith(cargo);bounds.UnionWith(Gf.Vec3d(t.plan['target_center_m']))
            contact=t.sts_contact()
            if key=='complete':contact[2]=min(contact[2],cargo.GetMax()[2]+2.5)
            self.add_contact(bounds,contact);direction=Gf.Vec3d(-1,-.7,.5)
            if key=='complete':
                for support in t.plan['target_supports']:
                    if support['path']!='deck':bounds.UnionWith(box(self.stage,support['path']))
                margin=1.6
        else:
            bounds.UnionWith(cargo);bounds.UnionWith(Gf.Vec3d(getattr(t,'cinema_target_center',t.plan['target_center_m'])))
            self.add_contact(bounds,t.rtg.contact());direction=Gf.Vec3d(1,-.7,.65)
            if key=='complete':
                bounds.UnionWith(box(self.stage,getattr(t,'cinema_support_path',t.plan['support_path'])));margin=1.6
        center=bounds.GetMidpoint();center[2]=max(.4,center[2])
        radius=max(2.3,bounds.GetSize().GetLength()/2)
        vertical_half=math.atan(math.tan(math.radians(55)/2)/(16/9))
        distance=max(10,min(600,radius/math.sin(vertical_half)*margin))
        return center,direction.GetNormalized(),distance

    def collect_obstacles(self,center):
        if self.reverse or self.camera_group=='rtg_work':return self.loading_obstacles(center)
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        result=[]
        roots=(self.task.c.path+'/Gantry',GANTRY_PATH)
        exclude=(self.task.c.path+'/Gantry/Trolley/Hoist',HOIST_PATH)
        for path in roots:
            root=self.stage.GetPrimAtPath(path)
            if not root:continue
            for prim in Usd.PrimRange(root,Usd.TraverseInstanceProxies()):
                if any(str(prim.GetPath()).startswith(p) for p in exclude):continue
                if not prim.IsA(UsdGeom.Boundable) or UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
                b=cache.ComputeWorldBound(prim).ComputeAlignedRange()
                if not b.IsEmpty() and .5<b.GetSize().GetLength()<150 and (b.GetMidpoint()-center).GetLength()<85:result.append(b)
        transforms=UsdGeom.XformCache()
        for group in self.stage.GetPrimAtPath(SHIP+'/StructureSlots').GetChildren():
            for prim in group.GetChildren():
                if str(prim.GetPath()) in [self.task.plan['source_path'],*self.task.plan.get('source_paths',[])] or prim.GetAttribute('ship:zone').Get()!='D':continue
                if self.reverse and (str(prim.GetPath()) in self.task.plan['target_placeholder_paths'] or UsdGeom.Imageable(prim).ComputeVisibility()=='invisible'):continue
                point=transforms.GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(0))
                if (point-center).GetLength()>22:continue
                size=prim.GetAttribute('ship:displayDimensions').Get()
                if size:result.append(Gf.Range3d(point-Gf.Vec3d(size)/2,point+Gf.Vec3d(size)/2))
        support=self.stage.GetPrimAtPath(self.task.plan['source_path'] if self.reverse else self.task.plan['support_path'])
        if support:
            for prim in support.GetParent().GetChildren():
                if prim.HasAttribute('live:containerSizeFt') and (not self.reverse or str(prim.GetPath())!=self.task.plan['source_path']):result.append(cache.ComputeWorldBound(prim).ComputeAlignedRange())
        return sorted(result,key=lambda b:(b.GetMidpoint()-center).GetLength())[:240]

    def loading_obstacles(self,center):
        # Cache mesh extents once. Only world transforms change during loading;
        # repeatedly rebuilding all crane bounds causes visible camera hitches.
        if not hasattr(self,'loading_geometry'):
            self.loading_geometry=[];self.loading_static=[]
            bounds=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
            transforms=UsdGeom.XformCache()
            for path,exclude in ((self.task.c.path+'/Gantry',self.task.c.path+'/Gantry/Trolley/Hoist'),(GANTRY_PATH,HOIST_PATH)):
                root=self.stage.GetPrimAtPath(path)
                if not root:continue
                for prim in Usd.PrimRange(root,Usd.TraverseInstanceProxies()):
                    if str(prim.GetPath()).startswith(exclude) or not prim.IsA(UsdGeom.Boundable) or UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
                    local=bounds.ComputeUntransformedBound(prim).ComputeAlignedRange()
                    if not local.IsEmpty():self.loading_geometry.append((prim,local))
            for group in self.stage.GetPrimAtPath(SHIP+'/StructureSlots').GetChildren():
                for prim in group.GetChildren():
                    if prim.GetAttribute('ship:zone').Get()!='D' or UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
                    size=prim.GetAttribute('ship:displayDimensions').Get()
                    if size:
                        point=transforms.GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(0))
                        self.loading_static.append(Gf.Range3d(point-Gf.Vec3d(size)/2,point+Gf.Vec3d(size)/2))
            source=self.stage.GetPrimAtPath(self.task.plan['source_path'])
            # Adjacent bays can be taller than the selected source column.
            # Include them too, otherwise a low source shot sees their backs.
            if self.reverse:positions=[p['source_pick_m'][1] for p in self.task.plan.get('containers',[self.task.plan])]
            else:positions=[self.task.yard_stop[1]]+[p['target_center_m'][1] for p in self.task.plan.get('containers',[self.task.plan])]
            for group in self.stage.GetPrimAtPath(YARD).GetChildren():
                group_bounds=bounds.ComputeWorldBound(group).ComputeAlignedRange()
                if group_bounds.IsEmpty() or group_bounds.GetMin()[1]>max(positions)+25 or group_bounds.GetMax()[1]<min(positions)-25:continue
                for prim in group.GetChildren():
                    if prim!=source and str(prim.GetPath()) not in self.task.plan.get('source_paths',[]) and prim.HasAttribute('live:containerSizeFt') and UsdGeom.Imageable(prim).ComputeVisibility()!='invisible':
                        self.loading_static.append(bounds.ComputeWorldBound(prim).ComputeAlignedRange())
        transforms=UsdGeom.XformCache();result=[]
        for prim,local in self.loading_geometry:
            b=Gf.BBox3d(local,transforms.GetLocalToWorldTransform(prim)).ComputeAlignedRange()
            if .5<b.GetSize().GetLength()<150 and (b.GetMidpoint()-center).GetLength()<85:result.append(b)
        result.extend(b for b in self.loading_static if (b.GetMidpoint()-center).GetLength()<25)
        return sorted(result,key=lambda b:(b.GetMidpoint()-center).GetLength())[:240]

    def safe_eye(self,center,direction,distance):
        choices=[]
        angles=(0,-35,35,-70,70);lifts=(0,5,12)
        # For loading, first try all low angles under the trolley platform.
        # Raising directly over a clear target ray can hide the grip behind it.
        pairs=[(a,l) for l in lifts for a in angles] if self.reverse else [(a,l) for a in angles for l in lifts]
        for angle,lift in pairs:
            rotated=Gf.Rotation(Gf.Vec3d(0,0,1),angle).TransformDir(direction)
            eye=center+rotated*distance+Gf.Vec3d(0,0,lift);eye[2]=max(2.5,eye[2])
            hits=sum(segment_hits(eye,center,b) for b in self.obstacles)
            choices.append((hits,abs(angle)+lift,eye))
            if not hits:return eye,False
        hits,_,eye=min(choices,key=lambda item:(item[0],item[1]))
        return eye,bool(hits)

    def tick(self,dt,initial=False):
        if self.closed:return
        if self.task.closed:self.close();return
        if not initial and not self.paused and str(self.viewport.camera_path)!=CAMERA_PATH:
            self.pause('manual');return
        if self.paused or self.task.paused:return
        dt=max(0,min(float(dt),.1));self.elapsed+=dt
        key,index=self.selection();goal_center,direction,distance=self.framing(key)
        group='rtg_work' if key in RTG_SHOTS or key=='complete' and not self.reverse and self.rtg_work is not None else key
        self.obstacle_elapsed+=dt
        if key=='causeway':
            self.causeway_elapsed+=dt
            if self.causeway_elapsed>=3:self.causeway_seen=True
        if key!=self.shot:
            self.shot=key;self.shot_index=index;self.history.append(key)
            with Usd.EditContext(self.stage,self.layer):
                self.nav_target.Set('' if key in ('overview','causeway') else str(self.task.truck.GetPath()) if key in ('truck_out','truck_loaded') else getattr(self.task,'cinema_cargo_path',self.cargo_path))
        if group!=self.camera_group:
            self.from_eye=Gf.Vec3d(self.eye) if self.eye is not None else None
            self.from_center=Gf.Vec3d(self.center) if self.center is not None else None
            self.rtg_transition=group=='rtg_work' or self.camera_group=='rtg_work'
            self.transition=0.;self.camera_group=group;self.camera_history.append(group)
            self.transition_duration=2.4 if self.rtg_transition else 1.2
            self.obstacles=[] if key in ('overview','causeway') else self.collect_obstacles(goal_center)
            self.obstacle_elapsed=0.
            if group=='rtg_work':self.rtg_work.choose(self.obstacles,segment_hits)
        elif self.reverse and key in ('sts_pick','ship_place') and self.obstacle_elapsed>=.5:
            self.obstacles=self.collect_obstacles(goal_center);self.obstacle_elapsed=0.
        if group=='rtg_work':
            goal_center,direction,distance=self.rtg_work.framing();goal_eye=Gf.Vec3d(self.rtg_work.eye);self.occluded=self.rtg_work.occluded
        else:goal_eye,self.occluded=self.safe_eye(goal_center,direction,distance)
        if self.eye is None:
            self.eye=goal_eye;self.center=goal_center
        elif self.transition<self.transition_duration and self.from_eye is not None:
            self.transition+=dt;fraction=min(1,self.transition/self.transition_duration);smooth=fraction*fraction*(3-2*fraction)
            self.center=self.from_center*(1-smooth)+goal_center*smooth
            if self.rtg_transition:
                # Orbit around the working area instead of flying through the
                # crane or adding the old high aerial arc between close shots.
                a=self.from_eye-self.from_center;b=goal_eye-goal_center
                yaw=math.atan2(a[1],a[0]);turn=(math.atan2(b[1],b[0])-yaw+math.pi)%(2*math.pi)-math.pi
                radius=math.hypot(a[0],a[1])*(1-smooth)+math.hypot(b[0],b[1])*smooth
                angle=yaw+turn*smooth
                self.eye=self.center+Gf.Vec3d(radius*math.cos(angle),radius*math.sin(angle),a[2]*(1-smooth)+b[2]*smooth)
            else:
                self.eye=self.from_eye*(1-smooth)+goal_eye*smooth
                self.eye[2]+=min(80,(goal_eye-self.from_eye).GetLength()*.18)*math.sin(math.pi*fraction)
        else:
            alpha=1-math.exp(-4*dt)
            self.eye=self.eye*(1-alpha)+goal_eye*alpha;self.center=self.center*(1-alpha)+goal_center*alpha
        self.eye[2]=max(2.5,self.eye[2])
        if (self.eye-self.center).GetLength()<1:self.eye[2]=self.center[2]+5
        with Usd.EditContext(self.stage,self.layer):
            self.op.Set(Gf.Matrix4d(1).SetLookAt(self.eye,self.center,Gf.Vec3d(0,0,1)).GetInverse())
            self.nav_center.Set(self.center)
            self.nav_radius.Set(max(2,distance/3.8))
        self.viewport.camera_path=CAMERA_PATH
        if self.task.finished:
            self.finish_elapsed+=dt
            if self.finish_elapsed>=3:self.close('completed')

    def pause(self,reason='paused'):
        if not self.closed:self.paused=True;self.reason=reason

    def resume(self):
        if self.closed:raise ValueError('电影视角已结束，请重新开启')
        if str(self.viewport.camera_path)!=CAMERA_PATH:self.previous_camera=str(self.viewport.camera_path)
        self.paused=False;self.reason='';self.elapsed=max(self.elapsed,3.)
        self.shot=None;self.camera_group=None;self.eye=self.center=None;self.viewport.camera_path=CAMERA_PATH

    def state(self):
        key,index=self.selection(ignore_intro=True) if self.paused else (self.shot,self.shot_index)
        titles=REVERSE_TITLES if self.reverse else TITLES
        if self.task.plan.get('delivery_mode')=='quay':titles=QUAY_TITLES;index=min(index,len(titles)-1)
        return {'status':('completed' if self.reason=='completed' else 'stopped') if self.closed else 'paused' if self.paused else 'task_paused' if self.task.paused else 'running',
                'visible':not self.closed,'title':titles[index],'direction':'yard_to_ship' if self.reverse else 'ship_to_yard',
                'detail':'集卡通过引桥' if key=='causeway' else getattr(self.task,'cinema_detail',self.task.phase),
                'step':index+1,'total':len(titles),'reason':self.reason,'task_finished':self.task.finished,
                'source':self.task.plan['source_location'],
                'destination':self.task.plan['target_location'] if self.task.plan.get('delivery_mode')=='quay' else ('船上 '+self.task.plan['target_location']) if self.reverse else '；'.join(f"{p['target_bay']} 第{p['target_row']}排第{p['target_tier']}层" for p in self.task.plan['containers']) if self.task.plan.get('double20') else f"{self.task.plan['target_bay']} 第{self.task.plan['target_row']}排第{self.task.plan['target_tier']}层",
                'camera_path':CAMERA_PATH,'occluded':self.occluded}

    def close(self,reason='stopped'):
        if self.closed:return
        try:
            if str(self.viewport.camera_path)==CAMERA_PATH:self.viewport.camera_path=self.previous_camera
        except Exception:pass
        if self.layer.identifier in self.stage.GetSessionLayer().subLayerPaths:self.stage.GetSessionLayer().subLayerPaths.remove(self.layer.identifier)
        self.closed=True;self.reason=reason
