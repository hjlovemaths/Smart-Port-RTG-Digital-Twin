"""Small task-layer cab interior for the exterior-only yard truck asset."""
import math
from pxr import Gf,Usd,UsdGeom


class DriverCab:
    def __init__(self,stage,truck,root):
        self.stage=stage;self.layer=stage.GetEditTarget().GetLayer();self.windows=[]
        for prim in Usd.PrimRange(truck,Usd.TraverseInstanceProxies()):
            if any(name in prim.GetName() for name in ('white_cab_body','front_black_windshield','side_window')):
                self.windows.append(prim)
        self.root=UsdGeom.Xform.Define(stage,root);self.op=self.root.AddTransformOp()
        def cube(name,center,size,color):
            mesh=UsdGeom.Cube.Define(stage,root+'/'+name);mesh.CreateSizeAttr(1)
            mesh.AddTranslateOp().Set(Gf.Vec3d(center));mesh.AddScaleOp().Set(Gf.Vec3f(size))
            mesh.CreateDisplayColorAttr([Gf.Vec3f(color)])
        trim=(.07,.09,.11);white=(.75,.8,.82)
        cube('Dashboard',(-2.98,0,-.005),(.25,.94,.16),trim)
        cube('Roof',(-2.59,0,.49),(1.2,1.03,.05),white)
        cube('BackWall',(-2.02,0,.1),(.05,1.01,.75),white)
        for side in (-1,1):
            cube('FrontPillar'+str(side).replace('-','M'),(-3.14,side*.49,.24),(.055,.055,.5),white)
            cube('Door'+str(side).replace('-','M'),(-2.58,side*.49,-.12),(1.13,.055,.3),trim)
            cube('WindowFrame'+str(side).replace('-','M'),(-2.14,side*.49,.27),(.055,.055,.4),white)
        wheel=UsdGeom.Xform.Define(stage,root+'/SteeringWheel');wheel.AddTranslateOp().Set(Gf.Vec3d(-2.86,.23,.10))
        self.steer_op=wheel.AddRotateXOp()
        mesh=UsdGeom.Mesh.Define(stage,root+'/SteeringWheel/Rim');points=[];indices=[]
        for i in range(32):
            angle=2*math.pi*i/32
            for radius in (.075,.105):points.append(Gf.Vec3f(0,radius*math.cos(angle),radius*math.sin(angle)))
            indices.extend((2*i,2*i+1,(2*((i+1)%32))+1,2*((i+1)%32)))
        mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr([4]*32);mesh.CreateFaceVertexIndicesAttr(indices)
        mesh.CreateSubdivisionSchemeAttr('none');mesh.CreateDoubleSidedAttr(True);mesh.CreateDisplayColorAttr([Gf.Vec3f(.025)])

    def apply(self,inside,pose,steer):
        with Usd.EditContext(self.stage,self.layer):
            self.op.Set(pose);self.steer_op.Set(steer*70)
            UsdGeom.Imageable(self.root).CreateVisibilityAttr().Set('inherited' if inside else 'invisible')
            for prim in self.windows:UsdGeom.Imageable(prim).CreateVisibilityAttr().Set('invisible' if inside else 'inherited')
