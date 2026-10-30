"""Fixed hoist-local cable attachments shared by PLC and visual control."""
from pxr import Gf, Usd, UsdGeom


class HoistRopeBinding:
    def __init__(self, stage, hoist_path, rope_path, fallback_points):
        self.stage = stage
        self.hoist = stage.GetPrimAtPath(hoist_path)
        self.rope = stage.GetPrimAtPath(rope_path)
        if not self.hoist or not self.rope:
            raise RuntimeError("RTG hoist or rope system is missing")
        self.upper = [Gf.Vec3f(p) for p in fallback_points[::2]]
        self.lower = [Gf.Vec3d(p) for p in fallback_points[1::2]]
        self.measured = False
        self._measure_attachments()

    def _measure_attachments(self):
        # Blender exports named objects as Xforms with anonymous Cube meshes.
        # Match each mesh's object, then measure it in the hoist's own frame.
        # World AABBs would move the anchor when the spreader yaws or shifts.
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render", "proxy"])
        lugs, headers = [], []
        for prim in Usd.PrimRange(self.hoist):
            if not prim.IsA(UsdGeom.Boundable):
                continue
            name = (prim.GetName() + " " + prim.GetParent().GetName()).upper()
            if "YELLOW_LIFTING_LUG_TOP" not in name and "YELLOW_PAIR_LIFT_HEADER" not in name:
                continue
            box = cache.ComputeRelativeBound(prim, self.hoist).ComputeAlignedRange()
            if box.IsEmpty():
                continue
            if "YELLOW_LIFTING_LUG_TOP" in name:
                lugs.append(box.GetMidpoint())
            else:
                headers.append(box.GetMax()[2])
        if len(lugs) != 4 or not headers:
            return
        header_z = max(headers)
        anchors = []
        for point in self.lower:
            lug = min(lugs, key=lambda p: (p[0] - point[0]) ** 2 + (p[1] - point[1]) ** 2)
            anchors.append(Gf.Vec3d(lug[0], lug[1], header_z))
        self.lower = anchors
        self.measured = True

    def points(self):
        cache = UsdGeom.XformCache()
        hoist_to_rope = (cache.GetLocalToWorldTransform(self.hoist)
                         * cache.GetLocalToWorldTransform(self.rope).GetInverse())
        # Upper ends stay on the trolley. Lower ends follow the full evaluated
        # hoist matrix, including yaw/sideshift and carried-load compensation.
        points = []
        for upper, lower in zip(self.upper, self.lower):
            points.extend((upper, Gf.Vec3f(hoist_to_rope.Transform(lower))))
        return points

    def write(self):
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            self.rope.GetAttribute("points").Set(self.points())

    def profile(self):
        return {"frame": str(self.hoist.GetPath()), "measured": self.measured,
                "lower_anchors": [list(p) for p in self.lower]}
