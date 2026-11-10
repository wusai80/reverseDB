package reversedb.tpcc;

import java.sql.*;

public final class OrderStatusService {
  public int latestOrder(Connection c,int district,int customer) throws SQLException {
    try(PreparedStatement s=Sql.bind(c,"SELECT c_id FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?",district,customer);
        ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Missing customer");
    }
    try(PreparedStatement s=Sql.bind(c,"SELECT o_id FROM oorder WHERE o_w_id=1 AND o_d_id=? AND o_c_id=? ORDER BY o_id DESC LIMIT 1",district,customer);
        ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Customer has no order");
      return r.getInt(1);
    }
  }
}
