"""Clean scene-space device location panels for live RTG inspection."""

from __future__ import annotations

import builtins
import importlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

import rtg_billboard_utils as _billboard_utils

_billboard_utils = importlib.reload(_billboard_utils)
camera_facing_rotate_xyz = _billboard_utils.camera_facing_rotate_xyz
existing_xform_op = _billboard_utils.existing_xform_op
get_or_add_xform_op = _billboard_utils.get_or_add_xform_op


DEVICE_PANEL_FACING_DIAG_ENABLED = False

STATUS_PRIM_PATH = "/World/LiveTwinState"
PRIMARY_RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
CAMERA_MARKER_NAME_RE = re.compile(
    r"^(?:(?:CAMERA|CAM|MARKER)[_-]?C?|C)[_-]?([0-9]{1,2})(?:[_-].*)?$",
    re.IGNORECASE,
)
LIVE_HUD_ROOT_PATH = "/World/LiveHUD"
CJK_FONT_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
DEVICE_PANEL_WIDTH = 768
DEVICE_PANEL_HEIGHT = 512
DEVICE_PANEL_VALUE_X = 724
DEVICE_PANEL_VALUE_Y = {
    "Device": 132,
    "Field": 196,
    "Bay": 260,
    "Gantry": 324,
    "Trolley": 388,
    "Hoist": 452,
}
DEVICE_SCENE_PANEL_ROOT_PATH = "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL"
DEVICE_SCENE_PANEL_RIGHT_ROOT_PATH = "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL_RIGHT"
DEVICE_SCENE_PANEL_MESH_PATH = f"{DEVICE_SCENE_PANEL_ROOT_PATH}/Panel"
DEVICE_SCENE_PANEL_RIGHT_MESH_PATH = f"{DEVICE_SCENE_PANEL_RIGHT_ROOT_PATH}/Panel"
DEVICE_SCENE_PANEL_MATERIAL_PATH = "/World/Looks/RTG_Device_Location_Panel_New"
DEVICE_SCENE_PANEL_ROOT_PATHS = (
    DEVICE_SCENE_PANEL_ROOT_PATH,
    DEVICE_SCENE_PANEL_RIGHT_ROOT_PATH,
)
DEVICE_SCENE_HUD_SELECTABLE_PATH_PREFIXES = DEVICE_SCENE_PANEL_ROOT_PATHS
DEVICE_SCENE_PANEL_WIDTH_M = 6.24
DEVICE_SCENE_PANEL_HEIGHT_M = 4.16
DEVICE_SCENE_PANEL_WORLD_X = -9.0
DEVICE_SCENE_PANEL_RIGHT_WORLD_X = 9.0
DEVICE_SCENE_PANEL_WORLD_Z = 12.0
DEVICE_SCENE_PANEL_Y_OFFSET_M = -2.2
DEVICE_SCENE_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
DEVICE_SCENE_PANEL_UV_COORDS = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
WINDOW_TITLE = "RTG Device Location"
LEGACY_WINDOW_TITLES = ("RTG Digital Twin", "RTG Device Location")
DIAGNOSTIC_LOG_ENABLED = False


@dataclass(frozen=True)
class InfoRow:
    label: str
    value: str


def _current_stage() -> Any:
    try:
        import omni.usd  # type: ignore

        return omni.usd.get_context().get_stage()
    except Exception:
        return None


def _selected_paths() -> List[str]:
    try:
        import omni.usd  # type: ignore

        return list(omni.usd.get_context().get_selection().get_selected_prim_paths())
    except Exception:
        return []


def _status_prim(stage: Any) -> Any:
    try:
        return stage.GetPrimAtPath(STATUS_PRIM_PATH)
    except Exception:
        return None


def _read_attr(prim: Any, name: str, default: Any = None) -> Any:
    if not prim:
        return default
    try:
        attr = prim.GetAttribute(name)
        value = attr.Get() if attr else None
        return default if value is None else value
    except Exception:
        return default


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
    else:
        text = str(value).strip()
    return f"{text}{suffix}" if text else "--"


