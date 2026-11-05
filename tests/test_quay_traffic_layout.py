"""Check visible composed markings, label clearance and open bridge railings."""
import sys,unittest
from pathlib import Path
from pxr import Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
from build_ground_traffic_network import QUAY_ROAD_EDGES,QUAY_LANE_CENTERS,CAUSEWAYS


def faces(stage,path):
    mesh=UsdGeom.Mesh(stage.GetPrimAtPath(path));points=mesh.GetPointsAttr().Get();indices=mesh.GetFaceVertexIndicesAttr().Get();start=0
    for count in mesh.GetFaceVertexCountsAttr().Get():
        yield [points[i] for i in indices[start:start+count]]
        start+=count


def bounds(face):
    return tuple(min(p[i] for p in face) for i in (0,1)),tuple(max(p[i] for p in face) for i in (0,1))


class QuayTrafficTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))

    def test_five_visible_lanes_have_four_dashed_separators_and_same_direction(self):
        root=self.s.GetPrimAtPath('/World/GROUND_TRAFFIC_NETWORK')
        self.assertEqual(root.GetAttribute('quayLaneCount').Get(),5)
        self.assertEqual(root.GetAttribute('quayTravelDirectionY').Get(),-1)
        white=list(faces(self.s,str(root.GetPath())+'/WhiteTrafficLines'))
        lines=set()
        for face in white:
            lo,hi=bounds(face);x=(lo[0]+hi[0])/2
            if QUAY_ROAD_EDGES[0]-.01<=x<=QUAY_ROAD_EDGES[1]+.01 and hi[0]-lo[0]<.2 and hi[1]-lo[1]>3.9:lines.add(round(x,3))
        self.assertEqual(lines,{round(QUAY_ROAD_EDGES[0]+i*3.2,3) for i in range(6)})
        for x in QUAY_LANE_CENTERS:
            arrow=[bounds(f) for f in white if all(abs(p[0]-x)<1.1 and abs(p[1]-40)<2.1 for p in f)]
            self.assertEqual(len(arrow),3)
            heads=[(lo,hi) for lo,hi in arrow if hi[0]-lo[0]>.4]
            self.assertEqual(len(heads),2)
            self.assertTrue(all((lo[1]+hi[1])/2<40 for lo,hi in heads))
        # Strong surfacing layers must not restore the old 2+2 road's points.
        base=Usd.Stage.Open(str(ROOT/'omniverse/scenes/ground_traffic_network.usda'))
        for name in ('WhiteTrafficLines','YellowTrafficDividers'):
            path='/World/GROUND_TRAFFIC_NETWORK/'+name
            self.assertEqual(self.s.GetPrimAtPath(path).GetAttribute('points').Get(),base.GetPrimAtPath(path).GetAttribute('points').Get())

    def test_short_arrows_do_not_cover_any_actual_bay_number_panel(self):
        arrows=list(faces(self.s,'/World/GROUND_TRAFFIC_NETWORK/YardWorkLaneArrows'))
        panels=list(faces(self.s,'/World/SMART_PORT_REBUILT_YARD_LAYOUT/SMART_PORT_CLEAN_BAY_NUMBER_BACKINGS'))
        self.assertEqual(len(arrows),8*7*3);self.assertGreater(len(panels),1000)
        for arrow in arrows:
            lo,hi=bounds(arrow);self.assertLess(hi[1]-lo[1],1.3)
            for panel in panels:
                a,b=bounds(panel)
                self.assertFalse(all(lo[i]<b[i]+.10 and hi[i]>a[i]-.10 for i in (0,1)),f'Arrow overlaps label at {a}')

    def test_land_edge_is_complete_and_all_four_causeway_openings_remain_clear(self):
        prim=self.s.GetPrimAtPath('/World/QuayEdgeGuardrails')
        spans=list(prim.GetAttribute('guardrail:segments').Get())
        vertical=sorted((s[1],s[3]) for s in spans if abs(s[0]+201.7)<1e-4 and abs(s[0]-s[2])<1e-5)
        self.assertEqual(len(vertical),5)
        edges=[-9.75]+[v for y in CAUSEWAYS for v in (y-4.3,y+4.3)]+[509.75]
        for actual,expected in zip(vertical,zip(edges[::2],edges[1::2])):
            self.assertAlmostEqual(actual[0],expected[0]);self.assertAlmostEqual(actual[1],expected[1])
        for y in CAUSEWAYS:
            for lo,hi in vertical:self.assertFalse(lo<y+3.7 and hi>y-3.7)
            for side in (-1,1):
                self.assertTrue(any(abs(s[0]+201.7)<1e-4 and abs(s[2]+201)<1e-4 and abs(s[1]-y-side*4.3)<1e-4 for s in spans))


if __name__=='__main__':unittest.main()
