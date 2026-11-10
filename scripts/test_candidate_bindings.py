#!/usr/bin/env python3
"""Bind the completed experiment to the common review, without new executions."""
import copy
import json
from pathlib import Path
import shutil
from uuid import uuid4

from backend_boundaries import describe_boundary
from candidate_bindings import review_candidate, source_digest
from region_contracts import fingerprint, inspect_description_evidence

ROOT=Path(__file__).resolve().parents[1]
COMMON=ROOT/'evidence/sql-effect-observations/20261109-9f2591af957b'
BOUNDARY=ROOT/'evidence/backend-boundaries/20261109-29e334478285'
EXPORT=ROOT/'evidence/beets-prefix-export/20261109-44ae25a1b8a2'
AUDIT=EXPORT.parent/(EXPORT.name+'-audit.json')
PREFIX=ROOT/'evidence/initialization-effects/20261109-d12bba955bf8'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'


def main():
    out=ROOT/'evidence/candidate-bindings'/('20261109-'+uuid4().hex[:12]);out.mkdir(parents=True)
    def load(p):return json.loads(p.read_text())
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def ref(p):return dict(path=str(p.relative_to(ROOT)),sha256=fingerprint(p))
    checks=[];negative=[]
    def check(name,value):
        assert value,name
        checks.append(name)
    save('protocol.json',dict(kind='Offline source/candidate binding',no_business_execution=True,no_performance=True,
        goal='Attach the actual tested implementation, ordered effects and domain to the common review; do not manufacture admission',
        controls=['stale source/scope','changed implementation','false validation report','old candidate swap',
                  'missing node/condition/obligation','wrong effect order/type','false interface signature',
                  'missing decoder/resource','promoted bounded evidence','renamed source units'],
        stop='One fixed binding/review pass; existing selectors unchanged'))
    for name in ['candidate_bindings.py','region_contracts.py','test_candidate_bindings.py']:
        shutil.copy2(ROOT/'scripts'/name,out/name)
    try:
        for label,folder in [('common',COMMON),('boundary',BOUNDARY),('prefix',PREFIX),('export',EXPORT)]:
            check(label+' complete archive intact',all(fingerprint(folder/p)==h for p,h in load(folder/'manifest.json').items()))
        audit=load(AUDIT)
        check('actual final audit passed with all seven executions',audit['status']=='passed' and audit['executions']==7 and len(audit['checks'])==190)
        check('auditor and corrected runner match actual tested association',fingerprint(ROOT/audit['audit_source']['path'])==audit['audit_source']['sha256'] and
              fingerprint(ROOT/'scripts/test_beets_prefix_export.py')==audit['corrected_runner_sha256'])
        check('audit explicitly bound to this raw archive',audit['raw_evidence']==str(EXPORT.relative_to(ROOT)))
        d=load(COMMON/'beets-description.json');before=copy.deepcopy(d)
        artifacts={name:ref(EXPORT/path) for name,path in {
            'entry':'restored-candidate.py','reader':'reader.py','prefix':'prefix.py','state':'state.py',
            'consumer':'prefix_consumer.py','trace':'normal/restored/events.json','runs':'runs.json',
            'binding':'binding.json','manifest':'manifest.json','raw_failure':'failure.json'}.items()}
        artifacts.update(audit=ref(AUDIT),prefix_plan=ref(PREFIX/'prefix-plan.json'),prefix_report=ref(PREFIX/'report.json'),
                         counterexamples=ref(ROOT/'evidence/beets-scalar-contract/20261108-0671127c5421/cases.json'))
        for i,p in enumerate(d['provenance']):artifacts['source_'+str(i)]=copy.deepcopy(p)
        boundary=load(BOUNDARY/'beets-boundary.json')
        decoders=copy.deepcopy(boundary['output_decoders'])
        decoders['tracktotal']['host_decode']='Original LazyDict conversion/cache; source-derived update condition then private clear_dirty state step, then original fixed-field consumption; empty error retained. No arbitrary getter/identity equivalence.'
        boundary=describe_boundary(d,input_roles=boundary['input_roles'],ambient_resources=boundary['ambient_resources'],
                  output_decoders=decoders,evidence=[artifacts[k] for k in ['source_0','source_1','source_2','consumer','prefix','state']])
        conditions=[
            dict(id='fixed_deployment',status='execution_premise',statement='Pinned no-plugin source and stable native dispatch; no receiver-dependent getters, tracing or identity escape',evidence=['audit','prefix_report','counterexamples']),
            dict(id='successful_original_reads',status='execution_premise',statement='Original reads succeed; omitted read errors/connection callbacks are NOT recovered by fallback',evidence=['counterexamples','audit']),
            dict(id='closed_constructor_subset',status='unproved_obligation',statement='Whole-constructor allocation, descriptors, class defaults and KeyError album fallback are not proved by the retained prefix experiment',evidence=['prefix_report','consumer']),
            dict(id='existing_fallbacks',status='source_guard',statement='Existing multidisc and slow-sort branches call the original method before entering the optimized path',evidence=['entry','trace','audit']),
        ]
        def step(id,units,after,inputs,outputs,evidence,note):
            return dict(id=id,source_units=units,after=after,inputs=inputs,outputs=outputs,evidence=evidence,note=note)
        steps=[
            step('read_full_rows',['fixed_read'],[],d['scope']['interface_inputs'],{'rows':'ordered_full_sqlite_rows'},['reader','trace'],'Same first SQL and context; all columns/rows, sort and connection retained'),
            step('prepare_local_values',['first_materialization'],['read_full_rows'],{'rows':'ordered_full_sqlite_rows','db':'sqlite_library'},
                 {'lazy_fields':'local_lazy_values','private_state':'local_private_state'},['consumer'],'Original empty-result handling; original LazyDict, local nonescaping representation'),
            step('initialization_condition',['first_materialization'],['prepare_local_values'],{'lazy_fields':'local_lazy_values'},{},['prefix','consumer','prefix_plan','trace'],'Original predicate comparisons/truth and lazy cache effects'),
            step('private_state_step',['first_materialization'],['initialization_condition'],{'private_state':'local_private_state'},{},['state','consumer','trace'],'Retain resource truth/revision read after the initialization condition'),
            step('consume_scalar',['scalar_consumption'],['private_state_step'],{'lazy_fields':'local_lazy_values'},{'tracktotal':'python_int'},['consumer','entry','trace'],'Retain native conversion and cache, scoped fixed-field dispatch'),
        ]
        records=[
            ('retained',['read_full_rows'],[],[
                ('bounded_observation','Source/SQL and retained event sequence match the archived fixed deployment',['reader','audit']),
                ('unproved','General connection hooks/errors remain beyond the successful-read observations',['reader','counterexamples'])]),
            ('omitted_under_conditions',[],['fixed_deployment','successful_original_reads'],[
                ('unproved','Second statement has an independent view; absence of returned flex use is not general invisibility',['source_0','counterexamples']),
                ('excluded_by_execution_premise','Strict read-error counterexamples remain; no automatic recovery of omitted failures',['counterexamples','audit'])]),
            ('replaced',['prepare_local_values','initialization_condition','private_state_step'],['fixed_deployment','closed_constructor_subset'],[
                ('bounded_observation','Original empty-result error retained and locally checked',['consumer','prefix_report']),
                ('unproved','Recovered update and clear_dirty effects tested; arbitrary constructor/identity behavior still open',['prefix_report','audit'])]),
            ('replaced',['consume_scalar'],['fixed_deployment','closed_constructor_subset'],[
                ('unproved','Native fixed-field getter registry queried; receiver-dependent getters unsupported',['consumer','counterexamples']),
                ('bounded_observation','Original converter and prefix-shared cache preserved in tested domains',['prefix_report','audit'])]),
        ]
        coverage=[]
        for node,(disposition,mapped,conditions_for_node,obligations) in zip(d['program']['units'],records):
            coverage.append(dict(source_unit=node['id'],disposition=disposition,steps=mapped,conditions=conditions_for_node,
                obligations=[dict(requirement=req,status=status,note=note,evidence=evidence) for req,(status,note,evidence) in zip(node['requires'],obligations)]))
        implementation=['entry','reader','prefix','state','consumer']
        c=dict(schema='source-region-candidate/v1',source_digest=source_digest(d),authorizes_lowering=False,
               artifacts=artifacts,implementation_artifacts=implementation,
               entry=dict(kind='experiment_wrapper',artifact='entry',symbol='candidate',signature=dict(positional_only=[],positional=['album','arm'],keyword_only=[],vararg=None,kwarg=None)),
               boundary=boundary,conditions=conditions,steps=steps,coverage=coverage,
               validation=dict(report='audit',manifest='manifest',artifact_hashes={k:artifacts[k]['sha256'] for k in implementation},
                               authority='The fixed integration runner/audit associates these artifacts with seven exports; bounded observations only'))
        baseline=inspect_description_evidence(d,ROOT)
        bound=copy.deepcopy(d);bound['candidate_implementations']=[c]
        review=inspect_description_evidence(bound,ROOT)
        save('candidate.json',c);save('bound-description.json',bound);save('review.json',review)
        check('original source description unchanged',d==before)
        check('only new candidate review appended',{k:v for k,v in review.items() if k!='candidate_reviews'}==baseline)
        check('source units and all dependencies remain unchanged',bound['program']==d['program'] and bound['scope']==d['scope'])
        cr=review['candidate_reviews'][0]
        check('concrete executable wrapper recognized without admission',cr['status']=='bound_experiment_not_admitted' and not cr['authorizes_lowering'] and
              any(b['kind']=='experiment_entry_not_region_interface' for b in cr['blockers']))
        check('all eight original obligations retained',sum(len(x['obligations']) for x in cr['coverage'])==8)
        check('prior simplistic decoder annotation replaced with bound prefix/state',cr['boundary']['output_decoders']!=load(BOUNDARY/'beets-boundary.json')['output_decoders'])
        check('beets decision unchanged and no eligible intervals',review['decision']==load(COMMON/'beets-decision.json') and not any(i['eligible_under_existing_contract'] for i in review['intervals']))
        for name in ['neworder','payment']:
            legacy=load(COMMON/(name+'-description.json'));result=inspect_description_evidence(legacy,ROOT)
            old=load(ROOT/'evidence/common-evidence-admission/20261109-19732b434ce1'/(name+'-review.json'))
            check(name+' complete review and original selection unchanged',result==old)
            save(name+'-review.json',result)
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(name+' unchanged original core',fingerprint(ROOT/'scripts'/name)==fingerprint(FIRST/'inputs/scripts'/name))
        def reject(name,change):
            altered=copy.deepcopy(c);desc=copy.deepcopy(d);change(altered,desc)
            try:review_candidate(desc,altered,ROOT)
            except ValueError as exc:negative.append(dict(case=name,error=str(exc)));checks.append(name+' rejected')
            else:raise AssertionError('Accepted invalid binding: '+name)
        reject('changed execution scope',lambda c,d:d['scope'].update(execution_domain='all dynamic behavior'))
        reject('changed code hash',lambda c,d:c['artifacts']['prefix'].update(sha256='0'*64))
        reject('old entry substituted',lambda c,d:c['artifacts'].update(entry=ref(EXPORT/'old-candidate.py')))
        reject('failed raw report substituted for audit',lambda c,d:c['validation'].update(report='raw_failure'))
        reject('source unit omitted',lambda c,d:c['coverage'].pop(1))
        reject('deletion condition omitted',lambda c,d:c['coverage'][1].update(conditions=[]))
        reject('source obligation omitted',lambda c,d:c['coverage'][2]['obligations'].pop())
        reject('state before prefix',lambda c,d:c['steps'].__setitem__(slice(2,4),list(reversed(c['steps'][2:4]))))
        reject('wrong output type',lambda c,d:c['steps'][-1]['outputs'].update(tracktotal='int32'))
        reject('false region signature',lambda c,d:c['entry']['signature'].update(positional=['album_id','sort','db']))
        reject('wrapper relabeled direct region entry',lambda c,d:c['entry'].update(kind='region_entry'))
        reject('entry omitted from tested implementation',lambda c,d:c['implementation_artifacts'].remove('entry'))
        reject('step cites only test output',lambda c,d:c['steps'][2].update(evidence=['audit']))
        reject('missing resource',lambda c,d:c['boundary']['input_roles'].pop('db'))
        reject('missing decoder',lambda c,d:c['boundary']['output_decoders'].clear())
        reject('bounded evidence promoted to proof',lambda c,d:c['coverage'][2]['obligations'][1].update(status='proved'))
        reject('candidate declares admission',lambda c,d:c.update(authorizes_lowering=True))
        # These are generic source-unit identities, not names used by admission rules.
        renamed=copy.deepcopy(d);renamed_c=copy.deepcopy(c);mapping={n['id']:'unit_'+str(i) for i,n in enumerate(d['program']['units'])}
        for node in renamed['program']['units']:node['id']=mapping[node['id']]
        for edge in renamed['program']['edges']:
            edge['before']=mapping[edge['before']];edge['after']=mapping[edge['after']]
        for record in renamed_c['coverage']:record['source_unit']=mapping[record['source_unit']]
        for step_record in renamed_c['steps']:step_record['source_units']=[mapping[n] for n in step_record['source_units']]
        renamed_c['source_digest']=source_digest(renamed)
        rr=review_candidate(renamed,renamed_c,ROOT)
        check('renamed source units preserve review semantics',[b['kind'] for b in rr['blockers']]==[b['kind'] for b in cr['blockers']] and rr['status']==cr['status'])
        save('negative-controls.json',negative)
        save('report.json',dict(status='passed',checks=checks,negative_controls=len(negative),source_units=4,candidate_steps=5,
            obligations=8,implementation_artifacts=5,backend_admitted=False,production_changed=False,business_executions=0,performance_measured=False,
            conclusion='Actual tested candidate now bound to common review with all source obligations retained; wrapper/interface and semantic gaps explicit',
            next_gap='A source-bound direct region entry and closed effect/codec capability are needed; do not relabel the experiment wrapper as a database backend'))
    except BaseException as exc:
        save('failure.json',dict(type=type(exc).__name__,message=str(exc),checks=checks));raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):fingerprint(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out)


if __name__=='__main__':main()
