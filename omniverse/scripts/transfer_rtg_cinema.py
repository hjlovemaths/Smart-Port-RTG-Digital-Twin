"""Fit RTG work-cell views and individual unload shots."""
import math
from pxr import Gf
from ship_transfer_plan import box


class RTGWorkShot:
    def __init__(self,stage,task,unload_shot=None):
        self.bounds=Gf.Range3d();self.points=[];self.landings=[];self.chosen=False;self.occluded=False
        self.min_distance=12.;self.heights=(8.4,8.,8.65)
        if unload_shot is not None:
            self.frame_unload(task,unload_shot)
            return
        p=task.plan;reverse=p.get('direction')=='yard_to_ship';height=task.height
        # Cover every planned stop, so framing remains stable while the truck
        # advances between loading bays and the empty RTG follows it.
        stops=p.get('yard_loading_stops_m') or p.get('yard_unloading_stops_m') or [p['yard_vehicle_stop_m']]
        for stop in stops:
            park=Gf.Vec3d(stop)
            delta=(Gf.Matrix4d(1).SetTranslate(-task.truck_point)*
                   Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),90-task.truck_heading))*
                   Gf.Matrix4d(1).SetTranslate(park))
            self.bounds.UnionWith(Gf.BBox3d(box(stage,task.truck.GetPath()),delta).ComputeAlignedRange())
        if reverse:
            contacts=[]
            for i,item in enumerate(p.get('containers',[p])):
                contacts.append(Gf.Vec3d(item['source_pick_m']))
                place=p['truck_slots'][i]['yard_contact_m'] if p.get('plan_type')=='cumulative_double20' else p['yard_stop_m']
                contacts.append(Gf.Vec3d(place)+Gf.Vec3d(0,0,item['cargo_dimensions_m'][2]))
            dimensions=Gf.Vec3d(p['cargo_dimensions_m'])
        else:
            items=p.get('containers',[p]);contacts=[]
            dimensions=Gf.Vec3d(items[0]['cargo_dimensions_m'])
            for i,item in enumerate(items):
                if p.get('double20'):
                    slot=p['truck_slots'][i]
                    contacts.append(Gf.Vec3d(slot['yard_contact_m'])+Gf.Vec3d(0,0,slot['load_top_above_deck_m']))
                else:contacts.append(task.yard_stop+Gf.Vec3d(0,0,height))
                landing=Gf.Vec3d(item['target_center_m'])
                contacts.append(landing+Gf.Vec3d(0,0,item['cargo_dimensions_m'][2]/2))
                self.landings.append(Gf.Range3d(landing-dimensions/2,landing+dimensions/2))
        for point in contacts:
            point=Gf.Vec3d(point)
            self.points.extend((point+Gf.Vec3d(0,0,.12),Gf.Vec3d(point[0],point[1],task.rtg_high)))
            self.bounds.UnionWith(Gf.Range3d(point-Gf.Vec3d(dimensions[0]/2,dimensions[1]/2,height),
                                           Gf.Vec3d(point[0]+dimensions[0]/2,point[1]+dimensions[1]/2,task.rtg_high+.8)))
        self.center=self.bounds.GetMidpoint();self.eye=self.fit_eye(Gf.Vec3d(-1,-.65,0),self.heights[0])

    def frame_unload(self,task,shot):
        item=task.plan['containers'][task.active_index]
        width,length,height=item['cargo_dimensions_m']
        pick,place=task.yard_pick,task.target
        if shot=='rtg_unload_medium':
            # Include the full lifted path at this bay, from trailer to stack.
            for point in (pick,place):
                self.bounds.UnionWith(Gf.Range3d(
                    point-Gf.Vec3d(width/2+.6,length/2+.6,height+.5),
                    Gf.Vec3d(point[0]+width/2+.6,point[1]+length/2+.6,task.rtg_high+.65)))
                self.points.append(Gf.Vec3d(point[0],point[1],task.rtg_high))
            self.min_distance=10.
        else:
            # Tight on the twistlocks, the entire box and the supporting deck
            # or stack. The spreader enters from above during descent.
            contact=pick if shot=='rtg_pick_close' else place
            self.bounds=Gf.Range3d(
                contact-Gf.Vec3d(width/2+.32,length/2+.35,height+.38),
                contact+Gf.Vec3d(width/2+.32,length/2+.35,1.15))
            self.points.extend(contact+Gf.Vec3d(0,y,.12) for y in (0.,-length*.34,length*.34))
            self.min_distance=3.8
        self.center=self.bounds.GetMidpoint()
        if shot!='rtg_unload_medium':
            height=max(4.0,min(8.4,self.center[2]+2.6))
            self.heights=(height,min(8.65,height+1.25),8.65)
        self.eye=self.fit_eye(Gf.Vec3d(-1,-.65,0),self.heights[0])

    def fit_eye(self,side,height):
        horizontal=math.tan(math.radians(55)/2)/1.10;vertical=horizontal/(16/9)
        side=side.GetNormalized();distance=self.min_distance
        for _ in range(60):
            eye=self.center+side*distance;eye[2]=height
            view=Gf.Matrix4d(1).SetLookAt(eye,self.center,Gf.Vec3d(0,0,1))
            fits=True
            for corner in range(8):
                point=view.Transform(Gf.Vec3d(*(self.bounds.GetMax()[a] if corner&(1<<a) else self.bounds.GetMin()[a] for a in range(3))))
                if point[2]>=-.1 or abs(point[0])>-point[2]*horizontal or abs(point[1])>-point[2]*vertical:
                    fits=False;break
            if fits:return eye
            distance*=1.08
        raise ValueError('RTG工作区无法完整取景')

    def choose(self,obstacles,hit_test):
        if self.chosen:return
        candidates=[]
        for angle in (0,-30,30,-60,60,-100,100,180):
            side=Gf.Rotation(Gf.Vec3d(0,0,1),angle).TransformDir(Gf.Vec3d(-1,-.65,0))
            for height in self.heights:
                eye=self.fit_eye(side,height)
                # Select an angle once per shot to avoid reacting to every
                # momentary obstruction or lock state change.
                hits=sum(hit_test(eye,point,b) for point in self.points for b in [*obstacles,*self.landings])
                candidates.append((hits,abs(angle)+abs(height-self.heights[0])*10,eye))
        hits,_,self.eye=min(candidates,key=lambda item:(item[0],item[1]))
        self.occluded=bool(hits);self.chosen=True

    def framing(self):
        delta=self.eye-self.center
        return Gf.Vec3d(self.center),delta.GetNormalized(),delta.GetLength()