def _fmt_number(value: Any, places: int, suffix: str) -> str:
    try:
        return f"{float(value):.{places}f}{suffix}" if value is not None else "--"
    except (TypeError, ValueError):
        return "--"


def _fmt_int(value: Any) -> str:
    try:
        number = int(value)
        return str(number) if number > 0 else "--"
    except (TypeError, ValueError):
        return "--"


def _device_bay_id(status: Any) -> str:
    explicit = _read_attr(status, "rtg:currentBayId")
    if explicit:
        return _fmt(explicit)
    field = str(_read_attr(status, "rtg:currentField") or "").strip()
    bay = _fmt_int(_read_attr(status, "rtg:currentBay"))
    return f"{field}/{int(bay):03d}" if field and bay != "--" else "--"


def _collect_device_info_rows(stage: Any, selected_path: str) -> Tuple[InfoRow, ...]:
    status = _status_prim(stage)
    return (
        InfoRow("Device", _fmt(_read_attr(status, "rtg:rtgName"))),
        InfoRow("Payload", _fmt(_read_attr(status, "rtg:manualPayload") or _read_attr(status, "rtg:carriedContainer"))),
        InfoRow("Field", _fmt(_read_attr(status, "rtg:currentField"))),
        InfoRow("Bay", _fmt_int(_read_attr(status, "rtg:currentBay"))),
        InfoRow("Bay ID", _device_bay_id(status)),
        InfoRow("Mode", _fmt(_read_attr(status, "rtg:controlMode") or "LIVE")),
        InfoRow("Gantry", _fmt_number(_read_attr(status, "rtg:gantryMeter"), 2, " m")),
        InfoRow("Trolley", _fmt_number(_read_attr(status, "rtg:trolleyMeter"), 2, " m")),
        InfoRow("Hoist", _fmt_number(_read_attr(status, "rtg:hoistMeter"), 2, " m")),
        InfoRow("Tool Size", _fmt_number(_read_attr(status, "rtg:spreaderSizeFt"), 0, " ft")),
        InfoRow("Locked", "YES" if bool(_read_attr(status, "rtg:spreaderLocked")) else "NO"),
        InfoRow("Selected", selected_path),
    )


def _find_cjk_font_path() -> Optional[Path]:
    for raw_path in CJK_FONT_PATHS:
        path = Path(raw_path)
        if path.is_file():
            return path
    return None


