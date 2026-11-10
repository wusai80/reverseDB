package research;

import java.lang.reflect.*;
import java.math.BigDecimal;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;
import reversedb.tpcc.AppRuntime;

/** Real PostgreSQL differential, two failure boundaries and fixed helper timings. */
public class PaymentSegmentProbe extends FusionProbe {
  record Case(String id,String credit,String balance,String amount,String data,String name) {}
  static JSONObject command(Case test) {
    return new JSONObject().put("id",test.id).put("type","Payment").put("warehouse",1).put("terminal",1)
      .put("district",1).put("customer_district",1).put("customer",1).put("clock_ms",1762300800000L)
      .put("amount",new BigDecimal(test.amount));
  }
  static void fixture(Connection c,Case test) throws Exception {
    try(PreparedStatement s=c.prepareStatement("UPDATE customer SET c_credit=?,c_balance=?,c_ytd_payment=10,c_payment_cnt=1,c_data=? WHERE c_w_id=1 AND c_d_id=1 AND c_id=1")) {
      s.setString(1,test.credit); s.setBigDecimal(2,new BigDecimal(test.balance)); s.setString(3,test.data); require(s.executeUpdate()==1,"Missing fixture customer");
    }
    try(PreparedStatement s=c.prepareStatement("UPDATE warehouse SET w_name=? WHERE w_id=1")) { s.setString(1,test.name); require(s.executeUpdate()==1,"Missing warehouse"); }
    try(Statement s=c.createStatement()) { require(s.executeUpdate("UPDATE district SET d_name='district' WHERE d_w_id=1 AND d_id=1")==1,"Missing district"); }
  }
  static final class Instrument {
    int calls;
    Connection wrap(Connection raw,JSONObject cmd,String fault,boolean original) {
      return (Connection)Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{Connection.class},(p,m,a)-> {
        Object result=forward(raw,m,a);
        if(!m.getName().equals("prepareStatement")) return result;
        String sql=((String)a[0]).replaceAll("\\s+"," ").trim().toLowerCase(Locale.ROOT);
        return Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{PreparedStatement.class},(s,op,args)-> {
          if(op.getName().startsWith("execute")) {
            calls++;
            if(fault.equals("between") && sql.startsWith("select c_first")) throw new SQLException("injected between regions","58000");
            if(original && fault.equals("late") && sql.startsWith("select c_id,c_d_id,c_balance")) throw new SQLException("injected after writes","58000");
          }
          if(op.getName().equals("setTimestamp")) { args=args.clone(); args[1]=new Timestamp(cmd.getLong("clock_ms")); }
          return forward(result,op,args);
        });
      });
    }
  }
  public static void main(String[] args) throws Exception {
    List<Arm> arms=List.of(arm("original",args[1],args[3]),arm("segmented",args[2],args[3]));
    String prefix="1 1 1 1 1 0.1 | ";
    List<Case> cases=List.of(new Case("gc-small","GC","10.00","0.01","data","warehouse"),
      new Case("gc-large","GC","262144.00","0.01","data","warehouse"),
      new Case("bc-text","BC","10.00","0.10","data","warehouse"),
      new Case("bc-unicode","BC","10.00","0.10","a".repeat(498-prefix.length())+"😀Ztail","warehouse"),
      new Case("gc-name","GC","10.00","0.10","data","a".repeat(8)+"😀Z"));
    JSONObject report=new JSONObject(); JSONArray checks=new JSONArray(),faults=new JSONArray(),samples=new JSONArray();
    Map<String,JSONObject> expectedResults=new HashMap<>();
    try(Connection c=DriverManager.getConnection(args[0],"tpcc",System.getenv("TPCC_PASSWORD"))) {
      c.setAutoCommit(false); c.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
      String baseline=state(c); c.rollback();
      for(Case test:cases) {
        JSONObject cmd=command(test); AppRuntime.validate(cmd); String expectedState=null;
        for(Arm arm:arms) {
          fixture(c,test); Instrument instrument=new Instrument();
          JSONObject result=invoke(arm,instrument.wrap(c,cmd,"none",arm.name().equals("original")),cmd);
          String after=state(c);
          if(arm.name().equals("original")) { expectedResults.put(test.id,result); expectedState=after; }
          else { require(expectedResults.get(test.id).similar(result),"Response mismatch "+test.id); require(expectedState.equals(after),"Full data mismatch "+test.id); }
          int count=test.credit.equals("BC")?9:8;
          require(instrument.calls==(arm.name().equals("original")?count:count-5),"Wrong call count");
          JSONObject customer=AppRuntime.rows(c,"SELECT c_balance,c_data FROM customer WHERE c_w_id=1 AND c_d_id=1 AND c_id=1").getJSONObject(0);
          JSONArray history=AppRuntime.rows(c,"SELECT h_data,h_amount FROM history WHERE h_c_w_id=1 AND h_c_d_id=1 AND h_c_id=1 AND h_date=?",new Timestamp(cmd.getLong("clock_ms")));
          require(history.length()==1,"History witness not unique");
          if(test.id.equals("gc-large")) require(customer.getBigDecimal("c_balance").compareTo(new BigDecimal("262143.98"))==0,"Float semantics lost");
          if(test.id.equals("bc-text")) require(customer.getString("c_data").equals(prefix+"data"),"Float text lost");
          if(test.id.equals("bc-unicode")) { String data=customer.getString("c_data"); require(data.length()==500 && data.endsWith("😀"),"UTF-16 C_DATA lost"); }
          if(test.id.equals("gc-name")) require(history.getJSONObject(0).getString("h_data").equals("a".repeat(8)+"😀    district"),"UTF-16 history lost");
          checks.put(new JSONObject().put("case",test.id).put("arm",arm.name()).put("calls",instrument.calls)
            .put("customer",customer).put("history",history).put("full_state_equal",true));
          c.rollback();
        }
      }
      for(Case test:List.of(cases.get(0),cases.get(2))) for(String fault:List.of("between","late")) for(Arm arm:arms) {
        JSONObject cmd=command(test); fixture(c,test); Instrument instrument=new Instrument();
        System.setProperty("reversedb.payment.lateFault",Boolean.toString(arm.name().equals("segmented") && fault.equals("late")));
        try { invoke(arm,instrument.wrap(c,cmd,fault,arm.name().equals("original")),cmd); throw new AssertionError("Missing fault"); }
        catch(SQLException error) { require("58000".equals(error.getSQLState()),"Wrong fault"); }
        finally { c.rollback(); System.clearProperty("reversedb.payment.lateFault"); }
        require(baseline.equals(state(c)),"Fault leaked writes"); c.rollback();
        faults.put(new JSONObject().put("case",test.id).put("arm",arm.name()).put("point",fault).put("nine_tables_restored",true));
      }
      Random random=new Random(20261105);
      for(Case test:List.of(cases.get(0),cases.get(2))) {
        JSONObject cmd=command(test);
        for(Arm arm:arms) for(int warm=0;warm<3;warm++) {
          fixture(c,test); invoke(arm,new Instrument().wrap(c,cmd,"none",arm.name().equals("original")),cmd); c.rollback();
        }
        for(int block=0;block<10;block++) {
          List<Arm> order=new ArrayList<>(arms); Collections.shuffle(order,random);
          for(Arm arm:order) {
            fixture(c,test); Instrument instrument=new Instrument(); Connection wrapped=instrument.wrap(c,cmd,"none",arm.name().equals("original"));
            long started=System.nanoTime(); JSONObject result=invoke(arm,wrapped,cmd); double ms=(System.nanoTime()-started)/1e6;
            require(expectedResults.get(test.id).similar(result),"Timed response mismatch"); c.rollback();
            samples.put(new JSONObject().put("case",test.id).put("arm",arm.name()).put("block",block).put("ms",ms).put("calls",instrument.calls));
          }
        }
      }
      require(baseline.equals(state(c)),"Probe changed data"); c.rollback();
    } finally { for(Arm arm:arms) arm.loader().close(); }
    report.put("status","passed").put("checks",checks).put("faults",faults).put("samples",samples).put("seed",20261105)
      .put("scope","Real PostgreSQL, full nine-table differential before rollback; timing excludes fixture setup, commit and rollback; two cases, ten shuffled blocks");
    Files.writeString(Path.of(args[4]),report.toString(2)+"\n");
  }
}
