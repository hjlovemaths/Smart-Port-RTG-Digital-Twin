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

The demo animation still keeps the small authored gantry movement for visual
validation, but the engineering limits now match the real 1C-4C yard:

| Joint | Hard limits | Soft limits | Maximum velocity |
| --- | ---: | ---: | ---: |
| Gantry engineering travel | 0.00 to 1,961.00 m | 1.00 to 1,960.00 m | 1.50 m/s |
| Trolley engineering travel | 0.00 to 18.00 m | 0.30 to 17.70 m | 1.00 m/s |
| Hoist engineering height | 0.00 to 15.00 m | 0.50 to 14.70 m | 0.90 m/s |

The hard limits describe the physical stroke. The soft limits describe the
normal command envelope used by WPF/ROS2 so regular commands keep clearance
from end stops and keep the spreader at least 0.5 m above ground.

### Engineering-coordinate mapping

PLC, ROS2, and WPF use real equipment coordinates while the current visual
model retains its validated travel. The live controller applies these linear
mappings:

| Engineering value | USD controller value |
| --- | ---: |
| Gantry 0 m, 1C/001 seaside bay-start origin | Y = 0.00 |
| Gantry 1,961 m, 4C/041 bay end | Y = 1,961.00 |
| Trolley 0 m, left end near the tower | X = 0.00 |
| Trolley 18 m, far end | X = -2.25 |
| Hoist 0 m, spreader at ground level | Z = -0.45 |
| Hoist 15 m, highest position | Z = 0.85 |

Therefore `set_gantry()` accepts 0-1,961 m from the 1C/001 seaside origin,
`set_trolley()` accepts 0-18 actual metres, and `set_hoist()` accepts 0-15
actual metres. Reverse conversion helpers are provided for status values sent
back to WPF/ROS2. The rope visual continues to use the mapped USD hoist
position, so it remains attached throughout the full engineering range.

`scripts/yard_coordinate_mapping.py` contains the shared business-coordinate
rules for the real-scale yard. `set_gantry_bay("1C", 25)` and
`set_gantry_bay_id("1C/025")` move the gantry to a bay target; by default the
target is the bay center. Pass `anchor="start"` or `anchor="end"` when the
command should use the bay boundary instead. Bay spacing is 6.0 m pad length
plus 0.20 m clear gap, and the four C blocks are separated by 5.0 m gaps.

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
Use `set_positions()` when a command is allowed to reach the hard limits, and
`set_positions_safe()` for normal operation inside the soft-limit envelope.

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

## Runtime container generation

`scripts/build_live_containers.py` generates lightweight container stacks into
`scenes/live_containers.usda`. The current demo command is:

```bash
blender --background --python omniverse/scripts/build_live_containers.py -- --bay 1C/041 --pattern 413413
```

The pattern is parsed one digit per row from right to left. For example,
`413413` means row 1 on the right has 4 tiers, row 2 has 1 tier, row 3 has 3
tiers, and so on toward the left. The generated layer writes one schematic stack
column per row, adds `live:bayId`, `live:row`, and `live:tiers` metadata, draws
tier separator lines, and draws a red outline around the bay-map footprint.
`scenes/smart_port.usda` sublayers `live_containers.usda`, so reloading the
stage shows the current live stack without changing the Blender source asset.

The bay-map visualization is constrained to fit inside the 12 m bay width. The
left side keeps 1.8 m clear for the truck lane, while the right side keeps 0.4 m
clearance from the RTG structure. It uses a 1.20 m schematic tier height, so the
maximum 6-tier stack is 7.2 m high. This keeps the live bay-map readable without
blocking the RTG cabin and lower beam in the current visual scene.

## Real-scale 1C-4C yard layout

Run `scripts/build_yard_layout.py` in Blender to regenerate the central yard
layout. The current parameters are:

| Block | Bay range | Block length |
| --- | ---: | ---: |
| 1C | 001-091 | 564 m |
| 2C | 001-091 | 564 m |
| 3C | 001-091 | 564 m |
| 4C | 001-041 | 254 m |

Each bay pad is 6 m long and 12 m wide. Adjacent pads have a 0.20 m clear gap,
and adjacent C blocks have a 5 m clear gap. The complete layout is 1,961 m
long. `YARD_LAYOUT_1C4C_BAY_ANCHORS` contains 314 logical anchors with
`yard_block`, `bay_number`, and `bay_id` properties for later live container
instancing. The visual pads and markings are combined per block to keep the
Blender and USD object counts low.

The same build pass extends the existing continuous left, right, and outer
side-yard surfaces, stack pads, lane boundaries, AGV routes, and the internal
truck lane to the same `Y=-0.5` through `Y=1960.5` longitudinal range. Their
authored widths and X positions are preserved. Short decorative objects and
existing container meshes are not stretched.

Three previously uncovered longitudinal strips are filled by generated yard
surfaces and edge markings over the same 1,961 m range:

- left-central infill: X = -32.0 to -8.0 m;
- right-central infill: X = 8.0 to 37.5 m;
- right narrow infill: X = 60.0 to 67.0 m.
