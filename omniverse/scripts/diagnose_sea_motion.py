"""Read-only diagnostic for the currently open Kit scene."""
import builtins
import json
from pathlib import Path
import omni.usd
from pxr import UsdShade,UsdGeom
from ship_sea_motion import SEA,SHIPS

s=omni.usd.get_context().get_stage()
motion=getattr(builtins,'_smart_port_sea_motion',None)
sea=s.GetPrimAtPath(SEA)
mat,_=UsdShade.MaterialBindingAPI(sea).ComputeBoundMaterial()
data={'stage':s.GetRootLayer().identifier,'seaMaterial':str(mat.GetPath()),
      'driver':None if motion is None else {'enabled':motion.enabled,'closed':motion.closed,'strength':motion.strength,'time':motion.time,'held':list(motion.held),'subscribed':motion.sub is not None},'shaders':{}}
for name in ('PreviewSurface','UVTransform','NormalTexture','RoughnessTexture'):
    p=s.GetPrimAtPath('/World/Environment/Looks/HarborWater/'+name)
    data['shaders'][name]={a.GetName():{'value':str(a.Get()),'connections':[str(x) for x in a.GetConnections()]} for a in p.GetAttributes() if a.GetName().startswith('inputs:')}
out=Path(__file__).resolve().parents[2]/'outputs/sea_motion'
out.mkdir(parents=True,exist_ok=True)
(out/'live_diagnostic.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
print('SEA_DIAGNOSTIC',data['driver'],data['seaMaterial'])
