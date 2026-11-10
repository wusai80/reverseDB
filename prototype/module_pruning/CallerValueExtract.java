package research;

import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.lang.model.element.*;
import javax.tools.*;
import java.nio.file.*;
import java.util.*;
import org.json.*;

/** Local caller value-use slices; external calls and mutable aliases stay opaque. */
public final class CallerValueExtract {
  static Trees trees;
  static final Map<Element,TreePath> declarations=new HashMap<>();
  static final Set<Element> written=new HashSet<>(),escaped=new HashSet<>(),mutable=new HashSet<>();
  static final Set<String> fieldWrites=new HashSet<>();
  static final Map<String,JSONObject> bridgeSites=new HashMap<>();
  static final Map<String,String> cache=new HashMap<>();
  static final JSONArray nodes=new JSONArray(),edges=new JSONArray(),returns=new JSONArray();
  static String symbol(Element e) { return e==null?"unknown":e.getEnclosingElement()+"#"+e; }
  static TreePath path(TreePath p,Tree t) { return trees.getPath(p.getCompilationUnit(),t); }
  static long offset(TreePath p) { return trees.getSourcePositions().getStartPosition(p.getCompilationUnit(),p.getLeaf()); }
  static String owner(TreePath p) { for(TreePath q=p;q!=null;q=q.getParentPath())if(q.getLeaf() instanceof MethodTree)return symbol(trees.getElement(q));return "unknown"; }
  static String site(TreePath p) { return Path.of(p.getCompilationUnit().getSourceFile().toUri())+":"+offset(p); }
  static String node(String kind,TreePath p,JSONObject data) {
    String id="n"+nodes.length();nodes.put(data.put("id",id).put("kind",kind).put("owner",owner(p)).put("site",site(p))
      .put("line",p.getCompilationUnit().getLineMap().getLineNumber(offset(p))));return id;
  }
  static void edge(String a,String b,String kind) { edges.put(new JSONObject().put("from",a).put("to",b).put("kind",kind)); }
  static boolean summary(String target) { return target.startsWith("org.json.JSONObject#put(java.lang.String,") || target.equals("org.json.JSONArray#getJSONObject(int)") || target.equals("reversedb.tpcc.AppRuntime#rows(java.sql.Connection,java.lang.String,java.lang.Object...)"); }
  static boolean scalar(Element e) { return e!=null && (e.asType().getKind().isPrimitive() || Set.of("java.lang.String","java.lang.Integer","java.lang.Long","java.lang.Double","java.lang.Boolean").contains(e.asType().toString())); }
  static String expr(TreePath p,Set<Element> seen) {
    String key=site(p);if(cache.containsKey(key))return cache.get(key);
    Tree t=p.getLeaf();Element e=trees.getElement(p);String id;
    if(t instanceof ParenthesizedTree par)return expr(path(p,par.getExpression()),seen);
    if(t instanceof LiteralTree literal)id=node("literal",p,new JSONObject().put("value",literal.getValue()==null?JSONObject.NULL:literal.getValue()));
    else if(t instanceof TypeCastTree cast) { id=node("cast",p,new JSONObject().put("type",cast.getType().toString()));edge(expr(path(p,cast.getExpression()),seen),id,"cast_value"); }
    else if(t instanceof IdentifierTree) {
      TreePath d=declarations.get(e);
      if(d==null || written.contains(e) || mutable.contains(e) || !seen.add(e))id=node("unknown_value",p,new JSONObject().put("reason","unbound_reassigned_mutable_or_cyclic_local"));
      else if(e.getKind()==ElementKind.PARAMETER)id=node("parameter",p,new JSONObject().put("symbol",symbol(e)));
      else if(((VariableTree)d.getLeaf()).getInitializer()!=null) {
        id=node("local_value",p,new JSONObject().put("declaration",site(d)).put("symbol",symbol(e)));
        edge(expr(path(d,((VariableTree)d.getLeaf()).getInitializer()),new HashSet<>(seen)),id,"initializer_value");
      } else id=node("unknown_value",p,new JSONObject().put("reason","no_local_initializer"));
    } else if(t instanceof MemberSelectTree member) {
      Element object=trees.getElement(path(p,member.getExpression()));
      boolean blocked=fieldWrites.contains(owner(p)+"|"+symbol(e)) || escaped.contains(object);
      id=node(blocked?"unknown_field":"field_read",p,new JSONObject().put("field",symbol(e)).put("reason",blocked?"field_write_or_object_escape":"conditional_local_object_field_use"));
      if(!blocked)edge(expr(path(p,member.getExpression()),new HashSet<>(seen)),id,"object_field_selection");
    } else if(t instanceof MethodInvocationTree call) {
      String target=symbol(e);JSONObject details=new JSONObject().put("target",target).put("expression",call.toString()).put("effects_preserved",true);
      JSONObject bridge=bridgeSites.get(key);
      if(bridge!=null)details.put("reflection",bridge);
      boolean known=summary(target);
      if(target.startsWith("org.json.JSONObject#put(") && call.getMethodSelect() instanceof MemberSelectTree top && !call.getArguments().isEmpty()) {
        ExpressionTree prior=top.getExpression();ExpressionTree keyArg=call.getArguments().get(0);
        while(prior instanceof MethodInvocationTree previous && previous.getMethodSelect() instanceof MemberSelectTree member && !previous.getArguments().isEmpty()) {
          if(symbol(trees.getElement(path(p,previous))).startsWith("org.json.JSONObject#put(") && keyArg instanceof LiteralTree keyLiteral
            && previous.getArguments().get(0) instanceof LiteralTree oldLiteral && Objects.equals(keyLiteral.getValue(),oldLiteral.getValue())) {
            known=false;details.put("unknown_reason","repeated_JSON_key_requires_overwrite_analysis");break;
          }
          prior=member.getExpression();
        }
      }
      details.put("return_summary",known?"explicit_may_depend_summary":"opaque_return_no_argument_to_result_inference");
      id=node("call_result",p,details);cache.put(key,id);
      if(known) {
        if(call.getMethodSelect() instanceof MemberSelectTree m && !target.startsWith("reversedb.tpcc.AppRuntime#rows("))edge(expr(path(p,m.getExpression()),new HashSet<>(seen)),id,"summary_receiver");
        for(int i=0;i<call.getArguments().size();i++) {
          String argument=expr(path(p,call.getArguments().get(i)),new HashSet<>(seen));
          edge(argument,id,target.startsWith("reversedb.tpcc.AppRuntime#rows(")?"query_argument_"+i:"summary_argument_"+i);
        }
      }
    } else if(t instanceof NewClassTree n)id=node("new_object",p,new JSONObject().put("type",n.getIdentifier().toString()).put("constructor_effects_proved",false));
    else id=node("unknown_value",p,new JSONObject().put("reason","unsupported_expression:"+t.getKind()).put("syntax",t.toString()));
    cache.put(key,id);return id;
  }
  public static void main(String[] args) throws Exception {
    JSONObject calls=new JSONObject(Files.readString(Path.of(args[2])));
    for(Object item:calls.getJSONArray("calls")) {
      JSONObject c=(JSONObject)item;
      if(c.has("reflection_argument_transfer")) {
        JSONObject loc=c.getJSONObject("location");bridgeSites.put(loc.getString("file")+":"+loc.getLong("start"),c.getJSONObject("reflection_argument_transfer"));
      }
    }
    JavaCompiler compiler=ToolProvider.getSystemJavaCompiler();DiagnosticCollector<JavaFileObject> diagnostics=new DiagnosticCollector<>();
    try(StandardJavaFileManager files=compiler.getStandardFileManager(diagnostics,null,null)) {
      JavacTask task=(JavacTask)compiler.getTask(null,files,diagnostics,List.of("-proc:none","-implicit:none","--release","23","-classpath",args[1]),null,files.getJavaFileObjectsFromStrings(Arrays.asList(args).subList(3,args.length)));
      List<CompilationUnitTree> units=new ArrayList<>();task.parse().forEach(units::add);task.analyze();trees=Trees.instance(task);
      if(diagnostics.getDiagnostics().stream().anyMatch(d->d.getKind()==Diagnostic.Kind.ERROR))throw new IllegalArgumentException(diagnostics.getDiagnostics().toString());
      for(CompilationUnitTree unit:units)new TreePathScanner<Void,Void>() {
        @Override public Void visitVariable(VariableTree v,Void x) { declarations.put(trees.getElement(getCurrentPath()),getCurrentPath());return super.visitVariable(v,x); }
        void write(Tree t) {
          Element e=trees.getElement(path(getCurrentPath(),t));written.add(e);
          if(e!=null && e.getKind().isField())fieldWrites.add(owner(getCurrentPath())+"|"+symbol(e));
        }
        @Override public Void visitAssignment(AssignmentTree v,Void x) { write(v.getVariable());return super.visitAssignment(v,x); }
        @Override public Void visitCompoundAssignment(CompoundAssignmentTree v,Void x) { write(v.getVariable());return super.visitCompoundAssignment(v,x); }
        @Override public Void visitUnary(UnaryTree v,Void x) { if(Set.of(Tree.Kind.PREFIX_INCREMENT,Tree.Kind.POSTFIX_INCREMENT,Tree.Kind.PREFIX_DECREMENT,Tree.Kind.POSTFIX_DECREMENT).contains(v.getKind()))write(v.getExpression());return super.visitUnary(v,x); }
      }.scan(unit,null);
      // Alias/escape closure: conservatively poison related local objects.
      Map<Element,Set<Element>> aliases=new HashMap<>();
      for(var entry:declarations.entrySet()) {
        VariableTree v=(VariableTree)entry.getValue().getLeaf();
        if(v.getInitializer() instanceof IdentifierTree && !scalar(entry.getKey())) {
          Element other=trees.getElement(path(entry.getValue(),v.getInitializer()));
          aliases.computeIfAbsent(entry.getKey(),k->new HashSet<>()).add(other);aliases.computeIfAbsent(other,k->new HashSet<>()).add(entry.getKey());
        }
      }
      for(CompilationUnitTree unit:units)new TreePathScanner<Void,Void>() {
        @Override public Void visitMethodInvocation(MethodInvocationTree call,Void x) {
          TreePath p=getCurrentPath();String target=symbol(trees.getElement(p));
          if(call.getMethodSelect() instanceof MemberSelectTree m && m.getExpression() instanceof IdentifierTree) {
            Element receiver=trees.getElement(path(p,m.getExpression()));
            if(!scalar(receiver))mutable.add(receiver); // Local mutable receiver cannot be modeled as its initializer only.
          }
          if(!summary(target))for(ExpressionTree a:call.getArguments())if(a instanceof IdentifierTree) {
            Element argument=trees.getElement(path(p,a));if(!scalar(argument))escaped.add(argument);
          }
          return super.visitMethodInvocation(call,x);
        }
      }.scan(unit,null);
      boolean changed=true;while(changed) { changed=false;for(var a:aliases.entrySet())if(escaped.contains(a.getKey()) || mutable.contains(a.getKey()))for(Element b:a.getValue())if(escaped.add(b))changed=true; }
      for(CompilationUnitTree unit:units)new TreePathScanner<Void,Void>() {
        @Override public Void visitReturn(ReturnTree r,Void x) {
          if(r.getExpression()!=null) {
            TreePath p=getCurrentPath();String sink=node("method_return",p,new JSONObject().put("boundary","method_value_only; control, exceptions and wrapper not proven"));
            edge(expr(path(p,r.getExpression()),new HashSet<>()),sink,"return_value");returns.put(sink);
          }
          return super.visitReturn(r,x);
        }
        @Override public Void visitMethodInvocation(MethodInvocationTree c,Void x) {
          TreePath p=getCurrentPath();String id=expr(p,new HashSet<>());
          if(p.getParentPath().getLeaf() instanceof ExpressionStatementTree)nodes.put(new JSONObject().put("id","n"+nodes.length()).put("kind","discarded_call_value").put("call",id).put("site",site(p)).put("owner",owner(p)).put("execution_effects_still_required",true));
          return super.visitMethodInvocation(c,x);
        }
      }.scan(unit,null);
      Files.writeString(Path.of(args[0]),new JSONObject().put("nodes",nodes).put("edges",edges).put("returns",returns)
        .put("scope","immutable local value uses and explicit API may-depend summaries; not whole-program equivalence")
        .put("automatic_deletion_authorized",false).toString(2)+"\n");
    }
  }
}
