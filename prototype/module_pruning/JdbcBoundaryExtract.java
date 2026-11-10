package research;

import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.lang.model.element.*;
import javax.tools.*;
import java.nio.file.*;
import java.util.*;
import org.json.*;

/** Typed AST binding slices, not control-flow, alias or whole-program proofs. */
public final class JdbcBoundaryExtract {
  static Trees trees;
  static final Map<Element,TreePath> declarations=new HashMap<>();
  static final Set<Element> written=new HashSet<>();
  static final JSONArray bindings=new JSONArray();
  static final JSONArray statements=new JSONArray();
  static TreePath sqlUse;
  static JSONArray sqlGuards;
  static ExpressionTree unwrap(ExpressionTree t) {
    while(t instanceof ParenthesizedTree p) t=p.getExpression();
    return t;
  }
  static boolean throwsDirectly(StatementTree t) {
    if(t instanceof ThrowTree) return true;
    return t instanceof BlockTree b && b.getStatements().size()==1 && b.getStatements().get(0) instanceof ThrowTree;
  }
  static Set<String> guardedStrings(Element variable) {
    if(sqlUse==null || variable==null || written.contains(variable) || !variable.asType().toString().equals("java.lang.String")
      || !Set.of(ElementKind.PARAMETER,ElementKind.LOCAL_VARIABLE).contains(variable.getKind())) return Set.of();
    // Restricted dominance proof: a direct rejecting guard precedes the use's
    // containing statement in the same lexical block. Do not borrow guards
    // across lambda/class/method boundaries or from a sibling try/if block.
    for(TreePath child=sqlUse;child.getParentPath()!=null;child=child.getParentPath()) {
      TreePath parent=child.getParentPath(); Tree scope=parent.getLeaf();
      if(scope instanceof LambdaExpressionTree || scope instanceof ClassTree || scope instanceof MethodTree) break;
      if(!(scope instanceof BlockTree block)) continue;
      int index=block.getStatements().indexOf(child.getLeaf());
      if(index<0) continue;
      for(int i=index-1;i>=0;i--) {
        if(!(block.getStatements().get(i) instanceof IfTree guard) || guard.getElseStatement()!=null || !throwsDirectly(guard.getThenStatement())) continue;
        ExpressionTree condition=unwrap(guard.getCondition());
        if(!(condition instanceof UnaryTree negation) || negation.getKind()!=Tree.Kind.LOGICAL_COMPLEMENT) continue;
        ExpressionTree positive=unwrap(negation.getExpression());
        if(!(positive instanceof MethodInvocationTree contains) || contains.getArguments().size()!=1
          || !(contains.getMethodSelect() instanceof MemberSelectTree select)) continue;
        TreePath call=path(parent,contains);
        if(!symbol(trees.getElement(call)).equals("java.util.Set#contains(java.lang.Object)")) continue;
        if(!variable.equals(trees.getElement(path(parent,unwrap(contains.getArguments().get(0)))))) continue;
        ExpressionTree receiver=unwrap(select.getExpression());
        if(!(receiver instanceof MethodInvocationTree factory)) continue;
        Element resolved=trees.getElement(path(parent,factory));
        if(!(resolved instanceof ExecutableElement method) || !method.getEnclosingElement().toString().equals("java.util.Set")
          || !method.getSimpleName().contentEquals("of") || factory.getArguments().isEmpty() || factory.getArguments().size()>8) continue;
        Set<String> values=new TreeSet<>(); boolean known=true;
        for(ExpressionTree argument:factory.getArguments()) {
          if(unwrap(argument) instanceof LiteralTree literal && literal.getValue() instanceof String text) values.add(text);
          else known=false;
        }
        if(!known) continue;
        CompilationUnitTree unit=parent.getCompilationUnit(); long offset=trees.getSourcePositions().getStartPosition(unit,guard);
        TreePath declaration=declarations.get(variable);
        JSONObject evidence=new JSONObject().put("rule","stable-string-rejecting-set-guard/v1").put("values",values)
          .put("file",Path.of(unit.getSourceFile().toUri()).toString()).put("line",unit.getLineMap().getLineNumber(offset)).put("offset",offset)
          .put("variable_declaration",declaration==null?-1:trees.getSourcePositions().getStartPosition(unit,declaration.getLeaf()))
          .put("condition",guard.getCondition().toString()).put("dominance","preceding direct guard in same containing block")
          .put("stability","no syntactic assignment/compound assignment/increment in supplied units");
        if(declaration!=null && declaration.getLeaf() instanceof VariableTree d && d.getInitializer()!=null)
          evidence.put("initializer",d.getInitializer().toString());
        sqlGuards.put(evidence); return values;
      }
    }
    return Set.of();
  }
  static Set<String> strings(TreePath p,Set<Element> seen) {
    Tree t=p.getLeaf(); Element e=trees.getElement(p);
    if(t instanceof ParenthesizedTree x) return strings(path(p,x.getExpression()),seen);
    if(t instanceof LiteralTree x && x.getValue() instanceof String value) return Set.of(value);
    if(t instanceof BinaryTree x && x.getKind()==Tree.Kind.PLUS) {
      Set<String> a=strings(path(p,x.getLeftOperand()),new HashSet<>(seen)), b=strings(path(p,x.getRightOperand()),new HashSet<>(seen));
      if(a.isEmpty() || b.isEmpty() || a.size()*b.size()>8) return Set.of();
      Set<String> out=new TreeSet<>(); for(String left:a) for(String right:b) out.add(left+right); return out;
    }
    if(t instanceof ConditionalExpressionTree x) {
      Set<String> a=strings(path(p,x.getTrueExpression()),new HashSet<>(seen)), b=strings(path(p,x.getFalseExpression()),new HashSet<>(seen));
      if(a.isEmpty() || b.isEmpty()) return Set.of();
      Set<String> out=new TreeSet<>(a); out.addAll(b); return out.size()>8?Set.of():out;
    }
    if(t instanceof IdentifierTree || t instanceof MemberSelectTree) {
      TreePath d=declarations.get(e);
      if(e==null || d==null || written.contains(e) || !seen.add(e)) return Set.of();
      Set<String> narrowed=guardedStrings(e);
      if(!narrowed.isEmpty()) return narrowed;
      if(e.getKind().isField() && !e.getModifiers().contains(Modifier.FINAL)
        && !e.asType().toString().equals("com.oltpbenchmark.api.SQLStmt")) return Set.of();
      ExpressionTree init=((VariableTree)d.getLeaf()).getInitializer();
      return init==null?Set.of():strings(path(d,init),seen);
    }
    // Explicit API summary: immutable SQLStmt constructor stores this SQL text.
    if(t instanceof NewClassTree x && e instanceof ExecutableElement method
      && method.getEnclosingElement().toString().equals("com.oltpbenchmark.api.SQLStmt") && x.getArguments().size()==1)
      return strings(path(p,x.getArguments().get(0)),seen);
    if(t instanceof MethodInvocationTree x && e instanceof ExecutableElement method
      && method.getEnclosingElement().toString().equals("java.lang.String") && method.getSimpleName().contentEquals("formatted")
      && x.getMethodSelect() instanceof MemberSelectTree select) {
      Set<String> formats=strings(path(p,select.getExpression()),new HashSet<>(seen));
      List<String> values=new ArrayList<>();
      for(ExpressionTree argument:x.getArguments()) {
        Set<String> value=strings(path(p,argument),new HashSet<>(seen)); if(value.size()!=1) return Set.of(); values.add(value.iterator().next());
      }
      Set<String> out=new TreeSet<>();
      for(String format:formats) {
        if(java.util.regex.Pattern.compile("%(?!s|%)").matcher(format.replace("%%","")).find()) return Set.of();
        try { out.add(String.format(Locale.ROOT,format,values.toArray())); } catch(IllegalFormatException error) { return Set.of(); }
      }
      return out;
    }
    return Set.of();
  }
  static String receiverId(Element e,TreePath d) {
    return d==null?"unknown":symbol(e.getEnclosingElement())+"@"+trees.getSourcePositions().getStartPosition(d.getCompilationUnit(),d.getLeaf());
  }
  static TreePath path(TreePath p,Tree t) { return trees.getPath(p.getCompilationUnit(),t); }
  static String symbol(Element e) { return e==null?"unresolved":e.getEnclosingElement()+"#"+e; }
  static JSONObject unknown(String reason) { return new JSONObject().put("op","unknown").put("reason",reason); }
  static JSONObject term(TreePath p,Set<Element> seen) {
    Tree t=p.getLeaf(); Element e=trees.getElement(p);
    if(t instanceof ParenthesizedTree x) return term(path(p,x.getExpression()),seen);
    if(t instanceof LiteralTree x) return new JSONObject().put("op","literal").put("value",x.getValue()==null?JSONObject.NULL:x.getValue());
    if(t instanceof IdentifierTree) {
      if(e==null || written.contains(e) || !seen.add(e)) return unknown("unresolved_written_or_cyclic_variable");
      if(e.getKind()==ElementKind.PARAMETER && e.getEnclosingElement() instanceof ExecutableElement method)
        return new JSONObject().put("op","parameter").put("position",method.getParameters().indexOf(e)).put("type",e.asType().toString());
      TreePath d=declarations.get(e);
      if(e.getKind()==ElementKind.LOCAL_VARIABLE && d!=null && ((VariableTree)d.getLeaf()).getInitializer()!=null)
        return term(path(d,((VariableTree)d.getLeaf()).getInitializer()),seen);
      return unknown("field_or_uninitialized_variable");
    }
    if(t instanceof NewClassTree x) {
      if(!symbol(e).equals("java.sql.Timestamp#Timestamp(long)")) return unknown("unsupported_constructor");
      return new JSONObject().put("op","timestamp").put("value",term(path(p,x.getArguments().get(0)),new HashSet<>(seen)));
    }
    if(t instanceof MethodInvocationTree x && e instanceof ExecutableElement method) {
      String target=symbol(method);
      if(target.equals("java.lang.System#currentTimeMillis()")) return new JSONObject().put("op","wall_clock");
      if(!(x.getMethodSelect() instanceof MemberSelectTree select)) return unknown("implicit_receiver");
      TreePath receiver=path(p,select.getExpression());
      if(target.equals("org.json.JSONObject#getLong(java.lang.String)"))
        return new JSONObject().put("op","json_long").put("receiver",term(receiver,new HashSet<>(seen)))
          .put("key",term(path(p,x.getArguments().get(0)),new HashSet<>(seen)));
      if(target.equals("java.sql.Timestamp#toString()"))
        return new JSONObject().put("op","timestamp_text").put("value",term(receiver,new HashSet<>(seen)));
      return unknown("unsupported_call:"+target);
    }
    return unknown("unsupported_expression:"+t.getKind());
  }
  static JSONArray guards(TreePath p) {
    JSONArray out=new JSONArray();
    for(TreePath q=p.getParentPath();q!=null && !(q.getLeaf() instanceof MethodTree);q=q.getParentPath()) {
      Tree t=q.getLeaf();
      if(t instanceof IfTree || t instanceof ConditionalExpressionTree || t instanceof SwitchTree || t instanceof CaseTree
        || t instanceof ForLoopTree || t instanceof EnhancedForLoopTree || t instanceof WhileLoopTree || t instanceof DoWhileLoopTree
        || t instanceof CatchTree || t instanceof LambdaExpressionTree) out.put(t.getKind().toString());
    }
    return out;
  }
  public static void main(String[] args) throws Exception {
    JavaCompiler compiler=ToolProvider.getSystemJavaCompiler();
    DiagnosticCollector<JavaFileObject> diagnostics=new DiagnosticCollector<>();
    try(StandardJavaFileManager files=compiler.getStandardFileManager(diagnostics,null,null)) {
      JavacTask task=(JavacTask)compiler.getTask(null,files,diagnostics,List.of("-proc:none","-implicit:none","--release","23","-classpath",args[1]),
        null,files.getJavaFileObjectsFromStrings(Arrays.asList(args).subList(2,args.length)));
      List<CompilationUnitTree> units=new ArrayList<>(); task.parse().forEach(units::add); task.analyze(); trees=Trees.instance(task);
      if(diagnostics.getDiagnostics().stream().anyMatch(d->d.getKind()==Diagnostic.Kind.ERROR))
        throw new IllegalArgumentException("Cannot attribute Java: "+diagnostics.getDiagnostics());
      for(CompilationUnitTree unit:units) new TreePathScanner<Void,Void>() {
        @Override public Void visitVariable(VariableTree v,Void x) { declarations.put(trees.getElement(getCurrentPath()),getCurrentPath()); return super.visitVariable(v,x); }
        void write(Tree t) { written.add(trees.getElement(path(getCurrentPath(),t))); }
        @Override public Void visitAssignment(AssignmentTree a,Void x) { write(a.getVariable()); return super.visitAssignment(a,x); }
        @Override public Void visitCompoundAssignment(CompoundAssignmentTree a,Void x) { write(a.getVariable()); return super.visitCompoundAssignment(a,x); }
        @Override public Void visitUnary(UnaryTree a,Void x) {
          if(Set.of(Tree.Kind.PREFIX_INCREMENT,Tree.Kind.POSTFIX_INCREMENT,Tree.Kind.PREFIX_DECREMENT,Tree.Kind.POSTFIX_DECREMENT).contains(a.getKind())) write(a.getExpression());
          return super.visitUnary(a,x);
        }
      }.scan(unit,null);
      for(CompilationUnitTree unit:units) new TreePathScanner<Void,Void>() {
        @Override public Void visitVariable(VariableTree v,Void x) {
          TreePath p=getCurrentPath(); Element variable=trees.getElement(p);
          if(variable!=null && variable.asType().toString().equals("java.sql.PreparedStatement") && v.getInitializer() instanceof MethodInvocationTree init) {
            TreePath call=path(p,init); Element target=trees.getElement(call); Tree sql=null;
            String api=symbol(target);
            if(api.equals("java.sql.Connection#prepareStatement(java.lang.String)")) sql=init.getArguments().get(0);
            if(target instanceof ExecutableElement method && method.getEnclosingElement().toString().equals("com.oltpbenchmark.api.Procedure")
              && method.getSimpleName().contentEquals("getPreparedStatement") && init.getArguments().size()==2
              && trees.getTypeMirror(path(p,init.getArguments().get(1))).toString().equals("com.oltpbenchmark.api.SQLStmt")) sql=init.getArguments().get(1);
            sqlUse=p; sqlGuards=new JSONArray();
            Set<String> alternatives=sql==null?Set.of():strings(path(p,sql),new HashSet<>());
            long offset=trees.getSourcePositions().getStartPosition(unit,v);
            statements.put(new JSONObject().put("receiver",receiverId(variable,p)).put("factory",api)
              .put("file",Path.of(unit.getSourceFile().toUri()).toString()).put("line",unit.getLineMap().getLineNumber(offset))
              .put("sql_alternatives",new TreeSet<>(alternatives)).put("status",alternatives.isEmpty()?"unknown":"finite_sql_set")
              .put("choice_control",sqlGuards.length()>0?"finite target set from dominating rejecting guard; selected member remains dynamic":alternatives.size()>1?"both syntactic alternatives retained; predicate unresolved":"single static SQL")
              .put("guard_constraints",sqlGuards)
              .put("sql_field_premise",api.contains("#getPreparedStatement(")?"SQLStmt initializer and Procedure API summary; external field mutation unresolved":"none")
              .put("receiver_stable",!written.contains(variable)));
          }
          return super.visitVariable(v,x);
        }
        @Override public Void visitMethodInvocation(MethodInvocationTree call,Void x) {
          TreePath p=getCurrentPath(); Element e=trees.getElement(p);
          if(e instanceof ExecutableElement method && method.getEnclosingElement().toString().equals("java.sql.PreparedStatement")
            && call.getMethodSelect() instanceof MemberSelectTree select) {
            TreePath owner=p; while(owner!=null && !(owner.getLeaf() instanceof MethodTree)) owner=owner.getParentPath();
            Element receiver=trees.getElement(path(p,select.getExpression())); TreePath declaration=declarations.get(receiver);
            long offset=trees.getSourcePositions().getStartPosition(unit,call);
            String name=method.getSimpleName().toString();
            JSONObject b=new JSONObject().put("owner",owner==null?"unknown":symbol(trees.getElement(owner)))
              .put("target",symbol(method)).put("operation",name).put("file",Path.of(unit.getSourceFile().toUri()).toString())
              .put("line",unit.getLineMap().getLineNumber(offset)).put("offset",offset).put("guards",guards(p))
              .put("receiver",receiverId(receiver,declaration))
              .put("receiver_stable",receiver!=null && !written.contains(receiver))
              .put("arguments",new JSONArray());
            for(ExpressionTree arg:call.getArguments()) b.getJSONArray("arguments").put(term(path(p,arg),new HashSet<>()));
            bindings.put(b);
          }
          return super.visitMethodInvocation(call,x);
        }
      }.scan(unit,null);
      Files.writeString(Path.of(args[0]),new JSONObject().put("kind","jdbc-boundary-slices/v3").put("bindings",bindings).put("statements",statements)
        .put("whole_program_proof",false).toString(2)+"\n");
    }
  }
}
