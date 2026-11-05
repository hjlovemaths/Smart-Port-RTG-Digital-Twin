"""Reversible RTG -> truck -> STS loading, reusing the shared task interlocks."""
from pxr import Gf,Sdf,Usd,UsdGeom
from ship_to_yard_transfer import ShipToYardTransfer,VisualRTG,task_op,tr
from ship_transfer_plan import PARK,box,solve

ROOT='/World/YardToShipTransfer'


class YardToShipTransfer(ShipToYardTransfer):
    initial_phase='停车场集卡前往堆场'

    def prepare(self):
        p=self.plan;self.height=p['cargo_dimensions_m'][2]
        if p.get('delivery_mode')!='quay':self.c.set_spreader_size(p['size_ft'])
        UsdGeom.Xform.Define(self.stage,ROOT)
        self.prepare_cargo(p,ROOT+'/Cargo')
        self.prepare_transport()

    def prepare_cargo(self,p,path):
        source=self.stage.GetPrimAtPath(p['source_path']);prefix=source.GetName()
        parts=[q for q in source.GetParent().GetChildren() if q.GetName()==prefix or q.GetName().startswith(prefix+'_')]
        self.cargo=UsdGeom.Xform.Define(self.stage,path).GetPrim()
        xf,self.cargo_op=task_op(self.cargo);xf.SetXformOpOrder([self.cargo_op],resetXformStack=True)
        self.cargo_op.Set(Gf.Matrix4d(1));self.cargo_world=Gf.Matrix4d(1)
        self.cargo_origin=(Gf.Vec3d(p['source_bounds'][0])+Gf.Vec3d(p['source_bounds'][1]))/2
        cache=UsdGeom.XformCache()
        for i,part in enumerate(parts):
            world=cache.GetLocalToWorldTransform(part)
            copied=self.stage.DefinePrim(str(self.cargo.GetPath())+f'/Part{i:02d}')
            copied.GetReferences().AddInternalReference(part.GetPath());copied.SetInstanceable(False)
            xf,op=task_op(copied);xf.SetXformOpOrder([op]);op.Set(world)
            UsdGeom.Imageable(copied).CreateVisibilityAttr().Set('inherited')
        for part in parts:UsdGeom.Imageable(part).CreateVisibilityAttr().Set('invisible')
        for path in p['target_placeholder_paths']:
            UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).CreateVisibilityAttr().Set('invisible')
        self.cargo.CreateAttribute('transfer:sourceBay',Sdf.ValueTypeNames.String).Set(p['resolved_source_bay'])
        self.cargo.CreateAttribute('transfer:targetLocation',Sdf.ValueTypeNames.String).Set(p['target_location'])

    def prepare_transport(self):
        p=self.plan;cache=UsdGeom.XformCache()
        self.truck=UsdGeom.Xform.Define(self.stage,ROOT+'/Truck').GetPrim()
        self.truck.GetReferences().AddInternalReference(PARK);self.truck.SetInstanceable(False)
        UsdGeom.Imageable(self.truck).CreateVisibilityAttr().Set('inherited')
        park=self.stage.GetPrimAtPath(PARK);self.truck_world=cache.GetLocalToWorldTransform(park)
        self.deck=Gf.Range3d()
        for q in Usd.PrimRange(park,Usd.TraverseInstanceProxies()):
            if q.IsA(UsdGeom.Boundable) and 'trailer_side_rail' in q.GetName():self.deck.UnionWith(box(self.stage,q.GetPath()))
        if self.deck.IsEmpty():raise ValueError('停车场集卡缺少可测量承载面')
        self.pivot=Gf.Vec3d(self.deck.GetMidpoint()[0],self.deck.GetMidpoint()[1],self.deck.GetMax()[2])
        xf,self.truck_op=task_op(self.truck);xf.SetXformOpOrder([self.truck_op],resetXformStack=True)
        UsdGeom.Imageable(park).CreateVisibilityAttr().Set('invisible')
        label=self.stage.GetPrimAtPath('/World/ParkedTruckNumbers/Truck01')
        if label:UsdGeom.Imageable(label).CreateVisibilityAttr().Set('invisible')
        self.truck.CreateAttribute('vehicle:number',Sdf.ValueTypeNames.String).Set('01')
        self.truck_point=Gf.Vec3d(self.pivot);self.truck_heading=0.
        self.set_route(p['truck_route_empty']);self.route[0]=Gf.Vec3d(self.pivot);self.set_truck(self.pivot,0)
        self.rtg=VisualRTG(self.stage,p['size_ft']);self.rtg_high=max(p['yard_clear_z'],self.rtg.origin[2])
        self.pick=Gf.Vec3d(p['source_pick_m'])
        self.yard_stop=Gf.Vec3d(p['yard_stop_m']);self.yard_stop[2]=self.pivot[2]
        self.yard_place=self.yard_stop+Gf.Vec3d(0,0,self.height)
        self.stop=Gf.Vec3d(p['truck_stop_m']);self.stop[2]=self.pivot[2]
        self.quay_pick=self.stop+Gf.Vec3d(0,0,self.height)
        self.target=Gf.Vec3d(p['target_center_m'])+Gf.Vec3d(0,0,self.height/2)
        for point in (self.quay_pick,self.target,Gf.Vec3d(self.quay_pick[0],self.quay_pick[1],25),Gf.Vec3d(self.target[0],self.target[1],25)):solve(self.stage,self.c,point)
        for point in (self.pick,self.yard_place,Gf.Vec3d(self.pick[0],self.pick[1],self.rtg_high),Gf.Vec3d(self.yard_place[0],self.yard_place[1],self.rtg_high)):self.command_rtg(point)
        self.command_rtg(Gf.Vec3d(self.pick[0],self.pick[1],self.rtg_high))
        self.go_sts(Gf.Vec3d(self.quay_pick[0],self.quay_pick[1],25))

    def lock(self,owner):
        self.check_grip_size(owner)
        self.attached=owner
        self.cargo.CreateAttribute('transfer:lockedTo',Sdf.ValueTypeNames.String).Set(owner or '')

    def command_rtg(self,point):self.rtg.command(point)

    def advance_yard_load(self):return False

    def advance_quay_load(self):return False

    def arrive_at_quay(self):self.change('岸桥下降到集卡');self.go_sts(self.quay_pick)

    def tick(self,dt):
        if self.closed or self.finished:return
        if self.c.stopped:self.pause();return
        if self.paused:return
        dt=max(0,min(float(dt),.1));self.elapsed+=dt;self.wait+=dt
        with Usd.EditContext(self.stage,self.layer):
            if not self.spreader_gate(dt):return
            self.c.tick(dt);rtg_ready=self.rtg.tick(dt)
            sts_ready=all(abs(self.c.state[a]-v)<1e-4 for a,v in self.c.targets.items())
            if self.phase=='停车场集卡前往堆场':
                if self.drive(dt) and rtg_ready:self.change('RTG下降到源箱');self.command_rtg(self.pick)
            elif self.phase=='RTG移向下一只源箱' and rtg_ready:self.change('RTG下降到源箱');self.command_rtg(self.pick)
            elif self.phase=='RTG下降到源箱' and rtg_ready:self.change('RTG源箱对位闭锁')
            elif self.phase=='RTG源箱对位闭锁' and self.wait>=1:
                self.lock('rtg');self.change('RTG起升堆场箱');self.command_rtg(Gf.Vec3d(self.pick[0],self.pick[1],self.rtg_high))
            elif self.phase=='RTG起升堆场箱' and rtg_ready:
                self.change('RTG移箱到集卡');self.command_rtg(Gf.Vec3d(self.yard_place[0],self.yard_place[1],self.rtg_high))
            elif self.phase=='RTG移箱到集卡' and rtg_ready:self.change('RTG落箱到集卡');self.command_rtg(self.yard_place)
            elif self.phase=='RTG落箱到集卡' and rtg_ready:self.change('RTG开锁交接')
            elif self.phase=='RTG开锁交接' and self.wait>=1:
                self.lock(None);self.cargo_on_truck=True;self.change('RTG抬离集卡');self.command_rtg(Gf.Vec3d(self.yard_place[0],self.yard_place[1],self.rtg_high))
            elif self.phase=='RTG抬离集卡' and rtg_ready:
                if not self.advance_yard_load():
                    self.change('集卡经引桥运往岸桥');self.set_route(self.plan['truck_route_loaded']);self.route[0]=Gf.Vec3d(self.truck_point)
            elif self.phase=='集卡经引桥运往岸桥':
                if self.drive(dt) and sts_ready:self.arrive_at_quay()
            elif self.phase=='岸桥下降到集卡' and sts_ready:self.change('岸桥集卡对位闭锁')
            elif self.phase=='岸桥移向下一只集卡箱' and sts_ready:self.change('岸桥下降到集卡');self.go_sts(self.quay_pick)
            elif self.phase=='岸桥集卡对位闭锁' and self.wait>=1:
                self.cargo_on_truck=False;self.lock('sts');self.change('岸桥起升集卡箱');self.go_sts(Gf.Vec3d(self.quay_pick[0],self.quay_pick[1],25))
            elif self.phase=='岸桥起升集卡箱' and sts_ready:
                self.change('岸桥小车移向船箱位');self.go_sts(Gf.Vec3d(self.target[0],self.target[1],25))
            elif self.phase=='岸桥小车移向船箱位' and sts_ready:self.change('岸桥装船下降');self.go_sts(self.target)
            elif self.phase=='岸桥装船下降' and sts_ready:self.change('岸桥船上开锁落箱')
            elif self.phase=='岸桥船上开锁落箱' and self.wait>=1:
                self.set_cargo(self.target-Gf.Vec3d(0,0,self.height/2));self.lock(None);self.change('岸桥吊具抬离船箱');self.go_sts(Gf.Vec3d(self.target[0],self.target[1],25))
            elif self.phase=='岸桥吊具抬离船箱' and sts_ready:
                if not self.advance_quay_load():self.begin_return()
            elif self.phase in ('集卡空车返回停车场','集卡倒车归位'):self.tick_return(dt)
            if self.attached=='rtg':self.set_cargo(self.rtg.contact()-Gf.Vec3d(0,0,self.height/2))
            elif self.attached=='sts':self.set_cargo(self.sts_contact()-Gf.Vec3d(0,0,self.height/2))
            elif self.cargo_on_truck:self.set_cargo(self.truck_load_center(),self.truck_heading-90)

    def summary(self):
        result=super().summary()
        result.update(direction='yard_to_ship',source_bay=self.plan['resolved_source_bay'],source_row=self.plan['source_row'],source_tier=self.plan['source_tier'],target_location=self.plan['target_location'])
        return result
