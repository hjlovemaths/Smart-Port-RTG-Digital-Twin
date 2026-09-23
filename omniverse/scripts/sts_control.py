"""Four-axis visual STS control for the composed smart-port stage.

Gantry is world-Y displacement from home. Trolley is seaward reach from the
boom hinge. Hoist is the lowest spreader point's Z. All distances are existing
scene metres. Boom angle is degrees upward from horizontal. This is kinematic
visual control, not a load-bearing physics/PLC model.
"""
from __future__ import annotations

import math
import importlib.util
import traceback
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom

ROOT = '/World/STSControls'
PIVOT = Gf.Vec3d(0, 0.94, 9.36)
INITIAL = dict(gantry=0.0, trolley=1.62, hoist=2.78, boom=0.0)
LIMITS = dict(trolley=(-10.0, 17.0), hoist=(0.30, 8.0), boom=(0.0, 80.0))
SPEEDS = dict(gantry=1.0, trolley=0.8, hoist=0.6, boom=4.0)
SPREADER_40FT_HALF_SPAN = 1.05
SPREADER_20FT_END_DELTA = 0.60
SPREADER_CENTER_Y = -0.70
SPREADER_ROPE_Z = 4.24


def load_unload_task_class():
    """Load the sibling task even when Kit executes this script by file path."""
    path = Path(__file__).resolve().with_name('sts_unload_task.py')
    if not path.is_file():
        raise FileNotFoundError(f'Unload task script missing: {path}')
    spec = importlib.util.spec_from_file_location('_sts_unload_task_runtime', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'Cannot load unload task: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UnloadTask


def _translate(v):
    return Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(*v))


def boom_matrix(angle):
    return (_translate(-PIVOT) *
            Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), -angle)) *
            _translate(PIVOT))


def rope_endpoints(trolley, hoist, spreader_size_ft=40):
    dy = INITIAL['trolley'] - trolley
    dz = hoist - INITIAL['hoist']
    # Fixed upper headblock; only the lower spreader end beams telescope.
    return [(Gf.Vec3d(x, y+dy, 9.82),
             Gf.Vec3d(x, y+dy, SPREADER_ROPE_Z+dz))
            for x in (-0.26, 0.26) for y in (-0.92, -0.48)]


def cylinder_matrix(start, end, radius):
    direction = end - start
    length = direction.GetLength()
    if length <= 1e-8:
        raise ValueError('Zero-length STS cable')
    return (Gf.Matrix4d(1).SetScale(Gf.Vec3d(radius, radius, length)) *
            Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), direction / length)) *
            _translate((start + end) * 0.5))


