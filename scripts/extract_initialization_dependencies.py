#!/usr/bin/env python3
"""One bounded constructor/initialization extraction with structural checks only."""
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from app_db_smoke import ROOT, JDK, ARCHIVE, LIB, digest

sys.path.insert(0, str(ROOT / 'prototype/module_pruning'))
from call_dag import condense, from_extraction


def main():
    baseline = ROOT / 'evidence/module-extraction/20261104-f2652809967a'
    old = json.loads((baseline / 'extracted.json').read_text())
    manifest = json.loads((baseline / 'manifest.json').read_text())
    source_paths = sorted({Path(m['location']['file']) for m in old['methods']
        if m['symbol'].split('#')[0] in {'com.oltpbenchmark.api.TpccApp',
            'com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus'}})
    for path in [*source_paths, baseline / 'bridge-contract.json', baseline / 'extracted.json']:
        assert digest(path) == manifest[str(path.relative_to(baseline))], path
    retained = json.loads((ARCHIVE / 'retained-assets.json').read_text())
    assets_manifest = json.loads((ARCHIVE / 'manifest.json').read_text())
    assets = [ARCHIVE/'bundle/reference.jar', *sorted((ARCHIVE/'bundle/candidate-classes').rglob('*.class')),
              *sorted(LIB.glob('*.jar'))]
    asset_hashes = {}
    for path in assets:
        expected = assets_manifest[str(path.relative_to(ARCHIVE))] if path.is_relative_to(ARCHIVE) else retained[str(path)]
        assert digest(path) == expected, path
        asset_hashes[str(path)] = expected
    out = ROOT/'evidence/module-initialization'/('20261105-'+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report = {'status': 'running', 'checks': [], 'commands': [], 'inputs': {},
              'retained_assets': asset_hashes, 'application_runs': 0, 'database_runs': 0, 'deletions': 0}

    def freeze(path):
        target = out/'inputs'/path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        report['inputs'][str(path.relative_to(ROOT))] = digest(target)
        return target

    def run(command, logfile):
        report['commands'].append(list(map(str, command)))
        with (out/logfile).open('w') as stream:
            result = subprocess.run(list(map(str, command)), stdout=stream, stderr=subprocess.STDOUT, timeout=60)
        if result.returncode:
            raise RuntimeError('Command failed: '+str(out/logfile))

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)

    try:
        sources = [freeze(p) for p in source_paths]
        fixture = freeze(ROOT/'prototype/module_pruning/InitializationFixture.java')
        extractor = freeze(ROOT/'prototype/module_pruning/DependencyExtract.java')
        bridge = freeze(baseline/'bridge-contract.json')
        shutil.copy2(bridge, out/'bridge-contract.json')
        for path in [Path(__file__), ROOT/'scripts/app_db_smoke.py',
                     ROOT/'prototype/module_pruning/call_dag.py', ROOT/'prototype/module_pruning/test_call_dag.py',
                     baseline/'manifest.json', ARCHIVE/'manifest.json', ARCHIVE/'retained-assets.json']:
            freeze(path)
        classes = out/'extractor-classes'; classes.mkdir()
        jsonjar = LIB/'json-20240303.jar'
        run([JDK/'javac', '--release', '23', '-proc:none', '-cp', jsonjar, '-d', classes, extractor], 'compile.log')
        cp = ':'.join(map(str, [ARCHIVE/'bundle/reference.jar', ARCHIVE/'bundle/candidate-classes', *sorted(LIB.glob('*.jar'))]))
        run([JDK/'java', '-cp', str(classes)+':'+str(jsonjar), 'research.DependencyExtract',
             out/'extracted.json', cp, bridge, *sources, fixture], 'extract.log')
        data = json.loads((out/'extracted.json').read_text())
        fixture_calls = [c for c in data['calls'] if c['location']['file']==str(fixture)]
        def calls(owner, kind):
            return [c for c in fixture_calls if owner in c['caller'] and c.get('call_kind')==kind]
        check('new_resolves_noarg_constructor', any(c['target_symbol'].endswith('Box#Box()') for c in calls('#allocate(', 'new_class')))
        check('new_resolves_overloaded_constructor', any(c['target_symbol'].endswith('Box#Box(int)') for c in calls('#overloaded(', 'new_class')))
        check('this_delegation', any(c['target_symbol'].endswith('Box#Box(int)') for c in calls('Box#Box()', 'constructor_chain')))
        check('super_delegation', any(c['target_symbol'].endswith('Base#Base(int)') for c in calls('Box#Box(int)', 'constructor_chain')))
        check('implicit_constructor_target', any(c['target_symbol'].endswith('Implicit#Implicit()') for c in calls('#implicit(', 'new_class')))
        references = calls('#deferred(', 'constructor_reference_may_target')
        check('deferred_constructor_keeps_activation_unknown', len(references)==1 and references[0]['activation_unknown'] and references[0]['target_symbol'].endswith('Box#Box()'))
        for name in ('anonymous', 'local'):
            check(name+'_construction_unknown', any(not c['construction_target_complete'] for c in calls('#'+name+'(', 'new_class')))
        check('static_block_owned_by_clinit', any(c['caller'].endswith('Box#<clinit>') and c['target_symbol'].endswith('Box#touch()') for c in fixture_calls))
        check('instance_block_owned_by_instance_init', any(c['caller'].endswith('Box#<instance-init>') and c['target_symbol'].endswith('Box#touch()') for c in fixture_calls))
        nested_outer = calls('#anonymous(', 'new_class') + calls('#local(', 'new_class')
        check('nested_initialization_not_outer_method', not any(c['target_symbol']=='java.lang.Object#Object()' for c in nested_outer))
        roots = ['com.oltpbenchmark.api.TpccApp#main(java.lang.String[])']
        graph = from_extraction(data, list(map(str, sources)), roots)
        dag = condense(graph)
        ctor = 'com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus#OrderStatus()'
        field = 'com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus#customerByNameSQL'
        check('actual_orderstatus_constructor_reachable', ctor in dag['reachability_witnesses'])
        check('sql_definition_retained_by_initialization', field in dag['reachability_witnesses'])
        app_ctor = 'com.oltpbenchmark.api.TpccApp#TpccApp()'
        check('static_main_does_not_imply_app_construction', app_ctor not in dag['reachability_witnesses'])
        check('fixture_not_in_business_graph', not any('InitializationFixture' in n['id'] for n in graph['nodes']))
        terminal = next(m['symbol'] for m in data['methods'] if '#run(' in m['symbol'])
        graph2 = from_extraction(data, list(map(str, sources)), roots+[terminal])
        dag2 = condense(graph2)
        customer_name = next(m['symbol'] for m in data['methods'] if '#getCustomerByName(' in m['symbol'])
        check('extra_terminal_root_retains_name_lookup', customer_name not in dag['reachability_witnesses'] and customer_name in dag2['reachability_witnesses'])
        check('extra_root_does_not_lose_retained_nodes', set(dag['reachability_witnesses']) <= set(dag2['reachability_witnesses']))
        check('still_no_deletion_authorization', not dag['deletion_authorized'] and not dag2['deletion_authorized'])
        root_review = {'source': 'explicit_reviewed_scope_not_automatic_entry_discovery',
            'application': {'roots': graph['roots'], 'out_of_path_internal': [n['id'] for n in graph['nodes'] if n['kind'] in ('method','sql_field') and n['id'] not in dag['reachability_witnesses']]},
            'application_and_terminal': {'roots': graph2['roots'], 'out_of_path_internal': [n['id'] for n in graph2['nodes'] if n['kind'] in ('method','sql_field') and n['id'] not in dag2['reachability_witnesses']]},
            'entry_inventory_complete': False, 'unclosed': ['external framework callbacks and reflective entry discovery', 'external superclass and transaction bodies', 'anonymous/local class identity and deferred activation'],
            'static_initializers': 'Conservatively rooted for each selected declared class; not proven to execute.'}
        (out/'entry-review.json').write_text(json.dumps(root_review,indent=2)+'\n')
        run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', out/'inputs/prototype/module_pruning', '-p', 'test_call_dag.py', '-v'], 'dag-tests.log')
        report.update(status='passed', structural_checks=len(report['checks']), dag_unit_tests=6,
                      constructor_calls=sum(c.get('call_kind') in ('new_class','constructor_chain','constructor_reference_may_target') for c in data['calls']),
                      dependency_summary_complete=False, deletion_authorized=False)
        for path, expected in asset_hashes.items():
            assert digest(Path(path))==expected
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (out/'manifest.json').write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
        print(json.dumps({'status':report['status'],'archive':str(out),'checks':len(report['checks'])}))


if __name__=='__main__':
    main()
