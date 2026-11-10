/* Reference adapters compose Apache-2.0 BenchBase helpers; see BENCHBASE-LICENSE. */
package com.oltpbenchmark.api;

import com.oltpbenchmark.benchmarks.tpcc.procedures.*;
import com.oltpbenchmark.benchmarks.tpcc.pojo.Customer;
import com.oltpbenchmark.benchmarks.tpcc.pojo.District;
import com.oltpbenchmark.benchmarks.tpcc.pojo.Warehouse;
import com.oltpbenchmark.benchmarks.tpcc.pojo.Oorder;
import com.oltpbenchmark.types.DatabaseType;
import reversedb.tpcc.NewOrderService;
import reversedb.tpcc.AppRuntime;
import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Reference-only adapter; never loaded by the candidate runtime. */
public class TpccApp extends AppRuntime {
  static Object invoke(Object target, String name, Object... args) throws Exception {
    Method selected = null;
    for (Method method : target.getClass().getDeclaredMethods())
      if (method.getName().equals(name) && method.getParameterCount() == args.length) {
        if (selected != null) throw new IllegalStateException("Ambiguous reference helper " + name);
        selected = method;
      }
    if (selected == null) throw new IllegalStateException("Missing reference helper " + name);
    selected.setAccessible(true);
    try { return selected.invoke(target, args); }
    catch (InvocationTargetException e) {
      if (e.getCause() instanceof Exception error) throw error;
      throw e;
    }
  }

  static JSONObject reference(Connection c, JSONObject cmd) throws Exception {
    String type = cmd.getString("type");
    int d = cmd.optInt("district",1), customer = cmd.optInt("customer",1);
    if (type.equals("NewOrder")) {
      int id = rows(c,"SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=?",d).getJSONObject(0).getInt("d_next_o_id");
      List<NewOrderService.Line> lines = new ArrayList<>();
      for (Object o:cmd.getJSONArray("lines")) {
        JSONObject l=(JSONObject)o; lines.add(new NewOrderService.Line(l.getInt("item"),l.getInt("quantity")));
      }
      {
        NewOrder p = new NewOrder(); ((Procedure)p).initialize(DatabaseType.POSTGRES);
        int[] suppliers = new int[lines.size()]; Arrays.fill(suppliers,1);
        invoke(p,"newOrderTransaction",1,d,customer,lines.size(),1,
            lines.stream().mapToInt(NewOrderService.Line::item).toArray(), suppliers,
            lines.stream().mapToInt(NewOrderService.Line::quantity).toArray(),c);
      }
      return new JSONObject().put("order",rows(c,"SELECT * FROM oorder WHERE o_w_id=1 AND o_d_id=? AND o_id=?",d,id).getJSONObject(0))
          .put("lines",rows(c,"SELECT * FROM order_line WHERE ol_w_id=1 AND ol_d_id=? AND ol_o_id=? ORDER BY ol_number",d,id));
    }
    TPCCProcedure p = switch(type) {
      case "Payment" -> new Payment(); case "OrderStatus" -> new OrderStatus();
      case "Delivery" -> new Delivery(); case "StockLevel" -> new StockLevel();
      default -> throw new IllegalArgumentException(type);
    };
    p.initialize(DatabaseType.POSTGRES);
    switch(type) {
      case "Payment" -> {
        float amount = cmd.getBigDecimal("amount").floatValue();
        int cd=cmd.getInt("customer_district");
        invoke(p,"updateWarehouse",c,1,amount);
        Warehouse warehouse = (Warehouse)invoke(p,"getWarehouse",c,1);
        invoke(p,"updateDistrict",c,1,d,amount);
        District district = (District)invoke(p,"getDistrict",c,1,d);
        Customer cust = (Customer)invoke(p,"getCustomerById",1,cd,customer,c);
        cust.c_balance-=amount; cust.c_ytd_payment+=amount; cust.c_payment_cnt++;
        if (cust.c_credit.equals("BC")) {
          cust.c_data=(String)invoke(p,"getCData",c,1,d,cd,1,amount,cust);
          invoke(p,"updateBalanceCData",c,cd,1,cust);
        } else invoke(p,"updateBalance",c,cd,1,cust);
        invoke(p,"insertHistory",c,1,d,cd,1,amount,warehouse.w_name,district.d_name,cust);
        return new JSONObject().put("customer",rows(c,"SELECT c_id,c_d_id,c_balance,c_ytd_payment,c_payment_cnt,c_credit FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?",cd,customer).getJSONObject(0));
      }
      case "OrderStatus" -> {
        Customer cust=(Customer)invoke(p,"getCustomerById",1,d,customer,c);
        Oorder order=(Oorder)invoke(p,"getOrderDetails",c,1,d,cust);
        invoke(p,"getOrderLines",c,1,d,order.o_id,cust);
        return new JSONObject().put("order",rows(c,"SELECT * FROM oorder WHERE o_w_id=1 AND o_d_id=? AND o_id=?",d,order.o_id).getJSONObject(0))
            .put("lines",rows(c,"SELECT * FROM order_line WHERE ol_w_id=1 AND ol_d_id=? AND ol_o_id=? ORDER BY ol_number",d,order.o_id));
      }
      case "StockLevel" -> {
        int next=(Integer)invoke(p,"getOrderId",c,1,d);
        return new JSONObject().put("low_stock_count",invoke(p,"getStockCount",c,1,cmd.getInt("threshold"),d,next));
      }
      case "Delivery" -> {
        JSONArray delivered=new JSONArray();
        for(int district=1;district<=10;district++) {
          Integer order=(Integer)invoke(p,"getOrderId",c,1,district);
          if(order==null) continue;
          invoke(p,"deleteOrder",c,1,district,order);
          int cid=(Integer)invoke(p,"getCustomerId",c,1,district,order);
          invoke(p,"updateCarrierId",c,1,cmd.getInt("carrier"),district,order);
          invoke(p,"updateDeliveryDate",c,1,district,order);
          float total=(Float)invoke(p,"getOrderLineTotal",c,1,district,order);
          invoke(p,"updateBalanceAndDelivery",c,1,district,cid,total);
          delivered.put(new JSONObject().put("district",district).put("order",order).put("customer",cid));
        }
        return new JSONObject().put("delivered",delivered);
      }
    }
    throw new IllegalStateException(type);
  }

  public static void main(String[] args) throws Exception {
    AppRuntime.run(args,(connection,command)-> {
      try { return reference(connection,command); }
      catch(Procedure.UserAbortException error) {
        if(!command.getString("type").equals("NewOrder")) throw error;
        throw new AppRuntime.BusinessAbort();
      }
    });
  }
}
