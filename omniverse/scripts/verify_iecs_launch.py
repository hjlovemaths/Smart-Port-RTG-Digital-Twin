"""Exercise restart-free reopening and explicit refresh without Kit UI."""
import runpy
from pathlib import Path
from pxr import Usd,UsdGeom
project=Path(__file__).resolve().parents[2]
launch=runpy.run_path(str(project/'omniverse/scripts/iecs_launch.py'))['launch']
s=Usd.Stage.Open(str(project/'omniverse/scenes/smart_port.usda'))
options=dict(port=0,with_ui=False,subscribe=False,db_path=':memory:')
r=launch(stage=s,**options)
try:
    r.process({'action':'submit','flow':'gate_in'})
    r.engine.tick(.25);r.apply_scene()
    before=r.engine.snapshot()
    assert launch(stage=s,**options) is r
    assert r.engine.snapshot()==before
    c=next(iter(r.engine.containers.values()))
    assert r.check_scene()['ok'],r.scene_report
    with Usd.EditContext(s,r.layer):
        xf=UsdGeom.Xformable(s.GetPrimAtPath(c['visual_prim']))
        xf.AddScaleOp().Set((.5,.5,.5))
    report=r.check_scene()
    assert any('size/heading mismatch' in e for e in report['errors']),report
    r.engine.control('pause')
    try:r.process({'action':'resume'})
    except ValueError:pass
    else:raise AssertionError('Resume allowed inconsistent scene')
    assert r.engine.paused
    r2=launch(restart=True,stage=s,**options)
    assert r.closed and not r2.engine.orders
    assert r2.check_scene()['ok']
    r2.close()
    print('Launcher PASS: absolute imports, reopen preserves task, invalid scale blocks resume, explicit restart')
finally:r.close()
