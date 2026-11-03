"""Task-owned RTG motion and measured, continuous 20/40-foot telescoping."""
import math
from pxr import Gf,Sdf,Usd,UsdGeom,Vt
from ship_transfer_plan import box
from rtg_live_control import (
    GANTRY_PATH,TROLLEY_PATH,HOIST_PATH,ROPE_SYSTEM_PATH,
    SPREADER_TELESCOPE_TRAVEL_M,SPREADER_TELESCOPE_SPEED_M_S,
    SPREADER_LIVE_TELESCOPE_OP_SUFFIX,
)


def tr(v):return Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(v))


def read_spreader_size_ft(stage):
    values=[]
    for side,sign in (('FRONT',1),('REAR',-1)):
        prim=stage.GetPrimAtPath(HOIST_PATH+'/ANIM_CTRL_SPREADER_TELESCOPE_'+side)
        if not prim:return None
        attr=prim.GetAttribute('xformOp:translate:'+SPREADER_LIVE_TELESCOPE_OP_SUFFIX)
        value=attr.Get() if attr else None
        offset=sign*value[1] if value is not None else 0.
        if not math.isfinite(offset) or not -.001<=offset<=SPREADER_TELESCOPE_TRAVEL_M+.001:return None
        values.append(offset)
    if abs(values[0]-values[1])>.001:return None
    return 40-20*(sum(values)/2)/SPREADER_TELESCOPE_TRAVEL_M


class SessionProperties:
    """Temporarily own specific strongest-session properties, restoring exactly.

    A task sublayer cannot override an xformOpOrder authored by manual control
    in the session root. Back up only owned properties, never the entire session.
    """
    def __init__(self,stage):
        self.stage=stage;self.root=stage.GetSessionLayer()
        self.backup=Sdf.Layer.CreateAnonymous('rtg_task_property_backup.usda')
        self.saved={};self.missing_prims=set()

    def write(self,prim,name,kind,value):
        path=prim.GetPath().AppendProperty(name)
        if path not in self.saved:
            present=self.root.GetPropertyAtPath(path) is not None
            self.saved[path]=present
            if present:
                Sdf.CreatePrimInLayer(self.backup,prim.GetPath())
                Sdf.CopySpec(self.root,path,self.backup,path)
            parent=prim.GetPath()
            while parent!=Sdf.Path.absoluteRootPath:
                if not self.root.GetPrimAtPath(parent):self.missing_prims.add(parent)
                parent=parent.GetParentPath()
        with Usd.EditContext(self.stage,self.root):
            attribute=prim.CreateAttribute(name,kind,custom=False)
            attribute.Set(value)
        return attribute

    def close(self):
        for path,present in self.saved.items():
            if present:Sdf.CopySpec(self.backup,path,self.root,path)
            else:
                with Usd.EditContext(self.stage,self.root):
                    self.stage.GetPrimAtPath(path.GetPrimPath()).RemoveProperty(path.name)
        for path in sorted(self.missing_prims,key=lambda p:p.pathElementCount,reverse=True):
            spec=self.root.GetPrimAtPath(path)
            if spec and not spec.properties and not spec.nameChildren and set(spec.ListInfoKeys())<={'specifier'}:
                edit=Sdf.BatchNamespaceEdit();edit.Add(path,Sdf.Path.emptyPath);self.root.Apply(edit)
        self.saved.clear();self.missing_prims.clear()


