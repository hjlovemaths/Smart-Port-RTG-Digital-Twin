"""Quay road route and kinematic primary-RTG receiver for STS unloading."""
import math
from pxr import Gf, Usd, UsdGeom, Vt

GANTRY='/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL'
TROLLEY=GANTRY+'/ANIM_CTRL_RTG_TROLLEY_TRAVEL'
HOIST=TROLLEY+'/ANIM_CTRL_RTG_HOIST_VERTICAL'
ROPES=TROLLEY+'/RTG_DYNAMIC_HOIST_ROPES'

def translation(v):
    return Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(*v))

def rounded_route(points,radius=4.):
    """Short sampled quadratic turns, with straight segments in between."""
    result=[Gf.Vec3d(*points[0])]
    for i in range(1,len(points)-1):
        a,b,c=(Gf.Vec3d(*p) for p in points[i-1:i+2])
        u,v=b-a,c-b
        distance=min(radius,u.GetLength()*.35,v.GetLength()*.35)
        entry=b-u.GetNormalized()*distance
        leave=b+v.GetNormalized()*distance
        result.append(entry)
        for j in range(1,25):
            t=j/24
            result.append((1-t)**2*entry+2*t*(1-t)*b+t*t*leave)
    result.append(Gf.Vec3d(*points[-1]))
    return result

