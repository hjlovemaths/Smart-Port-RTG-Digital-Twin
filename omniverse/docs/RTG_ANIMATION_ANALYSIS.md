# RTG Animation Analysis

Source: `blender/RTG_Model.blend`
Timeline: frames 1-230 at 24 fps

## Motion hierarchy

```text
ANIM_CTRL_RTG_GANTRY_TRAVEL        (gantry travel / 大车)
└── ANIM_CTRL_RTG_TROLLEY_TRAVEL   (trolley travel / 小车)
    └── ANIM_CTRL_RTG_HOIST_VERTICAL (hoist and spreader / 起升与吊具)
```

The nested hierarchy correctly makes the trolley follow gantry travel and the
hoist/spreader follow both the trolley and gantry.

## Keyed motion

| System | Local axis | Frames | Displacement | Average speed |
|---|---:|---:|---:|---:|
| Hoist/spreader | Z | 1-35 | 0 to -0.45 m | 0.32 m/s |
| Hoist/spreader | Z | 35-70 | -0.45 to +0.85 m | 0.89 m/s |
| Trolley | X | 90-150 | 0 to -2.25 m | 0.90 m/s |
| Gantry | Y | 160-230 | 0 to +4.20 m | 1.44 m/s |

The transform curves use Bezier interpolation, so the table gives average,
not instantaneous, speeds. The modeled load remains visible through frame 155
and is hidden from frame 160 onward using constant visibility keys.

## Components following each controller

### Gantry travel controller

1,969 direct visual components plus the nested trolley controller. The major
groups are:

- portal frame, columns, girders, braces, cross beams and rail beds;
- wheel bogies, tires, axles, drive housings, motors and rail equipment;
- ladders, stairs, platforms, walkways and guardrails;
- power/electrical rooms, cabinets and louvers;
- festoon cables, cable loops, clamps and service details;
- lights, signs and structural detailing.

### Trolley travel controller

1,651 direct visual components plus the nested hoist controller. The major
groups are:

- trolley frame, machinery deck and trolley wheels;
- trolley drive motors and wheel housings;
- hoist drums, rope grooves and upper sheaves;
- operator cab, access platforms and railings;
- electrical cabinets, festoon cables and cable runs;
- machinery covers, maintenance details and lighting.

### Hoist/spreader controller

372 direct visual components. The major groups are:

- spreader main beam, cross ties and end beams;
- four lower sheave groups, rope wraps and pendant links;
- hydraulic hoses and hose runs;
- twistlock pins, corner guides, locking feet and lifting lugs;
- maintenance covers and spreader hardware;
- a 75-object `YLOAD_*` container assembly used by the animation.

## Conversion notes

- Only the three controller transforms and the 75 load-visibility objects are
  animated. Wheels, drums, ropes and sheaves do not have independent rotation,
  winding or length animation.
- The file contains 39 copies of each controller name pattern. Only the
  unsuffixed three-controller chain owns geometry; the other 38 chains contain
  controllers only and should be removed from a production asset.
- The populated controller hierarchy contains multiple visual naming families
  (`CODEx_left_yard_*`, `CODEx_right_yard_*`, and `RTG_*`). It is suitable for
  visual playback but should be reduced to one canonical RTG before adding
  physics.
- Recommended Omniverse mapping: one gantry prismatic joint on local Y, one
  trolley prismatic joint on local X, and one hoist prismatic joint on local Z.
  The spreader and load attachment state should be authored separately from
  visibility animation.
