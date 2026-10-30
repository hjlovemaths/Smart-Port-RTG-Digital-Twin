from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse/scripts"))
from rtg_plc_contact import sweep_box


BOX = ((-1, -1, 0), (1, 1, 1))


def test_fast_downward_sweep_cannot_tunnel_through_a_container():
    assert sweep_box(((-.5, -.5, 3), (.5, .5, 3.3)),
                     ((-.5, -.5, -2), (.5, .5, -1.7)), BOX) == pytest.approx(.4)


def test_contact_can_move_away_or_slide_but_cannot_move_into_roof():
    touching = ((-.5, -.5, 1), (.5, .5, 1.2))
    assert sweep_box(touching, ((-.5, -.5, 2), (.5, .5, 2.2)), BOX) is None
    assert sweep_box(touching, ((0, -.5, 1), (1, .5, 1.2)), BOX) is None
    assert sweep_box(touching, ((-.5, -.5, .9), (.5, .5, 1.1)), BOX) == 0


def test_side_sweep_stops_loaded_container_and_allows_recovery_from_overlap():
    assert sweep_box(((-3, -.5, .1), (-2, .5, .9)),
                     ((2, -.5, .1), (3, .5, .9)), BOX) == pytest.approx(.2)
    start = ((-.5, -.5, .99), (.5, .5, 1.2))
    assert sweep_box(start, ((-.5, -.5, 1.1), (.5, .5, 1.31)), BOX) is None
    assert sweep_box(start, ((-.5, -.5, .8), (.5, .5, 1.01)), BOX) == 0


def test_empty_crossing_and_stationary_shapes_do_not_generate_contact():
    assert sweep_box(((2, 2, 3), (3, 3, 4)), ((2, 2, -1), (3, 3, 0)), BOX) is None
    assert sweep_box(((2, 2, 3), (3, 3, 4)), ((2, 2, 3), (3, 3, 4)), BOX) is None
