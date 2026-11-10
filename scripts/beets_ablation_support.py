"""Experiment-only factorial variants of the frozen, effect-restored region.

All arms use full fixed rows. The model-only arm also carries the complete flex
packet into the original Results indexing step. No default application changes.
"""
import ast
import random
import statistics

ARMS = ['original', 'query_only', 'model_only', 'full']
ORDERS = [
    ['original', 'query_only', 'full', 'model_only'],
    ['query_only', 'model_only', 'original', 'full'],
    ['model_only', 'full', 'query_only', 'original'],
    ['full', 'original', 'model_only', 'query_only'],
]
QUERY_COUNTS = dict(original=18002, query_only=13502, model_only=18002, full=13502)
MODEL_COUNTS = dict(original=14000, query_only=14000, model_only=9500, full=9500)


def schedule():
    result = [dict(id='diagnostic-'+a, stage='diagnostic', pair=None, arm=a) for a in ARMS]
    for block in range(10):
        order = ORDERS[block % 4] if block < 2 else ORDERS[(block-2) % 4]
        for arm in order:
            result.append(dict(id=f'p{block:02d}-{arm}', pair=block,
                               stage='warmup' if block < 2 else 'formal', arm=arm))
    return result


def variants(restored, core, bindings, out):
    """Derive only the two missing cells; full is the exact frozen function."""
    reader_tree = ast.parse((restored/'reader.py').read_text())
    assignments = [n for n in ast.walk(reader_tree) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'flex_rows' for t in n.targets)]
    assert len(assignments) == 1
    assignments[0].value = ast.parse('tx.query(flex_sql, subvals)', mode='eval').body
    reader_tree.body[0].body[-1] = ast.parse('return rows, flex_rows').body[0]
    reader_code = ast.unparse(ast.fix_missing_locations(reader_tree))+'\n'
    (out/'retain-reader.py').write_text(reader_code)
    ns = dict(core.__dict__)
    exec(compile(reader_code, str(out/'retain-reader.py'), 'exec'), ns)
    readers = dict(bindings['readers'], model_only=ns['read_fixed_rows'])
    readers['query_only'] = readers['drop-flex-and-model']
    readers['full'] = readers['drop-flex-and-model']
    result = {}
    for arm in ARMS[1:]:
        tree = ast.parse((restored/'restored-candidate.py').read_text())
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'candidate')
        if arm == 'query_only':
            fn.body[-1] = ast.parse('return core.Results(Item, rows, album._db, [])[0].tracktotal').body[0]
        elif arm == 'model_only':
            assert isinstance(fn.body[-2], ast.Assign) and fn.body[-2].targets[0].id == 'rows'
            fn.body[-2].targets = [ast.parse('rows, flex', mode='eval').body]
            fn.body[-2].targets[0].ctx = ast.Store()
            for element in fn.body[-2].targets[0].elts:
                element.ctx = ast.Store()
            fn.body[-1] = ast.parse("return consume_packet(core, Item, rows, flex, album._db, prefix, clear_state, ['tracktotal'])[0][0]").body[0]
        code = ast.unparse(ast.fix_missing_locations(fn))+'\n'
        (out/(arm+'-candidate.py')).write_text(code)
        namespace = dict(bindings, readers=readers)
        exec(compile(code, str(out/(arm+'-candidate.py')), 'exec'), namespace)
        function = namespace['candidate']
        result[arm] = lambda album, function=function, arm=arm: function(album, arm)
    return result


def summarize(records):
    blocks = [{r['arm']: r['wall_ms'] for r in records if r['stage']=='formal' and r['pair']==p}
              for p in range(2, 10)]
    assert all(set(b)==set(ARMS) for b in blocks)
    def estimate(values):
        rng = random.Random(20261109)
        boot = sorted(statistics.median(rng.choices(values, k=8)) for _ in range(20000))
        return dict(values=values, median=statistics.median(values),
                    exploratory_percentile95=[boot[499], boot[19499]])
    comparisons = {}
    for before, after in [('original','query_only'), ('original','model_only'), ('original','full'),
                          ('model_only','full'), ('query_only','full')]:
        ratios = [b[before]/b[after] for b in blocks]
        comparisons[before+'__'+after] = dict(estimate(ratios), wins=sum(v>1 for v in ratios))
    interaction = [b['query_only']+b['model_only']-b['original']-b['full'] for b in blocks]
    return dict(arm_median_ms={a:statistics.median(b[a] for b in blocks) for a in ARMS},
        comparisons=comparisons, interaction_saving_ms=estimate(interaction),
        interaction_definition='T(query_only)+T(model_only)-T(original)-T(full); positive means joint saving exceeds sum of isolated savings',
        interpretation='Eight balanced blocks from one fixed deployment; exploratory intervals, no multiple-comparison-adjusted significance claim')
