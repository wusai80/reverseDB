package research;

import java.lang.reflect.*;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;
import reversedb.tpcc.AppRuntime;

/** A bounded experiment on an archived decompiled class, not a general slicer. */
public final class PruningProbe {
  static final JSONArray checks = new JSONArray(), samples = new JSONArray(), bindings = new JSONArray();
  static void require(boolean b, String message) { if (!b) throw new AssertionError(message); }
  record Arm(String name, URLClassLoader loader, Method entry) {}
  static Arm arm(String name, String classes, String jar) throws Exception {
    URLClassLoader loader = new URLClassLoader(new URL[]{Path.of(classes).toUri().toURL(), Path.of(jar).toUri().toURL()}, PruningProbe.class.getClassLoader());
    Class<?> app = loader.loadClass("com.oltpbenchmark.api.TpccApp");
    Class<?> procedure = loader.loadClass("com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus");
    require(Path.of(app.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(Path.of(classes)), "wrong adapter origin");
    require(Path.of(procedure.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(Path.of(jar)), "wrong procedure origin");
    boolean hasModule = Arrays.stream(procedure.getDeclaredMethods()).anyMatch(m -> m.getName().equals("getOrderLines"));
    boolean hasQuery = Arrays.stream(procedure.getDeclaredFields()).anyMatch(f -> f.getName().equals("ordStatGetOrderLinesSQL"));
    require(hasModule == name.equals("original") && hasQuery == name.equals("original"), "module removal mismatch");
    bindings.put(new JSONObject().put("arm",name).put("adapter",classes).put("procedure_jar",jar).put("module_present",hasModule).put("query_field_present",hasQuery));
    Method entry = app.getDeclaredMethod("reference",Connection.class,JSONObject.class); entry.setAccessible(true);
    return new Arm(name,loader,entry);
  }
  static JSONObject cmd(int district, int customer) { return new JSONObject().put("type","OrderStatus").put("district",district).put("customer",customer); }
  static JSONObject execute(Arm arm, Connection c, JSONObject command) throws Exception {
    try { return (JSONObject)arm.entry.invoke(null,c,command); }
    catch (InvocationTargetException e) { if(e.getCause() instanceof Exception ex) throw ex; throw e; }
  }
  static JSONObject outcome(Arm arm, Connection c, JSONObject command) throws Exception {
    Savepoint sp = c.setSavepoint();
    try { return new JSONObject().put("status","ok").put("response",execute(arm,c,command)); }
    catch (Exception e) { c.rollback(sp); return new JSONObject().put("status","error").put("class",e.getClass().getName()).put("message",e.getMessage()); }
    finally { c.releaseSavepoint(sp); }
  }
  static Object forward(Object target, Method m, Object[] a) throws Throwable {
    try { return m.invoke(target,a); } catch(InvocationTargetException e) { throw e.getCause(); }
  }
  static final class Reads {
    int queries; boolean failUnused;
    final List<String> sql = new ArrayList<>();
    Connection wrap(Connection raw) {
      return (Connection)Proxy.newProxyInstance(PruningProbe.class.getClassLoader(),new Class[]{Connection.class},(p,m,a)->{
        Object result = forward(raw,m,a);
        if(m.getName().equals("prepareStatement") && a[0] instanceof String text) {
          PreparedStatement statement = (PreparedStatement)result;
          return Proxy.newProxyInstance(PruningProbe.class.getClassLoader(),new Class[]{PreparedStatement.class},(q,n,b)->{
            if(n.getName().equals("executeQuery")) {
              queries++; sql.add(text);
              if(failUnused && text.contains("OL_SUPPLY_W_ID")) throw new SQLException("injected unused-module query failure","58000");
            }
            return forward(statement,n,b);
          });
        }
        return result;
      });
    }
  }
  static void sql(Connection c, String text) throws Exception { try(Statement s=c.createStatement()) { s.execute(text); } }
  static String state(Connection c) throws Exception {
    JSONArray result = new JSONArray();
    for(String table : List.of("warehouse","district","customer","history","item","stock","oorder","new_order","order_line")) {
      result.put(AppRuntime.rows(c,"SELECT count(*) n, encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE \"C\"),''),'UTF8')),'hex') hash FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM "+table+" t) x"));
    }
    return result.toString();
  }
  static void compare(String name, Arm a, Arm b, Connection c, JSONObject command, boolean succeeds) throws Exception {
    JSONObject left = outcome(a,c,command), right = outcome(b,c,command);
    require(left.similar(right), "response mismatch: "+name+" "+left+" / "+right);
    require(left.getString("status").equals(succeeds?"ok":"error"),"wrong status: "+name);
    checks.put(new JSONObject().put("name",name).put("original",left).put("pruned",right));
  }
  public static void main(String[] args) throws Exception {
    JSONObject protocol = new JSONObject(Files.readString(Path.of(args[6])));
    Arm a=arm("original",args[1],args[2]), b=arm("pruned",args[3],args[4]);
    JSONObject report = new JSONObject().put("protocol",protocol).put("bindings",bindings).put("checks",checks).put("samples",samples);
    try(Connection c=DriverManager.getConnection(args[0],"tpcc","synthetic-tpcc-only")) {
      c.setAutoCommit(false); c.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE); c.setReadOnly(true);
      String initial=state(c);
      for(Object o : protocol.getJSONArray("performance_cases")) {
        JSONArray pair=(JSONArray)o; compare("existing-"+pair,a,b,c,cmd(pair.getInt(0),pair.getInt(1)),true);
      }
      Reads ra=new Reads(),rb=new Reads();
      require(execute(a,ra.wrap(c),cmd(1,1)).similar(execute(b,rb.wrap(c),cmd(1,1))),"diagnostic mismatch");
      require(ra.queries==5 && rb.queries==4,"unexpected query counts");
      report.put("read_counts",new JSONObject().put("original",ra.queries).put("pruned",rb.queries).put("original_sql",ra.sql).put("pruned_sql",rb.sql));
      Reads faultA=new Reads(),faultB=new Reads(); faultA.failUnused=true; faultB.failUnused=true;
      JSONObject faultLeft=outcome(a,faultA.wrap(c),cmd(1,1)),faultRight=outcome(b,faultB.wrap(c),cmd(1,1));
      require(faultLeft.getString("status").equals("error") && faultRight.getString("status").equals("ok"),"fault counterexample missing");
      report.put("fault_counterexample",new JSONObject().put("original",faultLeft).put("pruned",faultRight).put("equivalent",false));
      c.rollback(); c.setReadOnly(false);
      // Fixture changes are made only inside rollback-only transactions; business calls stay read-only SQL.
      String latest="(SELECT max(o_id) FROM oorder WHERE o_w_id=1 AND o_d_id=1 AND o_c_id=1)";
      String[][] edges={
        {"empty-lines","DELETE FROM order_line WHERE ol_w_id=1 AND ol_d_id=1 AND ol_o_id="+latest,"true"},
        {"null-delivery","UPDATE order_line SET ol_delivery_d=NULL WHERE ol_w_id=1 AND ol_d_id=1 AND ol_o_id="+latest,"true"},
        {"delivered-and-duplicate-items","UPDATE order_line SET ol_delivery_d=TIMESTAMP '2026-01-01 00:00:00',ol_i_id=1 WHERE ol_w_id=1 AND ol_d_id=1 AND ol_o_id="+latest,"true"},
        {"missing-customer","DELETE FROM customer WHERE c_w_id=1 AND c_d_id=1 AND c_id=1","false"},
        {"missing-orders","DELETE FROM oorder WHERE o_w_id=1 AND o_d_id=1 AND o_c_id=1","false"}
      };
      for(String[] edge:edges) { sql(c,edge[1]); compare(edge[0],a,b,c,cmd(1,1),Boolean.parseBoolean(edge[2])); c.rollback(); }
      c.setReadOnly(true);
      require(initial.equals(state(c)),"fixture rollback/state mismatch");
      Random random=new Random(protocol.getLong("schedule_seed"));
      for(Object o:protocol.getJSONArray("performance_cases")) {
        JSONArray pair=(JSONArray)o; JSONObject command=cmd(pair.getInt(0),pair.getInt(1));
        JSONObject expected=execute(a,c,command);
        for(int i=0;i<protocol.getInt("warmups_per_arm");i++) { execute(a,c,command); execute(b,c,command); }
        for(int block=0;block<protocol.getInt("blocks");block++) {
          List<Arm> order=new ArrayList<>(List.of(a,b)); Collections.shuffle(order,random);
          for(Arm arm:order) {
            long start=System.nanoTime();
            String result=execute(arm,c,command).toString();
            double ms=(System.nanoTime()-start)/1e6;
            require(expected.similar(new JSONObject(result)),"timed output mismatch");
            samples.put(new JSONObject().put("case",pair).put("block",block).put("arm",arm.name).put("ms",ms));
          }
        }
        System.out.println("completed fixed case "+pair);
      }
      require(initial.equals(state(c)),"database state changed");
      c.rollback(); report.put("nine_table_state_unchanged",true).put("status","passed");
    } finally { a.loader.close(); b.loader.close(); }
    Files.writeString(Path.of(args[5]),report.toString(2)+"\n");
  }
}
