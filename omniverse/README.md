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
| Trolley engineering travel | 0.00 to 18.06 m | 0.00 to 18.06 m | 1.00 m/s |
| Hoist engineering height | 0.00 to 18.00 m | 0.50 to 17.70 m | 0.90 m/s |

The hard limits describe the physical stroke. The soft limits describe the
normal command envelope used by WPF/ROS2 so regular commands keep clearance
from end stops and keep the spreader at least 0.5 m above ground.

### Engineering-coordinate mapping

PLC, ROS2, and WPF use real equipment coordinates while the current visual
model retains its validated travel. The live controller applies calibrated
trolley row stops so PLC trolley positions align the spreader centreline with
the current six-row container layout:

| Engineering value | USD controller value |
| --- | ---: |
| Gantry 0 m, 1C/001 seaside bay-start origin | Y = 0.00 |
| Gantry 1,961 m, 4C/041 bay end | Y = 1,961.00 |
| Trolley 0.48-1.00 m, truck centre | X = 0.615562 |
| Trolley 3.71 m, row 6 centre | X = -1.793975 |
| Trolley 6.66 m, row 5 centre | X = -2.815810 |
| Trolley 9.50 m, row 4 centre | X = -3.837645 |
| Trolley 12.35 m, row 3 centre | X = -4.859480 |
| Trolley 15.19 m, row 2 centre | X = -5.881315 |
| Trolley 18.06 m, row 1 centre | X = -6.903150 |
| Hoist 0 m, engineering zero | Z = -2.465000 |
| Hoist 2.6 m, spreader on tier-1 container top | Z = -1.525647 |
| Hoist +2.6 m, one physical container tier | Z += 0.939353 |
| Hoist 18 m, highest position | Z = 4.038213 |

The stop table is implemented only in `scripts/rtg_live_control.py` and maps
PLC readings to the positions already validated by the `task.txt` animation.
It does not reposition the RTG model, truck, container rows, or scripted demo.

Therefore `set_gantry()` accepts 0-1,961 m from the 1C/001 seaside origin,
`set_trolley()` accepts 0-18.06 actual metres, and `set_hoist()` accepts 0-18
actual metres. Reverse conversion helpers are provided for status values sent
back to WPF/ROS2. The rope visual continues to use the mapped USD hoist
position, so it remains attached throughout the full engineering range.

`scripts/yard_coordinate_mapping.py` contains the shared business-coordinate
rules for the real-scale yard. `set_gantry_bay("1C", 25)` and
`set_gantry_bay_id("1C/025")` move the gantry to a bay target; by default the
target is the bay center. Pass `anchor="start"` or `anchor="end"` when the
command should use the bay boundary instead. Bay spacing is 6.0 m pad length
plus 0.20 m clear gap, and the four C blocks are separated by 5.0 m gaps.

Bay-addressed stops include the visual centerline calibration used by the
painted yard numbers. In particular, `currentField=3C, currentBay=63` parks
the RTG short-beam/spreader center on the middle of the visible `63` mark and
the 20-ft box center. Continuous `bigcarElecDistance` commands remain on the
PLC metre calibration and are not shifted by this label correction.

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

### PLC-free task simulation

When no PLC publisher is available, use `scripts/rtg_simulation_driver.py` in
the Kit Script Editor. It emits the same pose/task snapshot shape consumed by
`rtg_twin_state_receiver.py`, so the normal bay-map renderer, truck, spreader,
container attachment, and the shared LOAD/UNLOAD/SWITCH state machine are
exercised together:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_simulation_driver.py", encoding="utf-8").read())
start_simulation()
publish_simulated_task(
    work_type="UNLOAD",
    start_pos="3C6323",
    bay_map="441000",
    container_size=20,
)
```

Use `work_type="LOAD"` with `end_pos` for truck-to-yard, or
`work_type="SWITCH"` with both `start_pos` and `end_pos` for a rehandle. The
simulator can be stopped with `stop_simulation()` and the active task removed
with `clear_simulated_task()`. Do not run the simulator and the TCP live
receiver at the same time; both would write the same session-layer transforms.

Example task positions and debugging commands:

```python
# LOAD: truck -> row 2, tier 1
publish_simulated_task(
    work_type="LOAD",
    end_pos="3C6321",
    bay_map="401000",
    container_size=20,
)

