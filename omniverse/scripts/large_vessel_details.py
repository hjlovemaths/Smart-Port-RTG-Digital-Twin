"""Curved hull ends and low-cost nautical deck fittings for the large vessel."""
import math
from pxr import Gf, UsdGeom


def line_tubes(mesh,path,lines,width,mat):
    """Batch solid handrail tubes; visible in RTX modes without curve support."""
    points=[];faces=[];radius=width/2
    for line in lines:
        for start,end in zip(line,line[1:]):
            start,end=Gf.Vec3d(*start),Gf.Vec3d(*end)
            direction=(end-start).GetNormalized()
            ref=Gf.Vec3d(1,0,0) if abs(direction[2])>.9 else Gf.Vec3d(0,0,1)
            u=Gf.Cross(direction,ref).GetNormalized()*radius
            v=Gf.Cross(direction,u).GetNormalized()*radius
            offset=len(points)
            for p in (start,end):
                for i in range(8):
                    a=math.tau*i/8
                    points.append(tuple(p+math.cos(a)*u+math.sin(a)*v))
            for i in range(8):faces.append((offset+i,offset+(i+1)%8,offset+8+(i+1)%8,offset+8+i))
    return mesh(path,points,faces,mat)


def smooth_stations(keys):
    """Monotone cubic interpolation preserves the designed beam and length."""
    slopes=[(b[1]-a[1])/(b[0]-a[0]) for a,b in zip(keys,keys[1:])]
    tangents=[slopes[0]]
    for a,b in zip(slopes,slopes[1:]):
        tangents.append(2*a*b/(a+b) if a*b>0 else 0.)
    tangents.append(slopes[-1])
    out=[]
    for i,((y0,w0),(y1,w1)) in enumerate(zip(keys,keys[1:])):
        steps=max(2,math.ceil((y1-y0)/2))
        for j in range(steps):
            t=j/steps;h=y1-y0
            w=(2*t**3-3*t*t+1)*w0+(t**3-2*t*t+t)*h*tangents[i]+(-2*t**3+3*t*t)*w1+(t**3-t*t)*h*tangents[i+1]
            out.append((y0+t*h,w))
    return out+[keys[-1]]


def deck_height(y,deck):
    return deck + 1.2*max(0.,min(1.,(14-y)/18)) + .65*max(0.,min(1.,(y-236)/8))


