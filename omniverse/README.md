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

1. Export the Blender-authored visual asset and controller animation to `assets/rtg/RTG_Model.usdc`.
2. Open `scenes/smart_port.usda`; the RTG asset is included as a payload.
3. Validate hierarchy, materials, transforms, normals, lights, and performance.
4. Split environment and dynamic equipment into separate USD assets.
5. Add physics, joints, collision, sensors, and ROS2 bindings in stronger USD layers rather than editing the source asset.

## RTG roles

The middle yard RTG is the production candidate and is exported under
`RTG_PRIMARY_DYNAMIC`. Its existing gantry, trolley, and hoist controller chain
is retained for SimReady conversion. The left and right RTGs are grouped under
`RTG_STATIC_LEFT` and `RTG_STATIC_RIGHT`; they remain visible but are detached
from the animated controllers and are not intended to receive physics or ROS2
control.

Run `scripts/configure_rtg_roles.py` in Blender to create or repair this role
hierarchy before exporting the USD asset.

`scenes/rtg_simready.usda` is a stronger, non-destructive SimReady layer for the
primary RTG. It preserves rigid-body, mass, and prismatic-joint metadata for a
later collision-proxy pass, adds the dynamic rope visual, and hides the legacy
silver rope meshes. Blender remains the source of truth for the three
controller animations. Prototype physics is disabled during visual validation
so PhysX cannot overwrite those authored transforms.

The 230-frame validation sequence raises and lowers the hoist during frames
1-70, moves the trolley during frames 90-150, and moves the complete primary
RTG 4.2 m during frames 160-230. The eight tires do not receive independent
rotation samples; they inherit the gantry transform together with the wheel
bogies, frame, cable loops, conduits, floodlights, trolley, hoist, and spreader.

The same layer contains `RTG_DYNAMIC_HOIST_ROPES`: 16 linear rope curves
measured from the Blender source. Their upper endpoints remain fixed to the
trolley while their lower endpoints use the same Z targets as the hoist joint,
so the ropes extend and retract with the spreader. They are visual geometry
only and are deliberately excluded from collision.

For live control in Kit, create `RTGController(stage)` from
`scripts/rtg_live_control.py`. `set_gantry`, `set_trolley`, and `set_hoist`
write deterministic controller transforms plus the retained drive targets to
the USD session layer. `set_hoist` updates all lower rope endpoints in the same
call, which is the entry point intended for the later ROS2/WPF data bridge.

The SimReady layer hides the 16 top-level
`RTG_BOUND_HOIST_ROPE_*__USD_MESH` meshes exported from Blender. Those meshes
are static duplicates of the dynamic rope system and would otherwise appear as
a second, silver-grey set of ropes in RTX rendering. Static left/right RTG rope
meshes are not affected.

The dynamic curves use all 16 endpoint pairs measured directly from Blender.
Their lower ends terminate at the four attachment groups on the upper edge of
the yellow hoist beam, using a small visual offset from the Blender guide-curve
tips. The rope lower endpoints
and the complete hoist/spreader hierarchy use the same hoist command, while the
upper endpoints remain fixed in trolley-local space.

The source Blender file remains the visual-authoring source of truth. Omniverse layers hold SimReady and runtime overrides.

The validation export intentionally excludes the Blender showcase hemisphere and
round display base. Omniverse lighting and environment layers should replace
that backdrop; the source `.blend` objects are not modified.

Only the `Omniverse_User_View` camera is included. Blender inspection cameras
and preview lights are omitted so their Omniverse gizmos cannot obstruct the
stage viewport.

The export uses Blender's built-in USD exporter. It exports only objects visible
in the active view layer and includes the controller time samples. During export
it creates temporary evaluated mesh copies of visible Blender curves while
preserving their controller parents, so thin rails, ropes, and cables keep both
their Blender thickness and their gantry/trolley motion in Omniverse. The source
`.blend` curves remain editable.

Static yard and ground containers are omitted from `RTG_Model.usdc`. The hoisted
`YLOAD` container, truck load, and ship deck cargo remain; runtime container
instances can be authored later from live terminal data.
