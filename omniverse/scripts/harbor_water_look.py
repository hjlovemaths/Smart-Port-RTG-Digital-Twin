"""Shared static harbor-water appearance, independent of animation."""
from pathlib import Path
from pxr import Gf, Sdf, UsdShade

WATER='/World/Environment/Looks/HarborWater'
TEXTURES=Path(__file__).resolve().parents[1]/'materials/textures'


def author_harbor_water_look(stage, absolute_assets=False):
    """Author the same textured look for static and animated water."""
    for name,file in (
        ('NormalTexture','harbor_water_multiscale_normal.png'),
        ('RoughnessTexture','harbor_water_multiscale_roughness.png'),
    ):
        shader=UsdShade.Shader(stage.OverridePrim(WATER+'/'+name))
        path=(TEXTURES/file).as_posix() if absolute_assets else '../materials/textures/'+file
        shader.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(path))
    normal=UsdShade.Shader(stage.GetPrimAtPath(WATER+'/NormalTexture'))
    normal.CreateInput('scale',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(3.4,3.4,2,1))
    normal.CreateInput('bias',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1.7,-1.7,-1,0))
    uv=UsdShade.Shader(stage.OverridePrim(WATER+'/UVTransform'))
    uv.CreateInput('scale',Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(190))
