"""Close the telescopic frame when the task moves STS heads to twin-20 ends."""
from pxr import Gf,UsdGeom
from ship_transfer_plan import box


def connect_twin20_frame(stage,hoist_path,overlap=.15):
    """Grow inward from each fixed outer end, retaining every lock coordinate.

    Only task-layer cube translations/scales are authored; the original asset,
    controller, centre sleeve and corner-lock transforms are untouched.
    """
    center=hoist_path+'/STS_TelescopicSpreader/Center/'
    core=box(stage,center+'MainSleeve').GetMidpoint()[1]
    joints=[]
    for end in ('Front','Rear'):
        root=hoist_path+'/STS_SpreaderEnd'+end
        sign=1 if box(stage,root+'/MainBeam').GetMidpoint()[1]>core else -1
        for moving,fixed in (('InnerTelescopicBeam','MainSleeve'),
                             ('InnerTelescopicRailFront','SideSleeveFront'),
                             ('InnerTelescopicRailRear','SideSleeveRear')):
            path=root+'/'+moving;prim=stage.GetPrimAtPath(path)
            sleeve=box(stage,center+fixed);arm=box(stage,path)
            inner=arm.GetMin()[1] if sign>0 else arm.GetMax()[1]
            lip=sleeve.GetMax()[1] if sign>0 else sleeve.GetMin()[1]
            extension=max(0.,sign*(inner-lip)+overlap)
            # Clearance scaling maps local X to longitudinal world Y. Measure
            # that mapping instead of assuming the prototype's current scale.
            parent=UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent())
            axis=parent.TransformDir(Gf.Vec3d(1,0,0))
            if abs(axis[1])<1e-6:raise ValueError('STS伸缩梁纵向轴无效')
            shift=parent.GetInverse().TransformDir(Gf.Vec3d(0,-sign*extension/2,0))
            translate=prim.GetAttribute('xformOp:translate');scale=prim.GetAttribute('xformOp:scale')
            old_scale=scale.Get();new_scale=type(old_scale)(old_scale)
            new_scale[0]+=extension/abs(axis[1])
            translate.Set(Gf.Vec3d(translate.Get())+shift);scale.Set(new_scale)
            joints.append((path,center+fixed))
    return joints
