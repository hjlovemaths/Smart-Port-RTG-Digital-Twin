# RTG Isaac Sim Physics Prototype

## Purpose

`omniverse/scenes/rtg_simready.usda` already contains the RTG PhysicsScene,
three prismatic joints, drive parameters, limits, and mass metadata. The
prototype is disabled by default so the current visual demo continues to use
the Blender-authored animation.

`omniverse/scripts/rtg_isaac_physics.py` explicitly activates that skeleton in
the Kit session layer. It freezes the authored Blender animation on the three
physics links, spreader visual controls, and rope curve, enables the three
rigid bodies and joints, places the articulation root on the Gantry link, and
sends position targets to the linear drives. A lightweight Kit update callback
then updates the rope endpoints from the evaluated hoist pose, so the rope
cannot descend independently of the visible spreader. Coarse hidden box
colliders are optional and disabled by default until the joint-only test is
stable.

## Required application

Run this in a full Isaac Sim application with the physics and articulation
extensions available. The current custom Kit demo can continue to display the
scene, but importing `isaacsim.*` controllers requires the Isaac Sim runtime.
Isaac Sim's current articulation controller accepts position, velocity, or
effort commands; the OmniGraph controller remains a supported option. See the
[official articulation controller documentation](https://docs.isaacsim.omniverse.nvidia.com/latest/py/source/extensions/isaacsim.core.nodes/docs/ogn/OgnIsaacArticulationController.html).

## Activation

For the standalone Isaac Sim install on Windows, the project includes a
launcher that opens the stage in the full Isaac Sim Python runtime. From a
PowerShell prompt:

```powershell
& 'D:\isaacsim\python.bat' `
  'D:\dnt\project\Smart Port RTG Digital Twin\omniverse\scripts\launch_isaac_sim_project.py' `
  --activate-physics
```

The `--activate-physics` option activates the controller after the stage is
loaded and leaves the application paused, so pressing Play is still an
explicit user action. Omit that option when you only want to inspect the
visual scene. The launcher uses the full Isaac Sim experience, which includes
the Script Editor and Statistics windows. It defaults to a `1780x900`
viewport and DLSS Balanced; override them when using another monitor, for
example `--width 1920 --height 1080 --dlss-mode quality`.
The Statistics/FPS window opens automatically; use `--hide-fps` to suppress it.

The launcher uses the stable joint-only pass by default. Do not add collision
proxies until their measured geometry and collision filtering have been
validated; the current coarse boxes are experimental and overlap around the
trolley/hoist joints. To explicitly test them, add `--collision-proxies` to
the launcher command.

Open `omniverse/scenes/smart_port.usda`, then choose `Window -> Console` in
Isaac Sim. Execute the following as one line. Replace the root path with the
actual project path on the target computer.

```python
exec(open(r"D:/path/to/project/omniverse/scripts/rtg_isaac_physics.py", encoding="utf-8").read())
activate_rtg_physics()
set_rtg_physics_targets(gantry_m=10.0, trolley_m=8.0, hoist_m=4.0)
```

`activate_rtg_physics()` starts the rope visual synchronizer automatically.
After Play, the synchronizer also initializes Isaac Sim's articulation view
and replays the latest three joint targets through the runtime articulation
controller. The USD Drive targets remain authored for inspection and limits,
while the runtime controller is the active command path.
For a shell where the Kit update stream is unavailable, call
`update_rtg_physics_visuals()` manually after each physics step, or start it
explicitly with `start_rtg_physics_visual_sync()`.

Press Play in Isaac Sim and check the physics debug visualization. The first
pass is intentionally coarse; it is for validating that the three bodies stay
connected, respond to drives, and respect limits.

Keep the default joint-only pass first. If it is stable, enable the coarse
proxies explicitly:

```python
deactivate_rtg_physics()
activate_rtg_physics(create_collision_proxies=True)
```

Never enable the coarse proxies before confirming that the joints are stable;
their dimensions are placeholders and they can create self-contact until they
are replaced with measured compound colliders.

After a physics step, inspect evaluated body poses with:

```python
get_rtg_physics_state()
```

This reports the simulated USD body transforms. It does not yet report joint
forces; force/torque sensing belongs to the next integration phase.

Return to the visual demo with:

```python
deactivate_rtg_physics()
```

After deactivation, reopen the stage before returning to the timeline demo;
the animation masks are session-layer opinions and are intentionally cleared
by a stage reload.

Do not run the simulation driver, live TwinState receiver, or manual visual
controller at the same time as this physics controller. Both systems would
write the same RTG transforms.

## Validation order

1. Start with the Hoist Z joint and confirm the spreader moves vertically.
2. Add the Trolley X target and confirm the trolley moves without rotating.
3. Add the Gantry Y target and confirm the complete RTG remains stable.
4. Add a simplified dynamic container and a lock fixed joint.
5. Test lower/upper limits, drive saturation, collision contacts, and an
   emergency stop before connecting PLC or ROS2 commands.

## Important scale decision

The current visual asset maps the engineering hoist travel onto a compressed
USD range. That is acceptable for the existing display, but a physically
meaningful Isaac Sim model should use a real-scale hoist or a separate
real-scale physics rig with a visual follower. Do not treat the first-pass
coarse collider and compressed hoist mapping as production physics.

For collision authoring, start with a small number of convex or box proxies.
Isaac Sim documents triangle mesh, convex hull, and convex decomposition
options, with fewer convex pieces generally giving better performance. See
the [official collision and rigid-body guidance](https://docs.isaacsim.omniverse.nvidia.com/latest/physics/simulation_fundamentals.html).
