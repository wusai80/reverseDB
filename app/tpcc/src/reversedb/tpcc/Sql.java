package reversedb.tpcc;

import java.sql.*;

final class Sql {
  private Sql() {}
  static PreparedStatement bind(Connection c, String sql, Object... args) throws SQLException {
    PreparedStatement s=c.prepareStatement(sql);
    try {
      for(int i=0;i<args.length;i++) s.setObject(i+1,args[i]);
      return s;
    } catch(SQLException error) { s.close(); throw error; }
  }
  static void one(Connection c, String sql, Object... args) throws SQLException {
    try(PreparedStatement s=bind(c,sql,args)) {
      if(s.executeUpdate()!=1) throw new SQLException("Expected exactly one affected row");
    }
  }
}