class YardDelivery:
    def __init__(self,task):
        self.task=task
        self.stage=task.stage
        self.attached=False
        self.loaded=True
        self.phase='Truck to 2C via causeway 02'
        # Import the same coordinate functions as the yard builder by file path.
        layout=task.load_sibling('build_live_containers')
        self.truck_lane_center_x=layout.TRUCK_OPERATING_LANE_CENTER_X_M
        self.target=Gf.Vec3d(layout.row_center_x_m(1,6),
            layout.container_center_y_for_bay('2C',4,40),
            layout.PAD_TOP_Z_M+layout.STACK_FLOOR_CLEARANCE_M+task.height)
        self.truck_stop=Gf.Vec3d(self.truck_lane_center_x,self.target[1],task.stop[2])
        self.pick=self.truck_stop+Gf.Vec3d(0,0,task.height+.02)
        self.check_occupancy()
        for path in (GANTRY,TROLLEY,HOIST,ROPES):
            if not self.stage.GetPrimAtPath(path):
                raise ValueError('Missing RTG receiver: '+path)
        # Blender exports some static rotations/scales only as time samples.
        # Default-time matrix evaluation treats missing values as identity,
        # dropping the gantry's 180-degree rotation and swapping its wheel lanes.
        # Keep live/default opinions; fill only absent defaults from the authored
        # start pose, in this task's reversible session layer.
        for prim in Usd.PrimRange(self.stage.GetPrimAtPath(GANTRY).GetParent()):
            for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
                attr=op.GetAttr()
                if attr.Get() is None and attr.GetTimeSamples():
                    value=attr.Get(Usd.TimeCode(self.stage.GetStartTimeCode()))
                    if value is not None:
                        attr.Set(value)
        self.paths=(GANTRY,TROLLEY,HOIST)
        self.ops=[]
        self.bases=[]
        self.offset=[0.,0.,0.]
        self.goal=list(self.offset)
        cache=UsdGeom.XformCache()
        self.directions=[]
        for path,axis in zip(self.paths,((0,1,0),(1,0,0),(0,0,1))):
            prim=self.stage.GetPrimAtPath(path)
            parent=cache.GetLocalToWorldTransform(prim.GetParent())
            self.directions.append(parent.TransformDir(Gf.Vec3d(*axis)))
            xf=UsdGeom.Xformable(prim)
            self.bases.append(xf.GetLocalTransformation())
            op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'stsYardTask')
            xf.SetXformOpOrder([op])
            op.Set(self.bases[-1])
            self.ops.append(op)
        # Default-only matrices suppress old timeline animation throughout task.
        for side in ('FRONT','REAR'):
            prim=self.stage.GetPrimAtPath(HOIST+'/ANIM_CTRL_SPREADER_TELESCOPE_'+side)
            xf=UsdGeom.Xformable(prim)
            base=xf.GetLocalTransformation()
            op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'stsYardTask')
            xf.SetXformOpOrder([op])
            op.Set(base)
        self.origin=self.contact()
        self.rope=UsdGeom.BasisCurves(self.stage.GetPrimAtPath(ROPES))
        self.rope_points=list(self.rope.GetPointsAttr().Get())
        self.rope_lower=[]
        index=0
        for count in self.rope.GetCurveVertexCountsAttr().Get():
            ids=range(index,index+count)
            self.rope_lower.append(min(ids,key=lambda i:self.rope_points[i][2]))
            index+=count
        self.rope_inverse=cache.GetLocalToWorldTransform(self.rope.GetPrim()).GetInverse()
        self.high=max(self.origin[2],6.4)
        self.route=rounded_route([task.stop,
            (-238.,126.05,task.stop[2]),
            (self.truck_lane_center_x,126.05,task.stop[2]),self.truck_stop])
        self.route_index=1
        self.position=Gf.Vec3d(task.stop)
        self.command(Gf.Vec3d(self.origin[0],self.origin[1],self.high))
        self.rtg_arriving=False

    def check_occupancy(self):
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
        half=Gf.Vec3d(.685,2.636,self.task.height/2)
        center=self.target-Gf.Vec3d(0,0,self.task.height/2)
        for prim in self.stage.Traverse():
            name=prim.GetName().lower()
            if not ('container' in name and prim.IsA(UsdGeom.Boundable)):
                continue
            b=cache.ComputeWorldBound(prim).ComputeAlignedBox()
            if all(b.GetMin()[i]<center[i]+half[i]-.01 and
                   b.GetMax()[i]>center[i]-half[i]+.01 for i in range(3)):
                raise ValueError('2C004 row 1 tier 1 occupied: '+str(prim.GetPath()))

    def contact(self):
        box=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(
            self.stage.GetPrimAtPath(HOIST)).ComputeAlignedBox()
        center=box.GetMidpoint()
        return Gf.Vec3d(center[0],center[1],box.GetMin()[2])

    def command(self,point):
        delta=point-self.origin
        self.goal=[Gf.Dot(delta,v)/Gf.Dot(v,v) for v in self.directions]
        # Local travel limits from the measured RTG structure (scene scale).
        if not (-12<=self.goal[1]<=12 and -9<=self.goal[2]<=2):
            raise ValueError('RTG target outside visual working envelope')

    def move_rtg(self,dt):
        for i,speed in enumerate((8.,1.3,.8)):
            d=self.goal[i]-self.offset[i]
            self.offset[i]+=max(-speed*dt,min(speed*dt,d))
            vec=[0.,0.,0.]
            vec[(1,0,2)[i]]=self.offset[i]
            self.ops[i].Set(self.bases[i]*translation(vec))
        points=list(self.rope_points)
        delta=self.rope_inverse.TransformDir(self.directions[2]*self.offset[2])
        for i in self.rope_lower:
            points[i]=Gf.Vec3f(Gf.Vec3d(points[i])+delta)
        self.rope.GetPointsAttr().Set(Vt.Vec3fArray(points))
        return all(abs(a-b)<1e-5 for a,b in zip(self.offset,self.goal))

    def tick(self,dt):
        ready=self.move_rtg(dt)
        if self.attached:
            self.task.set_box_world(self.contact()-Gf.Vec3d(0,0,self.task.height/2),180.)
        if self.phase.startswith('Truck to'):
            if ready and not self.rtg_arriving:
                self.command(Gf.Vec3d(self.pick[0],self.pick[1],self.high))
                self.rtg_arriving=True
            remaining=dt*8.
            while remaining>0 and self.route_index<len(self.route):
                d=self.route[self.route_index]-self.position
                length=d.GetLength()
                if length<1e-8:
                    self.route_index+=1
                    continue
                step=min(remaining,length)
                self.position+=d*(step/length)
                remaining-=step
                heading=math.degrees(math.atan2(d[0],-d[1]))
                self.task.set_truck(self.position,heading)
                self.task.set_box_world(self.position+Gf.Vec3d(0,0,self.task.height/2+.02),heading)
                if step==length:
                    self.route_index+=1
            if self.route_index==len(self.route):
                self.phase='RTG approaching truck at 2C004'
        elif self.phase=='RTG approaching truck at 2C004' and ready:
            self.command(self.pick)
            self.phase='RTG lower to truck'
        elif self.phase=='RTG lower to truck' and ready:
            self.attached=True
            self.loaded=False
            self.command(Gf.Vec3d(self.pick[0],self.pick[1],self.high))
            self.phase='RTG locked - lift box'
        elif self.phase=='RTG locked - lift box' and ready:
            self.command(Gf.Vec3d(self.target[0],self.target[1],self.high))
            self.phase='RTG transfer to row 1'
        elif self.phase=='RTG transfer to row 1' and ready:
            self.command(self.target)
            self.phase='RTG lower to tier 1'
        elif self.phase=='RTG lower to tier 1' and ready:
            self.attached=False
            self.command(Gf.Vec3d(self.target[0],self.target[1],self.high))
            self.phase='RTG unlocked - raise clear'
        elif self.phase=='RTG unlocked - raise clear' and ready:
            self.phase='Complete: 2C003+005 / row 1 / tier 1'
            self.task.finished=True
