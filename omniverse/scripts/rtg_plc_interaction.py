"""Measured spreader contacts, twistlock interlocks and PhysX cargo for ZMQ.

All entry points belong to the Kit main thread. Geometry uses the rendered
world coordinates, never an engineering tier-height estimate or stale bay map.
"""
import hashlib
import itertools
import math

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from rtg_plc_contact import box_bounds, sweep_box
from rtg_plc_physx import PlcPhysx

POSE_OP = "xformOp:transform:plcCargoPose"
GROUND_Z = .10
CONTACT_TOLERANCE = .015
ALIGNMENT_TOLERANCE = .07
SUPPORT_TOLERANCE = .045
SKIN = .002
NEAR_RADIUS = 30.0


def corners(size, z=None):
    return [Gf.Vec3d(x * size[0] / 2, y * size[1] / 2, h)
            for x in (-1, 1) for y in (-1, 1)
            for h in ((-size[2] / 2, size[2] / 2) if z is None else (z,))]


def rigid_pose(matrix, centre):
    axis = matrix.TransformDir(Gf.Vec3d(1, 0, 0))
    yaw = math.degrees(math.atan2(axis[1], axis[0]))
    return (Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), yaw))
            * Gf.Matrix4d().SetTranslate(centre))


class PlcInteraction:
    def __init__(self, stage, controller, backend_factory=PlcPhysx):
        self.stage, self.controller = stage, controller
        self.backend_factory = backend_factory
        self.enabled = False
        self.backend = self.notice = self.pose_layer = None
        self.records, self.decks, self.dynamic = {}, {}, set()
        self.carried = self.relative_carry = None
        self.collision = None
        self.last_release = None
        self.dirty = True
        self.writing = False
        self.proxy_centre = None
        self.anchor = None
        self.feet = []
        self.scene_conflict = False
        self.external_changes = set()

    def _changed(self, notice, sender):
        if self.writing:
            return
        for path in itertools.chain(notice.GetResyncedPaths(), notice.GetChangedInfoOnlyPaths()):
            text = str(path)
            if text == "/" or text == "/World" or text.startswith("/World/LiveContainers"):
                self.dirty = True
                if not path.IsPropertyPath() or path.name.startswith("xformOp") or path.name in {"size", "visibility"}:
                    self.external_changes.add(path.GetPrimPath())
        if self.carried:
            members = self._visual_paths(self.records[self.carried])
            for path in self.external_changes:
                if any(Sdf.Path(member).HasPrefix(path) or path.HasPrefix(Sdf.Path(member)) for member in members):
                    self.scene_conflict = True

    def set_enabled(self, enabled):
        if bool(enabled) == self.enabled:
            return
        if not enabled:
            if self.carried:
                raise ValueError("land and unlock the carried container before disabling sim_physics")
            self.close()
            return
        import rtg_live_control as live
        if not self.feet:
            cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
            for prim in Usd.PrimRange(self.stage.GetPrimAtPath(live.HOIST_PATH)):
                if prim.IsA(UsdGeom.Boundable) and "red_corner_lock_foot_" in str(prim.GetPath()):
                    bound = cache.ComputeUntransformedBound(prim).ComputeAlignedBox()
                    mid = bound.GetMidpoint()
                    self.feet.append((str(prim.GetPath()), Gf.Vec3d(mid[0], mid[1], bound.GetMin()[2])))
        if len(self.feet) != 4:
            raise ValueError("cannot measure the four spreader lock feet")
        self.backend = self.backend_factory(self.stage)
        try:
            self.backend.start()
            self.enabled = True
            self._refresh()
            pose, size = self._anchor_pose()
            self.anchor = self.backend.body("Spreader", size, pose, True)
            self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._changed, self.stage)
        except BaseException:
            self.close()
            raise

    def _foot_points(self):
        cache = UsdGeom.XformCache()
        result = []
        for path, point in self.feet:
            prim = self.stage.GetPrimAtPath(path)
            if not prim:
                raise RuntimeError("spreader geometry changed; disable and re-enable sim_physics")
            result.append(cache.GetLocalToWorldTransform(prim).Transform(point))
        return result

    def _anchor_pose(self):
        import rtg_live_control as live
        points = self._foot_points()
        centre = sum(points, Gf.Vec3d(0)) / 4
        world = UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(live.HOIST_PATH))
        pose = rigid_pose(world, centre + Gf.Vec3d(0, 0, .125))
        local = [pose.GetInverse().Transform(p) for p in points]
        bounds = box_bounds(local)
        size = (bounds[1][0] - bounds[0][0] + .12, bounds[1][1] - bounds[0][1] + .24, .25)
        return pose, size

    def _read_record(self, prim):
        size = float(UsdGeom.Cube(prim).GetSizeAttr().Get())
        world = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        centre = world.Transform(Gf.Vec3d(0))
        dims = tuple(world.TransformDir(Gf.Vec3d(*(size if i == axis else 0 for i in range(3)))).GetLength()
                     for axis in range(3))
        pose = rigid_pose(world, centre)
        size_ft = None
        ancestor = prim
        while ancestor and not ancestor.IsPseudoRoot():
            attr = ancestor.GetAttribute("live:containerSizeFt")
            if attr and attr.Get() in (20, 40):
                size_ft = int(attr.Get())
                break
            ancestor = ancestor.GetParent()
        return {"path": str(prim.GetPath()), "size": dims, "pose": pose, "shape": world * pose.GetInverse(),
                "size_ft": size_ft or (40 if dims[1] > 4 else 20), "body": None}

    @staticmethod
    def _bounds(record, pose=None):
        pose = record["pose"] if pose is None else pose
        return box_bounds([pose.Transform(p) for p in corners(record["size"])])

    @staticmethod
    def _visual_paths(record):
        return (record["path"], *record.get("visuals", {}))

    @staticmethod
    def _neutral_world(prim, cache):
        world = cache.GetLocalToWorldTransform(prim)
        attr = prim.GetAttribute(POSE_OP)
        matrix = attr.Get() if attr else None
        return matrix.GetInverse() * world if matrix is not None else world

    def _capture_visuals(self, record):
        """Bind decorative siblings to the same source frame as the box body.

        The generated details are siblings, not children of the collision
        cube. Remove our additive pose when measuring the source frame so a
        live code update can also repair an already raised, split container.
        """
        source = self.stage.GetPrimAtPath(record["path"])
        if not source:
            raise RuntimeError("a physics-controlled container disappeared: " + record["path"])
        cache = UsdGeom.XformCache()
        record["visual_source_world"] = self._neutral_world(source, cache)
        prefix = source.GetName() + "_"
        record["visuals"] = {
            str(prim.GetPath()): self._neutral_world(prim, cache)
            for prim in source.GetParent().GetChildren()
            if prim.GetName().startswith(prefix) and prim.IsA(UsdGeom.Xformable)
        }

    def _clear_record_poses(self, record):
        for path in self._visual_paths(record):
            if self.pose_layer is None or not self.pose_layer.GetPrimAtPath(path):
                continue
            prim = self.stage.GetPrimAtPath(path)
            if prim:
                xf = UsdGeom.Xformable(prim)
                ops = xf.GetOrderedXformOps()
                if any(op.GetOpName() == POSE_OP for op in ops):
                    with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                        xf.SetXformOpOrder([op for op in ops if op.GetOpName() != POSE_OP])
            with Usd.EditContext(self.stage, self.pose_layer):
                self.stage.RemovePrim(path)

    def _refresh(self):
        if self.scene_conflict:
            raise RuntimeError("the locked container was rebuilt by a scene update; PLC motion stopped")
        if self.dirty:
            # A new task's rendered inventory is authoritative. Remove only
            # our old pose overrides and relinquish dynamic ownership for the
            # rebuilt boxes; never retain a proxy at the previous task's slot.
            paths = self.stage.GetSessionLayer().subLayerPaths
            if self.pose_layer is None:
                self.pose_layer = next((Sdf.Layer.Find(p) for p in paths if p.endswith(":smartport_plc_cargo_poses.usda")), None)
            self.writing = True
            try:
                for path, record in self.records.items():
                    if not any(Sdf.Path(member).HasPrefix(changed) or changed.HasPrefix(Sdf.Path(member))
                               for member in self._visual_paths(record) for changed in self.external_changes):
                        continue
                    if path in self.dynamic and record["body"]:
                        self.backend.set_kinematic(record["body"], True)
                    self.dynamic.discard(path)
                    self._clear_record_poses(record)
                    deck = self.decks.pop(path, None)
                    if deck and deck["body"]:
                        self.backend.remove_body(deck["body"])
            finally:
                self.writing = False
            self.external_changes.clear()
            root = self.stage.GetPrimAtPath("/World/LiveContainers")
            found = {}
            if root:
                for prim in Usd.PrimRange(root):
                    if not prim.IsA(UsdGeom.Cube) or not prim.HasAttribute("live:row"):
                        continue
                    if UsdGeom.Imageable(prim).ComputeVisibility() == "invisible":
                        model = self.stage.GetPrimAtPath(str(prim.GetPath()) + "_ImportedModel")
                        if not model or UsdGeom.Imageable(model).ComputeVisibility() == "invisible":
                            continue
                    path = str(prim.GetPath())
                    if path in self.dynamic:
                        found[path] = self.records[path]
                    else:
                        record = self._read_record(prim)
                        previous = self.records.get(path)
                        if previous:
                            record["body"] = previous["body"]
                        found[path] = record
                    if "Truck" in path and path not in self.decks:
                        bounds = self._bounds(found[path])
                        deck = dict(found[path], path="deck:" + path, body=None)
                        deck["size"] = (deck["size"][0] + .05, deck["size"][1] + .05, .15)
                        centre = found[path]["pose"].ExtractTranslation()
                        deck["pose"] = rigid_pose(found[path]["pose"], Gf.Vec3d(centre[0], centre[1], bounds[0][2] - .075))
                        self.decks[path] = deck
            if self.carried and self.carried not in found:
                raise RuntimeError("the locked container was removed by a scene update; PLC motion stopped")
            for path, record in self.records.items():
                if path not in found and record["body"]:
                    self.backend.remove_body(record["body"])
                    self.dynamic.discard(path)
            for path in tuple(self.decks):
                if path not in found:
                    deck = self.decks.pop(path)
                    if deck["body"]:
                        self.backend.remove_body(deck["body"])
            self.records = found
            self.dirty = False
            self.proxy_centre = None
        centre = sum(self._foot_points(), Gf.Vec3d(0)) / 4
        if self.proxy_centre is not None and (centre - self.proxy_centre).GetLength() < 2:
            return
        self.proxy_centre = centre
        for record in itertools.chain(self.records.values(), self.decks.values()):
            near = (record["pose"].ExtractTranslation() - centre).GetLength() < NEAR_RADIUS
            if near and record["body"] is None:
                key = hashlib.sha1(record["path"].encode()).hexdigest()[:16]
                record["body"] = self.backend.body("Cargo_" + key, record["size"], record["pose"], True)
            elif record["body"] and record["path"] not in self.dynamic:
                if near:
                    self.backend.body(record["body"].rsplit("/", 1)[-1], record["size"], record["pose"], True)
                else:
                    self.backend.remove_body(record["body"])
                    record["body"] = None

    def _moving_shapes(self):
        pose, size = self._anchor_pose()
        shapes = [box_bounds([pose.Transform(p) for p in corners(size)])]
        if self.carried:
            shapes.append(self._bounds(self.records[self.carried], self.relative_carry * pose))
        return shapes

    def _obstacles(self):
        for record in itertools.chain(self.records.values(), self.decks.values()):
            if record["path"] != self.carried and record["body"] is not None:
                yield record["path"], self._bounds(record)
        yield "ground", ((-1000, -1000, -.1), (1000, 1000, GROUND_Z))

    def _limit(self, start, end):
        fraction, hit = 1.0, None
        obstacles = list(self._obstacles())
        for before, after in zip(start, end):
            distance = max(abs(after[j][i] - before[j][i]) for j in (0, 1) for i in range(3))
            if distance < 1e-10:
                continue
            for path, bounds in obstacles:
                contact = sweep_box(before, after, bounds)
                if contact is not None and contact <= fraction:
                    fraction = max(0.0, contact - SKIN / distance)
                    hit = path
        return fraction, hit

    def move_axis(self, index, target):
        self.backend.check()
        self._refresh()
        before = self.controller.get_positions()[index]
        method = getattr(self.controller, ("set_gantry_safe", "set_trolley_safe", "set_hoist_safe")[index])
        start = self._moving_shapes()
        try:
            method(target)
            fraction, hit = self._limit(start, self._moving_shapes())
        except BaseException:
            method(before)
            raise
        applied = before + (target - before) * fraction
        if fraction < 1:
            method(applied)
            self.collision = {"axis": ("gantry", "trolley", "hoist")[index], "obstacle": hit}
        return applied

    def move_trim(self, target):
        self.backend.check()
        self._refresh()
        original = self.controller.get_spreader_trim()
        count = max(1, math.ceil(abs(target[0] - original[0]) / .1), math.ceil(abs(target[1] - original[1]) / .003))
        for step in range(1, count + 1):
            before = self.controller.get_spreader_trim()
            candidate = tuple(a + (b - a) * step / count for a, b in zip(original, target))
            start = self._moving_shapes()
            try:
                self.controller.set_spreader_trim(*candidate)
                fraction, hit = self._limit(start, self._moving_shapes())
            except BaseException:
                self.controller.set_spreader_trim(*before)
                raise
            if fraction < 1:
                self.controller.set_spreader_trim(*before)
                self.collision = {"axis": "spreader_trim", "obstacle": hit}
                return before
        return target

    def contact(self):
        points = self._foot_points()
        candidates = []
        for record in self.records.values():
            if record["path"] == self.carried or record["body"] is None:
                continue
            inv = record["pose"].GetInverse()
            local = [inv.Transform(p) for p in points]
            sx, sy, sz = record["size"]
            on_roof = [abs(p[0]) <= sx / 2 + .01 and abs(p[1]) <= sy / 2 + .01
                       and abs(p[2] - sz / 2) <= CONTACT_TOLERANCE for p in local]
            if not any(on_roof):
                continue
            roof_bounds = self._bounds(record)
            if any(other["path"] != record["path"] and other["path"] != self.carried
                   and self._bounds(other)[0][2] >= roof_bounds[1][2] - SKIN
                   and all(min(self._bounds(other)[1][i], roof_bounds[1][i])
                           - max(self._bounds(other)[0][i], roof_bounds[0][i]) > .02 for i in (0, 1))
                   for other in self.records.values() if other["body"] is not None):
                continue
            targets = [Gf.Vec3d(x * (sx / 2 - .158), y * (sy / 2 - .17), sz / 2)
                       for x in (-1, 1) for y in (-1, 1)]
            error = min(max(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(local, order))
                        for order in itertools.permutations(targets))
            candidates.append({"container": record["path"], "feet": on_roof, "alignment_error_m": error,
                               "size_matches": abs(self.controller.current_spreader_size_ft() - record["size_ft"]) < .1})
        return min(candidates, key=lambda item: (not all(item["feet"]), item["alignment_error_m"])) if candidates else None

    def support(self, record):
        points = [record["pose"].Transform(p) for p in corners(record["size"], -record["size"][2] / 2)]
        supports = []
        for point in points:
            if -SKIN <= point[2] - GROUND_Z <= SUPPORT_TOLERANCE:
                supports.append("ground")
                continue
            hit = None
            for other in itertools.chain(self.records.values(), self.decks.values()):
                if other["path"] == record["path"] or other["body"] is None:
                    continue
                local = other["pose"].GetInverse().Transform(point)
                sx, sy, sz = other["size"]
                if (abs(local[0]) <= sx / 2 + .015 and abs(local[1]) <= sy / 2 + .015
                        and -SKIN <= local[2] - sz / 2 <= SUPPORT_TOLERANCE):
                    hit = other["path"]
                    break
            if hit is None:
                return None
            supports.append(hit)
        return sorted(set(supports))

    def set_lock(self, locked):
        self.backend.check()
        self._refresh()
        if locked:
            if self.carried:
                return
            contact = self.contact()
            if not contact or not all(contact["feet"]):
                raise ValueError("lock rejected: all four spreader feet must contact an exposed container roof")
            if not contact["size_matches"]:
                raise ValueError("lock rejected: spreader 20/40 ft size does not match the container")
            if contact["alignment_error_m"] > ALIGNMENT_TOLERANCE:
                raise ValueError("lock rejected: twistlocks are misaligned; adjust trolley/gantry/yaw/sideshift")
            record = self.records[contact["container"]]
            self._capture_visuals(record)
            anchor_pose, _ = self._anchor_pose()
            self.backend.lock(self.anchor, record["body"], anchor_pose, record["pose"])
            self.relative_carry = record["pose"] * anchor_pose.GetInverse()
            self.carried = record["path"]
            self.dynamic.add(self.carried)
        elif self.carried:
            record = self.records[self.carried]
            supports = self.support(record)
            if not supports:
                raise ValueError("unlock rejected: the container must be landed with all four bottom corners supported")
            self.backend.unlock()
            self.last_release = {"container": self.carried, "supports": supports}
            self.carried = self.relative_carry = None

    def _write_pose(self, record):
        prim = self.stage.GetPrimAtPath(record["path"])
        if not prim:
            raise RuntimeError("a physics-controlled container disappeared: " + record["path"])
        if self.pose_layer is None:
            paths = self.stage.GetSessionLayer().subLayerPaths
            self.pose_layer = next((Sdf.Layer.Find(p) for p in paths if p.endswith(":smartport_plc_cargo_poses.usda")), None)
            if self.pose_layer is None:
                self.pose_layer = Sdf.Layer.CreateAnonymous("smartport_plc_cargo_poses.usda")
                paths.insert(0, self.pose_layer.identifier)
        if "visuals" not in record:
            self._capture_visuals(record)
        desired = record["shape"] * record["pose"]
        delta = record["visual_source_world"].GetInverse() * desired
        cache = UsdGeom.XformCache()
        self.writing = True
        try:
            self._write_visual_pose(prim, desired, cache)
            for path, baseline in record["visuals"].items():
                part = self.stage.GetPrimAtPath(path)
                if not part:
                    raise RuntimeError("a physics-controlled container detail disappeared: " + path)
                self._write_visual_pose(part, baseline * delta, cache)
            with Usd.EditContext(self.stage, self.pose_layer):
                mode = prim.CreateAttribute("live:manualReleaseMode", Sdf.ValueTypeNames.Token)
                if mode.Get() != "plc_physx":
                    mode.Set("plc_physx")
        finally:
            self.writing = False

    def _write_visual_pose(self, prim, desired, cache):
        xf = UsdGeom.Xformable(prim)
        ops = xf.GetOrderedXformOps()
        world = cache.GetLocalToWorldTransform(prim)
        old = next((op for op in ops if op.GetOpName() == POSE_OP), None)
        neutral = old.Get().GetInverse() * world if old is not None else world
        with Usd.EditContext(self.stage, self.pose_layer):
            if old is None:
                attr = prim.GetAttribute(POSE_OP)
                if not attr:
                    attr = prim.CreateAttribute(POSE_OP, Sdf.ValueTypeNames.Matrix4d, custom=False)
                old = UsdGeom.XformOp(attr)
                # Keep each visual's own scale/rotation. Only its additional
                # pose belongs to our layer; task rebuilds own the base ops.
                with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                    xf.SetXformOpOrder(ops + [old])
            matrix = desired * neutral.GetInverse()
            if old.Get() != matrix:
                old.Set(matrix)

    def tick(self, dt):
        if not self.enabled:
            return
        self.backend.check()
        self._refresh()
        pose, size = self._anchor_pose()
        self.backend.body("Spreader", size, pose, True)
        # Empty-spreader contacts are swept geometrically before each axis
        # write. With only kinematic proxies there is no gravity/joint state to
        # solve, so an idle crane must not run PhysX at render-frame frequency.
        if self.dynamic:
            self.backend.step(min(max(dt, 0), .1))
        for path in tuple(self.dynamic):
            record = self.records[path]
            record["pose"] = self.backend.pose(record["body"])
            self._write_pose(record)

    def snapshot(self):
        if not self.enabled:
            return {"enabled": False}
        contact = self.contact()
        return {"enabled": True, "backend": "PhysX", "timeline_mode": "paused_manual_scene_step",
                "locked": bool(self.carried), "carried_container": self.carried,
                "contact": contact, "landed": bool(self.carried and self.support(self.records[self.carried])),
                "collision": self.collision, "last_release": self.last_release,
                "nearby_colliders": sum(bool(record["body"]) for record in self.records.values()),
                "contact_tolerance_visual_m": CONTACT_TOLERANCE,
                "alignment_tolerance_visual_m": ALIGNMENT_TOLERANCE}

    def close(self):
        if self.notice is not None:
            self.notice.Revoke()
        self.notice = None
        if self.backend is not None:
            self.backend.close()
        self.backend = None
        self.enabled = False
        self.carried = self.relative_carry = None
        self.records.clear()
        self.decks.clear()
        self.dynamic.clear()
        self.dirty = True
        self.proxy_centre = self.anchor = None
        self.scene_conflict = False
        self.external_changes.clear()
        self.collision = self.last_release = None