def _render_device_panel_bitmap(values: dict[str, str], output_path: Path) -> bool:
    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        image = Image.new("RGBA", (DEVICE_PANEL_WIDTH, DEVICE_PANEL_HEIGHT), (0, 0, 0, 0))
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        box = (2, 2, DEVICE_PANEL_WIDTH - 3, DEVICE_PANEL_HEIGHT - 3)
        glow_draw.rounded_rectangle(box, radius=18, fill=(46, 194, 236, 45), outline=(83, 215, 248, 170), width=4)
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(box, radius=16, fill=(7, 29, 49, 248), outline=(107, 215, 245, 236), width=3)
        draw.line((18, 20, DEVICE_PANEL_WIDTH - 19, 20), fill=(82, 211, 244, 210), width=3)
        draw.rectangle((16, 18, DEVICE_PANEL_WIDTH - 17, DEVICE_PANEL_HEIGHT - 17), outline=(129, 224, 246, 56), width=2)
        draw.rectangle((0, 0, DEVICE_PANEL_WIDTH, 88), fill=(20, 66, 90, 250))

        title_font = ImageFont.truetype(str(font_path), 38)
        label_font = ImageFont.truetype(str(font_path), 28)
        value_font = ImageFont.truetype(str(font_path), 31)
        device_font = ImageFont.truetype(str(font_path), 42)
        draw.text((36, 25), "设备定位", fill=(245, 252, 255, 255), font=title_font)
        labels = {
            "Device": "设备编号",
            "Field": "当前堆场",
            "Bay": "当前贝位",
            "Gantry": "大车位置",
            "Trolley": "小车位置",
            "Hoist": "吊具高度",
        }
        for key, y in DEVICE_PANEL_VALUE_Y.items():
            text = values.get(key, "--")
            font = device_font if key == "Device" else value_font
            max_width = 280 if key == "Device" else 230
            while len(text) > 2 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
                text = text[:-2] + "..."
            bbox = draw.textbbox((0, 0), text, font=font)
            draw.text((40, y - 18), labels[key], fill=(190, 221, 234, 255), font=label_font)
            draw.text(
                (DEVICE_PANEL_VALUE_X - (bbox[2] - bbox[0]), y - (bbox[3] - bbox[1]) // 2),
                text,
                fill=(78, 222, 249, 255) if key == "Device" else (246, 252, 255, 255),
                font=font,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception:
        return False


def _rtg_root_path_from_selection(path: str) -> Optional[str]:
    text = str(path or "").strip()
    if text == PRIMARY_RTG_PATH or text.startswith(f"{PRIMARY_RTG_PATH}/"):
        return PRIMARY_RTG_PATH
    parts = [part for part in text.split("/") if part]
    if "RTG_PRIMARY_DYNAMIC" in parts:
        index = parts.index("RTG_PRIMARY_DYNAMIC")
        return "/" + "/".join(parts[: index + 1])
    return None


def _is_camera_marker_selection_path(path: str) -> bool:
    """Keep Cxx camera markers out of the generic device-location panel."""

    text = str(path or "").strip()
    if not text.startswith(f"{PRIMARY_RTG_PATH}/"):
        return False
    for component in reversed([part for part in text.split("/") if part]):
        match = CAMERA_MARKER_NAME_RE.match(component)
        if match and 1 <= int(match.group(1)) <= 33:
            return True
    return False


def _is_live_hud_selection_path(path: str) -> bool:
    text = str(path or "").strip()
    return text == LIVE_HUD_ROOT_PATH or text.startswith(f"{LIVE_HUD_ROOT_PATH}/")


def _device_hud_root_from_selection(path: str) -> Optional[str]:
    text = str(path or "").strip()
    for root_path in DEVICE_SCENE_PANEL_ROOT_PATHS:
        if text == root_path or text.startswith(f"{root_path}/"):
            return root_path
    return None


def _hide_known_windows() -> None:
    try:
        import omni.ui as ui  # type: ignore

        workspace = getattr(ui, "Workspace", None)
        show_window = getattr(workspace, "show_window", None) if workspace else None
        if callable(show_window):
            for title in LEGACY_WINDOW_TITLES:
                show_window(title, False)
    except Exception:
        pass


class RtgDeviceLocationPanelService:
    def __init__(self) -> None:
        self._selection_sub = None
        self._update_sub = None
        self._bitmap_seq = 0
        self._clearing_selection = False
        self._normalizing_hud_selection = False
        self._last_selection_signature: tuple[str, ...] = ()
        self._start()

    def _start(self) -> None:
        self._subscribe_selection()
        self._subscribe_update()

    def stop(self) -> None:
        for subscription_name in ("_selection_sub", "_update_sub"):
            subscription = getattr(self, subscription_name)
            if subscription is not None:
                try:
                    subscription.unsubscribe()
                except Exception:
                    pass
                setattr(self, subscription_name, None)
        stage = _current_stage()
        if stage is not None:
            self._hide_scene_panels(stage)

    def _subscribe_selection(self) -> None:
        try:
            import omni.usd  # type: ignore

            stream = omni.usd.get_context().get_selection().get_changed_event_stream()
            self._selection_sub = stream.create_subscription_to_pop(self._on_selection)
        except Exception:
            self._selection_sub = None

    def _subscribe_update(self) -> None:
        try:
            import omni.kit.app  # type: ignore

            stream = omni.kit.app.get_app().get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(self._on_update)
        except Exception:
            self._update_sub = None

    def _on_selection(self, *_args: Any) -> None:
        self._handle_selected_paths(_selected_paths(), source="event")

    def _on_update(self, *_args: Any) -> None:
        self._handle_selected_paths(_selected_paths(), source="poll")
        self._sync_visible_scene_panels_facing_camera()

    def _handle_selected_paths(
        self,
        paths: List[str],
        source: str = "selection",
    ) -> None:
        del source
        self._handle_selection(paths)

    def _handle_selection(self, paths: List[str]) -> None:
        if self._clearing_selection or self._normalizing_hud_selection:
            return
        signature = tuple(paths)
        if signature == self._last_selection_signature:
            return
        self._last_selection_signature = signature
        if any(_is_camera_marker_selection_path(path) for path in paths):
            # The RTSP popup owns camera-marker clicks. Hide this panel but do
            # not clear selection, so the camera service can consume the same
            # USD selection event.
            self._hide()
            return
        if paths and all(_is_live_hud_selection_path(path) for path in paths):
            root = next((_device_hud_root_from_selection(path) for path in paths), None)
            if root:
                self._select_panel_root(paths, root)
            return
        for path in paths:
            if _rtg_root_path_from_selection(path) is not None:
                self._show_for_selection(path)
                self._clear_selection()
                return
        self._hide()

    def _show_for_selection(self, selected_path: str) -> None:
        stage = _current_stage()
        if stage is None:
            return
        self._bitmap_seq += 1
        texture_path = Path(tempfile.gettempdir()) / f"smart_port_device_location_new_{os.getpid()}_{self._bitmap_seq}.png"
        rows = _collect_device_info_rows(stage, selected_path)
        if _render_device_panel_bitmap({row.label: row.value for row in rows}, texture_path):
            self._show_scene_panels(stage, texture_path)

    def _show_scene_panels(self, stage: Any, texture_path: Path) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            material = UsdShade.Material.Define(stage, DEVICE_SCENE_PANEL_MATERIAL_PATH)
            shader = UsdShade.Shader.Define(stage, f"{DEVICE_SCENE_PANEL_MATERIAL_PATH}/PreviewSurface")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            texture = UsdShade.Shader.Define(stage, f"{DEVICE_SCENE_PANEL_MATERIAL_PATH}/Texture")
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(str(texture_path)))
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(stage, f"{DEVICE_SCENE_PANEL_MATERIAL_PATH}/Primvar_st")
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.ConnectableAPI(), "rgb")
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.ConnectableAPI(), "rgb")
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(texture.ConnectableAPI(), "a")
            material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

            half_w = DEVICE_SCENE_PANEL_WIDTH_M * 0.5
            half_h = DEVICE_SCENE_PANEL_HEIGHT_M * 0.5
            points = [
                Gf.Vec3f(half_w, 0.0, -half_h),
                Gf.Vec3f(-half_w, 0.0, -half_h),
                Gf.Vec3f(-half_w, 0.0, half_h),
                Gf.Vec3f(half_w, 0.0, half_h),
            ]
            for root_path, mesh_path, world_x in (
                (DEVICE_SCENE_PANEL_ROOT_PATH, DEVICE_SCENE_PANEL_MESH_PATH, DEVICE_SCENE_PANEL_WORLD_X),
                (DEVICE_SCENE_PANEL_RIGHT_ROOT_PATH, DEVICE_SCENE_PANEL_RIGHT_MESH_PATH, DEVICE_SCENE_PANEL_RIGHT_WORLD_X),
            ):
                root = UsdGeom.Xform.Define(stage, root_path)
                root_prim = stage.GetPrimAtPath(root_path)
                UsdGeom.Imageable(root_prim).MakeVisible()
                self._set_panel_pose(stage, root_prim, world_x)
                panel = UsdGeom.Mesh.Define(stage, mesh_path)
                # Some Kit builds return a Mesh schema from GetPrim(); use
                # the stage lookup to obtain the actual Usd.Prim for attrs.
                panel_prim = stage.GetPrimAtPath(mesh_path)
                UsdGeom.Xformable(panel_prim).ClearXformOpOrder()
                panel.CreatePointsAttr(points)
                panel.CreateFaceVertexCountsAttr([4])
                panel.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
                panel.CreateDoubleSidedAttr(True)
                panel.CreateExtentAttr([Gf.Vec3f(-half_w, 0.0, -half_h), Gf.Vec3f(half_w, 0.0, half_h)])
                # Author the UV primvar directly.  This avoids a Kit schema
                # wrapper incompatibility in PrimvarsAPI.CreatePrimvar.
                st = panel_prim.CreateAttribute(
                    "primvars:st",
                    Sdf.ValueTypeNames.TexCoord2fArray,
                )
                st.Set([Gf.Vec2f(u, v) for u, v in DEVICE_SCENE_PANEL_UV_COORDS])
                st.SetMetadata("interpolation", "faceVarying")
                panel_prim.CreateAttribute("rtg:devicePanelTexture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(str(texture_path)))
                UsdShade.MaterialBindingAPI(panel_prim).Bind(material)

    def _set_panel_pose(self, stage: Any, root_prim: Any, world_x: float) -> None:
        from pxr import Gf, UsdGeom

        status = _status_prim(stage)
        y = 0.0
        try:
            from build_live_containers import container_center_y_for_bay

            field = _read_attr(status, "rtg:currentField") or "4C"
            bay = int(_read_attr(status, "rtg:currentBay") or 0)
            y = container_center_y_for_bay(str(field), bay, 40 if bay % 2 == 0 else 20)
        except Exception:
            pass
        translate = Gf.Vec3d(world_x, y + DEVICE_SCENE_PANEL_Y_OFFSET_M, DEVICE_SCENE_PANEL_WORLD_Z)
        xform = UsdGeom.Xformable(root_prim)
        get_or_add_xform_op(
            xform,
            "xformOp:translate",
            xform.AddTranslateOp,
        ).Set(translate)
        self._set_panel_facing_camera(stage, root_prim, translate)

    def _sync_visible_scene_panels_facing_camera(self) -> None:
        stage = _current_stage()
        if stage is None:
            if DEVICE_PANEL_FACING_DIAG_ENABLED:
                print("[RTG Device Panel] PANEL_FACING_DIAG [sync_facing] stage is None")
            return
        for root_path in DEVICE_SCENE_PANEL_ROOT_PATHS:
            root = stage.GetPrimAtPath(root_path)
            if not root:
                if DEVICE_PANEL_FACING_DIAG_ENABLED:
                    print(
                        "[RTG Device Panel] PANEL_FACING_DIAG [sync_facing] "
                        f"SKIP root not found: {root_path}"
                    )
                continue
            translate = self._world_translate(stage, root)
            if translate is None:
                if DEVICE_PANEL_FACING_DIAG_ENABLED:
                    print(
                        "[RTG Device Panel] PANEL_FACING_DIAG [sync_facing] "
                        f"SKIP translate=None: {root_path}"
                    )
                continue
            if DEVICE_PANEL_FACING_DIAG_ENABLED:
                tl_str = (
                    f"({translate[0]:.1f}, {translate[1]:.1f}, {translate[2]:.1f})"
                    if translate is not None
                    else "None"
                )
                print(
                    "[RTG Device Panel] PANEL_FACING_DIAG [sync_facing] "
                    f"SET {root_path.split('/')[-1]} tl={tl_str}"
                )
            self._set_panel_facing_camera(stage, root, translate)

    def _sync_visible_scene_panel_facing_camera(self) -> None:
        """Compatibility name for scripts that used the old single-panel service."""

        self._sync_visible_scene_panels_facing_camera()

    @staticmethod
    def _world_translate(stage: Any, root_prim: Any) -> Any:
        try:
            from pxr import Usd, UsdGeom

            matrix = UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(root_prim)
            for name in ("ExtractTranslation", "GetTranslation"):
                method = getattr(matrix, name, None)
                if callable(method):
                    return method()
        except Exception:
            pass
        try:
            from pxr import UsdGeom

            xform = UsdGeom.Xformable(root_prim)
            op = existing_xform_op(xform, "xformOp:translate")
            return op.Get() if op is not None else None
        except Exception:
            pass
        return None

    @staticmethod
    def _set_panel_facing_camera(stage: Any, root_prim: Any, translate: Any) -> None:
        from pxr import Gf, UsdGeom

        rotate = Gf.Vec3f(*camera_facing_rotate_xyz(stage, translate, DEVICE_SCENE_PANEL_ROTATE_XYZ))
        if DEVICE_PANEL_FACING_DIAG_ENABLED:
            rot_str = f"({rotate[0]:.1f}, {rotate[1]:.1f}, {rotate[2]:.1f})"
            print(
                "[RTG Device Panel] PANEL_FACING_DIAG [_set_facing] "
                f"path={root_prim.GetPath()} rot={rot_str}"
            )
        xform = UsdGeom.Xformable(root_prim)
        get_or_add_xform_op(
            xform,
            "xformOp:rotateXYZ",
            xform.AddRotateXYZOp,
        ).Set(rotate)

    @staticmethod
    def _scene_panel_world_translate(stage: Any, root_prim: Any) -> Any:
        return RtgDeviceLocationPanelService._world_translate(stage, root_prim)

    @staticmethod
    def _set_scene_panel_facing_camera(stage: Any, root_prim: Any, translate: Any) -> None:
        RtgDeviceLocationPanelService._set_panel_facing_camera(stage, root_prim, translate)

    def _hide(self) -> None:
        stage = _current_stage()
        if stage is not None:
            self._hide_scene_panels(stage)

    @staticmethod
    def _hide_scene_panels(stage: Any) -> None:
        from pxr import Usd, UsdGeom

        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for root_path in DEVICE_SCENE_PANEL_ROOT_PATHS:
                    root = stage.GetPrimAtPath(root_path)
                    if root:
                        UsdGeom.Imageable(root).MakeInvisible()
        except Exception:
            pass

    def _select_panel_root(self, paths: Sequence[str], root_path: str) -> None:
        if tuple(paths) == (root_path,):
            return
        try:
            import omni.usd  # type: ignore

            self._normalizing_hud_selection = True
            omni.usd.get_context().get_selection().set_selected_prim_paths([root_path], False)
            self._last_selection_signature = (root_path,)
        except Exception:
            pass
        finally:
            self._normalizing_hud_selection = False

    def _clear_selection(self) -> None:
        try:
            import omni.usd  # type: ignore

            self._clearing_selection = True
            selection = omni.usd.get_context().get_selection()
            try:
                selection.clear_selected_prim_paths()
            except Exception:
                selection.set_selected_prim_paths([], False)
        except Exception:
            pass
        finally:
            self._clearing_selection = False
            self._last_selection_signature = ()

    def _clear_selection_gizmo(self) -> None:
        self._clear_selection()


RtgDeviceInfoPopupService = RtgDeviceLocationPanelService
_service: Optional[RtgDeviceLocationPanelService] = None


def _stop_previous_builtin_service() -> None:
    for key in (
        "_rtg_device_location_panel_service",
        "_rtg_device_info_popup_service",
    ):
        previous = getattr(builtins, key, None)
        if previous is not None:
            try:
                previous.stop()
            except Exception:
                pass
        setattr(builtins, key, None)


def start_rtg_device_info_popup() -> RtgDeviceLocationPanelService:
    global _service
    _hide_known_windows()
    _stop_previous_builtin_service()
    if _service is not None:
        _service.stop()
    _service = RtgDeviceLocationPanelService()
    builtins._rtg_device_location_panel_service = _service
    builtins._rtg_device_info_popup_service = _service
    return _service


def stop_rtg_device_info_popup() -> None:
    global _service
    if _service is not None:
        _service.stop()
    _service = None
    builtins._rtg_device_location_panel_service = None
    builtins._rtg_device_info_popup_service = None
    _hide_known_windows()


start_rtg_device_location_panel = start_rtg_device_info_popup
stop_rtg_device_location_panel = stop_rtg_device_info_popup
