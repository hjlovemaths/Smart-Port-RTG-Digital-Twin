"""Automatic ship -> 2C -> ship round trip for a paired 20 ft load.

This is a session-only visual task.  It deliberately uses the project's
existing STS, yard-truck and primary-RTG kinematic controllers so it can be
started from the STS panel without changing authored scene assets.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, Vt


SHIP = '/World/PortAndRTG/SMART_PORT_LOCAL_SHIP_4c40_shoreline_east_IMPORTED'
TRUCK = '/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft'
TASK = '/World/STSUnloadTask'
RTG_GANTRY = '/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL'
RTG_TROLLEY = RTG_GANTRY + '/ANIM_CTRL_RTG_TROLLEY_TRAVEL'
RTG_HOIST = RTG_TROLLEY + '/ANIM_CTRL_RTG_HOIST_VERTICAL'
RTG_ROPES = RTG_TROLLEY + '/RTG_DYNAMIC_HOIST_ROPES'
# The RTG model's hoist bbox includes the vertical hoist assembly, so its
# bbox midpoint is not the container contact plane.  These are the authored
# local attachment coordinates used by the RTG live-control code.
RTG_LOAD_LOCAL_CENTER = Gf.Vec3d(2.55, -5.00, 2.55)


def bounds(stage, path):
    return UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), ['default', 'render']
    ).ComputeWorldBound(stage.GetPrimAtPath(path)).ComputeAlignedBox()


def translate(v):
    return Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(*v))


def rounded_route(points, radius=4.0):
    result = [Gf.Vec3d(*points[0])]
    for i in range(1, len(points) - 1):
        a, b, c = (Gf.Vec3d(*p) for p in points[i - 1:i + 2])
        u, v = b - a, c - b
        distance = min(radius, u.GetLength() * 0.35, v.GetLength() * 0.35)
        entry = b - u.GetNormalized() * distance
        leave = b + v.GetNormalized() * distance
        result.append(entry)
        for j in range(1, 25):
            t = j / 24
            result.append((1 - t) ** 2 * entry + 2 * t * (1 - t) * b + t ** 2 * leave)
    result.append(Gf.Vec3d(*points[-1]))
    return result


class Double20RoundTripTask:
    """STS unloads two 20 ft boxes, RTG reloads them onto another truck."""

    def __init__(self, controller):
        self.c = controller
        self.stage = controller.stage
        self.phase = 'Ready'
        self.finished = False
        self.paused = False
        self.elapsed = 0.0
        self.delivering = False
        self.layer = Sdf.Layer.CreateAnonymous('sts_double20_roundtrip.usda')
        self.box_paths = self._find_pair()
        for path in (SHIP, TRUCK, TRUCK + '/TrailerDeck', *self.box_paths,
                     RTG_GANTRY, RTG_TROLLEY, RTG_HOIST, RTG_ROPES):
            if not self.stage.GetPrimAtPath(path):
                raise ValueError('双20自动任务模型缺失: ' + path)
        if controller.path.rsplit('/', 1)[-1] != 'STS_03':
            raise ValueError('当前这组船位需要 STS_03 执行双20自动任务')
        if abs(controller.state['boom']) > 0.001:
            raise ValueError('请先将岸桥大臂降到 0 度')
        if controller.stopped:
            raise ValueError('请先恢复 STS_01，再启动自动任务')
        self.home = dict(controller.state)
        self.home_size = controller.spreader_size_ft
        self.home_limits = dict(controller.limits)
        # The active double20 ship layer sits about 1.4 m beyond STS_03's
        # authored travel stop.  Give this visual-only demo a small reversible
        # margin; the regular manual controller limits remain unchanged after
        # the task closes.
        controller.limits['gantry'] = (controller.limits['gantry'][0],
                                       controller.limits['gantry'][1] + 3.0)
        self.cargo_mode = 'ship'
        self.pickup_parked = False
        self._truck_visibility = {}
        self.stage.GetSessionLayer().subLayerPaths.insert(0, self.layer.identifier)
        try:
            sea = getattr(sys.modules.get('builtins'), '_smart_port_sea_motion', None)
            if sea and not sea.closed and sea.stage == self.stage:
                sea.hold(SHIP)
            self.prepare()
        except Exception:
            self.close()
            raise

    def _find_pair(self):
        # The active scene uses the orange 2x20 pair as the first ship load.
        # Keep a fallback for older stages that only have the authored ship
        # replacement layer.
        preferred = (
            '/World/ShipDouble20YardModelLayer/Orange20A',
            '/World/ShipDouble20YardModelLayer/Orange20B',
        )
        if all(self.stage.GetPrimAtPath(path) and self.stage.GetPrimAtPath(path).IsActive()
               for path in preferred):
            return preferred
        fallback = (
            SHIP + '/Model/ShipDouble20_000/UnitA',
            SHIP + '/Model/ShipDouble20_000/UnitB',
        )
        if all(self.stage.GetPrimAtPath(path) and self.stage.GetPrimAtPath(path).IsActive()
               for path in fallback):
            return fallback
        raise ValueError('场景中没有激活的双20尺箱子对')

    @staticmethod
    def load_sibling(name):
        path = Path(__file__).resolve().with_name(name + '.py')
        spec = importlib.util.spec_from_file_location('_sts_roundtrip_' + name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def contact(self):
        cache = UsdGeom.XformCache()
        points = []
        for end in ('Front', 'Rear'):
            for side in ('Front', 'Rear'):
                path = self.c.path + f'/Gantry/Trolley/Hoist/STS_SpreaderEnd{end}/CornerLock{side}'
                points.append(cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(path))
                             .Transform(Gf.Vec3d(0, 0, -0.5)))
        return sum(points, Gf.Vec3d(0)) / 4

    def pose(self, point):
        cache = UsdGeom.XformCache()
        root = cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.c.path))
        gantry = cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.c.path + '/Gantry'))
        columns = [root.TransformDir(Gf.Vec3d(-1, 0, 0)),
                   gantry.TransformDir(Gf.Vec3d(0, -1, 0)),
                   gantry.TransformDir(Gf.Vec3d(0, 0, 1))]
        delta = point - self.contact()
        result = {axis: self.c.state[axis] + Gf.Dot(delta, direction) /
                  Gf.Dot(direction, direction)
                  for axis, direction in zip(('gantry', 'trolley', 'hoist'), columns)}
        for axis, value in result.items():
            lo, hi = self.c.limits[axis]
            if not lo <= value <= hi:
                raise ValueError(f'双20任务 {axis} 目标 {value:.3f} 超出 {lo}..{hi}')
        return result

    def _setup_rtg(self):
        def task_op(xformable, name):
            op_name = 'xformOp:transform:' + name
            for existing in xformable.GetOrderedXformOps():
                if existing.GetOpName() == op_name:
                    return existing
            return xformable.AddTransformOp(
                UsdGeom.XformOp.PrecisionDouble, name
            )

        self.rtg_paths = (RTG_GANTRY, RTG_TROLLEY, RTG_HOIST)
        self.rtg_ops = []
        self.rtg_bases = []
        self.rtg_offset = [0.0, 0.0, 0.0]
        self.rtg_goal = list(self.rtg_offset)
        cache = UsdGeom.XformCache()
        self.rtg_directions = []
        for path, axis in zip(self.rtg_paths, ((0, 1, 0), (1, 0, 0), (0, 0, 1))):
            prim = self.stage.GetPrimAtPath(path)
            parent = cache.GetLocalToWorldTransform(prim.GetParent())
            self.rtg_directions.append(parent.TransformDir(Gf.Vec3d(*axis)))
            xf = UsdGeom.Xformable(prim)
            base = xf.GetLocalTransformation()
            op = task_op(xf, 'stsDouble20Task')
            xf.SetXformOpOrder([op])
            op.Set(base)
            self.rtg_bases.append(base)
            self.rtg_ops.append(op)
        for side in ('FRONT', 'REAR'):
            prim = self.stage.GetPrimAtPath(RTG_HOIST + '/ANIM_CTRL_SPREADER_TELESCOPE_' + side)
            xf = UsdGeom.Xformable(prim)
            op = task_op(xf, 'stsDouble20Task')
            xf.SetXformOpOrder([op])
            op.Set(xf.GetLocalTransformation())
        self.rope = UsdGeom.BasisCurves(self.stage.GetPrimAtPath(RTG_ROPES))
        self.rope_points = list(self.rope.GetPointsAttr().Get())
        self.rope_lower = []
        index = 0
        for count in self.rope.GetCurveVertexCountsAttr().Get():
            ids = range(index, index + count)
            self.rope_lower.append(min(ids, key=lambda i: self.rope_points[i][2]))
            index += count
        self.rope_inverse = cache.GetLocalToWorldTransform(self.rope.GetPrim()).GetInverse()
        self.rtg_origin = self.rtg_contact()
        self.rtg_high = max(self.rtg_origin[2], 6.4)

    def rtg_contact(self):
        cache = UsdGeom.XformCache()
        return cache.GetLocalToWorldTransform(
            self.stage.GetPrimAtPath(RTG_HOIST)
        ).Transform(RTG_LOAD_LOCAL_CENTER)

    def rtg_command(self, point):
        delta = point - self.rtg_origin
        self.rtg_goal = [Gf.Dot(delta, direction) / Gf.Dot(direction, direction)
                         for direction in self.rtg_directions]
        if not (-12 <= self.rtg_goal[1] <= 12 and -9 <= self.rtg_goal[2] <= 2):
            raise ValueError('RTG 目标超出可视工作范围')

    def move_rtg(self, dt):
        for i, speed in enumerate((8.0, 1.3, 0.8)):
            delta = self.rtg_goal[i] - self.rtg_offset[i]
            self.rtg_offset[i] += max(-speed * dt, min(speed * dt, delta))
            vec = [0.0, 0.0, 0.0]
            vec[(1, 0, 2)[i]] = self.rtg_offset[i]
            self.rtg_ops[i].Set(self.rtg_bases[i] * translate(vec))
        points = list(self.rope_points)
        delta = self.rope_inverse.TransformDir(self.rtg_directions[2] * self.rtg_offset[2])
        for index in self.rope_lower:
            points[index] = Gf.Vec3f(Gf.Vec3d(points[index]) + delta)
        self.rope.GetPointsAttr().Set(Vt.Vec3fArray(points))
        return all(abs(a - b) < 1e-5 for a, b in zip(self.rtg_offset, self.rtg_goal))

    def prepare(self):
        self.c.set_spreader_size(40)
        cache = UsdGeom.XformCache()
        self.box_centers = []
        self.box_heights = []
        self.box_world_matrices = []
        self.box_ops = []
        self.box_world_anchored = False
        for path in self.box_paths:
            box = bounds(self.stage, path)
            self.box_centers.append(box.GetMidpoint())
            self.box_heights.append(box.GetSize()[2])
            self.box_world_matrices.append(cache.GetLocalToWorldTransform(
                self.stage.GetPrimAtPath(path)
            ))
        self.source_center = sum(self.box_centers, Gf.Vec3d(0)) / 2
        self.offsets = [center - self.source_center for center in self.box_centers]
        self.height = sum(self.box_heights) / 2
        # The berth at STS_03 is near Y=232, so route the return truck through
        # the closest quay access road (Causeway_03 at Y=256.30), not the old
        # fixed Causeway_02 waypoint at Y=126.05.
        self.ship_causeway_y = min(
            (-5.375, 126.05, 256.30, 386.55),
            key=lambda y: abs(y - self.source_center[1]),
        )
        self.source_top = Gf.Vec3d(self.source_center[0], self.source_center[1],
                                   max(bounds(self.stage, path).GetMax()[2] for path in self.box_paths))
        deck = bounds(self.stage, TRUCK + '/TrailerDeck')
        self.deck_z = deck.GetMax()[2]
        self.ship_stop = Gf.Vec3d(-238.0, self.source_center[1], self.deck_z)
        self.ship_start = self.ship_stop + Gf.Vec3d(0, -22, 0)
        self.truck_deck = deck.GetMidpoint()
        self.clear_z = max(self.source_top[2] + self.height + 1.5, self.contact()[2])
        self.pick = self.source_top
        self.pick_pose = self.pose(self.pick)
        self.pick_high = self.pose(Gf.Vec3d(self.pick[0], self.pick[1], self.clear_z))
        self.place_center = self.ship_stop + Gf.Vec3d(0, 0, self.height / 2 + 0.02)
        self.place_top = self.place_center + Gf.Vec3d(0, 0, self.height / 2)
        self.place_high = self.pose(Gf.Vec3d(self.place_top[0], self.place_top[1], self.clear_z))
        layout = self.load_sibling('build_live_containers')
        # Match PLC receiver row 1 and the live yard renderer exactly.
        target_x = layout.row_center_x_m(1, 6)
        y_a = layout.container_center_y_for_bay('2C', 3, 20)
        y_b = layout.container_center_y_for_bay('2C', 5, 20)
        target_z_top = layout.PAD_TOP_Z_M + layout.STACK_FLOOR_CLEARANCE_M + self.height
        self.yard_tops = (Gf.Vec3d(target_x, y_a, target_z_top),
                          Gf.Vec3d(target_x, y_b, target_z_top))
        self.yard_center_top = sum(self.yard_tops, Gf.Vec3d(0)) / 2
        self.yard_pair_offsets = tuple(
            Gf.Vec3d(0, top[1] - self.yard_center_top[1], 0)
            for top in self.yard_tops
        )
        self.yard_center = self.yard_center_top - Gf.Vec3d(0, 0, self.height / 2)
        # PLC receiver parks task trucks on its calibrated physical stop line,
        # which is intentionally not the visual lane's geometric midpoint.
        self.yard_truck_x = layout.TRUCK_OPERATING_LANE_CENTER_X_M
        self.yard_stop = Gf.Vec3d(self.yard_truck_x, self.yard_center_top[1], self.deck_z)
        self.yard_truck_start = self.yard_stop + Gf.Vec3d(0, -22, 0)
        self.check_occupancy(layout)
        with Usd.EditContext(self.stage, self.layer):
            # All temporary RTG and rope animation opinions must live in the
            # task layer, otherwise a failed/reset run leaves duplicate ops in
            # the session layer and the next start fails in prepare().
            self._setup_rtg()
            self.stage.OverridePrim(SHIP).SetInstanceable(False)
            for i, path in enumerate(self.box_paths):
                xf = UsdGeom.Xformable(self.stage.GetPrimAtPath(path))
                op = xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, 'double20RoundtripDelta')
                xf.SetXformOpOrder([op] + [old for old in xf.GetOrderedXformOps() if old != op])
                op.Set(Gf.Matrix4d(1))
                self.box_ops.append(op)
            self.trucks = []
            # Keep one stable world-space reference for the truck model.  The
            # task-owned refs below reset their xform stacks, so recomputing
            # this from the ref itself would compound the task transform.
            self.truck_world = cache.GetLocalToWorldTransform(
                self.stage.GetPrimAtPath(TRUCK)
            )
            for name in ('TruckInbound', 'TruckReturn', 'TruckPickup'):
                truck = UsdGeom.Xform.Define(self.stage, TASK + '/' + name)
                truck.GetPrim().GetReferences().AddInternalReference(TRUCK)
                truck.GetPrim().SetInstanceable(False)
                op = truck.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, 'double20TruckPose')
                truck.SetXformOpOrder([op], resetXformStack=True)
                op.Set(self.truck_world)
                self.trucks.append((truck, op))
                imageable = UsdGeom.Imageable(truck)
                imageable.CreateVisibilityAttr().Set(
                    UsdGeom.Tokens.inherited if name == 'TruckInbound'
                    else UsdGeom.Tokens.invisible
                )
                self._truck_visibility[name] = imageable.GetVisibilityAttr().Get()
            self.inbound_truck, self.inbound_op = self.trucks[0]
            self.return_truck, self.return_op = self.trucks[1]
            self.pickup_truck, self.pickup_op = self.trucks[2]
            UsdGeom.Imageable(self.return_truck).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            UsdGeom.Imageable(self.pickup_truck).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            self.set_truck(self.inbound_op, self.truck_start_world(self.ship_start))
            self.set_truck(self.return_op, self.truck_start_world(self.yard_truck_start))
            self.set_truck(self.pickup_op, Gf.Vec3d(-238.0, 100.0, self.deck_z))
        self.cargo_mode = 'ship'
        self.phase = 'Truck 1 approaching ship'
        self.c.hold()

    def check_occupancy(self, layout):
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render'])
        half = Gf.Vec3d(.685, 1.33, self.height / 2)
        for center in (self.yard_center - Gf.Vec3d(0, self.yard_center[1] - self.yard_tops[0][1], 0),
                       self.yard_center - Gf.Vec3d(0, self.yard_center[1] - self.yard_tops[1][1], 0)):
            probe = Gf.Vec3d(center[0], center[1], self.yard_center[2])
            for prim in self.stage.Traverse():
                if prim.GetPath().HasPrefix('/World/STSUnloadTask'):
                    continue
                name = prim.GetName().lower()
                if 'container' not in name or not prim.IsA(UsdGeom.Boundable):
                    continue
                box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
                if all(box.GetMin()[i] < probe[i] + half[i] - .01 and
                       box.GetMax()[i] > probe[i] - half[i] + .01 for i in range(3)):
                    raise ValueError('2C/004 的双20目标位已有箱子: ' + str(prim.GetPath()))

    def truck_start_world(self, point):
        return Gf.Vec3d(point)

    def set_truck(self, op, point, heading=180.0):
        pivot = Gf.Vec3d(self.truck_deck[0], self.truck_deck[1], self.deck_z)
        rotation = Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), heading))
        op.Set(self.truck_world * translate(-pivot) * rotation * translate(point))

    def set_box_world(self, index, center, heading=0.0, preserve_pair_offset=True):
        if not self.box_world_anchored:
            # The cargo layer is a world-level sea-motion companion, not a
            # child of the ship. Capture both boxes at the instant of pickup,
            # then make their task-layer transforms world-rooted. This avoids
            # mixing the moving companion parent with the referenced box's
            # own translate/scale stack on every frame.
            cache = UsdGeom.XformCache()
            current_centers = []
            for path in self.box_paths:
                prim = self.stage.GetPrimAtPath(path)
                current_centers.append(bounds(self.stage, path).GetMidpoint())
                self.box_world_matrices[len(current_centers) - 1] = (
                    cache.GetLocalToWorldTransform(prim)
                )
            self.box_centers = current_centers
            self.source_center = sum(current_centers, Gf.Vec3d(0)) / 2
            self.offsets = [p - self.source_center for p in current_centers]
            for path, op in zip(self.box_paths, self.box_ops):
                UsdGeom.Xformable(self.stage.GetPrimAtPath(path)).SetXformOpOrder(
                    [op], resetXformStack=True
                )
            self.box_world_anchored = True
        rotation = Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), heading))
        desired = Gf.Vec3d(center)
        if preserve_pair_offset:
            desired += self.offsets[index]
        delta = translate(-self.box_centers[index]) * rotation * translate(desired)
        self.box_ops[index].Set(self.box_world_matrices[index] * delta)

    def set_pair_center(self, center, heading=0.0):
        for index in range(2):
            self.set_box_world(index, Gf.Vec3d(center), heading)

    def set_pair_to_rtg(self):
        center = self.rtg_contact() - Gf.Vec3d(0, 0, self.height / 2)
        for index, offset in enumerate(self.yard_pair_offsets):
            self.set_box_world(index, center + offset,
                               preserve_pair_offset=False)

    def set_pair_to_sts(self):
        """Keep the pair under the STS spreader while lifting from the ship."""
        self.set_pair_center(self.contact() - Gf.Vec3d(0, 0, self.height / 2))

    def set_pair_to_yard(self):
        """Place each 20 ft box in its own PLC-aligned yard bay slot."""
        for index, top in enumerate(self.yard_tops):
            center = Gf.Vec3d(top) - Gf.Vec3d(0, 0, self.height / 2)
            # The ship pair is separated laterally in X; yard slots are laid
            # out along Y. Use explicit PLC-aligned bay centres without adding
            # an extra yaw to the container models.
            self.set_box_world(index, center,
                               preserve_pair_offset=False)

    def set_pair_to_truck(self, point, heading=180.0):
        center = Gf.Vec3d(point[0], point[1], self.deck_z + self.height / 2 + .02)
        truck_rotation = Gf.Matrix4d(1).SetRotate(
            Gf.Rotation(Gf.Vec3d(0, 0, 1), heading)
        )
        for index, offset in enumerate(self.yard_pair_offsets):
            offset_in_truck_frame = truck_rotation.TransformDir(offset)
            self.set_box_world(index, center + offset_in_truck_frame,
                               heading=heading,
                               preserve_pair_offset=False)

    def assert_pair_under_sts(self):
        """Reject a visual/logic-only pickup if the trailer load is not below STS."""
        centers = [bounds(self.stage, path).GetMidpoint() for path in self.box_paths]
        pair_center = sum(centers, Gf.Vec3d(0)) / len(centers)
        pair_top = max(bounds(self.stage, path).GetMax()[2]
                       for path in self.box_paths)
        contact = self.contact()
        horizontal_error = Gf.Vec2d(contact[0] - pair_center[0],
                                    contact[1] - pair_center[1]).GetLength()
        vertical_error = abs(contact[2] - pair_top)
        if horizontal_error > .75 or vertical_error > .5:
            raise ValueError(
                'STS_03 未对准集卡上的双20，已暂停以避免虚空抓取 '
                f'(水平偏差 {horizontal_error:.2f} m，垂直偏差 {vertical_error:.2f} m)'
            )

    def move_pair_with_truck(self, point, heading=180.0):
        self.set_pair_to_truck(point, heading)

    def go(self, phase, pose):
        self.phase = phase
        self.c.command(**pose)

    def _truck_route_tick(self, dt, route, index_attr, position_attr, op, cargo=False):
        position = getattr(self, position_attr)
        route_index = getattr(self, index_attr)
        remaining = dt * 8.0
        while remaining > 0 and route_index < len(route):
            delta = route[route_index] - position
            length = delta.GetLength()
            if length < 1e-8:
                route_index += 1
                continue
            step = min(remaining, length)
            position += delta * (step / length)
            remaining -= step
            heading = math.degrees(math.atan2(delta[0], -delta[1]))
            self.set_truck(op, position, heading)
            if cargo:
                self.move_pair_with_truck(position, heading)
            if step == length:
                route_index += 1
        setattr(self, position_attr, position)
        setattr(self, index_attr, route_index)
        return route_index == len(route)

    def tick(self, dt):
        if self.finished:
            # Keep the quay truck parked even after the cargo transfer is
            # complete; another stage/timeline update must not pull it back.
            if self.pickup_parked:
                with Usd.EditContext(self.stage, self.layer):
                    self.pickup_position = Gf.Vec3d(self.ship_stop)
                    self.set_truck(self.pickup_op, self.pickup_position,
                                   self.pickup_heading)
            return
        if self.paused or self.c.stopped:
            return
        dt = max(0.0, min(float(dt), .1))
        with Usd.EditContext(self.stage, self.layer):
            rtg_ready = self.move_rtg(dt)
            # Once the return truck reaches the quay, keep its transform fixed
            # at the same STS_03 handoff point used by the first truck. This
            # prevents any later route/timeline update from sending it back to
            # the yard while the crane is picking the load.
            if self.pickup_parked:
                self.pickup_position = Gf.Vec3d(self.ship_stop)
                self.set_truck(self.pickup_op, self.pickup_position,
                               self.pickup_heading)
            if self.cargo_mode == 'sts':
                self.set_pair_to_sts()
            elif self.cargo_mode == 'rtg':
                self.set_pair_to_rtg()
            elif self.cargo_mode == 'truck_in':
                self.move_pair_with_truck(self.inbound_position, self.inbound_heading)
            elif self.cargo_mode == 'truck_out':
                self.move_pair_with_truck(self.pickup_position, self.pickup_heading)

            if self.phase == 'Truck 1 approaching ship':
                self.elapsed += dt
                fraction = min(self.elapsed / 8.0, 1.0)
                point = self.ship_start + (self.ship_stop - self.ship_start) * fraction
                self.set_truck(self.inbound_op, point)
                if fraction == 1.0:
                    self.go('STS raise clear', self.pick_high)
                return
            if self.phase == 'Truck 1 and 2 leaving 2C':
                inbound_done = self._truck_route_tick(
                    dt, self.inbound_departure_route, 'inbound_departure_index',
                    'inbound_position', self.inbound_op)
                return_done = self._truck_route_tick(
                    dt, self.return_departure_route, 'return_departure_index',
                    'return_position', self.return_op)
                if inbound_done and return_done:
                    for truck in (self.inbound_truck, self.return_truck):
                        UsdGeom.Imageable(truck).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
                    UsdGeom.Imageable(self.pickup_truck).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                    self.pickup_position = Gf.Vec3d(-238.0, 100.0, self.deck_z)
                    self.pickup_route = rounded_route([
                        self.pickup_position, (-238.0, 126.05, self.deck_z),
                        (self.yard_truck_x, 126.05, self.deck_z), self.yard_stop])
                    self.pickup_route_index = 1
                    self.phase = 'Empty truck approaching 2C'
                return
            if self.phase == 'Empty truck approaching 2C':
                if self._truck_route_tick(dt, self.pickup_route, 'pickup_route_index',
                                          'pickup_position', self.pickup_op):
                    self.phase = 'RTG approaching double20 in 2C'
                    self.rtg_command(Gf.Vec3d(
                        self.yard_center_top[0], self.yard_center_top[1], self.rtg_high
                    ))
                return
            if self.phase == 'Truck 3 carrying double20 to STS_03':
                if self._truck_route_tick(dt, self.pickup_ship_route,
                                          'pickup_ship_route_index',
                                          'pickup_position', self.pickup_op, True):
                    # Snap to the known quay handoff coordinate and enter a
                    # one-way parked state before STS_03 starts its pickup.
                    self.pickup_position = Gf.Vec3d(self.ship_stop)
                    final_leg = (self.pickup_ship_route[-1]
                                 - self.pickup_ship_route[-2])
                    self.pickup_heading = math.degrees(
                        math.atan2(final_leg[0], -final_leg[1])
                    )
                    self.set_truck(self.pickup_op, self.pickup_position,
                                   self.pickup_heading)
                    self.set_pair_to_truck(self.pickup_position,
                                           self.pickup_heading)
                    self.pickup_parked = True
                    self.go('STS_03 truck parked, crane approaching', self.place_high)
                return
            if any(abs(self.c.state[axis] - value) > .0001
                   for axis, value in self.c.targets.items()):
                return
            if self.phase == 'STS raise clear':
                self.go('STS align with double20', self.pick_high)
            elif self.phase == 'STS align with double20':
                self.go('STS lower to double20', self.pick_pose)
            elif self.phase == 'STS lower to double20':
                self.cargo_mode = 'sts'
                self.go('STS locked - lift double20', self.pick_high)
            elif self.phase == 'STS locked - lift double20':
                self.go('STS transfer to truck 1', self.place_high)
            elif self.phase == 'STS transfer to truck 1':
                self.go('STS lower onto truck 1', self.pose(self.place_top))
            elif self.phase == 'STS lower onto truck 1':
                self.cargo_mode = 'truck_in'
                self.inbound_position = self.ship_stop
                self.inbound_heading = 180.0
                self.set_pair_to_truck(self.inbound_position, self.inbound_heading)
                self.delivering = True
                self.inbound_route = rounded_route([
                    self.ship_stop, (-238.0, 126.05, self.deck_z),
                    (self.yard_truck_x, 126.05, self.deck_z), self.yard_stop])
                self.inbound_route_index = 1
                self.phase = 'Truck 1 to 2C/004'
                self.rtg_command(Gf.Vec3d(self.rtg_origin[0], self.rtg_origin[1], self.rtg_high))
            elif self.phase == 'Truck 1 to 2C/004':
                if self._truck_route_tick(dt, self.inbound_route, 'inbound_route_index',
                                           'inbound_position', self.inbound_op, True):
                    self.phase = 'RTG approaching truck 1 at 2C/004'
                    self.rtg_command(Gf.Vec3d(self.yard_stop[0], self.yard_stop[1], self.rtg_high))
            elif self.phase == 'RTG approaching truck 1 at 2C/004' and rtg_ready:
                self.rtg_command(self.yard_stop + Gf.Vec3d(0, 0, self.height / 2 + .02))
                self.phase = 'RTG lower to truck 1'
            elif self.phase == 'RTG lower to truck 1' and rtg_ready:
                self.cargo_mode = 'rtg'
                self.rtg_command(Gf.Vec3d(self.yard_stop[0], self.yard_stop[1], self.rtg_high))
                self.phase = 'RTG locked - lift double20'
            elif self.phase == 'RTG locked - lift double20' and rtg_ready:
                self.rtg_command(self.yard_center_top)
                self.phase = 'RTG lower to 2C/004'
            elif self.phase == 'RTG lower to 2C/004' and rtg_ready:
                self.cargo_mode = 'yard'
                self.set_pair_to_yard()
                self.rtg_command(Gf.Vec3d(self.yard_center_top[0], self.yard_center_top[1], self.rtg_high))
                UsdGeom.Imageable(self.return_truck).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                self.return_position = self.yard_truck_start
                self.inbound_position = Gf.Vec3d(self.yard_stop)
                self.inbound_departure_route = rounded_route([
                    self.yard_stop, self.yard_truck_start,
                    (self.yard_truck_x, 100.0, self.deck_z),
                    (-238.0, 100.0, self.deck_z)])
                self.return_departure_route = rounded_route([
                    self.yard_truck_start,
                    (self.yard_truck_x, 100.0, self.deck_z),
                    (-238.0, 100.0, self.deck_z)])
                self.inbound_departure_index = 1
                self.return_departure_index = 1
                self.phase = 'Truck 1 and 2 leaving 2C'
            elif self.phase == 'RTG approaching double20 in 2C' and rtg_ready:
                self.rtg_command(self.yard_center_top)
                self.phase = 'RTG lower to double20 in 2C'
            elif self.phase == 'RTG lower to double20 in 2C' and rtg_ready:
                self.cargo_mode = 'rtg'
                self.rtg_command(Gf.Vec3d(
                    self.yard_center_top[0], self.yard_center_top[1], self.rtg_high
                ))
                self.phase = 'RTG lifting double20 from 2C'
            elif self.phase == 'RTG lifting double20 from 2C' and rtg_ready:
                self.rtg_command(Gf.Vec3d(
                    self.yard_stop[0], self.yard_stop[1], self.rtg_high
                ))
                self.phase = 'RTG transfer to empty truck'
            elif self.phase == 'RTG transfer to empty truck' and rtg_ready:
                self.rtg_command(self.yard_stop + Gf.Vec3d(0, 0, self.height / 2 + .02))
                self.phase = 'RTG lower onto empty truck'
            elif self.phase == 'RTG lower onto empty truck' and rtg_ready:
                self.cargo_mode = 'truck_out'
                self.pickup_position = Gf.Vec3d(self.yard_stop)
                self.pickup_heading = 180.0
                self.set_pair_to_truck(self.pickup_position, self.pickup_heading)
                # The empty truck returns to the exact STS_03 handoff point
                # using the nearest access causeway, then waits there for STS_03.
                self.pickup_ship_route = rounded_route([
                    self.yard_stop,
                    (self.yard_truck_x, self.ship_causeway_y, self.deck_z),
                    # Follow the actual ground -> causeway -> quay connection
                    # instead of cutting diagonally across the water.
                    (-101.50, self.ship_causeway_y, self.deck_z),
                    (-221.50, self.ship_causeway_y, self.deck_z),
                    (-238.0, self.ship_causeway_y, self.deck_z),
                    self.ship_stop,
                ])
                self.pickup_ship_route_index = 1
                self.phase = 'Truck 3 carrying double20 to STS_03'
            elif self.phase == 'STS_03 truck parked, crane approaching':
                self.go('STS_03 lower to truck cargo', self.pose(self.place_top))
            elif self.phase == 'STS_03 lower to truck cargo':
                self.assert_pair_under_sts()
                self.cargo_mode = 'sts'
                self.go('STS_03 lift from truck', self.place_high)
            elif self.phase == 'STS_03 lift from truck':
                self.go('STS_03 transfer back to ship', self.pick_high)
            elif self.phase == 'STS_03 transfer back to ship':
                self.go('STS_03 lower onto ship', self.pick_pose)
            elif self.phase == 'STS_03 lower onto ship':
                self.set_pair_center(self.source_center)
                self.cargo_mode = 'ship'
                self.phase = 'Complete: double20 returned to STS_03 ship'
                self.finished = True

    def close(self):
        if getattr(self, 'c', None):
            self.c.hold()
        session = self.stage.GetSessionLayer()
        if self.layer.identifier in session.subLayerPaths:
            session.subLayerPaths.remove(self.layer.identifier)
        if getattr(self, 'c', None):
            if hasattr(self, 'home_limits'):
                self.c.limits = dict(self.home_limits)
            self.c.state = dict(self.home)
            self.c.targets = dict(self.home)
            self.c.spreader_size_ft = self.home_size
            self.c._apply()
        sea = getattr(sys.modules.get('builtins'), '_smart_port_sea_motion', None)
        if sea and not sea.closed and sea.stage == self.stage:
            sea.release(SHIP)
        self.finished = True


def cache_world(stage, path):
    return UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path))

