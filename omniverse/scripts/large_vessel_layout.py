"""Shared, metre-based vessel layout for the STS01--STS03 berth."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / "omniverse/scenes/large_sts_vessel.usda"
STRUCTURE_LAYER = ROOT / "omniverse/scenes/ship_structure_boxes.usda"
SHIP = "/World/PortAndRTG/SMART_PORT_LARGE_CONTAINER_SHIP"
REPLACED = (
    "/World/PortAndRTG/SMART_PORT_4C_FEEDER_SHIP_LEFT_IMPORTED",
    "/World/PortAndRTG/SMART_PORT_4C_FEEDER_SHIP_RIGHT_IMPORTED",
    "/World/PortAndRTG/SMART_PORT_LOCAL_SHIP_4c40_shoreline_east_IMPORTED",
    "/World/ShipDouble20YardModelLayer",
)
ROWS = (12, 10, 8, 6, 4, 2, 0, 1, 3, 5, 7, 9, 11)
HOLD_TIERS = tuple(range(2, 17, 2))
DECK_TIERS = tuple(range(82, 91, 2))
BAYS = tuple(range(1, 60, 2)) + (62,)
ROW_PITCH = 2.55
TIER_PITCH = 2.64
DECK_Z = 9.4
SHIP_X = -262.8
BEAM_M = 40.6


def bay_y(bay):
    if bay == 62:
        return 229.35
    if bay < 1 or bay > 59 or bay % 2 == 0:
        raise ValueError("Expected an authored odd 20-foot bay, or stern bay 62")
    return 17.5 + ((bay - 1) // 2) * 6.3 + (19.7 if bay >= 45 else 0)


def row_x(row):
    return (row // 2 if row % 2 == 0 else -(row + 1) // 2) * ROW_PITCH


def tier_z(tier):
    if tier in DECK_TIERS:
        return DECK_Z + .2 + (tier - 82) / 2 * TIER_PITCH + 2.591 / 2
    if tier in HOLD_TIERS:
        return DECK_Z - (18 - tier) / 2 * TIER_PITCH + 2.591 / 2
    raise ValueError("Unknown vessel tier")


def ensure_live_layout(stage):
    """Mount the saved layout without reopening the stage or clearing sessions."""
    if not LAYER.is_file() or not stage.GetPrimAtPath("/World/PortAndRTG"):
        return False
    from pxr import Sdf, Usd
    identifier = LAYER.as_posix()
    saved = Sdf.Layer.Find(identifier)
    if saved is not None:
        saved.Reload()
    if not stage.GetPrimAtPath(SHIP):
        # A session sublayer also updates an already open Kit stage.
        layers = stage.GetSessionLayer().subLayerPaths
        if identifier not in layers:
            layers.insert(0, identifier)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for path in REPLACED:
            if stage.GetPrimAtPath(path):
                stage.OverridePrim(path).SetActive(False)
    if STRUCTURE_LAYER.is_file():
        structure_id=STRUCTURE_LAYER.as_posix()
        saved_structure=Sdf.Layer.Find(structure_id)
        if saved_structure is not None:
            saved_structure.Reload()
        if not stage.GetPrimAtPath(SHIP+'/StructureSlots'):
            layers=stage.GetSessionLayer().subLayerPaths
            if structure_id not in layers:
                layers.insert(0,structure_id)
    import importlib, sts_boom_layout
    importlib.reload(sts_boom_layout).ensure_live_booms(stage)
    import sys
    catalog = sys.modules.get('smartport.agent_panel.runtime_catalog')
    if catalog is not None and 'get_vessel_structure' not in catalog.SCHEMAS:
        for name in ('runtime_catalog','agent_api','project_interfaces'):
            module = sys.modules.get('smartport.agent_panel.'+name)
            if module is not None: importlib.reload(module)
    return bool(stage.GetPrimAtPath(SHIP))
