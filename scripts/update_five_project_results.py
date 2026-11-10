"""Generate paper tables from the fixed fresh five-subject and module studies."""
import hashlib
import json
from pathlib import Path
import statistics
from update_paper_results import contrast, table

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'evidence/five-project-evaluation/20261110-v1'
MODULE=ROOT/'evidence/fossil-module-factorial/20261110-v1'
def load(p):return json.loads(p.read_text())

def main():
    index=load(SUITE/'run.json');assert index['status']=='passed'
    studies={r['project']:ROOT/r['evidence'][0] for r in index['projects']}
    arms={}
    for arm in ['original','slice_only','fusion_only','fusion_slice']:
        rows=sorted([r for r in load(studies['java']/'run.json')['cases'] if r['phase']=='measurement' and r['arm']==arm],key=lambda r:r['round'])
        arms[arm]=dict(blocks=[r['round'] for r in rows],mixed_ms=[sum(s['duration_ns'] for s in r['samples'])/1e6 for r in rows],
            orderstatus_ms=[sum(s['duration_ns'] for s in r['samples'] if s['type']=='OrderStatus')/1e6 for r in rows],calls=[sum(s['execute_calls'] for s in r['samples']) for r in rows])
    java=contrast(arms['original']['mixed_ms'],arms['fusion_only']['mixed_ms'])
    fossil={}
    for scenario in load(studies['fossil']/'summary.json')['performance']:
        raw={a:[b['runs'][a]['elapsed_ms'] for b in scenario['blocks']] for a in ['original','roundtrip','slice']}
        fossil[scenario['name']]=dict(contrast(raw['original'],raw['slice']),roundtrip_ms=statistics.median(raw['roundtrip']))
    os=load(studies['oscar']/'summary.json');be=load(studies['beets']/'summary.json');al=load(studies['alfio']/'summary.json')
    oscar=contrast([r['original_ms'] for r in os['pairs']],[r['candidate_ms'] for r in os['pairs']])
    beets=contrast([r['original_ms'] for r in be['pairs']],[r['candidate_ms'] for r in be['pairs']])
    alfio=contrast([r['original']['total_ms'] for r in al['pairs']],[r['retained']['total_ms'] for r in al['pairs']])
    values=[('Java fusion',java),('alf.io (A/A)',alfio),('Oscar (experimental)',oscar),('beets (experimental)',beets),('Fossil hash',fossil['hash']),('Fossil branch',fossil['branch'])]
    rows=[[label,f"{r['original_ms']:.3f}",f"{r['candidate_ms']:.3f}",f"${r['ratio_of_medians']:.3f}\\times$",f"{r['wins']}/{r['blocks']}"] for label,r in values]
    tex='% Generated from fresh five-subject replication; raw hashes in results.json.\n'
    tex+=table('PrimaryResults','lrrrr','Workload & Original & Tested arm & Median ratio & Wins',rows)
    rows=[]
    for arm,label in [('original','Original'),('slice_only','Deletion only'),('fusion_only','Fusion only'),('fusion_slice','Fusion + deletion')]:
        r=arms[arm];assert len(set(r['calls']))==1
        rows.append([label,f"{statistics.median(r['mixed_ms']):.3f}",f"{statistics.median(r['orderstatus_ms']):.3f}",str(r['calls'][0])])
    tex+=table('JavaCompositionResults','lrrr','Plan & Mixed ms & OrderStatus ms & JDBC calls',rows)
    selections={};rows=[]
    for name,label in [('java','Java'),('alfio','alf.io'),('oscar','Oscar'),('beets','beets'),('fossil-hash','Fossil hash'),('fossil-branch','Fossil branch')]:
        r=load(SUITE/'portfolios'/name/'selection.json');selections[name]=r
        choice={'fusion_only':'Fusion','slice':'Deletion','original':'Original'}[r['selected']]
        gain=lower='---'
        if r['selected']!='original':
            c=r['policy_details']['comparisons'][r['selected']+'>original'];gain=f"{c['geometric_speedup']:.3f}";lower=f"{c['lower_speedup']:.3f}"
        rows.append([label,choice,gain,lower])
    tex+=table('SelectionResults','llrr','Context & Choice & Geometric ratio & Lower bound',rows)
    # Keep the independent historical Java region factorial, with no sample pooling.
    old=(ROOT/'paper/history/three-subject-tables-20261110.tex').read_text()
    tex+=old[old.index('\\newcommand{\\JavaRegionResults}'):]
    mod=load(MODULE/'summary.json');diag=[r for r in load(MODULE/'diagnostics.json') if r['case']=='work']
    rows=[]
    for arm,label in [('original','Original'),('projection_only','SQL-side only'),('printer_only','C-side only'),('both','Both')]:
        vals=[r['median_ms'][arm] for r in mod['performance']]
        d=next(r for r in diag if r['arm']==arm)
        rows.append([label,*[f'{v:.3f}' for v in vals],str(d['wiki_calls']),str(d['private_status_queries'])])
    tex+=table('ModuleResults','lrrrr','Arm & Hash ms & Branch ms & Wiki calls & Private reads',rows)
    sources=[SUITE/'protocol.json',SUITE/'run.json',MODULE/'protocol.json',MODULE/'summary.json',MODULE/'diagnostics.json']
    sources += [p/'summary.json' for p in studies.values()]+[studies['java']/'run.json']
    result=dict(schema='paper-five-subject-results/v1',date='2026-11-10',subjects={'java':java,'alfio':alfio,'oscar':oscar,'beets':beets,'fossil':fossil},
        scope={'java':'admitted fusion','alfio':'A/A repetition; not a speedup','oscar':'experimental adapter, current gate abstains','beets':'effect-restored experimental adapter, current gate abstains','fossil':'admitted slice'},
        java_four_arm=arms,java_composition=contrast(arms['fusion_only']['mixed_ms'],arms['fusion_slice']['mixed_ms']),
        oscar_interval=os['exploratory_bootstrap_95'],beets_interval=be['paired_bootstrap_percentile95'],
        selectors=selections,module_analysis=mod,module_diagnostics=diag,
        fresh_application_executions=40+16+20+22+304,module_executions=mod['executions'],
        historical_comparison='Keep earlier batches separate; Oscar old no-stable-gain result is not overwritten.',
        generalization='All existing principal development subjects; no held-out projects and no automatic frontend claim',
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (ROOT/'paper/results.json').write_text(json.dumps(result,indent=2)+'\n')
    (ROOT/'paper/results-tables.tex').write_text(tex)
    print(json.dumps({k:v for k,v in result['subjects'].items()},indent=2))

if __name__=='__main__':main()
