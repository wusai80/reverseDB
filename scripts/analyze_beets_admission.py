#!/usr/bin/env python3
"""Separate deployment, execution-domain, and lowering obligations.

Manual source-bound interpretation of retained evidence, not a classifier,
equivalence prover, new optimizer, or executable authorization guard.
"""
import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'work/beets/source-e978874d'
RAW = ROOT / 'evidence/beets-scalar-contract/20261108-0671127c5421'
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
OUT = ROOT / 'evidence/beets-admission' / ('20261109-' + uuid4().hex[:12])


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True)
    def save(name, value):
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    def load(path):
        return json.loads(path.read_text())
    checks = []
    def check(name, value):
        assert value, name
        checks.append(name)
    save('protocol.json', dict(date='2026-11-09', task='Scope audit, not more optimization',
        operations=['verify retained evidence', 'inspect fresh fixed runtime without database execution',
                    'classify existing cases by declared domain', 'reselect original two contracts with unchanged DP'],
        execution_domains=['open extensible application', 'fixed deployment including read failures',
                           'fixed deployment, successful database reads'],
        no_new_performance_samples=True, no_beets_backend_admission=True))
    shutil.copy2(__file__, OUT / 'analysis.py')
    try:
        manifest = load(RAW / 'manifest.json')
        check('all retained raw artifacts match', all(sha(RAW / p) == h for p, h in manifest.items()))
        audit = load(RAW.with_name(RAW.name + '-audit.json'))
        check('retained audit bound to raw report', audit['status'] == 'passed' and
              audit['raw_report_sha256'] == sha(RAW / 'report.json'))
        source_manifest = load(ROOT / 'evidence/beets-feasibility/20261108-a03fafe1c20f/source-manifest.json')
        check('whole fixed upstream tree unchanged', all(sha(SOURCE / p) == h for p, h in source_manifest.items()))
        check('analysis starts with empty beets import state', 'beets' not in sys.modules)
        (OUT / 'config').mkdir()
        (OUT / 'config/config.yaml').write_text('plugins: []\n')
        os.environ['BEETSDIR'] = str(OUT / 'config')
        sys.path.insert(0, str(SOURCE))
        import beets
        from beets import plugins
        from beets.library import Item, Album, Library
        from beets.dbcore import db as core
        from beetsplug.export import ExportPlugin
        beets.config['plugins'] = []
        export = ExportPlugin()
        runtime = dict(python=sys.version, beets=beets.__version__, beets_file=beets.__file__,
            configured_plugins=beets.config['plugins'].get(),
            registry=[type(p).__qualname__ for p in plugins.find_plugins()],
            plugin_fields=sorted(plugins.item_field_getters()), item_getters=sorted(Item._getters()),
            export_fields=sorted(export.template_fields), export_album_fields=sorted(export.album_template_fields),
            item_uses_base_constructor=Item.__init__ is core.Model.__init__,
            scope='Fresh direct ExportPlugin constructor, same entry as experiment; not CLI plugin-loader equivalence')
        save('runtime.json', runtime)
        check('fixed direct-export environment has no field override', not runtime['registry'] and
              not runtime['plugin_fields'] and 'tracktotal' not in runtime['item_getters'])
        check('export itself has no field getters and Item inherits constructor', not runtime['export_fields'] and
              not runtime['export_album_fields'] and runtime['item_uses_base_constructor'])

        # Record source locations and actual call syntax, without claiming that
        # syntactic calls are an exhaustive dynamic call graph or effect proof.
        functions = [Album._albumtotal, Album.items, Library.items, core.Database._get_results,
            core.Results.__getitem__, core.Results._get_objects, core.Results._make_model,
            core.Model.__init__, core.Model.update, core.Model.clear_dirty,
            core.Model.__getattr__, core.Model._get, core.Model._convert,
            Item._getters, plugins.item_field_getters, core.Transaction.query]
        import textwrap
        witnesses = []
        for fn in functions:
            path = Path(inspect.getsourcefile(fn))
            source = inspect.getsource(fn)
            tree = ast.parse(textwrap.dedent(source))
            witnesses.append(dict(function=fn.__qualname__, file=str(path.relative_to(ROOT)),
                line=inspect.getsourcelines(fn)[1], source_sha256=sha(path),
                calls=sorted({ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)})))
        save('source-witnesses.json', witnesses)

        cases = load(RAW / 'cases.json')
        injected = {'getter-value', 'getter-error', 'constructor-escape', 'constructor-error',
                    'concurrent-flex-getter', 'converter-effect', 'converter-error'}
        read_failures = {'deny-flex', 'invalid-flex-text', 'deny-fixed'}
        domains = [
            dict(id='open-application', excluded=set(), meaning='Includes tested dynamic extensions and read failures'),
            dict(id='fixed-deployment-all-read-outcomes', excluded=injected,
                 meaning='Pinned direct-export deployment; includes real read errors'),
            dict(id='fixed-deployment-successful-reads', excluded=injected | read_failures,
                 meaning='Same pinned deployment; both original reads complete successfully; all data retained'),
        ]
        matrix = []
        for domain in domains:
            rows = [c for c in cases if c['case'] not in domain['excluded']]
            failures = [c['case'] for c in rows if not c['equal']['drop-flex-and-model']]
            matrix.append(dict(id=domain['id'], definition=domain['meaning'],
                included_cases=[c['case'] for c in rows], excluded_cases=sorted(domain['excluded']),
                counterexamples=failures, observed_equivalence=not failures,
                proof_status='refuted' if failures else 'bounded evidence only; not universally proved',
                source_of_scope='Explicit manual domain interpretation; not learned from sample values'))
        check('open application has seven counterexamples', len(matrix[0]['counterexamples']) == 7)
        check('fixed deployment still has two real read-error counterexamples',
              set(matrix[1]['counterexamples']) == {'deny-flex', 'invalid-flex-text'})
        check('successful-read fixed domain has ten existing cases and no counterexample',
              len(matrix[2]['included_cases']) == 10 and not matrix[2]['counterexamples'])
        save('domain-matrix.json', matrix)

        obligations = [
            dict(id='deployment', requires='Pinned bodies, descriptors, plugin registry, connection hooks and stable configuration',
                 evidence='Fresh direct-export introspection and source witnesses; injected modifications are outside this deployment',
                 status='observed_for_current_environment', unresolved='No runtime guard against later monkeypatching, plugin loading or configuration mutation'),
            dict(id='data', requires='No restriction to sampled rows, metadata values, counts or flex contents',
                 evidence='Nonzero values, NULL, invalid number, ties, reverse and empty case; full tables/output retained',
                 status='bounded_tests', unresolved='No universal quantification over every legal library and SQLite execution'),
            dict(id='read-effects', requires='Retain read errors or explicitly limit the execution domain to successful reads',
                 evidence='Real SQLite authorizer denial and UTF-8 decode failure disappear when flex read is removed',
                 status='refuted_for_full_error_semantics', unresolved='A check before the query cannot guarantee future read success; do not advertise an automatic fallback for a skipped failing read'),
            dict(id='host-effects', requires='No omitted getter dispatch, constructor effects or observable object escape under pinned deployment',
                 evidence='Original constructor and getter-dispatch source; no registered overrides in fresh baseline runtime',
                 status='source_bound_argument_not_machine_proof', unresolved='Arbitrary extensions remain supported only by retaining their original path; no extension guard implemented here'),
            dict(id='sort-and-view', requires='Keep original first SQL/order/conversion; retain original multidisc and slow-sort branches',
                 evidence='Exact query sequence audit and deterministic interleaving; original fixed view remains old after concurrent commit',
                 status='bounded_tests', unresolved='No projection or LIMIT rewrite proved; arbitrary custom collation/row-factory/progress callbacks not analyzed'),
            dict(id='placement-backend', requires='Verified executable lowering and supported effects/ABI for same optimizer',
                 evidence='Current candidate is a Python helper deleting a read, not a verified database-resident region',
                 status='missing', unresolved='No beets lowering or admitted contract; do not set verified_source_contract from output equality'),
        ]
        save('obligations.json', obligations)

        sys.path.insert(0, str(ROOT / 'scripts'))
        from fusion_placement import choose_placement
        from fusion_regions import sql_effect
        for name in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
            check(name + ' immutable shared core', sha(ROOT / 'scripts' / name) == sha(FIRST / 'inputs/scripts' / name))
        regressions = {}
        for name in ['neworder', 'payment']:
            selection = choose_placement(load(FIRST / (name + '-contract.json')))
            check(name + ' identical complete DP result', selection == load(FIRST / (name + '-selection.json')))
            regressions[name] = selection
        save('shared-selections.json', regressions)
        ordinary = next(c for c in cases if c['case'] == 'default')['arms'][0]['queries']
        effects = [dict(sql=q['sql'], parsed_effect=sql_effect(q['sql'])) for q in ordinary]
        check('actual nested beets SQL outside existing effect grammar', all(not e['parsed_effect']['known'] for e in effects))
        save('backend-boundary.json', dict(actual_sql_effects=effects,
            existing_objective='Calls per verified database interval, then moved units, then region count',
            candidate_kind='Host helper specialization with retained Python converter and removed read/model',
            decision='No beets DP contract fabricated; no automatic selection claimed; first-project choices reproduced exactly'))
        report = dict(status='passed', checks=checks, evidence_files_verified=len(manifest),
            conclusion='Prior rejection applies to unrestricted replacement. Fixed deployment with successful reads remains a plausible mechanism; no universal proof, runtime guard, backend admission or speedup.',
            next_bounded_experiment='If pursued, one explicitly scoped original-versus-candidate performance comparison under the successful-read fixed deployment; keep strict-error negative controls and original default path. Do not equate its result with shared-DP transfer.',
            automation_limit='This is a manual source/evidence scope audit, not a rule-based deletion classifier or probability estimator',
            prior_failures='No new database execution, timing, retries or data reduction in this audit')
        save('report.json', report)
        save('input-hashes.json', {str(p.relative_to(ROOT)): sha(p) for p in [RAW / 'report.json', RAW / 'cases.json',
            RAW / 'manifest.json', RAW.with_name(RAW.name + '-audit.json'), FIRST / 'neworder-contract.json',
            FIRST / 'neworder-selection.json', FIRST / 'payment-contract.json', FIRST / 'payment-selection.json']})
    except BaseException as exc:
        save('failure.json', dict(error=type(exc).__name__, message=str(exc), checks=checks))
        raise
    finally:
        save('manifest.json', {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'manifest.json'})
        print(OUT)


if __name__ == '__main__':
    main()
