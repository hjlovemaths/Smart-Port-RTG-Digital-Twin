"""Build seamless multi-scale water normals/roughness and bind them to the sea.

Run --textures with Python + NumPy/Pillow; run without flags with USD Python.
"""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]

def textures():
    import numpy as np
    from PIL import Image
    n=1024
    y,x=np.mgrid[0:n,0:n].astype(np.float64)*(2*np.pi/n)
    rng=np.random.default_rng(230923)
    sx=np.zeros((n,n)); sy=sx.copy(); broad=sx.copy()
    # Integer wave numbers keep the tile exactly periodic. Dominant wind
    # direction plus weaker crossing ripples avoids a regular checkerboard.
    for low,high,count,amplitude in ((2,7,12,.017),(8,24,28,.011),(25,70,40,.004)):
        for _ in range(count):
            k=int(rng.integers(low,high))
            angle=rng.normal(.45,.38)
            kx=int(round(k*np.cos(angle))); ky=int(round(k*np.sin(angle)))
            phase=kx*x+ky*y+rng.uniform(0,2*np.pi)
            slope=amplitude*rng.uniform(.55,1.3)
            sx+=slope*np.cos(phase)*kx/k
            sy+=slope*np.cos(phase)*ky/k
            if low==2:broad+=np.sin(phase)/count
    normal=np.stack((-sx,-sy,np.ones_like(sx)),axis=-1)
    normal/=np.linalg.norm(normal,axis=-1,keepdims=True)
    normal=np.uint8(np.clip((normal*.5+.5)*255,0,255))
    rough=np.uint8(np.clip(.16+.045*broad+.12*np.sqrt(sx*sx+sy*sy),.11,.24)*255)
    directory=ROOT/'materials/textures'
    Image.fromarray(normal).save(directory/'harbor_water_multiscale_normal.png')
    Image.fromarray(rough).save(directory/'harbor_water_multiscale_roughness.png')
    print('Generated 1024px seamless multi-scale normal and roughness maps')

def material():
    from pxr import Gf,Sdf,Usd,UsdShade
    stage=Usd.Stage.Open(str(ROOT/'scenes/environment.usda'))
    base='/Environment/Looks/HarborWater'
    surface=UsdShade.Shader(stage.GetPrimAtPath(base+'/PreviewSurface'))
    surface.GetInput('diffuseColor').Set(Gf.Vec3f(.007,.048,.064))
    surface.GetInput('clearcoat').Set(.05)
    surface.GetInput('clearcoatRoughness').Set(.14)
    normal=UsdShade.Shader(stage.GetPrimAtPath(base+'/NormalTexture'))
    normal.GetInput('file').Set(Sdf.AssetPath('../materials/textures/harbor_water_multiscale_normal.png'))
    uv=UsdShade.Shader(stage.GetPrimAtPath(base+'/UVTransform'))
    uv.GetInput('scale').Set(Gf.Vec2f(145,145))
    uv.CreateInput('translation',Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(0))
    rough=UsdShade.Shader.Define(stage,base+'/RoughnessTexture')
    rough.CreateIdAttr('UsdUVTexture')
    rough.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('../materials/textures/harbor_water_multiscale_roughness.png'))
    rough.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('raw')
    for axis in ('S','T'):rough.CreateInput('wrap'+axis,Sdf.ValueTypeNames.Token).Set('repeat')
    rough.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result')
    rough.CreateOutput('r',Sdf.ValueTypeNames.Float)
    surface.GetInput('roughness').ConnectToSource(rough.ConnectableAPI(),'r')
    stage.GetRootLayer().Save()
    print('Updated HarborWater material, retaining dielectric IOR and existing binding')

if __name__=='__main__':
    textures() if '--textures' in sys.argv else material()
