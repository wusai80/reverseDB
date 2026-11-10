package research;

import com.oltpbenchmark.api.Procedure;
import com.oltpbenchmark.api.SQLStmt;
import com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus;
import com.oltpbenchmark.types.DatabaseType;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.Map;
import org.json.JSONObject;

/** Checks the retained initialization boundary and witnesses the excluded capability. */
public class NameLookupBoundaryProbe {
  private static Object forward(Object target, Method method, Object[] args) throws Throwable {
    try { return method.invoke(target, args); }
    catch (InvocationTargetException error) { throw error.getCause(); }
  }

  private static JSONObject displayOutcome(Connection connection, boolean inject, String displaySql) throws Exception {
    int[] queries = {0}, displayQueries = {0};
    Connection wrapped = (Connection) Proxy.newProxyInstance(NameLookupBoundaryProbe.class.getClassLoader(),
        new Class<?>[]{Connection.class}, (proxy, method, args) -> {
          Object result = forward(connection, method, args);
          if (!method.getName().equals("prepareStatement")) return result;
          boolean display = ((String) args[0]).replaceAll("\\s+", " ").trim().equals(displaySql);
          return Proxy.newProxyInstance(NameLookupBoundaryProbe.class.getClassLoader(),
              new Class<?>[]{PreparedStatement.class}, (statement, operation, arguments) -> {
                if (operation.getName().equals("executeQuery")) {
                  queries[0]++;
                  if (display) {
                    displayQueries[0]++;
                    if (inject) throw new SQLException("injected unused-module query failure", "58000");
                  }
                }
                return forward(result, operation, arguments);
              });
        });
    Method entry = Class.forName("com.oltpbenchmark.api.TpccApp")
        .getDeclaredMethod("reference", Connection.class, JSONObject.class);
    entry.setAccessible(true);
    JSONObject outcome = new JSONObject();
    try {
      Object response = entry.invoke(null, wrapped,
          new JSONObject().put("type", "OrderStatus").put("district", 1).put("customer", 1));
      outcome.put("status", "ok").put("response", response);
    } catch (InvocationTargetException error) {
      Throwable cause = error.getCause();
      if (!(cause instanceof SQLException sql) || !"58000".equals(sql.getSQLState())) throw error;
      outcome.put("status", "error").put("sql_state", sql.getSQLState()).put("message", sql.getMessage());
    } finally {
      connection.rollback();
    }
    return outcome.put("queries", queries[0]).put("display_queries", displayQueries[0]);
  }

  @SuppressWarnings("unchecked")
  public static void main(String[] args) throws Exception {
    String arm = args[1];
    boolean nameRemoved = arm.equals("pruned") || arm.equals("combined");
    OrderStatus procedure = new OrderStatus();
    Method initialize = Procedure.class.getDeclaredMethod("initialize", DatabaseType.class);
    initialize.setAccessible(true);
    initialize.invoke(procedure, DatabaseType.POSTGRES);
    Method registryMethod = Procedure.class.getDeclaredMethod("getStatements");
    registryMethod.setAccessible(true);
    Map<String, SQLStmt> registry = (Map<String, SQLStmt>) registryMethod.invoke(procedure);
    JSONObject statements = new JSONObject();
    for (var entry : registry.entrySet()) statements.put(entry.getKey(), entry.getValue().getSQL());
    if (registry.size() != 4 || !registry.containsKey("customerByNameSQL"))
      throw new AssertionError("SQL initialization changed");
    JSONObject result = new JSONObject().put("arm", arm).put("sql_registry", statements)
        .put("class_origin", OrderStatus.class.getProtectionDomain().getCodeSource().getLocation().toString());
    Method lookup = null;
    try {
      lookup = OrderStatus.class.getDeclaredMethod("getCustomerByName", int.class, int.class,
          String.class, Connection.class);
    } catch (NoSuchMethodException expected) {
      if (!nameRemoved) throw expected;
    }
    if ((lookup == null) != nameRemoved) throw new AssertionError("Wrong method shape");
    boolean displayPresent = java.util.Arrays.stream(OrderStatus.class.getDeclaredMethods())
        .anyMatch(method -> method.getName().equals("getOrderLines"));
    if (displayPresent == arm.equals("combined")) throw new AssertionError("Wrong display method shape");
    result.put("display_method_present", displayPresent);
    // This is intentionally OUTSIDE the retained application interface. It prevents
    // reporting application specialization as a complete BenchBase replacement.
    try (Connection connection = DriverManager.getConnection(args[0], "tpcc", System.getenv("TPCC_PASSWORD"))) {
      connection.setAutoCommit(false);
      connection.setReadOnly(true);
      String last;
      try (var statement = connection.createStatement();
           var rows = statement.executeQuery("SELECT c_last FROM customer WHERE c_w_id=1 AND c_d_id=1 AND c_id=1")) {
        if (!rows.next()) throw new AssertionError("Missing full fixture");
        last = rows.getString(1);
      }
      if (lookup != null) {
        Object customer = lookup.invoke(procedure, 1, 1, last, connection);
        if (!last.equals(customer.getClass().getField("c_last").get(customer)))
          throw new AssertionError("Name lookup witness failed");
        result.put("outside_profile_name_lookup", "succeeded");
      } else result.put("outside_profile_name_lookup", "method_absent");
      connection.rollback();
      if (args.length == 4 && args[3].equals("display-boundary")) {
        String displaySql = registry.get("ordStatGetOrderLinesSQL").getSQL().replaceAll("\\s+", " ").trim();
        result.put("normal_display_probe", displayOutcome(connection, false, displaySql));
        result.put("fault_display_probe", displayOutcome(connection, true, displaySql));
      }
    }
    if (!arm.equals("original")) {
      Method run = java.util.Arrays.stream(OrderStatus.class.getDeclaredMethods())
          .filter(method -> method.getName().equals("run")).findFirst().orElseThrow();
      try {
        run.invoke(procedure, null, null, 1, 1, 1, 10, null);
        throw new AssertionError("Terminal entry was not rejected");
      } catch (InvocationTargetException expected) {
        if (!(expected.getCause() instanceof UnsupportedOperationException)
            || !"Application-adapter profile excludes BenchBase run".equals(expected.getCause().getMessage()))
          throw expected;
        result.put("outside_profile_terminal", "explicitly_rejected");
      }
    }
    Files.writeString(Path.of(args[2]), result.toString(2) + "\n");
  }
}