def build_fittings(stage,ship,mesh,cube,mats,deck,stations):
    hull,steel,white,dark,orange,glass,bronze=mats

    def cylinder(path,center,radius,length,mat,axis='Z'):
        c=UsdGeom.Cylinder.Define(stage,path)
        c.CreateAxisAttr(axis);c.CreateRadiusAttr(radius);c.CreateHeightAttr(length)
        c.AddTranslateOp().Set(Gf.Vec3d(*center))
        from pxr import UsdShade
        UsdShade.MaterialBindingAPI.Apply(c.GetPrim()).Bind(mat)
        return c

    def curves(path,lines,width,mat):
        return line_tubes(mesh,path,lines,width,mat)

    def torus(path,center,radius,tube,mat,normal='Z'):
        pts=[];faces=[];n=24;k=8
        for i in range(n):
            a=i*math.tau/n
            for j in range(k):
                b=j*math.tau/k
                v=((radius+tube*math.cos(b))*math.cos(a),(radius+tube*math.cos(b))*math.sin(a),tube*math.sin(b))
                if normal=='X':v=(v[2],v[0],v[1])
                if normal=='Y':v=(v[0],v[2],v[1])
                pts.append(tuple(center[d]+v[d] for d in range(3)))
        for i in range(n):
            for j in range(k):faces.append((i*k+j,((i+1)%n)*k+j,((i+1)%n)*k+(j+1)%k,i*k+(j+1)%k))
        mesh(path,pts,faces,mat)

    base=ship+'/EndDetails'
    # Railing follows the actual curved gunwale, including the stern transom.
    for end,predicate in [('Bow',lambda y:y<=28),('Stern',lambda y:y>=232)]:
        sample=[(y,w) for y,w in stations if predicate(y)]
        outline=([(-w+.12,y) for y,w in reversed(sample)]+[(w-.12,y) for y,w in sample] if end=='Bow' else
                 [(-w+.12,y) for y,w in sample]+[(w-.12,y) for y,w in reversed(sample)])
        lines=[]
        for h in (.55,1.05):
            lines.append([(x,y,deck_height(y,deck)+.16+h) for x,y in outline])
        for x,y in outline[::2]:
            z=deck_height(y,deck)+.16
            lines.append([(x,y,z),(x,y,z+1.1)])
        curves(base+'/'+end+'/CurvedGuardrails',lines,.045,white)

    # Mooring winches: drums, flanges, motors and cable leads.
    for end,y in [('Bow',6.),('Stern',240.)]:
        z=deck_height(y,deck)+.18
        for side in (-1,1):
            x=side*(4.3 if end=='Bow' else 7.)
            p=base+f'/{end}/Winch'+('Port' if side<0 else 'Starboard')
            cube(p+'/Foundation',(x,y,z+.18),(3.4,3.3,.36),steel)
            for dx in (-1.15,1.15):
                cube(p+('/BearingL' if dx<0 else '/BearingR'),(x+dx,y,z+.65),(.24,1.65,1.),dark)
            cylinder(p+'/Drum',(x,y,z+1.1),.52,2.5,steel,'X')
            for j,dx in enumerate((-.9,-.35,.35,.9)):
                cylinder(p+f'/Flange{j}',(x+dx,y,z+1.1),.82,.11,dark,'X')
            cylinder(p+'/Motor',(x+1.55,y,z+.7),.4,.7,hull,'X')
            lines=[]
            for dx in (-.65,.65):
                lines.append([(x+dx,y-.1,z+1.55),(x+dx,y-2.5,z+.3),(x+dx,y-3.5,z+.3)])
            curves(p+'/MooringLines',lines,.065,bronze)
            # Twin bitts aft of each winch.
            for k,dx in enumerate((-.55,.55)):
                q=p+f'/Bitt{k}'
                cylinder(q+'/Post',(x+dx,y+2.05,z+.55),.22,1.1,dark)
                cylinder(q+'/Cap',(x+dx,y+2.05,z+1.05),.32,.13,white)
            cube(p+'/BittBed',(x,y+2.05,z+.1),(2.,.9,.2),steel)

    # Hawse rings, hanging stockless anchors and visible chains on both bows.
    for side in (-1,1):
        p=base+'/Bow/'+('PortAnchor' if side<0 else 'StarboardAnchor')
        # At Y=6 the curved hull is approximately 8.8 m from the centreline.
        width=min(stations,key=lambda yw:abs(yw[0]-6.))[1]
        x=side*width*.98;y=6.;z=deck-2.1
        cylinder(p+'/HawseRecess',(x,y,z),.58,.12,dark,'X')
        torus(p+'/HawseRim',(x+side*.08,y,z),.58,.085,steel,'X')
        chain=[]
        for i in range(10):
            c=(x+side*.16,y-.035*i,z-.18*i)
            torus(p+f'/ChainLink{i}',c,.12,.035,dark,'X' if i%2==0 else 'Y')
        cube(p+'/Shank',(x+side*.23,y-.35,z-2.1),(.16,.2,1.5),dark)
        cube(p+'/Crown',(x+side*.23,y-.35,z-2.8),(.2,1.4,.22),dark)
        for s in (-1,1):
            yy=y-.35+s*.7
            points=[(x,yy,z-2.8),(x+side*.65,yy+s*.25,z-2.1),(x+side*.8,yy-s*.3,z-2.2),
                    (x+side*.1,yy-s*.2,z-2.9)]
            mesh(p+('/FlukeF' if s<0 else '/FlukeA'),points,[(0,1,2,3)],dark)

    # Fairleads on the aft corners and closed stern-edge access platform.
    for side in (-1,1):
        p=base+'/Stern/'+('Port' if side<0 else 'Starboard')
        width=min(stations,key=lambda yw:abs(yw[0]-241.5))[1]
        torus(p+'/Fairlead',(side*width*.97,241.5,deck_height(241.5,deck)+.55),.43,.1,dark,'X')
        cube(p+'/Platform',(side*9.8,242.8,deck+.65),(3.,1.2,.2),steel)
    # Foremast and aft navigation mast with antenna booms and lanterns.
    for end,y,height in [('Bow',.8,5.8),('Stern',242.5,4.4)]:
        p=base+'/'+end+'/NavigationMast';z=deck_height(y,deck)+.18
        cylinder(p+'/Foot',(0,y,z+.2),.55,.4,steel)
        cylinder(p+'/Lower',(0,y,z+height*.3),.17,height*.6,white)
        cylinder(p+'/Upper',(0,y,z+height*.78),.09,height*.45,white)
        cube(p+'/CrossArm',(0,y,z+height*.8),(3.,.12,.12),white)
        cylinder(p+'/Lantern',(0,y,z+height),.18,.32,white)
        for side in (-1,1):cylinder(p+('/LightPort' if side<0 else '/LightStarboard'),(side*1.3,y,z+height*.85),.12,.28,orange if side<0 else glass)
        curves(p+'/Rigging',[[(0,y,z+height*.9),(side*2.,y+2.,z+.2)] for side in (-1,1)],.025,dark)

    # Submerged bulb, shaft, propeller and rudder remain part of the model.
    bulb=UsdGeom.Sphere.Define(stage,base+'/Bow/BulbousForefoot')
    bulb.AddTranslateOp().Set(Gf.Vec3d(0,1.,-5.5));bulb.AddScaleOp().Set(Gf.Vec3f(2.2,4.5,2.1))
    from pxr import UsdShade
    UsdShade.MaterialBindingAPI.Apply(bulb.GetPrim()).Bind(hull)
    cylinder(base+'/Stern/PropellerShaft',(0,236.8,-7.8),.48,7.,bronze,'Y')
    cylinder(base+'/Stern/PropellerHub',(0,240.,-7.8),.75,1.6,bronze,'Y')
    pts=[];faces=[]
    for i in range(5):
        a=i*math.tau/5
        start=len(pts)
        for r,t,y in ((.6,-.12,239.4),(2.8,-.25,239.8),(3.1,.16,240.1),(1.1,.55,240.3)):
            pts.append((r*math.cos(a+t),y,-7.8+r*math.sin(a+t)))
        faces.append(tuple(range(start,start+4)))
    mesh(base+'/Stern/PropellerBlades',pts,faces,bronze)
    mesh(base+'/Stern/Rudder',[(x,y,z) for x in (-.22,.22) for y,z in ((241.,-4.3),(244.,-4.8),(243.7,-11.8),(241.5,-11.4))],
         [(0,1,2,3),(4,7,6,5),(0,4,5,1),(1,5,6,2),(2,6,7,3),(3,7,4,0)],hull)

    # Side windows, bridge-wing glazing and external ladders.
    for side in (-1,1):
        for level,(w,l) in enumerate(((28.,13.),(25.,12.),(23.,11.),(21.,10.))):
            for j in range(5):
                cube(ship+f'/Bridge/SideWindow_{side}_{level}_{j}'.replace('-','M'),
                     (side*(w/2+.025),162.8+(j-2)*1.9,deck+1.7+level*2.7),(.08,1.,.8),glass)
        cube(ship+('/Bridge/WingGlassPort' if side<0 else '/Bridge/WingGlassStarboard'),
             (side*14.54,162.8,deck+12.6),(.1,10.2,1.25),glass)
        lines=[]
        x=side*10.8
        for dx in (-.27,.27):lines.append([(x+dx,168.1,deck+.2),(x+dx,168.1,deck+10.8)])
        for i in range(34):
            z=deck+.5+i*.3
            lines.append([(x-.27,168.1,z),(x+.27,168.1,z)])
        curves(ship+('/Bridge/AccessLadderPort' if side<0 else '/Bridge/AccessLadderStarboard'),lines,.04,white)
