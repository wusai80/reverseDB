package reversedb.tpcc;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Shared input contract, transaction lifecycle, output projection and observation. */
public class AppRuntime {
  public interface Executor { JSONObject execute(Connection c,JSONObject cmd) throws Exception; }
  public static final class BusinessAbort extends Exception {}
  static final Set<String> TYPES=Set.of("NewOrder","Payment","OrderStatus","Delivery","StockLevel");
  public static JSONArray rows(Connection c, String sql, Object... args) throws SQLException {
    try (PreparedStatement s = c.prepareStatement(sql)) {
      for (int i = 0; i < args.length; i++) s.setObject(i+1, args[i]);
      try (ResultSet r = s.executeQuery()) {
        JSONArray result = new JSONArray();
        while (r.next()) {
          JSONObject row = new JSONObject();
          for (int i=1; i<=r.getMetaData().getColumnCount(); i++) {
            Object v = r.getObject(i);
            row.put(r.getMetaData().getColumnLabel(i), v instanceof Timestamp ? v.toString() : v == null ? JSONObject.NULL : v);
          }
          result.put(row);
        }
        return result;
      }
    }
  }

  static int integer(JSONObject obj, String key, int low, int high) {
    Object raw = obj.get(key);
    if (!(raw instanceof Integer || raw instanceof Long) || ((Number) raw).longValue() < low || ((Number) raw).longValue() > high)
      throw new IllegalArgumentException(key + " must be an integer in [" + low + "," + high + "]");
    return ((Number)raw).intValue();
  }
  public static void validate(JSONObject cmd) {
    if (!(cmd.get("id") instanceof String) || cmd.getString("id").isBlank()) throw new IllegalArgumentException("id required");
    if (!TYPES.contains(cmd.getString("type"))) throw new IllegalArgumentException("Unknown transaction type");
    integer(cmd,"terminal",1,4); integer(cmd,"warehouse",1,1);
    Object clock = cmd.get("clock_ms");
    if (!(clock instanceof Long || clock instanceof Integer) || ((Number)clock).longValue() < 0)
      throw new IllegalArgumentException("clock_ms must be nonnegative integer");
    Set<String> keys = new HashSet<>(Set.of("id","terminal","warehouse","clock_ms","type"));
    switch (cmd.getString("type")) {
      case "NewOrder" -> {
        keys.addAll(Set.of("district","customer","lines"));
        integer(cmd,"district",1,10); integer(cmd,"customer",1,3000);
        JSONArray lines = cmd.getJSONArray("lines");
        if (lines.length()<5 || lines.length()>15) throw new IllegalArgumentException("Expected 5 to 15 order lines");
        for (Object o : lines) {
          if (!(o instanceof JSONObject line)) throw new IllegalArgumentException("Each line must be an object");
          if (!line.keySet().equals(Set.of("item","quantity"))) throw new IllegalArgumentException("Unknown line field");
          integer(line,"item",1,Integer.MAX_VALUE); integer(line,"quantity",1,10);
        }
      }
      case "Payment" -> {
        keys.addAll(Set.of("district","customer_district","customer","amount"));
        integer(cmd,"district",1,10); integer(cmd,"customer_district",1,10); integer(cmd,"customer",1,3000);
        if (!(cmd.get("amount") instanceof Number)) throw new IllegalArgumentException("Numeric amount required");
        java.math.BigDecimal amount = cmd.getBigDecimal("amount");
        if (amount.signum()<=0 || amount.compareTo(new java.math.BigDecimal("5000"))>0 || amount.stripTrailingZeros().scale()>2)
          throw new IllegalArgumentException("Payment amount must be 0.01..5000.00 with at most two decimals");
      }
      case "OrderStatus" -> {
        keys.addAll(Set.of("district","customer")); integer(cmd,"district",1,10); integer(cmd,"customer",1,3000);
      }
      case "StockLevel" -> {
        keys.addAll(Set.of("district","threshold")); integer(cmd,"district",1,10); integer(cmd,"threshold",10,20);
      }
      case "Delivery" -> { keys.add("carrier"); integer(cmd,"carrier",1,10); }
    }
    if (!cmd.keySet().equals(keys)) throw new IllegalArgumentException("Missing or unknown command fields");
  }

  static JSONObject footprint(Connection c, JSONObject cmd) throws SQLException {
    int d = cmd.getInt("district");
    Set<Integer> items = new TreeSet<>();
    for (Object obj: cmd.getJSONArray("lines")) items.add(((JSONObject)obj).getInt("item"));
    String placeholders = String.join(",", Collections.nCopies(items.size(), "?"));
    return new JSONObject()
        .put("district", rows(c,"SELECT * FROM district WHERE d_w_id=1 AND d_id=?",d))
        .put("stock", rows(c,"SELECT * FROM stock WHERE s_w_id=1 AND s_i_id IN ("+placeholders+") ORDER BY s_i_id",items.toArray()))
        .put("orders",rows(c,"SELECT * FROM oorder WHERE o_w_id=1 AND o_d_id=? AND o_id>=3001 ORDER BY o_id",d))
        .put("pending",rows(c,"SELECT * FROM new_order WHERE no_w_id=1 AND no_d_id=? AND no_o_id>=3001 ORDER BY no_o_id",d))
        .put("lines",rows(c,"SELECT * FROM order_line WHERE ol_w_id=1 AND ol_d_id=? AND ol_o_id>=3001 ORDER BY ol_o_id,ol_number",d));
  }

