from pxr import Sdf,Usd,UsdGeom,Gf
from pathlib import Path
from iecs_ship_pickup import ShipPickup

stage=Usd.Stage.Open(str(Path(__file__).resolve().parents[1]/'scenes/smart_port.usda'))
layer=Sdf.Layer.CreateAnonymous('pickup_test');stage.GetSessionLayer().subLayerPaths.insert(0,layer.identifier)
pickup=ShipPickup(stage,layer)
try:
    print('SOURCE',pickup.source,'HEIGHT',pickup.height)
    pickup.configure([-238.15,pickup.source[1],.085],.685)
    for _ in range(20000):
        if pickup.step(.1):break
    assert pickup.finished,pickup.phase
    actual=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(
        stage.GetPrimAtPath(pickup.box_path)).ComputeAlignedRange().GetMidpoint()
    expected=Gf.Vec3d(-238.15,pickup.source[1],.685+pickup.height/2)
    assert (actual-expected).GetLength()<.002,(actual,expected)
    print('PASS: real STS axes + real ship box lock/lift/transfer/release')
finally:pickup.close()
