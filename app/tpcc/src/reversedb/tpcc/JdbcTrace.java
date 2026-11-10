package reversedb.tpcc;

import java.lang.reflect.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Records JDBC evidence. Only reference execution needs upstream wall-clock substitution. */
final class JdbcTrace {
  static final boolean OBSERVE="1".equals(System.getenv("TPCC_OBSERVE"));
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
    return (ResultSet) Proxy.newProxyInstance(JdbcTrace.class.getClassLoader(),
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

  static PreparedStatement statement(PreparedStatement target, String sql, long millis, JSONArray trace, boolean freezeClock) {
    String normalized = sql.replaceAll("\\s+", " ").trim();
    return (PreparedStatement) Proxy.newProxyInstance(JdbcTrace.class.getClassLoader(),
        new Class<?>[]{PreparedStatement.class}, (proxy, method, args) -> {
          String name = method.getName();
          if (freezeClock && name.equals("setTimestamp")) {
            // Test clock at the JDBC boundary, only for the three upstream wall-clock writes.
            String lower = normalized.toLowerCase(Locale.ROOT);
            int index = (Integer) args[0];
            boolean allowed = (lower.startsWith("insert into oorder ") && index == 5)
                || (lower.startsWith("insert into history ") && index == 6)
                || (lower.startsWith("update order_line ") && index == 1);
            // Fused helpers already use the command clock. Preserve their actual
            // value so observation cannot conceal a candidate clock defect.
            boolean explicitClock = (index == 5 && lower.equals("select reversedb_fusion.neworder_full(?,?,?,?::jsonb,?::timestamp)"))
                || (index == 10 && lower.equals("select reversedb_payment.finish_payment(?,?,?,?,?,?,?,?,?,?::timestamp,?,?)"));
            if (!allowed && !explicitClock) throw new IllegalStateException("Unexpected timestamp binding: " + normalized);
            if (allowed) {
              args = args.clone();
              args[1] = new Timestamp(millis);
            }
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
          long started=OBSERVE && name.startsWith("execute") ? System.nanoTime() : 0;
          Object result;
          try {
            result = call(target, method, args);
            if(OBSERVE && name.startsWith("execute")) event.put("outcome","returned");
          } catch(Throwable error) {
            if(OBSERVE && name.startsWith("execute")) {
              event.put("outcome","threw").put("error_type",error.getClass().getName());
              if(error instanceof SQLException sqlError) event.put("sql_state",sqlError.getSQLState());
            }
            throw error;
          } finally {
            if(OBSERVE && name.startsWith("execute")) event.put("duration_ns",System.nanoTime()-started);
          }
          if (result instanceof ResultSet rs) return rows(rs, trace);
          if (recorded && name.startsWith("execute")) event.put("result", value(result));
          return result;
        });
  }

  static Connection traced(Connection target, long millis, JSONArray trace, boolean freezeClock) {
    return (Connection) Proxy.newProxyInstance(JdbcTrace.class.getClassLoader(),
        new Class<?>[]{Connection.class}, (proxy, method, args) -> {
          Object result = call(target, method, args);
          if (method.getName().equals("prepareStatement"))
            return statement((PreparedStatement) result, (String) args[0], millis, trace, freezeClock);
          if (method.getName().equals("commit") || method.getName().equals("rollback"))
            trace.put(new JSONObject().put("op", method.getName()));
          return result;
        });
  }

}
