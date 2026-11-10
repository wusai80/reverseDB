package research;

import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.lang.model.element.*;
import javax.tools.*;
import java.nio.file.*;
import java.util.*;
import org.json.*;

/** Attributed cursor use and local return candidates, never a deletion rule. */
public final class ResultConsumptionExtract {
  static Trees trees;
  static final Map<Element,TreePath> declarations=new HashMap<>();
  static final Set<Element> written=new HashSet<>();
  static final Map<Element,JSONObject> results=new LinkedHashMap<>();
  static TreePath path(TreePath p,Tree t) { return trees.getPath(p.getCompilationUnit(),t); }
  static String symbol(Element e) { return e==null?"unknown":e.getEnclosingElement()+"#"+e; }
  static long offset(TreePath p) { return trees.getSourcePositions().getStartPosition(p.getCompilationUnit(),p.getLeaf()); }
  static long line(TreePath p) { return p.getCompilationUnit().getLineMap().getLineNumber(offset(p)); }
  static TreePath owner(TreePath p) {
    for(TreePath q=p;q!=null;q=q.getParentPath()) if(q.getLeaf() instanceof MethodTree) return q;
    return null;
  }
  static String identity(Element e) {
    TreePath d=declarations.get(e);
    return d==null?"unknown":symbol(e.getEnclosingElement())+"@"+offset(d);
  }
  static boolean loop(Tree t) { return t instanceof ForLoopTree || t instanceof EnhancedForLoopTree || t instanceof WhileLoopTree || t instanceof DoWhileLoopTree; }
  static Set<Tree> loops(TreePath p) {
    Set<Tree> out=Collections.newSetFromMap(new IdentityHashMap<>());
    for(TreePath q=p.getParentPath();q!=null && !(q.getLeaf() instanceof MethodTree);q=q.getParentPath()) if(loop(q.getLeaf()))out.add(q.getLeaf());
    return out;
  }
  static void unknown(JSONObject r,String reason) {
    JSONArray a=r.getJSONArray("unknowns");
    for(Object x:a)if(x.equals(reason))return;
    a.put(reason);
  }
  static JSONObject usage(TreePath call) {
    TreePath q=call.getParentPath();
    while(q!=null && (q.getLeaf() instanceof ParenthesizedTree || q.getLeaf() instanceof TypeCastTree))q=q.getParentPath();
    if(q==null)return new JSONObject().put("kind","unresolved");
    Tree t=q.getLeaf();
    if(t instanceof ReturnTree ret)return new JSONObject().put("kind","direct_return_expression").put("line",line(q))
      .put("expression_syntax",ret.getExpression().toString()).put("value_preservation_proved",false);
    if(t instanceof AssignmentTree a) {
      TreePath target=path(q,a.getVariable());Element e=trees.getElement(target);
      JSONObject u=new JSONObject().put("kind",e!=null && e.getKind().isField()?"field_assignment":"local_assignment")
        .put("target",symbol(e)).put("line",line(q));
      if(a.getVariable() instanceof MemberSelectTree member) {
        Element object=trees.getElement(path(q,member.getExpression()));
        u.put("object",identity(object));
        TreePath method=owner(q);JSONArray returned=new JSONArray();
        if(method!=null)new TreePathScanner<Void,Void>() {
          @Override public Void visitReturn(ReturnTree ret,Void v) {
            if(ret.getExpression()!=null && object!=null && object.equals(trees.getElement(path(getCurrentPath(),ret.getExpression()))))returned.put(line(getCurrentPath()));
            return super.visitReturn(ret,v);
          }
        }.scan(method,null);
        u.put("same_object_return_lines",returned).put("value_preservation_proved",false)
          .put("limit","syntactic candidate only; aliasing, later field writes and finally effects unresolved");
      }
      return u;
    }
    if(t instanceof VariableTree v)return new JSONObject().put("kind","local_initializer").put("target",symbol(trees.getElement(q))).put("line",line(q));
    return new JSONObject().put("kind","nested_expression_or_call").put("syntax",t.getKind().toString()).put("line",line(q));
  }

