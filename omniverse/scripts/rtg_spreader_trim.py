"""Session-layer spreader yaw/sideshift, measured in world degrees/metres.

The asset has non-uniform scale. Conjugate a rigid WORLD transform into its
local space instead of adding rotateZ (which would distort the spreader).
"""
from pxr import Gf, Sdf, Usd, UsdGeom

OP_SUFFIX = "rtgSpreaderTrim"
OP_NAME = "xformOp:transform:" + OP_SUFFIX
YAW_ATTR = "smartport:spreaderYawDeg"
SHIFT_ATTR = "smartport:spreaderShiftM"
PIVOT_ATTR = "smartport:spreaderTrimPivot"
CARRY_SUFFIX = "rtgCarriedSpreaderTrim"


def neutral_world_transform(prim, op_name=OP_NAME):
    world = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
    attribute = prim.GetAttribute(op_name)
    matrix = attribute.Get() if attribute else None
    return matrix.GetInverse() * world if matrix is not None else world


def get_pose(stage, hoist_path):
    prim = stage.GetPrimAtPath(hoist_path)
    result = []
    for name in (YAW_ATTR, SHIFT_ATTR):
        attribute = prim.GetAttribute(name) if prim else None
        value = attribute.Get() if attribute else None
        result.append(float(value) if value is not None else 0.0)
    return tuple(result)


class SpreaderTrim:
    def __init__(self, stage, hoist_path):
        self.stage = stage
        self.prim = stage.GetPrimAtPath(hoist_path)
        if not self.prim:
            raise RuntimeError("Spreader hoist is missing")
        pivot = self.prim.GetAttribute(PIVOT_ATTR)
        self.pivot = pivot.Get() if pivot else None
        if self.pivot is None:
            cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
            feet = [cache.ComputeWorldBound(p).ComputeAlignedBox().GetMidpoint()
                    for p in Usd.PrimRange(self.prim) if p.IsA(UsdGeom.Boundable)
                    and "RTG_SPREAD_REDRAW_red_corner_lock_foot_" in str(p.GetPath())]
            if len(feet) != 4:
                raise RuntimeError("Cannot locate the four spreader lock feet for trim pivot")
            centre = sum(feet, Gf.Vec3d(0)) / len(feet)
            self.pivot = UsdGeom.XformCache().GetLocalToWorldTransform(self.prim).GetInverse().Transform(centre)

    def world_adjustment(self, yaw=None, shift=None):
        if yaw is None:
            yaw, shift = get_pose(self.stage, self.prim.GetPath())
        neutral = neutral_world_transform(self.prim)
        centre = neutral.Transform(self.pivot)
        # Positive local X is the crane's left (-world X in the primary asset).
        left = neutral.TransformDir(Gf.Vec3d(1, 0, 0)).GetNormalized()
        return (Gf.Matrix4d().SetTranslate(-centre)
                * Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), yaw))
                * Gf.Matrix4d().SetTranslate(centre + left * shift))

    def set_pose(self, yaw, shift):
        neutral = neutral_world_transform(self.prim)
        adjustment = self.world_adjustment(yaw, shift)
        matrix = neutral * adjustment * neutral.GetInverse()
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            attr = self.prim.GetAttribute(OP_NAME)
            if not attr:
                attr = UsdGeom.Xformable(self.prim).AddTransformOp(
                    UsdGeom.XformOp.PrecisionDouble, OP_SUFFIX).GetAttr()
            attr.Set(matrix)
            for name, kind, value in ((YAW_ATTR, Sdf.ValueTypeNames.Double, yaw),
                                      (SHIFT_ATTR, Sdf.ValueTypeNames.Double, shift),
                                      (PIVOT_ATTR, Sdf.ValueTypeNames.Double3, self.pivot)):
                self.prim.CreateAttribute(name, kind, custom=True).Set(value)

    def rope_points(self, points, rope_prim):
        adjustment = self.world_adjustment()
        world = UsdGeom.XformCache().GetLocalToWorldTransform(rope_prim)
        inverse = world.GetInverse()
        return [Gf.Vec3f(inverse.Transform(adjustment.Transform(world.Transform(Gf.Vec3d(p)))))
                if index % 2 else p for index, p in enumerate(points)]

    def apply_to_carried_container(self, prim):
        op_name = "xformOp:transform:" + CARRY_SUFFIX
        neutral = neutral_world_transform(prim, op_name)
        matrix = neutral * self.world_adjustment() * neutral.GetInverse()
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            attr = prim.GetAttribute(op_name)
            if not attr:
                attr = UsdGeom.Xformable(prim).AddTransformOp(
                    UsdGeom.XformOp.PrecisionDouble, CARRY_SUFFIX).GetAttr()
            attr.Set(matrix)
