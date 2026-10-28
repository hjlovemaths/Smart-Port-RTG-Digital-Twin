# Smart Port Agent Panel

This Kit extension adds a dockable chat window that talks to an OpenAI-compatible
Chat Completions API. The model can answer questions and call a small allowlist
of tools for reading the active stage, changing its environment/time, and moving
the primary RTG in the USD scene.

The panel sends requests directly from Kit to the configured API endpoint. API
keys are entered in the panel, masked, kept in memory only, and never written to
the project or Kit settings. Local loopback endpoints may omit a key. Remote endpoints should use HTTPS. RTG movement
tools edit the current USD session only; they do not send commands to a PLC.

## Enable

The Smart Port Composer app and `omniverse/scripts/launch_isaac_sim_project.py`
load this extension automatically. The **Smart Port Agent** panel opens after
the startup layout is ready. If you close it, reopen it from
**Window > Smart Port Agent**.

For another Kit app, enable it manually:

Add `omniverse/extensions` to Kit's extension search paths, then enable
`smartport.agent_panel` in **Window > Extensions**. For a Kit executable, the
equivalent command-line options are:

```powershell
kit.exe --ext-folder "D:\path\to\Smart Port RTG Digital Twin\omniverse\extensions" --enable smartport.agent_panel
```

The current stage should be `omniverse/scenes/smart_port.usda` for scene tools
to work. Configure the API URL and model in the panel. Paste the API key into
the masked key field; it is not persisted when Kit closes.

## DeepSeek configuration

The panel keeps English labels and replies in Simplified Chinese by default,
unless the user explicitly requests another language. Questions and replies
use Kit's startup font atlas, with Windows SimHei and the bundled Chinese glyph
list merged into it. Do not override the conversation widgets' font file: Kit
does not include the custom CJK glyph range in per-widget font atlases.
Restart the app once after this update; enabling/reloading the extension in an
already running app does not rebuild the renderer's startup font atlas.
API requests and replies retain UTF-8.
It defaults to the official DeepSeek API:

- Chat Completions URL: `https://api.deepseek.com/chat/completions`
- Model: `deepseek-flash`
- API Key: your DeepSeek API key, entered in the masked field.

You can also set `DEEPSEEK_API_KEY` before starting Kit. The generic
`SMARTPORT_AGENT_API_KEY` environment variable takes precedence. API keys stay
in memory. The URL and model fields remain editable for other compatible
services or DeepSeek models.

See the [DeepSeek API quick start](https://api-docs.deepseek.com/) for current
model names and API setup.

## Available tools

The panel exposes 37 runtime interfaces, including viewport presets and camera
following, smooth RTG motion, all seven STS cranes, current task queries,
simulation and IECS workflows, sea motion, camera/info panels, slot picking,
physics, LiDAR and local streaming. Read the
[complete interface catalog and examples](../../../docs/AGENT_INTERFACES.md)
or ask the Agent to list its capabilities. The exact tool schemas are in
[`agent_interfaces.json`](agent_interfaces.json).

- Read active stage, environment, time-of-day, and primary RTG axis positions.
- Read a bay's six-row box area map, tier occupancy, container numbers, sizes,
  and the 40 ft stack covering a requested 20 ft bay.
- Read yard/block inventory totals and occupied bay maps.
- Set day, night, rain, or fog; set time from 0 to 24 hours.
- Adjust wind, rain grade, and fog grade.
- Move the primary RTG to a yard bay, or set one or more axes using soft limits.

Motion commands report that movement has started; use equipment or operation
status to confirm arrival. Shared task ownership and the STS boom interlock
remain enforced. RTX sensing requires the Isaac sensor runtime.

The extension does not expose arbitrary Python execution, file access, or PLC
write operations to the model.

## Under-trolley wide view

Ask: `Show a wide straight-down view from below the RTG trolley platform, following the trolley.`
The Agent calls `set_viewport_view(target="trolley", view="under_trolley")`.
This camera sits below the trolley deck, points vertically down at the spreader
and container tops, and follows gantry/trolley travel without descending with
the hoist. Its default horizontal field of view is 110 degrees. Ask
`Keep the under-trolley view and widen its field of view to 130 degrees.`
to see more; the allowed range is 30 to 140 degrees. No equipment motion or
control-mode change is needed, including while the live TwinState driver runs.

## Inventory questions

After updating the extension, restart the app to load the new tools. Ask in the
chat box and click **Send**, for example:

- `What is the box area map on 4C41 Bay?`
- `Show the row heights and containers in 4C/040.`
- `How many containers are recorded in block 4C?`

`get_bay_inventory` accepts compact IDs such as `4C41` and returns the requested
bay, matching logical bay IDs, the six-digit `bay_map` (R1 to R6, right to left),
container rows/tiers, and an ASCII occupancy layout. A 40 ft logical bay such as
`4C/040` covers physical bays `4C/039` and `4C/041`; a query for either physical
bay reports the covering stack. A query for a 40 ft bay can also report its two
separate 20 ft stacks.

Queries use the active driver's inventory when it belongs to the current stage;
otherwise they read the inventory selected in the stage metadata, falling back
to the project's live JSON cache when no source is selected. Results identify
the source mode, snapshot timestamp, and completeness. Missing selected files
are reported as errors; queries never substitute simulation data for a missing
live source. Unrecorded bays in a partial snapshot are reported as unknown.
These queries do not reload inventory, modify USD, move the RTG, or send PLC commands.
