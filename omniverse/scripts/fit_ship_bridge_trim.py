"""Seat the two blue bridge trims against the roof fascia marked by the user."""
from pathlib import Path
from pxr import Gf, Usd, UsdGeom

ASSET=Path(__file__).resolve().parents[1]/'assets/ship/Custom_Feeder_Ship.usdc'
BASE='/CustomFeederShip/Model/CODEx_left_reference_feeder_ship_clone_v2_forward_bridge_'

def fit():
    stage=Usd.Stage.Open(str(ASSET))
    cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    roof=cache.ComputeWorldBound(stage.GetPrimAtPath(BASE+'overha')).ComputeAlignedBox()
    for name,sign in (('window_001',1),('window_002',-1)):
        prim=stage.GetPrimAtPath(BASE+name)
        box=cache.ComputeWorldBound(prim).ComputeAlignedBox()
        # Inner face slightly overlaps the white roof fascia, avoiding a gap.
        target_y=(roof.GetMax()[1] if sign>0 else roof.GetMin()[1])+sign*(box.GetSize()[1]/2-.003)
        delta=Gf.Vec3d(0,target_y-box.GetMidpoint()[1],
                       roof.GetMidpoint()[2]-box.GetMidpoint()[2])
        parent=UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent())
        local_delta=parent.GetInverse().TransformDir(delta)
        xf=UsdGeom.Xformable(prim)
        ops=xf.GetOrderedXformOps()
        op=next((o for o in ops if o.GetOpName()=='xformOp:translate:fitBridgeTrim'),None)
        if op is None:
            op=xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble,'fitBridgeTrim')
            op.Set(Gf.Vec3d(0))
        op.Set(op.Get()+local_delta)
        xf.SetXformOpOrder([op]+[o for o in ops if o!=op])
        verify=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(prim).ComputeAlignedBox()
        gap=(verify.GetMin()[1]-roof.GetMax()[1]) if sign>0 else (roof.GetMin()[1]-verify.GetMax()[1])
        assert abs(gap+.003)<1e-5,(name,gap)
        assert abs(verify.GetMidpoint()[2]-roof.GetMidpoint()[2])<1e-5
        print(name,'Y',box.GetMidpoint()[1],'->',verify.GetMidpoint()[1],'face gap',gap)
    stage.GetRootLayer().Save()

if __name__=='__main__':
    fit()
