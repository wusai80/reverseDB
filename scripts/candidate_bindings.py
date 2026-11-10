"""Bind executable experiment artifacts to common source regions for review.

Integrity and coverage checks only. A bounded test never certifies a new backend,
and an experiment wrapper is not a source-interface implementation by declaration.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path

from backend_boundaries import describe_boundary
from region_contracts import fingerprint


def source_digest(description):
    value={key:description[key] for key in ['schema','scope','program','provenance','unresolved']}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def review_candidate(description, candidate, root):
    """Return a non-admitting review, or reject stale/incomplete bindings.

    Source effects remain the authority's assertions. This checks references and
    accounting; it does not derive semantic equivalence from the proposed steps.
    """
    root=Path(root)
    if candidate.get('schema')!='source-region-candidate/v1' or candidate.get('authorizes_lowering') is not False:
        raise ValueError('Candidate evidence cannot grant lowering')
    if candidate.get('source_digest')!=source_digest(description):
        raise ValueError('Candidate bound to a different source/scope')
    artifacts=candidate['artifacts']
    if not artifacts:raise ValueError('Missing implementation/evidence artifacts')
    for label,ref in artifacts.items():
        path=(root/ref['path']).resolve()
        if not path.is_relative_to(root.resolve()) or fingerprint(path)!=ref['sha256']:
            raise ValueError('Changed or outside artifact: '+label)
    def artifact(label):
        if label not in artifacts:raise ValueError('Unbound artifact: '+label)
        return root/artifacts[label]['path']
    entry=candidate['entry']
    functions=[n for n in ast.parse(artifact(entry['artifact']).read_text()).body if isinstance(n,ast.FunctionDef) and n.name==entry['symbol']]
    if len(functions)!=1:raise ValueError('Entry function missing or ambiguous')
    fn=functions[0]
    actual=dict(positional_only=[a.arg for a in fn.args.posonlyargs],positional=[a.arg for a in fn.args.args],
                keyword_only=[a.arg for a in fn.args.kwonlyargs],vararg=fn.args.vararg.arg if fn.args.vararg else None,
                kwarg=fn.args.kwarg.arg if fn.args.kwarg else None)
    if entry['signature']!=actual:raise ValueError('Entry signature does not match actual code')
    if entry['kind'] not in ['experiment_wrapper','region_entry']:raise ValueError('Unknown entry kind')
    if entry['kind']=='region_entry' and (actual['vararg'] or actual['kwarg'] or
            set(actual['positional_only']+actual['positional']+actual['keyword_only'])!=set(description['scope']['interface_inputs'])):
        raise ValueError('Declared region entry does not implement the source interface')
    implementation=set(candidate['implementation_artifacts'])
    if entry['artifact'] not in implementation:raise ValueError('Entry is not bound as an implementation artifact')
    boundary=candidate['boundary']
    rebuilt=describe_boundary(description,input_roles=boundary['input_roles'],ambient_resources=boundary['ambient_resources'],
                              output_decoders=boundary['output_decoders'],evidence=boundary['evidence'])
    if rebuilt!=boundary:raise ValueError('Boundary description altered or incomplete')
    for ref in boundary['evidence']:
        if ref not in artifacts.values():raise ValueError('Boundary evidence is not bound')
    conditions=candidate['conditions']
    if not conditions or len({c['id'] for c in conditions})!=len(conditions):raise ValueError('Missing/duplicate execution conditions')
    condition_ids={c['id'] for c in conditions}
    for condition in conditions:
        if condition['status'] not in ['execution_premise','unproved_obligation','source_guard'] or not condition['statement']:
            raise ValueError('Conditions cannot assert a new semantic proof')
        if not condition['evidence']:raise ValueError('Condition has no source/evidence reference')
        for label in condition['evidence']:artifact(label)
    nodes=description['program']['units'];node_ids=[n['id'] for n in nodes]
    steps=candidate['steps'];step_ids=[s['id'] for s in steps]
    if not steps or len(set(step_ids))!=len(step_ids):raise ValueError('Missing/duplicate candidate steps')
    available=dict(description['scope']['interface_inputs']);seen=set()
    for step in steps:
        if not step['source_units'] or not set(step['source_units'])<=set(node_ids):raise ValueError('Step source unit missing')
        if not set(step['after'])<=seen:raise ValueError('Candidate effect order violates dependencies')
        if not set(step['evidence'])&implementation:raise ValueError('Step has no implementation reference')
        for label in step['evidence']:artifact(label)
        if any(available.get(k)!=v for k,v in step['inputs'].items()):raise ValueError('Step consumes an unavailable or wrong-typed value')
        if set(available)&set(step['outputs']):raise ValueError('Candidate values must have unique definitions')
        available.update(step['outputs']);seen.add(step['id'])
    if any(available.get(k)!=v for k,v in description['scope']['interface_outputs'].items()):
        raise ValueError('Candidate loses source output')
    coverage=candidate['coverage']
    if [c['source_unit'] for c in coverage]!=node_ids:raise ValueError('All source units need ordered accounting')
    obligations=[]
    for node,record in zip(nodes,coverage):
        if record['disposition'] not in ['retained','replaced','omitted_under_conditions']:
            raise ValueError('Unknown source disposition')
        if not set(record['conditions'])<=condition_ids:raise ValueError('Unbound deletion/specialization condition')
        if record['disposition']!='retained' and not record['conditions']:
            raise ValueError('Replacement/deletion requires explicit conditions')
        mapped=[s['id'] for s in steps if node['id'] in s['source_units']]
        if record['steps']!=mapped or (record['disposition']=='omitted_under_conditions')!= (not mapped):
            raise ValueError('Source-to-step coverage mismatch')
        if [r['requirement'] for r in record['obligations']]!=node.get('requires',[]):
            raise ValueError('A source effect obligation was lost or changed')
        for obligation in record['obligations']:
            if obligation['status'] not in ['bounded_observation','unproved','excluded_by_execution_premise']:
                raise ValueError('Bounded evidence cannot discharge a source obligation')
            if not obligation['note'] or not obligation['evidence']:raise ValueError('Missing obligation evidence/limits')
            for label in obligation['evidence']:artifact(label)
            if obligation['status']=='excluded_by_execution_premise' and not record['conditions']:
                raise ValueError('Excluded obligation needs conditions')
            obligations.append(dict(source_unit=node['id'],**copy.deepcopy(obligation)))
    validation=candidate['validation']
    report= json.loads(artifact(validation['report']).read_text())
    if report.get('status')!='passed':raise ValueError('Bound validation report did not pass')
    manifest_path=artifact(validation['manifest'])
    manifest=json.loads(manifest_path.read_text())
    for label in candidate['implementation_artifacts']:
        try:relative=str(artifact(label).relative_to(manifest_path.parent))
        except ValueError:raise ValueError('Implementation is outside validation archive')
        if manifest.get(relative)!=artifacts[label]['sha256']:
            raise ValueError('Implementation not in validated manifest: '+label)
    for label,digest in validation['artifact_hashes'].items():
        if label not in artifacts or artifacts[label]['sha256']!=digest:raise ValueError('Validation/artifact binding mismatch')
    if not set(candidate['implementation_artifacts'])<=set(validation['artifact_hashes']):
        raise ValueError('An implementation component lacks validation binding')
    # Validated hashes are frontend associations, NOT proof that arbitrary reports
    # tested arbitrary code. The study-specific runner verifies that association.
    blockers=[dict(kind='source_obligation_not_proved',source_unit=r['source_unit'],requirement=r['requirement'],status=r['status']) for r in obligations]
    blockers.extend(dict(kind=c['status'],condition=c['id'],statement=c['statement']) for c in conditions if c['status']!='source_guard')
    if entry['kind']=='experiment_wrapper':
        blockers.append(dict(kind='experiment_entry_not_region_interface',actual=actual,required=description['scope']['interface_inputs']))
    blockers.append(dict(kind='no_new_backend_admission',reason='Artifact binding and bounded observations do not create a verified backend contract'))
    return dict(kind='source-candidate-binding-review/v1',status='bound_experiment_not_admitted',
                entry=copy.deepcopy(entry),actual_signature=actual,source_digest=source_digest(description),
                boundary=copy.deepcopy(boundary),steps=copy.deepcopy(steps),coverage=copy.deepcopy(coverage),
                conditions=copy.deepcopy(conditions),validation=copy.deepcopy(validation),
                blockers=blockers,authorizes_lowering=False,selector_input_changed=False,
                authority='Source and artifact integrity plus declared effect accounting; bounded observations do not prove arbitrary semantics')
