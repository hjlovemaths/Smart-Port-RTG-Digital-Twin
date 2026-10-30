"""Dockable top/side/whole-RTG work views sharing the live USD stage."""
from __future__ import annotations

import asyncio
import builtins
import math
import time

KEY = "_smart_port_rtg_work_views"
TITLE = "RTG Work Views"
ROOT_PATH = "/World/SmartPortWorkViews"
TOP_WIDE_FOCAL_MM = 36.0 / (2 * math.tan(math.radians(110) / 2))
TOP_ZOOM_POINTS = ((.5, 48.0), (5.0, 40.0), (10.0, 24.0), (15.0, TOP_WIDE_FOCAL_MM), (18.0, 9.0))
TOP_ZOOM_RESPONSE_S = .45
VIEW_NAMES = ("Top", "Side", "Overview")
VIEW_DEFAULTS = {"Top": True, "Side": True, "Overview": False}
VIEW_SETTINGS_ROOT = "/persistent/smartport/workViews"
OVERVIEW_FOV_DEG = 45.0
OVERVIEW_AZIMUTH_DEG = -12.0
OVERVIEW_ELEVATION_DEG = 8.0


def overview_pose(bounds):
    """Near-frontal whole-crane composition with a slight downward view."""
    from pxr import Gf
    centre = bounds.GetMidpoint()
    azimuth = math.radians(OVERVIEW_AZIMUTH_DEG)
    outward = Gf.Vec3d(math.sin(azimuth), -math.cos(azimuth), 0)
    elevation = math.radians(OVERVIEW_ELEVATION_DEG)
    offset = outward * math.cos(elevation) + Gf.Vec3d(0, 0, math.sin(elevation))
    tan_h = math.tan(math.radians(OVERVIEW_FOV_DEG) / 2)
    tan_v = tan_h * .75
    corners = [Gf.Vec3d(x, y, z)
               for x in (bounds.GetMin()[0], bounds.GetMax()[0])
               for y in (bounds.GetMin()[1], bounds.GetMax()[1])
               for z in (bounds.GetMin()[2], bounds.GetMax()[2])]
    distance = max(1.0, bounds.GetSize()[0], bounds.GetSize()[1])
    for _ in range(64):
        eye = centre + offset * distance
        forward = (centre - eye).GetNormalized()
        right = Gf.Cross(forward, Gf.Vec3d(0, 0, 1)).GetNormalized()
        up = Gf.Cross(right, forward).GetNormalized()
        fits = True
        for corner in corners:
            delta = corner - eye
            depth = Gf.Dot(delta, forward)
            if (depth <= .03 or abs(Gf.Dot(delta, right)) * 1.02 > depth * tan_h
                    or abs(Gf.Dot(delta, up)) * 1.02 > depth * tan_v):
                fits = False
                break
        if fits:
            return eye, centre
        distance *= 1.05
    raise ValueError("Cannot frame RTG from the overview camera")


def top_zoom_focal_for_height(height_m):
    """Continuous optical zoom in engineering metres, with bounded endpoints."""
    if not math.isfinite(height_m):
        raise ValueError("Hoist height must be finite")
    for (lo_h, lo_f), (hi_h, hi_f) in zip(TOP_ZOOM_POINTS, TOP_ZOOM_POINTS[1:]):
        if height_m <= hi_h:
            blend = max(0.0, (height_m - lo_h) / (hi_h - lo_h))
            return lo_f + blend * (hi_f - lo_f)
    return TOP_ZOOM_POINTS[-1][1]


