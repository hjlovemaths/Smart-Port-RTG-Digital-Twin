"""Primary RTG's real gantry/trolley/hoist receiver for IECS ship deliveries."""
from pxr import Gf,Usd,UsdGeom
from sts_yard_delivery import YardDelivery,GANTRY,TROLLEY,HOIST,ROPES
from yard_coordinate_mapping import RTG_TIRE_TRACK_OFFSETS_X_M,VISUAL_YARD_LAYOUT_CENTER_X_M


def wheel_track_centers(stage):
    cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    xs=[cache.ComputeWorldBound(p).ComputeAlignedBox().GetMidpoint()[0]
        for p in Usd.PrimRange(stage.GetPrimAtPath(GANTRY))
        if p.IsA(UsdGeom.Boundable) and 'black_tire_axis_X_rolls_Y' in p.GetName()]
    if len(xs)!=8:raise ValueError('RTG wheel calibration requires eight tires')
    xs.sort()
    return (sum(xs[:4])/4,sum(xs[4:])/4)


class YardDrop(YardDelivery):
    def __init__(self,stage,layer,pickup,truck,container,target):
        self.stage=stage;self.layer=layer;self.pickup=pickup;self.finished=False
        self.phase='RTG raise clear';self.attached=False
        self.paths=(GANTRY,TROLLEY,HOIST);self.ops=[];self.bases=[]
        self.offset=[0.,0.,0.];self.goal=[0.,0.,0.];self.directions=[]
        with Usd.EditContext(stage,layer):
            root=stage.GetPrimAtPath(GANTRY).GetParent()
            for prim in Usd.PrimRange(root):
                for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
                    attr=op.GetAttr()
                    if attr.Get() is None and attr.GetTimeSamples():
                        attr.Set(attr.Get(Usd.TimeCode(stage.GetStartTimeCode())))
            cache=UsdGeom.XformCache()
            guides=sorted(VISUAL_YARD_LAYOUT_CENTER_X_M+x for x in RTG_TIRE_TRACK_OFFSETS_X_M)
            wheels=wheel_track_centers(stage)
            corrections=[a-b for a,b in zip(guides,wheels)]
            if abs(corrections[0]-corrections[1])>.01:
                raise ValueError('RTG wheel gauge does not match painted corridor')
            correction=sum(corrections)/2
            for path,axis in zip(self.paths,((0,1,0),(1,0,0),(0,0,1))):
                prim=stage.GetPrimAtPath(path)
                if not prim:raise ValueError('RTG axis missing: '+path)
                self.directions.append(cache.GetLocalToWorldTransform(prim.GetParent()).TransformDir(Gf.Vec3d(*axis)))
                if path==GANTRY and abs(self.directions[-1][0])>1e-6:
                    raise ValueError('RTG gantry travel must remain parallel to painted guides')
                xf=UsdGeom.Xformable(prim);self.bases.append(xf.GetLocalTransformation())
                if path==GANTRY:
                    parent=cache.GetLocalToWorldTransform(prim.GetParent())
                    local=parent.GetInverse().TransformDir(Gf.Vec3d(correction,0,0))
                    self.bases[-1]=self.bases[-1]*Gf.Matrix4d(1).SetTranslate(local)
                op=next((op for op in xf.GetOrderedXformOps() if op.GetOpName()=='xformOp:transform:iecsRtg'),None)
                if op is None:op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'iecsRtg')
                xf.SetXformOpOrder([op]);op.Set(self.bases[-1]);self.ops.append(op)
            self.origin=self.contact()
            self.rope=UsdGeom.BasisCurves(stage.GetPrimAtPath(ROPES))
            self.rope_points=list(self.rope.GetPointsAttr().Get());self.rope_lower=[];index=0
            for count in self.rope.GetCurveVertexCountsAttr().Get():
                self.rope_lower.append(min(range(index,index+count),key=lambda i:self.rope_points[i][2]));index+=count
            self.rope_inverse=cache.GetLocalToWorldTransform(self.rope.GetPrim()).GetInverse()
        self.pick=Gf.Vec3d(*container['position'])+Gf.Vec3d(0,0,pickup.height/2)
        self.target=Gf.Vec3d(*target['point'])+Gf.Vec3d(0,0,pickup.height/2)
        self.high=max(self.origin[2],self.pick[2]+2.,self.target[2]+2.)
        self.command(Gf.Vec3d(self.origin[0],self.origin[1],self.high))

    def step(self,dt):
        with Usd.EditContext(self.stage,self.layer):
            ready=self.move_rtg(dt)
            if self.attached:
                self.pickup.set_box_world(self.contact()-Gf.Vec3d(0,0,self.pickup.height/2))
            if not ready:return False
            if self.phase=='RTG raise clear':
                self.command(Gf.Vec3d(self.pick[0],self.pick[1],self.high));self.phase='RTG align with truck'
            elif self.phase=='RTG align with truck':self.command(self.pick);self.phase='RTG lower to truck'
            elif self.phase=='RTG lower to truck':
                self.attached=True;self.command(Gf.Vec3d(self.pick[0],self.pick[1],self.high));self.phase='RTG lock and lift'
            elif self.phase=='RTG lock and lift':
                self.command(Gf.Vec3d(self.target[0],self.target[1],self.high));self.phase='RTG transfer to stack'
            elif self.phase=='RTG transfer to stack':self.command(self.target);self.phase='RTG lower to slot'
            elif self.phase=='RTG lower to slot':
                self.pickup.set_box_world(self.target-Gf.Vec3d(0,0,self.pickup.height/2))
                self.attached=False;self.command(Gf.Vec3d(self.target[0],self.target[1],self.high));self.phase='RTG unlock and raise'
            elif self.phase=='RTG unlock and raise':self.finished=True
            return self.finished
