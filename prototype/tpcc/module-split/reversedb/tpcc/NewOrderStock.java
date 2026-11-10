package reversedb.tpcc;

import java.sql.*;
import java.util.Locale;

/**
 * One order's stock work inside a caller-owned transaction.
 * stage must follow the original line order, including repeated item IDs.
 * No stock updates execute until apply; each repeated read sees the pre-batch value.
 * The caller applies order-line writes before apply and owns commit/rollback.
 * close releases only this object's statement, never the borrowed connection.
 */
final class NewOrderStock implements AutoCloseable {
  private final Connection transaction;
  private final PreparedStatement updates;
  private final String districtColumn;
  private boolean finished;

  NewOrderStock(Connection transaction, int district) throws SQLException {
    this.transaction = transaction;
    districtColumn = String.format(Locale.ROOT, "s_dist_%02d", district);
    updates = transaction.prepareStatement("UPDATE stock SET s_quantity=?,s_ytd=s_ytd+?,s_order_cnt=s_order_cnt+1,s_remote_cnt=s_remote_cnt+0 WHERE s_w_id=1 AND s_i_id=?");
  }

  /** Lock/read this occurrence, queue its update, and supply its order-line district info. */
  String stage(int item, int quantity) throws SQLException {
    if (finished) throw new SQLException("Stock work already applied or closed");
    int oldQuantity;
    String districtInfo;
    try (PreparedStatement query = Sql.bind(transaction,
        "SELECT s_quantity," + districtColumn + " FROM stock WHERE s_w_id=1 AND s_i_id=? FOR UPDATE", item);
         ResultSet row = query.executeQuery()) {
      if (!row.next()) throw new SQLException("Missing stock");
      oldQuantity = row.getInt(1);
      districtInfo = row.getString(2);
    }
    int remaining = oldQuantity - quantity;
    if (remaining < 10) remaining += 91;
    updates.setInt(1, remaining);
    updates.setInt(2, quantity);
    updates.setInt(3, item);
    updates.addBatch();
    return districtInfo;
  }

  /** Apply once after all lines have been staged; failure requires the caller to roll back. */
  void apply() throws SQLException {
    if (finished) throw new SQLException("Stock work already applied or closed");
    finished = true;
    updates.executeBatch();
  }

  @Override public void close() throws SQLException {
    finished = true;
    updates.close();
  }
}
