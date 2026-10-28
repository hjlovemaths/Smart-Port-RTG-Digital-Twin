"""Manual harbor time, independent of the simulation timeline (session edits)."""
import math
import time

TIME_ATTRIBUTE = "smartPort:timeOfDayHours"
VIEWPORT_SETTLE_SECONDS = .75


def _viewport_product_key(stage):
    """Viewports can appear after stage lighting has already initialized."""
    try:
        from omni.kit.viewport.utility import get_active_viewport
        viewport = get_active_viewport()
        if viewport is None:
            return None
        path = str(viewport.render_product_path)
        return path if _optional_prim(stage, path) is not None else None
    except (ImportError, AttributeError, RuntimeError):
        return None


def _viewport_is_rendering():
    """A RenderProduct can exist before Hydra has delivered its first frame."""
    try:
        from omni.kit.viewport.utility import get_active_viewport
        viewport = get_active_viewport()
        return viewport is not None and viewport.fps > 0
    except (ImportError, AttributeError, RuntimeError):
        return False


def _has_usd_bindings():
    try:
        import pxr
    except ModuleNotFoundError as exc:
        if exc.name != "pxr":
            raise
        return False
    return True


def _restore_clear_sky(stage, procedural=False):
    import smart_port_environment as w
    sky = w._valid_prim(stage, w.DAY_SKY_PATH)
    with w._session_edit_context(stage):
        w._set_attribute(sky, "visibility", "inherited", "Token")
        w._set_attribute(sky, "inputs:texture:format", "latlong", "Token")
        # Explicit absolute assets remain resolvable after session overrides
        # and after the viewport recreates its render product during startup.
        texture = str(w.DAY_BACKGROUND_TEXTURE.resolve()).replace("\\", "/") if w.DAY_BACKGROUND_TEXTURE.is_file() else ""
        if not procedural:
            w._set_attribute(sky, "inputs:texture:file", texture, "Asset")
        for prim in stage.Traverse():
            if prim.GetTypeName() == "RenderProduct":
                w._set_attribute(prim, "omni:rtx:lights:visibilityOverride", "perLight", "Token")
                w._set_attribute(prim, "omni:rtx:background:source:type", "domeLight" if procedural or texture else "color", "Token")
                w._set_attribute(prim, "omni:rtx:background:source:texture:path", "", "Asset")
                w._set_attribute(prim, "omni:rtx:post:compositing:postTonemapBackgroundComposite", False, "Bool")


def _optional_prim(stage, path):
    """A non-null stage can still be empty while Kit opens its layers."""
    if stage is None:
        return None
    try:
        prim = stage.GetPrimAtPath(path)
        return prim if prim is not None and prim.IsValid() else None
    except RuntimeError:  # The previous stage may have just closed.
        return None


def _environment_ready(stage):
    import smart_port_environment as w
    return all(_optional_prim(stage, path) is not None for path in (
        w.ENVIRONMENT_ROOT_PATH, w.DAY_SKY_PATH, w.DAY_SUN_PATH,
        w.NIGHT_STREET_LIGHTS_PATH,
    ))


def lighting_at(hour):
    hour = float(hour)
    if not math.isfinite(hour) or not 0 <= hour <= 24:
        raise ValueError("Time must be between 0 and 24 hours")
    phase = hour % 24
    elevation = 65 * math.sin((phase - 6) * math.pi / 12)
    daylight = max(0.0, min(1.0, (elevation + 10) / 36))
    daylight = daylight * daylight * (3 - 2 * daylight)
    sine = max(0.0, math.sin(math.radians(elevation)))
    sun = sine ** .35 * math.exp(-.16 / max(sine, .001))
    sky_daylight = max(0.0, min(1.0, (elevation + 6) / 18))
    sky_daylight = sky_daylight * sky_daylight * (3 - 2 * sky_daylight)
    twilight = math.exp(-((elevation + 4) / 9) ** 2)
    # Sky tint alone does not make night: the dome also illuminates the port
    # and its reflected/background radiance must fall with the sun. Retain a
    # small night floor and a short twilight contribution, not daytime power.
    sky_strength = .003 + .997 * min(1., sky_daylight + .15 * twilight)
    warmth = max(0.0, 1 - max(0.0, elevation) / 25)
    return dict(hour=hour, elevation=elevation, daylight=daylight, sun=sun,
                sky_daylight=sky_daylight, twilight=twilight, sky_strength=sky_strength,
                color=(1.0, .95 - .43 * warmth, .86 - .62 * warmth),
                rotation=(90 - elevation, 0.0, (phase - 12) * 15 - 38))