  public static void main(String[] args) throws Exception {
    JavaCompiler compiler=ToolProvider.getSystemJavaCompiler();DiagnosticCollector<JavaFileObject> diagnostics=new DiagnosticCollector<>();
    try(StandardJavaFileManager files=compiler.getStandardFileManager(diagnostics,null,null)) {
      JavacTask task=(JavacTask)compiler.getTask(null,files,diagnostics,List.of("-proc:none","-implicit:none","--release","23","-classpath",args[1]),null,
        files.getJavaFileObjectsFromStrings(Arrays.asList(args).subList(2,args.length)));
      List<CompilationUnitTree> units=new ArrayList<>();task.parse().forEach(units::add);task.analyze();trees=Trees.instance(task);
      if(diagnostics.getDiagnostics().stream().anyMatch(d->d.getKind()==Diagnostic.Kind.ERROR))throw new IllegalArgumentException(diagnostics.getDiagnostics().toString());
      for(CompilationUnitTree unit:units)new TreePathScanner<Void,Void>() {
        @Override public Void visitVariable(VariableTree v,Void x) { declarations.put(trees.getElement(getCurrentPath()),getCurrentPath());return super.visitVariable(v,x); }
        @Override public Void visitAssignment(AssignmentTree v,Void x) { written.add(trees.getElement(path(getCurrentPath(),v.getVariable())));return super.visitAssignment(v,x); }
      }.scan(unit,null);
      for(var entry:declarations.entrySet()) {
        Element e=entry.getKey();TreePath p=entry.getValue();
        if(e==null || !e.asType().toString().equals("java.sql.ResultSet"))continue;
        VariableTree v=(VariableTree)p.getLeaf();TreePath method=owner(p);
        JSONObject r=new JSONObject().put("id",identity(e)).put("owner",method==null?"unknown":symbol(trees.getElement(method)))
          .put("file",Path.of(p.getCompilationUnit().getSourceFile().toUri()).toString()).put("line",line(p))
          .put("calls",new JSONArray()).put("unknowns",new JSONArray()).put("producer",JSONObject.NULL);
        if(v.getInitializer() instanceof MethodInvocationTree call && call.getMethodSelect() instanceof MemberSelectTree select) {
          TreePath c=path(p,call);String api=symbol(trees.getElement(c));
          if(api.equals("java.sql.PreparedStatement#executeQuery()"))
            r.put("producer",new JSONObject().put("statement_receiver",identity(trees.getElement(path(c,select.getExpression())))).put("line",line(c)).put("api",api));
        }
        if(r.isNull("producer") || r.getJSONObject("producer").getString("statement_receiver").equals("unknown"))unknown(r,"query_factory_not_local_prepared_statement");
        if(written.contains(e))unknown(r,"cursor_reassigned");
        results.put(e,r);
      }
      for(CompilationUnitTree unit:units)new TreePathScanner<Void,Void>() {
        @Override public Void visitIdentifier(IdentifierTree id,Void x) {
          TreePath p=getCurrentPath();Element e=trees.getElement(p);JSONObject r=results.get(e);
          if(r!=null) {
            if(!Objects.equals(owner(p)==null?null:trees.getElement(owner(p)),owner(declarations.get(e))==null?null:trees.getElement(owner(declarations.get(e)))))unknown(r,"cursor_captured_by_nested_code");
            TreePath parent=p.getParentPath();boolean direct=false;
            if(parent!=null && parent.getLeaf() instanceof MemberSelectTree member && member.getExpression()==id) {
              TreePath grand=parent.getParentPath();
              if(grand!=null && grand.getLeaf() instanceof MethodInvocationTree call && call.getMethodSelect()==member)direct=true;
            }
            if(!direct)unknown(r,"cursor_alias_escape_or_indirect_use");
            for(TreePath q=p.getParentPath();q!=null && !(q.getLeaf() instanceof MethodTree);q=q.getParentPath())
              if(q.getLeaf() instanceof LambdaExpressionTree || q.getLeaf() instanceof ClassTree)unknown(r,"cursor_captured_by_nested_code");
          }
          return super.visitIdentifier(id,x);
        }
        @Override public Void visitMethodInvocation(MethodInvocationTree call,Void x) {
          TreePath p=getCurrentPath();
          if(call.getMethodSelect() instanceof MemberSelectTree select) {
            Element receiver=trees.getElement(path(p,select.getExpression()));JSONObject r=results.get(receiver);
            if(r!=null) {
              String name=select.getIdentifier().toString(),api=symbol(trees.getElement(p));
              Set<Tree> repeated=loops(p);repeated.removeAll(loops(declarations.get(receiver)));
              JSONObject event=new JSONObject().put("operation",name).put("api",api).put("line",line(p)).put("offset",offset(p)).put("inside_cursor_loop",!repeated.isEmpty());
              boolean getter=name.startsWith("get") && !call.getArguments().isEmpty();
              if(getter) {
                Tree first=call.getArguments().get(0);
                if(first instanceof LiteralTree lit && (lit.getValue() instanceof String || lit.getValue() instanceof Integer))event.put("column",lit.getValue());
                else event.put("column",JSONObject.NULL).put("column_reason","dynamic_column");
                event.put("use",usage(p)).put("decode_semantics","not_proved");
              }
              if(!api.startsWith("java.sql.ResultSet#") || !(name.equals("next") || name.equals("close") || name.equals("wasNull") || getter))unknown(r,"unsupported_cursor_operation:"+name);
              r.getJSONArray("calls").put(event);
            }
          }
          return super.visitMethodInvocation(call,x);
        }
      }.scan(unit,null);
      JSONArray out=new JSONArray();
      List<JSONObject> ordered=new ArrayList<>(results.values());ordered.sort(Comparator.comparing(r->r.getString("owner")+":"+String.format("%09d",r.getLong("line"))));
      for(JSONObject r:ordered) {
        List<JSONObject> advances=new ArrayList<>();int getters=0;
        for(Object x:r.getJSONArray("calls")) { JSONObject call=(JSONObject)x;if(call.getString("operation").equals("next"))advances.add(call);if(call.has("use"))getters++; }
        String shape=r.getJSONArray("unknowns").length()>0?"unknown":advances.stream().anyMatch(c->c.getBoolean("inside_cursor_loop"))?"iteration_capable":advances.size()==1?"single_advance":advances.isEmpty()?"no_advance":"multiple_advances";
        r.put("cursor_shape",shape).put("next_sites",advances.size()).put("getter_sites",getters)
          .put("all_rows_consumed_proved",false).put("application_observation_connected",false).put("deletion_authorized",false);
        out.put(r);
      }
      Files.writeString(Path.of(args[0]),new JSONObject().put("kind","result-consumption-slices/v1").put("result_sets",out)
        .put("scope","syntactic cursor operations per local query result; no whole-program alias/control/result decoding proof").toString(2)+"\n");
    }
  }
}
