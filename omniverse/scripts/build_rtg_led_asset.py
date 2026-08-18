"""Build the RTG-ready LED asset without modifying the source USDZ."""

from __future__ import annotations

from pathlib import Path

from pxr import Gf, Usd, UsdGeom, UsdShade


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "omniverse" / "assets" / "resource" / "led" / "LED_Floodlight.usdz"
OUTPUT = ROOT / "omniverse" / "assets" / "resource" / "led" / "RTG_LED_Floodlight.usdc"
PHOSPHOR_SHADER = "/scene/Materials/phosphorus_002/pbr_shader"
BODY_SHADER = "/scene/Materials/body/pbr_shader"
LED_BASE_SHADER = "/scene/Materials/led_base/pbr_shader"
GLASS_SHADER = "/scene/Materials/tempered_glass/pbr_shader"
LED_BASE_MESH = "/scene/Meshes/Sketchfab_model/root/GLTF_SceneRootNode/Plane_001_9/Plane_8/led_chip_6/Object_11/Object_4"
LED_PHOSPHOR_MESH = "/scene/Meshes/Sketchfab_model/root/GLTF_SceneRootNode/Plane_001_9/Plane_8/led_chip_6/Object_12/Object_5"
# The source board is wide enough already, but its bead field only occupies
# about 40% of the fixture height.  Scale both the board and beads around
# their own centre so the visible matrix fills roughly 80% of the face.
LED_FACE_SCALE_X = 1.04
LED_FACE_SCALE_Y = 2.0
PHOSPHOR_EMISSIVE = Gf.Vec3f(18.0, 26.0, 6.0)
# A small amount of self-illumination keeps the fixture silhouette readable
# against the RTG's dark-blue underside when the night preset is active. The
# phosphor remains the bright source; these values only lift housing edges.
BODY_EMISSIVE = Gf.Vec3f(0.025, 0.040, 0.070)
LED_BASE_EMISSIVE = Gf.Vec3f(0.060, 0.090, 0.130)
GLASS_EMISSIVE = Gf.Vec3f(0.120, 0.180, 0.240)


def _scale_face_mesh(stage: Usd.Stage, path: str) -> None:
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsA(UsdGeom.Mesh):
        raise RuntimeError(f"Missing LED face mesh: {path}")
    mesh = UsdGeom.Mesh(prim)
    attr = mesh.GetPointsAttr()
    points = list(attr.Get() or [])
    if not points:
        raise RuntimeError(f"LED face mesh has no points: {path}")
    min_x = min(float(point[0]) for point in points)
    max_x = max(float(point[0]) for point in points)
    min_y = min(float(point[1]) for point in points)
    max_y = max(float(point[1]) for point in points)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    scaled_points = [
        Gf.Vec3f(
            center_x + (float(point[0]) - center_x) * LED_FACE_SCALE_X,
            center_y + (float(point[1]) - center_y) * LED_FACE_SCALE_Y,
            point[2],
        )
        for point in points
    ]
    attr.Set(scaled_points)
    # USD meshes often carry a cached extent; keep it in sync so the scaled
    # face is not culled using the source asset's smaller bounds.
    extent_min = Gf.Vec3f(
        min(float(point[0]) for point in scaled_points),
        min(float(point[1]) for point in scaled_points),
        min(float(point[2]) for point in scaled_points),
    )
    extent_max = Gf.Vec3f(
        max(float(point[0]) for point in scaled_points),
        max(float(point[1]) for point in scaled_points),
        max(float(point[2]) for point in scaled_points),
    )
    mesh.GetExtentAttr().Set([extent_min, extent_max])


def main() -> None:
    source_stage = Usd.Stage.Open(str(SOURCE))
    if source_stage is None:
        raise RuntimeError(f"Unable to open LED source asset: {SOURCE}")
    source_stage.Load()
    source_stage.Flatten().Export(str(OUTPUT))

    stage = Usd.Stage.Open(str(OUTPUT))
    if stage is None:
        raise RuntimeError(f"Unable to open generated LED asset: {OUTPUT}")

    shader = stage.GetPrimAtPath(PHOSPHOR_SHADER)
    if not shader:
        raise RuntimeError(f"Missing LED phosphor shader: {PHOSPHOR_SHADER}")
    # Keep the fixture face visibly illuminated in the night scene. The actual
    # ground throw is provided by the eight authored DiskLights in the layer.
    shader.GetAttribute("inputs:emissiveColor").Set(PHOSPHOR_EMISSIVE)

    for mesh_path in (LED_BASE_MESH, LED_PHOSPHOR_MESH):
        _scale_face_mesh(stage, mesh_path)

    # The source asset's body material is pure black, which disappears against
    # the RTG underside. Give the housing a visible industrial-gray finish and
    # lift the fixture edge materials slightly in the night scene.
    material_colors = {
        BODY_SHADER: (Gf.Vec3f(0.16, 0.18, 0.20), BODY_EMISSIVE),
        LED_BASE_SHADER: (Gf.Vec3f(0.42, 0.45, 0.48), LED_BASE_EMISSIVE),
        GLASS_SHADER: (Gf.Vec3f(0.82, 0.88, 0.92), GLASS_EMISSIVE),
    }
    for shader_path, (diffuse, emissive) in material_colors.items():
        shader = stage.GetPrimAtPath(shader_path)
        shader.GetAttribute("inputs:diffuseColor").Set(diffuse)
        shader.GetAttribute("inputs:emissiveColor").Set(emissive)

    # The source USDZ contains material relationships without the matching API
    # schema. Applying it here keeps Blender and other USD consumers warning-free.
    for prim in stage.Traverse():
        if prim.HasRelationship("material:binding"):
            UsdShade.MaterialBindingAPI.Apply(prim)

    stage.GetRootLayer().Save()
    print(f"Wrote {OUTPUT} (night emissive enabled)")


if __name__ == "__main__":
    main()
