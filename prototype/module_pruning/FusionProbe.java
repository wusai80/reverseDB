package research;

import java.lang.reflect.*;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;
import reversedb.tpcc.AppRuntime;

/** Fixed same-JVM differential/latency diagnostics, all operations rolled back. */
public class FusionProbe {
  record Arm(String name, URLClassLoader loader, Method entry) {}
  static void require(boolean value,String message) { if(!value) throw new AssertionError(message); }
  static Arm arm(String name,String adapter,String jar) throws Exception {
    URLClassLoader loader=new URLClassLoader(new URL[]{Path.of(adapter).toUri().toURL(),Path.of(jar).toUri().toURL()},FusionProbe.class.getClassLoader());
    Class<?> type=loader.loadClass("com.oltpbenchmark.api.TpccApp");
    require(Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(Path.of(adapter)),"Wrong adapter source");
    Method entry=type.getDeclaredMethod("reference",Connection.class,JSONObject.class); entry.setAccessible(true);
    return new Arm(name,loader,entry);
  }
  static Object forward(Object target,Method method,Object[] args) throws Throwable {
    try { return method.invoke(target,args); } catch(InvocationTargetException error) { throw error.getCause(); }
  }
  static final class Calls {
    int execute;
    Connection wrap(Connection raw,JSONObject command,boolean failProjection) {
      return (Connection)Proxy.newProxyInstance(FusionProbe.class.getClassLoader(),new Class<?>[]{Connection.class},(proxy,method,args)->{
        Object result=forward(raw,method,args);
        if (!method.getName().equals("prepareStatement")) return result;
        String sql=((String)args[0]).replaceAll("\\s+"," ").trim();
        return Proxy.newProxyInstance(FusionProbe.class.getClassLoader(),new Class<?>[]{PreparedStatement.class},(statement,operation,arguments)->{
          if(operation.getName().startsWith("execute")) {
            execute++;
            if(failProjection && sql.startsWith("SELECT * FROM oorder")) throw new SQLException("injected failure after writes","58000");
          }
          if(operation.getName().equals("setTimestamp")) {
            arguments=arguments.clone(); arguments[1]=new Timestamp(command.getLong("clock_ms"));
          }
          return forward(result,operation,arguments);
        });
      });
    }
  }
  static JSONObject invoke(Arm arm,Connection connection,JSONObject command) throws Exception {
    System.setProperty("reversedb.fusion.mode",arm.name);
    try { return (JSONObject)arm.entry.invoke(null,connection,command); }
    catch(InvocationTargetException error) { if(error.getCause() instanceof Exception cause) throw cause; throw error; }
  }
  static String state(Connection connection) throws Exception {
    JSONArray result=new JSONArray();
    for(String table:List.of("warehouse","district","customer","history","item","stock","oorder","new_order","order_line"))
      result.put(AppRuntime.rows(connection,"SELECT count(*) n,encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE \"C\"),''),'UTF8')),'hex') hash FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM "+table+" t) x"));
    return result.toString();
  }
  public static void main(String[] args) throws Exception {
    Map<String,JSONObject> commands=new HashMap<>();
    for(Object value:new JSONObject(Files.readString(Path.of(args[4]))).getJSONArray("commands")) {
      JSONObject command=(JSONObject)value; commands.put(command.getString("id"),command);
    }
    List<Arm> arms=List.of(arm("original",args[1],args[3]),arm("full",args[2],args[3]),arm("pruned",args[2],args[3]));
    JSONArray samples=new JSONArray(),checks=new JSONArray();
    JSONObject report=new JSONObject().put("samples",samples).put("checks",checks).put("seed",20261105)
        .put("scope","Three valid frozen NewOrder commands, three warmups/arm/case and ten shuffled blocks; helper+result duration, excludes commit/rollback and setup; instrumented same-JVM, not throughput");
    try(Connection connection=DriverManager.getConnection(args[0],"tpcc",System.getenv("TPCC_PASSWORD"))) {
      connection.setAutoCommit(false); connection.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
      String before=state(connection); connection.rollback(); Random random=new Random(20261105);
      for(String id:List.of("new-01","new-04","new-05")) {
        JSONObject command=commands.get(id); AppRuntime.validate(command);
        Calls baselineCalls=new Calls(); JSONObject expected=invoke(arms.get(0),baselineCalls.wrap(connection,command,false),command); connection.rollback();
        for(Arm arm:arms) {
          Calls calls=new Calls(); JSONObject result=invoke(arm,calls.wrap(connection,command,false),command); connection.rollback();
          require(expected.similar(result),"Diagnostic response mismatch "+id+" "+arm.name);
          require(arm.name.equals("original") || calls.execute==1,"Fused path is not a single execute");
          checks.put(new JSONObject().put("id",id).put("arm",arm.name).put("execute_calls",calls.execute));
          for(int warm=0;warm<3;warm++) { invoke(arm,new Calls().wrap(connection,command,false),command); connection.rollback(); }
        }
        for(int block=0;block<10;block++) {
          List<Arm> order=new ArrayList<>(arms); Collections.shuffle(order,random);
          for(Arm arm:order) {
            Calls calls=new Calls(); Connection wrapped=calls.wrap(connection,command,false);
            long started=System.nanoTime(); JSONObject result=invoke(arm,wrapped,command);
            double ms=(System.nanoTime()-started)/1e6;
            require(expected.similar(result),"Timed response mismatch"); connection.rollback();
            samples.put(new JSONObject().put("id",id).put("block",block).put("arm",arm.name).put("ms",ms).put("execute_calls",calls.execute));
          }
        }
      }
      // Compare rollback after ALL writes, not only invalid-item early aborts.
      JSONObject command=commands.get("new-01"); JSONArray errors=new JSONArray();
      Arm fault=arm("full_fault",args[2],args[3]);
      try {
        for(Arm arm:List.of(arms.get(0),fault)) {
          try { invoke(arm,new Calls().wrap(connection,command,true),command); throw new AssertionError("Missing late failure"); }
          catch(SQLException error) { require("58000".equals(error.getSQLState()),"Wrong injected fault"); errors.put(new JSONObject().put("arm",arm.name).put("sql_state",error.getSQLState())); }
          finally { connection.rollback(); }
          require(before.equals(state(connection)),"Late failure changed data"); connection.rollback();
        }
      } finally { fault.loader.close(); }
      report.put("late_failure",errors);
      // Source-order negative control: eager stock writes must differ for duplicates.
      command=commands.get("new-05");
      invoke(arms.get(0),new Calls().wrap(connection,command,false),command);
      JSONObject correct=AppRuntime.rows(connection,"SELECT s_quantity,s_ytd,s_order_cnt FROM stock WHERE s_w_id=1 AND s_i_id=6").getJSONObject(0); connection.rollback();
      Arm wrong=arm("early_write",args[2],args[3]);
      JSONObject mutated;
      try {
        invoke(wrong,new Calls().wrap(connection,command,false),command);
        mutated=AppRuntime.rows(connection,"SELECT s_quantity,s_ytd,s_order_cnt FROM stock WHERE s_w_id=1 AND s_i_id=6").getJSONObject(0); connection.rollback();
      } finally { wrong.loader.close(); }
      require(!correct.similar(mutated),"Early-write mutant not distinguished");
      report.put("early_write_counterexample",new JSONObject().put("original",correct).put("mutant",mutated));
      require(before.equals(state(connection)),"Rollback-only diagnostics changed full data"); connection.rollback();
      report.put("status","passed").put("nine_table_state_unchanged",true);
    } finally { for(Arm arm:arms) arm.loader.close(); }
    Files.writeString(Path.of(args[5]),report.toString(2)+"\n");
  }
}