  public static void run(String[] args, Executor executor) throws Exception {
    if (args.length!=5 || !Set.of("reference","candidate").contains(args[4]))
      throw new IllegalArgumentException("Usage: TpccApp JDBC_URL USER COMMANDS_JSON OUTPUT_DIR reference|candidate; TPCC_PASSWORD in environment");
    JSONArray commands = new JSONObject(Files.readString(Path.of(args[2]))).getJSONArray("commands");
    Path out=Path.of(args[3]); Files.createDirectories(out);
    JSONArray responses=new JSONArray(), traces=new JSONArray(), observations=new JSONArray(), runtime=new JSONArray();
    boolean observe="1".equals(System.getenv("TPCC_OBSERVE"));
    Connection[] connections=new Connection[4];
    try {
      for(int i=0;i<4;i++) {
        connections[i]=DriverManager.getConnection(args[0],args[1],System.getenv("TPCC_PASSWORD"));
        connections[i].setClientInfo("ApplicationName","tpcc-app-terminal-"+(i+1));
        connections[i].setAutoCommit(false); connections[i].setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
      }
      Set<String> ids=new HashSet<>();
      for(Object obj:commands) {
        JSONObject cmd=(JSONObject)obj;
        JSONObject response=new JSONObject().put("id",cmd.opt("id")); responses.put(response);
        try { validate(cmd); if(!ids.add(cmd.getString("id"))) throw new IllegalArgumentException("Duplicate command id"); }
        catch(IllegalArgumentException|JSONException error) {
          response.put("status","rejected").put("code","INVALID_INPUT").put("message",error.getMessage()); continue;
        }
        Connection raw=connections[cmd.getInt("terminal")-1];
        JSONArray trace=new JSONArray(); traces.put(new JSONObject().put("id",cmd.getString("id")).put("events",trace));
        Connection c=JdbcTrace.traced(raw,cmd.getLong("clock_ms"),trace,args[4].equals("reference"));
        JSONArray context=Observation.context(raw,cmd);
        JSONObject before=Observation.capture(raw,cmd,context);
        int backend=observe?rows(raw,"SELECT pg_backend_pid() pid").getJSONObject(0).getInt("pid"):0;
        raw.commit(); // End the observation-only transaction before business execution.
        boolean readOnly=Set.of("OrderStatus","StockLevel").contains(cmd.getString("type"));
        raw.setReadOnly(readOnly);
        JSONObject execution=new JSONObject();
        String tag="rdb-t"+cmd.getInt("terminal")+"-s"+responses.length();
        long started=System.nanoTime();
        if(observe) {
          execution.put("id",cmd.getString("id")).put("type",cmd.getString("type"))
              .put("terminal",cmd.getInt("terminal")).put("application_name",tag)
              .put("postgres_pid",backend).put("jvm_pid",ProcessHandle.current().pid())
              .put("started_at",java.time.Instant.now().toString());
          runtime.put(execution);
        }
        try {
          // SET LOCAL via set_config lasts only for this transaction; observation queries keep the original tag.
          if(observe) rows(raw,"SELECT set_config('application_name', ?, true)",tag);
          JSONObject result=executor.execute(c,cmd); c.commit();
          response.put("status","committed").put("result",result);
        } catch(BusinessAbort|NewOrderService.InvalidItem abort) {
          c.rollback(); response.put("status","aborted").put("code","INVALID_ITEM");
        } catch(Exception error) { c.rollback(); throw error; }
        finally {
          if(observe) execution.put("finished_at",java.time.Instant.now().toString())
              .put("duration_ns",System.nanoTime()-started).put("status",response.optString("status","failed"));
        }
        raw.setReadOnly(false);
        JSONObject after=Observation.capture(raw,cmd,context); raw.commit();
        observations.put(new JSONObject().put("id",cmd.getString("id")).put("before",before).put("after",after).put("read_only",readOnly));
        if(cmd.getString("type").equals("NewOrder")) response.put("state",after);
        if(response.getString("status").equals("aborted") && !before.similar(after))
          throw new AssertionError("Abort changed observed state");
      }
    } finally {
      for(Connection c:connections) if(c!=null) {try{c.rollback();}finally{c.close();}}
      Files.writeString(out.resolve("responses.json"),responses.toString(2)+"\n");
      Files.writeString(out.resolve("jdbc-trace.json"),traces.toString(2)+"\n");
      Files.writeString(out.resolve("observations.json"),observations.toString(2)+"\n");
      if(observe) Files.writeString(out.resolve("runtime.json"),runtime.toString(2)+"\n");
    }
    System.out.println("Completed "+responses.length()+" business commands ("+args[4]+")");
  }
}
