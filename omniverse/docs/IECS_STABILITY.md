# IECS entity and scene checks

The dispatch container record is the authority for position, heading, location,
owner and version. Each bound container records one `source_prim`, one
`visual_prim`, a fixed `visual_size` and a declared `visual_asset`.
The source and its yard detail siblings stay hidden until reset. A single
referenced detailed yard asset follows the record. This is an adapter-based
entity consolidation, not a rewrite of production inventory or original cranes.

Slot selection includes landed dispatch records as well as visible inventory:
hiding a logical source no longer makes the delivered slot appear empty.

`services/iecs_geometry.py` owns common visual calibration constants. These are
scene-scale values, not real-world container engineering measurements. Existing
layout builders retain their separate engineering/visual coordinate mappings.

`iecs.check_scene()` returns a read-only diagnostic report; `/state` exposes it
as `scene_checks`. The panel lists failures without silently hiding them or
altering scene data. Checks cover missing/hidden/empty bay markings, unique
visual paths, source duplicates, cargo visibility and position synchronization.
They do not prove swept-volume collision safety or production inventory integrity.

Validation scripts: `verify_iecs_ground_flows.py`, `verify_iecs_load.py`,
`verify_iecs_scene.py`, `verify_iecs_scene_checks.py`.
Reset removes only the anonymous demonstration layer, restoring original models.
