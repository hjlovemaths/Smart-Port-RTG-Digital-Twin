"""USD-only regression: smooth scheduling, cargo hold/release and reset."""
from pathlib import Path
from pxr import Gf,Tf,Usd,UsdGeom
from ship_sea_motion import SeaMotion,SHIPS,SEA,COMPANION_ROOTS

root=Path(__file__).resolve().parents[2]
s=Usd.Stage.Open(str(root/'omniverse/scenes/smart_port.usda'))
original=s.GetRootLayer().ExportToString()
water=list(UsdGeom.Mesh(s.GetPrimAtPath(SEA)).GetPointsAttr().Get())
paths=[*SHIPS,*(p for p,_ in COMPANION_ROOTS)]
def transforms():
    c=UsdGeom.XformCache()
    return {p:c.GetLocalToWorldTransform(s.GetPrimAtPath(p)) for p in paths}
before=transforms()
look_paths=[
    '/World/Environment/Looks/HarborWater/'+shader+'.inputs:'+name
    for shader,name in (('NormalTexture','file'),('NormalTexture','scale'),
                        ('NormalTexture','bias'),('RoughnessTexture','file'),
                        ('UVTransform','scale'))
]
def water_look():
    return {path:str(s.GetAttributeAtPath(path).Get()) for path in look_paths}
static_look=water_look()
assert 'harbor_water_multiscale_normal.png' in static_look[look_paths[0]]
assert 'harbor_water_multiscale_roughness.png' in static_look[look_paths[3]]
m=SeaMotion(s)
assert water_look()==static_look
m.set_quality(True)
counts={p:0 for p in SHIPS}
notices=[]
subscription=Tf.Notice.Register(Usd.Notice.ObjectsChanged,lambda notice,sender:notices.append(1),s)
for _ in range(360):
    old={p:data[0].Get() for p,data in m.ships.items()}
    notices.clear()
    m.tick(1/60)
    assert len(notices)<=1,'One animation tick must not notify USD once per ship'
    for p,data in m.ships.items(): counts[p]+=data[0].Get()!=old[p]
subscription.Revoke()
assert all(178<=c<=181 for c in counts.values()),counts
assert m.uv_translation.Get()!=m.uv_origin
assert all(transforms()[p]!=before[p] for p in paths)
# Paired cargo must apply exactly the same world delta as its ship.
after=transforms()
for cargo,ship in COMPANION_ROOTS:
    assert Gf.IsClose(before[cargo].GetInverse()*after[cargo],before[ship].GetInverse()*after[ship],1e-8)
    m.hold(ship)
    held=transforms()
    assert Gf.IsClose(held[ship],before[ship],1e-8)
    assert Gf.IsClose(held[cargo],before[cargo],1e-8)
    for _ in range(30): m.tick(1/60)
    assert Gf.IsClose(transforms()[cargo],before[cargo],1e-8)
    m.release(ship)
    for _ in range(30): m.tick(1/60)
    assert transforms()[cargo]!=before[cargo]
m.enabled=False
assert transforms()==before
assert water_look()==static_look
m.enabled=True
for _ in range(90):m.tick(1/60)
assert transforms()!=before
m.close()
assert transforms()==before
assert water_look()==static_look
assert list(UsdGeom.Mesh(s.GetPrimAtPath(SEA)).GetPointsAttr().Get())==water
assert s.GetRootLayer().ExportToString()==original
print('PASS: textured water identical with animation on/off/closed; six ships 30 Hz; cargo follows/holds; scene restored without root writes')
