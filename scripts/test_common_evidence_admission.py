#!/usr/bin/env python3
"""Offline evidence-to-admission integration; no SQL compilation or timing."""
import copy
import json
from pathlib import Path
import shutil
from uuid import uuid4

from fusion_placement import inspect_interval
from region_contracts import denormalize, fingerprint, inspect_description_evidence

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'evidence/sql-effect-observations/20261109-9f2591af957b'
FIRST = ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'


def main():
    out=ROOT/'evidence/common-evidence-admission'/('20261109-'+uuid4().hex[:12]);out.mkdir(parents=True)
    def load(p):return json.loads(p.read_text())
    def save(n,v):(out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
    checks=[]
    def check(n,v):
        assert v,n
        checks.append(n)
    def rejected(n,fn):
        try:fn()
        except ValueError as e:
            checks.append(n);return str(e)
        raise AssertionError(n)
    save('protocol.json',dict(cases=['NewOrder','Payment','beets'],
        method='Replay unchanged old checker on source intervals with explicit external-output observation; actual selection receives exact original contract',
        no_new_optimizer=True,no_sql_execution=True,no_database_changes=True,no_timing=True,
        controls=['observations cannot certify purity','changed SQL/hash and missing coverage rejected',
                  'additional table reads block until reviewed','terminal output remains live',
                  'unknown cost not zero','missing effect and codec obligations still block','symbol renaming does not change cuts']))
    for name in ['region_contracts.py','test_common_evidence_admission.py']:
        shutil.copy2(ROOT/'scripts'/name,out/name)
    try:
        check('all input observation artifacts intact',all(fingerprint(SOURCE/p)==h for p,h in load(SOURCE/'manifest.json').items()))
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(name+' immutable first-project core',fingerprint(ROOT/'scripts'/name)==fingerprint(FIRST/'inputs/scripts'/name))
        reviews={};descriptions={}
        for name in ['neworder','payment','beets']:
            d=load(SOURCE/(name+'-description.json'));before=copy.deepcopy(d)
            review=inspect_description_evidence(d,ROOT);reviews[name]=review;descriptions[name]=d
            save(name+'-review.json',review)
            check(name+' source description untouched',d==before)
            check(name+' existing complete decision unchanged',review['decision']==load(SOURCE/(name+'-decision.json')))
            check(name+' access evidence never discharges semantics',all(not x['discharges_semantic_obligations'] for x in review['unit_evidence']))
            n=len(d['program']['units'])
            check(name+' all source intervals inspected without marker candidates',len(review['intervals'])==n*(n+1)//2 and
                  all(x['original_checker']['stop']<=n for x in review['intervals']))
            check(name+' external outputs explicitly observed',review['external_boundary']['values']==d['scope']['interface_outputs'] and
                  review['external_boundary']['analysis_only'] and not review['external_boundary']['included_in_selector_input'])
            if name!='beets':
                old=load(FIRST/(name+'-selection.json'))
                expected={(x['start'],x['stop']) for x in old['legal_intervals']}
                actual={(x['original_checker']['start'],x['original_checker']['stop']) for x in review['intervals'] if x['eligible_under_existing_contract']}
                check(name+' all legal intervals identical to original catalog',actual==expected)
            # An observation may veto a conflicting summary, but cannot grant
            # a backend. This is the same evidence contract for every project.
            qnode=next(n for n in d['program']['units'] if n.get('queries'))
            conflict=copy.deepcopy(d)
            conflict['analysis_observations']['sql_access'][qnode['id']][0]['direct_access']['reads'].append('unreviewed_relation')
            result=inspect_description_evidence(conflict,ROOT)
            check(name+' newly observed relation gets no plan',result['decision']['status']=='evidence_conflict_no_plan' and result['decision']['selection'] is None)
            check(name+' contradictory observation cannot admit any interval',not any(x['eligible_under_existing_contract'] for x in result['intervals']))

        beets=descriptions['beets'];review=reviews['beets']
        whole=next(x['original_checker'] for x in review['intervals'] if x['original_checker']['start']==0 and x['original_checker']['stop']==4)
        check('beets terminal scalar is live out',whole['live_out']=={'tracktotal':'python_int'})
        check('beets missing output codec diagnosed',any(r['kind']=='missing_wire_codec' and r.get('value')=='tracktotal' and r.get('direction')=='database_to_host' for r in whole['reasons']))
        check('beets unknown body and backend retained',{'unknown_body_effect','no_database_lowering'}<= {r['kind'] for r in whole['reasons']})
        check('beets sidecar has not made SQL grammar or body known',all(not n['body_known'] and n['backend_implementation'] is None for n in beets['program']['units']))
        check('beets still no global plan or objective',review['decision']['selection'] is None and not any(x['eligible_under_existing_contract'] for x in review['intervals']))
        # Witness the boundary issue in the old checker without changing it.
        without=inspect_interval(denormalize(beets['program']),0,4)
        check('without external boundary old liveness would omit return scalar',without['live_out']=={})
        save('external-output-witness.json',dict(without_boundary=without,with_boundary=whole,
            implication='Missing return consumer in a local description can hide an ABI obligation; analysis marker restores it without adding an executable phase'))

        negatives={}
        for variant in ['false_purity','false_permission','wrong_sql','missing_observation','wrong_output_type']:
            d=copy.deepcopy(beets)
            if variant=='false_purity':d['analysis_observations']['sql_access']['fixed_read'][0]['complete_effects']=True
            elif variant=='false_permission':d['analysis_observations']['authorizes_replacement']=True
            elif variant=='wrong_sql':d['analysis_observations']['sql_access']['fixed_read'][0]['sql_sha256']='0'*64
            elif variant=='missing_observation':d['analysis_observations']['sql_access'].pop('flex_read')
            else:d['scope']['interface_outputs']['tracktotal']='not-the-produced-type'
            negatives[variant]=rejected(variant+' rejected',lambda d=d:inspect_description_evidence(d,ROOT))
        unknown=copy.deepcopy(beets);unknown['program']['units'][0]['original_calls']=None
        result=inspect_description_evidence(unknown,ROOT)
        check('unknown cost never turned into zero or inspected cost',result['intervals']==[] and result['inspection_unavailable_costs']==['fixed_read'] and result['decision']['selection'] is None)
        # A standalone scalar can expose a missing codec even when a backend
        # claim is supplied. It remains an UNBOUND structural control only.
        control=copy.deepcopy(beets)
        control['program']['units']=[dict(id='read',inputs={'key':'int'},outputs={'result':'int'},queries=['SELECT value FROM data WHERE id=?'],
            transaction='tx',effects=dict(reads=['data'],writes=[],locks=[]),requires=['preserve_errors'],body_known=True,
            backend_implementation=dict(lowering='structural-test',verified_source_contract=True,guarantees=['preserve_errors'],transaction_policy='same_outer_transaction'),
            original_calls=dict(base=1,terms={}))]
        control['program'].update(edges=[],external_inputs={'key':'int'},wire_types={'int':{'host_to_database':True,'database_to_host':False}})
        control['scope'].update(interface_inputs={'key':'int'},interface_outputs={'result':'int'})
        control.pop('analysis_observations')
        result=inspect_description_evidence(control,ROOT)
        check('standalone result codec cannot be hidden by terminal position',not result['intervals'][0]['original_checker']['allowed'] and
              any(r['kind']=='missing_wire_codec' and r.get('value')=='result' for r in result['intervals'][0]['original_checker']['reasons']))
        control['program']['wire_types']['int']['database_to_host']=True
        result=inspect_description_evidence(control,ROOT)
        check('structural feasibility alone does not authorize an unbound backend',result['intervals'][0]['original_checker']['allowed'] and not result['intervals'][0]['eligible_under_existing_contract'] and result['decision']['selection'] is None)
        control['program']['units'][0]['backend_implementation']['guarantees']=[]
        result=inspect_description_evidence(control,ROOT)
        check('old checker still requires explicit effect guarantees',any(r['kind']=='unmet_semantic_obligation' for r in result['intervals'][0]['original_checker']['reasons']))
        # Rename ordinary source symbols, including the external output. No
        # project names participate in liveness or admission.
        renamed=copy.deepcopy(beets)
        for n in renamed['program']['units']:
            if 'tracktotal' in n.get('outputs',{}):n['outputs']['public_value']=n['outputs'].pop('tracktotal')
        renamed['scope']['interface_outputs']={'public_value':'python_int'}
        result=inspect_description_evidence(renamed,ROOT)
        full=next(x['original_checker'] for x in result['intervals'] if x['original_checker']['start']==0 and x['original_checker']['stop']==4)
        check('public output liveness independent of field name',full['live_out']=={'public_value':'python_int'} and result['decision']['selection'] is None)
        save('negative-controls.json',negatives)
        save('report.json',dict(status='passed',checks=checks,
            intervals={name:len(r['intervals']) for name,r in reviews.items()},
            selected_regions={name:len(r['decision']['selection']['database_regions']) if r['decision'].get('selection') is not None else None for name,r in reviews.items()},
            result='Existing source-bound capabilities retain original plans; access observations cannot replace semantic/ABI/backend evidence. Terminal public values now explicitly reach the old ABI checker.',
            next_gap='Distinguish resident execution resources from transferable parameters, and bind actual input/output codecs plus scoped lowering; do not invent a serialization for the Library object.',
            limits='Same manual authority and old checker; no new equivalent implementation, data run, SQL analysis or performance sample'))
    except BaseException as e:
        save('failure.json',dict(type=type(e).__name__,message=str(e),checks=checks));raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):fingerprint(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out)


if __name__=='__main__':main()