def current_hour(stage):
    import smart_port_environment as w
    root = _optional_prim(stage, w.ENVIRONMENT_ROOT_PATH)
    if root is None:
        return 12.0
    fallback = 0.0 if w.current_environment_mode(stage) == "night" else 12.0
    return float(w._get_attribute_value(root, TIME_ATTRIBUTE, fallback))


def apply_time(stage, hour, configure=True):
    import smart_port_environment as w
    p = lighting_at(hour)
    procedural = _has_usd_bindings()
    mode = w.current_environment_mode(stage)
    if configure and mode in ("day", "night"):
        desired = "day" if p["daylight"] > .05 else "night"
        if mode != desired:
            w.apply_environment_mode(stage, desired)
            mode = desired
    root = w._valid_prim(stage, w.ENVIRONMENT_ROOT_PATH)
    sky = w._valid_prim(stage, w.DAY_SKY_PATH)
    sun = w._valid_prim(stage, w.DAY_SUN_PATH)
    d = p["daylight"]
    with w._session_edit_context(stage):
        w._set_attribute(root, TIME_ATTRIBUTE, p["hour"], "Float")
        # Keep the authored sun transform order; its existing rotateXYZ is
        # the only rotation in this project's environment layer.
        w._set_attribute(sun, "xformOp:rotateXYZ", p["rotation"], "Float3")
        if mode in ("rain", "fog"):
            if mode == "rain":
                grade = w._get_attribute_value(root, w.RAIN_AMOUNT_ATTRIBUTE, "medium")
                sky_scale, sun_scale = w.RAIN_ATMOSPHERE_PROFILES[grade][:2]
                fog_color = w.RAIN_ATMOSPHERE_PROFILES[grade][2]
            else:
                grade = w._get_attribute_value(root, w.FOG_AMOUNT_ATTRIBUTE, "medium")
                sky_scale, sun_scale = {"light":(1.08,.65),"medium":(1,.32),"heavy":(.94,.1)}[grade]
                fog_color = w.FOG_AMOUNT_BACKGROUND_COLORS[grade]
            preset = w.ENVIRONMENT_PRESETS[mode]
            w._set_attribute(sky, "inputs:intensity", preset.sky_intensity * sky_scale * (.08 + .92*d), "Float")
            w._set_attribute(sun, "inputs:intensity", preset.sun_intensity * sun_scale * p["sun"], "Float")
            for prim in stage.Traverse():
                if prim.GetTypeName() == "RenderProduct":
                    w._set_attribute(prim, "omni:rtx:fog:color", tuple(c*(.10+.90*d) for c in fog_color), "Color3f")
                    if mode == "fog":
                        w._set_attribute(prim, "omni:rtx:background:source:color",
                                         tuple((c+(1-c)*.55)*(.025+.975*d) for c in fog_color), "Color3f")
        else:
            w._set_attribute(root, w.ENVIRONMENT_MODE_ATTRIBUTE, "day" if d > .05 else "night", "Token")
            w._set_attribute(sky, "inputs:exposure", 0.0, "Float")
            if not procedural:
                w._set_attribute(sky, "inputs:intensity", 650*p["sky_strength"], "Float")
                w._set_attribute(sky, "inputs:color", tuple(a+(b-a)*d for a,b in zip((.12,.22,.55),(1,1,1))), "Color3f")
            w._set_attribute(sun, "inputs:intensity", 2200*p["sun"], "Float")
            w._set_attribute(sun, "inputs:color", p["color"], "Color3f")
            _restore_clear_sky(stage, procedural)
            for prim in stage.Traverse():
                if prim.GetTypeName() == "RenderProduct":
                    w._set_attribute(prim, "omni:rtx:background:source:color",
                                     tuple(a+(b-a)*d for a,b in zip((.008,.015,.04), w.DAY_BACKGROUND_FALLBACK_COLOR)), "Color3f")
            enabled = d < .65
            w._set_attribute(w._valid_prim(stage, w.NIGHT_STREET_LIGHTS_PATH), "visibility", "inherited" if enabled else "invisible", "Token")
            w._configure_rtg_floodlights(stage, enabled)
        if procedural:
            from harbor_daylight import configure as configure_daylight
            configure_daylight(stage, p, enabled=mode in ('day', 'night'))
    return p


