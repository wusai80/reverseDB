#!/usr/bin/env python3
"""One real decompiled method, three arms, the existing frozen 62-command suite.

No data pruning, workload search, performance claim, or automatic deletion proof.
"""
import difflib
import json
import os
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest

TABLES = ('warehouse', 'district', 'customer', 'item', 'stock', 'oorder', 'new_order', 'order_line', 'history')
SCENARIOS = dict(baseline=(10, 1, 0), holdout=(10, 3, 4), transaction_edges=(23, 0, 4), delivery_edges=(7, 0, 0))
ARMS = ('original', 'profile', 'pruned')
MEMBER = 'com/oltpbenchmark/benchmarks/tpcc/procedures/OrderStatus.class'


def variants(source):
    start = source.index('   @Override\n   public void run(')
    end = source.index('   private Oorder getOrderDetails', start)
    signature = source[start:source.index('{', start) + 1]
    profile = source[:start] + signature + '\n      throw new UnsupportedOperationException("Application-adapter profile excludes BenchBase run");\n   }\n\n' + source[end:]
    start = profile.index('   public Customer getCustomerByName(')
    # The hash-verified decompiled method is last in this class.
    assert profile.endswith('   }\n}\n')
    pruned = profile[:start] + '}\n'
    assert 'getCustomerByName' not in pruned
    assert source[:source.index('   @Override')] == pruned[:pruned.index('   @Override')]
    return {'profile': profile, 'pruned': pruned}


def remove_display(source, adapter):
    """Keep SQL initialization; remove just the discarded method and call."""
    start = source.index('   private List<String> getOrderLines(')
    end = source.index('   public Customer getCustomerById(', start)
    call = '        invoke(p,"getOrderLines",c,1,d,order.o_id,cust);\n'
    assert adapter.count(call) == 1
    return source[:start] + source[end:], adapter.replace(call, '')


def project_display_trace(trace, commands, responses, display_sql, transaction='OrderStatus', statuses=('committed',)):
    """Remove only the contiguous, exact-SQL display operation and its row events.

    This diagnostic projection is not an observation-equivalence claim for SQL
    observers. All retained events are compared verbatim, in their original order.
    """
    command_map = {command['id']: command for command in commands}
    status_map = {response['id']: response['status'] for response in responses}
    projected, removed = [], []
    for item in trace:
        events, skipped, active, calls = [], [], False, 0
        for event in item['events']:
            if 'sql' in event:
                active = event['sql'] == display_sql
            elif event['op'] in ('commit', 'rollback'):
                active = False
            if active:
                assert 'sql' in event or event['op'] in ('next', 'wasNull') or (
                    event['op'].startswith('get') and 'column' in event and 'value' in event)
                calls += event['op'] == 'executeQuery'
                skipped.append(event)
            else:
                events.append(event)
        expected = command_map[item['id']]['type'] == transaction and status_map[item['id']] in statuses
        assert calls == int(expected), (item['id'], calls)
        projected.append({**item, 'events': events})
        if skipped:
            removed.append(dict(id=item['id'], queries=calls, events=skipped))
    assert sum(item['queries'] for item in removed) == sum(
        command['type'] == transaction and status_map[command['id']] in statuses for command in commands)
    return projected, removed


