"""Production-schema dry-run projection. Never writes the live inventory."""
import copy
import hashlib
import json
import re


def project_inventory(path,orders,containers):
    raw=path.read_bytes();data=json.loads(raw);candidate=copy.deepcopy(data)
    boxes={c['id']:c for c in containers};applied=[]
    for o in orders:
        if o['status']!='completed':continue
        c=boxes[o['container']];record=None
        match=re.search(r'/Bay_([1234]C)_(\d+)/R(\d+)_T(\d+)_Container$',o.get('source_prim',''))
        if match:
            block,bay,row,tier=match.groups();key=f'{block}/{int(bay):03d}'
            stack=candidate['stacks'].get(key);rows=stack['rows'] if stack else []
            if int(row)>len(rows) or len(rows[int(row)-1])!=int(tier):
                raise ValueError('Source inventory changed or source is not top-level: '+key)
            record=rows[int(row)-1][-1]
            identity=c.get('production_id')
            if not identity or record['containerId']!=identity:
                raise ValueError('Source inventory identity mismatch: '+key)
            record=rows[int(row)-1].pop();stack['revision']=stack.get('revision',0)+1
        if o['target']['kind']=='yard':
            key=f"{o['target']['block']}/{o['target']['bay']:03d}"
            stack=candidate['stacks'].setdefault(key,{'bayId':key,'block':o['target']['block'],
                'bayNumber':o['target']['bay'],'containerSizeFt':40,'rows':[[] for _ in range(6)],'revision':0})
            if stack['rows'][0]:raise ValueError('Production target row is occupied: '+key)
            if record is None:
                # Keep imported demo identities explicit, never invent a real box number.
                record={'containerId':c['id'],'containerNo':c['id'],'sizeFt':40,'heightM':2.6,
                        'type':'40GP','source':'iecs-demo-proposal','colorIndex':0}
            stack['rows'][0].append(record);stack['revision']=stack.get('revision',0)+1
        applied.append(o['id'])
    return {'base_sha256':hashlib.sha256(raw).hexdigest(),'orders':applied,
            'candidate':candidate,'mode':'dry_run','production_write':False,
            'requires_review':['anchors and stats reconciliation','real container numbers for incoming cargo',
                               'scene regeneration and atomic commit','external authoritative inventory approval']}
