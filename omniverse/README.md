# Smart Port RTG Digital Twin - Omniverse Content Project

This directory contains the OpenUSD content project used by the Smart Port RTG Digital Twin Kit application.

## Entry point

Open `scenes/smart_port.usda` in Omniverse.

## Layout

- `scenes/`: composed stages and environment layers.
- `assets/rtg/`: Blender-exported RTG and port assets.
- `materials/`: shared MDL/USD materials added during SimReady authoring.
- `scripts/`: repeatable Blender/USD preparation scripts.
- `config/`: project metadata and import settings.
- `docs/`: model, animation, and SimReady conversion analysis.

## Workflow

1. Export a static validation asset from Blender to `assets/rtg/RTG_Model.usdc`.
2. Open `scenes/smart_port.usda`; the RTG asset is included as a payload.
3. Validate hierarchy, materials, transforms, normals, lights, and performance.
4. Split environment and dynamic equipment into separate USD assets.
5. Add physics, joints, collision, sensors, and ROS2 bindings in stronger USD layers rather than editing the source asset.

The source Blender file remains the visual-authoring source of truth. Omniverse layers hold SimReady and runtime overrides.

The validation export intentionally excludes the Blender showcase hemisphere and
round display base. Omniverse lighting and environment layers should replace
that backdrop; the source `.blend` objects are not modified.

Only the `Omniverse_User_View` camera is included. Blender inspection cameras
and preview lights are omitted so their Omniverse gizmos cannot obstruct the
stage viewport.

The export uses Blender's built-in USD exporter. It exports only objects visible
in the active view layer. During export it creates temporary evaluated mesh
copies of visible Blender curves, so thin rails, ropes, and cables keep their
Blender thickness in Omniverse while the source `.blend` curves remain editable.

Static yard and ground containers are omitted from `RTG_Model.usdc`. The hoisted
`YLOAD` container, truck load, and ship deck cargo remain; runtime container
instances can be authored later from live terminal data.