def main(combined=False, warehouse=False):
    assert not (combined and warehouse)
    arms = ('original', 'warehouse') if warehouse else (('original', 'combined') if combined else ARMS)
    category = 'module-warehouse-pruning' if warehouse else ('module-combined-pruning' if combined else 'module-name-pruning')
    procedure_name = 'NewOrder' if warehouse else 'OrderStatus'
    member = f'com/oltpbenchmark/benchmarks/tpcc/procedures/{procedure_name}.class'
    out = ROOT / 'evidence' / category / ('20261105-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    container = 'reversedb-name-' + out.name
    report = dict(status='running', container=container, commands=[], inputs={}, retained_assets={}, cases={}, cleanup_errors=[])
    created = False
    env = dict(os.environ, TPCC_PASSWORD='synthetic-tpcc-only', TPCC_OBSERVE='0')

    def save(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def run(command, log, stdin=None, check=True, timeout=180):
        command = list(map(str, command))
        report['commands'].append(command)
        result = subprocess.run(command, cwd=ROOT, env=env, stdin=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        (out / log).write_bytes(result.stdout)
        if check and result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}): {out / log}')
        return result.stdout.decode()

    def freeze(path):
        destination = out / 'inputs' / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        report['inputs'][str(path.relative_to(ROOT))] = digest(destination)
        return destination

    def sql(database, query, log):
        return run(DOCKER + ['exec', container, 'psql', '-U', 'tpcc', '-d', database,
                            '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], log).strip()

    def state(database, folder, phase):
        values = {}
        for table in TABLES:
            query = f"""SELECT json_build_object('rows',count(*), 'sha256',
              encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE "C"),''),'UTF8')),'hex'))
              FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {table} t) rows"""
            values[table] = json.loads(sql(database, query, f'{folder}/{phase}-{table}.log'))
        save(out / folder / (phase + '-state.json'), values)

    try:
        bundle = ARCHIVE / 'bundle'
        manifest = json.loads((ARCHIVE / 'manifest.json').read_text())
        retained = json.loads((ARCHIVE / 'retained-assets.json').read_text())
        source = bundle / f'sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/{procedure_name}.java'
        scenarios = ARCHIVE / 'run/inputs/app/tpcc/scenarios'
        invariants = ARCHIVE / 'run/inputs/prototype/tpcc/invariants.sql'
        archived = [source, bundle / 'reference.jar', invariants, *scenarios.glob('*'),
                    *sorted((bundle / 'reference-adapter').rglob('*.class')),
                    *sorted((bundle / 'candidate-classes').rglob('*.class'))]
        dependencies = [*sorted(LIB.glob('*.jar')), ROOT / 'work/tpcc/runs/c615d3357989/initial.dump']
        for path in archived:
            assert digest(path) == manifest[str(path.relative_to(ARCHIVE))], path
        for path in dependencies:
            assert digest(path) == retained[str(path)], path
        for path in [*archived, *dependencies]:
            report['retained_assets'][str(path)] = digest(path)
        probe_name = 'WarehouseBoundaryProbe' if warehouse else 'NameLookupBoundaryProbe'
        harness = ROOT / 'prototype/module_pruning' / (probe_name + '.java')
        lock = ROOT / 'work/paperless/images.lock.json'
        observation = ROOT / 'evidence/tpcc-observation/20261103-8729d2283fdc'
        adapter_source = observation / 'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
        assert digest(adapter_source) == json.loads((observation / 'manifest.json').read_text())[str(adapter_source.relative_to(observation))]
        extra_inputs = [ROOT / 'scripts/test_combined_module_pruning.py', ROOT / 'prototype/module_pruning/protocol.json'] if combined else []
        if warehouse:
            from test_warehouse_pruning import screen
            screening = screen(); save(out / 'screening.json', screening)
            extra_inputs = [ROOT / 'scripts/test_warehouse_pruning.py', *map(Path,screening['source_files'])]
            report['retained_assets'].update(screening['trace_files'])
        for path in [Path(__file__).resolve(), ROOT / 'scripts/app_db_smoke.py', harness, source, lock, *extra_inputs,
                     adapter_source, observation / 'manifest.json',
                     ARCHIVE / 'manifest.json', ARCHIVE / 'retained-assets.json', invariants, *scenarios.glob('*')]:
            freeze(path)
        report['protocol'] = dict(
            roots=['com.oltpbenchmark.api.TpccApp.main'],
            retained='All five existing application transactions and their unchanged input validation; all database rows and fields',
            excluded=['BenchBase terminal run', 'arbitrary external reflective entry/metadata observers', 'resource exhaustion and timing'],
            module='OrderStatus.getCustomerByName method only; SQL field and initialization retained',
            arms=list(arms), scenarios=list(SCENARIOS), commands_per_arm=62,
            sequence='Existing frozen command order, clocks and terminal assignments; each scenario restored independently',
            observations=['responses', 'per-command observations', 'full nine-table state', 'invariants', 'JDBC trace'],
            analysis='Manual source/bytecode boundary review, not an automatic whole-program proof',
            stop='One fixed run; no tuning, new data generation, or performance measurements')
        if combined:
            report['protocol'].update(
                module='getCustomerByName + getOrderLines methods and discarded adapter call; all four SQL fields retained',
                environment='Existing serial snapshot profile; removed display query and formatter complete normally, with no observable read effects',
                observations=['responses', 'per-command observations', 'full nine-table state', 'invariants'],
                diagnostics=['exact removed-query trace segment', 'unchanged remaining JDBC events', 'query-fault counterexample'],
                universal_fault_equivalence=False,
                composition='Intersection of prior contracts; two existing candidates, no new discovery',
                excluded=report['protocol']['excluded'] + ['SQL/trace diagnostics as public behavior', 'faults and extra side effects of removed display query'])
        if warehouse:
            report['protocol'].update(
                module='NewOrder.getWarehouse and its single internal call only; all ten SQL fields retained',
                environment='Same serializable snapshot; preceding getCustomer succeeds with identical w_id; validated enforced immediate customer→district→warehouse FKs; ordinary tables without RLS; normal removed-query completion and no extra read effects',
                observations=['responses','per-command observations','full nine-table state','invariants'],
                diagnostics=['schema eligibility and missing-FK rejection','exact removed-query trace segment','query-fault counterexample','retained customer error'],
                universal_fault_equivalence=False,
                excluded=['Removed-query faults and observable read effects','arbitrary external reflective entry/metadata observers','resource exhaustion and timing','concurrency outside the serial profile'],
                analysis='Manual FK implication and source-order argument with runtime schema gates; not automatic theorem discovery')
        save(out / 'protocol.json', report['protocol'])
        libs = ':'.join(map(str, sorted(LIB.glob('*.jar'))))
        base_cp = str(bundle / 'candidate-classes') + ':' + libs
        compile_cp = str(bundle / 'reference.jar') + ':' + base_cp
        empty = out / 'empty-sourcepath'; empty.mkdir()
        jars = {'original': bundle / 'reference.jar'}
        adapters = {arm: bundle / 'reference-adapter' for arm in arms}
        if warehouse:
            text = source.read_text()
            call = '      this.getWarehouse(conn, w_id);\n'
            assert text.count(call) == 1
            start = text.index('   private void getWarehouse(')
            end = text.index('   private void getCustomer(', start)
            reduced = (text[:start] + text[end:]).replace(call,'')
            assert 'getWarehouse(' not in reduced and 'stmtGetWhseSQL' in reduced
            source_variants = {'warehouse': reduced}
        else:
            source_variants = variants(source.read_text())
        combined_adapter = None
        if combined:
            text, combined_adapter = remove_display(source_variants['pruned'], adapter_source.read_text())
            display_first, adapter_first = remove_display(source.read_text(), adapter_source.read_text())
            assert variants(display_first)['pruned'] == text and adapter_first == combined_adapter
            source_variants = {'combined': text}
            report['composition_source_order_equal'] = True
        for arm, text in source_variants.items():
            folder = out / arm; folder.mkdir()
            generated = folder / (procedure_name + '.java'); generated.write_text(text)
            (folder / 'source.diff').write_text(''.join(difflib.unified_diff(source.read_text().splitlines(True), text.splitlines(True), fromfile='original', tofile=arm)))
            classes = folder / 'classes'; classes.mkdir()
            run([JDK / 'javac', '--release', '23', '-proc:none', '-implicit:none', '-sourcepath', empty,
                 '-cp', compile_cp, '-d', classes, generated], arm + '/compile.log')
            assert [p.relative_to(classes).as_posix() for p in classes.rglob('*.class')] == [member]
            jars[arm] = folder / 'reference.jar'
            with zipfile.ZipFile(jars['original']) as original, zipfile.ZipFile(jars[arm], 'w', compression=zipfile.ZIP_DEFLATED) as changed:
                assert len(original.namelist()) == len(set(original.namelist()))
                for item in original.infolist():
                    changed.writestr(item, (classes / member).read_bytes() if item.filename == member else original.read(item))
            with zipfile.ZipFile(jars['original']) as original, zipfile.ZipFile(jars[arm]) as changed:
                assert original.namelist() == changed.namelist()
                assert [n for n in original.namelist() if original.read(n) != changed.read(n)] == [member]
            if arm == 'combined':
                adapter_java = folder / 'TpccApp.java'; adapter_java.write_text(combined_adapter)
                adapters[arm] = folder / 'adapter-classes'; adapters[arm].mkdir()
                (folder / 'adapter.diff').write_text(''.join(difflib.unified_diff(
                    adapter_source.read_text().splitlines(True), combined_adapter.splitlines(True), fromfile='original', tofile='combined')))
                run([JDK / 'javac', '--release', '23', '-proc:none', '-implicit:none', '-sourcepath', empty,
                     '-cp', str(jars[arm]) + ':' + base_cp, '-d', adapters[arm], adapter_java], arm + '/compile-adapter.log')
        if not combined and not warehouse:
            (out / 'profile-to-pruned.diff').write_text(''.join(difflib.unified_diff(
                (out / 'profile/OrderStatus.java').read_text().splitlines(True),
                (out / 'pruned/OrderStatus.java').read_text().splitlines(True), fromfile='profile', tofile='pruned')))
        probe_classes = out / 'probe-classes'; probe_classes.mkdir()
        run([JDK / 'javac', '--release', '23', '-proc:none', '-implicit:none', '-sourcepath', empty,
             '-cp', compile_cp, '-d', probe_classes, harness], 'compile-probe.log')
        for arm, jar in jars.items():
            run([JDK / 'javap', '-c', '-p', '-cp', str(jar) + ':' + libs,
                 'com.oltpbenchmark.benchmarks.tpcc.procedures.' + procedure_name], arm + '-bytecode.txt')
        run([JDK / 'javap', '-c', '-p', '-cp', compile_cp, 'com.oltpbenchmark.api.Procedure'], 'procedure-bytecode.txt')
        run([JDK / 'javap', '-c', '-p', '-cp', str(bundle / 'reference-adapter') + ':' + compile_cp,
             'com.oltpbenchmark.api.TpccApp'], 'adapter-bytecode.txt')
        report['class_bytes'] = {}
        for arm, jar in jars.items():
            with zipfile.ZipFile(jar) as archive:
                report['class_bytes'][arm] = len(archive.read(member))
        print(f'Built {len(arms)} real JAR arms; starting the fixed application suite', flush=True)
        image = json.loads(lock.read_text())['images']['postgres']
        assert run(DOCKER + ['image', 'inspect', image['resolved'], '--format', '{{.Id}}'], 'image.log').strip() == image['id']
        report['image'] = image
        run(DOCKER + ['run', '-d', '--pull=never', '--name', container, '--label', 'reversedb.experiment=name-pruning',
                     '--memory=1536m', '--cpus=2', '--tmpfs', '/var/lib/postgresql:rw,size=1024m',
                     '-p', '127.0.0.1::5432', '-e', 'POSTGRES_USER=tpcc', '-e', 'POSTGRES_PASSWORD=synthetic-tpcc-only',
                     image['resolved'], '-c', 'timezone=UTC'], 'start.log')
        created = True
        for attempt in range(40):
            if 'accepting connections' in run(DOCKER + ['exec', container, 'pg_isready', '-U', 'tpcc'], f'ready-{attempt}.log', check=False):
                break
            time.sleep(.5)
        else:
            raise RuntimeError('PostgreSQL readiness timeout')
        port = run(DOCKER + ['port', container, '5432/tcp'], 'port.log').strip()
        assert port.startswith('127.0.0.1:') and '\n' not in port
        for scenario, expected in SCENARIOS.items():
            for arm in arms:
                key = scenario + '_' + arm
                (out / key).mkdir()
                sql('tpcc', 'CREATE DATABASE experiment', key + '/create.log')
                with dependencies[-1].open('rb') as dump:
                    run(DOCKER + ['exec', '-i', container, 'pg_restore', '--exit-on-error', '-U', 'tpcc', '-d', 'experiment'], key + '/restore.log', stdin=dump)
                if scenario == 'delivery_edges':
                    sql('experiment', (scenarios / 'delivery_edges.sql').read_text(), key + '/fixture.log')
                state('experiment', key, 'initial')
                url = f'jdbc:postgresql://{port}/experiment?sslmode=disable&reWriteBatchedInserts=true'
                cp = str(jars[arm]) + ':' + base_cp
                if scenario == 'baseline' or warehouse:
                    run([JDK / 'java', '-Duser.timezone=UTC', '-cp', str(probe_classes) + ':' + str(adapters[arm]) + ':' + cp,
                         'research.' + probe_name, url, arm, out / key / 'boundary.json',
                         *(['display-boundary'] if combined else [])], key + '/boundary.log')
                    if warehouse:
                        state('experiment', key, 'post-probe')
                        assert json.loads((out/key/'initial-state.json').read_text()) == json.loads((out/key/'post-probe-state.json').read_text())
                run([JDK / 'java', '-Duser.timezone=UTC', '-Xmx512m', '-XX:ActiveProcessorCount=2',
                     f'-Xlog:class+load=info:file={out / key / "class-load.log"}',
                     '-cp', str(adapters[arm]) + ':' + cp,
                     'com.oltpbenchmark.api.TpccApp', url, 'tpcc', scenarios / (scenario + '.json'), out / key, 'reference'], key + '/replay.log')
                loaded = [line for line in (out / key / 'class-load.log').read_text().splitlines()
                          if 'com.oltpbenchmark.benchmarks.tpcc.procedures.' + procedure_name + ' source:' in line]
                assert len(loaded) == 1 and Path(unquote(urlparse(loaded[0].split('source: ', 1)[1]).path)) == jars[arm], loaded
                adapter_loaded = [line for line in (out / key / 'class-load.log').read_text().splitlines()
                                  if 'com.oltpbenchmark.api.TpccApp source:' in line]
                assert len(adapter_loaded) == 1 and Path(unquote(urlparse(adapter_loaded[0].split('source: ', 1)[1]).path)) == adapters[arm]
                state('experiment', key, 'final')
                values = json.loads(sql('experiment', invariants.read_text(), key + '/invariants.log'))
                assert len(values) == 5 and not any(values.values())
                save(out / key / 'invariants.json', values)
                responses = json.loads((out / key / 'responses.json').read_text())
                commands = json.loads((scenarios / (scenario + '.json')).read_text())['commands']
                assert [r['id'] for r in responses] == [c['id'] for c in commands]
                counts = tuple(sum(r['status'] == status for r in responses) for status in ('committed', 'aborted', 'rejected'))
                assert counts == expected, (key, counts)
                report['cases'][key] = dict(commands=len(commands), outcomes=counts)
                sql('tpcc', 'DROP DATABASE experiment', key + '/drop.log')
                print('Completed ' + key, flush=True)
            filenames = ('responses.json', 'observations.json', 'initial-state.json', 'final-state.json', 'invariants.json')
            for filename in filenames + (() if combined or warehouse else ('jdbc-trace.json',)):
                values = [json.loads((out / (scenario + '_' + arm) / filename).read_text()) for arm in arms]
                assert all(value == values[0] for value in values), (scenario, filename)
            if combined or warehouse:
                registry = json.loads((out / 'baseline_original/boundary.json').read_text())['sql_registry']
                display_sql = ' '.join(registry['stmtGetWhseSQL' if warehouse else 'ordStatGetOrderLinesSQL'].split())
                original_trace = json.loads((out / (scenario + '_original/jdbc-trace.json')).read_text())
                projected, removed = project_display_trace(original_trace, commands, responses, display_sql,
                    transaction='NewOrder' if warehouse else 'OrderStatus', statuses=('committed','aborted') if warehouse else ('committed',))
                combined_trace = json.loads((out / (scenario + '_' + arms[1] + '/jdbc-trace.json')).read_text())
                assert projected == combined_trace, scenario
                save(out / (scenario + '-removed-query-events.json'), removed)
                report.setdefault('query_reductions', {})[scenario] = sum(item['queries'] for item in removed)
        boundaries = {arm: json.loads((out / ('baseline_' + arm) / 'boundary.json').read_text()) for arm in arms}
        assert all(boundaries[arm]['sql_registry'] == boundaries['original']['sql_registry'] for arm in arms)
        for arm in arms:
            assert Path(unquote(urlparse(boundaries[arm]['class_origin']).path)) == jars[arm]
        report['boundary_witnesses'] = boundaries
        for path, sha in report['retained_assets'].items():
            assert digest(Path(path)) == sha
        if combined:
            original_probe = boundaries['original']; combined_probe = boundaries['combined']
            assert original_probe['normal_display_probe']['response'] == combined_probe['normal_display_probe']['response']
            assert original_probe['normal_display_probe']['queries'] == 5 and combined_probe['normal_display_probe']['queries'] == 4
            assert original_probe['normal_display_probe']['display_queries'] == 1 and combined_probe['normal_display_probe']['display_queries'] == 0
            assert original_probe['fault_display_probe']['status'] == 'error' and original_probe['fault_display_probe']['sql_state'] == '58000'
            assert combined_probe['fault_display_probe']['status'] == 'ok'
            assert combined_probe['fault_display_probe']['response'] == combined_probe['normal_display_probe']['response']
            assert sum(report['query_reductions'].values()) == 7
        if warehouse:
            for scenario in SCENARIOS:
                original_probe = json.loads((out / (scenario + '_original/boundary.json')).read_text())
                candidate_probe = json.loads((out / (scenario + '_warehouse/boundary.json')).read_text())
                assert original_probe['normal_probe']['response'] == candidate_probe['normal_probe']['response']
                assert original_probe['normal_probe']['queries'] == candidate_probe['normal_probe']['queries'] + 1
                assert original_probe['normal_probe']['warehouse_queries'] == 1 and candidate_probe['normal_probe']['warehouse_queries'] == 0
                assert original_probe['warehouse_fault_probe']['status'] == 'error' and candidate_probe['warehouse_fault_probe']['status'] == 'ok'
                assert original_probe['retained_customer_fault_probe'] == candidate_probe['retained_customer_fault_probe']
                assert original_probe['retained_customer_fault_probe']['status'] == 'error'
                assert candidate_probe['schema_admission']['eligible'] and not candidate_probe['missing_fk_control']['eligible']
            assert sum(report['query_reductions'].values()) == 14
        report.update(status='passed', total_commands=62 * len(arms), comparisons=20 if combined or warehouse else 24,
                      conclusion=('Two method deletions composed under the normal-completion application contract; seven display queries removed; fault equivalence false; no latency claim'
                                  if combined else 'Application profile matched; outside-profile name capability intentionally differs; no speedup claim'))
        if warehouse:
            report['conclusion'] = 'One new executed module removed under a validated FK/snapshot implication; fourteen warehouse checks removed; query-fault equivalence false; no latency claim'
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        if created:
            try:
                run(DOCKER + ['logs', container], 'postgres.log', check=False)
                run(DOCKER + ['rm', '-f', '-v', container], 'cleanup.log')
            except Exception as error:
                report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:
            report['status'] = 'failed'
        save(out / 'run.json', report)
        save(out / 'manifest.json', {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'], output=str(out))), flush=True)
    if report['status'] != 'passed':
        raise RuntimeError('Experiment failed')


if __name__ == '__main__':
    main()
