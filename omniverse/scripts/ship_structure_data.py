"""Validate STS 6.6 vessel structure without inventing a cargo manifest."""
import json
from pathlib import Path

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / 'data/vessel_structure.sample.json'


def digits(value, name):
    text=str(value)
    if not text.isdigit():
        raise ValueError(f'{name} must contain digits: {value!r}')
    return int(text)


def read_structure(path=DEFAULT_SOURCE):
    return normalize_structure(json.loads(Path(path).read_text(encoding='utf-8-sig')))


def normalize_structure(response):
    if not isinstance(response,dict) or not isinstance(response.get('data'),dict):
        raise ValueError('6.6 response must contain a data object')
    if response.get('result',0)!=0 or response.get('success',True) is not True:
        raise ValueError('6.6 response reports failure')
    data=response['data']
    bays=data.get('vesselBay')
    if not isinstance(bays,list) or not bays:
        raise ValueError('vesselBay must be a non-empty list')
    groups=[];slots=[];seen=set();group_keys=set()
    for bay in bays:
        number=digits(bay['bayNo'],'bayNo')
        zone=bay.get('htdDkFg',bay.get('htDkFghtdDkFg'))
        if zone not in ('H','D'):
            raise ValueError(f'Bay {number}: expected H or D zone')
        key=(number,zone)
        if key in group_keys:
            raise ValueError(f'Duplicate bay/zone {key}')
        group_keys.add(key)
        def sequence_map(items,seq_name,no_name):
            result={}
            for item in items:
                seq=digits(item[seq_name],seq_name)
                value=digits(item[no_name],no_name)
                if seq in result:
                    raise ValueError(f'Duplicate {seq_name} in bay {number}')
                result[seq]=value
            return result
        rows=sequence_map(bay['rowList'],'rowSeq','rowNo')
        tiers=sequence_map(bay['tierList'],'tierSeq','tierNo')
        group={'bay':number,'zone':zone,'bay_id':bay.get('bayId'),
               'hatch_id':bay.get('vhtId'),'rows':rows,'tiers':tiers,
               'source':bay,'locations':[]}
        for cell in bay['locationList']:
            loc=str(cell['location'])
            if len(loc)<6 or not loc.isdigit():
                raise ValueError(f'Invalid location {loc!r}')
            b,r,t=int(loc[:-4]),int(loc[-4:-2]),int(loc[-2:])
            rs=digits(cell['rowSeq'],'rowSeq');ts=digits(cell['tierSeq'],'tierSeq')
            if b!=number or rows.get(rs)!=r or tiers.get(ts)!=t:
                raise ValueError(f'location/sequence mismatch: {loc}')
            if (zone=='H' and not 0<t<80) or (zone=='D' and t<82) or t%2:
                raise ValueError(f'location/zone mismatch: {loc}')
            if loc in seen:
                raise ValueError(f'Duplicate location {loc}')
            seen.add(loc)
            slot={'location':loc,'bay':b,'row':r,'tier':t,'zone':zone,
                  'row_seq':rs,'tier_seq':ts,'size_code':str(cell.get('size') or ''),
                  'size_ft':20 if b%2 else 40,'source':cell,'bay_id':bay.get('bayId')}
            slots.append(slot);group['locations'].append(loc)
        groups.append(group)
    hatches={}
    for hatch in data.get('hatch',[]):
        ident=hatch['id']
        if ident in hatches and hatches[ident]!=hatch:
            raise ValueError(f'Conflicting hatch {ident}')
        hatches[ident]=hatch
    return {'groups':groups,'slots':slots,'hatches':list(hatches.values()),
            'hatch_covers':data.get('hatchCover',[]),
            'vbt_id':data.get('vbtId'),'source':response}


def tier_center(tier,deck_z,max_hold_tier):
    height=2.591;pitch=2.64
    if tier>=82:
        return deck_z+.2+(tier-82)/2*pitch+height/2
    return deck_z-(max_hold_tier-tier+2)/2*pitch+height/2
