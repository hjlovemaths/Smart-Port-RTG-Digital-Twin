"""Cache-independent IECS entry point. Reopening does not reset active tasks."""
import builtins
import importlib.util
import sys
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[2]


def launch(restart=False,stage=None,**runtime_options):
    old=getattr(builtins,'_iecs_runtime',None)
    if old and not old.closed and not restart:
        if stage is not None and stage!=old.stage:
            raise ValueError('IECS belongs to another stage; use restart=True explicitly.')
        if stage is None:
            import omni.usd
            if omni.usd.get_context().get_stage()!=old.stage:
                raise ValueError('Stage changed; use restart=True explicitly.')
        if old.window:old.window.visible=True
        return old
    if old and not old.closed:old.close()
    for folder in (PROJECT/'services',PROJECT/'omniverse/scripts'):
        if str(folder) not in sys.path:sys.path.insert(0,str(folder))
    for name,folder in (('iecs_geometry','services'),('iecs_collision','services'),('iecs_inventory','services'),('iecs_core','services'),
                        ('iecs_scene_checks','omniverse/scripts'),
                        ('iecs_ship_pickup','omniverse/scripts'),('iecs_runtime','omniverse/scripts')):
        previous=sys.modules.get(name)
        spec=importlib.util.spec_from_file_location(name,PROJECT/folder/(name+'.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        try:spec.loader.exec_module(module)
        except Exception:
            if previous is None:sys.modules.pop(name,None)
            else:sys.modules[name]=previous
            raise
    if stage is None:
        import omni.usd
        stage=omni.usd.get_context().get_stage()
    if not stage:raise ValueError('Open smart_port.usda before launching IECS.')
    runtime=sys.modules['iecs_runtime'].Runtime(stage,**runtime_options)
    builtins._iecs_runtime=runtime
    if runtime.window:runtime.render_ui()
    return runtime
