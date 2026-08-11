import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "omniverse" / "scenes" / "smart_port.usda"
REPLACEMENTS = ROOT / "omniverse" / "scenes" / "4c_ship_replacements.usda"
RECEIVER = ROOT / "omniverse" / "scripts" / "rtg_twin_state_receiver.py"

BERTHED_SHIPS = (
    (REPLACEMENTS, "SMART_PORT_4C_FEEDER_SHIP_LEFT_IMPORTED", -246.2, 50.0, -90.0),
    (REPLACEMENTS, "SMART_PORT_4C_FEEDER_SHIP_RIGHT_IMPORTED", -246.2, 120.0, -90.0),
    (SCENE, "SMART_PORT_LOCAL_SHIP_4c40_shoreline_east_IMPORTED", -246.2, 190.0, -90.0),
    (SCENE, "SMART_PORT_LOCAL_SHIP_4c40_shoreline_west_IMPORTED", -246.2, 260.0, -90.0),
    (SCENE, "SMART_PORT_REMOTE_CARGO_SHIP_01", -260.0, 330.0, -78.0),
    (SCENE, "SMART_PORT_REMOTE_CARGO_SHIP_02", -275.0, 400.0, -105.0),
    (SCENE, "SMART_PORT_REMOTE_CARGO_SHIP_03", -292.0, 460.0, -65.0),
)

COMMON_SHIP_SCALE = "(0.03883929, 0.027641999, 0.043764994)"


def _ship_body(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf'def Xform "{re.escape(name)}"\s*\(.*?\)\s*\{{(.*?)\n\s*\}}',
        text,
        flags=re.DOTALL,
    )
    assert match, name
    return match.group(1)


def test_all_seven_cargo_ships_use_one_size_in_the_sts_quay_waters():
    berth_positions = []
    for path, name, x, y, rotation in BERTHED_SHIPS:
        body = _ship_body(path, name)
        assert f"xformOp:rotateZ = {rotation:g}" in body
        assert f"xformOp:scale = {COMMON_SHIP_SCALE}" in body
        translate = re.search(
            r"xformOp:translate = \((-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\)",
            body,
        )
        assert translate
        actual_x, actual_y, _actual_z = map(float, translate.groups())
        assert actual_x == x
        assert actual_y == y
        assert actual_x < -241.75
        berth_positions.append(actual_y)

    assert berth_positions == [50.0, 120.0, 190.0, 260.0, 330.0, 400.0, 460.0]
    assert all(
        following - previous >= 60.0
        for previous, following in zip(berth_positions, berth_positions[1:])
    )


def test_near_ships_are_parallel_and_remote_ships_vary_distance_and_heading():
    near_ships = BERTHED_SHIPS[:4]
    remote_ships = BERTHED_SHIPS[4:]
    assert {rotation for _path, _name, _x, _y, rotation in near_ships} == {-90.0}
    assert len({x for _path, _name, x, _y, _rotation in remote_ships}) == 3
    assert len({rotation for _path, _name, _x, _y, rotation in remote_ships}) == 3


def test_live_driver_preserves_the_two_feeder_ship_berths():
    text = RECEIVER.read_text(encoding="utf-8")
    assert "(-246.2, 50.0, 1.3012471449582028)" in text
    assert "(-246.2, 120.0, 1.3012471449582028)" in text
    assert "rotate_op.Set((90.0, 0.0, -90.0))" in text