class RtgWorkViews:
    def __init__(self, stage):
        from pxr import Gf, Sdf, Usd, UsdGeom
        from rtg_live_control import HOIST_PATH, TROLLEY_PATH, HOIST_JOINT_PATH, GANTRY_PATH
        if not stage or not stage.GetPrimAtPath(TROLLEY_PATH) or not stage.GetPrimAtPath(HOIST_PATH):
            raise ValueError("Open the RTG scene before starting work views")
        self.stage, self.window, self.widgets = stage, None, []
        self.view_enabled = dict(VIEW_DEFAULTS)
        self.view_panels, self.view_frames, self.view_widgets, self.view_models = {}, {}, {}, {}
        self.empty_label = self.settings = None
        self.subscription = self.dock_task = None
        self.visibility_task = None
        self.layer = None
        self.side_sign, self.side_distance = 1, 5.5
        self.error = None
        self.next_update = 0.0
        self.top_auto_zoom = True
        self.top_focal_mm = self.top_target_focal_mm = self.hoist_height_m = None
        self.last_lens_update = time.monotonic()
        self.lens_signatures = {}
        self.hoist_drive = stage.GetPrimAtPath(HOIST_JOINT_PATH).GetAttribute("drive:linear:physics:targetPosition")
        self.trolley_path, self.hoist_path = TROLLEY_PATH, HOIST_PATH
        self.gantry_path = GANTRY_PATH
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        # Measure once: trolley/hoist motion must not make the overview breathe.
        gantry = stage.GetPrimAtPath(GANTRY_PATH)
        bounds = cache.ComputeWorldBound(gantry).ComputeAlignedBox()
        if bounds.IsEmpty():
            raise ValueError("Cannot frame the complete RTG: its bounds are empty")
        inverse = UsdGeom.XformCache().GetLocalToWorldTransform(gantry).GetInverse()
        self.overview_local_center = inverse.Transform(bounds.GetMidpoint())
        overview_eye, overview_target = overview_pose(bounds)
        self.overview_local_eye = inverse.Transform(overview_eye)
        self.overview_local_target = inverse.Transform(overview_target)
        self.overview_local_corners = [inverse.Transform(Gf.Vec3d(x, y, z))
                                       for x in (bounds.GetMin()[0], bounds.GetMax()[0])
                                       for y in (bounds.GetMin()[1], bounds.GetMax()[1])
                                       for z in (bounds.GetMin()[2], bounds.GetMax()[2])]
        self.anchors = []
        for prim in Usd.PrimRange(stage.GetPrimAtPath(HOIST_PATH)):
            if prim.IsA(UsdGeom.Boundable) and "red_corner_lock_foot_" in str(prim.GetPath()):
                point = cache.ComputeLocalBound(prim).ComputeAlignedBox().GetMidpoint()
                self.anchors.append((prim, Gf.Vec3d(point)))
        if len(self.anchors) != 4:
            raise ValueError("Cannot find the four spreader lock feet for the work cameras")
        self.layer = Sdf.Layer.CreateAnonymous("smartport_work_views.usda")
        stage.GetSessionLayer().subLayerPaths.insert(0, self.layer.identifier)
        try:
            self.update_cameras()
        except BaseException:
            self.close()
            raise

    def poses(self):
        from pxr import Gf, UsdGeom
        cache = UsdGeom.XformCache()
        trolley = cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.trolley_path))
        eye_top = trolley.Transform(Gf.Vec3d(2.55, -5.0, 8.0))
        up_top = trolley.TransformDir(Gf.Vec3d(1, 0, 0)).GetNormalized()
        centre = sum((cache.GetLocalToWorldTransform(p).Transform(point) for p, point in self.anchors), Gf.Vec3d(0)) / 4
        horizontal = Gf.Vec3d(up_top[0], up_top[1], 0).GetNormalized()
        side_eye = centre + horizontal * (self.side_distance * self.side_sign) + Gf.Vec3d(0, 0, 1.8)
        # Keep both the contact plane and the top of the container in frame.
        top_fov = math.degrees(2 * math.atan(18 / (self.top_focal_mm or TOP_WIDE_FOCAL_MM)))
        gantry = cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.gantry_path))
        overview_eye = gantry.Transform(self.overview_local_eye)
        overview_target = gantry.Transform(self.overview_local_target)
        return {"Top": (eye_top, eye_top - Gf.Vec3d(0, 0, 5), horizontal, top_fov),
                "Side": (side_eye, centre - Gf.Vec3d(0, 0, .5), Gf.Vec3d(0, 0, 1), 65.0),
                "Overview": (overview_eye, overview_target, Gf.Vec3d(0, 0, 1), OVERVIEW_FOV_DEG)}

    def _update_top_zoom(self, pose, dt):
        from pxr import Gf, UsdGeom
        from rtg_live_control import hoist_usd_to_actual
        value = self.hoist_drive.Get() if self.hoist_drive else None
        if value is None:
            raise RuntimeError("Hoist engineering position is unavailable for auto zoom")
        self.hoist_height_m = hoist_usd_to_actual(float(value))
        target = top_zoom_focal_for_height(self.hoist_height_m) if self.top_auto_zoom else TOP_WIDE_FOCAL_MM
        framing_cap = math.inf
        if self.top_auto_zoom:
            # Always keep all four feet in frame, including yaw and sideshift.
            # The imported model uses compressed height; engineering metres
            # alone cannot determine how much zoom fits the rendered spreader.
            eye, look_at, up, _ = pose
            forward = (look_at - eye).GetNormalized()
            right = Gf.Cross(forward, up).GetNormalized()
            cache = UsdGeom.XformCache()
            for prim, local in self.anchors:
                delta = cache.GetLocalToWorldTransform(prim).Transform(local) - eye
                depth = max(.03, Gf.Dot(delta, forward))
                framing_cap = min(framing_cap,
                                  18 * depth / ((abs(Gf.Dot(delta, right)) + .15) * 1.1),
                                  13.5 * depth / ((abs(Gf.Dot(delta, up)) + .15) * 1.1))
            target = min(target, framing_cap)
        self.top_target_focal_mm = target
        if self.top_focal_mm is None:
            self.top_focal_mm = target
        else:
            blend = 1 - math.exp(-max(0.0, dt) / TOP_ZOOM_RESPONSE_S)
            self.top_focal_mm += (target - self.top_focal_mm) * blend
            # If the hoist rises quickly, preserve framing while the zoom catches up.
            self.top_focal_mm = min(self.top_focal_mm, framing_cap)
            if abs(target - self.top_focal_mm) < .001:
                self.top_focal_mm = target

    def set_top_auto_zoom(self, enabled):
        self.top_auto_zoom = bool(enabled)
        if not enabled:
            self.top_focal_mm = TOP_WIDE_FOCAL_MM
        self.update_cameras(dt=0)

    def update_cameras(self, dt=None):
        from pxr import Gf, Usd, UsdGeom
        now = time.monotonic()
        dt = now - self.last_lens_update if dt is None else dt
        self.last_lens_update = now
        poses = self.poses()
        self._update_top_zoom(poses["Top"], dt)
        with Usd.EditContext(self.stage, self.layer):
            for name, (eye, target, up, fov) in poses.items():
                camera = UsdGeom.Camera.Define(self.stage, ROOT_PATH + "/" + name)
                aperture = 36.0
                focal = self.top_focal_mm if name == "Top" else aperture / (2 * math.tan(math.radians(fov) / 2))
                signature = round(focal, 4)
                if self.lens_signatures.get(name) != signature:
                    # Render through these cameras without drawing their large
                    # editor helper shapes inside the other work views.
                    camera.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
                    camera.CreateHorizontalApertureAttr(aperture)
                    camera.CreateVerticalApertureAttr(aperture * 3 / 4)
                    camera.CreateFocalLengthAttr(signature)
                    camera.CreateClippingRangeAttr(Gf.Vec2f(.03, 3000))
                    self.lens_signatures[name] = signature
                xf = UsdGeom.Xformable(camera)
                ops = xf.GetOrderedXformOps()
                op = ops[0] if ops else xf.AddTransformOp()
                matrix = Gf.Matrix4d().SetLookAt(eye, target, up).GetInverse()
                if op.Get() != matrix:
                    op.Set(matrix)

    def flip_side(self):
        self.side_sign *= -1
        self.update_cameras()

    def zoom_side(self, factor):
        self.side_distance = max(2.0, min(16.0, self.side_distance * factor))
        self.update_cameras()

    def _ensure_view_widget(self, name):
        if name not in self.view_widgets and name in self.view_frames:
            from omni.kit.widget.viewport import ViewportWidget
            with self.view_frames[name]:
                widget = ViewportWidget(usd_context_name="", camera_path=ROOT_PATH + "/" + name,
                                        resolution=(960, 720))
            self.widgets.append(widget)
            self.view_widgets[name] = widget

    def set_view_enabled(self, name, enabled):
        if name not in VIEW_NAMES:
            raise ValueError("Unknown work view: " + str(name))
        enabled = bool(enabled)
        self.view_enabled[name] = enabled
        if self.settings is not None:
            self.settings.set(VIEW_SETTINGS_ROOT + "/show" + name, enabled)
        model = self.view_models.get(name)
        if model is not None and model.as_bool != enabled:
            model.set_value(enabled)
        panel = self.view_panels.get(name)
        if panel is not None:
            panel.visible = enabled
        if enabled:
            self.update_cameras(dt=0)
            self._ensure_view_widget(name)
        if self.empty_label is not None:
            self.empty_label.visible = not any(self.view_enabled.values())
        self._selected_changed(bool(self.window and self.window.selected_in_dock))

    def show(self):
        import omni.kit.app
        import omni.ui as ui
        import carb.settings
        from omni.kit.viewport.utility import get_active_viewport_window
        self.settings = carb.settings.get_settings()
        for name in VIEW_NAMES:
            saved = self.settings.get(VIEW_SETTINGS_ROOT + "/show" + name)
            if saved is not None:
                self.view_enabled[name] = bool(saved)
        active = get_active_viewport_window()
        # utility returns a weak proxy; the native dock_in binding needs a WindowHandle.
        main = ui.Workspace.get_window(active.title if active is not None else "Viewport")
        try:
            self.window = ui.Window(TITLE, width=1400, height=800)
            with self.window.frame:
                with ui.VStack(spacing=3):
                    with ui.HStack(height=28, spacing=5):
                        ui.Label("LIVE RTG | Views", width=140)
                        for name, label in (("Top", "Top down"), ("Side", "Side"), ("Overview", "Overview")):
                            toggle = ui.CheckBox(width=22)
                            toggle.model.set_value(self.view_enabled[name])
                            toggle.model.add_value_changed_fn(
                                lambda model, view=name: self.set_view_enabled(view, model.as_bool))
                            self.view_models[name] = toggle.model
                            ui.Label(label, width=80)
                    with ui.HStack(height=28, spacing=5):
                        ui.Label("Top auto zoom", width=100)
                        zoom = ui.CheckBox(width=22)
                        zoom.model.set_value(self.top_auto_zoom)
                        zoom.model.add_value_changed_fn(lambda model: self.set_top_auto_zoom(model.as_bool))
                        ui.Button("Other side", width=100, clicked_fn=self.flip_side)
                        ui.Button("Closer", width=75, clicked_fn=lambda: self.zoom_side(.8))
                        ui.Button("Wider", width=75, clicked_fn=lambda: self.zoom_side(1.25))
                    with ui.HStack(spacing=4):
                        for name, title in (("Top", "UNDER TROLLEY - TOP DOWN"),
                                            ("Side", "SIDE - SPREADER / CONTAINER"),
                                            ("Overview", "OVERVIEW - WHOLE RTG")):
                            panel = ui.VStack(spacing=2, visible=self.view_enabled[name])
                            self.view_panels[name] = panel
                            with panel:
                                ui.Label(title, height=22)
                                self.view_frames[name] = ui.Frame()
                                if self.view_enabled[name]:
                                    self._ensure_view_widget(name)
                    self.empty_label = ui.Label("All views are off. Enable a view above.",
                                                height=30, visible=not any(self.view_enabled.values()))
            self.window.set_visibility_changed_fn(self._visibility_changed)
            self.window.set_selected_in_dock_changed_fn(self._selected_changed)
            self.subscription = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._on_update, name="SmartPortRtgWorkViews")

            async def dock():
                await omni.kit.app.get_app().next_update_async()
                try:
                    if self.window is not None and main is not None:
                        self.window.dock_in(main, ui.DockPosition.SAME)
                        await omni.kit.app.get_app().next_update_async()
                        if self.window is not None:
                            self.window.focus()
                            self._selected_changed(self.window.selected_in_dock)
                except Exception as exc:
                    self.error = "Docking: " + str(exc)
                    print("[RTG Work Views] " + self.error)
            self.dock_task = asyncio.ensure_future(dock())
        except BaseException:
            self.close()
            raise

    def _selected_changed(self, selected):
        enabled = selected if self.window is not None and self.window.docked else bool(self.window and self.window.visible)
        for name, widget in self.view_widgets.items():
            widget.viewport_api.updates_enabled = bool(enabled and self.view_enabled[name])

    def _visibility_changed(self, visible):
        if not visible:
            import omni.kit.app
            async def close_if_hidden():
                # Docking briefly hides a new tab before it becomes selected.
                for _ in range(3):
                    await omni.kit.app.get_app().next_update_async()
                if self.window is not None and not self.window.visible:
                    self.close()
            if self.visibility_task is None or self.visibility_task.done():
                self.visibility_task = asyncio.ensure_future(close_if_hidden())

    def _on_update(self, event):
        import omni.usd
        if omni.usd.get_context().get_stage() != self.stage:
            self.close()
            return
        if self.window is None or (self.window.docked and not self.window.selected_in_dock):
            return
        if not any(self.view_enabled.values()):
            return
        now = time.monotonic()
        if now < self.next_update:
            return
        self.next_update = now + 1 / 30
        try:
            self.update_cameras()
        except Exception as exc:
            self.error = str(exc)
            self.close()
            print("[RTG Work Views] Stopped: " + str(exc))

    def status(self):
        return {"running": self.window is not None, "window": TITLE, "views": ["under_trolley", "side", "rtg_overview"],
                "camera_paths": [ROOT_PATH + "/" + name for name in VIEW_NAMES],
                "view_enabled": dict(self.view_enabled),
                "resolution_per_view": [960, 720], "side_distance_m": self.side_distance,
                "top_auto_zoom": self.top_auto_zoom, "hoist_height_m": self.hoist_height_m,
                "top_focal_length_mm": self.top_focal_mm, "top_target_focal_length_mm": self.top_target_focal_mm,
                "side": self.side_sign, "error": self.error}

    def close(self):
        self.subscription = None
        if self.visibility_task is not None and not self.visibility_task.done():
            self.visibility_task.cancel()
        self.visibility_task = None
        if self.dock_task is not None and not self.dock_task.done():
            self.dock_task.cancel()
        self.dock_task = None
        window, self.window = self.window, None
        for widget in self.widgets:
            widget.destroy()
        self.widgets.clear()
        self.view_widgets.clear()
        self.view_frames.clear()
        self.view_panels.clear()
        self.view_models.clear()
        self.empty_label = None
        if window is not None:
            window.set_visibility_changed_fn(None)
            window.destroy()
        if self.layer is not None:
            paths = self.stage.GetSessionLayer().subLayerPaths
            if self.layer.identifier in paths:
                paths.remove(self.layer.identifier)
        self.layer = None


def start_rtg_work_views(stage=None):
    import omni.usd
    stage = stage if stage is not None else omni.usd.get_context().get_stage()
    service = getattr(builtins, KEY, None)
    if service is not None and service.stage == stage and service.window is not None:
        service.window.focus()
        return service.status()
    stop_rtg_work_views()
    service = RtgWorkViews(stage)
    setattr(builtins, KEY, service)
    service.show()
    return service.status()


def stop_rtg_work_views():
    service = getattr(builtins, KEY, None)
    if service is not None:
        service.close()
        setattr(builtins, KEY, None)


def get_rtg_work_views_status():
    service = getattr(builtins, KEY, None)
    return service.status() if service is not None else {"running": False}


if __name__ == "__main__":
    start_rtg_work_views()
