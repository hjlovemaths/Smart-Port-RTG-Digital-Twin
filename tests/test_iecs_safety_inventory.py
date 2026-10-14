import sys
from pathlib import Path
import json
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'services'))
from iecs_collision import swept_conflict
from iecs_inventory import project_inventory
from iecs_core import IECS

def truck(identity,x,y):return {'id':identity,'position':[x,y,0],'heading':90.,'status':'working'}

def test_sweep_detects_blocker_between_endpoints():
    a=truck('a',0,0);b=truck('b',12,0)
    assert swept_conflict(a,[24,0,0],90,[a,b])=='b'
    b['position'][1]=5
    assert swept_conflict(a,[24,0,0],90,[a,b]) is None

def test_departed_truck_does_not_block():
    a=truck('a',0,0);b=truck('b',8,0);b['status']='departed'
    assert swept_conflict(a,[12,0,0],90,[a,b]) is None

def test_scheduler_waits_and_continues_after_blocker_removed():
    e=IECS();oid=e.submit('gate_out');o=e.orders[oid];e.assign(o)
    v=e.vehicles[o['vehicle']];v.update(position=[151,388.4,.085],heading=90.)
    blocker=truck('obstacle',161,388.4)
    e.motion_guard=lambda vehicle,end,heading:swept_conflict(vehicle,end,heading,[blocker])
    end=[163,388.4,.085]
    for _ in range(20):e._move(o,v,end,.25,target={'kind':'exit'})
    stopped=v['position'][:]
    assert o['wait_reason']=='Collision wait: obstacle' and stopped[0]<163
    blocker['status']='departed'
    for _ in range(20):
        if e._move(o,v,end,.25,target={'kind':'exit'}):break
    assert v['position']==end and not o['wait_reason']
    e.close()

def test_inventory_projection_is_read_only_and_identity_checked(tmp_path):
    path=tmp_path/'inventory.json'
    path.write_text(json.dumps({'stacks':{'3C/042':{'rows':[[{'containerId':'real-1'}]],'revision':1}}}))
    before=path.read_bytes()
    o={'id':'order','status':'completed','container':'demo','source_prim':'/World/LiveContainers/YardInventory/Bay_3C_042/R01_T01_Container','target':{'kind':'gate'}}
    c={'id':'demo','production_id':'real-1'}
    result=project_inventory(path,[o],[c])
    assert not result['candidate']['stacks']['3C/042']['rows'][0]
    assert path.read_bytes()==before and not result['production_write']
    c['production_id']='wrong'
    with pytest.raises(ValueError):project_inventory(path,[o],[c])
