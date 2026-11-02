"""Apply the saved boom layer to an open stage without resetting crane motion."""
from pathlib import Path

def ensure_live_booms(stage):
    import builtins, importlib
    from pxr import Sdf
    path = Path(__file__).resolve().parents[1] / 'scenes/sts_boom_extension.usda'
    if not path.is_file() or not stage.GetPrimAtPath('/World/STSControls'): return
    layer = Sdf.Layer.FindOrOpen(path.as_posix())
    layer.Reload()
    if not any("sts_boom_extension.usda" in item for item in stage.GetRootLayer().subLayerPaths):
        stage.GetRootLayer().subLayerPaths.insert(0,layer.identifier)
    if not stage.GetPrimAtPath('/World/STSControls/STS_01').GetAttribute('sts:boomLengthScale').Get():
        stage.GetSessionLayer().subLayerPaths.insert(0,layer.identifier)
    pool = getattr(builtins,'_smart_port_sts_controller_pool',None)
    if pool and pool['stage'] == stage:
        for c in pool['controllers'].values():
            if not c.closed:
                c.boom_scale=stage.GetPrimAtPath(c.path).GetAttribute('sts:boomLengthScale').Get() or 1.0
                c.limits['trolley']=(-10.0,17.0+17.32*(c.boom_scale-1))
                if hasattr(c,'raised_home'): c._apply()

    home_path = path.with_name('sts_spreader_home.usda')
    if home_path.is_file():
        home_layer = Sdf.Layer.FindOrOpen(home_path.as_posix())
        home_layer.Reload()
        if not any("sts_spreader_home.usda" in item for item in stage.GetRootLayer().subLayerPaths):
            stage.GetRootLayer().subLayerPaths.insert(0,home_layer.identifier)
        if not stage.GetPrimAtPath('/World/STSControls/STS_01').GetAttribute('sts:raisedHomeOffset').Get():
            stage.GetSessionLayer().subLayerPaths.insert(0,home_layer.identifier)
        if pool and pool['stage'] == stage:
            pending = [c for c in pool['controllers'].values() if not c.closed and not hasattr(c,'raised_home')]
            if pending:
                import sts_control
                importlib.reload(sts_control)
                for c in pending:
                    c.raised_home=stage.GetPrimAtPath(c.path).GetAttribute('sts:raisedHomeOffset').Get() or 0.0
                    c.state['hoist'] += c.raised_home
                    c.targets['hoist'] += c.raised_home
                    c._apply=sts_control.STSController._apply.__get__(c,type(c))
                    c._apply()
