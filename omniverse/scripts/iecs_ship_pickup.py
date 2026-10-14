"""Real ship-box pickup by the existing STS_01 axes and spreader."""
import builtins
from pxr import Gf,Usd,UsdGeom
from sts_control import STSController
from sts_unload_task import UnloadTask,SHIP,BOX,bounds,translate


class ShipPickup(UnloadTask):
    def __init__(self,stage,layer,box_path=BOX):
        self.stage=stage;self.layer=layer;self.box_path=box_path
        self.c=STSController(stage,'STS_01');self.home=dict(self.c.state)
        self.home_size=self.c.spreader_size_ft;self.closed=False;self.phase='Ready'
        self.attached=False;self.finished=False;self.configured=False
        box=bounds(stage,box_path)
        self.source=box.GetMidpoint();self.height=box.GetSize()[2]
        self.pick=Gf.Vec3d(self.source[0],self.source[1],box.GetMax()[2])
        self.c.set_spreader_size(40)
        self.clear_z=max(self.pick[2]+self.height+1.5,self.contact()[2])
        self.pick_pose=self.pose(self.pick)
        self.pick_high=self.pose(Gf.Vec3d(self.pick[0],self.pick[1],self.clear_z))
        sea=getattr(builtins,'_smart_port_sea_motion',None)
        if sea and not sea.closed and sea.stage==stage:sea.hold(SHIP)
        with Usd.EditContext(stage,layer):
            stage.OverridePrim(SHIP).SetInstanceable(False)
            xf=UsdGeom.Xformable(stage.GetPrimAtPath(box_path))
            self.box_op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'iecsShipPickup')
            xf.SetXformOpOrder([self.box_op]+[op for op in xf.GetOrderedXformOps() if op!=self.box_op])
            self.box_op.Set(Gf.Matrix4d(1))
            self.parent_inv=UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(box_path).GetParent()).GetInverse()
        self.c.hold()

    def configure(self,truck_center,deck_top):
        self.place=Gf.Vec3d(truck_center[0],truck_center[1],deck_top+self.height)
        self.place_pose=self.pose(self.place)
        self.place_high=self.pose(Gf.Vec3d(self.place[0],self.place[1],self.clear_z))
        self.phase='Raise clear';self.c.command(hoist=self.pick_high['hoist'])
        self.configured=True

    def set_box_world(self,center,heading=0.):
        # All motion opinions must be authored in the same stronger session layer.
        with Usd.EditContext(self.stage,self.layer):
            super().set_box_world(center,heading)

    def step(self,dt):
        # Controller clamps each tick to 0.1; substep to preserve speed multipliers.
        remaining=dt
        while remaining>1e-8:
            step=min(.05,remaining);remaining-=step
            self.c.tick(step)
            if self.attached:self.move_box()
            if any(abs(self.c.state[a]-v)>.0001 for a,v in self.c.targets.items()):continue
            if self.phase=='Raise clear':self.go('Align with ship box',self.pick_high)
            elif self.phase=='Align with ship box':self.go('Lower to box',self.pick_pose)
            elif self.phase=='Lower to box':
                self.attached=True;self.move_box();self.go('Locked - lift box',self.pick_high)
            elif self.phase=='Locked - lift box':self.go('Transfer to truck',self.place_high)
            elif self.phase=='Transfer to truck':self.go('Lower onto trailer',self.place_pose)
            elif self.phase=='Lower onto trailer':
                self.move_box();self.attached=False;self.go('Unlocked - raise spreader',self.place_high)
            elif self.phase=='Unlocked - raise spreader':self.finished=True;return True
        return self.finished

    def close(self):
        if self.closed:return
        self.closed=True;self.c.close()
        sea=getattr(builtins,'_smart_port_sea_motion',None)
        if sea and not sea.closed and sea.stage==self.stage:sea.release(SHIP)
