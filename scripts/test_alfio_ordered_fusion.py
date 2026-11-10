#!/usr/bin/env python3
"""One isolated candidate: local effect contracts followed by fixed service replay."""
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile
from alfio_baseline import ROOT, WORK, JDK, DOCKER, JAR, SHA, digest
from test_alfio_roundtrip import compare_folders

REPLAY = ROOT / 'evidence/alfio-roundtrip/20261106-17b16f1a21eb'
CONTRACT = ROOT / 'evidence/alfio-batch-contract/20261106-6b686908dc5e'
DECOMPILE = ROOT / 'evidence/alfio-baseline/20261106-788b831dd41c'


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def contract_source(source):
    source = source.replace('AlfioBatchContractProbe', 'AlfioFusionContractProbe')
    source = replace_once(source, 'actual=execute?super.batchUpdate(sql,params):returned;', '''actual=execute && Boolean.getBoolean(AlfioOrderedBatch.ENABLE)
        ? AlfioOrderedBatch.execute(new NamedParameterJdbcTemplate(getJdbcTemplate()),sql,params)
        : execute?super.batchUpdate(sql,params):returned;''')
    source = replace_once(source, 'name.equals("executeBatch"))', 'name.equals("executeBatch")||name.equals("executeQuery"))')
    source = replace_once(source, '        } catch(RuntimeException e) {', '          record.put("stateBeforeRollback",snapshot(c));\n        } catch(RuntimeException e) {')
    source = replace_once(source, '    } finally { save("checks.json",checks); }', '    } finally { save("checks.json",checks);save("dispatch.json",AlfioOrderedBatch.snapshot()); }')
    return source