class VisualRTG:
    def __init__(self,stage,size_ft=20):
        if size_ft not in (20,40):raise ValueError('RTG吊具尺型须为20或40尺')
        self.stage=stage;self.properties=SessionProperties(stage);self.target_size_ft=size_ft
        self.ops=[];self.bases=[];self.directions=[];self.offset=[0.,0.,0.];self.goal=list(self.offset)
        self.telescopes=[];self.telescoping_offset=0.
        try:self.prepare()
        except Exception:self.close();raise

    def task_matrix(self,prim,base,live_name=None,live_value=None):
        xf=UsdGeom.Xformable(prim)
        op=xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'shipTransfer');op.Set(base)
        order=[op.GetOpName()]
        if live_name:
            self.properties.write(prim,live_name,Sdf.ValueTypeNames.Double3,live_value)
            order.append(live_name)
        xf.SetXformOpOrder([op])
        self.properties.write(prim,'xformOpOrder',Sdf.ValueTypeNames.TokenArray,Vt.TokenArray(order))
        return op

    def prepare(self):
        stage=self.stage
        for p in Usd.PrimRange(stage.GetPrimAtPath(GANTRY_PATH).GetParent()):
            for op in UsdGeom.Xformable(p).GetOrderedXformOps():
                a=op.GetAttr()
                if a.Get() is None and a.GetTimeSamples():a.Set(a.Get(stage.GetStartTimeCode()))
        cache=UsdGeom.XformCache()
        for path,axis in zip((GANTRY_PATH,TROLLEY_PATH,HOIST_PATH),((0,1,0),(1,0,0),(0,0,1))):
            prim=stage.GetPrimAtPath(path)
            self.directions.append(cache.GetLocalToWorldTransform(prim.GetParent()).TransformDir(Gf.Vec3d(axis)))
            base=UsdGeom.Xformable(prim).GetLocalTransformation()
            self.ops.append(self.task_matrix(prim,base));self.bases.append(base)
        values=[];live_name='xformOp:translate:'+SPREADER_LIVE_TELESCOPE_OP_SUFFIX
        for side,sign in (('FRONT',1),('REAR',-1)):
            prim=stage.GetPrimAtPath(HOIST_PATH+'/ANIM_CTRL_SPREADER_TELESCOPE_'+side)
            current=prim.GetAttribute(live_name)
            value=Gf.Vec3d(current.Get()) if current and current.Get() is not None else Gf.Vec3d(0)
            contraction=sign*value[1]
            if not math.isfinite(contraction) or not -.001<=contraction<=SPREADER_TELESCOPE_TRAVEL_M+.001:
                raise ValueError('当前RTG吊具伸缩状态无效，请先复位吊具')
            values.append(contraction)
            base=Gf.Matrix4d(1)
            for old_op in UsdGeom.Xformable(prim).GetOrderedXformOps():
                if old_op.GetOpName()!=live_name:base=old_op.GetOpTransform(Usd.TimeCode.Default())*base
            self.task_matrix(prim,base,live_name,value)
            self.telescopes.append((prim,base,sign,live_name))
        if abs(values[0]-values[1])>.001:raise ValueError('RTG吊具前后端伸缩不同步，拒绝取箱')
        self.telescoping_offset=sum(values)/2
        cache=UsdGeom.XformCache();feet=[]
        for p in Usd.PrimRange(stage.GetPrimAtPath(HOIST_PATH)):
            if p.IsA(UsdGeom.Boundable) and 'RTG_SPREAD_REDRAW_red_corner_lock_foot_' in str(p.GetPath()):
                b=box(stage,p.GetPath());feet.append(Gf.Vec3d(b.GetMidpoint()[0],b.GetMidpoint()[1],b.GetMin()[2]))
        if len(feet)!=4:raise ValueError('RTG吊具四角锁点不完整，拒绝使用包围盒猜测吊点')
        self.local=cache.GetLocalToWorldTransform(stage.GetPrimAtPath(HOIST_PATH)).GetInverse().Transform(sum(feet,Gf.Vec3d(0))/4)
        self.origin=self.contact();self.rope=UsdGeom.BasisCurves(stage.GetPrimAtPath(ROPE_SYSTEM_PATH))
        self.points=list(self.rope.GetPointsAttr().Get());self.lower=[];index=0
        for count in self.rope.GetCurveVertexCountsAttr().Get():
            self.lower.append(min(range(index,index+count),key=lambda i:self.points[i][2]));index+=count
        self.inverse=cache.GetLocalToWorldTransform(self.rope.GetPrim()).GetInverse()

    @property
    def current_size_ft(self):
        values=[]
        for prim,base,sign,name in self.telescopes:
            value=prim.GetAttribute(name).Get()
            if value is None:return None
            values.append(sign*value[1])
        if len(values)!=2 or abs(values[0]-values[1])>.001:return None
        return 40-20*(sum(values)/2)/SPREADER_TELESCOPE_TRAVEL_M

    @property
    def spreader_ready(self):
        size=self.current_size_ft
        if size is None or abs(size-self.target_size_ft)>1e-4:return False
        for prim,base,sign,name in self.telescopes:
            value=prim.GetAttribute(name).Get()
            expected=tr(value)*base
            actual=UsdGeom.Xformable(prim).GetLocalTransformation()
            if any(abs(actual[i][j]-expected[i][j])>1e-5 for i in range(4) for j in range(4)):return False
        return True

    def tick_spreader(self,dt):
        target=SPREADER_TELESCOPE_TRAVEL_M if self.target_size_ft==20 else 0.
        delta=target-self.telescoping_offset
        self.telescoping_offset+=max(-SPREADER_TELESCOPE_SPEED_M_S*dt,min(SPREADER_TELESCOPE_SPEED_M_S*dt,delta))
        for prim,base,sign,name in self.telescopes:
            self.properties.write(prim,name,Sdf.ValueTypeNames.Double3,Gf.Vec3d(0,sign*self.telescoping_offset,0))
        return self.spreader_ready

    def contact(self):
        return UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(HOIST_PATH)).Transform(self.local)

    def command(self,point):
        if not (-6<=point[0]<=8 and -.5<=point[1]<=505 and .1<=point[2]<=9):raise ValueError('RTG目标超出港区可视工作范围')
        delta=point-self.origin;goal=[Gf.Dot(delta,v)/Gf.Dot(v,v) for v in self.directions]
        if not (-12<=goal[1]<=12 and -9<=goal[2]<=2):raise ValueError('RTG目标超出原有可视行程限制')
        self.goal=goal

    def tick(self,dt):
        for i,speed in enumerate((8.,1.3,.8)):
            delta=self.goal[i]-self.offset[i];self.offset[i]+=max(-speed*dt,min(speed*dt,delta))
            v=[0.,0.,0.];v[(1,0,2)[i]]=self.offset[i];self.ops[i].Set(self.bases[i]*tr(v))
        delta=self.inverse.TransformDir(self.directions[2]*self.offset[2]);points=list(self.points)
        for i in self.lower:points[i]=Gf.Vec3f(Gf.Vec3d(points[i])+delta)
        self.properties.write(self.rope.GetPrim(),'points',self.rope.GetPointsAttr().GetTypeName(),Vt.Vec3fArray(points))
        return all(abs(a-b)<1e-5 for a,b in zip(self.offset,self.goal))

    def close(self):self.properties.close()
