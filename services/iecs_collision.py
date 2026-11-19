"""Conservative sampled 2D truck swept envelopes (not a physics solver)."""
import math


def rectangle(position,heading,length=7.9,width=1.6):
    t=math.radians(heading);long=(math.sin(t),-math.cos(t));side=(math.cos(t),math.sin(t))
    return [(position[0]+a*length/2*long[0]+b*width/2*side[0],
             position[1]+a*length/2*long[1]+b*width/2*side[1]) for a,b in ((1,1),(1,-1),(-1,-1),(-1,1))]


def overlap(a,b):
    for poly in (a,b):
        for p,q in zip(poly,poly[1:]+poly[:1]):
            axis=(-(q[1]-p[1]),q[0]-p[0])
            x=[v[0]*axis[0]+v[1]*axis[1] for v in a]
            y=[v[0]*axis[0]+v[1]*axis[1] for v in b]
            if max(x)<min(y) or max(y)<min(x):return False
    return True


def swept_conflict(vehicle,end,heading,others):
    start=vehicle['position'];length=math.dist(start[:2],end[:2])
    # Two truck rectangles cannot touch if their centres are more than their
    # combined diagonal radii apart. Include the entire sampled translation.
    nearby=[other for other in others if other['id']!=vehicle['id'] and other['status'] not in ('departed','waiting_gate')
            and math.dist(start[:2],other['position'][:2])<=length+8.1]
    if not nearby:return None
    turn=(heading-vehicle['heading']+180)%360-180
    samples=max(1,math.ceil(length/.15),math.ceil(abs(turn)/2))
    obstacles=[(other['id'],rectangle(other['position'],other['heading'])) for other in nearby]
    for i in range(samples+1):
        t=i/samples
        rect=rectangle([start[j]+(end[j]-start[j])*t for j in range(2)],vehicle['heading']+turn*t)
        for identity,obstacle in obstacles:
            if overlap(rect,obstacle):return identity
    return None
