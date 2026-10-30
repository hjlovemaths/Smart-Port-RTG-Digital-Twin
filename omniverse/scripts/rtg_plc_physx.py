"""A manually stepped PhysX scene for PLC cargo, separate from the RTG hierarchy.

    The main timeline stays paused. Only this scene is simulated, so the source
    animation and existing articulated crane bodies never become axis writers.
"""
import math
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

ROOT = "/World/RtgPlcInteraction"
SCENE = ROOT + "/PhysicsScene"


class PlcPhysx:
    def __init__(self, stage):
        self.stage = stage
        self.layer = self.sim = self.native = self.timeline = None
        self.owns_attachment = False
        self.time = 0.0

    def start(self):
        try:
            import omni.physx
            import omni.timeline
            from pxr import PhysicsSchemaTools, PhysxSchema
        except ImportError as exc:
            raise RuntimeError("PhysX is required for sim_physics; enable omni.physx and reload the PLC adapter") from exc
        self.timeline = omni.timeline.get_timeline_interface()
        self.check()
        if UsdGeom.GetStageUpAxis(self.stage) != "Z" or abs(UsdGeom.GetStageMetersPerUnit(self.stage) - 1) > 1e-6:
            raise ValueError("PLC physics requires the metre-based Z-up Smart Port stage")
        self.sim = omni.physx.get_physx_simulation_interface()
        self.native = omni.physx.get_physx_interface()
        self.stage_id = UsdUtils.StageCache.Get().Insert(self.stage).ToLongInt()
        attached = self.sim.get_attached_stage()
        if attached not in (0, self.stage_id):
            raise RuntimeError("Another USD stage owns PhysX; stop its simulation first")
        self.scene_id = PhysicsSchemaTools.sdfPathToInt(Sdf.Path(SCENE))
        if self.stage.GetPrimAtPath(ROOT):
            raise RuntimeError("PLC physics proxy namespace is already in use; close the previous adapter first")
        self.layer = Sdf.Layer.CreateAnonymous("smartport_plc_physics.usda")
        self.stage.GetSessionLayer().subLayerPaths.insert(0, self.layer.identifier)
        try:
            with Usd.EditContext(self.stage, self.layer):
                scene = UsdPhysics.Scene.Define(self.stage, SCENE)
                scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
                scene.CreateGravityMagnitudeAttr(9.81)
                api = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
                api.CreateEnableCCDAttr(True)
                api.CreateBroadphaseTypeAttr("MBP")
                api.CreateSolverTypeAttr("TGS")
                self.body("Ground", (2000, 2000, .2), Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 200, 0)), True)
            if not attached:
                with Usd.EditContext(self.stage, self.layer):
                    self.sim.attach_stage(self.stage_id)
                self.owns_attachment = True
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.timeline is not None and self.timeline.is_playing():
            raise RuntimeError("Pause the Kit timeline before ZMQ physics control; this mode steps its own PhysX scene")

    def body(self, name, size, pose, kinematic=True):
        from pxr import PhysxSchema
        path = ROOT + "/Bodies/" + name
        existing = self.stage.GetPrimAtPath(path)
        if existing:
            self.set_pose(path, pose)
            with Usd.EditContext(self.stage, self.layer):
                scale = self.stage.GetPrimAtPath(path + "/Collider").GetAttribute("xformOp:scale")
                if scale.Get() != Gf.Vec3d(*size):
                    scale.Set(Gf.Vec3d(*size))
            return path
        with Usd.EditContext(self.stage, self.layer):
            xform = UsdGeom.Xform.Define(self.stage, path)
            xform.AddTranslateOp().Set(pose.ExtractTranslation())
            xform.AddOrientOp().Set(Gf.Quatf(pose.ExtractRotationQuat()))
            rigid = UsdPhysics.RigidBodyAPI.Apply(xform.GetPrim())
            rigid.CreateRigidBodyEnabledAttr(True)
            rigid.CreateKinematicEnabledAttr(kinematic)
            rigid.CreateSimulationOwnerRel().SetTargets([Sdf.Path(SCENE)])
            UsdPhysics.MassAPI.Apply(xform.GetPrim()).CreateMassAttr(24000.0)
            extra = PhysxSchema.PhysxRigidBodyAPI.Apply(xform.GetPrim())
            extra.CreateEnableCCDAttr(not kinematic)
            extra.CreateSolverPositionIterationCountAttr(16)
            extra.CreateSolverVelocityIterationCountAttr(4)
            cube = UsdGeom.Cube.Define(self.stage, path + "/Collider")
            cube.CreateSizeAttr(1)
            scales = cube.GetOrderedXformOps()
            (scales[0] if scales else cube.AddScaleOp()).Set(Gf.Vec3d(*size))
            cube.CreateVisibilityAttr("invisible")
            cube.CreatePurposeAttr("guide")
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim()).CreateCollisionEnabledAttr(True)
            collider = PhysxSchema.PhysxCollisionAPI.Apply(cube.GetPrim())
            collider.CreateContactOffsetAttr(.003)
            collider.CreateRestOffsetAttr(0.0)
        return path

    def set_pose(self, path, pose):
        with Usd.EditContext(self.stage, self.layer):
            prim = self.stage.GetPrimAtPath(path)
            for name, value in (("xformOp:translate", pose.ExtractTranslation()),
                                ("xformOp:orient", Gf.Quatf(pose.ExtractRotationQuat()))):
                attr = prim.GetAttribute(name)
                if attr.Get() != value:
                    attr.Set(value)

    def set_kinematic(self, path, enabled):
        from pxr import PhysxSchema
        with Usd.EditContext(self.stage, self.layer):
            prim = self.stage.GetPrimAtPath(path)
            # PhysX rejects CCD on kinematic bodies. Sweeps guard their motion;
            # CCD belongs only to the dynamic cargo.
            PhysxSchema.PhysxRigidBodyAPI(prim).GetEnableCCDAttr().Set(not enabled)
            UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Set(enabled)

    def remove_body(self, path):
        with Usd.EditContext(self.stage, self.layer):
            self.stage.RemovePrim(path)

    def lock(self, anchor, cargo, anchor_pose, cargo_pose):
        try:
            with Usd.EditContext(self.stage, self.layer):
                joint = UsdPhysics.FixedJoint.Define(self.stage, ROOT + "/Twistlock")
                joint.CreateBody0Rel().SetTargets([Sdf.Path(anchor)])
                joint.CreateBody1Rel().SetTargets([Sdf.Path(cargo)])
                relative = cargo_pose * anchor_pose.GetInverse()
                joint.CreateLocalPos0Attr(Gf.Vec3f(relative.ExtractTranslation()))
                joint.CreateLocalRot0Attr(Gf.Quatf(relative.ExtractRotationQuat()))
                joint.CreateLocalPos1Attr(Gf.Vec3f(0))
                joint.CreateLocalRot1Attr(Gf.Quatf(1))
                joint.CreateCollisionEnabledAttr(False)
                joint.CreateJointEnabledAttr(True)
                self.set_kinematic(cargo, False)
        except BaseException:
            self.unlock()
            self.set_kinematic(cargo, True)
            raise

    def unlock(self):
        with Usd.EditContext(self.stage, self.layer):
            self.stage.RemovePrim(ROOT + "/Twistlock")

    def step(self, dt):
        self.check()
        # Exactly one owner steps this scene. Never step the port's other scenes.
        if dt <= 0:
            return
        count = max(1, int(math.ceil(dt * 120)))
        with Usd.EditContext(self.stage, self.layer):
            for _ in range(count):
                self.sim.simulate_scene(self.scene_id, dt / count, self.time)
                self.sim.fetch_results_scene(self.scene_id)
                self.time += dt / count

    def pose(self, path):
        state = self.native.get_rigidbody_transformation(path)
        if not state.get("ret_val"):
            raise RuntimeError("PhysX did not create cargo body: " + path)
        q = state["rotation"]
        return (Gf.Matrix4d().SetRotate(Gf.Quatd(q[3], Gf.Vec3d(q[0], q[1], q[2])))
                * Gf.Matrix4d().SetTranslate(Gf.Vec3d(*state["position"])))

    def close(self):
        if self.owns_attachment and self.sim is not None and self.sim.get_attached_stage() == self.stage_id:
            with Usd.EditContext(self.stage, self.layer):
                self.sim.detach_stage()
        self.owns_attachment = False
        if self.layer is not None:
            # Native detach normalises/reset poses into the app's edit target
            # (possibly the root layer, regardless of our edit context). This
            # namespace was absent before start: all its specs are transient.
            # Remove those specs in memory, never save/revert any source layer.
            for layer in self.stage.GetLayerStack():
                if layer.GetPrimAtPath(ROOT):
                    edit = Sdf.BatchNamespaceEdit()
                    edit.Add(Sdf.Path(ROOT), Sdf.Path.emptyPath)
                    if not layer.Apply(edit):
                        raise RuntimeError("Could not clear PLC physics proxy overrides in " + layer.identifier)
            paths = self.stage.GetSessionLayer().subLayerPaths
            if self.layer.identifier in paths:
                paths.remove(self.layer.identifier)
        self.layer = None
