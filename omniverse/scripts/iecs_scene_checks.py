"""Read-only diagnostics for IECS visuals and painted scene datums."""
from pxr import Usd,UsdGeom
import math

MARK_ROOT='/World/SMART_PORT_REBUILT_YARD_LAYOUT'
MARKS=('SMART_PORT_CLEAN_BAY_MARKINGS','SMART_PORT_RTG_TIRE_TRACK_YELLOW_SOLID',
       'SMART_PORT_CLEAN_BAY_NUMBER_BACKINGS','SMART_PORT_CLEAN_BAY_NUMBER_DIGITS')


def inspect_scene(stage,containers=(),vehicles=()):
    errors=[];cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    for name in MARKS:
        path=MARK_ROOT+'/'+name;p=stage.GetPrimAtPath(path)
        if not p or not p.IsActive():errors.append('Missing bay element: '+path);continue
        if UsdGeom.Imageable(p).ComputeVisibility()=='invisible':errors.append('Hidden bay element: '+path)
        if not p.IsA(UsdGeom.Mesh) or not UsdGeom.Mesh(p).GetPointsAttr().Get():errors.append('Empty bay element: '+path)
    seen=set()
    for c in containers:
        path=c.get('visual_prim')
        if not path:errors.append('Container visual unbound: '+c['id']);continue
        if path in seen:errors.append('Duplicate container visual: '+path)
        seen.add(path);p=stage.GetPrimAtPath(path)
        if not p:errors.append('Missing container visual: '+path);continue
        if p.GetAttribute('iecs:containerId').Get()!=c['id']:
            errors.append('Container identity mismatch: '+c['id'])
        hidden=UsdGeom.Imageable(p).ComputeVisibility()=='invisible'
        exited=c['location'].startswith('EXIT/')
        if hidden!=exited:errors.append('Container visibility mismatch: '+c['id'])
        if not exited:
            b=cache.ComputeWorldBound(p).ComputeAlignedBox()
            if b.IsEmpty() or any(abs(b.GetMidpoint()[i]-c['position'][i])>.025 for i in range(3)):
                errors.append('Container position mismatch: '+c['id'])
            size=c.get('visual_size')
            if size and not b.IsEmpty():
                theta=math.radians(c['heading']);co=abs(math.cos(theta));si=abs(math.sin(theta))
                expected=(size[0]*co+size[1]*si,size[0]*si+size[1]*co,size[2])
                if any(abs(b.GetSize()[i]-expected[i])>.035 for i in range(3)):
                    errors.append('Container size/heading mismatch: '+c['id'])
            source=stage.GetPrimAtPath(c['source_prim'])
            if source and UsdGeom.Imageable(source).ComputeVisibility()!='invisible':
                errors.append('Duplicate source visible: '+c['id'])
    loaded={};by_id={c['id']:c for c in containers}
    for vehicle in vehicles:
        cid=vehicle.get('load')
        if not cid:continue
        if cid in loaded:errors.append('Container assigned to two trucks: '+cid)
        loaded[cid]=vehicle['id']
        if cid not in by_id:errors.append('Truck load record missing: '+cid)
        elif by_id[cid]['location']==vehicle['id'] and by_id[cid]['owner']!=vehicle['id']:
            errors.append('Truck/container ownership mismatch: '+cid)
    return {'ok':not errors,'errors':errors,'container_count':len(seen),
            'scope':'visibility, entity identity, geometry centers; not collision certification'}
