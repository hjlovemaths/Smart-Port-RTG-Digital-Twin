import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
from build_weather_effects import rain_streak_quads
from rain_particle_motion import RainPointBatch, RainSplashPool


def test_every_drop_reaches_road_before_respawning_without_group_reset():
    points = np.asarray(rain_streak_quads()[:40], dtype=np.float32).reshape(-1, 3)
    batch = RainPointBatch(points)
    contacted = np.zeros(20, dtype=bool)
    previous = batch.update(0, 4.5).reshape(-1, 8, 3).copy()
    for distance in np.arange(.025, 60, .025):
        current = batch.update(distance, 4.5).reshape(-1, 8, 3).copy()
        assert current[:, :, 2].min() >= .13 - 1e-6
        contacted |= np.isclose(current[:, 2, 2], .13)
        reset = current[:, 0, 2] > previous[:, 0, 2] + 1
        # Respawn must follow the tail reaching within one simulation step
        # of the road, regardless of other drops in the same mesh.
        assert np.all(previous[reset, 0, 2] < .13 + .026)
        previous = current
    assert contacted.all()


def test_non_wrapping_drops_fall_at_requested_speed_and_fixed_topology():
    points = np.asarray(rain_streak_quads()[:400], dtype=np.float32).reshape(-1, 3)
    batch = RainPointBatch(points)
    before = batch.update(1, 4.5).copy()
    after = batch.update(1.13, 4.5).copy()
    before_tail = before[::8, 2]
    after_tail = after[::8, 2]
    falling = after_tail < before_tail
    np.testing.assert_allclose(before_tail[falling]-after_tail[falling], .13, atol=3e-6)
    assert before.shape == after.shape == points.shape
    # Old geometry only occupied an 8m-tall band. New phases span the column.
    assert np.ptp(after_tail) > 26


def test_highest_roof_intercepts_streaks_and_produces_contacts_on_its_surface():
    points = np.asarray(rain_streak_quads()[:40], dtype=np.float32).reshape(-1, 3)
    batch = RainPointBatch(points)
    # Entire volume is roofed, including a lower hidden tier.
    batch.set_surfaces([(-100, -100, 100, 100, 3.), (-100, -100, 100, 100, 8.)])
    assert np.all(batch.landing_z == 8.)
    hits = []
    for distance in np.arange(0, 60, .05):
        rendered = batch.update(distance, 4.5).reshape(-1, 8, 3)
        assert rendered[:, :, 2].min() >= 8.
        # Finished streaks collapse completely, leaving no hovering white marks.
        finished = rendered[:, 0, 2] == 8.
        assert np.all(np.ptp(rendered[finished], axis=1) == 0)
        hits.extend(batch.contacts.tolist())
    assert len(hits) >= 20
    assert np.all(np.asarray(hits)[:, 2] == 8.)


def test_slanted_trajectory_hits_roof_at_its_actual_xy_not_emitter_xy():
    points = np.asarray(rain_streak_quads()[:2], dtype=np.float32).reshape(-1, 3)
    batch = RainPointBatch(points)
    batch.set_surfaces([(-100, -100, 100, 100, 6.)], (10., 20.), 12.)
    center = batch.landing_points[0].copy()
    x, y = center[:2] + [10, 20]
    batch.set_surfaces([(x-.05, y-.05, x+.05, y+.05, 6.)], (10., 20.), 12.)
    assert batch.landing_z[0] == 6.
    np.testing.assert_allclose(batch.landing_points[0], center)
    batch.set_surfaces([(x+1., y+1., x+2., y+2., 6.)], (10., 20.), 12.)
    assert batch.landing_z[0] == np.float32(.13)


def test_splash_pool_expires_and_stays_bounded_under_torrential_bursts():
    pool = RainSplashPool()
    centers = np.array([[1, 2, .13], [3, 4, 8.]], dtype=np.float32)
    pool.spawn(centers, 10.)
    points = pool.update(10.08).reshape(pool.CAPACITY, pool.VERTICES, 3)
    assert np.all(points[:2, :, 2] >= centers[:, None, 2])
    assert np.max(points[1, :, 2]) > 8.05
    expired = pool.update(10.3).reshape(pool.CAPACITY, pool.VERTICES, 3)
    assert np.all(np.ptp(expired, axis=1) == 0)
    pool.spawn(np.tile(centers, (500, 1)), 11.)
    assert pool.update(11.1).shape == (128*36, 3)
    counts, indices = pool.topology()
    assert sum(n-2 for n in counts) == 2048
    assert sum(counts) == len(indices) == 128*36
