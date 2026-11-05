"""Vectorized mesh clipping must match the original per-face slab clipping."""
import sys,math,unittest,random
from pathlib import Path
from unittest.mock import patch
from pxr import Gf,Usd,UsdGeom
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
import truck_driver_runtime as runtime


class ClipTests(unittest.TestCase):
    def test_rotated_triangle_and_quad_batches_match_scalar_clipping(self):
        try:import numpy
        except ImportError:self.skipTest('Kit numpy package is not on this offline interpreter path')
        random.seed(77)
        for count in (3,4):
            stage=Usd.Stage.CreateInMemory();mesh=UsdGeom.Mesh.Define(stage,'/Mesh')
            points=[]
            for _ in range(96):
                x,y,z=(random.uniform(-4,4) for _ in range(3))
                points.extend(Gf.Vec3f(x+dx,y+dy,z+dz) for dx,dy,dz in ((0,0,0),(1,0,.5),(1,1,1.8),(0,1,1.3))[:count])
            mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr([count]*96)
            mesh.CreateFaceVertexIndicesAttr(list(range(len(points))))
            mesh.AddRotateXYZOp().Set(Gf.Vec3f(15,25,35))
            cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
            with patch.object(runtime,'vector_mesh_rects',return_value=None):
                reference=runtime.collision_rects(mesh.GetPrim(),cache)
            actual=runtime.collision_rects(mesh.GetPrim(),cache)
            self.assertEqual(len(reference),len(actual))
            for expected,found in zip(reference,actual):
                for a,b in zip(expected,found):self.assertAlmostEqual(a,b,places=6)

if __name__=='__main__':unittest.main()
