"""Lightweight analytic sky and distant water; no animated volumetric clouds."""
from pathlib import Path
import hashlib
from functools import lru_cache

SKY_MATERIAL = '/World/Environment/Looks/HarborProceduralSky'
SKY_LIGHT = '/World/Environment/ProceduralDome'
OCEAN = '/World/Environment/DistantOcean'
SEA = '/World/PortAndRTG/CODEx_showcase_inside_circular_sea_surface/CODEx_showcase_inside_circular_sea_surface_mesh_001'


@lru_cache(maxsize=8)
def _cached_sky_asset(source_path, modified_ns):
    # Kit's live MDL reload can invalidate the imported analytic-sky function.
    # Give each source revision an immutable module path, so it is compiled
    # fresh rather than replacing a module referenced by an existing dome.
    source = Path(source_path)
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()[:16]
    target = source.parents[2] / 'outputs/daylight_realism/mdl_cache' / f'HarborSky_{digest}.mdl'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    return target.as_posix()


def lamp_scale(daylight, clear=True):
    """Ease through dusk; weather presets retain their authored lamp power."""
    fraction = max(0., min(1., 1. - daylight / .65))
    return .7 * fraction * fraction * (3. - 2. * fraction) if clear else 1.


def _fade_lamps(stage, profile, clear):
    from pxr import Usd, Sdf
    import smart_port_environment as w
    scale = lamp_scale(profile['daylight'], clear)
    for path in (w.NIGHT_STREET_LIGHTS_PATH, *w.RTG_LED_NIGHT_ROOT_PATHS, *w.sts_night_light_paths(stage)):
        root = stage.GetPrimAtPath(path)
        if not root:
            continue
        for prim in Usd.PrimRange(root):
            if not prim.GetTypeName().endswith('Light'):
                continue
            # Clear-weather calibration must win over cached weather-session
            # exposure restored when switching out of rain.
            clear_exposure = prim.GetAttribute('smartPort:clearNightExposure')
            if clear and clear_exposure and clear_exposure.Get() is not None:
                prim.GetAttribute('inputs:exposure').Set(clear_exposure.Get())
            intensity = prim.GetAttribute('inputs:intensity')
            if not intensity or intensity.Get() is None:
                continue
            base = prim.GetAttribute('smartPort:unscaledLampIntensity')
            if not base:
                base = prim.CreateAttribute('smartPort:unscaledLampIntensity', Sdf.ValueTypeNames.Float)
                base.Set(intensity.Get())
            target = base.Get() * scale
            if intensity.Get() != target:
                intensity.Set(target)


