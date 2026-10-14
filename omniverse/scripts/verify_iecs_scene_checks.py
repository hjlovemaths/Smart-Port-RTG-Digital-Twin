"""Ensure diagnostics detect hidden bay markings without modifying source files."""
from pxr import Usd,UsdGeom
from iecs_runtime import PROJECT
from iecs_scene_checks import inspect_scene,MARK_ROOT,MARKS
s=Usd.Stage.Open(str(PROJECT/'omniverse/scenes/smart_port.usda'))
assert inspect_scene(s)['ok']
with Usd.EditContext(s,s.GetSessionLayer()):
    UsdGeom.Imageable(s.GetPrimAtPath(MARK_ROOT)).CreateVisibilityAttr().Set('invisible')
report=inspect_scene(s)
assert not report['ok'] and len(report['errors'])==len(MARKS),report
s.GetSessionLayer().Clear()
assert inspect_scene(s)['ok']
print('Scene diagnostics PASS: hidden bay elements detected, reversible recovery')
