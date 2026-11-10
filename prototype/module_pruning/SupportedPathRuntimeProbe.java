package com.oltpbenchmark.api;

import java.nio.file.Files;
import java.nio.file.Path;
import org.json.JSONArray;
import org.json.JSONObject;

/** Executes isolated, instrumented copies of archived analysis fixtures. */
public final class SupportedPathRuntimeProbe {
  interface Action { void run() throws Exception; }
  static final JSONArray rows = new JSONArray();
  static void sample(String id, int expectedHits, String expectedError, Action action) {
    PathConditionFixture.hits=0;
    InterproceduralFixture.hits=0;
    ReflectionTransferFixture.hits=0;
    PathConditionFixture.hitSites.clear();
    InterproceduralFixture.hitSites.clear();
    ReflectionTransferFixture.hitSites.clear();
    String error=null, message=null;
    try { action.run(); }
    catch (Exception e) { error=e.getClass().getName(); message=e.getMessage(); }
    JSONObject observed=new JSONObject()
        .put("local_hits",PathConditionFixture.hits)
        .put("direct_hits",InterproceduralFixture.hits)
        .put("reflection_hits",ReflectionTransferFixture.hits)
        .put("local_sites",new JSONArray(PathConditionFixture.hitSites))
        .put("direct_sites",new JSONArray(InterproceduralFixture.hitSites))
        .put("reflection_sites",new JSONArray(ReflectionTransferFixture.hitSites))
        .put("error",error==null?JSONObject.NULL:error)
        .put("error_message",message==null?JSONObject.NULL:message)
        .put("completion",error==null?"void_return":"exception");
    rows.put(new JSONObject().put("id",id).put("observed",observed)
        .put("expected_hits",expectedHits).put("expected_error",expectedError==null?JSONObject.NULL:expectedError));
  }
  public static void main(String[] args) throws Exception {
    for(boolean b:new boolean[]{false,true}) {
      sample("local.opposite."+b,0,null,()->PathConditionFixture.opposite(b));
      sample("local.alternative."+b,1,null,()->PathConditionFixture.alternative(b));
      for(int count:new int[]{-1,0,1})
        sample("local.nestedUnknown."+b+"."+count,0,null,()->PathConditionFixture.nestedUnknown(b,count));
      sample("direct.blocked."+b,0,null,()->InterproceduralFixture.blocked(b));
      sample("direct.allowed."+b,b?0:1,null,()->InterproceduralFixture.allowed(b));
      sample("direct.sameActual."+b,0,null,()->InterproceduralFixture.sameActual(b));
      for(boolean other:new boolean[]{false,true})
        sample("direct.distinctActual."+b+"."+other,b&&!other?1:0,null,()->InterproceduralFixture.distinctActual(b,other));
      sample("reflection.blocked."+b,0,null,()->ReflectionTransferFixture.blocked(b));
      sample("reflection.allowed."+b,b?0:1,null,()->ReflectionTransferFixture.allowed(b));
      sample("reflection.alias."+b,0,null,()->ReflectionTransferFixture.alias(b));
    }
    String[] texts={null,"east","west","unseen-value","","未见值"};
    for(int i=0;i<texts.length;i++) {
      String s=texts[i], npe=s==null?"java.lang.NullPointerException":null;
      sample("local.stringConflict."+i,0,npe,()->PathConditionFixture.stringConflict(s));
      sample("local.switchConflict."+i,0,npe,()->PathConditionFixture.switchConflict(s));
      sample("local.renamed."+i,"east".equals(s)?1:0,null,()->PathConditionFixture.renamed(s));
      sample("local.multi."+i,s==null?0:1,npe,()->PathConditionFixture.multi(s));
      sample("direct.stringBlocked."+i,0,npe,()->InterproceduralFixture.stringBlocked(s));
      sample("reflection.text."+i,0,npe,()->ReflectionTransferFixture.text(s));
    }
    sample("direct.literalBlocked",0,null,()->InterproceduralFixture.literalBlocked());
    sample("direct.literalAllowed",1,null,()->InterproceduralFixture.literalAllowed());
    sample("direct.overloadCaller",1,null,()->InterproceduralFixture.overloadCaller());
    sample("reflection.literal",0,null,()->ReflectionTransferFixture.literal());
    sample("reflection.numeric",1,null,()->ReflectionTransferFixture.numeric());
    // Unknown-analysis controls: they may execute and must not be deleted.
    sample("unknown.direct.changedFormal",1,null,()->InterproceduralFixture.changedFormalCaller());
    sample("unknown.reflection.mutable",1,null,()->ReflectionTransferFixture.mutable());
    sample("unknown.reflection.packed",1,null,()->ReflectionTransferFixture.packed(new Object[]{false}));
    sample("unknown.reflection.objectType",1,null,()->ReflectionTransferFixture.unknownType("west"));
    sample("unknown.reflection.widening",1,null,()->ReflectionTransferFixture.widening());
    sample("unknown.reflection.targetVarargs",1,null,()->ReflectionTransferFixture.targetVarargs());
    // The bridge must retain lookup, argument-conversion and target exceptions.
    sample("error.ambiguous",0,"java.lang.IllegalStateException",()->ReflectionTransferFixture.ambiguous());
    sample("error.inherited",0,"java.lang.IllegalStateException",()->ReflectionTransferFixture.inherited());
    sample("error.wrongType",0,"java.lang.IllegalArgumentException",()->ReflectionTransferFixture.wrongType());
    sample("error.nullPack",0,"java.lang.NullPointerException",()->ReflectionTransferFixture.nullPack());
    sample("error.targetThrows",0,"java.lang.NullPointerException",()->TpccApp.invoke(new ReflectionTransferFixture.Box(),"text",(Object)null));
    sample("error.nullUnboxing",0,"java.lang.IllegalArgumentException",()->TpccApp.invoke(new ReflectionTransferFixture.Box(),"sink",(Boolean)null));
    Files.writeString(Path.of(args[0]),new JSONObject().put("cases",rows).toString(2)+"\n");
  }
}
