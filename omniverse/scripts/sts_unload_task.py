"""Session-only STS_01 -> yard truck -> RTG -> 2C004 row1 tier1.

Kinematic visual task, using the existing truck and primary RTG models.
"""
import importlib.util
import builtins
import sys
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom

SHIP = '/World/PortAndRTG/SMART_PORT_4C_FEEDER_SHIP_LEFT_IMPORTED'
BOX = SHIP + '/Model/ShipContainer179'
TRUCK = '/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft'
TASK = '/World/STSUnloadTask'

def bounds(stage, path):
    return UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default','render']).ComputeWorldBound(
        stage.GetPrimAtPath(path)).ComputeAlignedBox()

def translate(v):
    return Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(*v))

class UnloadTask:
    def __init__(self, controller):
        self.c = controller
        self.stage = controller.stage
        self.phase = 'Ready'
        self.attached = False
        self.finished = False
        self.paused = False
        self.elapsed = 0.
        self.delivery = None
        self.delivering = False
        self.layer = Sdf.Layer.CreateAnonymous('sts01_unload.usda')
        for path in (SHIP, BOX, TRUCK, TRUCK+'/TrailerDeck'):
            if not self.stage.GetPrimAtPath(path):
                raise ValueError('Task model missing: '+path)
        if controller.path.rsplit('/',1)[-1] != 'STS_01':
            raise ValueError('This task requires STS_01')
        if abs(controller.state['boom']) > .001:
            raise ValueError('Lower the boom fully before starting the unload task')
        if controller.stopped:
            raise ValueError('Resume STS_01 before starting the unload task')
        self.home = dict(controller.state)
        self.home_size = controller.spreader_size_ft
        self.stage.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        try:
            sea=getattr(builtins,'_smart_port_sea_motion',None)
            if sea and not sea.closed and sea.stage==self.stage:
                sea.hold(SHIP)
            self.prepare()
        except Exception:
            self.close()
            raise

    def contact(self):
        cache = UsdGeom.XformCache()
        pts=[]
        for end in ('Front','Rear'):
            for side in ('Front','Rear'):
                p=self.stage.GetPrimAtPath(self.c.path+f'/Gantry/Trolley/Hoist/STS_SpreaderEnd{end}/CornerLock{side}')
                pts.append(cache.GetLocalToWorldTransform(p).Transform(Gf.Vec3d(0,0,-.5)))
        return sum(pts,Gf.Vec3d(0))/4

    @staticmethod
    def load_sibling(name):
        path=Path(__file__).resolve().with_name(name+'.py')
        spec=importlib.util.spec_from_file_location('_sts_runtime_'+name,path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module
        spec.loader.exec_module(module)
        return module

    def pose(self, point):
        # Read actual hierarchy including gantry width/height scales.
        cache=UsdGeom.XformCache()
        root=cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.c.path))
        gantry=cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.c.path+'/Gantry'))
        columns=[root.TransformDir(Gf.Vec3d(-1,0,0)),
                 gantry.TransformDir(Gf.Vec3d(0,-1,0)),
                 gantry.TransformDir(Gf.Vec3d(0,0,1))]
        delta=point-self.contact()
        result={a:self.c.state[a]+Gf.Dot(delta,v)/Gf.Dot(v,v)
                for a,v in zip(('gantry','trolley','hoist'),columns)}
        for axis,value in result.items():
            lo,hi=self.c.limits[axis]
            if not lo<=value<=hi:
                raise ValueError(f'Task {axis} target {value:.3f} outside {lo}..{hi}')
        return result

    def prepare(self):
        self.c.set_spreader_size(40)
        box=bounds(self.stage,BOX)
        self.source=box.GetMidpoint()
        self.height=box.GetSize()[2]
        self.pick=Gf.Vec3d(self.source[0],self.source[1],box.GetMax()[2])
        # Quay truck lane below the crane; trailer is aligned along world Y.
        deck=bounds(self.stage,TRUCK+'/TrailerDeck')
        self.truck_deck=deck.GetMidpoint()
        self.stop=Gf.Vec3d(-238.0,self.source[1],deck.GetMax()[2])
        self.start=self.stop+Gf.Vec3d(0,-22,0)
        self.place=self.stop+Gf.Vec3d(0,0,self.height+.02)
        self.clear_z=max(self.pick[2]+self.height+1.5,self.contact()[2])
        self.pick_pose=self.pose(self.pick)
        self.place_pose=self.pose(self.place)
        self.pick_high=self.pose(Gf.Vec3d(self.pick[0],self.pick[1],self.clear_z))
        self.place_high=self.pose(Gf.Vec3d(self.place[0],self.place[1],self.clear_z))
        with Usd.EditContext(self.stage,self.layer):
            self.stage.OverridePrim(SHIP).SetInstanceable(False)
            truck=UsdGeom.Xform.Define(self.stage,TASK+'/Truck')
            truck.GetPrim().GetReferences().AddInternalReference(TRUCK)
            truck.GetPrim().SetInstanceable(False)
            # Cancel source animation by using a new, default-only matrix op.
            self.truck_op=truck.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'unloadPose')
            truck.SetXformOpOrder([self.truck_op],resetXformStack=True)
            self.truck_world=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(TRUCK))
            UsdGeom.Imageable(truck).CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            xf=UsdGeom.Xformable(self.stage.GetPrimAtPath(BOX))
            self.box_op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'unloadDelta')
            xf.SetXformOpOrder([self.box_op]+[o for o in xf.GetOrderedXformOps() if o!=self.box_op])
            self.box_op.Set(Gf.Matrix4d(1))
            self.parent_inv=UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(BOX).GetParent()).GetInverse()
            self.set_truck(self.start)
            self.delivery=self.load_sibling('sts_yard_delivery').YardDelivery(self)
        self.phase='Truck approaching'
        self.c.hold()

    def set_truck(self, point, heading=180.):
        pivot=Gf.Vec3d(self.truck_deck[0],self.truck_deck[1],self.stop[2])
        rotation=Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),heading))
        self.truck_op.Set(self.truck_world*translate(-pivot)*rotation*translate(point))

    def set_box_world(self, center, heading=0.):
        rotation=Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),heading))
        delta=translate(-self.source)*rotation*translate(center)
        self.box_op.Set(self.parent_inv.GetInverse()*delta*self.parent_inv)

    def move_box(self):
        desired=self.contact()-Gf.Vec3d(0,0,self.height/2)
        self.set_box_world(desired)

    def go(self,phase,pose):
        self.phase=phase
        self.c.command(**pose)

    def tick(self,dt):
        if self.finished or self.paused or self.c.stopped:
            return
        dt=max(0.,min(float(dt),.1))
        with Usd.EditContext(self.stage,self.layer):
            if self.delivering:
                self.delivery.tick(dt)
                self.phase=self.delivery.phase
                return
            if self.phase=='Truck approaching':
                self.elapsed+=dt
                fraction=min(self.elapsed/8.,1.)
                self.set_truck(self.start+(self.stop-self.start)*fraction)
                if fraction==1:
                    self.go('Raise clear',dict(hoist=self.pick_high['hoist']))
                return
            if self.attached:
                self.move_box()
            if any(abs(self.c.state[a]-v)>.0001 for a,v in self.c.targets.items()):
                return
            if self.phase=='Raise clear':
                self.go('Align with ship box',self.pick_high)
            elif self.phase=='Align with ship box':
                self.go('Lower to box',self.pick_pose)
            elif self.phase=='Lower to box':
                self.attached=True
                self.move_box()
                self.go('Locked - lift box',self.pick_high)
            elif self.phase=='Locked - lift box':
                self.go('Transfer to truck',self.place_high)
            elif self.phase=='Transfer to truck':
                self.go('Lower onto trailer',self.place_pose)
            elif self.phase=='Lower onto trailer':
                self.move_box()
                self.attached=False
                self.go('Unlocked - raise spreader',self.place_high)
            elif self.phase=='Unlocked - raise spreader':
                self.delivering=True
                self.phase=self.delivery.phase

    def close(self):
        self.c.hold()
        session=self.stage.GetSessionLayer()
        if self.layer.identifier in session.subLayerPaths:
            session.subLayerPaths.remove(self.layer.identifier)
        self.c.state=dict(self.home)
        self.c.targets=dict(self.home)
        self.c.spreader_size_ft=self.home_size
        self.c._apply()
        self.finished=True
        sea=getattr(builtins,'_smart_port_sea_motion',None)
        if sea and not sea.closed and sea.stage==self.stage:
            sea.release(SHIP)
