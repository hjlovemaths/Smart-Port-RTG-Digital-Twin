# STS_01 ship-to-truck task

Reload `sts_control` and call `show_sts_controls()` in the Kit Script Editor.
Click **STS_01 -> Truck -> 2C004** in the existing crane panel.

The task uses the nearest ship's `Model/ShipContainer179`: bow-end bay,
shore-side column, top tier. The receiving truck references the same
`LIVE_internal_yard_truck_unified_40ft` model used by the yard animation.
It approaches along the quay for 22 scene units in the corrected +Y direction,
with the cab facing forward, and parks beneath STS_01. Once loaded, it drives
to causeway 02 (Y=126.05), crosses to the yard approach road, turns into the
primary RTG truck lane (X=-3.75), and stops at the target bay. Rounded corners
rotate both truck and load. The target is the user-approved 40 ft paired bay
2C004, occupying 2C003+005, row 1 / tier 1 (X=6.433333, Y=135, bottom Z=.12).

The sequence is truck arrival, raise clear, align, lower, lock, lift,
transfer, lower onto trailer, unlock, raise clear, truck road transport,
RTG approach, RTG pickup, lift, transfer to row 1, lower to tier 1,
release and raise clear. The box follows the active spreader or truck and
stays in the yard after final release. Existing container geometry at the
destination is checked before starting; an occupied slot rejects the task.
This is a
kinematic scene demonstration, not PLC lock feedback or physical grasping.

**Pause / Resume task** preserves progress. **STOP ALL** also stops the task.
Resume the crane to continue after STOP ALL. **Reset task** restores the
box and both cranes and removes the dispatched truck. Closing/reloading the panel
also resets the task. Changes live only in an anonymous session layer;
source ship, yard truck and scene files are not rewritten by running it.

The task pauses timeline playback to prevent concurrent animation. Keep the
timeline paused while running; frame updates drive the crane task. Manual
movement and spreader-size commands for STS_01 require resetting the task.

Verification: run `tmp/test_sts_unload_task.py` with `tmp/run_sts_usd_tool.py`.
It checks the corrected truck heading, both crane attachments, trailer alignment
through road turns, final 2C004 row1/tier1 placement, pause and reset.
Use **Focus task vehicle / crane** to frame the truck in transit or the RTG
during yard handling. The whole default demonstration takes about 125 seconds.
