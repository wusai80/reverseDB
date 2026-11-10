package com.oltpbenchmark.api;

import com.oltpbenchmark.benchmarks.tpcc.procedures.NewOrder;
import com.oltpbenchmark.types.DatabaseType;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import org.json.JSONArray;
import org.json.JSONObject;

/** Calls the pinned, unmodified NewOrder body; this harness owns commit/rollback. */
public class TpccAtomicityProbe {
  static final String SNAPSHOT = """
      SELECT jsonb_build_object(
        'next', (SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=1),
        'orders', (SELECT count(*) FROM oorder WHERE o_w_id=1 AND o_d_id=1),
        'new_orders', (SELECT count(*) FROM new_order WHERE no_w_id=1 AND no_d_id=1),
        'lines', (SELECT count(*) FROM order_line WHERE ol_w_id=1 AND ol_d_id=1),
        'stock', (SELECT jsonb_agg(jsonb_build_object('item',s_i_id,'quantity',s_quantity,
          'ytd',s_ytd,'orders',s_order_cnt,'remote',s_remote_cnt) ORDER BY s_i_id)
          FROM stock WHERE s_w_id=1 AND s_i_id BETWEEN 1 AND 5))::text
      """;

  static String snapshot(Connection c) throws Exception {
    try (Statement s = c.createStatement(); ResultSet r = s.executeQuery(SNAPSHOT)) {
      if (!r.next()) throw new AssertionError("missing snapshot");
      return r.getString(1);
    }
  }

  static void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }

  static void invoke(Method method, NewOrder procedure, Connection c, int last) throws Exception {
    method.invoke(procedure, 1, 1, 1, 5, 1,
        new int[] {1,2,3,4,last}, new int[] {1,1,1,1,1}, new int[] {1,1,1,1,1}, c);
  }

  public static void main(String[] args) throws Exception {
    JSONObject report = new JSONObject();
    report.put("scope", "Pinned NewOrder business method via reflection; test-owned JDBC transaction boundaries, separate observer connection. No concurrent workload during this probe.");
    report.put("input", new JSONObject().put("warehouse",1).put("district",1).put("customer",1)
        .put("items",new JSONArray(new int[]{1,2,3,4,5})).put("quantities",new JSONArray(new int[]{1,1,1,1,1})));
    report.put("invalid_last_item", -12345);
    try (Connection c = DriverManager.getConnection(args[0], args[1], args[2]);
         Connection observer = DriverManager.getConnection(args[0], args[1], args[2])) {
      c.setClientInfo("ApplicationName", "tpcc-atomicity-writer");
      observer.setClientInfo("ApplicationName", "tpcc-atomicity-observer");
      c.setAutoCommit(false);
      c.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
      NewOrder procedure = new NewOrder();
      ((Procedure) procedure).initialize(DatabaseType.POSTGRES);
      Method method = NewOrder.class.getDeclaredMethod("newOrderTransaction",
          int.class,int.class,int.class,int.class,int.class,int[].class,int[].class,int[].class,Connection.class);
      method.setAccessible(true);
      String before = snapshot(observer);
      boolean expectedAbort = false;
      try {
        invoke(method, procedure, c, -12345);
      } catch (InvocationTargetException e) {
        if (!(e.getCause() instanceof Procedure.UserAbortException)) throw e;
        expectedAbort = true;
        report.put("abort_exception", e.getCause().toString());
      }
      check(expectedAbort, "invalid final item must abort the actual NewOrder procedure");
      String inside = snapshot(c);
      check(!inside.equals(before), "probe must have reached real intermediate writes");
      check(snapshot(observer).equals(before), "other session must not see uncommitted changes");
      c.rollback();
      String rolledBack = snapshot(observer);
      check(rolledBack.equals(before), "rollback must undo the entire observed write set");
      invoke(method, procedure, c, 5);
      check(snapshot(observer).equals(before), "valid transaction must remain invisible before commit");
      c.commit();
      String committed = snapshot(observer);
      JSONObject b = new JSONObject(before), a = new JSONObject(committed);
      for (String key : new String[]{"next","orders","new_orders"})
        check(a.getLong(key)==b.getLong(key)+1, key+" must advance once");
      check(a.getLong("lines")==b.getLong("lines")+5, "must insert exactly five order lines");
      for (int i=0; i<5; i++) {
        JSONObject oldStock=b.getJSONArray("stock").getJSONObject(i);
        JSONObject newStock=a.getJSONArray("stock").getJSONObject(i);
        int q=oldStock.getInt("quantity");
        check(newStock.getInt("quantity")== (q-1>=10?q-1:q+90), "TPC-C stock rule");
        check(newStock.getInt("ytd")==oldStock.getInt("ytd")+1, "stock YTD");
        check(newStock.getInt("orders")==oldStock.getInt("orders")+1, "stock order count");
        check(newStock.getInt("remote")==oldStock.getInt("remote"), "local stock remote count");
      }
      report.put("status","passed").put("before",b).put("inside_aborted_transaction",new JSONObject(inside))
          .put("after_rollback",new JSONObject(rolledBack)).put("after_commit",a)
          .put("dirty_read_absent",true).put("rollback_restored_write_set",true);
    } catch (Throwable error) {
      report.put("status","failed").put("error",error.toString());
      Files.writeString(Path.of(args[3]),report.toString(2)+"\n");
      throw error;
    }
    Files.writeString(Path.of(args[3]),report.toString(2)+"\n");
    System.out.println("Pinned NewOrder commit and rollback checks passed");
  }
}
