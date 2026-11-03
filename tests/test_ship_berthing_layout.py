"""The three-crane berth shares one ship; other berths remain independent."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'omniverse/scripts'))
import large_vessel_layout as layout


class LayoutMathTests(unittest.TestCase):
    def test_row_numbering_is_symmetric_about_zero(self):
        positions=[layout.row_x(row) for row in layout.ROWS]
        self.assertEqual(len(set(positions)),13)
        self.assertEqual(layout.row_x(0),0)
        self.assertAlmostEqual(layout.row_x(12),-layout.row_x(11))
        self.assertTrue(all(a>b for a,b in zip(positions,positions[1:])))

    def test_bridge_has_room_between_bays_43_and_45(self):
        self.assertLess(layout.bay_y(43)+3.029,156.3)
        self.assertGreater(layout.bay_y(45)-3.029,169.3)
        self.assertEqual(layout.BAYS[-1],62)

    def test_hold_and_deck_tier_heights_do_not_overlap(self):
        self.assertLess(layout.tier_z(16)+layout.TIER_PITCH/2,layout.DECK_Z)
        self.assertGreater(layout.tier_z(82)-layout.TIER_PITCH/2,layout.DECK_Z)


class ComposedVesselTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            raise unittest.SkipTest('Requires Kit USD Python')
        cls.Usd,cls.UsdGeom=Usd,UsdGeom
        cls.stage=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))

    def test_only_first_three_small_ships_are_replaced(self):
        self.assertTrue(self.stage.GetPrimAtPath(layout.SHIP).IsActive())
        for path in layout.REPLACED:
            self.assertFalse(self.stage.GetPrimAtPath(path).IsActive(),path)
        for name in ('SMART_PORT_LOCAL_SHIP_4c40_shoreline_west_IMPORTED',
                     'SMART_PORT_REMOTE_CARGO_SHIP_01','SMART_PORT_REMOTE_CARGO_SHIP_02'):
            self.assertTrue(self.stage.GetPrimAtPath('/World/PortAndRTG/'+name).IsActive())

    def test_hull_matches_reference_aspect_and_keeps_the_quay_edge(self):
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        b=cache.ComputeWorldBound(self.stage.GetPrimAtPath(layout.SHIP+'/Hull')).ComputeAlignedBox()
        self.assertLess(b.GetMax()[0],-241.75)
        self.assertAlmostEqual(b.GetSize()[1],248.*layout.STRUCTURE_HULL_SCALE[1])
        self.assertAlmostEqual(b.GetSize()[0],layout.STRUCTURE_BEAM_M,places=5)
        self.assertAlmostEqual(b.GetSize()[1]/b.GetSize()[0],573/107,places=5)
        self.assertGreater(b.GetSize()[1],125.)
        self.assertAlmostEqual(b.GetMax()[0],layout.SHIP_X+layout.BEAM_M/2,places=5)
        self.assertLess(b.GetMin()[1],55.)
        self.assertGreater(b.GetMax()[1],55.)
        self.assertAlmostEqual(layout.structure_bay_y(1),17.5)
        root=self.stage.GetPrimAtPath(layout.SHIP)
        self.assertAlmostEqual(root.GetAttribute('ship:lengthMeters').Get(),b.GetSize()[1])
        self.assertAlmostEqual(root.GetAttribute('xformOp:translate').Get()[2],-4.9478)
        # Root scale must stay at one: ship boxes retain actual yard dimensions.
        from pxr import Gf
        world=self.UsdGeom.XformCache().GetLocalToWorldTransform(root)
        for axis in ((1,0,0),(0,1,0),(0,0,1)):
            self.assertAlmostEqual(world.TransformDir(Gf.Vec3d(*axis)).GetLength(),1.)
        other=cache.ComputeWorldBound(self.stage.GetPrimAtPath('/World/PortAndRTG/SMART_PORT_LOCAL_SHIP_4c40_shoreline_west_IMPORTED')).ComputeAlignedBox()
        self.assertGreater(other.GetMin()[1],b.GetMax()[1])

    def test_full_reference_bays_fit_hull_and_clear_the_bridge(self):
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        hull=cache.ComputeWorldBound(self.stage.GetPrimAtPath(layout.SHIP+'/Hull')).ComputeAlignedBox()
        bridge=cache.ComputeWorldBound(self.stage.GetPrimAtPath(layout.SHIP+'/Bridge/Deckhouse0')).ComputeAlignedBox()
        root=self.stage.GetPrimAtPath(layout.SHIP+'/ReferenceBayLayout')
        self.assertEqual([p.GetAttribute('ship:bay').Get() for p in root.GetChildren()],list(layout.BAYS))
        transforms=self.UsdGeom.XformCache()
        from pxr import Gf
        centres={}
        for p in root.GetChildren():
            bay=p.GetAttribute('ship:bay').Get()
            y=transforms.GetLocalToWorldTransform(p).Transform(Gf.Vec3d(0))[1]
            half=(5.272 if bay==62 else 2.636)/2
            self.assertGreater(y-half,hull.GetMin()[1])
            self.assertLess(y+half,hull.GetMax()[1])
            centres[bay]=y
        self.assertLess(centres[43]+2.636/2,bridge.GetMin()[1])
        self.assertGreater(centres[45]-2.636/2,bridge.GetMax()[1])
        self.assertGreater(centres[62]-5.272/2,centres[59]+2.636/2)
        # All visible source cells use exactly these reserved logical positions.
        for group in self.stage.GetPrimAtPath(layout.SHIP+'/StructureSlots').GetChildren():
            for p in group.GetChildren():
                y=transforms.GetLocalToWorldTransform(p).Transform(Gf.Vec3d(0))[1]
                self.assertAlmostEqual(y,centres[p.GetAttribute('ship:bay').Get()])

    def test_full_design_asset_remains_available_without_structure_overlay(self):
        stage=self.Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        stage.MuteLayer(layout.STRUCTURE_LAYER.as_posix())
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        b=cache.ComputeWorldBound(stage.GetPrimAtPath(layout.SHIP+'/Hull')).ComputeAlignedBox()
        self.assertAlmostEqual(b.GetSize()[1],248.)

    def test_compact_hatches_and_bridge_align_with_reserved_bays(self):
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        for first in range(1,60,4):
            p=self.stage.GetPrimAtPath(layout.SHIP+f'/Hatches/Hatch_{first:02d}_{first+2:02d}/Cover1')
            box=cache.ComputeWorldBound(p).ComputeAlignedBox()
            self.assertAlmostEqual(box.GetMidpoint()[1],(layout.structure_bay_y(first)+layout.structure_bay_y(first+2))/2,places=5)
            self.assertAlmostEqual(box.GetSize()[1],12.5*2.675/6.3,places=5)
        stern=cache.ComputeWorldBound(self.stage.GetPrimAtPath(layout.SHIP+'/Hatches/Stern62Cover')).ComputeAlignedBox()
        self.assertAlmostEqual(stern.GetMidpoint()[1],layout.structure_bay_y(62))
        self.assertGreaterEqual(stern.GetSize()[1],5.272)
        self.assertTrue(self.stage.GetPrimAtPath(layout.SHIP+'/Deck/AfterCargoDeck'))

    def test_first_three_cranes_share_this_vessel_and_keep_mesh_clearance(self):
        baseline=self.Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        baseline.MuteLayer(layout.STRUCTURE_LAYER.as_posix())
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        hull=cache.ComputeWorldBound(self.stage.GetPrimAtPath(layout.SHIP+'/Hull')).ComputeAlignedBox()
        previous=None
        for name,bay in layout.STRUCTURE_STS_BAYS.items():
            p=self.stage.GetPrimAtPath('/World/STSControls/'+name)
            old=baseline.GetPrimAtPath(p.GetPath())
            a=p.GetAttribute('xformOp:translate').Get();b=old.GetAttribute('xformOp:translate').Get()
            self.assertEqual((a[0],a[2]),(b[0],b[2]))
            self.assertAlmostEqual(a[1],layout.structure_bay_y(bay))
            self.assertAlmostEqual(p.GetAttribute('sts:homeY').Get(),a[1])
            self.assertGreater(a[1],hull.GetMin()[1]);self.assertLess(a[1],hull.GetMax()[1])
            box=cache.ComputeWorldBound(p).ComputeAlignedBox()
            lo=p.GetAttribute('sts:gantryMin').Get();hi=p.GetAttribute('sts:gantryMax').Get()
            self.assertLessEqual(lo,0);self.assertGreaterEqual(hi,0)
            # Opposing independent extreme commands must still clear meshes.
            if previous:self.assertGreaterEqual(box.GetMin()[1]+lo-previous,2.9999)
            previous=box.GetMax()[1]+hi
        transforms=self.UsdGeom.XformCache();base_transforms=self.UsdGeom.XformCache()
        for n in range(4,8):
            path=f'/World/STSControls/STS_{n:02d}'
            self.assertEqual(transforms.GetLocalToWorldTransform(self.stage.GetPrimAtPath(path)),base_transforms.GetLocalToWorldTransform(baseline.GetPrimAtPath(path)))
            for key in ('homeY','gantryMin','gantryMax'):
                self.assertEqual(self.stage.GetPrimAtPath(path).GetAttribute('sts:'+key).Get(),baseline.GetPrimAtPath(path).GetAttribute('sts:'+key).Get())

    def test_boxes_keep_standard_dimensions_and_logical_locations(self):
        # Exercise the original demo asset separately from a loaded 6.6 plan.
        stage=self.Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        stage.MuteLayer(layout.STRUCTURE_LAYER.as_posix())
        cache=self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(),['default','render'])
        for suffix,length in (('/Model/ShipContainer179',5.272),('/Model/STS03_Double20/UnitA',2.636)):
            p=stage.GetPrimAtPath(layout.SHIP+suffix)
            self.assertTrue(p.IsInstance())
            size=cache.ComputeWorldBound(p).ComputeAlignedBox().GetSize()
            self.assertAlmostEqual(size[1],length,delta=.1)
            self.assertAlmostEqual(size[0],1.369287,delta=.1)
            self.assertAlmostEqual(size[2],.949853,delta=.1)
            self.assertTrue(p.GetAttribute('ship:location').Get())

    def test_bay_plan_and_stern_dimensions(self):
        stage=self.Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        stage.MuteLayer(layout.STRUCTURE_LAYER.as_posix())
        for bay in layout.BAYS:
            p=stage.GetPrimAtPath(layout.SHIP+f'/BayPlan/Bay{bay:02d}')
            self.assertEqual(p.GetAttribute('ship:sizeFt').Get(),40 if bay==62 else 20)
        aft=stage.GetPrimAtPath(layout.SHIP+'/BayPlan/Bay62')
        self.assertEqual(list(aft.GetAttribute('ship:holdTiers').Get()),[])

    def test_curved_ends_have_mooring_anchors_and_underwater_gear(self):
        for name in ('Bow/CurvedGuardrails','Stern/CurvedGuardrails',
                     'Bow/PortAnchor/HawseRim','Bow/StarboardAnchor/Shank',
                     'Bow/WinchPort/Drum','Stern/WinchStarboard/Drum',
                     'Stern/PropellerBlades','Stern/Rudder'):
            self.assertTrue(self.stage.GetPrimAtPath(layout.SHIP+'/EndDetails/'+name),name)
        mesh=self.UsdGeom.Mesh(self.stage.GetPrimAtPath(layout.SHIP+'/Hull/ContinuousTopsides'))
        self.assertGreater(len(mesh.GetPointsAttr().Get()),500)
        self.assertEqual(len(mesh.GetPointsAttr().Get()),len(mesh.GetNormalsAttr().Get()))
        rail=self.UsdGeom.Mesh(self.stage.GetPrimAtPath(layout.SHIP+'/EndDetails/Bow/CurvedGuardrails'))
        self.assertGreater(len(rail.GetPointsAttr().Get()),100)

    def test_live_mount_is_idempotent_and_preserves_other_session_edits(self):
        s=self.Usd.Stage.CreateInMemory()
        self.UsdGeom.Xform.Define(s,'/World/PortAndRTG')
        with self.Usd.EditContext(s,s.GetSessionLayer()):
            self.UsdGeom.Xform.Define(s,'/KeepThisSessionEdit')
        self.assertTrue(layout.ensure_live_layout(s))
        self.assertTrue(layout.ensure_live_layout(s))
        self.assertEqual(list(s.GetSessionLayer().subLayerPaths).count(layout.LAYER.as_posix()),1)
        self.assertTrue(s.GetPrimAtPath('/KeepThisSessionEdit'))

    def test_existing_sts_tasks_can_prepare_at_the_new_boxes(self):
        from sts_control import STSController
        from sts_unload_task import UnloadTask
        from sts_double20_roundtrip_task import Double20RoundTripTask
        for crane,task_type in (('STS_01',UnloadTask),('STS_03',Double20RoundTripTask)):
            s=self.Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
            s.MuteLayer(layout.STRUCTURE_LAYER.as_posix())
            controller=STSController(s,crane)
            try:
                task=task_type(controller)
                task.close()
            finally:
                controller.close()


if __name__=='__main__':
    unittest.main()
