package reversedb.tpcc;

import java.sql.Connection;
import java.util.*;
import org.json.*;

/** Entire candidate entry point; no BenchBase dependency or reflective dispatch. */
public final class CandidateApp {
  static JSONObject order(Connection c,int district,int order) throws Exception {
    return new JSONObject().put("order",AppRuntime.rows(c,"SELECT * FROM oorder WHERE o_w_id=1 AND o_d_id=? AND o_id=?",district,order).getJSONObject(0))
        .put("lines",AppRuntime.rows(c,"SELECT * FROM order_line WHERE ol_w_id=1 AND ol_d_id=? AND ol_o_id=? ORDER BY ol_number",district,order));
  }
  public static JSONObject execute(Connection c,JSONObject cmd) throws Exception {
    int district=cmd.optInt("district",1),customer=cmd.optInt("customer",1);
    switch(cmd.getString("type")) {
      case "NewOrder" -> {
        List<NewOrderService.Line> lines=new ArrayList<>();
        for(Object value:cmd.getJSONArray("lines")) {
          JSONObject line=(JSONObject)value;lines.add(new NewOrderService.Line(line.getInt("item"),line.getInt("quantity")));
        }
        return order(c,district,new NewOrderService().place(c,district,customer,cmd.getLong("clock_ms"),lines));
      }
      case "Payment" -> {
        int cd=cmd.getInt("customer_district");
        new PaymentService().pay(c,district,cd,customer,cmd.getBigDecimal("amount"),cmd.getLong("clock_ms"));
        return new JSONObject().put("customer",AppRuntime.rows(c,"SELECT c_id,c_d_id,c_balance,c_ytd_payment,c_payment_cnt,c_credit FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?",cd,customer).getJSONObject(0));
      }
      case "OrderStatus" -> { return order(c,district,new OrderStatusService().latestOrder(c,district,customer)); }
      case "StockLevel" -> { return new JSONObject().put("low_stock_count",new StockLevelService().count(c,district,cmd.getInt("threshold"))); }
      case "Delivery" -> {
        JSONArray result=new JSONArray();
        for(DeliveryService.Delivered item:new DeliveryService().deliver(c,cmd.getInt("carrier"),cmd.getLong("clock_ms")))
          result.put(new JSONObject().put("district",item.district()).put("order",item.order()).put("customer",item.customer()));
        return new JSONObject().put("delivered",result);
      }
      default -> throw new IllegalArgumentException("Unsupported command");
    }
  }
  public static void main(String[] args) throws Exception {
    if(args.length!=5 || !args[4].equals("candidate")) throw new IllegalArgumentException("Candidate engine required");
    try {
      Class.forName("com.oltpbenchmark.api.Procedure");
      throw new IllegalStateException("Candidate runtime must not contain BenchBase");
    } catch(ClassNotFoundException expected) { /* The candidate is actually isolated. */ }
    AppRuntime.run(args,CandidateApp::execute);
  }
}
