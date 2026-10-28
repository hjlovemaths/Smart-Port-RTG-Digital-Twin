"""Offline USD integration checks; no Kit window or renderer is required."""
from pathlib import Path
import sys
import unittest
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'omniverse/scripts'))
try:
    from pxr import Sdf, Usd, UsdGeom, UsdLux, UsdShade
except ImportError:
    raise unittest.SkipTest("Requires the Kit Python USD bindings")

import harbor_daylight as harbor
import smart_port_environment as weather
import smart_port_time_of_day as tod


class DaylightUSDTests(unittest.TestCase):
    def test_material_revision_keeps_previous_module_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'omniverse/materials/HarborSky.mdl'
            source.parent.mkdir(parents=True)
            source.write_text('first revision', encoding='utf-8')
            first = Path(harbor._cached_sky_asset(str(source), 1))
            source.write_text('second revision', encoding='utf-8')
            second = Path(harbor._cached_sky_asset(str(source), 2))
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(), 'first revision')
            self.assertEqual(second.read_text(), 'second revision')

    def setUp(self):
        self.stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(self.stage, UsdGeom.Tokens.z)
        root = self.stage.DefinePrim(weather.ENVIRONMENT_ROOT_PATH, 'Xform')
        root.CreateAttribute(weather.ENVIRONMENT_MODE_ATTRIBUTE, Sdf.ValueTypeNames.Token).Set('day')
        UsdLux.DomeLight.Define(self.stage, weather.DAY_SKY_PATH)
        UsdLux.DistantLight.Define(self.stage, weather.DAY_SUN_PATH)
        for path in (weather.NIGHT_STREET_LIGHTS_PATH,
                     *weather.RTG_LED_MODEL_ROOT_PATHS, *weather.RTG_LED_NIGHT_ROOT_PATHS):
            self.stage.DefinePrim(path, 'Xform')
        self.lamp = UsdLux.SphereLight.Define(self.stage, weather.NIGHT_STREET_LIGHTS_PATH+'/Lamp')
        self.lamp.CreateIntensityAttr(1000.)
        self.stage.DefinePrim('/Render/Viewport', 'RenderProduct')
        self.before = self.stage.GetRootLayer().ExportToString()

    def tearDown(self):
        self.assertEqual(self.before, self.stage.GetRootLayer().ExportToString())

    def test_clear_sky_keeps_binding_and_updates_uniforms(self):
        for hour in (12, 6.5, 18.5, 21+5/60, 0, 24):
            profile = tod.apply_time(self.stage, hour, configure=False)
            dome = self.stage.GetPrimAtPath(weather.DAY_SKY_PATH)
            bound, _ = UsdShade.MaterialBindingAPI(dome).ComputeBoundMaterial()
            self.assertEqual(str(bound.GetPath()), harbor.SKY_MATERIAL)
            self.assertEqual(dome.GetAttribute('inputs:texture:file').Get().path, '')
            shader = UsdShade.Shader(self.stage.GetPrimAtPath(harbor.SKY_MATERIAL+'/Shader'))
            self.assertAlmostEqual(shader.GetInput('daylight').Get(), profile['sky_daylight'], places=5)
            self.assertAlmostEqual(shader.GetInput('cloud_phase').Get(), (hour % 24) / 24 * 6.283185307179586, places=5)
            asset = shader.GetSourceAsset('mdl')
            self.assertTrue(Path(asset.path).is_file())
            self.assertEqual(shader.GetSourceAssetSubIdentifier('mdl'), 'HarborSky')

    def test_21_hour_uses_night_shader_without_double_dimming(self):
        tod.apply_time(self.stage, 12, configure=False)
        dome = self.stage.GetPrimAtPath(weather.DAY_SKY_PATH)
        noon_power = dome.GetAttribute('inputs:intensity').Get()
        for hour in (21, 21+5/60):
            tod.apply_time(self.stage, hour, configure=False)
            self.assertEqual(dome.GetAttribute('inputs:intensity').Get(), noon_power)
            shader = UsdShade.Shader(self.stage.GetPrimAtPath(harbor.SKY_MATERIAL+'/Shader'))
            self.assertEqual(shader.GetInput('daylight').Get(), 0)
            self.assertLess(shader.GetInput('twilight').Get(), .001)
            sun = self.stage.GetPrimAtPath(weather.DAY_SUN_PATH)
            self.assertEqual(sun.GetAttribute('inputs:intensity').Get(), 0)
            self.assertAlmostEqual(self.lamp.GetIntensityAttr().Get(), 700.)
            lamps = self.stage.GetPrimAtPath(weather.NIGHT_STREET_LIGHTS_PATH)
            self.assertEqual(lamps.GetAttribute('visibility').Get(), 'inherited')

    def test_weather_releases_procedural_sky_and_restores_lamp_power(self):
        tod.apply_time(self.stage, 0, configure=False)
        self.assertAlmostEqual(self.lamp.GetIntensityAttr().Get(), 700.)
        for mode in ('rain', 'fog'):
            with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                root = self.stage.GetPrimAtPath(weather.ENVIRONMENT_ROOT_PATH)
                weather._set_attribute(root, weather.ENVIRONMENT_MODE_ATTRIBUTE, mode, 'Token')
                weather._configure_viewport_background(self.stage, weather.ENVIRONMENT_PRESETS[mode])
            tod.apply_time(self.stage, 12, configure=False)
            dome = self.stage.GetPrimAtPath(weather.DAY_SKY_PATH)
            self.assertEqual(UsdShade.MaterialBindingAPI(dome).GetDirectBindingRel().GetTargets(), [])
            self.assertEqual(Path(dome.GetAttribute('inputs:texture:file').Get().path), weather.RAIN_SKY_TEXTURE)
            self.assertEqual(self.lamp.GetIntensityAttr().Get(), 1000.)
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            root = self.stage.GetPrimAtPath(weather.ENVIRONMENT_ROOT_PATH)
            weather._set_attribute(root, weather.ENVIRONMENT_MODE_ATTRIBUTE, 'day', 'Token')
        tod.apply_time(self.stage, 0, configure=False)
        bound, _ = UsdShade.MaterialBindingAPI(dome).ComputeBoundMaterial()
        self.assertEqual(str(bound.GetPath()), harbor.SKY_MATERIAL)
        self.assertAlmostEqual(self.lamp.GetIntensityAttr().Get(), 700.)

    def test_repeated_time_changes_do_not_add_geometry_or_dim_lamps(self):
        tod.apply_time(self.stage, 0, configure=False)
        prims = len(list(self.stage.Traverse()))
        for hour in (12, 6, 18, 0, 0):
            tod.apply_time(self.stage, hour, configure=False)
        self.assertEqual(len(list(self.stage.Traverse())), prims)
        self.assertEqual(len(UsdGeom.Mesh(self.stage.GetPrimAtPath(harbor.OCEAN)).GetPointsAttr().Get()), 4)
        self.assertAlmostEqual(self.lamp.GetIntensityAttr().Get(), 700.)
        product = self.stage.GetPrimAtPath('/Render/Viewport')
        self.assertEqual(product.GetAttribute('omni:rtx:lights:visibilityOverride').Get(), 'perLight')
        self.assertEqual(product.GetAttribute('omni:rtx:lights:dome:bakingResolution').Get(), 1024)

    def test_clear_lamp_calibration_replaces_stale_weather_exposure(self):
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            self.lamp.GetPrim().CreateAttribute('smartPort:clearNightExposure', Sdf.ValueTypeNames.Float).Set(1.4)
            self.lamp.CreateExposureAttr(2.2)
        tod.apply_time(self.stage, 3, configure=False)
        self.assertAlmostEqual(self.lamp.GetExposureAttr().Get(), 1.4, places=5)
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            weather._set_attribute(self.stage.GetPrimAtPath(weather.ENVIRONMENT_ROOT_PATH), weather.ENVIRONMENT_MODE_ATTRIBUTE, 'rain', 'Token')
            self.lamp.GetExposureAttr().Set(-.8)
        tod.apply_time(self.stage, 3, configure=False)
        self.assertAlmostEqual(self.lamp.GetExposureAttr().Get(), -.8, places=5)


if __name__ == '__main__':
    unittest.main()
