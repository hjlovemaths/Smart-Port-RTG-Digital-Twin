"""Bounded, Z-up model viewing for the web client; edits only a session camera."""
import math

CAMERA_PATH = "/World/SmartPortWebCamera"
RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL"
MIN_DISTANCE, MAX_DISTANCE = 14.0, 160.0
MIN_PITCH, MAX_PITCH = 12.0, 78.0
MAX_PAN = 35.0
STATIC_RTG_NAMES = ("M60", "M30", "P30", "P60", "P90")


def ensure_complete_rtg_instances(stage):
    """Avoid incomplete RTX drawing of the static cranes' shared prototypes."""
    from pxr import Usd
    changed = []
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for name in STATIC_RTG_NAMES:
            prim = stage.GetPrimAtPath(f"/World/PortAndRTG/SMART_PORT_STATIC_RTG_YARD_{name}")
            if prim and prim.IsInstanceable():
                prim.SetInstanceable(False)
                changed.append(str(prim.GetPath()))
    return changed


def finite_number(payload, name, default=0.0, limit=10000):
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid navigation {name}")
    return max(-limit, min(limit, float(value)))


def rtg_visibility(stage, restore=False):
    """Inspect/clear temporary hiding of RTGs, retaining authored hidden parts."""
    from pxr import Sdf, Usd, UsdGeom
    parent = stage.GetPrimAtPath("/World/PortAndRTG")
    roots = [prim for prim in parent.GetChildren()
             if prim.GetName() == "RTG_PRIMARY_DYNAMIC" or prim.GetName().startswith("SMART_PORT_STATIC_RTG_YARD_")] if parent else []
    hidden = []
    layer = stage.GetSessionLayer()
    def inspect(path):
        if not path.IsPropertyPath() or path.name != "visibility":
            return
        if not any(path.GetPrimPath().HasPrefix(prim.GetPath()) for prim in roots):
            return
        spec = layer.GetAttributeAtPath(path)
        if spec and spec.default == UsdGeom.Tokens.invisible:
            hidden.append(path)
    layer.Traverse(Sdf.Path.absoluteRootPath, inspect)
    # Restore whole cranes only; hidden lamps/ropes/parts can be intentional.
    restore_paths = [path for path in hidden if any(path.GetPrimPath() == prim.GetPath() for prim in roots)]
    if restore:
        with Usd.EditContext(stage, layer):
            for path in restore_paths:
                stage.GetAttributeAtPath(path).Clear()
    components = []
    model_parts = []
    for root in roots:
        if root.GetName() == "RTG_PRIMARY_DYNAMIC":
            continue
        meshes = []
        iterator = iter(Usd.PrimRange(root, Usd.TraverseInstanceProxies()))
        for prim in iterator:
            if prim.IsA(UsdGeom.Mesh):
                meshes.append({"path": str(prim.GetPath()).replace(str(root.GetPath()), "", 1),
                               "visible": str(UsdGeom.Imageable(prim).ComputeVisibility()),
                               "purpose": str(UsdGeom.Imageable(prim).ComputePurpose())})
            attr = prim.GetAttribute("visibility")
            if attr and attr.Get() == UsdGeom.Tokens.invisible:
                components.append({"path": str(prim.GetPath()), "type": prim.GetTypeName(),
                    "opinions": [{"layer": spec.layer.identifier, "path": str(spec.path),
                                  "value": str(spec.default)} for spec in attr.GetPropertyStack()[:3]]})
                iterator.PruneChildren()
                if len(components) >= 60:
                    break
        model_parts.append({"path": str(root.GetPath()), "mesh_count": len(meshes),
                            "visible_mesh_count": sum(mesh["visible"] == "inherited" for mesh in meshes)})
    return {"ok": True, "rtgs": [{"path": str(prim.GetPath()),
            "visibility": str(UsdGeom.Imageable(prim).ComputeVisibility())} for prim in roots],
            "temporary_hidden_paths": [str(path) for path in hidden], "restored": len(restore_paths) if restore else 0,
            "hidden_components": components, "model_parts": model_parts}


