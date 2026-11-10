package com.oltpbenchmark.api;

import com.oltpbenchmark.benchmarks.tpcc.procedures.*;
import com.oltpbenchmark.types.DatabaseType;
import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Serialized schedule over four connections; upstream procedure files remain unchanged. */
public class TpccReplay {
  static Object call(Object target, Method method, Object[] args) throws Throwable {
    try { return method.invoke(target, args); }
    catch (InvocationTargetException e) { throw e.getCause(); }
  }

  static Object value(Object obj) {
    if (obj == null) return JSONObject.NULL;
    if (obj instanceof Number || obj instanceof Boolean || obj instanceof String) return obj;
    if (obj instanceof int[] a) return new JSONArray(a);
    return obj.toString();
  }

  static ResultSet rows(ResultSet target, JSONArray trace) {
    return (ResultSet) Proxy.newProxyInstance(TpccReplay.class.getClassLoader(),
        new Class<?>[]{ResultSet.class}, (proxy, method, args) -> {
          Object result = call(target, method, args);
          String name = method.getName();
          if (name.equals("next") || name.equals("wasNull") ||
              (name.startsWith("get") && args != null && args.length == 1 &&
               (args[0] instanceof String || args[0] instanceof Integer))) {
            trace.put(new JSONObject().put("op", name)
                .put("column", args == null ? JSONObject.NULL : value(args[0]))
                .put("value", value(result)));
          }
          return result;
        });
  }

  static PreparedStatement statement(PreparedStatement target, String sql, long millis, JSONArray trace) {
    String normalized = sql.replaceAll("\\s+", " ").trim();
    return (PreparedStatement) Proxy.newProxyInstance(TpccReplay.class.getClassLoader(),
        new Class<?>[]{PreparedStatement.class}, (proxy, method, args) -> {
          String name = method.getName();
          if (name.equals("setTimestamp")) {
            // Test clock at the JDBC boundary, only for the three upstream wall-clock writes.
            String lower = normalized.toLowerCase(Locale.ROOT);
            int index = (Integer) args[0];
            boolean allowed = (lower.startsWith("insert into oorder ") && index == 5)
                || (lower.startsWith("insert into history ") && index == 6)
                || (lower.startsWith("update order_line ") && index == 1);
            if (!allowed) throw new IllegalStateException("Unexpected timestamp binding: " + normalized);
            args = args.clone();
            args[1] = new Timestamp(millis);
          }
          boolean recorded = name.startsWith("set") || name.startsWith("execute")
              || name.equals("addBatch") || name.equals("clearBatch") || name.equals("clearParameters");
          JSONObject event = new JSONObject().put("op", name).put("sql", normalized);
          if (recorded) {
            JSONArray arguments = new JSONArray();
            if (args != null) for (Object arg : args) arguments.put(value(arg));
            event.put("args", arguments);
            trace.put(event);
          }
          Object result = call(target, method, args);
          if (result instanceof ResultSet rs) return rows(rs, trace);
          if (recorded && name.startsWith("execute")) event.put("result", value(result));
          return result;
        });
  }

  static Connection traced(Connection target, long millis, JSONArray trace) {
    return (Connection) Proxy.newProxyInstance(TpccReplay.class.getClassLoader(),
        new Class<?>[]{Connection.class}, (proxy, method, args) -> {
          Object result = call(target, method, args);
          if (method.getName().equals("prepareStatement"))
            return statement((PreparedStatement) result, (String) args[0], millis, trace);
          if (method.getName().equals("commit") || method.getName().equals("rollback"))
            trace.put(new JSONObject().put("op", method.getName()));
          return result;
        });
  }

  public static void main(String[] args) throws Exception {
    JSONObject schedule = new JSONObject(Files.readString(Path.of(args[3])));
    JSONArray report = new JSONArray();
    Connection[] connections = new Connection[4];
    try {
      for (int i = 0; i < connections.length; i++) {
        connections[i] = DriverManager.getConnection(args[0], args[1], args[2]);
        connections[i].setClientInfo("ApplicationName", "tpcc-replay-terminal-" + (i+1));
        connections[i].setAutoCommit(false);
        connections[i].setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
      }
      for (Object obj : schedule.getJSONArray("transactions")) {
        JSONObject input = (JSONObject) obj;
        JSONArray trace = new JSONArray();
        JSONObject event = new JSONObject().put("input", input).put("trace", trace);
        report.put(event);
        Connection c = traced(connections[input.getInt("terminal") - 1], input.getLong("clock_ms"), trace);
        TPCCProcedure procedure = (TPCCProcedure) Class.forName(
            "com.oltpbenchmark.benchmarks.tpcc.procedures." + input.getString("type"))
            .getConstructor().newInstance();
        ((Procedure) procedure).initialize(DatabaseType.POSTGRES);
        trace.put(new JSONObject().put("op", "begin"));
        String outcome;
        try {
          if (input.has("items")) {
            int[] items = input.getJSONArray("items").toList().stream().mapToInt(x -> ((Number)x).intValue()).toArray();
            int[] quantities = input.getJSONArray("quantities").toList().stream().mapToInt(x -> ((Number)x).intValue()).toArray();
            int[] suppliers = new int[items.length]; Arrays.fill(suppliers, 1);
            Method method = NewOrder.class.getDeclaredMethod("newOrderTransaction", int.class,
                int.class,int.class,int.class,int.class,int[].class,int[].class,int[].class,Connection.class);
            method.setAccessible(true);
            try { method.invoke(procedure, 1, 1, 1, items.length, 1, items, suppliers, quantities, c); }
            catch (InvocationTargetException e) {
              if (e.getCause() instanceof Procedure.UserAbortException abort) throw abort;
              throw e;
            }
          } else {
            // The pinned five run methods never dereference the unused Worker parameter.
            procedure.run(c, new Random(input.getLong("seed")), 1, 1, 1, 10, null);
          }
          c.commit();
          outcome = "commit";
        } catch (Procedure.UserAbortException expected) {
          c.rollback(); outcome = "abort";
          event.put("reason", expected.getMessage());
        }
        event.put("outcome", outcome);
        if (!outcome.equals(input.getString("expected")))
          throw new AssertionError("Unexpected outcome for " + input.getString("id"));
      }
    } finally {
      for (Connection c : connections) if (c != null) { try { c.rollback(); } finally { c.close(); } }
      Files.writeString(Path.of(args[4]), report.toString(2) + "\n");
    }
  }
}
