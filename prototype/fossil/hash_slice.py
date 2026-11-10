"""Contract-rooted native slice using the existing three-way demand analysis.

The graph and C lowering are reviewed, source-bound adapters. Graph reachability
is reused unchanged; neither the graph nor native equivalence is inferred in full.
"""
import ast
import hashlib
import json
import re

from prototype.module_pruning.demand_model import decide, reachable, OBLIGATIONS
from prototype.fossil.effect_projection import source_of, span


REMOVED = ['wiki_conversion', 'comment_display', 'phase_display', 'comment_normalization',
           'sql_3', 'sql_4', 'sql_5', 'sql_9', 'sql_11']


def graph():
    edges=[]
    def depends(a,*bs):
        edges.extend({'from':a,'to':b} for b in bs)
    depends('hash_output','sql_1','row_iteration','limits_and_notices')
    depends('row_iteration','selection_and_order','prepare_step_finalize')
    depends('selection_and_order','original_from_join_where_suffix','sql_2','sql_6','sql_10')
    # These inexpensive original columns also preserve the original row shape.
    depends('prepare_step_finalize','sql_0','sql_1','sql_2','sql_6','sql_7','sql_8','sql_10')
    depends('required_failures','prepare_step_finalize','argument_and_repository_validation')
    depends('persistent_state','original_repository_lifecycle')
    depends('rich_output','hash_output','comment_display','phase_display','comment_normalization','sql_11')
    depends('comment_display','sql_3','sql_9','sql_10','hash_digits')
    depends('sql_3','wiki_conversion','display_tags')
    depends('wiki_conversion','wiki_config_reads','wiki_link_reads','private_rendering_state')
    depends('phase_display','sql_4','sql_5','private_status_reads')
    depends('comment_normalization','sql_9')
    depends('sql_11','display_tags')
    depends('strict_internal_failures',*REMOVED,'hash_digits')
    roots=[{'node':'hash_output','when':{'hash_contract':True}},
           {'node':'rich_output','when':{'hash_contract':False}},
           {'node':'strict_internal_failures','when':{'preserve_presentation_failures':True}},
           {'node':'persistent_state'},{'node':'required_failures'}]
    nodes=sorted({e[k] for e in edges for k in ['from','to']}|{r['node'] for r in roots})
    return dict(nodes=nodes,edges=edges,roots=roots,
                provenance='Reviewed native/SQL dependency summary; edges point from consumer to prerequisite',
                error_policy='Failures confined to unused presentation are outside the declared hash capability')


def analysis(binding_valid=True):
    g=graph();facts={'hash_contract':True,'preserve_presentation_failures':False}
    obligations={k:True for k in OBLIGATIONS};obligations['source_binding_valid']=binding_valid
    result=decide(g,facts,REMOVED,obligations)
    return dict(graph=g,facts=facts,obligations=obligations,decision=result,
                retained=sorted(reachable(g,facts,True)),removed=REMOVED,
                limitation='Completeness of the pinned native summary is a reviewed premise, not automatically proved')


def query_parts(sql):
    """Split a plain SELECT projection without disturbing its FROM/WHERE bytes.

    Handles quoted strings/identifiers and nested parentheses; other SELECT
    forms are unsupported here. The pinned SQL equality is checked separately.
    """
    if not sql.startswith('SELECT\n'):raise ValueError('Unknown SELECT form')
    depth=0;quote=None;parts=[];start=6;i=6
    while i<len(sql):
        c=sql[i]
        if quote:
            if c==quote:
                if i+1<len(sql) and sql[i+1]==quote:i+=2;continue
                quote=None
        elif c in "'\"":quote=c
        elif c=='(':depth+=1
        elif c==')':
            depth-=1
            if depth<0:raise ValueError('Unbalanced SQL')
        elif depth==0 and c==',':parts.append(sql[start:i]);start=i+1
        elif depth==0 and sql[i:i+4]=='FROM' and (i==0 or sql[i-1].isspace()):
            parts.append(sql[start:i]);return parts,sql[i:]
        i+=1
    raise ValueError('Missing outer FROM')


