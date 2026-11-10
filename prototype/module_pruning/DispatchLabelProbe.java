package research;

import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Execute Java control flow; stop at prepareStatement without simulating SQL. */
public final class DispatchLabelProbe {
  static final class BoundaryReached extends RuntimeException {}
  static JSONObject execute(Method method,String mode,String other,boolean check) throws Exception {
    Map<String,String> saved=new HashMap<>();
    for(String key:List.of("test.mode","test.selection","reversedb.fusion.mode","other")) saved.put(key,System.getProperty(key));
    JSONArray prepared=new JSONArray();
    Connection connection=(Connection)Proxy.newProxyInstance(DispatchLabelProbe.class.getClassLoader(),new Class<?>[]{Connection.class},(p,m,a)->{
      if(m.getName().equals("prepareStatement")) { prepared.put((String)a[0]); throw new BoundaryReached(); }
      throw new AssertionError("Unexpected connection method: "+m);
    });
    JSONObject result=new JSONObject().put("method",method.getName()).put("mode",mode==null?JSONObject.NULL:mode)
      .put("other",other).put("check",check).put("prepared_sql",prepared);
    try {
      for(String key:List.of("test.mode","test.selection","reversedb.fusion.mode")) {
        if(mode==null) System.clearProperty(key); else System.setProperty(key,mode);
      }
      System.setProperty("other",other);
      JSONObject command=new JSONObject().put("clock_ms",1762300800123L).put("check",check).put("allowed","gamma");
      method.setAccessible(true);
      try { method.invoke(null,connection,command); result.put("outcome","returned_before_jdbc"); }
      catch(InvocationTargetException error) {
        Throwable cause=error.getCause();
        result.put("outcome",cause instanceof BoundaryReached?"reached_jdbc":"threw_before_jdbc")
          .put("exception",cause.getClass().getName());
      }
    } finally {
      for(var entry:saved.entrySet()) {
        if(entry.getValue()==null) System.clearProperty(entry.getKey()); else System.setProperty(entry.getKey(),entry.getValue());
      }
    }
    return result;
  }
  public static void main(String[] args) throws Exception {
    JSONArray actual=new JSONArray(),controls=new JSONArray();
    Method fused=Class.forName("com.oltpbenchmark.api.FusedNewOrder").getDeclaredMethod("execute",Connection.class,JSONObject.class);
    for(String mode:new String[]{null,"full","pruned","early_write","full_fault","outside",""}) actual.put(execute(fused,mode,"alpha",true));
    Class<?> fixture=Class.forName("research.GuardExamples");
    var methods=new ArrayList<>(Arrays.asList(fixture.getDeclaredMethods())); methods.sort(Comparator.comparing(Method::getName));
    methods.removeIf(m->m.isSynthetic() || !Arrays.equals(m.getParameterTypes(),new Class<?>[]{Connection.class,JSONObject.class}));
    String[] modes={"alpha","beta","gamma","gamma","","Ω",null};
    String[] others={"alpha","beta","alpha","alpha","alpha","alpha","beta"};
    boolean[] checks={true,true,false,true,false,true,true};
    for(Method method:methods) for(int i=0;i<modes.length;i++) controls.put(execute(method,modes[i],others[i],checks[i]));
    Files.writeString(Path.of(args[0]),new JSONObject().put("actual",actual).put("controls",controls)
      .put("scope","Real Java execution up to JDBC prepareStatement; no SQL body or business transaction executed").toString(2)+"\n");
  }
}
