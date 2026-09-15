from pathlib import Path
import importlib.util
import sys

import numpy as np


SCRIPT = Path(__file__).parents[1] / "omniverse" / "scripts" / "rtg_yard_lidar.py"
SPEC = importlib.util.spec_from_file_location("rtg_yard_lidar_under_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_spherical_to_cartesian_uses_degrees_and_metres():
    points = MODULE.spherical_to_cartesian(
        [[0.0, 0.0, 10.0], [90.0, 0.0, 2.0], [0.0, 30.0, 4.0]]
    )

    np.testing.assert_allclose(
        points,
        [[10.0, 0.0, 0.0], [0.0, 2.0, 0.0], [3.4641016, 0.0, 2.0]],
        atol=1e-5,
    )


def test_pointcloud_bundle_writes_ply_pcd_and_npy(tmp_path):
    points = np.array([[1.25, 2.5, 3.75], [-1.0, 0.0, 0.5]], dtype=np.float32)
    bundle = MODULE.write_pointcloud_bundle(tmp_path, "yard scan", points)

    assert set(bundle) == {"ply", "pcd", "npy"}
    assert all(Path(path).is_file() for path in bundle.values())
    assert "element vertex 2" in Path(bundle["ply"]).read_text(encoding="ascii")
    assert "POINTS 2" in Path(bundle["pcd"]).read_text(encoding="ascii")
    np.testing.assert_allclose(np.load(bundle["npy"]), points)


def test_lidar_creation_forwards_downward_orientation():
    calls = []

    class FakeLidar:
        @staticmethod
        def create(path, **kwargs):
            calls.append((path, kwargs))
            return object()

    orientation = MODULE.DOWNWARD_ORIENTATION_WXYZ
    MODULE._create_lidar(
        FakeLidar,
        "/World/downward_lidar",
        "Example_Rotary",
        10.0,
        150.0,
        np,
        translations=(1.0, 2.0, 9.0),
        orientations=orientation,
    )

    assert calls[0][0] == "/World/downward_lidar"
    assert calls[0][1]["translations"].tolist() == [1.0, 2.0, 9.0]
    assert calls[0][1]["orientations"] == orientation
