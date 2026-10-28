# Fog environment

The Fog button and existing fog amount control use three grades. Medium is the
default. The 2026-10-20-depth-fog-v2 revision uses RTX Simple Fog with distance
attenuation and a small height component. It does not add fullscreen fog meshes
or volumetric ray marching.

| Grade | Start distance | Fade endpoint | Distance density | Height density |
| --- | ---: | ---: | ---: | ---: |
| Light | 25.6 m | 400 m | 2.30 | 0.009 |
| Medium | 19.2 m | 300 m | 7.36 | 0.018 |
| Heavy | 12.8 m | 200 m | 16.56 | 0.027 |

These are artistic rendering parameters, not measured meteorological visibility.
The overcast dome supplies illumination and reflections. A pale constant sky
reduces the horizon seam; sun intensity decreases with fog grade. Nearby street
lamps and the working RTG lights remain active, with the existing local light
budget. Rain geometry is hidden in fog mode.

Weather attributes are authored in the session layer. Modern viewports use their
RenderProduct fog/background attributes directly. Legacy global carb aliases are
only a fallback when no RenderProduct exists: writing both lets Kit asynchronously
copy settings into the stage root. The correct fog start attribute is
`omni:rtx:fog:start:distance`.

Run `omniverse/scripts/verify_fog_realism.py` in Kit's Script Editor with
`runpy.run_path(..., run_name='__main__')`. It reloads the weather runtime, samples
each grade at a fixed camera/resolution, captures screenshots, checks day-mode
fog disable and root-layer stability, then leaves medium fog active. It never
saves the stage. Results are in `outputs/fog_realism/performance.json`.

FPS results distinguish native viewport FPS from displayed FPS calculated with
Kit's reported frame generation multiplier. Fixed-view results do not guarantee
the same FPS at other resolutions or camera angles. An existing unrelated HUD
test expects seven emissive materials while the receiver currently contains eight;
exclude `test_rtg_twin_hud_does_not_duplicate_environment_buttons` when running
the targeted weather regression suite until that separate discrepancy is resolved.
