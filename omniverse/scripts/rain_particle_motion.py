"""Vectorized rain motion: each drop reaches the road before it wraps.

Keep the authored crossed ribbons and fixed topology. Only one packed point
buffer per visible batch changes; there are no per-drop USD prims/collisions.
"""
import numpy as np


class RainPointBatch:
    def __init__(self, points, phase=0.0, surface_z=.13, ceiling_z=28.0):
        self.base = np.asarray(points, dtype=np.float32).reshape(-1, 8, 3).copy()
        self.points = self.base.copy()
        self.surface_z = surface_z
        self.height = ceiling_z - surface_z
        # Each ribbon pair is one drop. Seed its phase from the original
        # emitter height, spreading all drops over the whole rain column.
        top = self.base[:, 0, 2]
        self.phase = np.remainder((top - 20.0)/8.0 + phase, 1.0)*self.height
        self.local_z = self.base[:, :, 2] - top[:, None]
        self.length = -self.local_z.min(axis=1)
        self.landing_z = np.full(len(top), surface_z, dtype=np.float32)
        self.landing_points = np.zeros((len(top), 3), dtype=np.float32)
        self.previous_cycles = None
        self.contacts = np.empty((0, 3), dtype=np.float32)
        self.surface_key = None

    def set_surfaces(self, roofs, anchor=(0., 0.), wind_speed=4.5):
        """Intersect each slanted trajectory with the highest horizontal roof.

        roofs contain world-space xmin,ymin,xmax,ymax,z. This only runs when
        the anchor, wind or container layout changes, never once per frame.
        """
        self.landing_z.fill(self.surface_z)
        head_xy = (self.base[:, 2, :2] + self.base[:, 3, :2])*.5
        wind = np.array([.035, .011], dtype=np.float32)*wind_speed
        for xmin, ymin, xmax, ymax, z in roofs:
            travel = self.height + self.surface_z - z - self.length
            xy = head_xy + travel[:, None]*wind + anchor
            hit = ((xy[:, 0] >= xmin) & (xy[:, 0] <= xmax)
                   & (xy[:, 1] >= ymin) & (xy[:, 1] <= ymax)
                   & (z > self.landing_z) & (travel >= 0))
            self.landing_z[hit] = z
        travel = self.height + self.surface_z - self.landing_z - self.length
        self.landing_points[:, :2] = head_xy + travel[:, None]*wind
        self.landing_points[:, 2] = self.landing_z
        # Changing surfaces must not create a burst of retrospective impacts.
        self.previous_cycles = None

    def update(self, fall_travel, wind_speed):
        tail_height = np.remainder(self.phase - fall_travel, self.height)
        fallen = self.height - tail_height
        stop_travel = self.height + self.surface_z - self.landing_z - self.length
        drift = np.minimum(fallen, stop_travel)
        self.points[:, :, 0] = self.base[:, :, 0] + (drift*wind_speed*.035)[:, None]
        self.points[:, :, 1] = self.base[:, :, 1] + (drift*wind_speed*.011)[:, None]
        # Clip the leading end at the road while the tail finishes falling.
        # A drop only respawns when its *entire* streak has reached the road.
        np.maximum(tail_height[:, None] + self.local_z + self.surface_z,
                   self.landing_z[:, None], out=self.points[:, :, 2])
        consumed = tail_height + self.surface_z <= self.landing_z
        self.points[consumed] = self.landing_points[consumed, None, :]
        threshold = self.landing_z - self.surface_z + self.length
        cycles = np.floor((fall_travel - self.phase + threshold)/self.height)
        hit = (cycles > self.previous_cycles) if self.previous_cycles is not None else np.zeros(len(cycles), dtype=bool)
        self.contacts = self.landing_points[hit].copy()
        self.previous_cycles = cycles
        return self.points.reshape(-1, 3)


class RainSplashPool:
    """Fixed-size pooled ripples and ballistic spray, shared by road and roofs."""
    CAPACITY = 128
    LIFETIME = .28
    VERTICES = 36  # Six ring quads and four three-vertex droplets (16 triangles).

    def __init__(self):
        self.born = np.full(self.CAPACITY, -1e9)
        self.centers = np.zeros((self.CAPACITY, 3), dtype=np.float32)
        self.scales = np.ones(self.CAPACITY, dtype=np.float32)
        self.points = np.zeros((self.CAPACITY, self.VERTICES, 3), dtype=np.float32)
        self.cursor = 0
        self.total_ground = self.total_roof = 0
        angles = np.arange(7)*np.pi/3
        self.ring = np.array([(np.cos(a), np.sin(a)) for i in range(6)
                              for a in (angles[i], angles[i+1], angles[i+1], angles[i])], dtype=np.float32)
        self.outer = np.tile([0., 0., 1., 1.], 6).astype(np.float32)
        angles = np.arange(4)*np.pi/2 + .3
        self.spray = np.column_stack((np.cos(angles), np.sin(angles))).astype(np.float32)

    def spawn(self, positions, now, scale=1.):
        if not len(positions):
            return
        positions = np.asarray(positions, dtype=np.float32)[-self.CAPACITY:]
        slots = (self.cursor + np.arange(len(positions))) % self.CAPACITY
        self.centers[slots] = positions
        self.born[slots] = now
        self.scales[slots] = scale
        self.cursor = (self.cursor + len(positions)) % self.CAPACITY
        self.total_roof += int(np.count_nonzero(positions[:, 2] > .5))
        self.total_ground += int(np.count_nonzero(positions[:, 2] <= .5))

    def update(self, now):
        age = np.clip(now-self.born, 0., self.LIFETIME)
        t = (age/self.LIFETIME).astype(np.float32)
        active = (t < 1).astype(np.float32)
        radius = (.014 + .115*t)*active
        width = .009*(1-t)*active
        self.points[:] = self.centers[:, None, :]
        self.points[:, :24, :2] += self.ring[None, :, :]*(radius[:, None, None]+self.outer[None, :, None]*width[:, None, None])
        self.points[:, :24, 2] += .008*active[:, None]
        spray = self.points[:, 24:, :].reshape(self.CAPACITY, 4, 3, 3)
        spray[:, :, :, :2] += self.spray[None, :, None, :]*(radius[:, None, None, None]*.8)
        h = np.maximum(0., 1.25*age - 4.905*age*age)*active
        spray[:, :, :, 2] += (h + .008*active)[:, None, None]
        spray[:, :, 0, 0] -= width[:, None]*.6
        spray[:, :, 1, 0] += width[:, None]*.6
        spray[:, :, 2, 2] += .026*(1-t[:, None])*active[:, None]
        self.points[:] = self.centers[:, None, :] + (self.points-self.centers[:, None, :])*self.scales[:, None, None]
        return self.points.reshape(-1, 3)

    @classmethod
    def topology(cls):
        counts = [4]*6 + [3]*4
        return counts*cls.CAPACITY, list(range(cls.CAPACITY*cls.VERTICES))
