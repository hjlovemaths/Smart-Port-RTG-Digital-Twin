"""Route footprint checks against the actual painted divider mesh, not a mock road."""
import math,sys,unittest
from pathlib import Path
from pxr import Gf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
from ship_transfer_plan import PARK,box
from transfer_truck_geometry import truck_loading
from transfer_truck_routes import ship_to_yard_routes,yard_to_ship_routes,return_routes
from sts_yard_delivery import rounded_route
from build_ground_traffic_network import QUAY_ROAD_EDGES,CROSS


def overlaps(a,b):
    for polygon in (a,b):
        for p,q in zip(polygon,polygon[1:]+polygon[:1]):
            axis=(p[1]-q[1],q[0]-p[0])
            x=[v[0]*axis[0]+v[1]*axis[1] for v in a]
            y=[v[0]*axis[0]+v[1]*axis[1] for v in b]
            if max(x)<=min(y)+1e-6 or max(y)<=min(x)+1e-6:return False
    return True


class TruckRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        cls.deck,cls.park,cls.offset=truck_loading(cls.stage,20,2.597)
        b=box(cls.stage,PARK);cls.local=Gf.Range3d(b.GetMin()-cls.park,b.GetMax()-cls.park)
        mesh=UsdGeom.Mesh(cls.stage.GetPrimAtPath('/World/GROUND_TRAFFIC_NETWORK/YellowTrafficDividers'))
        points=mesh.GetPointsAttr().Get();indices=mesh.GetFaceVertexIndicesAttr().Get();cls.dividers=[];cls.grid={};start=0
        for count in mesh.GetFaceVertexCountsAttr().Get():
            face=[(points[i][0],points[i][1]) for i in indices[start:start+count]];start+=count
            cls.dividers.append(face)
        # The new edge railings must not obstruct truck turns or bridge mouths.
        for x1,y1,x2,y2 in cls.stage.GetPrimAtPath('/World/QuayEdgeGuardrails').GetAttribute('guardrail:segments').Get():
            cls.dividers.append([(min(x1,x2)-.0325,min(y1,y2)-.0325),(max(x1,x2)+.0325,min(y1,y2)-.0325),
                                 (max(x1,x2)+.0325,max(y1,y2)+.0325),(min(x1,x2)-.0325,max(y1,y2)+.0325)])
        for face in cls.dividers:
            for x in range(math.floor(min(v[0] for v in face)/16),math.floor(max(v[0] for v in face)/16)+1):
                for y in range(math.floor(min(v[1] for v in face)/16),math.floor(max(v[1] for v in face)/16)+1):
                    cls.grid.setdefault((x,y),[]).append(face)

    def test_twenty_foot_load_uses_front_edge_and_forty_foot_stays_centred(self):
        self.assertLess(self.offset,-1.)
        self.assertAlmostEqual(self.park[0]+self.offset-2.597/2,self.deck.GetMin()[0]+.08,places=5)
        self.assertEqual(truck_loading(self.stage,40,5.272)[2],0.)

    def check_route(self,points,label,reverse=False):
        route=rounded_route(points,radius=3)
        for a,b in zip(route,route[1:]):
            d=b-a;distance=d.GetLength()
            if distance<1e-6:continue
            heading=math.atan2(d[1],d[0])-(0 if reverse else math.pi)
            c,s=math.cos(heading),math.sin(heading)
            for i in range(max(1,math.ceil(distance/.4))+1):
                t=i/max(1,math.ceil(distance/.4));p=a+d*t
                if not reverse and QUAY_ROAD_EDGES[0]<p[0]<QUAY_ROAD_EDGES[1] and all(abs(p[1]-y)>6 for y in CROSS):
                    self.assertLessEqual(d[1],1e-8,f'{label}: wrong-way movement under STS')
                corners=[(p[0]+x*c-y*s,p[1]+x*s+y*c) for x,y in (
                    (self.local.GetMin()[0],self.local.GetMin()[1]),(self.local.GetMax()[0],self.local.GetMin()[1]),
                    (self.local.GetMax()[0],self.local.GetMax()[1]),(self.local.GetMin()[0],self.local.GetMax()[1]))]
                nearby=[]
                for x in range(math.floor(min(v[0] for v in corners)/16),math.floor(max(v[0] for v in corners)/16)+1):
                    for y in range(math.floor(min(v[1] for v in corners)/16),math.floor(max(v[1] for v in corners)/16)+1):
                        nearby.extend(self.grid.get((x,y),[]))
                for divider in nearby:
                    if (max(v[0] for v in divider)<min(v[0] for v in corners) or min(v[0] for v in divider)>max(v[0] for v in corners)
                        or max(v[1] for v in divider)<min(v[1] for v in corners) or min(v[1] for v in divider)>max(v[1] for v in corners)):continue
                    self.assertFalse(overlaps(corners,divider),f'{label}: truck at {tuple(p)}, heading {math.degrees(heading):.2f}, divider {divider}')

    def test_all_blocks_directions_and_returns_clear_painted_yellow_dividers(self):
        # First/middle/final work lanes, including the unbridged terminal road.
        for yard_y in (2.5,117.,141.,253.,272.,383.,403.,487.):
            for planner in (ship_to_yard_routes,yard_to_ship_routes):
                with self.subTest(yard_y=yard_y,direction=planner.__name__):
                    stop=[-238.15,18.8,self.park[2]]
                    empty,loaded,info=planner(self.park,stop,yard_y)
                    home,backing=return_routes(loaded[-1],self.park,planner==yard_to_ship_routes)
                    for name,route in (('empty',empty),('loaded',loaded),('return',home)):
                        self.check_route(route,f'{planner.__name__}/{yard_y}/{name}')
                    self.check_route(backing,'parking reverse',reverse=True)
                    self.assertEqual(backing[-1],list(self.park))

    def test_cumulative_loading_visits_each_bay_forward_without_crossing_dividers(self):
        from yard_to_ship_plan import plan_yard_to_ship
        plan=plan_yard_to_ship(self.stage,'3C/005',2,plan_type='cumulative_double20',
                              second_source_bay='3C/015',second_source_row=1,delivery_mode='quay',crane_id='STS_01')
        stops=plan['yard_loading_stops_m']
        self.assertGreater(stops[0][1],stops[1][1])
        self.assertEqual(plan['truck_route_empty'][-1],stops[0])
        self.assertEqual(plan['truck_route_loaded'][0],stops[1])
        for label,route in (('empty',plan['truck_route_empty']),('between loads',plan['truck_route_between_loads']),
                            ('loaded',plan['truck_route_loaded'])):
            self.check_route(route,'cumulative/'+label)


if __name__=='__main__':unittest.main()
