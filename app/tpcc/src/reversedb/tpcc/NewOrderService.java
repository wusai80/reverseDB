package reversedb.tpcc;

import java.sql.*;
import java.util.*;

/** Reconstructed NewOrder slice. No BenchBase classes, recorded responses or SQL replay. */
public final class NewOrderService {
  public record Line(int item, int quantity) {}
  public static final class InvalidItem extends Exception {
    public InvalidItem(int item) { super("Invalid item: " + item); }
  }

  private static PreparedStatement bind(Connection c, String sql, Object... args) throws SQLException {
    PreparedStatement s = c.prepareStatement(sql);
    try {
      for (int i = 0; i < args.length; i++) s.setObject(i + 1, args[i]);
      return s;
    } catch (SQLException error) { s.close(); throw error; }
  }

  private static void exists(Connection c, String sql, Object... args) throws SQLException {
    try (PreparedStatement s = bind(c, sql, args); ResultSet r = s.executeQuery()) {
      if (!r.next()) throw new SQLException("Missing warehouse/customer");
    }
  }

  private static void update(Connection c, String sql, Object... args) throws SQLException {
    try (PreparedStatement s = bind(c, sql, args)) {
      if (s.executeUpdate() != 1) throw new SQLException("Expected one changed row");
    }
  }

  /** Caller owns SERIALIZABLE transaction, commit and rollback. Single-warehouse contract. */
  public int place(Connection c, int district, int customer, long clockMillis, List<Line> lines)
      throws SQLException, InvalidItem {
    exists(c, "SELECT c_id FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?", district, customer);
    exists(c, "SELECT w_id FROM warehouse WHERE w_id=1");
    int order;
    try (PreparedStatement s = bind(c, "SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=? FOR UPDATE", district);
         ResultSet r = s.executeQuery()) {
      if (!r.next()) throw new SQLException("Missing district");
      order = r.getInt(1);
    }
    update(c, "UPDATE district SET d_next_o_id=d_next_o_id+1 WHERE d_w_id=1 AND d_id=?", district);
    update(c, "INSERT INTO oorder(o_id,o_d_id,o_w_id,o_c_id,o_entry_d,o_ol_cnt,o_all_local) VALUES(?,?,1,?,?,?,1)",
        order, district, customer, new Timestamp(clockMillis), lines.size());
    update(c, "INSERT INTO new_order(no_o_id,no_d_id,no_w_id) VALUES(?,?,1)", order, district);

    // Retain deferred batch semantics, including repeated item IDs. No optimization in this phase.
    try (PreparedStatement insert = c.prepareStatement("INSERT INTO order_line(ol_o_id,ol_d_id,ol_w_id,ol_number,ol_i_id,ol_supply_w_id,ol_quantity,ol_amount,ol_dist_info) VALUES(?,?,1,?,?,1,?,?,?)");
         PreparedStatement stock = c.prepareStatement("UPDATE stock SET s_quantity=?,s_ytd=s_ytd+?,s_order_cnt=s_order_cnt+1,s_remote_cnt=s_remote_cnt+0 WHERE s_w_id=1 AND s_i_id=?")) {
      int index = 0;
      for (Line line : lines) {
        float price;
        try (PreparedStatement s = bind(c, "SELECT i_price FROM item WHERE i_id=?", line.item());
             ResultSet r = s.executeQuery()) {
          if (!r.next()) throw new InvalidItem(line.item());
          price = r.getFloat(1);
        }
        int quantity;
        String info;
        String column = String.format(Locale.ROOT, "s_dist_%02d", district);
        try (PreparedStatement s = bind(c, "SELECT s_quantity," + column + " FROM stock WHERE s_w_id=1 AND s_i_id=? FOR UPDATE", line.item());
             ResultSet r = s.executeQuery()) {
          if (!r.next()) throw new SQLException("Missing stock");
          quantity = r.getInt(1); info = r.getString(2);
        }
        int remaining = quantity - line.quantity();
        if (remaining < 10) remaining += 91;
        insert.setInt(1, order); insert.setInt(2, district); insert.setInt(3, ++index);
        insert.setInt(4, line.item()); insert.setInt(5, line.quantity());
        // The reference computes a float product before binding a double.
        insert.setDouble(6, (double) (price * line.quantity())); insert.setString(7, info);
        insert.addBatch();
        stock.setInt(1, remaining); stock.setInt(2, line.quantity()); stock.setInt(3, line.item());
        stock.addBatch();
      }
      insert.executeBatch(); stock.executeBatch();
    }
    return order;
  }
}
