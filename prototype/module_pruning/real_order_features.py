"""Source-bound NewOrder schedule features; no learned score or safety decision."""
import hashlib
import itertools
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'


def extract(source):
    """The frozen generated dialect has one SQL statement per line, flat FORs.

    This scanner supplies observed table/local access and loop placement only.
    IF/error paths, constraints, triggers and hidden effects are not summarized.
    """
    events=[]; loop=None; loop_number=0
    for line_number,line in enumerate(source.splitlines(),1):
        line=line.strip()
        if re.fullmatch(r'FOR v_i IN 1\.\.v_count LOOP',line):
            if loop is not None:raise ValueError('Nested loop outside scope')
            loop_number+=1;loop=loop_number;continue
        if line=='END LOOP;':
            if loop is None:raise ValueError('Unmatched loop')
            loop=None;continue
        if not re.match(r'^(SELECT|PERFORM|UPDATE|INSERT)\b',line):continue
        if not line.endswith(';'):raise ValueError('Multiline SQL outside scope')
        normalized=' '.join(line.lower().split())
        reads=set(re.findall(r'\bfrom\s+([a-z_][a-z_0-9]*)\b',normalized))
        update=re.match(r'update\s+([a-z_][a-z_0-9]*)\s+set\b',normalized)
        insert=re.match(r'insert\s+into\s+([a-z_][a-z_0-9]*)\s*\(',normalized)
        writes={m[1] for m in (update,insert) if m}
        if update:reads.add(update[1])
        if not reads and not writes:raise ValueError('Unrecognized SQL access')
        definitions=set(re.findall(r'\binto\s+(v_\w+)\b',normalized)) if not insert else set()
        uses=set(re.findall(r'\bv_\w+\b',normalized))-definitions
        events.append(dict(id=hashlib.sha256(normalized.encode()).hexdigest()[:16],sql=line,line=line_number,
                           loop=loop,reads=sorted(reads),writes=sorted(writes),defines=sorted(definitions),uses=sorted(uses),
                           lock_read='for update' in normalized))
    if loop is not None:raise ValueError('Unclosed loop')
    if len({e['id'] for e in events})!=len(events):raise ValueError('Repeated SQL needs explicit identity mapping')
    return events


def expand(events, iterations):
    result=[]
    for loop,group in itertools.groupby(events,key=lambda e:e['loop']):
        group=list(group)
        for iteration in (range(iterations) if loop is not None else [None]):
            for event in group:
                result.append(dict(event,instance=event['id']+':'+str(iteration),iteration=iteration))
    return result


def compare(reference,candidate,iterations):
    before=expand(reference,iterations); after=expand(candidate,iterations)
    positions={e['instance']:i for i,e in enumerate(after)}
    if set(positions)!={e['instance'] for e in before}:raise ValueError('Changed SQL inventory, not only schedule')
    inverted=[]
    for a,b in itertools.combinations(before,2):
        if positions[a['instance']]<positions[b['instance']]:continue
        shared=(set(a['writes'])&(set(b['reads'])|set(b['writes']))) | (set(b['writes'])&set(a['reads']))
        value_dependency=(set(a['defines'])&set(b['uses'])) | (set(b['defines'])&set(a['uses']))
        inverted.append(dict(before=a['instance'],after=b['instance'],reference_lines=[a['line'],b['line']],
            shared_tables=sorted(shared),local_dependencies=sorted(value_dependency),
            cross_iteration=a['iteration'] is not None and b['iteration'] is not None and a['iteration']!=b['iteration'],
            both_write=bool(a['writes'] and b['writes'])))
    denominator=len(before)*(len(before)-1)//2
    return dict(expanded_sql_events=len(before),inverted_pairs=len(inverted),
                inverted_table_conflicts=sum(bool(p['shared_tables']) for p in inverted),
                cross_iteration_table_conflicts=sum(bool(p['shared_tables']) and p['cross_iteration'] for p in inverted),
                inverted_local_dependencies=sum(bool(p['local_dependencies']) for p in inverted),
                inverted_write_pairs=sum(p['both_write'] for p in inverted),
                inversion_fraction=len(inverted)/denominator,
                crossed_conflict_fraction=sum(bool(p['shared_tables']) for p in inverted)/denominator,
                pairs=inverted)