class STSController:
    def __init__(self, stage, crane='STS_04'):
        self.stage = stage
        self.path = f'{ROOT}/{crane}'
        prim = stage.GetPrimAtPath(self.path)
        if not prim:
            raise ValueError(f'Controlled crane missing: {self.path}. Load sts_controls.usda first.')
        self.limits = dict(LIMITS, gantry=(prim.GetAttribute('sts:gantryMin').Get(),
                                        prim.GetAttribute('sts:gantryMax').Get()))
        self.layer = Sdf.Layer.CreateAnonymous(f'{crane}_motion.usda')
        self.state = dict(INITIAL)
        self.targets = dict(INITIAL)
        self.spreader_size_ft = 40
        self.stopped = False
        self.closed = False
        self._ops = {}
        session = stage.GetSessionLayer()
        session.subLayerPaths.insert(0, self.layer.identifier)
        try:
            with Usd.EditContext(stage, self.layer):
                UsdGeom.Xform.Define(stage, f'{self.path}/Gantry/DynamicCables')
                for name in ('Gantry', 'Gantry/Boom', 'Gantry/Trolley', 'Gantry/Trolley/Hoist'):
                    xf = UsdGeom.Xformable(stage.GetPrimAtPath(f'{self.path}/{name}'))
                    if not xf:
                        raise ValueError(f'Missing STS axis: {name}')
                    op = xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, 'stsControl')
                    # The control delta is in the parent's coordinate system.
                    xf.SetXformOpOrder([op]+[o for o in xf.GetOrderedXformOps() if o != op])
                    self._ops[name] = op
                for name in ('Gantry/Trolley/Hoist/STS_SpreaderEndFront',
                             'Gantry/Trolley/Hoist/STS_SpreaderEndRear'):
                    xf = UsdGeom.Xformable(stage.GetPrimAtPath(f'{self.path}/{name}'))
                    if not xf:
                        raise ValueError(f'Missing STS spreader axis: {name}')
                    op = xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, 'stsControl')
                    xf.SetXformOpOrder([op]+[o for o in xf.GetOrderedXformOps() if o != op])
                    self._ops[name] = op
                for i in range(8):
                    cable = UsdGeom.Cylinder.Define(stage, f'{self.path}/Gantry/DynamicCables/Cable_{i}')
                    cable.CreateAxisAttr('Z')
                    cable.CreateRadiusAttr(1.0)
                    cable.CreateHeightAttr(1.0)
                    cable.CreateDisplayColorAttr([Gf.Vec3f(0.025)])
                    xf = UsdGeom.Xformable(cable)
                    self._ops[f'cable{i}'] = (xf.GetOrderedXformOps()[0] if xf.GetOrderedXformOps() else xf.AddTransformOp())
            self._apply()
        except Exception:
            self.close()
            raise

    def command(self, **values):
        if self.closed:
            raise RuntimeError('STS controller is closed')
        if self.stopped:
            raise ValueError('Emergency stop active. Press Resume first.')
        target = dict(self.targets)
        for axis, value in values.items():
            if axis not in self.limits:
                raise ValueError(f'Unknown STS axis: {axis}')
            value = float(value)
            lo, hi = self.limits[axis]
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f'{axis}: enter {lo:.2f} to {hi:.2f}')
            target[axis] = value
        # Require the actual and commanded trolley/hoist poses to be parked.
        # A simultaneous park+raise command cannot bypass the interlock.
        if max(self.state['boom'], target['boom']) > 0.01:
            if any(p['trolley'] > -6.0 or p['hoist'] < 7.5 for p in (self.state, target)):
                raise ValueError('Boom interlock: trolley <= -6.0 m and hoist >= 7.5 m required.')
        self.targets = target

    def set_spreader_size(self, size_ft):
        if self.closed:
            raise RuntimeError('STS controller is closed')
        if self.stopped:
            raise ValueError('Emergency stop active. Press Resume first.')
        size_ft = int(size_ft)
        if size_ft not in (20, 40):
            raise ValueError('Spreader size must be 20 or 40 ft')
        self.spreader_size_ft = size_ft
        self._apply()

    def tick(self, dt):
        if self.closed or self.stopped:
            return
        dt = float(dt)
        if not math.isfinite(dt) or dt <= 0:
            return
        dt = min(dt, 0.1)
        old = dict(self.state)
        for axis, target in self.targets.items():
            delta = target - self.state[axis]
            self.state[axis] += math.copysign(min(abs(delta), SPEEDS[axis]*dt), delta)
        if self.state != old:
            self._apply()

    def stop(self):
        self.targets = dict(self.state)
        self.stopped = True

    def resume(self):
        self.stopped = False

    def hold(self):
        self.targets = dict(self.state)

    def _apply(self):
        s = self.state
        # Root is rotated -90 degrees: local -X equals world +Y.
        with Usd.EditContext(self.stage, self.layer):
            self._ops['Gantry'].Set(_translate((-s['gantry'], 0, 0)))
            # Boom has an authored pivot translation; rotate locally at that pivot.
            self._ops['Gantry/Boom'].Set(boom_matrix(s['boom']))
            self._ops['Gantry/Trolley'].Set(_translate((0, INITIAL['trolley']-s['trolley'], 0)))
            self._ops['Gantry/Trolley/Hoist'].Set(_translate((0, 0, s['hoist']-INITIAL['hoist'])))
            delta = SPREADER_20FT_END_DELTA if self.spreader_size_ft == 20 else 0.0
            self._ops['Gantry/Trolley/Hoist/STS_SpreaderEndFront'].Set(_translate((-delta, 0, 0)))
            self._ops['Gantry/Trolley/Hoist/STS_SpreaderEndRear'].Set(_translate((delta, 0, 0)))
            endpoints = rope_endpoints(s['trolley'], s['hoist'], self.spreader_size_ft)
            for y in (-16.38, 12.38):
                for x in (-2.05, 2.05):
                    end = Gf.Vec3d(x, y, 9.86)
                    if y < 0:
                        end = boom_matrix(s['boom']).Transform(end)
                    endpoints.append((Gf.Vec3d(0, 4.06, 13.94), end))
            for i, (start, end) in enumerate(endpoints):
                self._ops[f'cable{i}'].Set(cylinder_matrix(start, end, 0.012 if i < 4 else 0.017))

    def close(self):
        self.closed = True
        session = self.stage.GetSessionLayer()
        if self.layer.identifier in session.subLayerPaths:
            session.subLayerPaths.remove(self.layer.identifier)


