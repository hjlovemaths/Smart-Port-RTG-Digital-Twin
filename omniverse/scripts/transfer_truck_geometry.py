"""Measured trailer support and cab-side loading, shared by both directions."""
from pxr import Gf,Usd,UsdGeom
from ship_transfer_plan import PARK,box


def truck_loading(stage,size_ft,cargo_length):
    deck=Gf.Range3d()
    for prim in Usd.PrimRange(stage.GetPrimAtPath(PARK),Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdGeom.Boundable) and 'trailer_side_rail' in prim.GetName():
            deck.UnionWith(box(stage,prim.GetPath()))
    if deck.IsEmpty():raise ValueError('停车场集卡缺少可测量承载面')
    pivot=Gf.Vec3d(deck.GetMidpoint()[0],deck.GetMidpoint()[1],deck.GetMax()[2])
    # The authored cab faces -X. Keep 8 cm behind the trailer's front edge.
    offset=min(0.,deck.GetMin()[0]+cargo_length/2+.08-pivot[0]) if size_ft==20 else 0.
    return deck,pivot,offset


def loading_routes(stage,size_ft,length,quay_contact,yard_y,reverse=False):
    from transfer_truck_routes import ship_to_yard_routes,yard_to_ship_routes,return_routes
    deck,park,offset=truck_loading(stage,size_ft,length)
    # Both operating lanes travel -Y (heading +90). Cargo is ahead of the
    # deck centre; stop the vehicle farther north while preserving crane poses.
    stop=list(quay_contact);stop[1]-=offset;stop[2]=park[2]
    yard_vehicle_y=yard_y-offset
    planner=yard_to_ship_routes if reverse else ship_to_yard_routes
    empty,loaded,info=planner(park,stop,yard_vehicle_y)
    home,backing=return_routes(loaded[-1],park,from_quay=reverse)
    info.update(truck_park_m=list(park),truck_load_offset_m=[offset,0,0],
                truck_vehicle_stop_m=stop,yard_vehicle_stop_m=[-3.75,yard_vehicle_y,park[2]],
                truck_route_return=home,truck_route_parking=backing)
    return empty,loaded,info