def reapply_if_set(stage):
    import smart_port_environment as w
    if not _environment_ready(stage):
        return
    root = _optional_prim(stage, w.ENVIRONMENT_ROOT_PATH)
    hour = w._get_attribute_value(root, TIME_ATTRIBUTE, None)
    if hour is not None:
        apply_time(stage, hour, configure=False)


def build_controls(owner):
    import omni.ui as ui
    import omni.usd
    stage = omni.usd.get_context().get_stage()
    hour = current_hour(stage) if stage else 12.0
    owner._time_pending = None
    owner._time_last_apply = 0.0
    # Reading the clock isn't sufficient: light state must be initialized
    # once the stage is complete, including panels built after loading.
    owner._time_stage = None
    owner._time_product_key = None
    owner._time_settle_at = None
    owner._time_syncing = False
    with ui.HStack(height=28, spacing=6):
        ui.Label("Time of day", width=90)
        owner._time_slider = ui.FloatSlider(min=0, max=24, step=1/12, format="%.2f h")
        owner._time_slider.model.set_value(hour)
        owner._time_label = ui.Label(_clock_label(hour), width=52)
    with ui.HStack(height=18):
        for label in ("00:00", "06:00", "12:00", "18:00", "24:00"):
            ui.Label(label)
    def changed(model):
        value = model.get_value_as_float()
        owner._time_label.text = _clock_label(value)
        if not owner._time_syncing:
            owner._time_pending = value
    owner._time_slider.model.add_value_changed_fn(changed)


def _clock_label(hour):
    minutes = round(hour*60)
    return f"{minutes//60:02d}:{minutes%60:02d}"


def tick(owner):
    if getattr(owner, "_time_slider", None) is None:
        return
    import omni.usd
    stage = omni.usd.get_context().get_stage()
    if not _environment_ready(stage):
        owner._time_stage = None
        owner._time_product_key = None
        owner._time_settle_at = None
        return
    # Opening/replacing a stage must synchronize the existing panel. Do not
    # write the default 12:00 back into a scene that has a selected hour.
    product_key = _viewport_product_key(stage)
    changed_stage = getattr(owner, "_time_stage", None) is not stage
    changed_product = product_key is not None and product_key != getattr(owner, "_time_product_key", None)
    if changed_stage or changed_product:
        owner._time_stage = stage
        owner._time_product_key = product_key
        owner._time_settle_at = time.monotonic() + VIEWPORT_SETTLE_SECONDS if product_key else None
        if getattr(owner, "_time_pending", None) is None:
            hour = current_hour(stage)
            try:
                apply_time(stage, hour, configure=False)
            except RuntimeError:
                # Stage closure can race with the readiness check above.
                owner._time_stage = None
                owner._time_product_key = None
                owner._time_settle_at = None
                return
            owner._time_syncing = True
            try:
                owner._time_slider.model.set_value(hour)
                owner._time_label.text = _clock_label(hour)
            finally:
                owner._time_syncing = False
    # Rebind once after the viewport starts delivering frames. This handles
    # render-product initialization that occurs after the USD stage is ready,
    # without repeatedly rebuilding the sky while the clock is idle.
    settle_at = getattr(owner, "_time_settle_at", None)
    if settle_at is not None and time.monotonic() >= settle_at and _viewport_is_rendering():
        owner._time_settle_at = None
        try:
            apply_time(stage, current_hour(stage), configure=False)
        except RuntimeError:
            owner._time_stage = None
            owner._time_product_key = None
            return
    pending = getattr(owner, "_time_pending", None)
    if pending is None or time.monotonic() - owner._time_last_apply < .10:
        return
    owner._time_pending = None
    owner._time_last_apply = time.monotonic()
    try:
        apply_time(stage, pending)
    except Exception as exc:
        owner._set_ui_result(str(exc), error=True)
