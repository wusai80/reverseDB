package reversedb.tpcc;

import java.sql.*;
import org.json.*;

/** External state observations, shared by both implementations and excluded from business traces. */
final class Observation {
  static JSONArray context(Connection c,JSONObject cmd) throws SQLException {
    if(!cmd.getString("type").equals("Delivery")) return new JSONArray();
    return AppRuntime.rows(c,"SELECT no_d_id district,min(no_o_id) order_id FROM new_order WHERE no_w_id=1 GROUP BY no_d_id ORDER BY no_d_id");
  }
  static JSONObject capture(Connection c,JSONObject cmd,JSONArray context) throws SQLException {
    switch(cmd.getString("type")) {
      case "NewOrder" -> { return AppRuntime.footprint(c,cmd); }
      case "Payment" -> {
        int d=cmd.getInt("district"),cd=cmd.getInt("customer_district"),customer=cmd.getInt("customer");
        return new JSONObject().put("warehouse",AppRuntime.rows(c,"SELECT * FROM warehouse WHERE w_id=1"))
            .put("district",AppRuntime.rows(c,"SELECT * FROM district WHERE d_w_id=1 AND d_id=?",d))
            .put("customer",AppRuntime.rows(c,"SELECT * FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?",cd,customer))
            .put("history",AppRuntime.rows(c,"SELECT * FROM history WHERE h_c_w_id=1 AND h_c_d_id=? AND h_c_id=? ORDER BY h_date,h_d_id,h_amount,h_data",cd,customer));
      }
      case "Delivery" -> {
        JSONArray orders=new JSONArray();
        for(Object value:context) {
          JSONObject key=(JSONObject)value;int d=key.getInt("district"),id=key.getInt("order_id");
          JSONArray order=AppRuntime.rows(c,"SELECT * FROM oorder WHERE o_w_id=1 AND o_d_id=? AND o_id=?",d,id);
          int customer=order.getJSONObject(0).getInt("o_c_id");
          orders.put(new JSONObject().put("order",order)
              .put("lines",AppRuntime.rows(c,"SELECT * FROM order_line WHERE ol_w_id=1 AND ol_d_id=? AND ol_o_id=? ORDER BY ol_number",d,id))
              .put("customer",AppRuntime.rows(c,"SELECT * FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?",d,customer)));
        }
        return new JSONObject().put("orders",orders).put("queue",AppRuntime.rows(c,"SELECT no_d_id,count(*) pending,min(no_o_id) first_id,max(no_o_id) last_id FROM new_order WHERE no_w_id=1 GROUP BY no_d_id ORDER BY no_d_id"));
      }
      default -> { return new JSONObject(); } // Read-only transactions are enforced by PostgreSQL.
    }
  }
}
