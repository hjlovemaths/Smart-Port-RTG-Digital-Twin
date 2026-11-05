"""Deterministic single-truck driving, swept collision checks and water reset."""
import math


def corners(x,y,heading,half_length,half_width):
    c,s=math.cos(heading),math.sin(heading)
    return [(x+a*c-b*s,y+a*s+b*c) for a,b in
            ((-half_length,-half_width),(-half_length,half_width),(half_length,half_width),(half_length,-half_width))]


def intersects(body,rect):
    other=[(rect[0],rect[1]),(rect[0],rect[3]),(rect[2],rect[3]),(rect[2],rect[1])]
    for polygon in (body,other):
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            axis=(a[1]-b[1],b[0]-a[0])
            p=[x*axis[0]+y*axis[1] for x,y in body];q=[x*axis[0]+y*axis[1] for x,y in other]
            if max(p)<min(q) or max(q)<min(p):return False
    return True


class TruckDriving:
    def __init__(self,spawn,half_length,half_width):
        self.spawn=tuple(spawn);self.x,self.y,self.heading=self.spawn
        self.half_length=half_length;self.half_width=half_width
        self.speed=0.;self.throttle=0.;self.steer=0.;self.brake=False
        self.event='ready';self.hit=None;self.reset_count=0

    def controls(self,throttle=0,steer=0,brake=False):
        for value in (throttle,steer):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -1<=value<=1:
                raise ValueError('油门和转向须为-1到1之间的有限数字')
        if type(brake) is not bool:raise ValueError('制动参数须为布尔值')
        self.throttle=float(throttle);self.steer=float(steer);self.brake=brake

    def reset(self,reason='reset'):
        self.x,self.y,self.heading=self.spawn;self.speed=0.;self.controls()
        self.event=reason;self.hit=None;self.reset_count+=1

    def stop(self):self.speed=0.;self.controls()

    def footprint(self,x=None,y=None,heading=None):
        return corners(self.x if x is None else x,self.y if y is None else y,
                       self.heading if heading is None else heading,self.half_length+.025,self.half_width+.025)

    def step(self,dt,obstacles,supported):
        dt=max(0.,min(float(dt),.1));self.event='driving' if self.throttle else 'ready';self.hit=None
        if self.brake:
            self.speed=math.copysign(max(0.,abs(self.speed)-12*dt),self.speed)
        elif self.throttle:
            target=8*self.throttle if self.throttle>=0 else 3*self.throttle
            self.speed+=max(-3*dt,min(3*dt,target-self.speed))
        else:self.speed=math.copysign(max(0.,abs(self.speed)-3*dt),self.speed)
        angular=self.speed*math.tan(self.steer*math.radians(32))/max(2.,self.half_length*1.1)
        count=max(1,math.ceil(abs(self.speed*dt)/.04),math.ceil(abs(angular*dt)/math.radians(.5)))
        for _ in range(count):
            angle=self.heading+angular*dt/count
            x=self.x-self.speed*math.cos((angle+self.heading)/2)*dt/count
            y=self.y-self.speed*math.sin((angle+self.heading)/2)*dt/count
            shape=self.footprint(x,y,angle)
            left,right=min(p[0] for p in shape),max(p[0] for p in shape)
            bottom,top=min(p[1] for p in shape),max(p[1] for p in shape)
            hit=next((name for rect,name in obstacles if not (
                rect[0]>right or rect[2]<left or rect[1]>top or rect[3]<bottom) and intersects(shape,rect)),None)
            if hit is not None:
                self.speed=0.;self.event='collision';self.hit=hit;return
            wheels=corners(x,y,angle,self.half_length*.6,self.half_width*.7)
            if not all(supported(a,b) for a,b in wheels):self.reset('water_reset');return
            self.x,self.y,self.heading=x,y,angle

    def state(self):
        return dict(position_m=[self.x,self.y],heading_deg=math.degrees(self.heading),
                    speed_kmh=round(self.speed*3.6,1),event=self.event,collision_path=self.hit,reset_count=self.reset_count)
