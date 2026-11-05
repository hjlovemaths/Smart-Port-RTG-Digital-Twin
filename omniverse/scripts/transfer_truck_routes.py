"""Directed lane-centre paths derived from the actual ground marking builder."""
import math
from build_ground_traffic_network import (
    CROSS,CAUSEWAYS,CROSS_LANE_OFFSET,COLLECTOR_CENTER_X,
    COLLECTOR_LANE_OFFSET,QUAY_SOUTHBOUND_X,QUAY_RETURN_NORTHBOUND_X,
)
from yard_coordinate_mapping import TRUCK_OPERATING_LANE_CENTER_X_M

COLLECTOR_NORTH_X=COLLECTOR_CENTER_X+COLLECTOR_LANE_OFFSET
COLLECTOR_SOUTH_X=COLLECTOR_CENTER_X-COLLECTOR_LANE_OFFSET
WORK_X=TRUCK_OPERATING_LANE_CENTER_X_M


def east_y(cross):return cross-CROSS_LANE_OFFSET
def west_y(cross):return cross+CROSS_LANE_OFFSET


def north_to_east(x,y,z):
    # Begin turning early enough that the long cab clears the bridge median.
    radius=6.
    return [[x+radius*(i/32)**2,y-radius*(1-i/32)**2,z] for i in range(33)]


def south_to_east(x,y,z):
    # The first crossroad is close to the quay end. A wider sweep keeps the
    # cab inside the new end railing instead of overshooting the deck edge.
    radius=6.
    return [[x+radius*(i/32)**2,y+radius*(1-i/32)**2,z] for i in range(33)]


def parking_exit(park):
    # A ten-metre arc clears the median with the actual long cab overhang.
    # Returning uses this exact path in reverse, with the cab still facing out.
    radius=10.;cx=COLLECTOR_NORTH_X+radius;cy=park[1]+radius;z=park[2]
    arc=[[cx+radius*math.cos(-math.pi/2-i*math.pi/64),
          cy+radius*math.sin(-math.pi/2-i*math.pi/64),z] for i in range(33)]
    return [list(park)]+arc


def return_routes(start,park,from_quay=False):
    z=park[2];bottom=east_y(CROSS[0]);route=[list(start)]
    if from_quay:
        route += south_to_east(QUAY_SOUTHBOUND_X,bottom,z)+[[COLLECTOR_NORTH_X,bottom,z]]
    else:
        exits=[c for c in CROSS if east_y(c)<start[1]-.5]
        if not exits:raise ValueError('堆场交接点下游没有可用的返场出口')
        exit_y=east_y(max(exits))
        route += [[WORK_X,exit_y,z],[COLLECTOR_NORTH_X,exit_y,z]]
        if exit_y>bottom+.5:
            upper=min(c for c in CROSS if west_y(c)>exit_y+6)
            # Use the open junction to turn around. The long cab cannot make
            # a tight east-to-south cut without sweeping the collector median.
            route += [[COLLECTOR_NORTH_X,west_y(upper),z],[COLLECTOR_SOUTH_X,west_y(upper),z],
                      [COLLECTOR_SOUTH_X,bottom,z],[COLLECTOR_NORTH_X,bottom,z]]
    backing=list(reversed(parking_exit(park)))
    route.append(backing[0])
    return route,backing


def upstream_cross(y):
    candidates=[cross for cross in CROSS if east_y(cross)>y+6]
    if not candidates:raise ValueError('堆位上游没有可用的顺向车道入口')
    return min(candidates)


def quay_arrival(cross,stop):
    """Approach the STS southbound; route a lower bridge via a legal U-turn."""
    z=stop[2];lane_y=west_y(cross)
    if lane_y>stop[1]+6:return [[QUAY_SOUTHBOUND_X,lane_y,z],list(stop)]
    upper=next((c for c in CAUSEWAYS if west_y(c)>stop[1]+6),None)
    if upper is None:raise ValueError('岸桥交接点上游没有可用的调头路口')
    return [[QUAY_RETURN_NORTHBOUND_X,lane_y,z],[QUAY_RETURN_NORTHBOUND_X,west_y(upper),z],
            [QUAY_SOUTHBOUND_X,west_y(upper),z],list(stop)]


def ship_to_yard_routes(park,stop,yard_y):
    z=stop[2];entry=upstream_cross(yard_y)
    approach=next((c for c in CAUSEWAYS if west_y(c)>park[1]+6 and west_y(c)>stop[1]+6),None)
    if approach is None:raise ValueError('停车场到岸桥缺少可用的顺向引桥')
    empty=parking_exit(park)+[[COLLECTOR_NORTH_X,west_y(approach),z]]
    empty+=quay_arrival(approach,stop)
    bridge=entry if entry in CAUSEWAYS else CAUSEWAYS[-1]
    loaded=[list(stop)]+south_to_east(QUAY_SOUTHBOUND_X,east_y(CROSS[0]),z)
    loaded += [[QUAY_RETURN_NORTHBOUND_X,east_y(CROSS[0]),z]]
    loaded+=north_to_east(QUAY_RETURN_NORTHBOUND_X,east_y(bridge),z)
    if entry in CAUSEWAYS:
        loaded += [[WORK_X,east_y(entry),z],[WORK_X,yard_y,z]]
    else:
        # 4C needs the land-side terminal road to enter its -Y work lane.
        # Cross water at the last actual causeway, never at the unbridged end.
        loaded += [[COLLECTOR_NORTH_X,east_y(bridge),z],[COLLECTOR_NORTH_X,west_y(entry),z],
                   [WORK_X,west_y(entry),z],[WORK_X,yard_y,z]]
    return empty,loaded,{'truck_crossroad_y':bridge,'yard_entry_crossroad_y':entry,'traffic_rule':'painted_lane_centres_right_hand_work_lane_negative_y'}


def yard_to_ship_routes(park,stop,yard_y):
    z=stop[2];entry=upstream_cross(yard_y);work=[WORK_X,yard_y,z]
    empty=parking_exit(park)+[[COLLECTOR_NORTH_X,west_y(entry),z],
           [WORK_X,west_y(entry),z],work]
    exits=[cross for cross in CROSS if east_y(cross)<yard_y-.5]
    if not exits:raise ValueError('源堆位下游没有可用的顺向车道出口')
    exit_cross=max(exits)
    bridge=next((c for c in CAUSEWAYS if west_y(c)>east_y(exit_cross)+6),None)
    if bridge is None:bridge=CAUSEWAYS[-1]
    turn=entry if entry>bridge else bridge
    loaded=[work,[WORK_X,east_y(exit_cross),z],[COLLECTOR_NORTH_X,east_y(exit_cross),z],
            [COLLECTOR_NORTH_X,west_y(turn),z]]
    if turn!=bridge:
        # Terminal road has no causeway: return down the land-side collector.
        loaded += [[COLLECTOR_SOUTH_X,west_y(turn),z],[COLLECTOR_SOUTH_X,west_y(bridge),z]]
    loaded+=quay_arrival(bridge,stop)
    return empty,loaded,{'truck_crossroad_y':bridge,'yard_entry_crossroad_y':entry,'traffic_rule':'painted_lane_centres_right_hand_work_lane_negative_y'}
