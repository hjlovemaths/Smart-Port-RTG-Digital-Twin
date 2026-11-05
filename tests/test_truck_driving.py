import math,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
from truck_driving import TruckDriving,intersects


def test_fast_motion_stops_before_thin_wall_and_can_back_away():
    truck=TruckDriving((0,0,0),3,.7);truck.speed=8;truck.controls(1)
    obstacle=((-4.2,-2,-4.17,2),'container')
    for _ in range(20):
        truck.step(.1,[obstacle],lambda x,y:True)
        assert not intersects(truck.footprint(),obstacle[0])
        if truck.event=='collision':break
    assert truck.event=='collision' and truck.speed==0
    assert truck.hit=='container'
    stopped=truck.x;truck.controls(-1)
    for _ in range(10):truck.step(.1,[obstacle],lambda x,y:True)
    assert truck.x>stopped and truck.speed<0


def test_turning_body_cannot_sweep_through_an_obstacle():
    truck=TruckDriving((0,0,0),3,.7);truck.speed=6;truck.controls(1,1)
    obstacles=[((-6,-6,-5,0),'wall')]
    for _ in range(120):
        truck.step(.1,obstacles,lambda x,y:True)
        assert not intersects(truck.footprint(),obstacles[0][0])
        if truck.event=='collision':break
    assert truck.event=='collision'
    assert truck.heading!=0


def test_water_resets_pose_speed_and_input_instead_of_continuing_to_drive():
    truck=TruckDriving((0,0,0),3,.7);truck.controls(1)
    for _ in range(100):
        truck.step(.1,[],lambda x,y:x>=-4)
        if truck.event=='water_reset':break
    assert truck.event=='water_reset' and truck.reset_count==1
    assert (truck.x,truck.y,truck.heading)==truck.spawn
    assert truck.speed==truck.throttle==truck.steer==0


def test_brake_and_large_elapsed_time_do_not_jump_through_obstacles():
    truck=TruckDriving((0,0,0),3,.7);truck.speed=8;truck.controls(brake=True)
    truck.step(100,[],lambda x,y:True)
    assert abs(truck.x)<.8 and truck.speed<8


@pytest.mark.parametrize('value',[float('nan'),float('inf'),2,-2,True,'1'])
def test_bad_controls_are_rejected_without_partially_changing_state(value):
    truck=TruckDriving((0,0,0),3,.7);truck.controls(.5,.2)
    with pytest.raises(ValueError):truck.controls(1,value)
    assert truck.throttle==.5 and truck.steer==.2
