"""Check planned IECS truck paths against the composed USD quay crane supports."""

import math

from pxr import Usd, UsdGeom

from iecs_collision import overlap, rectangle


def quay_route_obstacle(stage, points, cargo=None):
    truck_top = 1.11  # Measured parked truck roof is at z=1.005 m.
    if cargo:
        height = cargo.get('visual_size',(0.,0.,1.))[2]
        truck_top = max(truck_top,cargo['carry_height']+height/2+.1)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    obstacles = []
    cranes = stage.GetPrimAtPath('/World/STSControls')
    if not cranes:
        return None
    for crane in cranes.GetChildren():
        support = stage.GetPrimAtPath(str(crane.GetPath())+'/Gantry/RunningGear')
        for side in support.GetChildren() if support else ():
            for part in side.GetChildren():
                if not (part.GetName().startswith('Foot_') or part.GetName()=='ConnectingBeam'):
                    continue
                if UsdGeom.Imageable(part).ComputeVisibility()=='invisible':
                    continue
                bounds = cache.ComputeWorldBound(part).ComputeAlignedRange()
                if bounds.IsEmpty() or bounds.GetMin()[2]>truck_top:
                    continue
                x1,y1,_ = bounds.GetMin()
                x2,y2,_ = bounds.GetMax()
                obstacles.append((crane.GetName(),x1,y1,x2,y2,[(x1,y1),(x2,y1),(x2,y2),(x1,y2)]))
    length,width = 8.2,1.8
    radius = math.hypot(length,width)/2
    for start, end in zip(points,points[1:]):
        dx,dy = end[0]-start[0],end[1]-start[1]
        segment = math.hypot(dx,dy)
        if segment<1e-6:
            continue
        # The existing truck envelope includes cab overhang and turning clearance.
        nearby = [(name,poly) for name,x1,y1,x2,y2,poly in obstacles
                  if max(start[0],end[0])+radius>=x1 and min(start[0],end[0])-radius<=x2
                  and max(start[1],end[1])+radius>=y1 and min(start[1],end[1])-radius<=y2]
        if not nearby:
            continue
        heading = math.degrees(math.atan2(dx,-dy))
        samples = math.ceil(segment/.25)
        for i in range(samples+1):
            truck = rectangle((start[0]+dx*i/samples,start[1]+dy*i/samples),heading,length=length,width=width)
            for name,poly in nearby:
                if overlap(truck,poly):
                    return name
    return None
