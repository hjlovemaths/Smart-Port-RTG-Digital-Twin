"""Read-only Agent vessel structure queries; providers share the 6.6 response schema."""
from ship_structure_data import read_structure, normalize_structure

def query_vessel_structure(bay=None, zone=None, location=None, include_slots=False, offset=0, limit=100, provider=None):
    # Future TOS adapter supplies a callable returning the original 6.6 JSON envelope.
    data = normalize_structure(provider()) if provider is not None else read_structure()
    groups = [g for g in data['groups'] if (bay is None or g['bay']==bay) and (zone is None or g['zone']==zone)]
    slots = [s for s in data['slots'] if (bay is None or s['bay']==bay) and (zone is None or s['zone']==zone) and (location is None or s['location']==location)]
    result = {'source': 'interface' if provider is not None else 'local_txt_snapshot', 'vbt_id': data['vbt_id'],
        'scope': 'returned_structure_only', 'is_cargo_manifest': False,
        'note': '箱位结构不代表实际装箱；未返回的贝位未知。H=船舱，D=甲板。C/D/1原始size编码含义未确认。',
        'available_bays': sorted({g['bay'] for g in data['groups']}),
        'total_structure_slots':len(data['slots']), 'matched_slots':len(slots),
        'groups':[{'bay':g['bay'],'zone':g['zone'],'row_count':len(g['rows']),
                   'rows':[v for k,v in sorted(g['rows'].items())],
                   'declared_tiers':[v for k,v in sorted(g['tiers'].items())],
                   'valid_tiers':sorted({s['tier'] for s in data['slots'] if s['bay']==g['bay'] and s['zone']==g['zone']}),
                   'slot_count':len(g['locations'])} for g in groups],
        'hatch_count':len(data['hatches']),'hatch_cover_count':len(data['hatch_covers'])}
    if include_slots or location is not None:
        result['slots']=[{k:s[k] for k in ('location','bay','row','tier','zone','size_code','size_ft')} for s in slots[offset:offset+limit]]
        result['next_offset']=offset+limit if offset+limit<len(slots) else None
    return result
