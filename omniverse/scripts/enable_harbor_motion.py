"""Run in Kit Script Editor to enable and verify motion in the current stage.

Does not reopen or save the scene. All animation is in a removable session layer.
"""
import asyncio
import importlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
scripts=str(ROOT/'omniverse/scripts')
if scripts not in sys.path: sys.path.insert(0,scripts)
import ship_sea_motion
ship_sea_motion=importlib.reload(ship_sea_motion)
motion=ship_sea_motion.show_sea_motion()


async def verify_live_motion():
    import omni.kit.app
    from pxr import UsdGeom
    paths=list(motion.ships)
    def sample():
        cache=UsdGeom.XformCache()
        return {p:str(cache.GetLocalToWorldTransform(motion.stage.GetPrimAtPath(p))) for p in paths}
    before=sample()
    uv_before=str(motion.uv_translation.Get()) if motion.uv_translation else None
    start=motion.time
    for _ in range(900):
        await omni.kit.app.get_app().next_update_async()
        if motion.closed: return
        if motion.time-start>=4: break
    after=sample()
    result={'enabled':motion.enabled,'elapsed':motion.time-start,'ships':len(paths),
            'shipsMoving':[p for p in paths if before[p]!=after[p]],'held':list(motion.held),
            'waterUVBefore':uv_before,'waterUVAfter':str(motion.uv_translation.Get()) if motion.uv_translation else None,
            'quality':'smooth' if motion.batch_count==1 else 'eco','rootLayerSaved':False}
    out=ROOT/'outputs/sea_motion'
    out.mkdir(parents=True,exist_ok=True)
    (out/'live_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('HARBOR_MOTION_VERIFIED',len(result['shipsMoving']),'ships moving; water UV',result['waterUVAfter'])
    from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
    viewport=get_active_viewport()
    if viewport:
        await capture_viewport_to_file(viewport,str(out/'live_water.png')).wait_for_result(completion_frames=8)


verification_task=asyncio.ensure_future(verify_live_motion())
