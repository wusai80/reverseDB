package research;

import com.sun.source.tree.*;
import com.sun.source.util.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import javax.lang.model.element.*;
import javax.lang.model.type.TypeKind;
import javax.lang.model.type.TypeMirror;
import javax.lang.model.type.PrimitiveType;
import javax.lang.model.util.Elements;
import javax.lang.model.util.Types;
import javax.tools.*;
import org.json.*;

/** Attributed Java AST facts. Not a whole-program effect or deletion prover. */
public final class DependencyExtract {
  static Trees trees;
  static Elements elements;
  static Types typeUtils;
  static final Map<Element,TreePath> variables=new HashMap<>();
  static final Map<Element,TreePath> methods=new HashMap<>();
  static final Set<Element> assigned=new HashSet<>();
  static final Map<CompilationUnitTree,String> texts=new HashMap<>();
  static final JSONArray calls=new JSONArray(), declarations=new JSONArray(), sqlFields=new JSONArray(), effects=new JSONArray();
  static final JSONArray initializers=new JSONArray(), initializedFields=new JSONArray();
  static JSONObject bridge;
  static String symbol(Element e) {
    if(e==null) return "unresolved";
    if(e instanceof TypeElement t) return t.getQualifiedName().toString();
    return e.getEnclosingElement()+"#"+e;
  }
  static String hash(String text) throws Exception {
    return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
  }
  static TreePath path(TreePath from, Tree node) { return trees.getPath(from.getCompilationUnit(),node); }
  static JSONObject location(TreePath p) {
    CompilationUnitTree u=p.getCompilationUnit();
    long start=trees.getSourcePositions().getStartPosition(u,p.getLeaf());
    long end=trees.getSourcePositions().getEndPosition(u,p.getLeaf());
    return new JSONObject().put("file",Path.of(u.getSourceFile().toUri()).toString())
        .put("line",start<0?-1:u.getLineMap().getLineNumber(start)).put("start",start).put("end",end);
  }
  static String source(TreePath p) {
    JSONObject loc=location(p); long a=loc.getLong("start"),b=loc.getLong("end");
    return a<0||b<a?"":texts.get(p.getCompilationUnit()).substring((int)a,(int)b);
  }
  static String owner(TreePath p) {
    for(TreePath q=p;q!=null;q=q.getParentPath()) {
      if(q.getLeaf() instanceof MethodTree) return symbol(trees.getElement(q));
      TreePath parent=q.getParentPath();
      if(parent!=null && parent.getLeaf() instanceof ClassTree) {
        Element type=trees.getElement(parent);
        if(q.getLeaf() instanceof VariableTree) {
          Element field=trees.getElement(q);
          return initializer(type,field!=null && field.getModifiers().contains(Modifier.STATIC));
        }
        if(q.getLeaf() instanceof BlockTree block) return initializer(type,block.isStatic());
      }
      // Do not assign nested/anonymous class initialization to an enclosing method.
      if(q.getLeaf() instanceof ClassTree) return initializer(trees.getElement(q),false);
    }
    return "field-initializer";
  }
  static String initializer(Element type,boolean isStatic) {
    return symbol(type)+(isStatic?"#<clinit>":"#<instance-init>");
  }
  static JSONObject stableGuardVariable(TreePath p) {
    while(p.getLeaf() instanceof ParenthesizedTree par) p=path(p,par.getExpression());
    if(!(p.getLeaf() instanceof IdentifierTree)) return null;
    Element e=trees.getElement(p);
    if(e==null || !Set.of(ElementKind.LOCAL_VARIABLE,ElementKind.PARAMETER).contains(e.getKind())
        || assigned.contains(e) || !Set.of("java.lang.String","boolean").contains(e.asType().toString())) return null;
    TreePath declaration=variables.get(e);
    if(declaration==null) return null;
    JSONObject loc=location(declaration);
    return new JSONObject().put("id",owner(declaration)+"@"+loc.getString("file")+":"+loc.getLong("start"))
        .put("name",e.getSimpleName().toString()).put("type",e.asType().toString())
        .put("declaration",loc).put("scope",owner(declaration));
  }
  static JSONObject guardAtom(TreePath p,boolean branch) {
    Tree t=p.getLeaf();
    if(t instanceof ParenthesizedTree par) return guardAtom(path(p,par.getExpression()),branch);
    if(t instanceof UnaryTree un && un.getKind()==Tree.Kind.LOGICAL_COMPLEMENT)
      return guardAtom(path(p,un.getExpression()),!branch);
    JSONObject variable=stableGuardVariable(p);
    if(variable!=null && variable.getString("type").equals("boolean"))
      return new JSONObject().put("variable",variable).put("operator","in").put("values",List.of(branch));
    if(t instanceof LiteralTree literal && literal.getValue() instanceof Boolean value)
      return new JSONObject().put("constant",value==branch);
    if(t instanceof MethodInvocationTree call && call.getArguments().size()==1
        && call.getMethodSelect() instanceof MemberSelectTree select) {
      Element target=trees.getElement(p);
      if(target instanceof ExecutableElement ex && ex.getEnclosingElement().toString().equals("java.lang.String")
          && ex.getSimpleName().contentEquals("equals")) {
        TreePath receiver=path(p,select.getExpression()), argument=path(p,call.getArguments().getFirst());
        variable=stableGuardVariable(receiver);
        Object literal=constant(argument,new HashSet<>());
        if(variable==null || !variable.getString("type").equals("java.lang.String") || !(literal instanceof String)) {
          variable=stableGuardVariable(argument); literal=constant(receiver,new HashSet<>());
        }
        if(variable!=null && variable.getString("type").equals("java.lang.String") && literal instanceof String)
          return new JSONObject().put("variable",variable).put("operator",branch?"in":"not_in").put("values",List.of(literal));
      }
    }
    return null;
  }
  static JSONObject argumentValue(TreePath p) {
    JSONObject variable=stableGuardVariable(p);
    if(variable!=null) return new JSONObject().put("kind","stable_variable").put("variable",variable);
    while(p.getLeaf() instanceof ParenthesizedTree par) p=path(p,par.getExpression());
    if(p.getLeaf() instanceof LiteralTree literal && (literal.getValue() instanceof Boolean || literal.getValue() instanceof String))
      return new JSONObject().put("kind","literal").put("value",literal.getValue())
          .put("type",literal.getValue() instanceof Boolean?"boolean":"java.lang.String");
    return new JSONObject().put("kind","unknown").put("expression",p.getLeaf().toString());
  }
  static JSONObject directArguments(TreePath p,MethodInvocationTree call,Element target) {
    JSONObject result=new JSONObject().put("status","unknown").put("bindings",new JSONArray());
    if(!(target instanceof ExecutableElement ex) || ex.isVarArgs() || !methods.containsKey(target))
      return result.put("reason","external_or_varargs_target");
    if(!(ex.getModifiers().contains(Modifier.STATIC)||ex.getModifiers().contains(Modifier.PRIVATE)||ex.getModifiers().contains(Modifier.FINAL)))
      return result.put("reason","dynamic_dispatch_not_closed");
    MethodTree declaration=(MethodTree)methods.get(target).getLeaf();
    if(declaration.getBody()==null || call.getArguments().size()!=declaration.getParameters().size())
      return result.put("reason","body_or_arity_unknown");
    JSONArray bindings=new JSONArray();
    for(int i=0;i<declaration.getParameters().size();i++) {
      VariableTree formal=declaration.getParameters().get(i);
      JSONObject variable=null;
      // Parameter declarations use the same identity as their stable variable uses.
      Element parameter=trees.getElement(path(methods.get(target),formal));
      if(parameter!=null && !assigned.contains(parameter) && Set.of("boolean","java.lang.String").contains(parameter.asType().toString())) {
        TreePath decl=path(methods.get(target),formal); JSONObject loc=location(decl);
        variable=new JSONObject().put("id",owner(decl)+"@"+loc.getString("file")+":"+loc.getLong("start"))
            .put("name",parameter.getSimpleName().toString()).put("type",parameter.asType().toString())
            .put("scope",owner(decl)).put("declaration",loc);
      }
      bindings.put(new JSONObject().put("position",i).put("formal",variable==null?JSONObject.NULL:variable)
          .put("actual",argumentValue(path(p,call.getArguments().get(i)))));
    }
    return result.put("status","direct_source_target").put("target",symbol(target)).put("bindings",bindings);
  }
  static JSONObject reflectionArguments(TreePath p,MethodInvocationTree call,JSONObject resolution) {
    JSONObject result=new JSONObject().put("status","unknown").put("bindings",new JSONArray())
        .put("bridge_symbol",bridge.getString("symbol")).put("bridge_method_sha256",bridge.getString("method_sha256"))
        .put("invocation_may_fail",true);
    if(!resolution.optString("status").equals("bounded_candidates")) return result.put("reason",resolution.optString("reason","target_unknown"));
    if(resolution.getJSONArray("receiver_types").length()!=1 || resolution.getJSONArray("targets").length()!=1
        || !resolution.getJSONArray("ambiguous_receiver_types").isEmpty() || !resolution.getJSONArray("no_matching_method").isEmpty())
      return result.put("reason","target_not_unique_for_exact_receiver");
    String target=resolution.getJSONArray("targets").getString(0);
    TypeElement receiver=elements.getTypeElement(resolution.getJSONArray("receiver_types").getString(0));
    ExecutableElement ex=(ExecutableElement)receiver.getEnclosedElements().stream().filter(e->symbol(e).equals(target)).findFirst().orElseThrow();
    if(ex.isVarArgs()) return result.put("reason","target_varargs_not_supported");
    if(ex.getParameters().size()!=call.getArguments().size()-2) return result.put("reason","arity_mismatch");
    JSONArray bindings=new JSONArray();
    for(int i=0;i<ex.getParameters().size();i++) {
      VariableElement parameter=ex.getParameters().get(i);
      TreePath actualPath=path(p,call.getArguments().get(i+2));
      TypeMirror actualType=trees.getTypeMirror(actualPath), formalType=parameter.asType();
      boolean compatible=false;
      if(formalType.getKind().isPrimitive()) {
        compatible=typeUtils.isSameType(actualType,formalType)
            || typeUtils.isSameType(actualType,typeUtils.boxedClass((PrimitiveType)formalType).asType());
      } else if(Set.of(TypeKind.DECLARED,TypeKind.ARRAY).contains(formalType.getKind())) {
        TypeMirror boxed=actualType.getKind().isPrimitive()?typeUtils.boxedClass((PrimitiveType)actualType).asType():actualType;
        compatible=Set.of(TypeKind.DECLARED,TypeKind.ARRAY,TypeKind.NULL).contains(boxed.getKind()) && typeUtils.isAssignable(boxed,formalType);
      }
      if(!compatible) return result.put("reason","argument_type_not_proven").put("position",i)
          .put("actual_type",actualType.toString()).put("formal_type",formalType.toString());
      JSONObject variable=null; TreePath decl=variables.get(parameter);
      if(decl!=null && !assigned.contains(parameter) && Set.of("boolean","java.lang.String").contains(formalType.toString())) {
        JSONObject loc=location(decl);
        variable=new JSONObject().put("id",owner(decl)+"@"+loc.getString("file")+":"+loc.getLong("start"))
            .put("name",parameter.getSimpleName().toString()).put("type",formalType.toString()).put("scope",owner(decl)).put("declaration",loc);
      }
      bindings.put(new JSONObject().put("position",i).put("bridge_argument_index",i+2)
          .put("formal_name",parameter.getSimpleName().toString()).put("formal_type",formalType.toString())
          .put("actual_type",actualType.toString()).put("type_compatible",true).put("location",location(actualPath))
          .put("formal",variable==null?JSONObject.NULL:variable).put("actual",argumentValue(actualPath)));
    }
    boolean body=methods.containsKey(ex) && ((MethodTree)methods.get(ex).getLeaf()).getBody()!=null;
    return result.put("status","verified_bridge_target").put("target",target).put("bindings",bindings)
        .put("parameter_count",ex.getParameters().size()).put("body_available",body)
        .put("scope","positional mapping on successful invocation; exceptions remain observable");
  }
  static JSONObject lexicalConditions(TreePath p) {
    JSONArray atoms=new JSONArray(),unknowns=new JSONArray();
    for(TreePath q=p;q!=null && q.getParentPath()!=null;q=q.getParentPath()) {
      Tree child=q.getLeaf(), parent=q.getParentPath().getLeaf();
      if(parent instanceof MethodTree || parent instanceof ClassTree) break;
      if(parent instanceof LambdaExpressionTree) {
        unknowns.put(new JSONObject().put("reason","deferred_boundary_outer_conditions_not_inherited").put("location",location(q.getParentPath())));
        break;
      }
      if(parent instanceof IfTree branch && (child==branch.getThenStatement() || child==branch.getElseStatement())) {
        boolean taken=child==branch.getThenStatement();
        JSONObject atom=guardAtom(path(q,branch.getCondition()),taken);
        JSONObject evidence=new JSONObject().put("location",location(q.getParentPath())).put("expression",branch.getCondition().toString()).put("branch",taken);
        if(atom==null) unknowns.put(evidence.put("reason","unsupported_or_mutated_condition"));
        else atoms.put(atom.put("evidence",evidence));
      }
      if(child instanceof CaseTree c && (parent instanceof SwitchTree || parent instanceof SwitchExpressionTree)) {
        ExpressionTree selector=parent instanceof SwitchTree s?s.getExpression():((SwitchExpressionTree)parent).getExpression();
        JSONObject variable=stableGuardVariable(path(q,selector));
        List<? extends CaseTree> cases=parent instanceof SwitchTree s?s.getCases():((SwitchExpressionTree)parent).getCases();
        boolean valid=variable!=null && variable.getString("type").equals("java.lang.String");
        Set<String> allLabels=new TreeSet<>();
        for(CaseTree other:cases) {
          if(other.getCaseKind()!=CaseTree.CaseKind.RULE || other.getGuard()!=null) { valid=false; break; }
          if(other.getLabels().size()==1 && other.getLabels().getFirst() instanceof DefaultCaseLabelTree) continue;
          Set<String> labels=caseStrings(q,other);
          if(labels==null) { valid=false; break; }
          allLabels.addAll(labels);
        }
        boolean fallback=c.getLabels().size()==1 && c.getLabels().getFirst() instanceof DefaultCaseLabelTree;
        Set<String> labels=fallback?allLabels:caseStrings(q,c);
        if(valid && labels!=null) atoms.put(new JSONObject().put("variable",variable)
            .put("operator",fallback?"not_in":"in").put("values",labels)
            .put("evidence",new JSONObject().put("location",location(q)).put("expression",selector.toString()).put("default",fallback)));
        else unknowns.put(new JSONObject().put("reason","unsupported_switch_or_mutated_selector").put("location",location(q)));
      }
      if(parent instanceof ConditionalExpressionTree || parent instanceof WhileLoopTree || parent instanceof DoWhileLoopTree
          || parent instanceof ForLoopTree || parent instanceof EnhancedForLoopTree || parent instanceof CatchTree
          || parent instanceof TryTree || parent instanceof BinaryTree b && (b.getKind()==Tree.Kind.CONDITIONAL_AND || b.getKind()==Tree.Kind.CONDITIONAL_OR))
        unknowns.put(new JSONObject().put("reason","control_not_solved").put("kind",parent.getKind().name()).put("location",location(q.getParentPath())));
    }
    return new JSONObject().put("atoms",atoms).put("unknowns",unknowns)
        .put("semantics","necessary lexical conditions only; not full path or runtime feasibility")
        .put("full_path_condition_complete",false);
  }
  static JSONObject constructorCall(TreePath p,Element e,String expression,String kind) {
    boolean bounded=e instanceof ExecutableElement ex && ex.getKind()==ElementKind.CONSTRUCTOR
        && ex.getEnclosingElement() instanceof TypeElement type
        && type.getNestingKind()!=NestingKind.ANONYMOUS && type.getNestingKind()!=NestingKind.LOCAL;
    JSONObject r=new JSONObject().put("caller",owner(p))
        .put("target_symbol",bounded?symbol(e):"unknown-construction:"+location(p).getString("file")+":"+location(p).getLong("start"))
        .put("location",location(p)).put("expression",expression).put("call_kind",kind)
        .put("result_use",use(p)).put("effect_summary_complete",false)
        .put("dispatch",bounded?"nonvirtual_constructor":"constructor_target_unknown")
        .put("sql_field_arguments",new JSONArray()).put("constant_string_arguments",new JSONArray())
        .put("declared_throws",e instanceof ExecutableElement ex?ex.getThrownTypes().stream().map(Object::toString).toList():List.of());
    return r.put("construction_target_complete",bounded).put("conditions",lexicalConditions(p));
  }
  static Object constant(TreePath p, Set<Element> seen) {
    Tree t=p.getLeaf();
    if(t instanceof LiteralTree l) return l.getValue();
    if(t instanceof ParenthesizedTree x) return constant(path(p,x.getExpression()),seen);
    if(t instanceof BinaryTree b && b.getKind()==Tree.Kind.PLUS) {
      Object a=constant(path(p,b.getLeftOperand()),new HashSet<>(seen)),c=constant(path(p,b.getRightOperand()),new HashSet<>(seen));
      return a instanceof String && c instanceof String?(String)a+(String)c:null;
    }
    if(t instanceof IdentifierTree || t instanceof MemberSelectTree) {
      Element e=trees.getElement(p);
      if(e instanceof VariableElement v && v.getConstantValue()!=null) return v.getConstantValue();
      if(e!=null && !e.getKind().isField() && !assigned.contains(e) && seen.add(e) && variables.containsKey(e)) {
        VariableTree v=(VariableTree)variables.get(e).getLeaf();
        if(v.getInitializer()!=null) return constant(path(variables.get(e),v.getInitializer()),seen);
      }
    }
    if(t instanceof MethodInvocationTree m && m.getMethodSelect() instanceof MemberSelectTree select) {
      Element target=trees.getElement(p);
      if(target instanceof ExecutableElement ex && ex.getEnclosingElement().toString().equals("java.lang.String") && ex.getSimpleName().contentEquals("formatted")) {
        Object raw=constant(path(p,select.getExpression()),seen);
        if(!(raw instanceof String format)) return null;
        // Support only literal %s substitutions; reject all other formatting.
        if(format.replace("%s","").contains("%") || format.split("%s",-1).length-1!=m.getArguments().size()) return null;
        String[] parts=format.split("%s",-1); StringBuilder expanded=new StringBuilder(parts[0]); int index=0;
        for(ExpressionTree arg:m.getArguments()) {
          Object value=constant(path(p,arg),new HashSet<>(seen));
          if(!(value instanceof String s)) return null;
          expanded.append(s).append(parts[++index]);
        }
        return expanded.toString();
      }
    }
    return null;
  }
  record BranchConstraint(Set<String> labels, JSONObject witness) {}
  static Element stableStringSelector(TreePath p) {
    while(p.getLeaf() instanceof ParenthesizedTree x) p=path(p,x.getExpression());
    if(!(p.getLeaf() instanceof IdentifierTree)) return null;
    Element e=trees.getElement(p);
    if(e==null || !Set.of(ElementKind.LOCAL_VARIABLE,ElementKind.PARAMETER).contains(e.getKind())
        || !e.asType().toString().equals("java.lang.String") || assigned.contains(e)) return null;
    return e;
  }
  static Set<String> caseStrings(TreePath p,CaseTree c) {
    if(c.getCaseKind()!=CaseTree.CaseKind.RULE || c.getGuard()!=null) return null;
    Set<String> values=new TreeSet<>();
    for(CaseLabelTree label:c.getLabels()) {
      if(!(label instanceof ConstantCaseLabelTree literal)) return null;
      Object value=constant(path(p,literal.getConstantExpression()),new HashSet<>());
      if(!(value instanceof String s)) return null;
      values.add(s);
    }
    return values.isEmpty()?null:values;
  }
  static BranchConstraint enclosingBranch(TreePath call,Element selector) {
    for(TreePath q=call;q!=null;q=q.getParentPath()) {
      Tree t=q.getLeaf();
      // Do not carry lexical facts into deferred or nested execution contexts.
      if(t instanceof LambdaExpressionTree || t instanceof ClassTree || t instanceof MethodTree) break;
      if(t instanceof CaseTree c && q.getParentPath()!=null) {
        Tree sw=q.getParentPath().getLeaf();
        ExpressionTree expression=sw instanceof SwitchTree s?s.getExpression():sw instanceof SwitchExpressionTree s?s.getExpression():null;
        if(expression!=null && Objects.equals(selector,stableStringSelector(path(q,expression)))) {
          Set<String> labels=caseStrings(q,c);
          if(labels!=null) return new BranchConstraint(labels,new JSONObject().put("consumer_case",location(q))
              .put("selector_symbol",symbol(selector)).put("labels",labels)
              .put("rule","same immutable-value String variable, no assignments, arrow case"));
        }
      }
    }
    return null;
  }
  static List<? extends CaseTree> selectedCases(TreePath p,SwitchExpressionTree sw,TreePath call,JSONArray witnesses) {
    if(call==null) return sw.getCases();
    Element selector=stableStringSelector(path(p,sw.getExpression()));
    if(selector==null) return sw.getCases();
    BranchConstraint constraint=enclosingBranch(call,selector);
    if(constraint==null) return sw.getCases();
    Map<String,CaseTree> alternatives=new HashMap<>(); CaseTree fallback=null;
    for(CaseTree c:sw.getCases()) {
      if(c.getCaseKind()!=CaseTree.CaseKind.RULE || c.getGuard()!=null) return sw.getCases();
      if(c.getLabels().size()==1 && c.getLabels().getFirst() instanceof DefaultCaseLabelTree) { fallback=c; continue; }
      Set<String> labels=caseStrings(p,c);
      if(labels==null) return sw.getCases();
      for(String label:labels) alternatives.put(label,c);
    }
    Set<CaseTree> selected=new LinkedHashSet<>();
    for(String value:constraint.labels()) {
      CaseTree c=alternatives.getOrDefault(value,fallback);
      if(c==null) return sw.getCases();
      selected.add(c);
    }
    JSONArray positions=new JSONArray(); for(CaseTree c:selected) positions.put(location(path(p,c)));
    witnesses.put(new JSONObject(constraint.witness().toString()).put("allocation_switch",location(p)).put("selected_allocation_cases",positions));
    return new ArrayList<>(selected);
  }
  static Set<String> allocations(TreePath p,Set<Element> seen) {
    return allocations(p,seen,null,new JSONArray());
  }
  static Set<String> allocations(TreePath p,Set<Element> seen,TreePath call,JSONArray witnesses) {
    Tree t=p.getLeaf();
    if(t instanceof NewClassTree n && n.getClassBody()==null) {
      Element e=trees.getElement(path(p,n.getIdentifier()));
      return e instanceof TypeElement type?new TreeSet<>(Set.of(type.getQualifiedName().toString())):null;
    }
    if(t instanceof ParenthesizedTree x) return allocations(path(p,x.getExpression()),seen,call,witnesses);
    if(t instanceof SwitchExpressionTree sw) {
      Set<String> result=new TreeSet<>();
      for(CaseTree c:selectedCases(p,sw,call,witnesses)) {
        Tree body=c.getBody();
        if(body instanceof ThrowTree) continue; // no receiver value on this alternative
        if(!(body instanceof ExpressionTree)) return null;
        Set<String> part=allocations(path(p,body),new HashSet<>(seen),call,witnesses);
        if(part==null) return null;
        result.addAll(part);
      }
      return result.isEmpty()?null:result;
    }
    if(t instanceof IdentifierTree) {
      Element e=trees.getElement(p);
      if(e!=null && !e.getKind().isField() && !assigned.contains(e) && seen.add(e) && variables.containsKey(e)) {
        TreePath declaration=variables.get(e); VariableTree v=(VariableTree)declaration.getLeaf();
        if(v.getInitializer()!=null) return allocations(path(declaration,v.getInitializer()),seen,call,witnesses);
      }
    }
    return null; // aliases through fields, mutation, factories and parameters remain unknown
  }
  static JSONObject use(TreePath call) {
    TreePath q=call;
    while(q.getParentPath()!=null && (q.getParentPath().getLeaf() instanceof TypeCastTree || q.getParentPath().getLeaf() instanceof ParenthesizedTree)) q=q.getParentPath();
    TreePath parent=q.getParentPath(); Tree t=parent==null?null:parent.getLeaf();
    JSONObject r=new JSONObject();
    if(t instanceof ExpressionStatementTree) return r.put("kind","discarded");
    if(t instanceof VariableTree v && v.getInitializer()==q.getLeaf()) {
      Element variable=trees.getElement(parent); JSONArray references=new JSONArray();
      TreePath method=call;
      while(method!=null && !(method.getLeaf() instanceof MethodTree)) method=method.getParentPath();
      if(method!=null) new TreePathScanner<Void,Void>() {
        @Override public Void visitIdentifier(IdentifierTree node,Void unused) {
          if(Objects.equals(trees.getElement(getCurrentPath()),variable)) references.put(location(getCurrentPath()));
          return super.visitIdentifier(node,unused);
        }
      }.scan(method,null);
      return r.put("kind","assigned_local").put("variable",v.getName().toString()).put("symbol_references",references)
          .put("liveness_proven",false); // includes writes; no SSA or path-sensitive liveness claim
    }
    if(t instanceof ReturnTree) return r.put("kind","returned");
    return r.put("kind","expression_consumed").put("parent_kind",t==null?"unknown":t.getKind().name());
  }
  static JSONObject reflection(TreePath p,MethodInvocationTree call) {
    List<? extends ExpressionTree> args=call.getArguments();
    JSONObject r=new JSONObject().put("contract","manually summarized, method-hash-bound declared-name-arity bridge");
    if(args.size()<2) return r.put("status","unknown").put("reason","arity");
    Object name=constant(path(p,args.get(1)),new HashSet<>());
    if(!(name instanceof String)) return r.put("status","unknown").put("reason","dynamic_method_name");
    r.put("requested_name",name);
    if(args.size()==3 && Set.of(TypeKind.ARRAY,TypeKind.NULL).contains(trees.getTypeMirror(path(p,args.get(2))).getKind()))
      return r.put("status","unknown").put("reason","packed_varargs");
    Set<String> before=allocations(path(p,args.getFirst()),new HashSet<>());
    JSONArray witnesses=new JSONArray();
    Set<String> types=allocations(path(p,args.getFirst()),new HashSet<>(),p,witnesses);
    r.put("unconditioned_receiver_types",before==null?JSONObject.NULL:new JSONArray(before)).put("context_witnesses",witnesses);
    if(types==null) return r.put("status","unknown").put("reason","receiver_points_to_unknown");
    JSONArray targets=new JSONArray(),misses=new JSONArray(),ambiguous=new JSONArray();
    for(String type:types) {
      TypeElement e=elements.getTypeElement(type); int found=0;
      for(Element member:e.getEnclosedElements()) if(member instanceof ExecutableElement ex && ex.getKind()==ElementKind.METHOD && ex.getSimpleName().contentEquals((String)name) && ex.getParameters().size()==args.size()-2) {
        targets.put(symbol(ex)); found++;
      }
      if(found==0) misses.put(type);
      if(found>1) ambiguous.put(type);
    }
    return r.put("status","bounded_candidates").put("receiver_types",types).put("targets",targets)
        .put("no_matching_method",misses).put("ambiguous_receiver_types",ambiguous)
        .put("path_sensitive",!witnesses.isEmpty()).put("context_scope","same stable String selector in arrow cases only")
        .put("invocation_may_fail",true);
  }
  public static void main(String[] args) throws Exception {
    // args: output, classpath, bridge-contract JSON, source files...
    bridge=new JSONObject(Files.readString(Path.of(args[2])));
    JavaCompiler compiler=ToolProvider.getSystemJavaCompiler();
    DiagnosticCollector<JavaFileObject> diagnostics=new DiagnosticCollector<>();
    try(StandardJavaFileManager fm=compiler.getStandardFileManager(diagnostics,null,null)) {
      JavacTask task=(JavacTask)compiler.getTask(null,fm,diagnostics,List.of("-proc:none","-implicit:none","--release","23","-classpath",args[1]),null,fm.getJavaFileObjectsFromStrings(Arrays.asList(args).subList(3,args.length)));
      List<CompilationUnitTree> units=new ArrayList<>(); task.parse().forEach(units::add);
      task.analyze();
      for(Diagnostic<?> d:diagnostics.getDiagnostics()) if(d.getKind()==Diagnostic.Kind.ERROR) throw new IllegalArgumentException(d.toString());
      trees=Trees.instance(task); elements=task.getElements(); typeUtils=task.getTypes();
      for(CompilationUnitTree unit:units) {
        texts.put(unit,unit.getSourceFile().getCharContent(true).toString());
        new TreePathScanner<Void,Void>() {
          @Override public Void visitVariable(VariableTree v,Void x) { variables.put(trees.getElement(getCurrentPath()),getCurrentPath()); return super.visitVariable(v,x); }
          @Override public Void visitMethod(MethodTree m,Void x) { methods.put(trees.getElement(getCurrentPath()),getCurrentPath()); return super.visitMethod(m,x); }
          @Override public Void visitAssignment(AssignmentTree a,Void x) { assigned.add(trees.getElement(path(getCurrentPath(),a.getVariable()))); return super.visitAssignment(a,x); }
          @Override public Void visitCompoundAssignment(CompoundAssignmentTree a,Void x) { assigned.add(trees.getElement(path(getCurrentPath(),a.getVariable()))); return super.visitCompoundAssignment(a,x); }
          @Override public Void visitUnary(UnaryTree a,Void x) {
            if(Set.of(Tree.Kind.POSTFIX_INCREMENT,Tree.Kind.POSTFIX_DECREMENT,Tree.Kind.PREFIX_INCREMENT,Tree.Kind.PREFIX_DECREMENT).contains(a.getKind())) assigned.add(trees.getElement(path(getCurrentPath(),a.getExpression())));
            return super.visitUnary(a,x);
          }
        }.scan(unit,null);
      }
      TreePath bridgeMethod=methods.entrySet().stream().filter(e->symbol(e.getKey()).equals(bridge.getString("symbol"))).map(Map.Entry::getValue).findFirst().orElseThrow();
      if(!hash(source(bridgeMethod)).equals(bridge.getString("method_sha256"))) throw new IllegalArgumentException("Reflection bridge contract source changed");
      for(CompilationUnitTree unit:units) new TreePathScanner<Void,Void>() {
        @Override public Void visitClass(ClassTree c,Void x) {
          TreePath p=getCurrentPath(); Element e=trees.getElement(p);
          for(boolean isStatic:List.of(true,false)) initializers.put(new JSONObject()
              .put("symbol",initializer(e,isStatic)).put("owner_type",symbol(e)).put("static",isStatic)
              .put("location",location(p)).put("effect_summary_complete",false)
              .put("owner_identity_complete",e instanceof TypeElement t && t.getNestingKind()!=NestingKind.ANONYMOUS && t.getNestingKind()!=NestingKind.LOCAL));
          return super.visitClass(c,x);
        }
        @Override public Void visitMethod(MethodTree m,Void x) {
          TreePath p=getCurrentPath(); Element e=trees.getElement(p);
          if(e instanceof ExecutableElement ex) declarations.put(new JSONObject().put("symbol",symbol(e)).put("location",location(p)).put("declared_throws",ex.getThrownTypes().stream().map(Object::toString).toList()).put("source_available",!source(p).isEmpty())
              .put("constructor",ex.getKind()==ElementKind.CONSTRUCTOR).put("owner_type",symbol(ex.getEnclosingElement())));
          return super.visitMethod(m,x);
        }
        @Override public Void visitVariable(VariableTree v,Void x) {
          TreePath p=getCurrentPath(); Element e=trees.getElement(p);
          if(e!=null && e.getKind().isField() && v.getInitializer()!=null) initializedFields.put(new JSONObject()
              .put("symbol",symbol(e)).put("initializer",initializer(e.getEnclosingElement(),e.getModifiers().contains(Modifier.STATIC)))
              .put("location",location(p)));
          if(e!=null && e.getKind().isField() && e.asType().toString().equals("com.oltpbenchmark.api.SQLStmt") && v.getInitializer() instanceof NewClassTree n) {
            Object text=n.getArguments().size()==1?constant(path(p,n.getArguments().getFirst()),new HashSet<>()):null;
            sqlFields.put(new JSONObject().put("symbol",symbol(e)).put("location",location(p)).put("constant_sql",text==null?JSONObject.NULL:text).put("effect","unknown_until_SQL_and_environment_summary"));
          }
          return super.visitVariable(v,x);
        }
        @Override public Void visitThrow(ThrowTree t,Void x) {
          effects.put(new JSONObject().put("method",owner(getCurrentPath())).put("kind","explicit_throw").put("expression",t.getExpression().toString()).put("location",location(getCurrentPath())).put("conditions",lexicalConditions(getCurrentPath())));
          return super.visitThrow(t,x);
        }
        @Override public Void visitAssignment(AssignmentTree a,Void x) {
          Element e=trees.getElement(path(getCurrentPath(),a.getVariable()));
          if(a.getVariable() instanceof ArrayAccessTree || (e!=null && e.getKind().isField())) effects.put(new JSONObject().put("method",owner(getCurrentPath())).put("kind","heap_write_possible").put("location",location(getCurrentPath())).put("conditions",lexicalConditions(getCurrentPath())));
          return super.visitAssignment(a,x);
        }
        @Override public Void visitMethodInvocation(MethodInvocationTree m,Void x) {
          TreePath p=getCurrentPath(); Element e=trees.getElement(p);
          if(e instanceof ExecutableElement ex && ex.getKind()==ElementKind.CONSTRUCTOR) {
            calls.put(constructorCall(p,e,m.toString(),"constructor_chain"));
            return super.visitMethodInvocation(m,x);
          }
          JSONObject call=new JSONObject().put("caller",owner(p)).put("target_symbol",symbol(e)).put("location",location(p)).put("result_use",use(p)).put("expression",m.toString()).put("effect_summary_complete",false).put("conditions",lexicalConditions(p)).put("argument_transfer",directArguments(p,m,e));
          JSONArray sqlRefs=new JSONArray(),strings=new JSONArray();
          for(ExpressionTree arg:m.getArguments()) {
            Element a=trees.getElement(path(p,arg));
            if(a!=null && a.asType().toString().equals("com.oltpbenchmark.api.SQLStmt")) sqlRefs.put(symbol(a));
            Object c=constant(path(p,arg),new HashSet<>());
            if(c instanceof String s) strings.put(s);
          }
          call.put("sql_field_arguments",sqlRefs).put("constant_string_arguments",strings);
          if(e instanceof ExecutableElement ex) {
            call.put("declared_throws",ex.getThrownTypes().stream().map(Object::toString).toList());
            call.put("dispatch",ex.getModifiers().contains(Modifier.STATIC)||ex.getModifiers().contains(Modifier.PRIVATE)||ex.getModifiers().contains(Modifier.FINAL)?"nonvirtual_symbol":"virtual_targets_not_closed");
          }
          if(symbol(e).equals(bridge.getString("symbol"))) {
            JSONObject resolution=reflection(p,m);
            call.put("reflection",resolution).put("reflection_argument_transfer",reflectionArguments(p,m,resolution));
          }
          calls.put(call);
          return super.visitMethodInvocation(m,x);
        }
        @Override public Void visitNewClass(NewClassTree n,Void x) {
          calls.put(constructorCall(getCurrentPath(),trees.getElement(getCurrentPath()),n.toString(),"new_class"));
          return super.visitNewClass(n,x);
        }
        @Override public Void visitMemberReference(MemberReferenceTree r,Void x) {
          if(r.getMode()==MemberReferenceTree.ReferenceMode.NEW) {
            JSONObject call=constructorCall(getCurrentPath(),trees.getElement(getCurrentPath()),r.toString(),"constructor_reference_may_target");
            call.put("activation_unknown",true);
            calls.put(call);
          }
          return super.visitMemberReference(r,x);
        }
      }.scan(unit,null);
      JSONObject report=new JSONObject().put("status","passed").put("methods",declarations).put("calls",calls).put("sql_fields",sqlFields).put("effects",effects)
          .put("initializers",initializers).put("initialized_fields",initializedFields)
          .put("reflection_bridge",bridge).put("dependency_summary_complete",false).put("automatic_deletion_authorized",false);
      Files.writeString(Path.of(args[0]),report.toString(2)+"\n");
    }
  }
}
