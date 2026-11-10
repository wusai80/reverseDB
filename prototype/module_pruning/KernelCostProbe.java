package research;

import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.*;
import reversedb.tpcc.AppRuntime;

/** Bounded diagnostic collection; no durability, concurrency or rollback changes. */
public class KernelCostProbe {
  static void save(Path path, Object value) throws Exception {
    Files.writeString(path,(value instanceof JSONObject j ? j.toString(2) : ((JSONArray)value).toString(2))+"\n");
  }
  static JSONObject snapshot(Connection connection) throws Exception {
    AppRuntime.rows(connection,"SELECT pg_stat_clear_snapshot()");
    return new JSONObject().put("wal",AppRuntime.rows(connection,"SELECT * FROM pg_stat_wal"))
        .put("io",AppRuntime.rows(connection,"SELECT * FROM pg_stat_io"))
        .put("checkpointer",AppRuntime.rows(connection,"SELECT * FROM pg_stat_checkpointer"))
        .put("database",AppRuntime.rows(connection,"SELECT * FROM pg_stat_database"));
  }
  public static void main(String[] args) throws Exception {
    JSONObject plan=new JSONObject(Files.readString(Path.of(args[1])));
    String password=System.getenv("TPCC_PASSWORD");
    try (Connection control=DriverManager.getConnection(args[0]+"tpcc?sslmode=disable","tpcc",password)) {
      for (Object item:plan.getJSONArray("runs")) {
        JSONObject run=(JSONObject)item;
        String database=run.getString("database"); Path out=Path.of(run.getString("output"));
        Files.createDirectories(out);
        try (Statement statement=control.createStatement()) { statement.execute("CHECKPOINT"); }
        AppRuntime.rows(control,"SELECT pg_stat_statements_reset()");
        save(out.resolve("server-before.json"),snapshot(control));
        JSONArray samples=new JSONArray(), samplerErrors=new JSONArray();
        AtomicBoolean stop=new AtomicBoolean(false);
        try (Connection monitor=DriverManager.getConnection(args[0]+"tpcc?sslmode=disable","tpcc",password)) {
          Thread sampler=new Thread(()->{
            try (PreparedStatement query=monitor.prepareStatement("SELECT pid,state,wait_event_type,wait_event,query_id,cardinality(pg_blocking_pids(pid)) blockers FROM pg_stat_activity WHERE datname=? AND backend_type='client backend' AND application_name LIKE 'rdb-t%'") ) {
              query.setString(1,database); query.setQueryTimeout(2);
              while (!stop.get()) {
                long started=System.nanoTime(); JSONArray sessions=new JSONArray();
                try (ResultSet rows=query.executeQuery()) {
                  while (rows.next()) {
                    JSONObject row=new JSONObject();
                    for (int i=1;i<=rows.getMetaData().getColumnCount();i++) {
                      Object value=rows.getObject(i); row.put(rows.getMetaData().getColumnLabel(i),value==null?JSONObject.NULL:value);
                    }
                    sessions.put(row);
                  }
                }
                samples.put(new JSONObject().put("at_ns",started).put("query_ns",System.nanoTime()-started).put("sessions",sessions));
                Thread.sleep(10);
              }
            } catch (InterruptedException expected) {
              Thread.currentThread().interrupt();
            } catch (Exception error) { samplerErrors.put(error.toString()); }
          },"kernel-wait-sampler");
          sampler.start();
          try {
            Class<?> app=Class.forName("com.oltpbenchmark.api.TpccApp");
            app.getMethod("main",String[].class).invoke(null,(Object)new String[]{
                args[0]+database+"?sslmode=disable&reWriteBatchedInserts=true","tpcc",plan.getString("commands"),out.toString(),"reference"});
          } catch (InvocationTargetException error) {
            if (error.getCause() instanceof Exception cause) throw cause;
            throw error;
          } finally {
            stop.set(true); sampler.interrupt(); sampler.join(5000);
            if (sampler.isAlive()) throw new IllegalStateException("Sampler did not stop");
            save(out.resolve("wait-samples.json"),samples); save(out.resolve("sampler-errors.json"),samplerErrors);
          }
        }
        save(out.resolve("server-after.json"),snapshot(control));
        save(out.resolve("statements.json"),AppRuntime.rows(control,"SELECT * FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=?) ORDER BY total_exec_time DESC",database));
        if (!samplerErrors.isEmpty()) throw new AssertionError("Sampler error");
        System.out.println("Collected "+run.getString("name"));
      }
    }
  }
}
