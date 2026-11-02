"""Source-valid sparse cells, sequence mapping, size semantics and USD import."""
import copy
from collections import Counter
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
from ship_structure_data import read_structure,normalize_structure,tier_center


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.plan=read_structure()

    def test_exact_counts_and_sections(self):
        self.assertEqual(len(self.plan['slots']),507)
        self.assertEqual(Counter(s['zone'] for s in self.plan['slots']),{'H':165,'D':342})
        self.assertEqual(sorted({s['bay'] for s in self.plan['slots']}),[1,3,5,7,9])
        self.assertEqual(len(self.plan['groups']),10)
        self.assertEqual(len(self.plan['hatches']),15)

    def test_raw_codes_are_retained_and_do_not_decide_physical_length(self):
        self.assertEqual(Counter(s['size_code'] for s in self.plan['slots']),{'C':313,'D':168,'1':26})
        self.assertEqual({s['size_ft'] for s in self.plan['slots']},{20})

    def test_sparse_locations_are_not_replaced_by_full_row_tier_product(self):
        g=self.plan['groups'][0]
        self.assertEqual(len(g['locations']),17)
        self.assertLess(len(g['locations']),len(g['rows'])*len(g['tiers']))

    def test_unsorted_row_and_tier_lists_use_sequence_keys(self):
        r=copy.deepcopy(self.plan['source'])
        for b in r['data']['vesselBay']:
            b['rowList'].reverse();b['tierList'].reverse()
        parsed=normalize_structure(r)
        self.assertEqual(parsed['slots'],self.plan['slots'])

    def test_sequence_mismatch_is_rejected(self):
        r=copy.deepcopy(self.plan['source'])
        r['data']['vesselBay'][0]['locationList'][0]['rowSeq']=999
        with self.assertRaisesRegex(ValueError,'sequence mismatch'):normalize_structure(r)

    def test_duplicate_cells_and_failed_responses_are_rejected(self):
        r=copy.deepcopy(self.plan['source']);a=r['data']['vesselBay'][0]['locationList'];a.append(copy.deepcopy(a[0]))
        with self.assertRaisesRegex(ValueError,'Duplicate location'):normalize_structure(r)
        r['result']=1
        with self.assertRaisesRegex(ValueError,'failure'):normalize_structure(r)

    def test_even_bay_rule_is_independent_of_code(self):
        r=copy.deepcopy(self.plan['source']);b=r['data']['vesselBay'][0];b['bayNo']='02'
        for c in b['locationList']:c['location']='02'+c['location'][2:]
        p=normalize_structure(r)
        self.assertTrue(all(s['size_ft']==40 for s in p['slots'] if s['bay']==2))

    def test_high_deck_tiers_supported_without_filling_missing_cells(self):
        self.assertEqual(max(s['tier'] for s in self.plan['slots']),92)
        self.assertEqual(max(t for g in self.plan['groups'] for t in g['tiers'].values()),96)
        self.assertAlmostEqual(tier_center(96,9.4,16)-tier_center(82,9.4,16),7*2.64)


class StructureUsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Usd,UsdGeom
        except ImportError:raise unittest.SkipTest('Requires Kit USD Python')
        cls.Usd=Usd;cls.UsdGeom=UsdGeom
        cls.stage=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        from large_vessel_layout import SHIP
        cls.ship=SHIP

    def test_composed_cells_exactly_match_input_locations(self):
        root=self.stage.GetPrimAtPath(self.ship+'/StructureSlots')
        actual={p.GetAttribute('ship:location').Get() for p in self.Usd.PrimRange(root) if p.HasAttribute('ship:location')}
        self.assertEqual(actual,{s['location'] for s in read_structure()['slots']})
        self.assertFalse(self.stage.GetPrimAtPath(self.ship+'/Model').IsActive())
        self.assertFalse(self.stage.GetPrimAtPath(self.ship+'/BayPlan').IsActive())

    def test_each_slot_has_geometry_and_is_inside_the_ship_beam(self):
        root=self.stage.GetPrimAtPath(self.ship+'/StructureSlots')
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        for group in root.GetChildren():
            for p in group.GetChildren():
                self.assertFalse(p.IsInstance())
                b=cache.ComputeWorldBound(p).ComputeAlignedBox()
                self.assertFalse(b.IsEmpty(),str(p.GetPath()))
                self.assertLess(b.GetMax()[0],-241.75)
                self.assertGreater(b.GetMin()[0],-283.1)

    def test_visual_slots_cannot_be_started_as_confirmed_cargo(self):
        from sts_control import STSController
        from sts_unload_task import UnloadTask
        c=STSController(self.stage,'STS_01')
        try:
            with self.assertRaisesRegex(ValueError,'结构箱位'):UnloadTask(c)
        finally:c.close()

    def test_yard_parts_and_paint_materials_are_present_and_bound(self):
        from pxr import UsdShade
        paints=set()
        root=self.stage.GetPrimAtPath(self.ship+'/StructureSlots')
        for group in root.GetChildren():
            for p in group.GetChildren():
                paints.add(p.GetAttribute('ship:yardPaint').Get())
                geometry=p.GetAttribute('ship:geometryPath').Get()
                body=self.stage.GetPrimAtPath(str(p.GetPath())+'/'+geometry+'/R01_T01_Container')
                bars=self.stage.GetPrimAtPath(str(p.GetPath())+'/'+geometry+'/R01_T01_Container_Front_LockingBars')
                self.assertTrue(body);self.assertTrue(bars)
                mat,_=UsdShade.MaterialBindingAPI(body).ComputeBoundMaterial()
                self.assertTrue(mat)
                shader=mat.ComputeSurfaceSource()[0]
                self.assertIsNotNone(shader.GetInput('diffuseColor').Get())
        self.assertGreaterEqual(len(paints),5)

    def test_deck_stack_bases_touch_support_and_boxes_touch_each_other(self):
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        groups={}
        root=self.stage.GetPrimAtPath(self.ship+'/StructureSlots')
        for group in root.GetChildren():
            for p in group.GetChildren():
                if p.GetAttribute('ship:zone').Get()!='D':continue
                key=(p.GetAttribute('ship:bay').Get(),p.GetAttribute('ship:row').Get())
                groups.setdefault(key,[]).append(p)
        for cells in groups.values():
            cells.sort(key=lambda p:p.GetAttribute('ship:tier').Get())
            first=cells[0];b=cache.ComputeWorldBound(first).ComputeAlignedBox()
            from large_vessel_layout import DECK_Z
            self.assertAlmostEqual(b.GetMin()[2],first.GetAttribute('ship:supportLocalZ').Get()-4.9478,places=4)
            for p in cells[1:]:
                following=cache.ComputeWorldBound(p).ComputeAlignedBox()
                self.assertAlmostEqual(following.GetMin()[2],b.GetMax()[2],places=4)
                b=following

    def test_all_paints_copy_matching_yard_detail_colors_not_one_red_sample(self):
        yard=self.Usd.Stage.Open(str(ROOT/'omniverse/scenes/live_containers.usda'))
        from ship_yard_container_models import PAINTS
        for feet in (20,40):
            for paint in PAINTS:
                prototype=self.stage.GetPrimAtPath(self.ship+f'/YardContainerPrototypes/C{feet}_{paint}')
                source_path=prototype.GetAttribute('ship:yardSourcePrim').Get()
                for suffix in ('_WeatheredTop','_SideCorrugation','_RoofRidges','_Front_DoorFrame'):
                    original=yard.GetPrimAtPath(source_path+suffix)
                    geometry=prototype.GetAttribute('ship:geometryPath').Get()
                    copied=self.stage.GetPrimAtPath(str(prototype.GetPath())+'/'+geometry+'/R01_T01_Container'+suffix)
                    self.assertEqual(original.GetAttribute('primvars:displayColor').Get(),
                                     copied.GetAttribute('primvars:displayColor').Get(),(feet,paint,suffix))

    def test_real_deck_faces_do_not_cut_through_any_deck_stack_bottom(self):
        from pxr import Gf
        triangles=[];tops=[]
        transforms=self.UsdGeom.XformCache()
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        for suffix in ('/Deck/Forecastle','/Deck/PoopDeck'):
            p=self.stage.GetPrimAtPath(self.ship+suffix);m=self.UsdGeom.Mesh(p)
            points=[transforms.GetLocalToWorldTransform(p).Transform(Gf.Vec3d(v)) for v in m.GetPointsAttr().Get()]
            indices=list(m.GetFaceVertexIndicesAttr().Get());offset=0
            for count in m.GetFaceVertexCountsAttr().Get():
                face=indices[offset:offset+count];offset+=count
                self.assertEqual(count,4,'Deck must use section quads, not a non-planar outline')
                a,b,c,d=(points[i] for i in face)
                self.assertAlmostEqual(Gf.Dot(d-a,Gf.Cross(b-a,c-a)),0.,places=4)
                triangles.extend(((a,b,c),(a,c,d)))
        for root in ('/Hatches','/Deck'):
            for p in self.Usd.PrimRange(self.stage.GetPrimAtPath(self.ship+root)):
                if p.IsA(self.UsdGeom.Cube):tops.append(cache.ComputeWorldBound(p).ComputeAlignedBox())
        def height(x,y,tri):
            a,b,c=tri
            den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(den)<1e-10:return None
            u=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/den
            v=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/den
            if min(u,v,1-u-v)<-1e-6:return None
            return u*a[2]+v*b[2]+(1-u-v)*c[2]
        lowest={}
        for group in self.stage.GetPrimAtPath(self.ship+'/StructureSlots').GetChildren():
            for p in group.GetChildren():
                if p.GetAttribute('ship:zone').Get()!='D':continue
                key=(p.GetAttribute('ship:bay').Get(),p.GetAttribute('ship:row').Get())
                if key not in lowest or p.GetAttribute('ship:tier').Get()<lowest[key].GetAttribute('ship:tier').Get():lowest[key]=p
        for p in lowest.values():
            box=cache.ComputeWorldBound(p).ComputeAlignedBox();lo=box.GetMin();hi=box.GetMax()
            support_hits=[]
            for x in (lo[0],(lo[0]+hi[0])/2,hi[0]):
                for y in (lo[1],(lo[1]+hi[1])/2,hi[1]):
                    z=[v for t in triangles if (v:=height(x,y,t)) is not None]
                    z += [b.GetMax()[2] for b in tops if b.GetMin()[0]<=x<=b.GetMax()[0] and b.GetMin()[1]<=y<=b.GetMax()[1]]
                    if z:
                        support_hits.extend(z)
                        self.assertGreaterEqual(lo[2]+1e-4,max(z),str(p.GetPath()))
            # Small hatch-panel seams need not support all nine sample points.
            self.assertTrue(support_hits,str(p.GetPath()))
            self.assertLessEqual(lo[2]-max(support_hits),.03,str(p.GetPath()))


if __name__=='__main__':unittest.main()
