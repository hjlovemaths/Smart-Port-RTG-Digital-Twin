"""Run run(stage) on Kit's main thread with a paused timeline.

Creates only invisible proxies in the PLC PhysicsScene, away from the RTG.
Does not play the timeline, move crane axes or save the user's stage.
"""
from pxr import Gf, UsdGeom
from rtg_plc_physx import PlcPhysx, ROOT
import rtg_live_control as live


def run(stage):
    backend = PlcPhysx(stage)
    pose = lambda z: Gf.Matrix4d().SetTranslate(Gf.Vec3d(900, 200, z))
    before = {path: UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path))
              for path in (live.GANTRY_PATH, live.TROLLEY_PATH, live.HOIST_PATH)
              if stage.GetPrimAtPath(path)}
    try:
        backend.start()
        cargo = backend.body("SmokeCargo", (1, 2, 1), pose(3), False)
        for _ in range(240):
            backend.step(1 / 120)
        grounded = backend.pose(cargo).ExtractTranslation()[2]
        assert abs(grounded - .6) < .02, ("gravity/ground", grounded)
        backend.set_kinematic(cargo, True)
        backend.set_pose(cargo, pose(3))
        anchor = backend.body("SmokeSpreader", (1, 2, .2), pose(3.6), True)
        backend.step(1 / 120)
        backend.lock(anchor, cargo, pose(3.6), pose(3))
        for i in range(120):
            backend.set_pose(anchor, pose(3.6 + (i + 1) / 120))
            backend.step(1 / 120)
        lifted = backend.pose(cargo).ExtractTranslation()[2]
        assert abs(lifted - 4) < .025, ("fixed twistlock/lift", lifted)
        backend.body("SmokeStack", (2, 3, 1), pose(1.1), True)
        backend.unlock()
        for _ in range(240):
            backend.step(1 / 120)
        stacked = backend.pose(cargo).ExtractTranslation()[2]
        assert abs(stacked - 2.1) < .025, ("unlock/stack collision", stacked)
        temporary = backend.body("SmokeTemporary", (1, 1, 1), pose(10), True)
        backend.step(1 / 120)
        backend.remove_body(temporary)
        assert not stage.GetPrimAtPath(temporary), "stale body after remove"
        backend.body("SmokeTemporary", (1, 1, 1), pose(11), True)
        backend.step(1 / 120)
        assert abs(backend.pose(temporary).ExtractTranslation()[2] - 11) < .001
        result = {"ground_z": grounded, "lifted_z": lifted, "stacked_z": stacked}
    finally:
        backend.close()
    assert not stage.GetPrimAtPath(ROOT), "stale proxy namespace after close"
    for path, matrix in before.items():
        after = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path))
        assert Gf.IsClose(matrix, after, 1e-7), ("unrelated RTG transform changed", path)
    return result
