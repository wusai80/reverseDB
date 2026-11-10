"""Benchmark-only wiring of the frozen Region into a first-row field consumer.

The original branch logic and both SQL queries remain. Source rewriting is only
the temporary experiment entry, not automatic whole-application admission.
"""
import ast
import inspect
import textwrap

from .continuation_region import Region


def make_adapter(original,binding,stats=None):
    source=textwrap.dedent(inspect.getsource(original));tree=ast.parse(source)
    fn=tree.body[0];receiver=fn.args.args[0].arg;matches=[]
    class Replace(ast.NodeTransformer):
        def visit_Return(self,node):
            value=node.value
            if (isinstance(value,ast.Attribute) and isinstance(value.value,ast.Subscript)
                and isinstance(value.value.slice,ast.Constant) and type(value.value.slice.value) is int and value.value.slice.value==0
                and isinstance(value.value.value,ast.Call) and isinstance(value.value.value.func,ast.Attribute)
                and isinstance(value.value.value.func.value,ast.Name) and value.value.value.func.value.id==receiver
                and not value.value.value.args and not value.value.value.keywords):
                matches.append(value.attr)
                node.value=ast.Call(func=ast.Name(id='_rd_consume_first',ctx=ast.Load()),args=[value.value.value,ast.Constant(value.attr)],keywords=[])
            return self.generic_visit(node)
    Replace().visit(tree)
    if len(matches)!=1:raise ValueError('Expected exactly one first-row field return')
    code=ast.unparse(ast.fix_missing_locations(tree))+'\n'
    parameters=[x['parameter'] for x in binding[1]['lazy'].values()]
    def consume(results,field):
        # Preserve the original slow/empty path, reusing the already obtained
        # Results rather than restarting the album method or executing SQL twice.
        if results.query or results.sort or results.limit is not None or not results.rows:
            if stats is not None:stats['original_result_paths']+=1
            return getattr(results[0],field)
        indexed=results._get_indexed_flex_attrs();row=results.rows[0]
        raw={k:v for k,v in dict(row).items() if k[:4]!='flex'}
        region=Region(binding,results.db,{parameters[0]:raw,parameters[1]:indexed.get(row['id'],{}) or {}})
        result=region.run([field])[0]
        if stats is not None:
            stats['regions']+=1;stats['materializations']+=region.virtual.materializations
        return result
    namespace=dict(original.__globals__);namespace['_rd_consume_first']=consume
    exec(compile(code,'<continuation-export-adapter>','exec'),namespace)
    return namespace[fn.name],code