def main():
    out = ROOT / 'evidence/alfio-ordered-fusion' / ('20261106-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    container_name = 'reversedb-alfio-fusion-' + out.name
    report = dict(status='running', commands=[], inputs={}, retained_assets={}, cleanup_errors=[])
    resources = []
    env = dict(os.environ, ALFIO_DB_PASSWORD='synthetic-alfio-only')

    def save(file, data):
        (out / file).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

    def run(command, log, stdin=None, check=True):
        command = list(map(str, command)); report['commands'].append(command)
        p = subprocess.run(command, cwd=ROOT, env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
        (out / log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Failed: ' + str(out / log))
        return p.stdout.decode(errors='replace').strip()

    def retain(folder, name):
        p = folder / name
        assert digest(p) == json.loads((folder / 'manifest.json').read_text())[name], name
        report['retained_assets'][str(p)] = digest(p)
        return p

    def sql(query, log, database='alfio', user='postgres'):
        return run(DOCKER + ['exec', container_name, 'psql', '-U', user, '-d', database, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], log)

    try:
        assert digest(JAR) == SHA; report['retained_assets'][str(JAR)] = SHA
        fixture = retain(REPLAY, 'fixture.sql'); entropy = retain(REPLAY, 'entropy-agent.jar')
        replay_source = retain(REPLAY, 'generated/AlfioRoundtripProbe.java')
        probe_source = retain(CONTRACT, 'inputs/prototype/alfio/AlfioBatchContractProbe.java')
        repository_source = retain(DECOMPILE, 'decompiled/alfio/repository/TicketRepository.java')
        for name in ['scripts/test_alfio_ordered_fusion.py', 'scripts/alfio_baseline.py', 'scripts/test_alfio_roundtrip.py',
                     'prototype/alfio/AlfioOrderedBatch.java', 'prototype/alfio/AlfioOrderedBatchGuardProbe.java', 'prototype/alfio/ordered_batch.sql',
                     'prototype/alfio/ordered_batch_contract.json', 'work/paperless/images.lock.json']:
            p = ROOT / name; dst = out / 'inputs' / name; dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst); report['inputs'][name] = digest(dst)
        binary = WORK / 'official-binary'; original = binary / 'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs = [binary / json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/', 'BOOT-INF/lib/')) and not member.endswith('/'):
                    assert digest(binary / member) == __import__('hashlib').sha256(jar.read(member)).hexdigest()
        cp = os.pathsep.join(map(str, [original, *libs, entropy]))
        generated = out / 'generated'; generated.mkdir()
        classes = out / 'probe-classes'; classes.mkdir()
        candidate = out / 'candidate-classes'; candidate.mkdir()
        empty = out / 'empty'; empty.mkdir()
        contract = generated / 'AlfioFusionContractProbe.java'; contract.write_text(contract_source(probe_source.read_text()))
        replay = generated / 'AlfioRoundtripProbe.java'
        replay.write_text(replace_once(replay_source.read_text(), '      save("entropy.json",ReplayEntropy.finish());', '''      java.nio.file.Files.writeString(output.resolveSibling(output.getFileName()+"-dispatch.json"),alfio.util.Json.toJson(AlfioOrderedBatch.snapshot()));
      save("entropy.json",ReplayEntropy.finish());'''))
        raw = repository_source.read_text()
        modified = replace_once(raw,
            'this.getNamedParameterJdbcTemplate().batchUpdate(this.batchReserveTickets(), batchReserveParameters)',
            'research.alfio.AlfioOrderedBatch.execute(this.getNamedParameterJdbcTemplate(), this.batchReserveTickets(), batchReserveParameters)')
        repository = generated / 'TicketRepository.java'; repository.write_text(modified)
        (out / 'candidate.diff').write_text(''.join(difflib.unified_diff(raw.splitlines(True), modified.splitlines(True), fromfile='archived-decompiled/TicketRepository.java', tofile='candidate/TicketRepository.java')))
        helper = ROOT / 'prototype/alfio/AlfioOrderedBatch.java'
        run([JDK / 'javac', '--release', '17', '-proc:none', '-implicit:none', '-sourcepath', empty, '-cp', cp, '-d', classes,
             helper, contract, replay, ROOT / 'prototype/alfio/AlfioOrderedBatchGuardProbe.java'], 'compile-probes.log')
        run([JDK / 'javac', '--release', '17', '-proc:none', '-implicit:none', '-sourcepath', empty, '-cp', str(classes) + os.pathsep + cp,
             '-d', candidate, repository], 'compile-candidate.log')
        save('protocol.json', dict(scope='one explicit experimental database entry, no performance measurement',
            change='one call replacement in raw decompiled TicketRepository; all Java parameter construction and final count fold preserved',
            transport='all twelve values in ordered JSON arrays, original metadata string retained; database executes ordered individual UPDATEs and returns integer[]',
            guards='opt-in, exact base template and SQL and parameter ABI, nonempty batch and existing non-autocommit connection; no fallback after candidate SQL starts',
            deployment='isolated pinned JAR/dependencies/database; guards are not general schema or version attestation',
            equality='local return values, Spring exception class/SQLSTATE, full successful pre-rollback public state, all public state after rollback, observed effect order; then 53 exact service replay files',
            expected_boundary_differences=['JDBC wire calls', 'BatchUpdateException count diagnostics become unavailable on function failure; class/SQLSTATE compared, cause/message identity not claimed'],
            stop='one fixed contract pair and service pair; mismatch preserved; no performance or concurrency claims'))
        image = json.loads((ROOT / 'work/paperless/images.lock.json').read_text())['images']['postgres']; report['image'] = image
        assert run(DOCKER + ['image', 'inspect', image['resolved'], '--format', '{{.Id}}'], 'image.log') == image['id']
        run(DOCKER + ['volume', 'create', '--label', 'reversedb.experiment=alfio-ordered-fusion', container_name], 'volume.log'); resources.append('volume')
        run(DOCKER + ['run', '-d', '--pull=never', '--name', container_name, '--label', 'reversedb.experiment=alfio-ordered-fusion',
            '--memory=1536m', '--cpus=2', '--mount', 'type=volume,src=' + container_name + ',dst=/var/lib/postgresql',
            '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD=synthetic-admin-only', image['resolved'],
            '-c', 'timezone=UTC', '-c', 'shared_buffers=256MB', '-c', 'fsync=on', '-c', 'synchronous_commit=on', '-c', 'full_page_writes=on'], 'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER + ['exec', container_name, 'pg_isready', '-U', 'postgres'], f'ready-{i}.log', check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-alfio-only'", 'role.log', 'postgres')
        port = run(DOCKER + ['port', container_name, '5432/tcp'], 'port.log'); assert port.startswith('127.0.0.1:') and '\n' not in port
        url = 'jdbc:postgresql://' + port + '/alfio?sslmode=disable'

        def restore(label):
            sql('DROP DATABASE IF EXISTS alfio', label + '-drop.log', 'postgres')
            sql('CREATE DATABASE alfio OWNER alfio_user', label + '-create.log', 'postgres')
            with fixture.open('rb') as stream:
                run(DOCKER + ['exec', '-i', container_name, 'psql', '-U', 'alfio_user', '-d', 'alfio', '-X', '-v', 'ON_ERROR_STOP=1'], label + '-restore.log', stdin=stream)
            # Both arms have the identical function schema; only candidate invokes it.
            with (ROOT / 'prototype/alfio/ordered_batch.sql').open('rb') as stream:
                run(DOCKER + ['exec', '-i', container_name, 'psql', '-U', 'alfio_user', '-d', 'alfio', '-X', '-v', 'ON_ERROR_STOP=1'], label + '-install.log', stdin=stream)

        for arm in ['original', 'candidate']:
            label = 'contract-' + arm; restore(label)
            run([JDK / 'java', '-Xmx512m', '-XX:ActiveProcessorCount=2', '-Dreversedb.alfio.orderedBatch=' + str(arm == 'candidate').lower(),
                 '-cp', str(classes) + os.pathsep + cp, 'research.alfio.AlfioFusionContractProbe', out / label, url], label + '.log')
            print(label + ' passed', flush=True)
        left = json.loads((out / 'contract-original/database-cases.json').read_text())
        right = json.loads((out / 'contract-candidate/database-cases.json').read_text())
        differences = []; boundary_differences = []
        for a, b in zip(left, right):
            assert a['case'] == b['case']
            keys = set(a) | set(b)
            for key in keys:
                if a.get(key) != b.get(key):
                    target = boundary_differences if key in {'wire', 'batchExceptionCounts'} else differences
                    target.append(dict(case=a['case'], field=key))
        local = dict(equal=not differences, cases=len(left), differences=differences, boundary_differences=boundary_differences)
        save('contract-comparison.json', local); assert len(left) == len(right) == 8 and local['equal'], local
        for name in ['binding-abi.json', 'name-error.json', 'serialization-error.json', 'synthetic-count-fold.json', 'checks.json', 'initial-state.json']:
            assert json.loads((out / 'contract-original' / name).read_text()) == json.loads((out / 'contract-candidate' / name).read_text()), name
        dispatch = json.loads((out / 'contract-candidate/dispatch.json').read_text())
        assert sum(e['path'] == 'candidate-attempt' for e in dispatch) == 6, dispatch
        assert sum(e['path'] == 'candidate-return' for e in dispatch) == 4, dispatch
        assert sum(e['path'] == 'fallback:empty' for e in dispatch) == 1, dispatch
        for case in right:
            if case['case'] in {'length-error-second', 'foreign-key-error'}:
                methods = [e.get('method') for e in case['wire']]
                assert methods.count('executeQuery') == 1 and 'executeBatch' not in methods, case['case']
        run([JDK / 'java', '-Xmx512m', '-XX:ActiveProcessorCount=2', '-cp', str(classes) + os.pathsep + cp,
             'research.alfio.AlfioOrderedBatchGuardProbe', url, out / 'guard-cases.json'], 'guards.log')
        guards = json.loads((out / 'guard-cases.json').read_text())
        assert len(guards) == 7 and all(g['stateUnchanged'] for g in guards)
        for arm in ['original', 'candidate']:
            restore(arm)
            path = os.pathsep.join(map(str, [classes, *([candidate] if arm == 'candidate' else []), original, *libs, entropy]))
            run([JDK / 'java', '-Xmx1024m', '-XX:ActiveProcessorCount=2', '-Duser.timezone=UTC', '-Dreversedb.alfio.orderedBatch=' + str(arm == 'candidate').lower(),
                 '-javaagent:' + str(entropy), '-Xlog:class+load=info:file=' + str(out / (arm + '-classes.log')), '-cp', path,
                 'research.alfio.AlfioRoundtripProbe', url, out / arm, 'replay'], arm + '.log')
            loads = (out / (arm + '-classes.log')).read_text()
            line = next(l for l in loads.splitlines() if 'alfio.repository.TicketRepository source:' in l)
            assert str(candidate if arm == 'candidate' else original) in line
            print('Service replay ' + arm + ' passed', flush=True)
        comparison = compare_folders(out / 'original', out / 'candidate'); save('comparison.json', comparison)
        assert comparison['equal'] and comparison['compared_files'] == 53, comparison
        historical = compare_folders(REPLAY / 'original', out / 'original'); save('historical-comparison.json', historical); assert historical['equal'], historical
        dispatch = json.loads((out / 'candidate-dispatch.json').read_text())
        assert [e['rows'] for e in dispatch if e['path'] == 'candidate-attempt'] == [3, 2, 1], dispatch
        assert [e['rows'] for e in dispatch if e['path'] == 'candidate-return'] == [3, 2, 1], dispatch
        assert len(dispatch) == 6 and json.loads((out / 'original-dispatch.json').read_text()) == []
        save('function-definition.json', json.loads(sql("select json_build_object('security_definer',prosecdef,'volatile',provolatile,'definition',pg_get_functiondef(oid)) from pg_proc where oid='rdb_experiment.reserve_ticket_batch(jsonb)'::regprocedure", 'function.log')))
        for p, sha in report['retained_assets'].items(): assert digest(Path(p)) == sha
        report.update(status='passed', contract_cases=8, contract_checks_each=58, guard_cases=7, replay_files=53, service_candidate_rows=[3,2,1],
                      candidate_status='passed_bounded_experiment_not_general_admission')
    except BaseException as error:
        report.update(status='failed', error=repr(error)); raise
    finally:
        for resource in reversed(resources):
            try:
                if resource == 'container':
                    run(DOCKER + ['logs', container_name], 'postgres.log', check=False)
                    run(DOCKER + ['rm', '-f', '-v', container_name], 'cleanup-container.log')
                else: run(DOCKER + ['volume', 'rm', container_name], 'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status'] = 'failed'
        save('run.json', report)
        manifest = {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*')) if p.is_file()}
        save('manifest.json', manifest)
        assert all(digest(out / p) == sha for p, sha in manifest.items())
        print(json.dumps(dict(status=report['status'], output=str(out), files=len(manifest))), flush=True)
    if report['status'] != 'passed': raise RuntimeError('Experiment incomplete')


if __name__ == '__main__':
    main()
