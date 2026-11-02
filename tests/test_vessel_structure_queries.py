import sys
from pathlib import Path
sys.path.insert(0,str(Path('omniverse/scripts').resolve()))
from vessel_structure_queries import query_vessel_structure

def test_structure_snapshot_counts():
 r=query_vessel_structure();assert r['total_structure_slots']==507;assert r['available_bays']==[1,3,5,7,9];assert r['is_cargo_manifest'] is False
 assert query_vessel_structure(bay=1,zone='D')['groups'][0]['row_count']==11
 assert query_vessel_structure(bay=3,zone='D')['groups'][0]['row_count']==13

def test_location_pagination_and_unknown():
 r=query_vessel_structure(include_slots=True,limit=2);assert len(r['slots'])==2;assert r['next_offset']==2
 loc=r['slots'][0]['location'];r=query_vessel_structure(location=loc);assert r['matched_slots']==1;assert r['slots'][0]['location']==loc
 assert query_vessel_structure(bay=99)['matched_slots']==0

def test_future_provider_reuses_validation():
 from ship_structure_data import DEFAULT_SOURCE
 import json
 response=json.loads(DEFAULT_SOURCE.read_text(encoding='utf-8'))
 assert query_vessel_structure(provider=lambda:response)['source']=='interface'
