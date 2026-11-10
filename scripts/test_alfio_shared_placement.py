#!/usr/bin/env python3
"""Feed alf.io facts to the first project's unchanged selector; static only."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from alfio_placement_contracts import reservation_contract
from fusion_placement import choose_placement, inspect_interval, plan_cost, cost_value, validate_plan
from fusion_regions import sql_effect

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
DEPS = ROOT / 'evidence/alfio-dependencies/20261106-f8e5aaa63540'
DECOMPILE = ROOT / 'evidence/alfio-baseline/20261106-788b831dd41c'
CONTRACT = ROOT / 'evidence/alfio-batch-contract/20261106-6b686908dc5e'


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = ROOT / 'evidence/alfio-shared-placement' / ('20261106-' + uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report = dict(status='running', checks=[], inputs={}, retained_assets={}, new_database_runs=0, new_timing_samples=0)

    def save(name, value): (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    def check(name, value):
        if not value: raise AssertionError(name)
        report['checks'].append(name)
    def retain(folder, name):
        path = folder / name
        if digest(path) != json.loads((folder / 'manifest.json').read_text())[name]: raise ValueError('Changed evidence ' + str(path))
        report['retained_assets'][str(path)] = digest(path)
        return path
    try:
        for name in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
            archived = retain(FIRST, 'inputs/scripts/' + name)
            check('unchanged first-project core: ' + name, digest(ROOT / 'scripts' / name) == digest(archived))
        for name in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py', 'alfio_placement_contracts.py', 'test_alfio_shared_placement.py']:
            path = ROOT / 'scripts' / name; target = out / 'inputs/scripts' / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
            report['inputs']['scripts/' + name] = digest(path)
        save('protocol.json', dict(algorithm='ordered-placement-dp/v1, exact first-project implementation and SQL effect parser',
            objective='unchanged lexicographic worst-case normal-path JDBC execute calls, then fewer moved units, then fewer database regions',
            front_end='source-bound grouping for one alf.io method; exact original SQL retained; no new selection logic',
            branches='empty versus nonempty input separated because the old affine model cannot express the zero/nonzero indicator',
            historical_comparison='run the same selector on archived NewOrder/Payment contracts and require complete selection equality',
            abstention='unverified backend and unsupported SQL remain barriers; no timing-based or project-name-based override',
            conditional_cost='cost arithmetic on an explicitly unadmitted hypothetical layout, not optimizer admission',
            stop='one static integration check; no new optimization, database replay or benchmark'))
        for project in ['neworder', 'payment']:
            contract = json.loads(retain(FIRST, project + '-contract.json').read_text())
            old = json.loads(retain(FIRST, project + '-selection.json').read_text())
            current = choose_placement(contract); save(project + '-selection.json', current)
            check(project + ': complete selection unchanged', current == old)
        facts = json.loads(retain(DEPS, 'bytecode-facts.json').read_text())
        source = retain(DECOMPILE, 'decompiled/alfio/repository/TicketRepository.java').read_text()
        caller_source = retain(DECOMPILE, 'decompiled/alfio/manager/TicketReservationManager.java').read_text()
        try:
            reservation_contract(facts, source, 'unknown caller')
        except ValueError:
            check('unknown supplier cannot be silently modeled as zero JDBC calls', True)
        else:
            raise AssertionError('unknown supplier accepted')
        cases = json.loads(retain(CONTRACT, 'database/database-cases.json').read_text())
        check('nonempty batch costs one execute, regardless of duplicate or missing IDs', all(
            sum(e.get('method') == 'executeBatch' for e in c['wire']) == 1
            for c in cases if c['case'] in ['ordered-duplicates', 'missing-id', 'null-id', 'rls-denied']))
        check('empty input and preparation error execute no SQL', all(not c['wire'] for c in cases if c['case'] in ['empty', 'supplier-error']))
        conclusions = {}
        for empty in [False, True]:
            label = 'empty' if empty else 'nonempty'; contract = reservation_contract(facts, source, caller_source, empty)
            selection = choose_placement(contract); interval = inspect_interval(contract, 1, 2)
            save(label + '-contract.json', contract); save(label + '-selection.json', selection); save(label + '-batch-admission.json', interval)
            check(label + ': original method and all three phases retained', not selection['database_regions'] and selection['validation']['accepted'] and
                  [s['nodes'][0] for s in selection['segments']] == [n['id'] for n in contract['nodes']])
            check(label + ': true original cost retained', selection['original_calls'] == selection['selected_calls'] == {'base': 0 if empty else 1, 'terms': {}})
            reasons = {r['kind'] for r in interval['reasons']}
            check(label + ': unknown SQL and unverified lowering explicitly preserved', {'unknown_sql_effect', 'unverified_lowering'} <= reasons)
            query = contract['nodes'][1]['queries'][0]
            check(label + ': no casts, literal, or named bindings erased', ':ticketMetadata::jsonb' in query and ':vatStatus::VAT_STATUS' in query and "'PENDING'" in query and not sql_effect(query)['known'])
            if empty: check('empty: old minimum-call safeguard rejects an added invocation', 'may_add_call_at_legal_minimum' in reasons)
            hypothetical = [dict(placement='java', start=0, stop=1), dict(placement='database', start=1, stop=2), dict(placement='java', start=2, stop=3)]
            check(label + ': manual historical layout is not admitted', not validate_plan(contract, hypothetical)['accepted'])
            old_score = selection['objective']
            hypothetical_score = [cost_value(plan_cost(contract, hypothetical), {}), 1, 1]
            check(label + ': hypothetical migration cannot beat original objective even if later legalized', old_score < hypothetical_score)
            diagnostic = dict(status='conditional_cost_only_not_a_legal_plan', original_objective=old_score,
                hypothetical_objective=hypothetical_score, explanation='If semantics became admissible, 1->1 loses the fewer-moved-units tie break; empty 0->1 adds work',
                hypothetical_layout=hypothetical)
            save(label + '-conditional-cost.json', diagnostic)
            renamed = copy.deepcopy(contract); ids={n['id']:f'phase_{i}' for i,n in enumerate(renamed['nodes'])}
            for n in renamed['nodes']: n['id']=ids[n['id']]
            for edge in renamed['edges']: edge['before']=ids[edge['before']];edge['after']=ids[edge['after']]
            other = choose_placement(renamed)
            check(label + ': phase names do not influence selection', [(s['placement'],s['start'],s['stop']) for s in other['segments']] == [(s['placement'],s['start'],s['stop']) for s in selection['segments']])
            conclusions[label] = dict(database_regions=0, actual_rejection_reasons=sorted(reasons), objective=old_score, hypothetical_objective=hypothetical_score)
        save('coverage.json', dict(covered='TicketRepository.reserveTickets at the original ordinary-ticket caller with in-memory attendee supplier: Java preparation, original JDBC batch, Java count fold',
             uncovered=['parent reservation creation flow', 'all additional-field operations', 'price/billing/audit/extension paths', 'full inter-module effect graph and admissible backend catalog'],
             policy='Unknown outside-scope costs are not encoded as zero-cost nodes. No whole-application no-op or optimality claim.',
             frontend_limits=['manual phase grouping', 'original SQL grammar unsupported by frozen first-project parser', 'existing experimental backend not generally admitted'],
             history='previous alf.io manual fusion and timings are engineering controls, not outputs of this shared selector'))
        save('conclusion.json', dict(status='shared_selector_reused_limited_frontend_no_region_selected', projects_tested=['NewOrder','Payment','alf.io'],
            same_algorithm=True, same_objective=True, same_sql_effect_parser=True, alfio=conclusions,
            limitation='one-method integration, not full-system algorithm transfer; do not relabel prior manual fusion as selected output'))
        for path, expected in report['retained_assets'].items(): check('retained evidence unchanged: ' + str(Path(path).relative_to(ROOT)), digest(Path(path)) == expected)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=repr(error)); raise
    finally:
        save('run.json', report)
        manifest={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()};save('manifest.json',manifest)
        print(json.dumps(dict(status=report['status'], output=str(out), checks=len(report['checks']), files=len(manifest))))


if __name__ == '__main__': main()
