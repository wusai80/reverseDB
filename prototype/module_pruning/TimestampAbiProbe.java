package research;

import java.lang.reflect.*;
import java.math.BigDecimal;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;
import reversedb.tpcc.AppRuntime;

/** Bounded original / repaired / historical ABI differential; no timings. */
public class TimestampAbiProbe extends FusionProbe {
  static final long NORMAL=1762300800123L, INFINITY=9223372036825200000L;
  static JSONObject command(String kind,long clock) {
    JSONObject cmd=new JSONObject().put("id",kind+"-"+clock).put("terminal",1)
      .put("warehouse",1).put("district",1).put("customer",1).put("clock_ms",clock);
    if(kind.equals("NewOrder")) {
      JSONArray lines=new JSONArray();
      for(int i=1;i<=5;i++) lines.put(new JSONObject().put("item",i).put("quantity",i));
      return cmd.put("type","NewOrder").put("lines",lines);
    }
    return cmd.put("type","Payment").put("customer_district",2).put("amount",new BigDecimal("0.10"));
  }
  static void fixture(Connection c,String kind) throws Exception {
    if(kind.equals("NewOrder")) return;
    try(PreparedStatement s=c.prepareStatement("UPDATE customer SET c_credit=? WHERE c_w_id=1 AND c_d_id=2 AND c_id=1")) {
      s.setString(1,kind.equals("PaymentBC")?"BC":"GC"); require(s.executeUpdate()==1,"Fixture customer missing");
    }
  }
  static final class Instrument {
    int calls,timestamps;
    Connection wrap(Connection c,JSONObject cmd,boolean original) {
      return (Connection)Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{Connection.class},(p,m,a)->{
        Object result=forward(c,m,a);
        if(!m.getName().equals("prepareStatement")) return result;
        return Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{PreparedStatement.class},(s,op,args)->{
          if(op.getName().startsWith("execute")) calls++;
          if(op.getName().equals("setTimestamp")) {
            timestamps++;
            // Freeze only original wall-clock writes, as the frozen runtime does.
            // Candidate values pass through untouched so this probe cannot repair them.
            if(original) { args=args.clone(); args[1]=new Timestamp(cmd.getLong("clock_ms")); }
            else require(((Timestamp)args[1]).getTime()==cmd.getLong("clock_ms"),"Candidate changed the command clock");
          }
          return forward(result,op,args);
        });
      });
    }
  }
  static JSONArray timeWitness(Connection c,String kind,long clock) throws Exception {
    if(kind.equals("NewOrder")) return AppRuntime.rows(c,"SELECT o_entry_d::text AS stored_time FROM oorder WHERE o_w_id=1 AND o_d_id=1 AND o_id=(SELECT d_next_o_id-1 FROM district WHERE d_w_id=1 AND d_id=1)");
    return AppRuntime.rows(c,"SELECT h_date::text AS stored_time FROM history WHERE h_c_w_id=1 AND h_c_d_id=2 AND h_c_id=1 AND h_date=?",new Timestamp(clock));
  }
  public static void main(String[] args) throws Exception {
    // args: url original new-fixed payment-fixed new-old payment-old jar output
    Arm original=arm("original",args[1],args[6]);
    Arm newFixed=arm("full",args[2],args[6]), payFixed=arm("segmented",args[3],args[6]);
    Arm newOld=arm("full",args[4],args[6]), payOld=arm("segmented",args[5],args[6]);
    List<Arm> all=List.of(original,newFixed,payFixed,newOld,payOld);
    JSONArray checks=new JSONArray(),negative=new JSONArray(),aborts=new JSONArray();
    JSONObject report=new JSONObject().put("status","running").put("checks",checks).put("negative_controls",negative).put("aborts",aborts);
    try(Connection c=DriverManager.getConnection(args[0],"tpcc",System.getenv("TPCC_PASSWORD"))) {
      c.setAutoCommit(false); c.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
      String baseline=state(c); c.rollback();
      report.put("initial_nine_table_state",new JSONArray(baseline));
      for(String kind:List.of("NewOrder","PaymentGC","PaymentBC")) for(long clock:new long[]{NORMAL,INFINITY}) {
        JSONObject cmd=command(kind,clock); AppRuntime.validate(cmd);
        Arm fixed=kind.equals("NewOrder")?newFixed:payFixed, old=kind.equals("NewOrder")?newOld:payOld;
        JSONObject expected=null; String expectedState=null;
        for(int index=0;index<3;index++) {
          Arm arm=index==0?original:index==1?fixed:old;
          String label=index==0?"original":index==1?"fixed":"historical";
          fixture(c,kind); Instrument instrument=new Instrument();
          boolean shouldFail=index==2 && clock==INFINITY;
          try {
            JSONObject result=invoke(arm,instrument.wrap(c,cmd,index==0),cmd);
            require(!shouldFail,"Historical timestamp defect was not detected");
            String after=state(c); JSONArray timestamp=timeWitness(c,kind,clock);
            String expectedTime=clock==INFINITY?"infinity":"2025-11-05 00:00:00.123";
            require(timestamp.length()==1 && timestamp.getJSONObject(0).getString("stored_time").equals(expectedTime),"Missing exact transaction timestamp");
            if(index==0) { expected=result; expectedState=after; }
            else { require(expected.similar(result),"Result mismatch "+kind+" "+clock+" "+label); require(expectedState.equals(after),"Full state mismatch "+kind+" "+clock+" "+label); }
            if(index==1) {
              require(instrument.timestamps==1,"Fixed path bypasses timestamp binding");
              require(instrument.calls==(kind.equals("NewOrder")?1:kind.equals("PaymentBC")?4:3),"Changed fused call count");
            }
            checks.put(new JSONObject().put("case",kind).put("clock_ms",clock).put("arm",label).put("result",result)
              .put("nine_table_state",new JSONArray(after)).put("stored_time",timestamp).put("calls",instrument.calls).put("timestamp_bindings",instrument.timestamps));
          } catch(SQLException error) {
            require(shouldFail && error.getSQLState().startsWith("22"),"Unexpected SQL failure "+error);
            negative.put(new JSONObject().put("case",kind).put("clock_ms",clock).put("sql_state",error.getSQLState()).put("message",error.getMessage()));
          } finally { c.rollback(); }
          require(baseline.equals(state(c)),"Rollback changed any business data"); c.rollback();
        }
      }
      JSONObject bad=command("NewOrder",INFINITY); bad.getJSONArray("lines").getJSONObject(4).put("item",Integer.MAX_VALUE);
      AppRuntime.validate(bad);
      for(Arm arm:List.of(original,newFixed)) {
        try { invoke(arm,new Instrument().wrap(c,bad,arm==original),bad); throw new AssertionError("Missing INVALID_ITEM abort"); }
        catch(Exception expected) {
          // TpccApp.main maps this reference helper exception to BusinessAbort.
          require(arm==original ? expected.getClass().getName().equals("com.oltpbenchmark.api.Procedure$UserAbortException")
            : expected instanceof AppRuntime.BusinessAbort,"Unexpected abort type "+expected);
          aborts.put(new JSONObject().put("arm",arm==original?"original":"fixed").put("raw_exception",expected.getClass().getName()).put("application_status","INVALID_ITEM"));
        }
        finally { c.rollback(); }
        require(baseline.equals(state(c)),"Business abort leaked writes"); c.rollback();
      }
      require(checks.length()==15 && negative.length()==3 && aborts.length()==2,"Incomplete fixed protocol");
      report.put("status","passed").put("all_rollbacks_restore_nine_tables",true);
    } finally {
      for(Arm arm:all) arm.loader().close();
      Files.writeString(Path.of(args[7]),report.toString(2)+"\n");
    }
  }
}
