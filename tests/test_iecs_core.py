import importlib.util
from pathlib import Path
import pytest
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'services'))

spec=importlib.util.spec_from_file_location('iecs_core',Path(__file__).parents[1]/'services/iecs_core.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


@pytest.mark.parametrize('flow',list(m.FLOWS))
def test_flow_closes_vehicle_and_container_cycle(flow):
    engine=m.IECS();oid=engine.submit(flow)
    for _ in range(20000):
        engine.tick(.25)
        if engine.orders[oid]['status']=='completed':break
    order=engine.orders[oid];assert order['status']=='completed'
    truck=engine.vehicles[order['vehicle']]
    assert truck['load'] is None and truck['order'] is None
    assert not engine.reserved and not engine.equipment and engine.road_owner is None
    box=engine.containers[order['container']]
    assert box['location']==order['target']['id'] or box['location'].startswith('EXIT/')
    assert box['version']==2
    engine.close()


def test_yard_work_lane_never_reverses():
    for block in m.BLOCKS:
        target=m.yard(block,20)
        path=m.route([138,100,.085],target['stop'],target=target)
        assert path[-2][0]==-3.75 and path[-2][1]>path[-1][1]
        path=m.route(target['stop'],[138,100,.085],source=target)
        assert path[1][0]==-3.75 and path[1][1]<path[0][1]


def test_loaded_quay_exit_preserves_southbound_heading():
    source=m.quay();source['stop']=[-238.15,39.1855,.085]
    target=m.yard('3C',20)
    path=m.route(source['stop'],target['stop'],source,target)
    assert path[1][0]==path[0][0] and path[1][1]<path[0][1]
    assert any(abs(p[1]-(-5.375-1.85))<.001 for p in path)


def test_pause_reset_and_idempotent_request():
    e=m.IECS();oid=e.submit('unload','same');assert e.submit('unload','same')==oid
    e.control('pause');before=e.snapshot();e.tick(.25);assert e.snapshot()==before
    e.control('resume');e.tick(.25);assert e.orders[oid]['vehicle']
    e.control('reset');assert not e.orders and not e.containers and len(e.vehicles)==20
    assert all(v['position']==v['home'] for v in e.vehicles.values())


def test_mixed_queue_finishes_and_stock_has_unique_owners():
    e=m.IECS();ids=[e.submit(flow) for flow in m.FLOWS];e.control('speed',20)
    for _ in range(5000):
        e.tick(.25)
        loaded=[v['load'] for v in e.vehicles.values() if v['load']]
        assert len(loaded)==len(set(loaded))
        if all(e.orders[i]['status']=='completed' for i in ids):break
    assert all(e.orders[i]['status']=='completed' for i in ids)
    assert not e.reserved and e.road_owner is None


def test_invalid_speed_rejected():
    e=m.IECS()
    for speed in (0,float('nan'),21):
        with pytest.raises(ValueError):e.control('speed',speed)


@pytest.mark.parametrize('block',list(m.BLOCKS))
def test_forward_same_lane_yard_transfer_is_direct(block):
    source=m.yard(block,30);target=m.yard(block,10)
    assert m.route(source['stop'],target['stop'],source,target)==[source['stop'],target['stop']]


def test_upstream_and_other_block_transfers_still_use_roads():
    source=m.yard('3C',10);target=m.yard('3C',30)
    path=m.route(source['stop'],target['stop'],source,target)
    assert len(path)>2 and path[1][1]<path[0][1]
    other=m.yard('2C',10)
    assert len(m.route(source['stop'],other['stop'],source,other))>2


def test_different_lane_cannot_take_forward_shortcut():
    source=m.yard('3C',30);target=m.yard('3C',10)
    target['stop'][0]=17.25
    assert len(m.route(source['stop'],target['stop'],source,target))>2


def test_gate_out_continues_outward_without_returning_to_collector():
    e=m.IECS();oid=e.submit('gate_out');e.control('speed',20)
    exit_positions=[]
    for _ in range(5000):
        o=e.orders[oid]
        if o['step']==4:
            v=e.vehicles[o['vehicle']]
            exit_positions.append(v['position'][:])
            assert v['load']==o['container']
        e.tick(.25)
        if o['status']=='completed':
            exit_positions.append(e.vehicles[o['vehicle']]['position'][:]);break
    assert o['status']=='completed' and len(exit_positions)>=2
    assert all(b[0]>=a[0] for a,b in zip(exit_positions,exit_positions[1:]))
    assert all(abs(p[1]-o['target']['stop'][1])<1e-6 for p in exit_positions)
    assert exit_positions[-1][0]==o['target']['stop'][0]+12.
    assert e.vehicles[o['vehicle']]['status']=='departed'
    e.close()
