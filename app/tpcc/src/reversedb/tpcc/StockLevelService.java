package reversedb.tpcc;

import java.sql.*;

public final class StockLevelService {
  public int count(Connection c,int district,int threshold) throws SQLException {
    int next;
    try(PreparedStatement s=Sql.bind(c,"SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=?",district);ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Missing district");
      next=r.getInt(1);
    }
    try(PreparedStatement s=Sql.bind(c,"SELECT count(DISTINCT s.s_i_id) FROM order_line l JOIN stock s ON s.s_w_id=1 AND s.s_i_id=l.ol_i_id WHERE l.ol_w_id=1 AND l.ol_d_id=? AND l.ol_o_id>=? AND l.ol_o_id<? AND s.s_quantity<?",
        district,next-20,next,threshold);ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Missing stock count");
      return r.getInt(1);
    }
  }
}
