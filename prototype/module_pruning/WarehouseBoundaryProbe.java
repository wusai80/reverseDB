package research;

import com.oltpbenchmark.api.Procedure;
import com.oltpbenchmark.api.SQLStmt;
import com.oltpbenchmark.benchmarks.tpcc.procedures.NewOrder;
import com.oltpbenchmark.types.DatabaseType;
import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.Map;
import org.json.*;
import reversedb.tpcc.AppRuntime;

/** Checks the schema implication before testing one removed existence check. */
public class WarehouseBoundaryProbe {
  static void require(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }
  static JSONObject schema(Connection connection) throws Exception {
    JSONArray constraints = AppRuntime.rows(connection, "SELECT conname, conrelid::regclass::text child, confrelid::regclass::text parent, pg_get_constraintdef(oid) definition, convalidated, condeferrable, condeferred, conenforced FROM pg_constraint WHERE contype='f' AND conrelid IN ('public.customer'::regclass,'public.district'::regclass) ORDER BY conname");
    JSONArray tables = AppRuntime.rows(connection, "SELECT relname,relkind,relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('public.customer'::regclass,'public.district'::regclass,'public.warehouse'::regclass) ORDER BY relname");
    JSONArray triggers = AppRuntime.rows(connection, "SELECT t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint WHERE c.contype='f' AND c.conrelid IN ('public.customer'::regclass,'public.district'::regclass) ORDER BY t.tgname");
    String role = AppRuntime.rows(connection, "SHOW session_replication_role").getJSONObject(0).getString("session_replication_role");
    boolean eligible = constraints.length() == 2 && tables.length() == 3 && triggers.length() == 8
        && role.equals("origin") && connection.getTransactionIsolation() == Connection.TRANSACTION_SERIALIZABLE;
    for (Object value : constraints) {
      JSONObject row = (JSONObject)value;
      String expected = switch (row.getString("child")) {
        case "customer" -> "FOREIGN KEY (c_w_id, c_d_id) REFERENCES district(d_w_id, d_id) ON DELETE CASCADE";
        case "district" -> "FOREIGN KEY (d_w_id) REFERENCES warehouse(w_id) ON DELETE CASCADE";
        default -> "unsupported";
      };
      eligible &= row.getString("definition").equals(expected) && row.getBoolean("convalidated")
          && row.getBoolean("conenforced") && !row.getBoolean("condeferrable") && !row.getBoolean("condeferred");
    }
    for (Object value : tables) {
      JSONObject row = (JSONObject)value;
      eligible &= row.getString("relkind").equals("r") && !row.getBoolean("relrowsecurity") && !row.getBoolean("relforcerowsecurity");
    }
    for (Object value : triggers) eligible &= ((JSONObject)value).getString("tgenabled").equals("O") || ((JSONObject)value).getString("tgenabled").equals("A");
    return new JSONObject().put("eligible",eligible).put("constraints",constraints).put("tables",tables)
        .put("triggers",triggers).put("replication_role",role).put("isolation",connection.getTransactionIsolation());
  }
  static Object forward(Object target, Method method, Object[] args) throws Throwable {
    try { return method.invoke(target,args); }
    catch (InvocationTargetException error) { throw error.getCause(); }
  }
  static JSONObject outcome(Connection connection, String injectedSql, String warehouseSql) throws Exception {
    int[] queries={0}, warehouseQueries={0};
    Connection wrapped=(Connection)Proxy.newProxyInstance(WarehouseBoundaryProbe.class.getClassLoader(),new Class<?>[]{Connection.class},(proxy,method,args)->{
      Object result=forward(connection,method,args);
      if (!method.getName().equals("prepareStatement")) return result;
      String sql=((String)args[0]).replaceAll("\\s+"," ").trim();
      return Proxy.newProxyInstance(WarehouseBoundaryProbe.class.getClassLoader(),new Class<?>[]{PreparedStatement.class},(statement,operation,arguments)->{
        if (operation.getName().equals("executeQuery")) {
          queries[0]++;
          if (sql.equals(warehouseSql)) warehouseQueries[0]++;
          if (sql.equals(injectedSql)) throw new SQLException("injected selected query failure","58000");
        }
        if (operation.getName().equals("setTimestamp")) {
          arguments=arguments.clone(); arguments[1]=new Timestamp(1762300800000L);
        }
        return forward(result,operation,arguments);
      });
    });
    Method entry=Class.forName("com.oltpbenchmark.api.TpccApp").getDeclaredMethod("reference",Connection.class,JSONObject.class);
    entry.setAccessible(true);
    JSONObject result=new JSONObject();
    JSONObject command=new JSONObject().put("type","NewOrder").put("district",1).put("customer",1)
        .put("lines",new JSONArray().put(new JSONObject().put("item",1).put("quantity",1)));
    try {
      result.put("response",entry.invoke(null,wrapped,command)).put("status","ok");
    } catch (InvocationTargetException error) {
      if (!(error.getCause() instanceof SQLException sql) || !"58000".equals(sql.getSQLState())) throw error;
      result.put("status","error").put("sql_state",sql.getSQLState()).put("message",sql.getMessage());
    } finally { connection.rollback(); }
    return result.put("queries",queries[0]).put("warehouse_queries",warehouseQueries[0]);
  }
  @SuppressWarnings("unchecked")
  public static void main(String[] args) throws Exception {
    String arm=args[1];
    NewOrder procedure=new NewOrder();
    Method initialize=Procedure.class.getDeclaredMethod("initialize",DatabaseType.class); initialize.setAccessible(true);
    initialize.invoke(procedure,DatabaseType.POSTGRES);
    Method getStatements=Procedure.class.getDeclaredMethod("getStatements"); getStatements.setAccessible(true);
    Map<String,SQLStmt> registry=(Map<String,SQLStmt>)getStatements.invoke(procedure);
    JSONObject statements=new JSONObject();
    for (var entry:registry.entrySet()) statements.put(entry.getKey(),entry.getValue().getSQL());
    require(registry.size()==10 && registry.containsKey("stmtGetWhseSQL"),"SQL initialization changed");
    boolean present=java.util.Arrays.stream(NewOrder.class.getDeclaredMethods()).anyMatch(m->m.getName().equals("getWarehouse"));
    require(present==arm.equals("original"),"Wrong candidate method presence");
    JSONObject report=new JSONObject().put("arm",arm).put("warehouse_method_present",present).put("sql_registry",statements)
        .put("class_origin",NewOrder.class.getProtectionDomain().getCodeSource().getLocation().toString());
    try (Connection connection=DriverManager.getConnection(args[0],"tpcc",System.getenv("TPCC_PASSWORD"))) {
      connection.setAutoCommit(false); connection.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
      JSONObject admitted=schema(connection); require(admitted.getBoolean("eligible"),"Schema/snapshot contract not satisfied");
      report.put("schema_admission",admitted); connection.rollback();
      // A transactional schema-only negative control; never delete data or run the
      // candidate with a missing dependency. Restore the original schema by rollback.
      try (Statement statement=connection.createStatement()) {
        statement.execute("ALTER TABLE district DROP CONSTRAINT district_d_w_id_fkey");
        JSONObject refused=schema(connection); require(!refused.getBoolean("eligible"),"Missing FK incorrectly admitted");
        report.put("missing_fk_control",refused);
      } finally { connection.rollback(); }
      require(schema(connection).getBoolean("eligible"),"FK rollback failed"); connection.rollback();
      String warehouseSql=registry.get("stmtGetWhseSQL").getSQL().replaceAll("\\s+"," ").trim();
      String customerSql=registry.get("stmtGetCustSQL").getSQL().replaceAll("\\s+"," ").trim();
      report.put("normal_probe",outcome(connection,"",warehouseSql));
      report.put("warehouse_fault_probe",outcome(connection,warehouseSql,warehouseSql));
      report.put("retained_customer_fault_probe",outcome(connection,customerSql,warehouseSql));
    }
    Files.writeString(Path.of(args[2]),report.toString(2)+"\n");
  }
}