class ViewportNavigation:
    def __init__(self):
        self.stage = self.viewport = None
        self.yaw, self.pitch, self.distance = 60.0, 20.0, 60.0
        self.pan = [0.0, 0.0]
        self.last_pose = None
        self.focus_path, self.focus_local = RTG_PATH, (0.0, 5.0, 4.8)
        self.focus_world = None
        self.focus_radius = 0.0
        self.min_distance, self.max_distance = MIN_DISTANCE, MAX_DISTANCE
        self.min_pitch, self.max_pitch = MIN_PITCH, MAX_PITCH
        self.max_pan, self.max_height = MAX_PAN, 180.0
        self.home = (60.0, 20.0, 60.0)
        self.lens = (36.0, 20.25, 36 / (2 * math.tan(math.radians(55 / 2))))

    def _context(self):
        import omni.usd
        from omni.kit.viewport.utility import get_active_viewport, get_viewport_from_window_name
        stage = omni.usd.get_context().get_stage()
        viewport = get_viewport_from_window_name("Viewport") or get_active_viewport()
        if stage is None or viewport is None:
            raise ValueError("港区视图尚未就绪")
        return stage, viewport

    def _anchor(self, stage):
        from pxr import Gf, Usd, UsdGeom
        prim = stage.GetPrimAtPath(self.focus_path) if self.focus_path else None
        if prim:
            point = UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(*self.focus_local))
            return Gf.Vec3d(point[0], point[1], max(2.0, point[2]))
        if self.focus_world is not None:
            return Gf.Vec3d(*self.focus_world)
        return Gf.Vec3d(0, 0, 4.8)

    def _attach(self, stage, viewport, reset=False):
        from pxr import Gf, UsdGeom
        if self.stage is not stage or str(viewport.camera_path) != CAMERA_PATH:
            if self.stage is not stage:
                # A new stage can reuse the same camera paths at different places.
                self.focus_path, self.focus_local = RTG_PATH, (0.0, 5.0, 4.8)
                self.focus_world, self.focus_radius = None, 0.0
                self.min_distance, self.max_distance = MIN_DISTANCE, MAX_DISTANCE
                self.min_pitch, self.max_pitch = MIN_PITCH, MAX_PITCH
                self.max_pan, self.max_height = MAX_PAN, 180.0
                self.lens = (36.0, 20.25, 36 / (2 * math.tan(math.radians(55 / 2))))
            self.yaw, self.pitch, self.distance = 60.0, 20.0, 60.0
            self.pan = [0.0, 0.0]
            adopted = False
            prim = stage.GetPrimAtPath(str(viewport.camera_path))
            if prim:
                transforms = UsdGeom.XformCache()
                eye = transforms.GetLocalToWorldTransform(prim).ExtractTranslation()
                center_attr = prim.GetAttribute("smartport:navigationCenter")
                center = center_attr.Get() if center_attr else None
                adopted = center is not None and all(math.isfinite(v) for v in center)
                if adopted:
                    self.focus_world = tuple(center)
                    target_attr = prim.GetAttribute("smartport:navigationTarget")
                    self.focus_path = (target_attr.Get() or "") if target_attr else ""
                    target = stage.GetPrimAtPath(self.focus_path) if self.focus_path else None
                    self.focus_local = tuple(transforms.GetLocalToWorldTransform(target).GetInverse().Transform(Gf.Vec3d(center))) if target else (0, 0, 0)
                    radius_attr = prim.GetAttribute("smartport:navigationRadius")
                    self.focus_radius = float(radius_attr.Get() or 0) if radius_attr else 0.0
                    look_attr = prim.GetAttribute("omni:kit:centerOfInterest")
                    look = look_attr.Get() if look_attr else center
                    look = center if look is None else look
                    self.pan = [float(look[0]-center[0]), float(look[1]-center[1])]
                    delta = eye-Gf.Vec3d(look)
                    self.min_distance = min(MIN_DISTANCE, max(.5, delta.GetLength()*.5))
                    self.max_distance = max(MAX_DISTANCE, self.focus_radius*5, delta.GetLength()*1.3)
                    self.max_pan = max(MAX_PAN, min(150, self.focus_radius*.65))
                    self.max_height = max(180, center[2]+self.max_distance)
                    camera = UsdGeom.Camera(prim)
                    self.lens = (camera.GetHorizontalApertureAttr().Get(), camera.GetVerticalApertureAttr().Get(), camera.GetFocalLengthAttr().Get())
                else:
                    delta = eye - self._anchor(stage)
                distance = delta.GetLength()
                if eye[2] >= 2 and distance > 1e-6 and (adopted or MIN_DISTANCE <= distance <= MAX_DISTANCE):
                    self.distance = distance
                    self.pitch = math.degrees(math.asin(max(-1, min(1, delta[2] / distance))))
                    self.yaw = math.degrees(math.atan2(delta[0], -delta[1]))
                    if adopted:
                        self.min_pitch = min(MIN_PITCH, max(2, self.pitch))
                        self.max_pitch = max(MAX_PITCH, min(89.999, self.pitch))
                        if math.hypot(delta[0], delta[1]) < 1e-6:
                            right = transforms.GetLocalToWorldTransform(prim).TransformDir(Gf.Vec3d(1, 0, 0))
                            self.yaw = math.degrees(math.atan2(right[1], right[0]))
            self.home = (self.yaw, self.pitch, self.distance) if adopted else (60.0, 20.0, 60.0)
            self.last_pose = None
        if reset:
            self.yaw, self.pitch, self.distance = self.home
            self.pan = [0, 0]
            self.last_pose = None
        self.stage, self.viewport = stage, viewport

    def _constrain(self):
        self.distance = max(self.min_distance, min(self.max_distance, self.distance))
        self.pitch = max(self.min_pitch, min(self.max_pitch, self.pitch))
        self.yaw %= 360
        radius = math.hypot(*self.pan)
        # Tighten panning when zoomed in so the model cannot leave the frame.
        pan_limit = min(self.max_pan, self.distance * .25)
        if radius > pan_limit:
            self.pan = [v * pan_limit / radius for v in self.pan]

    def _write(self):
        from pxr import Gf, Sdf, Usd, UsdGeom
        self._constrain()
        center = self._anchor(self.stage) + Gf.Vec3d(*self.pan, 0)
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        horizontal = self.distance * math.cos(pitch)
        eye = center + Gf.Vec3d(horizontal * math.sin(yaw), -horizontal * math.cos(yaw), self.distance * math.sin(pitch))
        # The look-at up vector removes roll; the floor prevents underwater views.
        eye[2] = max(2.0, min(self.max_height, eye[2]))
        pose = Gf.Matrix4d(1).SetLookAt(eye, center, Gf.Vec3d(0, 0, 1)).GetInverse()
        prim = self.stage.GetPrimAtPath(CAMERA_PATH)
        if pose != self.last_pose or not prim:
            with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                camera = UsdGeom.Camera.Define(self.stage, CAMERA_PATH)
                camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
                camera.CreateHorizontalApertureAttr(self.lens[0])
                camera.CreateVerticalApertureAttr(self.lens[1])
                camera.CreateFocalLengthAttr(self.lens[2])
                camera.CreateClippingRangeAttr(Gf.Vec2f(0.1, max(2500, self.max_distance*10)))
                prim = camera.GetPrim()
                prim.CreateAttribute("omni:kit:cameraLock", Sdf.ValueTypeNames.Bool).Set(True)
                prim.CreateAttribute("omni:kit:centerOfInterest", Sdf.ValueTypeNames.Vector3d, custom=True).Set(center)
                prim.CreateAttribute("smartport:navigationTarget", Sdf.ValueTypeNames.String, custom=True).Set(self.focus_path)
                prim.CreateAttribute("smartport:navigationCenter", Sdf.ValueTypeNames.Vector3d, custom=True).Set(self._anchor(self.stage))
                prim.CreateAttribute("smartport:navigationRadius", Sdf.ValueTypeNames.Double, custom=True).Set(self.focus_radius)
                transform = prim.GetAttribute("xformOp:transform")
                if not transform:
                    transform = UsdGeom.Xformable(prim).AddTransformOp().GetAttr()
                transform.Set(pose)
                prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(["xformOp:transform"])
            self.last_pose = pose
        if str(self.viewport.camera_path) != CAMERA_PATH:
            self.viewport.camera_path = CAMERA_PATH
        return {"ok": True, "distance_m": self.distance, "pitch_deg": self.pitch,
                "height_m": float(eye[2]), "pan_m": list(self.pan),
                "camera_path": CAMERA_PATH, "eye_m": list(eye), "target_m": list(center), "focus_path": self.focus_path}

    def execute(self, payload):
        action = payload.get("action", "move")
        if action not in ("move", "reset", "state", "inspect_visibility", "restore_visibility", "snapshot"):
            raise ValueError("Invalid navigation action")
        # Validate the complete request before changing the current view.
        dx, dy = (finite_number(payload, key, limit=180) for key in ("orbit_x", "orbit_y"))
        px, py = (finite_number(payload, key, limit=100) for key in ("pan_x", "pan_y"))
        zoom = finite_number(payload, "zoom", limit=3)
        stage, viewport = self._context()
        if action == "snapshot":
            import asyncio
            import time
            from pathlib import Path
            from omni.kit.viewport.utility import capture_viewport_to_file
            output = Path(__file__).resolve().parents[5] / "outputs" / f"rtg-visibility-{time.time_ns()}.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            model = payload.get("model")
            if model is not None and model not in ("M60", "M30", "P30", "P60", "P90"):
                raise ValueError("Invalid RTG preview")
            async def capture():
                previous_camera = str(viewport.camera_path)
                try:
                    if model:
                        from pxr import Gf, Usd, UsdGeom
                        from omni.kit.app import get_app
                        root = stage.GetPrimAtPath(f"/World/PortAndRTG/SMART_PORT_STATIC_RTG_YARD_{model}")
                        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(root).ComputeAlignedRange()
                        center = bounds.GetMidpoint()
                        distance = max(25.0, bounds.GetSize().GetLength() * 1.15)
                        eye = center + Gf.Vec3d(.7, -.7, .4) * distance
                        with Usd.EditContext(stage, stage.GetSessionLayer()):
                            camera = UsdGeom.Camera.Define(stage, "/World/RTGVisibilityPreviewCamera")
                            camera.CreateFocalLengthAttr(28)
                            camera.CreateHorizontalApertureAttr(36)
                            camera.CreateVerticalApertureAttr(20.25)
                            camera.MakeInvisible()
                            xform = UsdGeom.Xformable(camera)
                            ops = xform.GetOrderedXformOps()
                            op = ops[0] if ops else xform.AddTransformOp()
                            op.Set(Gf.Matrix4d(1).SetLookAt(eye, center, Gf.Vec3d(0, 0, 1)).GetInverse())
                        viewport.camera_path = camera.GetPath()
                        for _ in range(12):
                            await get_app().next_update_async()
                    await capture_viewport_to_file(viewport, str(output)).wait_for_result(completion_frames=4)
                finally:
                    if model:
                        viewport.camera_path = previous_camera
            asyncio.ensure_future(capture())
            return {"ok": True, "path": str(output)}
        if action in ("inspect_visibility", "restore_visibility"):
            import carb.settings
            settings = carb.settings.get_settings()
            result = rtg_visibility(stage, restore=action == "restore_visibility")
            result["camera_helpers_visible"] = settings.get("/app/viewport/createCameraModelRep")
            return result
        if action == "state":
            from pxr import UsdGeom
            prim = stage.GetPrimAtPath(str(viewport.camera_path))
            eye = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation() if prim else None
            return {"ok": True, "active": str(viewport.camera_path) == CAMERA_PATH,
                    "camera_path": str(viewport.camera_path), "eye_m": list(eye) if eye is not None else None,
                    "distance_m": self.distance, "pitch_deg": self.pitch, "pan_m": list(self.pan)}
        self._attach(stage, viewport, reset=action == "reset")
        self.yaw -= dx * .22
        self.pitch += dy * .18
        yaw = math.radians(self.yaw)
        scale = self.distance * .0015
        self.pan[0] += (-px * math.cos(yaw) - py * math.sin(yaw)) * scale
        self.pan[1] += (-px * math.sin(yaw) + py * math.cos(yaw)) * scale
        self.distance *= math.exp(-zoom * .10)
        return self._write()

    def tick(self):
        # Continue following RTG translation, without overriding an Agent camera.
        if self.stage is None or self.viewport is None:
            return
        import omni.usd
        if omni.usd.get_context().get_stage() is not self.stage:
            self.stage = self.viewport = None
            return
        if str(self.viewport.camera_path) == CAMERA_PATH:
            # Reapply the bounded pose if another input source changed it.
            prim = self.stage.GetPrimAtPath(CAMERA_PATH)
            if prim and prim.GetAttribute("xformOp:transform").Get() != self.last_pose:
                self.last_pose = None
            self._write()
