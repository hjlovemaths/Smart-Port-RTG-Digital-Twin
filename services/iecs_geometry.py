"""Shared visual-scene calibration; not real-world engineering dimensions."""
import math

GROUND_Z = .085
TRAILER_DECK_Z = .685
TRUCK_LANE_X = -3.75
QUAY_PICKUP_LANE_X = -238.15
YARD_ROW_ONE_X = 6.433333
YARD_PAD_Z = .12
CONTAINER_40_SIZE = (1.306287, 5.15, .904353)
# Both lanes follow the painted arrows and keep the whole truck inside the
# 150 m apron edge, including while it finishes the outbound leg.
GATE_ENTRY_STOP = (144., 388.4, GROUND_Z)
# An admitted truck pulls forward to leave a full truck length behind it.
GATE_ENTRY_FRONT_STOP = (134., 388.4, GROUND_Z)
GATE_EXIT_STOP = (139., 384.7, GROUND_Z)
GATE_EXIT_END = (144., 384.7, GROUND_Z)
# Measured on the scaled IECS truck: rail top and front 20 ft load position.
YARD_TRUCK_RAIL_Z = .625


def front_20ft_load_forward(length_m):
    """Keep the front of a single 20 ft box 8 cm behind the front rail edge."""
    return 2.125 * 1.22 - length_m / 2 - .08


FRONT_20FT_LOAD_FORWARD = front_20ft_load_forward(2.475)


def truck_load_point(vehicle, cargo):
    """Follow a load at its assigned trailer position as the truck turns."""
    x, y, _ = vehicle['position']
    heading = math.radians(vehicle['heading'])
    forward = cargo.get('load_forward_m', 0.)
    return [x+forward*math.sin(heading), y-forward*math.cos(heading), cargo.get('carry_height', 1.)]
