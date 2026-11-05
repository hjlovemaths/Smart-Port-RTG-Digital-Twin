"""One stable work-cell view for an entire RTG pickup and placement sequence."""
import math
from pxr import Gf
from ship_transfer_plan import box


class RTGWorkShot:
    def __init__(self,stage,task):
        self.bounds=Gf.Range3d();self.points=[];self.landings=[];self.chosen=False;self.occluded=False
        p=task.plan;reverse=p.get('direction')=='yard_to_ship';height=task.height
        # Cover every planned stop, so framing remains stable while the truck
        # advances between loading bays and the empty RTG follows it.
        for stop in p.get('yard_loading_stops_m',[p['yard_vehicle_stop_m']]):
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
                offset=(i-.5)*p['pair_pitch_m'] if p.get('double20') else 0.
                contacts.append(task.yard_stop+Gf.Vec3d(0,offset,height))
                landing=Gf.Vec3d(item['target_center_m'])
                contacts.append(landing+Gf.Vec3d(0,0,height/2))
                self.landings.append(Gf.Range3d(landing-dimensions/2,landing+dimensions/2))
        for point in contacts:
            point=Gf.Vec3d(point)
            self.points.extend((point+Gf.Vec3d(0,0,.12),Gf.Vec3d(point[0],point[1],task.rtg_high)))
            self.bounds.UnionWith(Gf.Range3d(point-Gf.Vec3d(dimensions[0]/2,dimensions[1]/2,height),
                                           Gf.Vec3d(point[0]+dimensions[0]/2,point[1]+dimensions[1]/2,task.rtg_high+.8)))
        self.center=self.bounds.GetMidpoint();self.eye=self.fit_eye(Gf.Vec3d(-1,-.65,0),8.4)

    def fit_eye(self,side,height):
        horizontal=math.tan(math.radians(55)/2)/1.10;vertical=horizontal/(16/9)
        side=side.GetNormalized();distance=12.
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
            for height in (8.4,8.,8.65):
                eye=self.fit_eye(side,height)
                # Assess the entire planned action, including the second twin
                # box, once. Do not orbit again when a lock opens or a trolley
                # temporarily crosses the line of sight.
                hits=sum(hit_test(eye,point,b) for point in self.points for b in [*obstacles,*self.landings])
                candidates.append((hits,abs(angle)+abs(height-8.4)*10,eye))
        hits,_,self.eye=min(candidates,key=lambda item:(item[0],item[1]))
        self.occluded=bool(hits);self.chosen=True

    def framing(self):
        delta=self.eye-self.center
        return Gf.Vec3d(self.center),delta.GetNormalized(),delta.GetLength()
