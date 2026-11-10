package reversedb.tpcc;

import java.math.BigDecimal;
import java.sql.*;
import java.util.*;

public final class DeliveryService {
  public record Delivered(int district,int order,int customer) {}
  public List<Delivered> deliver(Connection c,int carrier,long clock) throws SQLException {
    List<Delivered> result=new ArrayList<>();
    for(int district=1;district<=10;district++) {
      int order;
      try(PreparedStatement s=Sql.bind(c,"SELECT no_o_id FROM new_order WHERE no_w_id=1 AND no_d_id=? ORDER BY no_o_id LIMIT 1",district);
          ResultSet r=s.executeQuery()) {
        if(!r.next()) continue;
        order=r.getInt(1);
      }
      Sql.one(c,"DELETE FROM new_order WHERE no_w_id=1 AND no_d_id=? AND no_o_id=?",district,order);
      int customer;
      try(PreparedStatement s=Sql.bind(c,"SELECT o_c_id FROM oorder WHERE o_w_id=1 AND o_d_id=? AND o_id=?",district,order);
          ResultSet r=s.executeQuery()) {
        if(!r.next()) throw new SQLException("Missing delivery order");
        customer=r.getInt(1);
      }
      Sql.one(c,"UPDATE oorder SET o_carrier_id=? WHERE o_w_id=1 AND o_d_id=? AND o_id=?",carrier,district,order);
      try(PreparedStatement s=Sql.bind(c,"UPDATE order_line SET ol_delivery_d=? WHERE ol_w_id=1 AND ol_d_id=? AND ol_o_id=?",new Timestamp(clock),district,order)) {
        if(s.executeUpdate()==0) throw new SQLException("Missing delivery lines");
      }
      float total;
      try(PreparedStatement s=Sql.bind(c,"SELECT sum(ol_amount) FROM order_line WHERE ol_w_id=1 AND ol_d_id=? AND ol_o_id=?",district,order);
          ResultSet r=s.executeQuery()) {
        if(!r.next()) throw new SQLException("Missing delivery amount");
        total=r.getFloat(1);
      }
      Sql.one(c,"UPDATE customer SET c_balance=c_balance+?,c_delivery_cnt=c_delivery_cnt+1 WHERE c_w_id=1 AND c_d_id=? AND c_id=?",
          BigDecimal.valueOf(total),district,customer);
      result.add(new Delivered(district,order,customer));
    }
    return result;
  }
}