# SWITCH: row 2, tier 3 -> row 5, tier 1
publish_simulated_task(
    work_type="SWITCH",
    start_pos="3C6323",
    end_pos="3C6351",
    bay_map="441000",
    container_size=20,
)

get_simulation_status()
clear_simulated_task()
stop_simulation()
```

`3C6324` is parsed as block `3C`, bay `063`, row `2`, tier `4`.

### Live PLC and task subscription

Run the standalone bridge from the repository root when RCMS/WPF is not
already publishing TwinState JSON on port 48700:

```powershell
python services\rtg_twin_service.py --config services\rtg_twin_service.config.json
```

The bridge subscribes to the configured PLC ZMQ endpoint (default port 5515)
and task/runinfo endpoint (default port 7065), merges the latest messages, and
publishes newline-delimited TwinState JSON on `127.0.0.1:48700`.

Start the Kit subscriber and scene driver in Script Editor:

```python
try:
    stop_simulation()
except NameError:
    pass

exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_twin_state_receiver.py", encoding="utf-8").read())
```

The script starts `start_live_driver("127.0.0.1", 48700)` automatically.
Use `get_latest_state()` to inspect the latest combined snapshot,
`apply_latest_state_once()` to replay it, and `stop_live_driver()` to stop the
TCP subscription and Kit update callback. Offline bridge checks:

```powershell
python services\rtg_twin_service.py --once
python services\rtg_twin_service.py --once --plc-json path\to\Yard_PLC_RTG_220.json --task-json path\to\Yard_TaskInfo_RTG_220.json
```

See `services/README.md` for jump-host relay/pull modes and the optional WPF
debug stream on port 48701.

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

Each physical container tier is treated as 2.6 m, so a six-tier stack is
15.6 m high in engineering coordinates. The compressed visual stack keeps its
existing 0.904353 m box height plus 0.035 m tier gap; live hoist movement maps
each physical 2.6 m increment to that 0.939353 m visual tier pitch.

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

## RTG camera marker popup

The Kit receiver can open an RTSP preview when a camera marker is selected in
the viewport. Camera definitions are read from the local, git-ignored file
`omniverse/config/cameras/Cam_RTG_220.json`, with the RCMS camera configuration
used as a fallback.
The authored RTG markers (`C1`–`C33`) are preserved; markers without a
matching entry open an `UNCONFIGURED` status instead of creating geometry
outside the RTG. To start it standalone from Script Editor:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_camera_popup.py", encoding="utf-8").read())
start_rtg_camera_popup()
```

Select a green marker to open the `RTG Camera` window. The reader uses the Axis
RTSP endpoint `rtsp://user:password@ip:port/axis-media/media.amp` built from
the configuration (including the `AttachedLine` camera query). For HaiKang/Hikvision entries the reader uses
`/Streaming/Channels/101`-style URLs and defaults to RTSP port 554; add
`RtspPort` or `RtspChannel` to override those defaults. The Omniverse Kit Python environment needs an OpenCV build
with FFMPEG/RTSP support; if it is unavailable the popup reports the missing
decoder and still shows the resolved stream URL. Stop it with
`stop_rtg_camera_popup()`.

## RTG virtual scene cameras

The same authored green marker prims can also be used as virtual camera mount
points inside the 3D world. Run this in the Kit Script Editor after opening
`scenes/smart_port.usda`:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_virtual_cameras.py", encoding="utf-8").read())
create_rtg_virtual_cameras()
open_rtg_virtual_camera(1)
```

The script scans the current stage for markers named `C1` through `C33`, removes
older generated marker-child cameras, and creates `/World/RTGVirtualCameras/C01`
through `/World/RTGVirtualCameras/C33` aimed at the RTG work area. The two-digit
names keep Omniverse camera menus sorted in numeric order.

`create_rtg_virtual_cameras()` creates session-only cameras for immediate
debugging. Use `open_rtg_virtual_camera(1)` through
`open_rtg_virtual_camera(33)` to bind the active viewport. Persist the camera
definitions with:

```python
save_rtg_virtual_camera_layer()
```

`scenes/rtg_virtual_cameras.usda` is sublayered by
`scenes/smart_port.usda`, so after saving the layer once, reopening or
reloading the stage loads all cameras without regenerating them in the session
layer. Use `cleanup_rtg_virtual_cameras()` to remove session-generated cameras.