class STSPanel:
    """Kit panel; imports remain lazy so the math/controller is testable with pxr."""
    def __init__(self, stage):
        import omni.ui as ui
        import omni.kit.app
        import omni.usd
        self.stage = stage
        self.controllers = {}
        self.unload_task = None
        self.cranes = [p.GetName() for p in stage.GetPrimAtPath(ROOT).GetChildren()]
        self.selected = 'STS_04' if 'STS_04' in self.cranes else self.cranes[0]
        self.window = ui.Window('STS Crane Control', width=510, height=570)
        self.fields = {}
        with self.window.frame:
            with ui.VStack(spacing=8):
                ui.Label('STS four-axis control (scene metres / degrees)', height=24)
                with ui.HStack(height=26):
                    ui.Label('Crane', width=75)
                    self.choice = ui.ComboBox(self.cranes.index(self.selected), *self.cranes)
                    self.choice.model.add_item_changed_fn(self._select)
                    ui.Button('Focus', clicked_fn=self.focus, width=65)
                for axis, label in (('gantry', 'Gantry offset (m)'), ('trolley', 'Seaward reach (m)'),
                                    ('hoist', 'Spreader height (m)'), ('boom', 'Boom up (deg)')):
                    with ui.HStack(height=28):
                        ui.Label(label, width=160)
                        field = ui.FloatField()
                        self.fields[axis] = field.model
                        field.model.set_value(INITIAL[axis])
                        ui.Button('Go', width=55, clicked_fn=lambda a=axis: self.go(a))
                with ui.HStack(height=28):
                    ui.Button('Park for luffing', clicked_fn=self.park)
                    ui.Button('Raise 80 deg', clicked_fn=lambda: self.send(boom=80))
                    ui.Button('Lower 0 deg', clicked_fn=lambda: self.send(boom=0))
                with ui.HStack(height=28):
                    ui.Label('Spreader size', width=160)
                    ui.Button('20 ft', clicked_fn=lambda: self.set_spreader(20), width=65)
                    ui.Button('40 ft', clicked_fn=lambda: self.set_spreader(40), width=65)
                with ui.HStack(height=28):
                    ui.Button('STOP ALL', clicked_fn=self.stop_all)
                    ui.Button('Resume selected', clicked_fn=lambda: self.controller.resume())
                self.status = ui.Label('', height=64, word_wrap=True)
                self.readout = ui.Label('', height=32, word_wrap=True)
                with ui.HStack(height=28):
                    ui.Button('STS_01 -> Truck -> 2C004', clicked_fn=self.start_unload)
                    ui.Button('Pause / Resume task', clicked_fn=self.pause_unload)
                    ui.Button('Reset task', clicked_fn=self.reset_unload)
                self.task_status = ui.Label('Ship bow top box -> truck -> RTG -> 2C003+005 / row 1 / tier 1', height=44, word_wrap=True)
                ui.Button('Focus task vehicle / crane', clicked_fn=self.focus_task, height=26)
                ui.Label('Raise only after Park finishes.\nLower fully before moving trolley seaward.\nClose panel: stop all axes and restore the home pose.', height=65, word_wrap=True)
        self.window.set_visibility_changed_fn(self._visibility)
        self._sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(self._update)
        self._stage_sub = omni.usd.get_context().get_stage_event_stream().create_subscription_to_pop(self._stage_event)
        self._refresh()

    @property
    def controller(self):
        if self.selected not in self.controllers:
            self.controllers[self.selected] = STSController(self.stage, self.selected)
        return self.controllers[self.selected]

    def _select(self, model, item):
        for controller in self.controllers.values():
            if not self.unload_task or controller != self.unload_task.c:
                controller.hold()
        self.selected = self.cranes[model.get_item_value_model().as_int]
        self._refresh()

    def _refresh(self):
        c = self.controller
        for axis, field in self.fields.items():
            field.set_value(c.targets[axis])
        self.status.text = (f'{self.selected}: gantry {c.limits["gantry"]}; trolley -10..17; '
                            f'hoist 0.3..8; boom 0..80; spreader {c.spreader_size_ft} ft')

    def send(self, **values):
        try:
            if self.unload_task and self.controller == self.unload_task.c:
                raise ValueError('Reset the unload task before manual control.')
            self.controller.command(**values)
            self._refresh()
        except (ValueError, RuntimeError) as exc:
            self.status.text = str(exc)

    def go(self, axis):
        self.send(**{axis: self.fields[axis].as_float})

    def park(self):
        self.send(trolley=-8.0, hoist=7.7)

    def set_spreader(self, size_ft):
        try:
            if self.unload_task and self.controller == self.unload_task.c:
                raise ValueError('Reset the unload task before changing spreader size.')
            self.controller.set_spreader_size(size_ft)
            self._refresh()
        except (ValueError, RuntimeError) as exc:
            self.status.text = str(exc)

    def stop_all(self):
        for c in self.controllers.values():
            c.stop()
        self.status.text = 'Emergency stop: all active STS axes stopped.'

    def start_unload(self):
        try:
            if self.unload_task:
                raise ValueError('Reset the existing task before starting again.')
            self.task_status.text = 'Loading STS_01 unload task...'
            UnloadTask = load_unload_task_class()
            import omni.timeline
            omni.timeline.get_timeline_interface().pause()
            if 'STS_01' not in self.controllers:
                self.controllers['STS_01'] = STSController(self.stage, 'STS_01')
            self.unload_task = UnloadTask(self.controllers['STS_01'])
            self.choice.model.get_item_value_model().set_value(self.cranes.index('STS_01'))
            self.task_status.text = self.unload_task.phase
        except Exception as exc:
            self.task_status.text = f'Start failed: {type(exc).__name__}: {exc}'
            traceback.print_exc()

    def pause_unload(self):
        if self.unload_task and not self.unload_task.finished:
            task = self.unload_task
            task.paused = not task.paused
            self.task_status.text = ('Paused: ' if task.paused else '') + task.phase

    def reset_unload(self):
        if self.unload_task:
            self.unload_task.close()
            self.unload_task = None
            self.task_status.text = 'Task reset; box restored to ship.'

    def focus(self):
        import omni.usd
        from omni.kit.viewport.utility import get_active_viewport, frame_viewport_selection
        omni.usd.get_context().get_selection().set_selected_prim_paths([self.controller.path], False)
        frame_viewport_selection(get_active_viewport())

    def focus_task(self):
        import omni.usd
        from omni.kit.viewport.utility import get_active_viewport, frame_viewport_selection
        task = self.unload_task
        path = '/World/STSUnloadTask/Truck'
        if not task:
            path = '/World/STSControls/STS_01'
        elif task.delivering and not task.delivery.phase.startswith('Truck to'):
            path = '/World/PortAndRTG/RTG_PRIMARY_DYNAMIC'
        omni.usd.get_context().get_selection().set_selected_prim_paths([path], False)
        frame_viewport_selection(get_active_viewport())

    def _update(self, event):
        import omni.usd
        if omni.usd.get_context().get_stage() != self.stage:
            self.close()
            return
        for c in self.controllers.values():
            if self.unload_task and c == self.unload_task.c and self.unload_task.paused:
                continue
            c.tick(event.payload.get('dt', 0.0))
        if self.unload_task:
            try:
                self.unload_task.tick(event.payload.get('dt', 0.0))
                task = self.unload_task
                self.task_status.text = ('Paused: ' if task.paused or task.c.stopped else '') + task.phase
            except Exception as exc:
                self.unload_task.paused = True
                self.task_status.text = 'Task paused: ' + str(exc)
        s = self.controller.state
        self.readout.text = (f'Actual: G {s["gantry"]:.2f} m | T {s["trolley"]:.2f} m | '
                             f'H {s["hoist"]:.2f} m | Boom {s["boom"]:.1f} deg | '
                             f'S {self.controller.spreader_size_ft} ft')

    def _stage_event(self, event):
        import omni.usd
        if event.type in (int(omni.usd.StageEventType.CLOSING), int(omni.usd.StageEventType.CLOSED)):
            self.close()

    def _visibility(self, visible):
        if not visible:
            self.close()

    def close(self):
        self._sub = None
        self._stage_sub = None
        self.reset_unload()
        for c in self.controllers.values():
            c.close()
        self.controllers.clear()


def show_sts_controls():
    import builtins
    import omni.usd
    old = getattr(builtins, '_smart_port_sts_panel', None)
    if old is not None:
        old.close()
        old.window.destroy()
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError('Open smart_port.usda first')
    if not stage.GetPrimAtPath(ROOT):
        # Allows activating in a stage that was opened before the layer was added.
        layer_path = Path(__file__).resolve().parents[1] / 'scenes/sts_controls.usda'
        if not layer_path.is_file():
            raise FileNotFoundError(layer_path)
        session = stage.GetSessionLayer()
        session.subLayerPaths.insert(0, str(layer_path))
    panel = STSPanel(stage)
    builtins._smart_port_sts_panel = panel
    return panel


if __name__ == '__main__':
    show_sts_controls()