def transform(source, function_ast, recovered_query, binding):
    if hashlib.sha256(source).hexdigest()!=binding['timeline_sha256']:
        raise ValueError('Unreviewed source')
    text=source.decode()
    match=re.search(r'static const char zBaseSql\[\] =\s*(.*?)\n  ;',text,re.S)
    literals=re.findall(r'"(?:[^"\\]|\\.)*"',match[1])
    original_sql=''.join(ast.literal_eval(x) for x in literals)
    recovered_literals=re.findall(r'"(?:[^"\\]|\\.)*"',recovered_query.split('return',1)[1])
    recovered_sql=''.join(ast.literal_eval(x) for x in recovered_literals)
    if original_sql!=recovered_sql:raise ValueError('Recovered/source SQL mismatch')
    plan=analysis()
    if plan['decision']['decision']!='conditionally_deletable':raise ValueError('Slice not admitted')
    projections,tail=query_parts(recovered_sql)
    if len(projections)!=12:raise ValueError('Unexpected row shape')
    aliases={3:'comment',4:'primPlinkCount',5:'plinkCount',9:'comment0',11:'tags'}
    newparts=list(projections)
    for i,alias in aliases.items():
        if 'sql_'+str(i) not in plan['removed']:raise ValueError('Missing demand decision')
        if not re.search(r'\bAS\s+'+alias+r'\s*$',projections[i],re.I):raise ValueError('Unexpected alias')
        newparts[i]='\n  NULL AS '+alias
    sliced_sql='SELECT'+','.join(newparts)+'\n'+tail
    body=next(n for n in function_ast['inner'] if n['kind']=='CompoundStmt')['inner']
    loop=body[12];rows=loop['inner'][-1]['inner']
    if loop['kind']!='WhileStmt' or not source_of(source,rows[14]).startswith('if( nAbsLimit!=0 )'):
        raise ValueError('Unknown control shape')
    declarations='\n'.join(source_of(source,body[i]) for i in [0,1,2,7])
    # Preserve the original limit check, extra db_step before limit termination,
    # row counter and EOF notices. Exact %H has one output line per entry.
    printer='''static void reverse_db_print_hash_slice(Stmt *q, int nLimit){
%s
  while( (rc=db_step(q))==SQLITE_ROW ){
    %s
    %s
    fossil_print("%%s\\n", zId);
    nLine++;
    nEntry++;
  }
  %s
}
'''%(declarations,source_of(source,rows[1]),source_of(source,rows[14]),source_of(source,body[13]))
    helper='''static const char *reverse_db_hash_query(void){
  return %s;
}
%s
'''%(json.dumps(sliced_sql),printer)
    insertion=text.index('void timeline_cmd(void){')
    result=text[:insertion]+helper+text[insertion:]
    # Local checkout, verbose output, SQL-as-output and every other format fall
    # back. No data-dependent branch chooses whether the optimization applies.
    result=result.replace('  int showSql = 0;','  int showSql = 0;\n  int reverseDbHashSlice = 0;',1)
    marker='  blob_append(&sql, timeline_query_for_tty(), -1);'
    if result.count(marker)!=1:raise ValueError('Unknown query call shape')
    result=result.replace(marker,'''  reverseDbHashSlice = zFormat && strcmp(zFormat,"%H")==0
      && !verboseFlag && !showSql && !g.localOpen && n!=(-2147483647-1);
  blob_append(&sql, reverseDbHashSlice ? reverse_db_hash_query()
                                     : timeline_query_for_tty(), -1);''',1)
    marker='  print_timeline(&q, n, width, zFormat, verboseFlag);'
    if result.count(marker)!=1:raise ValueError('Unknown print call shape')
    result=result.replace(marker,'''  if( reverseDbHashSlice ) reverse_db_print_hash_slice(&q, n);
  else print_timeline(&q, n, width, zFormat, verboseFlag);''',1)
    plan.update(original_sql=original_sql,sliced_sql=sliced_sql,retained_from_where_exact=query_parts(sliced_sql)[1]==tail,
                replaced_columns=aliases,lowering='Original loop/limit/EOF fragments plus exact-%H output specialization',
                query_origin='SQL literal recovered by Ghidra; checked equal to pinned source')
    return result.encode(),plan
