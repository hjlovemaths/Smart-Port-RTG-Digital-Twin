# Collision waiting and inventory projection

Runtime now installs a sampled 2D swept truck envelope before each movement
increment (translation <= 0.25 scene metres). SAT checks cover other internal
and external trucks, including parked trucks. Rotation is sampled every two
degrees and translation every 0.15 metres, with conservative 7.9 x 1.6 envelopes.
Conflicts leave the vehicle stationary and show the blocking truck's identity;
the guard retries on later scheduler ticks. Existing whole-network road locking
and single-real-task sequencing remain enabled. This is not continuous physics,
multi-agent rerouting, deadlock resolution, RTG/spreader/container obstacle
coverage, or a certified collision-prevention system.

`iecs.inventory_preview()` reads the live inventory and returns a dry-run
candidate with the source file SHA-256. It checks top-of-stack indices and real
source container identity, rejects occupied targets, and applies completed
movements to a copied stacks map. Incoming demo IDs are explicitly marked as
demo proposals, never treated as verified real container numbers.

The live inventory is never overwritten. Candidate anchors, statistics,
completed-task reconciliation, durable transaction commits, stale-file checks
at commit time, and synchronized USD regeneration are not yet implemented.
The returned requires_review list makes these limitations explicit. No Commit
button is exposed. Production reconciliation must be completed and tested
before enabling writes. Reset restores visuals without changing production data.

Tests: `tests/test_iecs_safety_inventory.py` and the real-equipment five-flow
integration tests. Production data and running Kit tasks remain unchanged by
these offline checks.
