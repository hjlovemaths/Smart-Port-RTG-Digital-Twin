"""Continuous box contact tests in rendered world metres; no Kit dependency."""
import math


def sweep_box(start, end, obstacle):
    """First collision fraction for a translating/resizing AABB, or None.

    A separating motion from an existing touching/overlapping face is allowed.
    The linearly changing bounds also conservatively cover small trim steps.
    """
    enter, leave = -math.inf, math.inf
    for axis in range(3):
        # Both inequalities must remain nonnegative for positive-volume overlap.
        for gap, change in ((start[1][axis] - obstacle[0][axis],
                             end[1][axis] - start[1][axis]),
                            (obstacle[1][axis] - start[0][axis],
                             start[0][axis] - end[0][axis])):
            if abs(change) < 1e-12:
                if gap <= 1e-7:
                    return None
            elif change > 0:
                enter = max(enter, -gap / change)
            else:
                leave = min(leave, -gap / change)
    if leave <= max(enter, 0) + 1e-9 or enter > 1 or leave <= 0:
        return None
    if enter < -1e-7:
        # Recovery from a pre-existing overlap must move out through the nearest
        # face, rather than across the whole obstacle toward its opposite face.
        penetration = min(min(start[1][i] - obstacle[0][i], obstacle[1][i] - start[0][i]) for i in range(3))
        for axis in range(3):
            motion = (end[0][axis] + end[1][axis] - start[0][axis] - start[1][axis]) / 2
            low = start[1][axis] - obstacle[0][axis]
            high = obstacle[1][axis] - start[0][axis]
            if ((motion < -1e-8 and low <= penetration + 1e-6)
                    or (motion > 1e-8 and high <= penetration + 1e-6)):
                return None
        return 0.0
    return max(0.0, enter)


def box_bounds(points):
    return tuple(tuple(fn(p[i] for p in points) for i in range(3)) for fn in (min, max))


def inside_xy(point, bounds, tolerance=0.0):
    return all(bounds[0][i] - tolerance <= point[i] <= bounds[1][i] + tolerance for i in (0, 1))
