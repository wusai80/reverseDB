package reversedb.tpcc;

import java.lang.reflect.Proxy;
import java.sql.*;
import org.json.JSONArray;

/** The observer must freeze upstream clocks but never repair fused arguments. */
public class TraceClockBoundaryTest {
  public static void main(String[] args) throws Exception {
    Timestamp[] received=new Timestamp[1];
    PreparedStatement target=(PreparedStatement)Proxy.newProxyInstance(TraceClockBoundaryTest.class.getClassLoader(),
      new Class<?>[]{PreparedStatement.class},(p,m,a)->{ if(m.getName().equals("setTimestamp")) received[0]=(Timestamp)a[1]; return null; });
    String[] originals={"INSERT INTO oorder (...) VALUES (...) off", "INSERT INTO history (...) VALUES (...) off", "UPDATE order_line SET ..."};
    int[] slots={5,6,1};
    for(int i=0;i<originals.length;i++) {
      JdbcTrace.statement(target,originals[i],123,new JSONArray(),true).setTimestamp(slots[i],new Timestamp(456));
      if(received[0].getTime()!=123) throw new AssertionError("Original clock not frozen");
    }
    String[] fused={"SELECT reversedb_fusion.neworder_full(?,?,?,?::jsonb,?::timestamp)",
      "SELECT reversedb_payment.finish_payment(?,?,?,?,?,?,?,?,?,?::timestamp,?,?)"};
    int[] positions={5,10};
    for(int i=0;i<fused.length;i++) {
      Timestamp supplied=new Timestamp(456);
      JdbcTrace.statement(target,fused[i],123,new JSONArray(),true).setTimestamp(positions[i],supplied);
      if(received[0]!=supplied) throw new AssertionError("Candidate clock was replaced");
      try { JdbcTrace.statement(target,fused[i],123,new JSONArray(),true).setTimestamp(positions[i]-1,supplied); throw new AssertionError("Unknown slot accepted"); }
      catch(IllegalStateException expected) { }
    }
    try { JdbcTrace.statement(target,"SELECT unknown(?)",123,new JSONArray(),true).setTimestamp(1,new Timestamp(456)); throw new AssertionError("Unknown SQL accepted"); }
    catch(IllegalStateException expected) { }
    JdbcTrace.statement(target,"SELECT unknown(?)",123,new JSONArray(),false).setTimestamp(1,new Timestamp(456));
    if(received[0].getTime()!=456) throw new AssertionError("Non-reference clock replaced");
    System.out.println("9 trace clock checks passed");
  }
}