def configure(stage, profile, enabled):
    from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt
    import smart_port_environment as w
    dome = stage.GetPrimAtPath(w.DAY_SKY_PATH)
    binding = UsdShade.MaterialBindingAPI.Apply(dome)
    _fade_lamps(stage, profile, enabled)
    if not enabled:
        binding.GetDirectBindingRel().SetTargets([])
        other = stage.GetPrimAtPath(SKY_LIGHT)
        if other:
            w._set_attribute(other, 'visibility', 'invisible', 'Token')
        return
    other = stage.GetPrimAtPath(SKY_LIGHT)
    if other:
        w._set_attribute(other, 'visibility', 'invisible', 'Token')
    w._set_attribute(dome, 'visibility', 'inherited', 'Token')
    w._set_attribute(dome, 'xformOpOrder', ['xformOp:rotateXYZ'], 'TokenArray')
    source = Path(__file__).resolve().parents[1]/'materials/HarborSky.mdl'
    asset_path = _cached_sky_asset(str(source), source.stat().st_mtime_ns)
    if not stage.GetPrimAtPath(SKY_MATERIAL):
        material = UsdShade.Material.Define(stage, SKY_MATERIAL)
        shader = UsdShade.Shader.Define(stage, SKY_MATERIAL+'/Shader')
        shader.CreateImplementationSourceAttr('sourceAsset')
        shader.SetSourceAsset(Sdf.AssetPath(asset_path), 'mdl')
        shader.SetSourceAssetSubIdentifier('HarborSky', 'mdl')
        shader.CreateOutput('out', Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput('mdl').ConnectToSource(shader.ConnectableAPI(), 'out')
    shader = UsdShade.Shader(stage.GetPrimAtPath(SKY_MATERIAL+'/Shader'))
    if shader.GetSourceAsset('mdl').path != asset_path:
        shader.SetSourceAsset(Sdf.AssetPath(asset_path), 'mdl')
    for name, val in (('elevation', profile['elevation']), ('azimuth', profile['rotation'][2]),
                      ('twilight', profile['twilight']), ('daylight', profile['sky_daylight']),
                      ('cloud_phase', (profile['hour'] % 24.) / 24. * 6.283185307179586)):
        shader.CreateInput(name, Sdf.ValueTypeNames.Float).Set(val)
    binding.Bind(UsdShade.Material(stage.GetPrimAtPath(SKY_MATERIAL)))
    w._set_attribute(dome, 'light:materialSyncMode', 'noMaterialResponse', 'Token')
    # MDL supplies the baked dome radiance. A simultaneously authored texture
    # can override that bake. The shader already converts Z-up into Y-up.
    w._set_attribute(dome, 'inputs:texture:file', '', 'Asset')
    w._set_attribute(dome, 'xformOp:rotateXYZ', (0., 0., 0.), 'Float3')
    w._set_attribute(dome, 'inputs:color', (1.,1.,1.), 'Color3f')
    # The MDL already attenuates radiance through dusk/night. Multiplying by
    # the texture-fallback curve a second time crushed the sky to black.
    w._set_attribute(dome, 'inputs:intensity', 400., 'Float')
    normal = stage.GetPrimAtPath('/World/Environment/Looks/HarborWater/NormalTexture')
    if normal:
        w._set_attribute(normal, 'inputs:scale', (.35,.35,2.,1.), 'Float4')
        w._set_attribute(normal, 'inputs:bias', (-.175,-.175,-1.,0.), 'Float4')
    if not stage.GetPrimAtPath(OCEAN):
        # Extend below the existing detailed harbor mesh, avoiding its finite
        # circular edge. This is a single quad with calm distant-water shading.
        sea = stage.GetPrimAtPath(SEA)
        z = -5.15
        if sea:
            box = UsdGeom.BBoxCache(0, ['default','render']).ComputeWorldBound(sea).ComputeAlignedRange()
            z = float(box.GetMin()[2]) - .25
        mesh = UsdGeom.Mesh.Define(stage, OCEAN)
        r = 40000.
        mesh.CreatePointsAttr(Vt.Vec3fArray([(-r,-r,z),(r,-r,z),(r,r,z),(-r,r,z)]))
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0,1,2,3])
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateDoubleSidedAttr(True)
        st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex')
        st.Set(Vt.Vec2fArray([(-40,-40),(40,-40),(40,40),(-40,40)]))
    # Unresolved distant wavelets should not retain the near-water normal
    # amplitude. Grazing-angle Fresnel reflection merges with the sky horizon.
    water_path = '/World/Environment/Looks/DistantWater'
    if not stage.GetPrimAtPath(water_path):
        water = UsdShade.Material.Define(stage, water_path)
        surface = UsdShade.Shader.Define(stage, water_path+'/Surface')
        surface.CreateIdAttr('UsdPreviewSurface')
        surface.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.003,.015,.025))
        surface.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.035)
        surface.CreateInput('ior', Sdf.ValueTypeNames.Float).Set(1.333)
        surface.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(0.)
        water.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), 'surface')
        UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(OCEAN)).Bind(water)
    # SimpleFog also obscures the entire sky. The analytic sky provides its
    # own horizon haze; keep volumetric fog reserved for weather presets.
    for prim in stage.Traverse():
        if prim.GetTypeName() == 'RenderProduct':
            # Discard the session-only mesh-light diagnostic override from
            # earlier previews; retain the scene's authored rendering policy.
            w._clear_attribute(prim, 'omni:rtx:meshlights:enabled')
            w._set_attribute(prim, 'omni:rtx:lights:visibilityOverride', 'perLight', 'Token')
            w._set_attribute(prim, 'omni:rtx:background:source:type', 'domeLight', 'Token')
            w._set_attribute(prim, 'omni:rtx:lights:dome:bakingResolution', 1024, 'Int')
            w._set_attribute(prim, 'omni:rtx:domeLight:baking:spp', 1, 'Int')
            w._set_attribute(prim, 'omni:rtx:fog:enabled', False, 'Bool')
