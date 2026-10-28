"""USD integration for cached container roofs and one pooled splash mesh."""
import numpy as np
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, Vt
from rain_particle_motion import RainSplashPool

LIVE_ROOT = '/World/LiveContainers'
SPLASH_PATH = '/World/SMART_PORT_WEATHER/Rain/AirRain/ContactSplashes'
MOTION_PATH = '/World/SMART_PORT_WEATHER/Rain/AirRain/FallingDrops'


class RainSurfaceEffects:
    def __init__(self, stage):
        self.stage = stage
        self.dirty = True
        self.anchor = None
        self.version = 0
        self.last_scan = -1e9
        self.roofs = []
        self.pool = RainSplashPool()
        self.last_splash_update = -1e9
        self.micro_points = np.zeros((512, 3), dtype=np.float32)
        self.micro_points[:, :2] = np.random.default_rng(20261019).uniform(-12, 12, (512, 2))
        self.micro_cursor = 0
        self.micro_credit = 0.
        self.last_micro_update = None
        self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._changed, stage)
        motion = UsdGeom.Mesh.Define(stage, MOTION_PATH)
        motion.CreateSubdivisionSchemeAttr('none')
        motion.CreateDoubleSidedAttr(True)
        motion.CreateExtentAttr([Gf.Vec3f(-25, -19, .12), Gf.Vec3f(65, 40, 28)])
        motion.CreateVisibilityAttr('inherited')
        UsdShade.MaterialBindingAPI.Apply(motion.GetPrim()).Bind(UsdShade.Material(
            stage.GetPrimAtPath('/World/SMART_PORT_WEATHER/Looks/RainStreakMaterial')))
        self.motion_points = motion.CreatePointsAttr([])
        self.motion_counts = motion.CreateFaceVertexCountsAttr([])
        self.motion_indices = motion.CreateFaceVertexIndicesAttr([])
        self.motion_vertex_count = 0
        mesh = UsdGeom.Mesh.Define(stage, SPLASH_PATH)
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateDoubleSidedAttr(True)
        counts, indices = self.pool.topology()
        mesh.CreateFaceVertexCountsAttr(counts)
        mesh.CreateFaceVertexIndicesAttr(indices)
        self.points_attr = mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(self.pool.update(0)))
        mesh.CreateExtentAttr([Gf.Vec3f(-35, -25, .12), Gf.Vec3f(55, 35, 28)])
        material = UsdShade.Material.Define(stage, SPLASH_PATH+'/Material')
        shader = UsdShade.Shader.Define(stage, SPLASH_PATH+'/Material/Surface')
        shader.CreateIdAttr('UsdPreviewSurface')
        for name, kind, value in (
            ('diffuseColor', Sdf.ValueTypeNames.Color3f, (.16, .19, .22)),
            ('emissiveColor', Sdf.ValueTypeNames.Color3f, (5., 5.3, 5.5)),
            ('opacity', Sdf.ValueTypeNames.Float, .38),
            ('roughness', Sdf.ValueTypeNames.Float, .42),
            ('metallic', Sdf.ValueTypeNames.Float, 0.)):
            shader.CreateInput(name, kind).Set(value)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        mesh.CreateVisibilityAttr('inherited')

    def _changed(self, notice, sender):
        if self.dirty:
            return
        for path in list(notice.GetResyncedPaths()) + list(notice.GetChangedInfoOnlyPaths()):
            if path.HasPrefix(Sdf.Path(LIVE_ROOT)):
                self.dirty = True
                return

    def refresh(self, anchor, now):
        moved = anchor != self.anchor
        if not moved and (not self.dirty or now-self.last_scan < .5):
            return
        if moved:
            self.pool = RainSplashPool()
        self.anchor = anchor
        self.last_scan = now
        self.dirty = False
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render'])
        root = self.stage.GetPrimAtPath(LIVE_ROOT)
        roofs = []
        if root:
            it = iter(Usd.PrimRange(root))
            for prim in it:
                if not prim.GetName().endswith(('Container', '_ImportedModel', '_WeatheredTop')):
                    continue
                if UsdGeom.Imageable(prim).ComputeVisibility() != 'invisible':
                    bounds = cache.ComputeWorldBound(prim).ComputeAlignedBox()
                    lo, hi = bounds.GetMin(), bounds.GetMax()
                    if (lo[0] < anchor[0]+55 and hi[0] > anchor[0]-35
                            and lo[1] < anchor[1]+35 and hi[1] > anchor[1]-25
                            and .5 < hi[2] < 27):
                        roofs.append((lo[0]+.02, lo[1]+.02, hi[0]-.02, hi[1]-.02, hi[2]))
                it.PruneChildren()
        self.roofs = roofs
        self.micro_points[:, 2] = .13
        xy = self.micro_points[:, :2] + anchor
        for xmin, ymin, xmax, ymax, z in roofs:
            hit = ((xy[:, 0] >= xmin) & (xy[:, 0] <= xmax)
                   & (xy[:, 1] >= ymin) & (xy[:, 1] <= ymax))
            self.micro_points[hit, 2] = np.maximum(self.micro_points[hit, 2], z)
        self.version += 1

    def update_splashes(self, contacts, now, visible_layers):
        for points in contacts:
            self.pool.spawn(points, now)
        # Fine droplets need no separately rendered airborne geometry. Their
        # small contact ripples keep a close roof view alive at a fixed budget.
        dt = min(.1, max(0., now-self.last_micro_update)) if self.last_micro_update is not None else 0.
        self.last_micro_update = now
        self.micro_credit += dt*(55, 170, 260, 340)[visible_layers-1]
        count = int(self.micro_credit)
        self.micro_credit -= count
        if count:
            slots = (self.micro_cursor + np.arange(count)) % len(self.micro_points)
            self.pool.spawn(self.micro_points[slots], now, scale=.5)
            self.micro_cursor = (self.micro_cursor + count) % len(self.micro_points)
        if now-self.last_splash_update >= 1/30 or now < self.last_splash_update:
            self.points_attr.Set(Vt.Vec3fArray.FromNumpy(self.pool.update(now)))
            self.last_splash_update = now

    def update_motion(self, points):
        # Individual particles no longer need separate phase meshes. A single
        # packed upload also avoids twelve acceleration-structure updates.
        array = np.concatenate(points)
        if len(array) != self.motion_vertex_count:
            self.motion_counts.Set([4]*(len(array)//4))
            self.motion_indices.Set(list(range(len(array))))
            self.motion_vertex_count = len(array)
        self.motion_points.Set(Vt.Vec3fArray.FromNumpy(array))

    def close(self):
        self.notice.Revoke()
        prim = self.stage.GetPrimAtPath(SPLASH_PATH)
        if prim:
            UsdGeom.Imageable(prim).CreateVisibilityAttr('invisible')
        prim = self.stage.GetPrimAtPath(MOTION_PATH)
        if prim:
            UsdGeom.Imageable(prim).CreateVisibilityAttr('invisible')