def legal_projection_swap(source):
    lines=source.splitlines()
    selected=[i for i,line in enumerate(lines) if re.match(r'SELECT .* INTO v_(order|lines)_json FROM ',line)]
    if len(selected)!=2 or selected[1]!=selected[0]+1:raise ValueError('Expected adjacent projection reads')
    a,b=selected;lines[a],lines[b]=lines[b],lines[a]
    return '\n'.join(lines)+'\n'


def run():
    manifest=json.loads((ARCHIVE/'manifest.json').read_text()); hashes={}
    def verified(name):
        data=(ARCHIVE/name).read_bytes();h=hashlib.sha256(data).hexdigest()
        assert manifest[name]==h;hashes[name]=h
        return data.decode()
    full=verified('generated/full.sql');early=verified('generated/early_write.sql')
    legal=legal_projection_swap(full)
    reference=extract(full);early_events=extract(early);legal_events=extract(legal)
    schedules={name:{str(n):compare(reference,events,n) for n in (5,15)}
               for name,events in [('unchanged',reference),('projection_swap',legal_events),('early_write',early_events)]}
    for n in ('5','15'):
        unchanged=schedules['unchanged'][n];good=schedules['projection_swap'][n];bad=schedules['early_write'][n]
        assert unchanged['inverted_pairs']==0
        assert good['inverted_pairs']==1 and good['inverted_table_conflicts']==good['inverted_local_dependencies']==0
        assert bad['cross_iteration_table_conflicts']==int(n)*(int(n)-1)//2
    # Selected legal pair is read-only, writes distinct locals and consumes neither output.
    projections=[e for e in reference if set(e['defines']) & {'v_order_json','v_lines_json'}]
    assert len(projections)==2 and all(not e['writes'] and not e['lock_read'] for e in projections)
    assert not(set(projections[0]['defines'])&set(projections[1]['uses']))
    assert not(set(projections[1]['defines'])&set(projections[0]['uses']))
    # Same structural calculation survives table renaming and function-name changes.
    renamed=lambda s:re.sub(r'\bstock\b','inventory_alias',s,flags=re.I)
    renamed_result=compare(extract(renamed(full)),extract(renamed(early)),5)
    assert renamed_result['inverted_table_conflicts']==schedules['early_write']['5']['inverted_table_conflicts']
    altered=full.replace('neworder_full(', 'unrelated_name(')
    assert compare(reference,extract(altered),5)['inverted_pairs']==0
    probe=json.loads(verified('probe-results.json'));run_report=json.loads(verified('run.json'))
    assert run_report['status']=='passed'
    assert probe['early_write_counterexample']['original']!=probe['early_write_counterexample']['mutant']
    return dict(scope='Source-bound SQL schedule evidence; no score, training label or deletion decision',
                source_hashes=hashes,source_sql_events=len(reference),events=dict(full=reference,projection_swap=legal_events,early_write=early_events),
                schedules=schedules,projection_swap_sql=legal,
                projection_swap_argument=dict(status='conditional_source_argument_only',new_database_execution=False,
                  premises=['frozen generated SQL and ordinary table schema','serial execution with the same visible state for both reads',
                            'normal completion of both projection queries','no required SQL-trace/error-timing observation',
                            'fixed built-in JSON projection semantics'],
                  reasoning='Adjacent read-only projections define disjoint locals, do not consume each other, and see unchanged business state. Final JSON construction uses the same named locals.'),
                existing_observation=dict(full='passed historical fixed replay, not universal equivalence',
                                          early_write=probe['early_write_counterexample']),
                checks=['unchanged schedule zero inversions','same SQL inventory in all variants',
                        'projection swap one independent pair','early write crosses later stock reads at both line counts',
                        'projection locals independent','table/function renaming controls','historical evidence hashes'],
                limitations=['one generated PL/pgSQL source family, flat-loop scanner only',
                    '5 and 15 are structural expansions, not executions or all-input coverage',
                    'table overlap is may-alias, not actual row collision; repeated items remain allowed',
                    'constraints, triggers, hidden calls, error control and lock scheduling not fully modeled',
                    'no source-held-out training or calibrated probability; toy-model coefficients not transferred'],
                new_business_executions=0,training_admitted=False,deletion_authorized=False)


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
