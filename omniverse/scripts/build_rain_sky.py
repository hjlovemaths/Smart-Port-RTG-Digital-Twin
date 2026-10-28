"""Bake a seamless overcast dome once; no runtime cloud simulation.

Spherical value noise avoids panorama seams and a screen-fixed backplate.
The same texture supplies visible clouds, diffuse light and wet reflections.
"""
from pathlib import Path
import numpy as np
from PIL import Image

OUTPUT = Path(__file__).resolve().parents[1] / 'materials/textures/rain_overcast_sky_360.png'


def cloud_noise(x, y, z, frequency, seed):
    rng = np.random.default_rng(seed)
    lattice = rng.random((64, 64, 64)).astype(np.float32)
    coords = [a * frequency + 24 for a in (x, y, z)]
    cells = [np.floor(a).astype(np.int32) for a in coords]
    weights = [a - b for a, b in zip(coords, cells)]
    weights = [w*w*(3-2*w) for w in weights]
    result = np.zeros_like(x)
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                weight = ((weights[0] if i else 1-weights[0]) *
                          (weights[1] if j else 1-weights[1]) *
                          (weights[2] if k else 1-weights[2]))
                result += weight*lattice[(cells[0]+i)%64, (cells[1]+j)%64, (cells[2]+k)%64]
    return result


def build_sky(width=2048, height=1024):
    longitude, latitude = np.meshgrid(np.linspace(-np.pi, np.pi, width),
                                     np.linspace(np.pi/2, -np.pi/2, height))
    x = (np.cos(latitude)*np.cos(longitude)).astype(np.float32)
    y = (np.cos(latitude)*np.sin(longitude)).astype(np.float32)
    z = np.sin(latitude).astype(np.float32)
    cloud = sum(weight*(cloud_noise(x, y, z*.65, freq, 801+index)-.5)
                for index, (freq, weight) in enumerate(((3, .64), (7, .23), (16, .09), (36, .04))))
    # A luminous horizon under a layered, fully covered cloud ceiling.
    horizon = np.exp(-np.abs(z)*5)
    detail = np.clip(z*4, 0, 1)
    value = .56 + .16*horizon + cloud*.58*detail
    value = np.where(z < 0, .43 + .29*np.exp(z*4), value)
    rgb = value[..., None] * np.array([.965, .985, 1.015])
    return Image.fromarray(np.uint8(np.clip(rgb, 0, 1)*255))


if __name__ == '__main__':
    build_sky().save(OUTPUT)
    print(OUTPUT)
